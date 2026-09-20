"""initial_schema

Revision ID: 59efdbdc35fb
Revises:
Create Date: 2026-09-17 09:42:09.747200

NOTE: This revision was regenerated to actually match app/models/*.py.
The original auto-generated file had drifted badly from the SQLAlchemy
models (wrong column names, a missing `scripts` table entirely) - which is
almost certainly why `init_db.py` / `force_create.py` existed as a
workaround using Base.metadata.create_all() instead of real migrations.
If you have an existing dev database created via that workaround, it's
safe: this migration is only used for fresh databases going forward.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '59efdbdc35fb'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    project_status = sa.Enum(
        "CREATED", "TRANSCRIBING", "GENERATING_SCRIPT", "SCRIPT_READY",
        "GENERATING_ASSETS", "GENERATING_IMAGES", "GENERATING_VIDEOS",
        "GENERATING_AUDIO", "COMPOSITING", "COMPLETED", "FAILED",
        name="projectstatus",
    )

    op.create_table(
        'projects',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('raw_prompt', sa.Text(), nullable=True),
        sa.Column('audio_input_path', sa.String(), nullable=True),
        sa.Column('requested_genre', sa.String(), nullable=True),
        sa.Column('requested_tone', sa.String(), nullable=True),
        sa.Column('requested_visual_style', sa.String(), nullable=True),
        sa.Column('status', project_status, nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('final_video_path', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'scripts',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('genre', sa.String(), nullable=True),
        sa.Column('tone', sa.String(), nullable=True),
        sa.Column('visual_style', sa.String(), nullable=True),
        sa.Column('full_text', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'characters',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('appearance_prompt', sa.Text(), nullable=False),
        sa.Column('reference_image_path', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'scenes',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('scene_number', sa.Integer(), nullable=False),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('location', sa.String(), nullable=True),
        sa.Column('visual_description', sa.Text(), nullable=False),
        sa.Column('narration_text', sa.Text(), nullable=True),
        sa.Column('image_prompt', sa.Text(), nullable=False),
        sa.Column('motion_prompt', sa.Text(), nullable=True),
        sa.Column('image_path', sa.String(), nullable=True),
        sa.Column('video_path', sa.String(), nullable=True),
        sa.Column('audio_path', sa.String(), nullable=True),
        sa.Column('dialogue_turns', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('scenes')
    op.drop_table('characters')
    op.drop_table('scripts')
    op.drop_table('projects')
    sa.Enum(name="projectstatus").drop(op.get_bind(), checkfirst=True)
