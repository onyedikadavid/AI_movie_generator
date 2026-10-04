"""run control (queue/pause/resume/delete), progress, per-character voices

Revision ID: d4f8b2a91c37
Revises: a1c3e7f92b04
Create Date: 2026-10-02 12:00:00.000000

Every statement is idempotent (IF NOT EXISTS / ON CONFLICT), so it is safe to
run against a database that was created by init_db.py / create_all() as well
as one built by the earlier migrations, and safe to run twice.

Adds:
  * QUEUED and PAUSED project statuses
  * projects: pause/delete flags, run_token, queued/started/heartbeat
    timestamps, progress fields, warning_message, resume_count
  * scenes: render_status, characters_present
  * characters: gender, age_group, voice_id
  * pipeline_lease: the single-row "only one project runs at a time" mutex
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d4f8b2a91c37"
down_revision: Union[str, Sequence[str], None] = "a1c3e7f92b04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE can't run inside a transaction block on older
    # Postgres versions, so run these outside of Alembic's transaction.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'QUEUED'")
        op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'PAUSED'")

    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS pause_requested BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS delete_requested BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS celery_task_id VARCHAR")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS run_token VARCHAR")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS queued_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS progress_pct INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS stage_detail TEXT")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS current_scene INTEGER")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS total_scenes INTEGER")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS warning_message TEXT")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS resume_count INTEGER NOT NULL DEFAULT 0")

    op.execute("ALTER TABLE scenes ADD COLUMN IF NOT EXISTS render_status VARCHAR NOT NULL DEFAULT 'PENDING'")
    op.execute("ALTER TABLE scenes ADD COLUMN IF NOT EXISTS characters_present JSON")

    op.execute("ALTER TABLE characters ADD COLUMN IF NOT EXISTS gender VARCHAR")
    op.execute("ALTER TABLE characters ADD COLUMN IF NOT EXISTS age_group VARCHAR")
    op.execute("ALTER TABLE characters ADD COLUMN IF NOT EXISTS voice_id VARCHAR")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_lease (
            id INTEGER PRIMARY KEY,
            project_id VARCHAR,
            holder VARCHAR,
            heartbeat_at TIMESTAMP WITH TIME ZONE
        )
        """
    )
    op.execute("INSERT INTO pipeline_lease (id) VALUES (1) ON CONFLICT (id) DO NOTHING")

    # Scenes of projects that already finished are, by definition, rendered.
    op.execute(
        "UPDATE scenes SET render_status = 'DONE' "
        "WHERE project_id IN (SELECT id FROM projects WHERE status::text = 'COMPLETED')"
    )


def downgrade() -> None:
    # Postgres can't drop enum values, and dropping the new columns would throw
    # away user data for no benefit, so the downgrade only removes the lease table.
    op.execute("DROP TABLE IF EXISTS pipeline_lease")
