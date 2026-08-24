"""add versioned PDF evidence mapping and visual links

Revision ID: 20260728_report_pdf_evidence
Revises: 20260728_youtube_video_sync
Create Date: 2026-07-28
"""

import sqlalchemy as sa
from alembic import op


revision = "20260728_report_pdf_evidence"
down_revision = "20260728_youtube_video_sync"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "report_pdf_evidence_outputs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_task_id", sa.String(36), nullable=False),
        sa.Column("pdf_attachment_id", sa.String(64)),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("pdf_filename", sa.String(500)),
        sa.Column("source_pdf_path", sa.String(700)),
        sa.Column("source_pdf_hash", sa.String(64), nullable=False),
        sa.Column("source_pdf_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("final_script_hash", sa.String(64), nullable=False),
        sa.Column("raw_response_hash", sa.String(64), nullable=False),
        sa.Column("gemini_task_id", sa.String(36)),
        sa.Column("status", sa.String(32), nullable=False, server_default="unavailable"),
        sa.Column("visuals_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("claim_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_claim_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mapped_script_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coverage_ratio", sa.Float(), nullable=False, server_default="0"),
        sa.Column("raw_artifact_path", sa.String(700)),
        sa.Column("normalized_artifact_path", sa.String(700)),
        sa.Column("parser_provider", sa.String(40), nullable=False, server_default="local"),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["source_task_id"], ["publication_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["gemini_task_id"], ["publication_tasks.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("source_task_id", name="uq_report_pdf_evidence_output_source_task"),
    )
    op.create_index("ix_report_pdf_evidence_outputs_source_task_id", "report_pdf_evidence_outputs", ["source_task_id"])
    op.create_index("ix_report_pdf_evidence_outputs_pdf_attachment_id", "report_pdf_evidence_outputs", ["pdf_attachment_id"])
    op.create_index("ix_report_pdf_evidence_outputs_source_pdf_hash", "report_pdf_evidence_outputs", ["source_pdf_hash"])
    op.create_index("ix_report_pdf_evidence_outputs_final_script_hash", "report_pdf_evidence_outputs", ["final_script_hash"])
    op.create_index("ix_report_pdf_evidence_outputs_raw_response_hash", "report_pdf_evidence_outputs", ["raw_response_hash"])
    op.create_index("ix_report_pdf_evidence_outputs_gemini_task_id", "report_pdf_evidence_outputs", ["gemini_task_id"])
    op.create_index("ix_report_pdf_evidence_outputs_status", "report_pdf_evidence_outputs", ["status"])

    op.create_table(
        "report_pdf_evidence_claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("output_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("claim_type", sa.String(40), nullable=False, server_default="claim"),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("script_quote", sa.Text()),
        sa.Column("script_start", sa.Integer()),
        sa.Column("script_end", sa.Integer()),
        sa.Column("pdf_page", sa.Integer()),
        sa.Column("pdf_quote", sa.Text()),
        sa.Column("evidence_kind", sa.String(32), nullable=False, server_default="page"),
        sa.Column("crop_x", sa.Float(), nullable=False, server_default="0"),
        sa.Column("crop_y", sa.Float(), nullable=False, server_default="0"),
        sa.Column("crop_width", sa.Float(), nullable=False, server_default="1"),
        sa.Column("crop_height", sa.Float(), nullable=False, server_default="1"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("verification_status", sa.String(32), nullable=False, server_default="unverified"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("use_as_visual", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("screenshot_path", sa.String(700)),
        sa.Column("source_trace", sa.String(300)),
        sa.Column("user_edited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["output_id"], ["report_pdf_evidence_outputs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("output_id", "sequence", name="uq_report_pdf_evidence_claim_sequence"),
    )
    op.create_index("ix_report_pdf_evidence_claims_output_id", "report_pdf_evidence_claims", ["output_id"])
    op.create_index("ix_report_pdf_evidence_claims_pdf_page", "report_pdf_evidence_claims", ["pdf_page"])
    op.create_index("ix_report_pdf_evidence_claims_verification_status", "report_pdf_evidence_claims", ["verification_status"])
    op.create_index("ix_report_pdf_evidence_claim_output_page", "report_pdf_evidence_claims", ["output_id", "pdf_page"])

    op.create_table(
        "report_visual_plan_segment_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("segment_id", sa.String(36), nullable=False),
        sa.Column("claim_id", sa.String(36), nullable=False),
        sa.Column("match_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("link_source", sa.String(32), nullable=False, server_default="deterministic"),
        sa.Column("use_as_visual", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["segment_id"], ["report_visual_plan_segments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["claim_id"], ["report_pdf_evidence_claims.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("segment_id", "claim_id", name="uq_report_visual_segment_evidence"),
    )
    op.create_index("ix_report_visual_plan_segment_evidence_segment_id", "report_visual_plan_segment_evidence", ["segment_id"])
    op.create_index("ix_report_visual_plan_segment_evidence_claim_id", "report_visual_plan_segment_evidence", ["claim_id"])
    op.create_index("ix_report_visual_segment_evidence_segment_visual", "report_visual_plan_segment_evidence", ["segment_id", "use_as_visual"])

    op.add_column("report_visual_plan_outputs", sa.Column("evidence_output_id", sa.String(36)))
    op.add_column("report_visual_plan_outputs", sa.Column("evidence_schema_version", sa.String(64)))
    op.add_column("report_visual_plan_outputs", sa.Column("evidence_status", sa.String(32)))
    op.create_foreign_key(
        "fk_report_visual_plan_output_evidence",
        "report_visual_plan_outputs",
        "report_pdf_evidence_outputs",
        ["evidence_output_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_report_visual_plan_outputs_evidence_output_id", "report_visual_plan_outputs", ["evidence_output_id"])

    op.add_column("report_stick_figure_scene_assets", sa.Column("image_source", sa.String(32), nullable=False, server_default="generated"))
    op.add_column("report_stick_figure_scene_assets", sa.Column("evidence_claim_id", sa.String(36)))
    op.create_foreign_key(
        "fk_report_stick_scene_evidence_claim",
        "report_stick_figure_scene_assets",
        "report_pdf_evidence_claims",
        ["evidence_claim_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_report_stick_figure_scene_assets_evidence_claim_id", "report_stick_figure_scene_assets", ["evidence_claim_id"])


def downgrade():
    op.drop_index("ix_report_stick_figure_scene_assets_evidence_claim_id", table_name="report_stick_figure_scene_assets")
    op.drop_constraint("fk_report_stick_scene_evidence_claim", "report_stick_figure_scene_assets", type_="foreignkey")
    op.drop_column("report_stick_figure_scene_assets", "evidence_claim_id")
    op.drop_column("report_stick_figure_scene_assets", "image_source")
    op.drop_index("ix_report_visual_plan_outputs_evidence_output_id", table_name="report_visual_plan_outputs")
    op.drop_constraint("fk_report_visual_plan_output_evidence", "report_visual_plan_outputs", type_="foreignkey")
    op.drop_column("report_visual_plan_outputs", "evidence_status")
    op.drop_column("report_visual_plan_outputs", "evidence_schema_version")
    op.drop_column("report_visual_plan_outputs", "evidence_output_id")
    op.drop_table("report_visual_plan_segment_evidence")
    op.drop_table("report_pdf_evidence_claims")
    op.drop_table("report_pdf_evidence_outputs")
