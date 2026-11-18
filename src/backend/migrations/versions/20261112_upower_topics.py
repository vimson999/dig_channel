"""Persist versioned UPower topic analysis and its archive membership."""
from alembic import op
import sqlalchemy as sa

revision = "20261112_upower_topics"
down_revision = "20261112_bilibili_upower_archive_stats"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("bilibili_upower_topic_reports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("account_uid", sa.String(32), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("source_fetched_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False))
    op.create_index("ix_bilibili_upower_topic_reports_account_uid", "bilibili_upower_topic_reports", ["account_uid"])
    op.create_table("bilibili_upower_topics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.String(64), sa.ForeignKey("bilibili_upower_topic_reports.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("archive_aids", sa.JSON(), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=False),
        sa.UniqueConstraint("report_id", "name", name="uq_upower_topic_report_name"))
    op.create_index("ix_bilibili_upower_topics_report_id", "bilibili_upower_topics", ["report_id"])


def downgrade():
    op.drop_table("bilibili_upower_topics")
    op.drop_table("bilibili_upower_topic_reports")
