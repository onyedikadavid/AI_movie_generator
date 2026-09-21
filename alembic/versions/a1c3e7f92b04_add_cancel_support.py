"""add cancel support

Revision ID: a1c3e7f92b04
Revises: 59efdbdc35fb
Create Date: 2026-09-21 18:00:00.000000

Adds cooperative-cancellation support: a cancel_requested flag the running
task checks between steps, a celery_task_id column so the API can look up
which task belongs to which project, and a CANCELLED project status.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c3e7f92b04'
down_revision: Union[str, Sequence[str], None] = '59efdbdc35fb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('projects', sa.Column('celery_task_id', sa.String(), nullable=True))
    # Postgres requires new enum values added outside the value's own
    # transaction before use, but adding it here (without using it in this
    # same migration) works fine on Postgres 12+, which Neon runs.
    op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'CANCELLED'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE - removing an enum value
    # requires rebuilding the type, which isn't worth doing for a downgrade
    # path. The two columns can be dropped safely.
    op.drop_column('projects', 'celery_task_id')
    op.drop_column('projects', 'cancel_requested')
