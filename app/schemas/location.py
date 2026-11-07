from pydantic import BaseModel
from typing import Optional


class LocationResponse(BaseModel):
    id: str
    project_id: str
    key: str
    name: str
    description: str = ""
    reference_image_path: Optional[str] = None

    class Config:
        from_attributes = True


class LocationUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class LocationMerge(BaseModel):
    """Two entries that are really the same place: everything in `from_id` moves into `into_id`."""
    from_id: str
    into_id: str
