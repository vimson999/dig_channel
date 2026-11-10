"""Create hotspot and X candidate tables."""
from alembic import op
import sqlalchemy as sa

revision = "20261105_hotspot_sync"
down_revision = "20261027_x_content"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('hotspots',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('source_platform', sa.String(length=32), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=True),
        sa.Column('heat_value', sa.String(length=64), nullable=True),
        sa.Column('raw_url', sa.String(length=512), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('scraped_at', sa.DateTime(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
    )
    op.create_index('ix_hotspots_scraped_at', 'hotspots', ['scraped_at'])
    op.create_table('x_posts',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('hotspot_id', sa.String(length=64), sa.ForeignKey('hotspots.id'), nullable=False),
        sa.Column('persona', sa.String(length=64), nullable=False),
        sa.Column('post_type', sa.String(length=32), nullable=False),
        sa.Column('hook', sa.String(length=255), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('call_to_action', sa.String(length=255), nullable=True),
        sa.Column('suggested_tags', sa.JSON(), nullable=False),
        sa.Column('publish_status', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_x_posts_hotspot_id', 'x_posts', ['hotspot_id'])


def downgrade():
    op.drop_table('x_posts')
    op.drop_table('hotspots')
