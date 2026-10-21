"""continue-the-story support

Revision ID: d7a14f90c3e8
Revises: c6f03e18b2d5
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d7a14f90c3e8"
down_revision: Union[str, Sequence[str], None] = "c6f03e18b2d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS continue_prompt TEXT")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS continue_scenes INTEGER")
    op.execute("ALTER TABLE scenes ADD COLUMN IF NOT EXISTS continues_previous BOOLEAN")


def downgrade() -> None:
    op.execute("ALTER TABLE scenes DROP COLUMN IF EXISTS continues_previous")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS continue_scenes")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS continue_prompt")
