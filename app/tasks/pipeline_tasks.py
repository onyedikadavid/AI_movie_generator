import os
import asyncio
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.project import Project, ProjectStatus
from app.models.script import Script
from app.models.character import Character
from app.models.scene import Scene

from app.services.stt_service import STTService
from app.services.llm_service import LLMService
from app.services.image_service import ImageGenerationService
from app.services.video_service import VideoGenerationService
from app.services.tts_service import TTSService
from app.services.ffmpeg_service import FFmpegService
from app.services.spatial_service import SpatialService

logger = logging.getLogger(__name__)

# Synchronous DB session wrapper for Celery Workers.
# Same asyncpg-vs-psycopg2 SSL param translation as alembic/env.py - needed
# if DATABASE_URL_OVERRIDE points at a hosted Postgres (e.g. Neon) that
# requires TLS via "?ssl=require".
sync_db_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
sync_db_url = sync_db_url.replace("ssl=require", "sslmode=require")
engine = create_engine(sync_db_url)
SessionLocal = sessionmaker(bind=engine)


def _check_cancelled(db, project) -> bool:
    """
    Cooperative-cancellation check. Queries cancel_requested fresh from the
    DB rather than trusting the in-memory `project` object, since the flag
    is set by the API process over a separate connection - frequent commits
    elsewhere in these tasks mean this session's transaction is short-lived
    enough to see that update promptly under Postgres's READ COMMITTED
    isolation. If cancelled, marks the project CANCELLED and returns True
    so the caller can stop without treating this as a failure.
    """
    cancelled = db.query(Project.cancel_requested).filter(Project.id == project.id).scalar()
    if cancelled:
        project.status = ProjectStatus.CANCELLED
        project.error_message = "Cancelled by user."
        db.commit()
        logger.info(f"Project {project.id} cancelled by user request.")
        return True
    return False


