"""Persist independent Shengcai project-list snapshots and import receipts."""
from alembic import op
import sqlalchemy as sa

revision = '20261021_shengcai_catalog'
down_revision = '20260930_add_bili_charging_competitor'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table('shengcai_projects'):
        op.create_table('shengcai_projects',
            sa.Column('id', sa.String(32), primary_key=True),
            sa.Column('name', sa.String(500), nullable=False),
            sa.Column('summary', sa.Text()),
            sa.Column('raw_payload', sa.JSON(), nullable=False),
            sa.Column('payload_sha256', sa.String(64), nullable=False),
            sa.Column('first_imported_at', sa.DateTime(), nullable=False),
            sa.Column('last_seen_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False))
    if not sa.inspect(bind).has_table('shengcai_project_imports'):
        op.create_table('shengcai_project_imports',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('source_sha256', sa.String(64), nullable=False),
            sa.Column('source_filename', sa.String(255), nullable=False),
            sa.Column('item_count', sa.Integer(), nullable=False),
            sa.Column('source_total', sa.Integer(), nullable=False),
            sa.Column('new_count', sa.Integer(), nullable=False),
            sa.Column('updated_count', sa.Integer(), nullable=False),
            sa.Column('unchanged_count', sa.Integer(), nullable=False),
            sa.Column('imported_at', sa.DateTime(), nullable=False))
        op.create_index('ix_shengcai_project_imports_source_sha256', 'shengcai_project_imports', ['source_sha256'])
        op.create_index('ix_shengcai_project_imports_imported_at', 'shengcai_project_imports', ['imported_at'])


def downgrade():
    op.drop_table('shengcai_project_imports')
    op.drop_table('shengcai_projects')
