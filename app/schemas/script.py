from typing import List, Optional
from pydantic import BaseModel, Field

# --- LLM Parsing Schemas ---

class DialogueTurn(BaseModel):
    speaker: str = Field(
        ...,
        description="Name of the character speaking (must match a character in the characters list)."
    )
    text: str = Field(
        ...,
        description="The spoken dialogue line for this turn."
    )
    expression: Optional[str] = Field(
        default="neutral",
        description="Facial expression or emotional tone during speech (e.g., excited, skeptical, angry)."
    )
    action: Optional[str] = Field(
        default="gesturing while speaking",
        description="Physical body motion or movement cue for spatial pose tracking (e.g., jumping, walking towards character, sitting)."
    )


class CharacterSchema(BaseModel):
    name: str = Field(..., description="Unique name of the character.")
    description: str = Field(..., description="Role and personality traits of the character.")
    appearance_prompt: str = Field(..., description="Detailed visual features for image consistency (clothing, hair, skin, age).")


class SceneSchema(BaseModel):
    scene_number: int = Field(..., description="Sequential index of the scene starting at 1.")
    duration_seconds: int = Field(..., description="Estimated duration of the scene in seconds.")
    location: str = Field(..., description="Setting or background environment for the scene.")
    visual_description: str = Field(..., description="Detailed visual action and layout of the scene.")
    narration_text: Optional[str] = Field(
        default=None,
        description="Voiceover or narrator text (leave empty/null if the scene relies strictly on character dialogue)."
    )
    characters_present: List[str] = Field(
        default_factory=list,
        description="List of character names present in this scene."
    )
    image_prompt: Optional[str] = Field(
        default="",
        description="High-fidelity realistic text-to-image prompt for the scene keyframe."
    )
    motion_prompt: str = Field(..., description="Camera movement and motion direction for video generation.")
    dialogue_turns: List[DialogueTurn] = Field(
        default_factory=list,
        description="Ordered list of back-and-forth dialogue turns spoken by characters in this scene."
    )


class ScriptDecompositionSchema(BaseModel):
    title: str = Field(..., description="Title of the story/script.")
    genre: str = Field(..., description="Genre of the production.")
    tone: str = Field(..., description="Overall tone of the video.")
    visual_style: str = Field(..., description="Consistent visual aesthetics and lighting style.")
    characters: List[CharacterSchema] = Field(..., description="List of characters participating in the project.")
    scenes: List[SceneSchema] = Field(..., description="Sequential breakdown of scenes.")


# --- FastAPI Database Request/Response Schemas ---

class ScriptBase(BaseModel):
    genre: Optional[str] = None
    tone: Optional[str] = None
    visual_style: Optional[str] = None
    full_text: Optional[str] = None

class ScriptCreate(ScriptBase):
    pass

class ScriptResponse(ScriptBase):
    id: str
    project_id: str

    class Config:
        from_attributes = True