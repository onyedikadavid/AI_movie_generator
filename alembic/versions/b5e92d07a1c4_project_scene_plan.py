"""per-project scene count and scene length

Revision ID: b5e92d07a1c4
Revises: a8d41c6e93f2
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b5e92d07a1c4"
down_revision: Union[str, Sequence[str], None] = "a8d41c6e93f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS scene_count INTEGER")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS scene_seconds INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS scene_seconds")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS scene_count")
