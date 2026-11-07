import uuid
from sqlalchemy import Column, String, Text, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.core.database import Base


class Character(Base):
    __tablename__ = "characters"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    appearance_prompt = Column(Text, nullable=False)
    reference_image_path = Column(String, nullable=True)

    # Used to give every character their own, gender-appropriate voice.
    gender = Column(String, nullable=True)      # "male" | "female" | null (= work it out automatically)
    age_group = Column(String, nullable=True)   # "child" | "teen" | "adult" | "elder"
    voice_id = Column(String, nullable=True)    # assigned automatically on first render, then kept stable

    # Character bible. `core_look` is the part that NEVER changes (face, hair, skin, build, age); `outfit` is what they wear by
    # default and `outfits` maps a location key to what they wear THERE (e.g. {"carpenter-workshop": "leather apron ..."}).
    # The look used in every scene = core_look + the outfit for that scene's place. Empty = use appearance_prompt as before.
    core_look = Column(Text, nullable=True)
    outfit = Column(Text, nullable=True)
    outfits = Column(JSON, nullable=True)

    project = relationship("Project", back_populates="characters")
