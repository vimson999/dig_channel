"""store a separately verified clean PDF source for evidence screenshots

Revision ID: 20260729_evidence_clean_visual
Revises: 20260728_report_pdf_evidence
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op


revision = "20260729_evidence_clean_visual"
down_revision = "20260728_report_pdf_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "report_pdf_evidence_outputs",
        sa.Column("visual_pdf_path", sa.String(700), nullable=True),
    )
    op.add_column(
        "report_pdf_evidence_outputs",
        sa.Column("visual_pdf_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "report_pdf_evidence_outputs",
        sa.Column(
            "visual_pdf_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "report_pdf_evidence_outputs",
        sa.Column(
            "visual_pdf_source",
            sa.String(40),
            nullable=False,
            server_default="source_pdf",
        ),
    )


def downgrade():
    op.drop_column("report_pdf_evidence_outputs", "visual_pdf_source")
    op.drop_column("report_pdf_evidence_outputs", "visual_pdf_bytes")
    op.drop_column("report_pdf_evidence_outputs", "visual_pdf_hash")
    op.drop_column("report_pdf_evidence_outputs", "visual_pdf_path")
