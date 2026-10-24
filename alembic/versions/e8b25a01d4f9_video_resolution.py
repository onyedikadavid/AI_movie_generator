"""per-project video resolution

Revision ID: e8b25a01d4f9
Revises: d7a14f90c3e8
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e8b25a01d4f9"
down_revision: Union[str, Sequence[str], None] = "d7a14f90c3e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS video_resolution VARCHAR")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS video_resolution")
