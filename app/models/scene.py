import uuid
from sqlalchemy import Column, String, Text, Integer, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.core.database import Base


class Scene(Base):
    __tablename__ = "scenes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    scene_number = Column(Integer, nullable=False)
    duration_seconds = Column(Integer, default=5)
    location = Column(String, nullable=True)
    visual_description = Column(Text, nullable=False)
    narration_text = Column(Text, nullable=True)
    image_prompt = Column(Text, nullable=False)
    motion_prompt = Column(Text, nullable=True)
    # Background sound / effects for the scene ("distant gunfire, helicopter, crowd panic") - used in hybrid audio mode.
    sound_design = Column(Text, nullable=True)

    image_path = Column(String, nullable=True)
    video_path = Column(String, nullable=True)   # the finished scene clip (picture + voices)
    audio_path = Column(String, nullable=True)

    # Persisted multi-character dialogue turns (list of {speaker, text, expression, action}).
    # Needed so the asset-generation phase can run independently of (and after) script review/editing.
    dialogue_turns = Column(JSON, nullable=True)

    # Names of the characters on screen in this scene (from the LLM breakdown).
    characters_present = Column(JSON, nullable=True)

    # PENDING -> RENDERING -> DONE (or FAILED). Lets the UI show per-scene
    # progress and lets a resumed run skip scenes that are already finished.
    render_status = Column(String, default="PENDING", nullable=False, server_default="PENDING")

    project = relationship("Project", back_populates="scenes")
