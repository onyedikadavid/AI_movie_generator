"""
Idempotent, additive schema upgrade, run automatically when the API and the
worker start - so deploying this version never requires remembering to run a
migration by hand. Every statement is IF NOT EXISTS / ON CONFLICT DO NOTHING,
so it is safe on a database built by Alembic, by init_db.py, or by an older
version of this app, and safe to run any number of times concurrently.
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

ENUM_VALUES = ["CANCELLED", "QUEUED", "PAUSED"]

STATEMENTS = [
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS pause_requested BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS delete_requested BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS celery_task_id VARCHAR",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS run_token VARCHAR",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS queued_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS progress_pct INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS stage_detail TEXT",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS current_scene INTEGER",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS total_scenes INTEGER",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS warning_message TEXT",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS resume_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE scenes ADD COLUMN IF NOT EXISTS render_status VARCHAR NOT NULL DEFAULT 'PENDING'",
    "ALTER TABLE scenes ADD COLUMN IF NOT EXISTS characters_present JSON",
    "ALTER TABLE scenes ADD COLUMN IF NOT EXISTS sound_design TEXT",
    "ALTER TABLE characters ADD COLUMN IF NOT EXISTS gender VARCHAR",
    "ALTER TABLE characters ADD COLUMN IF NOT EXISTS age_group VARCHAR",
    "ALTER TABLE characters ADD COLUMN IF NOT EXISTS voice_id VARCHAR",
    """CREATE TABLE IF NOT EXISTS pipeline_lease (
        id INTEGER PRIMARY KEY, project_id VARCHAR, holder VARCHAR, heartbeat_at TIMESTAMP WITH TIME ZONE)""",
    "INSERT INTO pipeline_lease (id) VALUES (1) ON CONFLICT (id) DO NOTHING",
]


def ensure_schema(engine) -> None:
    """Apply the upgrade using a SYNC engine. Never raises - a failure here is
    logged loudly instead of stopping the server from starting."""
    try:
        # ALTER TYPE ... ADD VALUE must be committed before the value is used.
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            for value in ENUM_VALUES:
                try:
                    conn.execute(text(f"ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS '{value}'"))
                except Exception as e:  # noqa: BLE001 - type may not exist yet on a brand new DB
                    logger.info("enum value %s skipped: %s", value, e)
    except Exception as e:  # noqa: BLE001
        logger.warning("Schema upgrade (enum step) failed: %s", e)

    try:
        with engine.begin() as conn:
            exists = conn.execute(text("SELECT to_regclass('public.projects')")).scalar()
            if not exists:
                logger.info("projects table doesn't exist yet - run init_db.py / alembic first; skipping schema upgrade.")
                return
            for stmt in STATEMENTS:
                conn.execute(text(stmt))
        logger.info("Database schema is up to date.")
    except Exception as e:  # noqa: BLE001
        logger.error("Schema upgrade failed: %s - the app may error until this is fixed. "
                     "You can also run: python scripts/upgrade_schema.py", e)
