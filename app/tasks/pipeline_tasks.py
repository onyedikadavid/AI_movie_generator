import os
import asyncio
import logging
import shutil
import time
import uuid

from celery.signals import worker_ready
from sqlalchemy import delete, select

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.db_safety import release_connection, safe_rollback
from app.core.schema_upgrade import ensure_schema
from app.core.sync_db import SessionLocal, engine  # noqa: F401  (engine re-exported for older scripts)
from app.models.project import Project, ProjectStatus
from app.models.script import Script
from app.models.character import Character
from app.models.scene import Scene

from app.services import run_control as rc
from app.services.run_control import RunControl, RunStopped
from app.services.stt_service import STTService
from app.services.llm_service import LLMService
from app.services.image_service import ImageGenerationService
from app.services.video_service import VideoGenerationService
from app.services.tts_service import TTSService
from app.services.ffmpeg_service import FFmpegService
from app.services.scene_renderer import SceneRenderer
from app.services.voice_service import VoiceCast, normalize_age_group, normalize_gender
from app.services.media_storage import publish_file
from app.services.style_presets import resolve_style

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Worker start-up: bring the schema up to date and clean up after a crash.
# ---------------------------------------------------------------------------
@worker_ready.connect
def _on_worker_ready(**_kwargs):
    try:
        ensure_schema(engine)
        with SessionLocal() as db:
            rc.ensure_lease_row(db)
            rc.recover_orphans_on_worker_start(db)
            rc.reconcile_stale(db)
    except Exception:  # noqa: BLE001
        logger.exception("Worker start-up cleanup failed (continuing).")


# ---------------------------------------------------------------------------
# The common shell around every run: wait for the single slot, claim the
# project, keep a heartbeat going, and always settle the project's state.
# ---------------------------------------------------------------------------
def _wait_for_slot(db, project_id: str, token: str) -> bool:
    """Block (politely) until this project may run. Returns False if it no
    longer needs to (paused / cancelled / deleted / superseded while waiting)."""
    rc.ensure_lease_row(db)
    while True:
        if rc.acquire_lease(db, project_id):
            return True
        row = db.execute(
            select(Project.status, Project.run_token, Project.delete_requested).where(Project.id == project_id)
        ).first()
        db.commit()
        if row is None or row.run_token != token or row.status != ProjectStatus.QUEUED or row.delete_requested:
            return False
        time.sleep(4)


def _execute(project_id: str, token, first_status: ProjectStatus, body) -> None:
    db = SessionLocal()
    ctl = None
    leased = False
    try:
        project = db.get(Project, project_id)
        if project is None:
            logger.info("Project %s no longer exists - nothing to do.", project_id)
            return

        if token is None:
            # Called without a token (older callers / scripts/test_pipeline.py):
            # mint one and enter the queue the normal way.
            token = uuid.uuid4().hex
            project.run_token, project.status, project.queued_at = token, ProjectStatus.QUEUED, rc.now()
            db.commit()

        if not _wait_for_slot(db, project_id, token):
            logger.info("Project %s left the queue before its turn (superseded / paused / deleted).", project_id)
            return
        leased = True

        if not rc.claim(db, project_id, token, first_status):
            logger.info("Project %s: this task's run token is no longer current - ignoring it.", project_id)
            return

        ctl = RunControl(project_id, token)
        ctl.start()
        db.expire_all()
        project = db.get(Project, project_id)

        try:
            ctl.checkpoint()  # the user may have hit Pause/Delete within the first second
            body(db, project, ctl)
        except RunStopped as stop:
            logger.info("Project %s stopped: %s", project_id, stop.reason)
            rc.finish_stopped(db, project_id, token, stop.reason)
        except Exception as e:  # noqa: BLE001
            logger.error("Run failed for project %s: %s", project_id, e, exc_info=True)
            # If the failure was caused by the user's stop request, honour the
            # stop rather than reporting it as an error.
            if ctl.stopping and ctl.stop_reason:
                rc.finish_stopped(db, project_id, token, ctl.stop_reason)
            else:
                rc.finish_failed(db, project_id, token, str(e))
        except BaseException:
            # Ctrl+C / worker shutdown: park the project so it can be resumed.
            try:
                rc.finish_interrupted(db, project_id, token)
            finally:
                raise
    finally:
        if ctl is not None:
            ctl.end()
        if leased:
            rc.release_lease(db)
        db.close()


def _call(db, ctl, fn, *args, **kwargs):
    """A long blocking call (LLM, GPU server, upload...). The DB connection is released first, so it can't be
    killed while idle during the wait."""
    release_connection(db)
    return ctl.call(fn, *args, **kwargs)


def _set(db, project, **fields) -> None:
    for k, v in fields.items():
        setattr(project, k, v)
    db.commit()


