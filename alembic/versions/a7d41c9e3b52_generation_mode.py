"""per-project generation mode (script / direct)

Revision ID: a7d41c9e3b52
Revises: f9c36b12e5a0
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a7d41c9e3b52"
down_revision: Union[str, Sequence[str], None] = "f9c36b12e5a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS generation_mode VARCHAR")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS generation_mode")
