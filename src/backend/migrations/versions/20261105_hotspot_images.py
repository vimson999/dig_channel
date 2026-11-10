"""Store hotspot evidence and cover images."""
from alembic import op
import sqlalchemy as sa

revision = '20261105_hotspot_images'
down_revision = '20261105_hotspot_sync'
branch_labels = None
depends_on = None


def upgrade():
    existing = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('hotspots')}
    for column in (
        sa.Column('evidence_image', sa.LargeBinary()),
        sa.Column('evidence_image_type', sa.String(32)),
        sa.Column('cover_image_url', sa.String(512)),
    ):
        if column.name not in existing:
            op.add_column('hotspots', column)


def downgrade():
    for name in ('cover_image_url', 'evidence_image_type', 'evidence_image'):
        op.drop_column('hotspots', name)
