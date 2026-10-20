"""store the generated story with the script

Revision ID: c6f03e18b2d5
Revises: b5e92d07a1c4
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c6f03e18b2d5"
down_revision: Union[str, Sequence[str], None] = "b5e92d07a1c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE scripts ADD COLUMN IF NOT EXISTS story_text TEXT")
    op.execute("ALTER TABLE scripts ADD COLUMN IF NOT EXISTS story_hook TEXT")
    op.execute("ALTER TABLE scripts ADD COLUMN IF NOT EXISTS story_outline JSON")


def downgrade() -> None:
    op.execute("ALTER TABLE scripts DROP COLUMN IF EXISTS story_outline")
    op.execute("ALTER TABLE scripts DROP COLUMN IF EXISTS story_hook")
    op.execute("ALTER TABLE scripts DROP COLUMN IF EXISTS story_text")
