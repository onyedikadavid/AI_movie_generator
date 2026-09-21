import os
import shutil
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import Optional, List

from app.core.database import get_db
from app.core.config import settings
from app.core.celery_app import celery_app
from app.models.project import Project, ProjectStatus
from app.models.character import Character
from app.models.scene import Scene
from app.schemas.project import ProjectResponse, ProjectListItem, ProjectDetailResponse
from app.schemas.character import CharacterUpdate, CharacterResponse
from app.schemas.scene import SceneUpdate, SceneResponse
from app.tasks.pipeline_tasks import run_script_breakdown, run_asset_pipeline

router = APIRouter()

# Statuses from which asset generation is allowed to start (or restart).
_ASSET_GENERATION_ALLOWED_FROM = {
    ProjectStatus.SCRIPT_READY,
    ProjectStatus.FAILED,
    ProjectStatus.COMPLETED,
    ProjectStatus.CANCELLED,
}

_IN_FLIGHT_STATUSES = {
    ProjectStatus.TRANSCRIBING,
    ProjectStatus.GENERATING_SCRIPT,
    ProjectStatus.GENERATING_ASSETS,
    ProjectStatus.GENERATING_IMAGES,
    ProjectStatus.GENERATING_VIDEOS,
    ProjectStatus.GENERATING_AUDIO,
    ProjectStatus.COMPOSITING,
}


async def _get_project_or_404(project_id: str, db: AsyncSession) -> Project:
    result = await db.execute(select(Project).filter(Project.id == project_id))
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


@router.post("/projects", response_model=ProjectResponse)
async def create_project(
    prompt: Optional[str] = Form(None),
    genre: Optional[str] = Form(None),
    tone: Optional[str] = Form(None),
    visual_style: Optional[str] = Form(None),
    audio_file: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
):
    """
    View 2 (Project Creation Form): kicks off Phase 1 only (STT + script
    breakdown). The heavy asset-generation phase is a separate, explicit
    step triggered from the review screen via /projects/{id}/run-pipeline.
    """
    if not prompt and not audio_file:
        raise HTTPException(status_code=400, detail="Must provide either a text prompt or an audio recording.")

    new_project = Project(
        raw_prompt=prompt,
        requested_genre=genre,
        requested_tone=tone,
        requested_visual_style=visual_style,
    )
    db.add(new_project)
    await db.commit()
    await db.refresh(new_project)

    if audio_file:
        project_dir = os.path.join(settings.STORAGE_DIR, "projects", new_project.id)
        os.makedirs(project_dir, exist_ok=True)
        safe_name = os.path.basename(audio_file.filename or "input_audio")
        audio_path = os.path.join(project_dir, f"input_audio_{safe_name}")

        with open(audio_path, "wb") as buffer:
            shutil.copyfileobj(audio_file.file, buffer)

        new_project.audio_input_path = audio_path
        await db.commit()

    task = run_script_breakdown.delay(new_project.id)
    new_project.celery_task_id = task.id
    await db.commit()
    await db.refresh(new_project)

    return new_project


@router.get("/projects", response_model=List[ProjectListItem])
async def list_projects(db: AsyncSession = Depends(get_db)):
    """View 1 (Project Dashboard): grid of all projects, newest first."""
    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
    return result.scalars().all()


@router.get("/projects/{project_id}", response_model=ProjectDetailResponse)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)):
    """
    View 3 & 4 (Review Editor / Player): full project detail including the
    parsed script, character registry, and scene timeline.
    """
    result = await db.execute(
        select(Project)
        .options(
            selectinload(Project.script),
            selectinload(Project.characters),
            selectinload(Project.scenes),
        )
        .filter(Project.id == project_id)
    )
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)):
    project = await _get_project_or_404(project_id, db)
    if project.status in _IN_FLIGHT_STATUSES:
        raise HTTPException(status_code=409, detail="Cannot delete a project while it is generating.")

    project_dir = os.path.join(settings.STORAGE_DIR, "projects", project_id)
    await db.delete(project)
    await db.commit()

    if os.path.isdir(project_dir):
        shutil.rmtree(project_dir, ignore_errors=True)
    return None


