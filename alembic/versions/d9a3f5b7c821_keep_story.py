"""projects.keep_story

Revision ID: d9a3f5b7c821
Revises: c5e2a8b97f13
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d9a3f5b7c821"
down_revision: Union[str, Sequence[str], None] = "c5e2a8b97f13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS keep_story BOOLEAN")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS keep_story")
