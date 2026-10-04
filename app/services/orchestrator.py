import logging
from sqlalchemy.orm import Session

from app.models.project import Project
from app.services import run_control as rc

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """
    Thin wrapper kept for scripts / future admin tools. All real rules (single
    running slot, queueing, resume, pause...) live in app/services/run_control.py,
    so every entry point behaves identically.
    """

    def __init__(self, db: Session):
        self.db = db

    def start_script_breakdown(self, project_id: str) -> Project:
        project = self.db.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project with ID '{project_id}' does not exist.")
        return rc.start_new_script(self.db, project)

    def start_asset_pipeline(self, project_id: str, fresh: bool = False) -> Project:
        try:
            return rc.start_generation(self.db, project_id, fresh=fresh)
        except rc.ActionError as e:
            raise ValueError(e.detail) from e
