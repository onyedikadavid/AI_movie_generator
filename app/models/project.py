import uuid
import enum
from sqlalchemy import Column, String, Text, Enum, DateTime, Boolean, Integer
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.core.database import Base


class ProjectStatus(str, enum.Enum):
    CREATED = "CREATED"                    # legacy - rows created before QUEUED existed
    TRANSCRIBING = "TRANSCRIBING"
    GENERATING_SCRIPT = "GENERATING_SCRIPT"
    SCRIPT_READY = "SCRIPT_READY"          # Script/characters/scenes parsed - awaiting user review before heavy generation
    QUEUED = "QUEUED"                      # Waiting for the (single) worker slot - NOT running yet
    GENERATING_ASSETS = "GENERATING_ASSETS"
    GENERATING_IMAGES = "GENERATING_IMAGES"
    GENERATING_VIDEOS = "GENERATING_VIDEOS"
    GENERATING_AUDIO = "GENERATING_AUDIO"
    COMPOSITING = "COMPOSITING"
    PAUSED = "PAUSED"                      # Stopped on request; every finished step is kept and "Resume" continues from there
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Statuses in which a worker is genuinely executing this project right now.
ACTIVE_STATUSES = (
    ProjectStatus.TRANSCRIBING,
    ProjectStatus.GENERATING_SCRIPT,
    ProjectStatus.GENERATING_ASSETS,
    ProjectStatus.GENERATING_IMAGES,
    ProjectStatus.GENERATING_VIDEOS,
    ProjectStatus.GENERATING_AUDIO,
    ProjectStatus.COMPOSITING,
)

# Active, or waiting for a worker (CREATED is the pre-QUEUED legacy equivalent).
BUSY_STATUSES = ACTIVE_STATUSES + (ProjectStatus.QUEUED, ProjectStatus.CREATED)


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, nullable=True)
    raw_prompt = Column(Text, nullable=True)
    audio_input_path = Column(String, nullable=True)

    # Optional stylistic toggles captured on the "New Project" form (PRD 4.2),
    # passed to the LLM as hints when it writes the script.
    requested_genre = Column(String, nullable=True)
    requested_tone = Column(String, nullable=True)
    requested_visual_style = Column(String, nullable=True)

    status = Column(Enum(ProjectStatus), default=ProjectStatus.CREATED)
    error_message = Column(Text, nullable=True)
    # Non-fatal problem worth telling the user about (e.g. neural voices were
    # unavailable so a basic voice was used). Shown as an amber note in the UI.
    warning_message = Column(Text, nullable=True)
    final_video_path = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # Run control. All three flags are *cooperative*: the API sets them, the
    # worker notices within a few seconds (its heartbeat thread polls them)
    # and stops cleanly between steps - see app/services/run_control.py.
    # ------------------------------------------------------------------
    cancel_requested = Column(Boolean, default=False, nullable=False)
    pause_requested = Column(Boolean, default=False, nullable=False)
    delete_requested = Column(Boolean, default=False, nullable=False)
    celery_task_id = Column(String, nullable=True)

    # Identifies the ONE run that currently owns this project. Every enqueue
    # mints a fresh token and the worker may only start if the token still
    # matches - so a duplicate/stale/zombie Celery message can never start a
    # second concurrent run of the same project.
    run_token = Column(String, nullable=True)
    queued_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    # Refreshed every few seconds by the worker while it is really running. An
    # "active" project whose heartbeat has gone quiet is a dead run, and is
    # flagged as such instead of showing "Generating..." forever.
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)

    # Live progress, shown on the dashboard and player.
    progress_pct = Column(Integer, default=0, nullable=False, server_default="0")
    stage_detail = Column(Text, nullable=True)
    current_scene = Column(Integer, nullable=True)
    total_scenes = Column(Integer, nullable=True)
    resume_count = Column(Integer, default=0, nullable=False, server_default="0")

    script = relationship("Script", back_populates="project", uselist=False, cascade="all, delete-orphan")
    characters = relationship("Character", back_populates="project", cascade="all, delete-orphan")
    scenes = relationship("Scene", back_populates="project", order_by="Scene.scene_number", cascade="all, delete-orphan")
