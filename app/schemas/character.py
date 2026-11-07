# app/schemas/character.py
from pydantic import BaseModel
from typing import Dict, Optional


class CharacterBase(BaseModel):
    name: str
    description: str
    appearance_prompt: str
    reference_image_path: Optional[str] = None
    gender: Optional[str] = None      # "male" | "female"
    age_group: Optional[str] = None   # "child" | "teen" | "adult" | "elder"
    voice_id: Optional[str] = None
    # Character bible: what never changes, the usual outfit, and outfits per place ({location key: clothing}).
    core_look: Optional[str] = None
    outfit: Optional[str] = None
    outfits: Optional[Dict[str, str]] = None


class CharacterCreate(CharacterBase):
    pass


class CharacterUpdate(BaseModel):
    """All fields optional - used for partial edits during script review."""
    name: Optional[str] = None
    description: Optional[str] = None
    appearance_prompt: Optional[str] = None
    gender: Optional[str] = None
    age_group: Optional[str] = None
    core_look: Optional[str] = None
    outfit: Optional[str] = None
    outfits: Optional[Dict[str, str]] = None


class CharacterResponse(CharacterBase):
    id: str
    project_id: str

    class Config:
        from_attributes = True