# ---------------------------------------------------------------------------
# Phase 1: script breakdown (text only). Leaves the project in SCRIPT_READY
# so the user can review/edit before any heavy GPU work happens.
# ---------------------------------------------------------------------------
def _script_body(db, project: Project, ctl: RunControl) -> None:
    pid = project.id

    if project.audio_input_path and not project.raw_prompt:
        _set(db, project, status=ProjectStatus.TRANSCRIBING, stage_detail="Listening to your recording")
        stt = STTService()
        text = _call(db, ctl, stt.transcribe, project.audio_input_path)
        _set(db, project, raw_prompt=text)

    ctl.checkpoint()
    _set(db, project, status=ProjectStatus.GENERATING_SCRIPT, stage_detail="Writing the script")

    llm = LLMService()
    prompt, genre, tone, vstyle = project.raw_prompt, project.requested_genre, project.requested_tone, project.requested_visual_style
    script_schema = _call(
        db, ctl,
        lambda: asyncio.run(
            llm.generate_script_structure(prompt, genre_hint=genre, tone_hint=tone, visual_style_hint=vstyle)
        )
    )
    ctl.checkpoint()

    # Re-running (Retry / Resume after a failed breakdown) replaces earlier output.
    db.execute(delete(Scene).where(Scene.project_id == pid))
    db.execute(delete(Character).where(Character.project_id == pid))
    db.execute(delete(Script).where(Script.project_id == pid))
    db.flush()

    project.title = script_schema.title
    db.add(Script(
        project_id=pid, genre=script_schema.genre, tone=script_schema.tone,
        visual_style=script_schema.visual_style, full_text=project.raw_prompt,
    ))

    for ch in script_schema.characters:
        db.add(Character(
            project_id=pid, name=ch.name, description=ch.description, appearance_prompt=ch.appearance_prompt,
            gender=normalize_gender(getattr(ch, "gender", None)),
            age_group=normalize_age_group(getattr(ch, "age_group", None)),
        ))

    for sc in script_schema.scenes:
        turns = [t.model_dump() if hasattr(t, "model_dump") else t for t in (sc.dialogue_turns or [])]
        db.add(Scene(
            project_id=pid,
            scene_number=sc.scene_number,
            duration_seconds=sc.duration_seconds,
            location=sc.location,
            visual_description=sc.visual_description,
            narration_text=sc.narration_text,
            image_prompt=(sc.image_prompt or "").strip() or sc.visual_description,
            motion_prompt=sc.motion_prompt,
            sound_design=(sc.sound_design or None),
            dialogue_turns=turns or None,
            characters_present=list(sc.characters_present or []) or None,
            render_status="PENDING",
        ))

    project.status = ProjectStatus.SCRIPT_READY
    project.stage_detail = None
    project.progress_pct = 0
    project.total_scenes = len(script_schema.scenes)
    db.commit()


@celery_app.task(bind=True)
def run_script_breakdown(self, project_id: str, run_token: str = None):
    _execute(project_id, run_token, ProjectStatus.GENERATING_SCRIPT, _script_body)


# ---------------------------------------------------------------------------
# Phase 2: assets. Renders scene by scene; every finished scene is saved, so
# Pause / a crash / a failure never loses work - Resume continues from there.
# ---------------------------------------------------------------------------
class _SceneCtx:
    """Adapts RunControl + DB progress reporting to what SceneRenderer expects."""

    def __init__(self, db, project, scene, ctl, index, total):
        self.db, self.project, self.scene, self.ctl = db, project, scene, ctl
        self.index, self.total = index, total

    def checkpoint(self):
        self.ctl.checkpoint()

    def call(self, fn, *a, **kw):
        return _call(self.db, self.ctl, fn, *a, **kw)

    def stage(self, status_name: str, frac: float, detail: str):
        # Scene work accounts for 0-92% of the bar; the final cut is the rest.
        pct = int(((self.index + max(0.0, min(1.0, frac))) / self.total) * 92)
        # After a multi-minute GPU wait the hosted database may have dropped our idle connection;
        # retry once on a fresh one instead of failing a render that is otherwise fine.
        for attempt in (1, 2):
            try:
                self.project.status = ProjectStatus[status_name]
                self.project.progress_pct = max(self.project.progress_pct or 0, pct)
                self.project.stage_detail = f"Scene {self.scene.scene_number}/{self.total}: {detail}"
                self.project.current_scene = self.scene.scene_number
                self.db.commit()
                return
            except Exception:  # noqa: BLE001
                safe_rollback(self.db)
                if attempt == 2:
                    raise
                logger.warning("Database connection hiccup while saving progress - retrying once.")

    def set_scene_image(self, path: str):
        # Publish the keyframe as soon as it exists so the gallery fills in live.
        # (Scenes that are already finished and unchanged never reach this.)
        url = _call(self.db, self.ctl, publish_file, path, f"projects/{self.project.id}/scene_{self.scene.scene_number}/keyframe.png")
        for attempt in (1, 2):
            try:
                self.scene.image_path = url
                self.db.commit()
                return
            except Exception:  # noqa: BLE001
                safe_rollback(self.db)
                if attempt == 2:
                    raise


