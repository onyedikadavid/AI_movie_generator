"""per-project video mode (image-first or text-to-video)

Revision ID: f3a9c1d27b68
Revises: e7b2c4d81f56
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f3a9c1d27b68"
down_revision: Union[str, Sequence[str], None] = "e7b2c4d81f56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS video_mode VARCHAR")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS video_mode")
