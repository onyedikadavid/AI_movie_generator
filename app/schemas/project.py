from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from app.models.project import ProjectStatus
from app.schemas.script import ScriptResponse
from app.schemas.character import CharacterResponse
from app.schemas.scene import SceneResponse


class ProjectCreate(BaseModel):
    raw_prompt: Optional[str] = None
    # Optional stylistic toggles from the "New Project" form - used as hints for the LLM breakdown.
    genre: Optional[str] = None
    tone: Optional[str] = None
    visual_style: Optional[str] = None


class ProjectListItem(BaseModel):
    """Slim shape for the dashboard grid - avoids loading every scene/character for every card."""
    id: str
    title: Optional[str]
    status: ProjectStatus
    raw_prompt: Optional[str]
    error_message: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ProjectResponse(BaseModel):
    id: str
    title: Optional[str]
    status: ProjectStatus
    raw_prompt: Optional[str]
    final_video_path: Optional[str]
    error_message: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ProjectDetailResponse(ProjectResponse):
    """Full shape used by the review/edit and player views."""
    script: Optional[ScriptResponse] = None
    characters: List[CharacterResponse] = []
    scenes: List[SceneResponse] = []

    class Config:
        from_attributes = True
