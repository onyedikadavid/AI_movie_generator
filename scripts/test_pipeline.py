import os
import sys
import uuid
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add project root directory to sys.path so app modules import cleanly
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.models.project import Project, ProjectStatus
from app.tasks.pipeline_tasks import run_story_pipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pipeline_tester")

# Synchronous DB Session setup for the test runner
sync_db_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
engine = create_engine(sync_db_url)
SessionLocal = sessionmaker(bind=engine)


def test_full_pipeline_execution():
    db = SessionLocal()
    test_project_id = str(uuid.uuid4())
    
    logger.info(f"--- Starting Pipeline Integration Test [Project ID: {test_project_id}] ---")

    try:
        # 1. Seed Test Project Record
        test_prompt = (
            "A tense sci-fi confrontation between Commander Rex and AI Agent Vex "
            "inside a dimly lit control room. Rex demands answers while Vex calmly explains the system overload."
        )
        
        project = Project(
            id=test_project_id,
            title="Pipeline Test Run",
            raw_prompt=test_prompt,
            status=ProjectStatus.CREATED
        )
        db.add(project)
        db.commit()
        logger.info("Test project created in Database.")

        # 2. Run Celery Task directly (Synchronous Execution)
        logger.info("Executing `run_story_pipeline` task...")
        run_story_pipeline(test_project_id)

        # 3. Verify Database Updates & Physical Output
        db.expire_all()
        updated_project = db.query(Project).filter(Project.id == test_project_id).first()

        logger.info(f"Final Project Status: {updated_project.status}")
        
        if updated_project.status == ProjectStatus.COMPLETED:
            logger.info("Pipeline executed successfully!")
            logger.info(f"Final Output Video: {updated_project.final_video_path}")
            
            if updated_project.final_video_path and os.path.exists(updated_project.final_video_path):
                file_size = os.path.getsize(updated_project.final_video_path)
                logger.info(f"Verified video file exists on disk ({file_size} bytes).")
            else:
                logger.error("Master video path set in DB, but file missing from disk!")
        else:
            logger.error(f"Pipeline failed with error: {updated_project.error_message}")

    except Exception as e:
        logger.exception(f"Integration test encountered an exception: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    test_full_pipeline_execution()