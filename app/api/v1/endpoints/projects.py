import asyncio
import logging
import os
import shutil
from typing import Callable, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.sync_db import SessionLocal
from app.models.character import Character
from app.models.project import BUSY_STATUSES, Project
from app.models.scene import Scene
from app.schemas.character import CharacterResponse, CharacterUpdate
from app.schemas.project import (
    DeleteResult, ProjectDetailResponse, ProjectListItem, ProjectResponse, QueueSnapshot,
)
from app.schemas.scene import SceneResponse, SceneUpdate
from app.services import run_control as rc
from app.services.voice_service import normalize_age_group, normalize_gender

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers: run-control functions are synchronous (they share code with the
# Celery worker), so the API runs them in a thread.
# ---------------------------------------------------------------------------
async def _in_session(fn: Callable):
    """Run `fn(db)` in a worker thread with a sync DB session. `fn` must return
    plain data (pydantic models / dicts), never ORM objects."""

    def inner():
        with SessionLocal() as db:
            return fn(db)

    try:
        return await asyncio.to_thread(inner)
    except rc.ActionError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


def _project_out(db, p: Project, model=ProjectResponse):
    item = model.model_validate(p)
    if p.status.value in ("QUEUED", "CREATED"):
        order = rc.queued_ids_in_order(db)
        if p.id in order:
            item.queue_position = order.index(p.id) + 1
    return item


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
    step triggered from the review screen.
    """
    if not (prompt and prompt.strip()) and not audio_file:
        raise HTTPException(status_code=400, detail="Must provide either a text prompt or an audio recording.")

    new_project = Project(
        raw_prompt=(prompt or "").strip() or None,
        requested_genre=genre or None,
        requested_tone=tone or None,
        requested_visual_style=visual_style or None,
    )
    db.add(new_project)
    await db.commit()
    await db.refresh(new_project)
    project_id = new_project.id

    if audio_file:
        project_dir = os.path.join(settings.STORAGE_DIR, "projects", project_id)
        os.makedirs(project_dir, exist_ok=True)
        safe_name = os.path.basename(audio_file.filename or "input_audio")
        audio_path = os.path.join(project_dir, f"input_audio_{safe_name}")
        with open(audio_path, "wb") as buffer:
            shutil.copyfileobj(audio_file.file, buffer)
        new_project.audio_input_path = audio_path
        await db.commit()

    def start(sdb):
        p = sdb.get(Project, project_id)
        rc.start_new_script(sdb, p)
        sdb.refresh(p)
        return _project_out(sdb, p)

    return await _in_session(start)


@router.get("/queue", response_model=QueueSnapshot)
async def get_queue():
    """What is running right now, and what is waiting (in order)."""

    def inner(db):
        rc.reconcile_stale(db)
        return rc.queue_snapshot(db)

    return await _in_session(inner)


@router.get("/projects", response_model=List[ProjectListItem])
async def list_projects():
    """View 1 (Project Dashboard): grid of all projects, newest first."""

    def inner(db):
        rc.reconcile_stale(db)
        order = rc.queued_ids_in_order(db)
        position = {pid: i + 1 for i, pid in enumerate(order)}
        rows = db.execute(select(Project).order_by(Project.created_at.desc())).scalars().all()
        out = []
        for p in rows:
            item = ProjectListItem.model_validate(p)
            item.queue_position = position.get(p.id)
            out.append(item)
        return out

    return await _in_session(inner)


@router.get("/projects/{project_id}", response_model=ProjectDetailResponse)
async def get_project(project_id: str):
    """View 3 & 4 (Review Editor / Player): full project detail."""

    def inner(db):
        rc.reconcile_stale(db)
        p = db.execute(
            select(Project)
            .options(selectinload(Project.script), selectinload(Project.characters), selectinload(Project.scenes))
            .where(Project.id == project_id)
        ).scalars().first()
        if not p:
            raise rc.ActionError(404, "Project not found.")
        return _project_out(db, p, ProjectDetailResponse)

    return await _in_session(inner)


# ---------------------------------------------------------------------------
# Run control
# ---------------------------------------------------------------------------
@router.post("/projects/{project_id}/run-pipeline", response_model=ProjectResponse, status_code=202)
async def run_pipeline(
    project_id: str,
    fresh: bool = Query(False, description="true = redo every scene from scratch; false = only what's missing or edited"),
):
    """View 3 'Start generation' / 'Regenerate'. Matches PRD POST /projects/{id}/run-pipeline (202 Accepted)."""

    def inner(db):
        p = rc.start_generation(db, project_id, fresh=fresh)
        return _project_out(db, p)

    return await _in_session(inner)


@router.post("/projects/{project_id}/resume", response_model=ProjectResponse, status_code=202)
async def resume_project(project_id: str):
    """Continue a paused / failed / cancelled project from where it stopped."""

    def inner(db):
        return _project_out(db, rc.resume(db, project_id))

    return await _in_session(inner)


@router.post("/projects/{project_id}/pause", response_model=ProjectResponse)
async def pause_project(project_id: str):
    """Pause. Everything finished so far is kept. Takes effect within a few
    seconds (the project shows 'Pausing...' until the worker has stopped)."""

    def inner(db):
        return _project_out(db, rc.pause(db, project_id))

    return await _in_session(inner)


@router.post("/projects/{project_id}/cancel", response_model=ProjectResponse)
async def cancel_project(project_id: str):
    """Stop for good (finished work is still kept on disk and can be picked up
    again with Resume)."""

    def inner(db):
        return _project_out(db, rc.cancel(db, project_id))

    return await _in_session(inner)


@router.delete("/projects/{project_id}", response_model=DeleteResult)
async def delete_project(project_id: str):
    """Delete. An idle project is removed immediately; a running one is stopped
    first and then removes itself (the UI shows 'Deleting...')."""

    def inner(db):
        return rc.delete(db, project_id)

    return await _in_session(inner)


# ---------------------------------------------------------------------------
# Human control: edit before / between runs
# ---------------------------------------------------------------------------
async def _get_project_or_404(project_id: str, db: AsyncSession) -> Project:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


@router.patch("/projects/{project_id}/scenes/{scene_id}", response_model=SceneResponse)
async def update_scene(project_id: str, scene_id: str, payload: SceneUpdate, db: AsyncSession = Depends(get_db)):
    """Edit a scene's prompts / narration / dialogue. The scene is re-rendered next run; others are kept."""
    project = await _get_project_or_404(project_id, db)
    if project.status in BUSY_STATUSES:
        raise HTTPException(status_code=409, detail="Pause the project (or wait for it to finish) before editing scenes.")

    result = await db.execute(select(Scene).where(Scene.id == scene_id, Scene.project_id == project_id))
    scene = result.scalars().first()
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found.")

    changes = payload.model_dump(exclude_unset=True)
    if "dialogue_turns" in changes and changes["dialogue_turns"] is not None:
        changes["dialogue_turns"] = [t for t in changes["dialogue_turns"] if (t.get("text") or "").strip()] or None
    for field, value in changes.items():
        setattr(scene, field, value)
    if changes:
        scene.render_status = "PENDING"

    await db.commit()
    await db.refresh(scene)
    return scene


@router.patch("/projects/{project_id}/characters/{character_id}", response_model=CharacterResponse)
async def update_character(
    project_id: str, character_id: str, payload: CharacterUpdate, db: AsyncSession = Depends(get_db)
):
    """Edit a character's look / gender / age. Their voice is re-chosen next run if gender or age changed."""
    project = await _get_project_or_404(project_id, db)
    if project.status in BUSY_STATUSES:
        raise HTTPException(status_code=409, detail="Pause the project (or wait for it to finish) before editing characters.")

    result = await db.execute(
        select(Character).where(Character.id == character_id, Character.project_id == project_id)
    )
    character = result.scalars().first()
    if not character:
        raise HTTPException(status_code=404, detail="Character not found.")

    changes = payload.model_dump(exclude_unset=True)
    if "gender" in changes:
        g = normalize_gender(changes["gender"])
        if g != character.gender:
            character.voice_id = None  # pick a new voice that matches
        changes["gender"] = g
    if "age_group" in changes:
        changes["age_group"] = normalize_age_group(changes["age_group"])
    for field, value in changes.items():
        setattr(character, field, value)

    if changes:
        scenes = (await db.execute(select(Scene).where(Scene.project_id == project_id))).scalars().all()
        for sc in scenes:
            if sc.render_status == "DONE":
                sc.render_status = "PENDING"

    await db.commit()
    await db.refresh(character)
    return character
