"""story bible: locations, character core look / outfits

Revision ID: a2d5f8c1e703
Revises: f9c36b12e5a0
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a2d5f8c1e703"
down_revision: Union[str, Sequence[str], None] = "f9c36b12e5a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN IF NOT EXISTS core_look TEXT")
    op.execute("ALTER TABLE characters ADD COLUMN IF NOT EXISTS outfit TEXT")
    op.execute("ALTER TABLE characters ADD COLUMN IF NOT EXISTS outfits JSON")
    op.execute("""CREATE TABLE IF NOT EXISTS locations (
        id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL REFERENCES projects(id), key VARCHAR NOT NULL,
        name VARCHAR NOT NULL, description TEXT NOT NULL DEFAULT '', reference_image_path VARCHAR)""")
    op.execute("ALTER TABLE scenes ADD COLUMN IF NOT EXISTS location_id VARCHAR REFERENCES locations(id) ON DELETE SET NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE scenes DROP COLUMN IF EXISTS location_id")
    op.execute("DROP TABLE IF EXISTS locations")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS outfits")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS outfit")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS core_look")
