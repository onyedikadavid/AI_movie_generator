from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from app.models.project import ProjectStatus
from app.schemas.script import ScriptResponse
from app.schemas.character import CharacterResponse
from app.schemas.scene import SceneResponse


def normalize_video_mode(value: Optional[str]) -> Optional[str]:
    """'t2v' / 'i2v' (also accepts friendly spellings); anything else = None (use the server default)."""
    v = (value or "").strip().lower().replace("_", "-")
    if v in ("t2v", "text", "text-to-video", "video"):
        return "t2v"
    if v in ("i2v", "image", "image-to-video", "image-first", "images"):
        return "i2v"
    return None


class ProjectCreate(BaseModel):
    raw_prompt: Optional[str] = None
    # Optional stylistic toggles from the "New Project" form - used as hints for the LLM breakdown.
    genre: Optional[str] = None
    tone: Optional[str] = None
    visual_style: Optional[str] = None
    # "i2v" (image model first, then animate) or "t2v" (text-to-video). None = server default.
    video_mode: Optional[str] = None


class ProjectSettingsUpdate(BaseModel):
    """Per-project generation settings, editable on the review screen before (re)generating."""
    video_mode: Optional[str] = None


class RunState(BaseModel):
    """Everything the UI needs to say honestly what a project is doing right now."""
    status: ProjectStatus
    error_message: Optional[str] = None
    warning_message: Optional[str] = None
    stage_detail: Optional[str] = None
    progress_pct: int = 0
    current_scene: Optional[int] = None
    total_scenes: Optional[int] = None
    # A stop was requested and the worker hasn't finished stopping yet.
    pause_requested: bool = False
    cancel_requested: bool = False
    delete_requested: bool = False
    # How many times this project has been resumed after pause / failure / cancel.
    resume_count: int = 0
    # 1 = next to run (only set while status is QUEUED).
    queue_position: Optional[int] = None
    heartbeat_at: Optional[datetime] = None


class ProjectListItem(RunState):
    """Slim shape for the dashboard grid - avoids loading every scene/character for every card."""
    id: str
    title: Optional[str]
    raw_prompt: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ProjectResponse(RunState):
    id: str
    title: Optional[str]
    raw_prompt: Optional[str]
    final_video_path: Optional[str]
    video_mode: Optional[str] = None
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


class QueueEntry(BaseModel):
    id: str
    title: str
    position: int


class RunningEntry(BaseModel):
    id: str
    title: str
    status: str
    progress_pct: int = 0
    stage_detail: Optional[str] = None
    current_scene: Optional[int] = None
    total_scenes: Optional[int] = None
    pause_requested: bool = False
    cancel_requested: bool = False
    delete_requested: bool = False
    heartbeat_age_seconds: Optional[float] = None


class QueueSnapshot(BaseModel):
    running: Optional[RunningEntry] = None
    queued: List[QueueEntry] = []
    oldest_queued_age_seconds: Optional[float] = None


class DeleteResult(BaseModel):
    deleted: bool
    deleting: bool
