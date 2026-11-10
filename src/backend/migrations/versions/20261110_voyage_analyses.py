"""Create an independent table for November voyage project analyses."""
from alembic import op
import sqlalchemy as sa


revision = '20261110_voyage_analyses'
down_revision = '20261105_hotspot_images'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table('voyage_analyses'):
        op.create_table(
            'voyage_analyses',
            sa.Column('id', sa.String(length=200), primary_key=True),
            sa.Column('activity_id', sa.String(length=32), nullable=False, unique=True),
            sa.Column('title', sa.String(length=500), nullable=False),
            sa.Column('analysis_date', sa.Date(), nullable=True),
            sa.Column('status', sa.String(length=100), nullable=False),
            sa.Column('summary', sa.Text(), nullable=False),
            sa.Column('content_markdown', sa.Text(), nullable=False),
            sa.Column('sort_order', sa.Integer(), nullable=False),
            sa.Column('content_sha256', sa.String(length=64), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_voyage_analyses_sort_order', 'voyage_analyses', ['sort_order'])


def downgrade():
    if sa.inspect(op.get_bind()).has_table('voyage_analyses'):
        op.drop_table('voyage_analyses')