@router.post("/projects/{project_id}/run-pipeline", response_model=ProjectResponse)
async def run_pipeline(project_id: str, db: AsyncSession = Depends(get_db)):
    """
    View 3 ("Start Generation" button): dispatches Phase 2 (asset/video
    rendering) now that the user has reviewed - and possibly edited - the
    script, characters and scenes. Matches PRD endpoint
    POST /api/projects/{id}/run-pipeline (202 Accepted).
    """
    project = await _get_project_or_404(project_id, db)

    if project.status in _IN_FLIGHT_STATUSES:
        raise HTTPException(status_code=409, detail=f"Project is already processing (status: {project.status}).")
    if project.status not in _ASSET_GENERATION_ALLOWED_FROM:
        raise HTTPException(
            status_code=409,
            detail=f"Project is not ready for generation yet (status: {project.status}). "
                   f"Wait for the script breakdown to finish.",
        )

    project.error_message = None
    project.cancel_requested = False
    await db.commit()

    task = run_asset_pipeline.delay(project_id)
    project.celery_task_id = task.id
    await db.commit()
    await db.refresh(project)
    return project


@router.post("/projects/{project_id}/cancel", response_model=ProjectResponse)
async def cancel_project(project_id: str, db: AsyncSession = Depends(get_db)):
    """
    Stops an in-progress run. This is cooperative, not a hard kill: it sets
    cancel_requested and the running task checks that flag between steps
    (before each scene, and between each generation call within a scene)
    and exits on its own - see run_script_breakdown/run_asset_pipeline in
    pipeline_tasks.py. A true hard-kill via Celery's revoke(terminate=True)
    needs the "prefork" worker pool, which isn't available with the
    --pool=solo Celery recommends on Windows, so this works regardless of
    which pool the worker is running under.

    If the task hasn't started yet (still sitting in the queue), we also
    revoke it outright so it never begins at all.
    """
    project = await _get_project_or_404(project_id, db)

    if project.status not in _IN_FLIGHT_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Nothing to cancel - project isn't currently running (status: {project.status}).",
        )

    project.cancel_requested = True
    await db.commit()

    if project.celery_task_id:
        try:
            celery_app.control.revoke(project.celery_task_id)
        except Exception:
            # Best-effort only - the cancel_requested flag is what actually
            # guarantees a running task stops; this just catches the case
            # where it hadn't started yet.
            pass

    await db.refresh(project)
    return project


@router.patch("/projects/{project_id}/scenes/{scene_id}", response_model=SceneResponse)
async def update_scene(project_id: str, scene_id: str, payload: SceneUpdate, db: AsyncSession = Depends(get_db)):
    """View 3 (Human Control): edit a scene's prompts/text before generation runs."""
    project = await _get_project_or_404(project_id, db)
    if project.status in _IN_FLIGHT_STATUSES:
        raise HTTPException(status_code=409, detail="Cannot edit scenes while generation is in progress.")

    result = await db.execute(select(Scene).filter(Scene.id == scene_id, Scene.project_id == project_id))
    scene = result.scalars().first()
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found.")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(scene, field, value)

    await db.commit()
    await db.refresh(scene)
    return scene


@router.patch("/projects/{project_id}/characters/{character_id}", response_model=CharacterResponse)
async def update_character(
    project_id: str, character_id: str, payload: CharacterUpdate, db: AsyncSession = Depends(get_db)
):
    """View 3 (Human Control): edit a character's description/appearance prompt before generation runs."""
    project = await _get_project_or_404(project_id, db)
    if project.status in _IN_FLIGHT_STATUSES:
        raise HTTPException(status_code=409, detail="Cannot edit characters while generation is in progress.")

    result = await db.execute(
        select(Character).filter(Character.id == character_id, Character.project_id == project_id)
    )
    character = result.scalars().first()
    if not character:
        raise HTTPException(status_code=404, detail="Character not found.")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(character, field, value)

    await db.commit()
    await db.refresh(character)
    return character
