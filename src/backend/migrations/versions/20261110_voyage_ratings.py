"""Persist voyage analysis ratings."""
from alembic import op
import sqlalchemy as sa
revision = '20261110_voyage_ratings'
down_revision = '20261110_voyage_analyses'
branch_labels = None
depends_on = None

def upgrade():
    if not sa.inspect(op.get_bind()).has_table('voyage_analysis_ratings'):
        op.create_table('voyage_analysis_ratings',
            sa.Column('analysis_id', sa.String(200), primary_key=True),
            sa.Column('score', sa.Integer(), nullable=False),
            sa.Column('grade', sa.String(8), nullable=False),
            sa.Column('reason', sa.Text(), nullable=False))

def downgrade():
    op.drop_table('voyage_analysis_ratings')