def _asset_body(db, project: Project, ctl: RunControl) -> None:
    pid = project.id
    scenes = db.execute(select(Scene).where(Scene.project_id == pid).order_by(Scene.scene_number)).scalars().all()
    if not scenes:
        raise RuntimeError("This project has no scenes yet. Press Resume/Retry to write the script first.")
    characters = db.execute(select(Character).where(Character.project_id == pid)).scalars().all()
    script = db.execute(select(Script).where(Script.project_id == pid)).scalars().first()
    style = resolve_style(
        project.requested_visual_style, script.visual_style if script else None,
        project.requested_genre, project.requested_tone,
    )

    # Voices: each character gets (and keeps) their own male/female voice.
    cast = VoiceCast(
        characters, settings.TTS_REGION, settings.TTS_NARRATOR_VOICE,
        texts=[project.raw_prompt, script.full_text if script else "", style, project.title]
              + [c.description for c in characters],
    )
    if cast.write_back(characters):
        db.commit()
    logger.info("Voice cast (%s): %s | narrator=%s", cast.region, [(m.name, m.voice_id) for m in cast.members], cast.narrator_voice)

    tts, ff = TTSService(), FFmpegService()
    renderer = SceneRenderer(ImageGenerationService(), VideoGenerationService(), tts, ff)

    total = len(scenes)
    _set(db, project, status=ProjectStatus.GENERATING_ASSETS, total_scenes=total, stage_detail="Starting")
    project_root = os.path.join(settings.STORAGE_DIR, "projects", pid)
    finals = []

    for i, scene in enumerate(scenes):
        ctl.checkpoint()
        scene_dir = os.path.join(project_root, f"scene_{scene.scene_number}")

        if scene.render_status == "REDO":  # the user asked to regenerate everything
            shutil.rmtree(scene_dir, ignore_errors=True)
            scene.render_status = "PENDING"
            scene.image_path = scene.video_path = scene.audio_path = None
            db.commit()

        if scene.render_status != "DONE":
            scene.render_status = "RENDERING"
            db.commit()

        sctx = _SceneCtx(db, project, scene, ctl, i, total)
        try:
            result = renderer.render(scene, characters, style, cast, scene_dir, pid, sctx)
        except RunStopped:
            safe_rollback(db)
            try:
                if scene.render_status == "RENDERING":
                    scene.render_status = "PENDING"
                    db.commit()
            except Exception:  # noqa: BLE001 - never let bookkeeping hide the real reason we stopped
                safe_rollback(db)
            raise
        except Exception:
            safe_rollback(db)
            try:
                scene.render_status = "FAILED"
                db.commit()
            except Exception:  # noqa: BLE001 - keep the ORIGINAL error (e.g. the video server's), not a DB one
                safe_rollback(db)
            raise

        scene.video_path = result.final_path
        scene.audio_path = os.path.join(scene_dir, "voices.wav")
        scene.render_status = "DONE"
        project.progress_pct = int(((i + 1) / total) * 92)
        db.commit()
        finals.append(result.final_path)
        logger.info("Scene %s done: %.1fs, %d shots, %d cached steps reused.",
                    scene.scene_number, result.duration, result.shots, result.reused)

    # ------------------------------------------------------------ final cut
    ctl.checkpoint()
    _set(db, project, status=ProjectStatus.COMPOSITING, progress_pct=94,
         stage_detail="Joining scenes into the final cut", current_scene=None)
    master = os.path.join(project_root, "final_master_video.mp4")
    _call(db, ctl, ff.concatenate_videos, finals, master, "aac")
    _set(db, project, progress_pct=97, stage_detail="Uploading the final video")
    url = _call(db, ctl, publish_file, master, f"projects/{pid}/final_master_video.mp4")

    warnings = list(dict.fromkeys(tts.warnings))
    project.final_video_path = url
    project.warning_message = " ".join(warnings)[:2000] or None
    project.status = ProjectStatus.COMPLETED
    project.progress_pct = 100
    project.stage_detail = "Done"
    project.current_scene = None
    db.commit()


@celery_app.task(bind=True)
def run_asset_pipeline(self, project_id: str, run_token: str = None):
    _execute(project_id, run_token, ProjectStatus.GENERATING_ASSETS, _asset_body)


# ---------------------------------------------------------------------------
# Backwards-compatible full run (used by scripts/test_pipeline.py): runs both
# phases back to back with no review step in between.
# ---------------------------------------------------------------------------
@celery_app.task(bind=True)
def run_story_pipeline(self, project_id: str):
    run_script_breakdown.run(project_id)
    return run_asset_pipeline.run(project_id)
