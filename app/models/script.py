import uuid
from sqlalchemy import Column, String, Text, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.core.database import Base

class Script(Base):
    __tablename__ = "scripts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    genre = Column(String, nullable=True)
    tone = Column(String, nullable=True)
    visual_style = Column(String, nullable=True)
    full_text = Column(Text, nullable=True)
    # The story the script writer built from the user's idea (shown in the app), its hook, and its analysis.
    story_text = Column(Text, nullable=True)
    story_hook = Column(Text, nullable=True)
    story_outline = Column(JSON, nullable=True)

    project = relationship("Project", back_populates="script")