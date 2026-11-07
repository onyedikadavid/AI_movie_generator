import uuid
from sqlalchemy import Column, String, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class Location(Base):
    """One PLACE in the story's scene bible (e.g. "carpenter workshop"). Every scene set there reuses this one
    description (and seed), so the place looks the same each time it comes back."""
    __tablename__ = "locations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    key = Column(String, nullable=False)                 # stable slug, e.g. "carpenter-workshop"
    name = Column(String, nullable=False)                # shown in the UI
    description = Column(Text, nullable=False, default="")   # the PLACE only: layout, props, colours, light - no people
    reference_image_path = Column(String, nullable=True)     # first keyframe drawn for this place (shown in the bible)

    project = relationship("Project", back_populates="locations")
