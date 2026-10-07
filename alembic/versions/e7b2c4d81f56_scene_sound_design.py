"""scene sound_design (background sound / effects for hybrid audio)

Revision ID: e7b2c4d81f56
Revises: d4f8b2a91c37
Create Date: 2026-10-06 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e7b2c4d81f56"
down_revision: Union[str, Sequence[str], None] = "d4f8b2a91c37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE scenes ADD COLUMN IF NOT EXISTS sound_design TEXT")


def downgrade() -> None:
    pass  # additive column; keep user data
