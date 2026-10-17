"""per-project narrator on/off

Revision ID: a8d41c6e93f2
Revises: f3a9c1d27b68
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a8d41c6e93f2"
down_revision: Union[str, Sequence[str], None] = "f3a9c1d27b68"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS narrator_enabled BOOLEAN")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS narrator_enabled")