# ---------------------------------------------------------------------------
# Phase 1: Script breakdown (fast, text-only). Runs right after project
# creation. Leaves the project in SCRIPT_READY so the user can review and
# edit characters/scenes in the frontend before any heavy GPU work happens.
# ---------------------------------------------------------------------------
@celery_app.task(bind=True)
def run_script_breakdown(self, project_id: str):
    db = SessionLocal()
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        logger.error(f"Project ID {project_id} not found.")
        return

    try:
        # Step 1: Speech to Text (if audio provided instead of a text prompt)
        if project.audio_input_path and not project.raw_prompt:
            project.status = ProjectStatus.TRANSCRIBING
            db.commit()
            stt = STTService()
            project.raw_prompt = stt.transcribe(project.audio_input_path)
            db.commit()

        if _check_cancelled(db, project):
            return

        # Step 2: LLM Story & Multi-Agent Scene Generation
        project.status = ProjectStatus.GENERATING_SCRIPT
        db.commit()

        llm = LLMService()
        # Safe asyncio execution for synchronous Celery worker threads
        script_schema = asyncio.run(
            llm.generate_script_structure(
                project.raw_prompt,
                genre_hint=project.requested_genre,
                tone_hint=project.requested_tone,
                visual_style_hint=project.requested_visual_style,
            )
        )

        project.title = script_schema.title
        script = Script(
            project_id=project.id,
            genre=script_schema.genre,
            tone=script_schema.tone,
            visual_style=script_schema.visual_style,
            full_text=project.raw_prompt,
        )
        db.add(script)

        # Store Characters
        for char in script_schema.characters:
            db_char = Character(
                project_id=project.id,
                name=char.name,
                description=char.description,
                appearance_prompt=char.appearance_prompt,
            )
            db.add(db_char)

        # Store Scenes, including any generated dialogue turns, so the
        # asset-generation phase can run later without recomputing anything.
        for sc in script_schema.scenes:
            raw_turns = sc.dialogue_turns or []
            dialogue_turns = [t.model_dump() if hasattr(t, "model_dump") else t for t in raw_turns]
            db_scene = Scene(
                project_id=project.id,
                scene_number=sc.scene_number,
                duration_seconds=sc.duration_seconds,
                location=sc.location,
                visual_description=sc.visual_description,
                narration_text=sc.narration_text,
                image_prompt=f"{sc.image_prompt}, visual style: {script_schema.visual_style}",
                motion_prompt=sc.motion_prompt,
                dialogue_turns=dialogue_turns or None,
            )
            db.add(db_scene)

        project.status = ProjectStatus.SCRIPT_READY
        db.commit()

    except Exception as e:
        db.rollback()
        project.status = ProjectStatus.FAILED
        project.error_message = str(e)
        db.commit()
        logger.error(f"Script breakdown failed for project {project_id}: {e}", exc_info=True)
        raise e
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Phase 2: Asset generation (images -> pose maps -> audio -> video -> composite).
# Triggered explicitly by the user from the review/edit screen, once they are
# happy with the parsed script, characters and scenes (which they may have edited).
# Reads scenes straight from the database, not from the original LLM output,
# so any edits the user made are respected.
# ---------------------------------------------------------------------------
@celery_app.task(bind=True)
def run_asset_pipeline(self, project_id: str):
    db = SessionLocal()
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        logger.error(f"Project ID {project_id} not found.")
        return

    try:
        db_scenes = (
            db.query(Scene)
            .filter(Scene.project_id == project_id)
            .order_by(Scene.scene_number)
            .all()
        )
        if not db_scenes:
            raise RuntimeError("No scenes found for this project. Run script breakdown first.")

        image_service = ImageGenerationService()
        video_service = VideoGenerationService()
        tts_service = TTSService()
        ffmpeg_service = FFmpegService()
        spatial_service = SpatialService()

        project.status = ProjectStatus.GENERATING_ASSETS
        db.commit()

        project_dir = os.path.join(settings.STORAGE_DIR, "projects", project.id)
        scene_rendered_files = []

        for scene in db_scenes:
            if _check_cancelled(db, project):
                return

            scene_dir = os.path.join(project_dir, f"scene_{scene.scene_number}")
            os.makedirs(scene_dir, exist_ok=True)

            # 3a. Generate Keyframe Image
            project.status = ProjectStatus.GENERATING_IMAGES
            db.commit()
            img_path = os.path.join(scene_dir, "keyframe.png")
            image_service.generate_image(scene.image_prompt, img_path)
            scene.image_path = img_path
            db.commit()

            dialogue_turns = scene.dialogue_turns or []
            scene_final_path = os.path.join(scene_dir, "scene_final.mp4")

            if dialogue_turns:
                turn_clips = []
                for idx, turn in enumerate(dialogue_turns):
                    if _check_cancelled(db, project):
                        return

                    speaker = turn.get("speaker", "Character")
                    text = turn.get("text", "")
                    expression = turn.get("expression", "neutral")
                    action = turn.get("action", "gesturing while speaking")

                    turn_aud_path = os.path.join(scene_dir, f"dialogue_{idx}_{speaker}.mp3")
                    turn_vid_path = os.path.join(scene_dir, f"clip_{idx}_{speaker}.mp4")
                    turn_merged_path = os.path.join(scene_dir, f"shot_{idx}_{speaker}.mp4")

                    project.status = ProjectStatus.GENERATING_AUDIO
                    db.commit()
                    tts_service.generate_speech(text, turn_aud_path)

                    aud_duration = 3.5
                    if hasattr(ffmpeg_service, "get_audio_duration"):
                        aud_duration = ffmpeg_service.get_audio_duration(turn_aud_path)

                    pose_map_path = os.path.join(scene_dir, f"pose_{idx}_{speaker}.png")
                    spatial_service.extract_pose_map(img_path, pose_map_path)

                    project.status = ProjectStatus.GENERATING_VIDEOS
                    db.commit()

                    dynamic_motion = (
                        f"Close-up of {speaker} performing action '{action}' with {expression} expression. "
                        f"{scene.motion_prompt or 'cinematic facial movement'}"
                    )

                    video_service.generate_video_from_image(
                        image_path=img_path,
                        output_path=turn_vid_path,
                        motion_prompt=dynamic_motion,
                        narration_text=f"{speaker}: {text}",
                        audio_duration=aud_duration,
                        pose_map_path=pose_map_path,
                    )

                    ffmpeg_service.combine_scene_assets(turn_vid_path, turn_aud_path, turn_merged_path)
                    turn_clips.append(turn_merged_path)

                ffmpeg_service.concatenate_videos(turn_clips, scene_final_path)

            else:
                project.status = ProjectStatus.GENERATING_AUDIO
                db.commit()
                aud_path = os.path.join(scene_dir, "narration.mp3")
                aud_duration = float(scene.duration_seconds or 5.0)

                if scene.narration_text:
                    tts_service.generate_speech(scene.narration_text, aud_path)
                    if hasattr(ffmpeg_service, "get_audio_duration"):
                        aud_duration = ffmpeg_service.get_audio_duration(aud_path)
                scene.audio_path = aud_path

                pose_map_path = os.path.join(scene_dir, "pose_narrator.png")
                spatial_service.extract_pose_map(img_path, pose_map_path)

                project.status = ProjectStatus.GENERATING_VIDEOS
                db.commit()
                vid_path = os.path.join(scene_dir, "raw_clip.mp4")
                video_service.generate_video_from_image(
                    image_path=img_path,
                    output_path=vid_path,
                    motion_prompt=scene.motion_prompt or "dynamic movement, cinematic camera shift",
                    narration_text=scene.narration_text or "",
                    audio_duration=aud_duration,
                    pose_map_path=pose_map_path,
                )
                scene.video_path = vid_path

                ffmpeg_service.combine_scene_assets(vid_path, aud_path, scene_final_path)

            scene_rendered_files.append(scene_final_path)
            db.commit()

        if _check_cancelled(db, project):
            return

        # Step 4: Stitch Final Master Video
        project.status = ProjectStatus.COMPOSITING
        db.commit()
        master_output_path = os.path.join(project_dir, "final_master_video.mp4")
        ffmpeg_service.concatenate_videos(scene_rendered_files, master_output_path)

        project.final_video_path = master_output_path
        project.status = ProjectStatus.COMPLETED
        db.commit()

    except Exception as e:
        db.rollback()
        project.status = ProjectStatus.FAILED
        project.error_message = str(e)
        db.commit()
        logger.error(f"Asset pipeline failed for project {project_id}: {e}", exc_info=True)
        raise e
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Backwards-compatible full run (used by scripts/test_pipeline.py): runs both
# phases back to back with no review step in between.
# ---------------------------------------------------------------------------
@celery_app.task(bind=True)
def run_story_pipeline(self, project_id: str):
    run_script_breakdown.run(project_id)
    return run_asset_pipeline.run(project_id)
