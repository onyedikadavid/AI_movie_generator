"""image review step: IMAGES_READY status + projects.images_approved

Revision ID: c5e2a8b97f13
Revises: a7d41c9e3b52
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c5e2a8b97f13"
down_revision: Union[str, Sequence[str], None] = "a7d41c9e3b52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS images_approved BOOLEAN")
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'IMAGES_READY'")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS images_approved")
