# app/schemas/scene.py
from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class DialogueTurnOut(BaseModel):
    speaker: str
    text: str
    expression: Optional[str] = "neutral"
    action: Optional[str] = "gesturing while speaking"


class SceneBase(BaseModel):
    scene_number: int
    duration_seconds: int = 5
    location: Optional[str] = None
    visual_description: str
    narration_text: Optional[str] = None
    image_prompt: str
    motion_prompt: Optional[str] = None


class SceneCreate(SceneBase):
    pass


class SceneUpdate(BaseModel):
    """All fields optional - used for partial edits during script review, before generation runs."""
    duration_seconds: Optional[int] = None
    location: Optional[str] = None
    visual_description: Optional[str] = None
    narration_text: Optional[str] = None
    image_prompt: Optional[str] = None
    motion_prompt: Optional[str] = None


class SceneResponse(SceneBase):
    id: str
    project_id: str
    image_path: Optional[str] = None
    video_path: Optional[str] = None
    audio_path: Optional[str] = None
    dialogue_turns: Optional[List[Dict[str, Any]]] = None

    class Config:
        from_attributes = True
