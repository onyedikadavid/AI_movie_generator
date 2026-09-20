import logging
from sqlalchemy.orm import Session
from app.models.project import Project, ProjectStatus
from app.tasks.pipeline_tasks import run_script_breakdown, run_asset_pipeline

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """
    Service responsible for validating project state and dispatching the
    correct Celery task for each phase of the pipeline. Kept separate from
    the API layer so the same rules apply whether it's called from a route,
    a script, or a future admin/CLI tool.
    """

    def __init__(self, db: Session):
        self.db = db

    def start_script_breakdown(self, project_id: str) -> Project:
        """Kicks off Phase 1 (STT + LLM script/character/scene parsing)."""
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ValueError(f"Project with ID '{project_id}' does not exist.")

        run_script_breakdown.delay(project_id=project.id)
        logger.info(f"Script breakdown dispatched for Project ID: {project.id}")
        return project

    def start_asset_pipeline(self, project_id: str) -> Project:
        """
        Kicks off Phase 2 (image/video/audio generation + composition).
        Only valid once the script has been reviewed, i.e. status is
        SCRIPT_READY (or the previous run FAILED and the user wants to retry).
        """
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ValueError(f"Project with ID '{project_id}' does not exist.")

        in_flight_statuses = {
            ProjectStatus.GENERATING_ASSETS,
            ProjectStatus.GENERATING_IMAGES,
            ProjectStatus.GENERATING_VIDEOS,
            ProjectStatus.GENERATING_AUDIO,
            ProjectStatus.COMPOSITING,
        }
        if project.status in in_flight_statuses:
            logger.warning(f"Project '{project_id}' is already generating assets.")
            return project

        if project.status not in {ProjectStatus.SCRIPT_READY, ProjectStatus.FAILED, ProjectStatus.COMPLETED}:
            raise ValueError(
                f"Project '{project_id}' is not ready for asset generation (status: {project.status})."
            )

        project.error_message = None
        self.db.commit()

        run_asset_pipeline.delay(project_id=project.id)
        logger.info(f"Asset pipeline dispatched for Project ID: {project.id}")
        return project
