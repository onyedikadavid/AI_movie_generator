import uuid
import enum
from sqlalchemy import Column, String, Text, Enum, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.core.database import Base

class ProjectStatus(str, enum.Enum):
    CREATED = "CREATED"
    TRANSCRIBING = "TRANSCRIBING"
    GENERATING_SCRIPT = "GENERATING_SCRIPT"
    SCRIPT_READY = "SCRIPT_READY"          # Script/characters/scenes parsed - awaiting user review before heavy generation
    GENERATING_ASSETS = "GENERATING_ASSETS"
    GENERATING_IMAGES = "GENERATING_IMAGES"
    GENERATING_VIDEOS = "GENERATING_VIDEOS"
    GENERATING_AUDIO = "GENERATING_AUDIO"
    COMPOSITING = "COMPOSITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

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
    final_video_path = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    script = relationship("Script", back_populates="project", uselist=False, cascade="all, delete-orphan")
    characters = relationship("Character", back_populates="project", cascade="all, delete-orphan")
    scenes = relationship("Scene", back_populates="project", order_by="Scene.scene_number", cascade="all, delete-orphan")