"""per-project scene style (whole / shots / reuse)

Revision ID: f9c36b12e5a0
Revises: e8b25a01d4f9
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f9c36b12e5a0"
down_revision: Union[str, Sequence[str], None] = "e8b25a01d4f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS scene_style VARCHAR")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS scene_style")
