"""Store authenticated Bilibili UPower archive statistics."""

from alembic import op
import sqlalchemy as sa

revision = "20261112_bilibili_upower_archive_stats"
down_revision = "20261110_codex_pdf_blocks"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("bilibili_upower_archive_stats"):
        return
    op.create_table(
        "bilibili_upower_archive_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_uid", sa.String(32), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("aid", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("cover_url", sa.Text()),
        sa.Column("video_url", sa.Text()),
        sa.Column("pub_time", sa.DateTime()),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("play_count", sa.BigInteger()),
        sa.Column("income", sa.BigInteger()),
        sa.Column("level_price", sa.BigInteger()),
        sa.Column("level_name", sa.String(255)),
        sa.Column("badge_text", sa.String(255)),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("account_uid", "period_days", "aid", name="uq_bilibili_upower_archive_account_period_aid"),
    )
    op.create_index("ix_bilibili_upower_archive_period_pub", "bilibili_upower_archive_stats", ["account_uid", "period_days", "pub_time"])


def downgrade():
    op.drop_index("ix_bilibili_upower_archive_period_pub", table_name="bilibili_upower_archive_stats")
    op.drop_table("bilibili_upower_archive_stats")
