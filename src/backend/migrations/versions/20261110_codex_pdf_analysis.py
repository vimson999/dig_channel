"""Add independent storage for Codex PDF analysis reports."""

from alembic import op
import sqlalchemy as sa


revision = "20261110_codex_pdf_analysis"
down_revision = "20260930_add_bili_charging_competitor"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "codex_pdf_analysis_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("source_exported_at", sa.DateTime(), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_record_count", sa.Integer(), nullable=False),
        sa.Column("source_total_download_count", sa.BigInteger(), nullable=False),
        sa.Column("contract_version", sa.String(length=64), nullable=False),
        sa.Column("analysis_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.Column("overall_summary", sa.Text(), nullable=False),
        sa.Column("selected_candidate_title", sa.String(length=500), nullable=True),
        sa.Column("data_quality_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "source_sha256", "contract_version", "analysis_version",
            name="uq_codex_pdf_analysis_runs_source_version",
        ),
    )
    op.create_index(
        "ix_codex_pdf_analysis_runs_user_id", "codex_pdf_analysis_runs", ["user_id"]
    )
    op.create_index(
        "ix_codex_pdf_analysis_runs_user_created",
        "codex_pdf_analysis_runs", ["user_id", "created_at"],
    )
    op.create_index(
        "ix_codex_pdf_analysis_runs_user_status_created",
        "codex_pdf_analysis_runs", ["user_id", "status", "created_at"],
    )

    op.create_table(
        "codex_pdf_analysis_sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("source_key", sa.String(length=160), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("institution", sa.String(length=255), nullable=True),
        sa.Column("report_date_text", sa.String(length=64), nullable=True),
        sa.Column("upload_time_text", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("download_count", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["codex_pdf_analysis_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "source_key", name="uq_codex_pdf_analysis_sources_key"),
        sa.UniqueConstraint("id", "run_id", name="uq_codex_pdf_analysis_sources_id_run"),
    )
    op.create_table(
        "codex_pdf_analysis_sections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("module_code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("evidence_state", sa.String(length=48), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["codex_pdf_analysis_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "module_code", name="uq_codex_pdf_analysis_sections_module"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_codex_pdf_analysis_sections_ordinal"),
        sa.UniqueConstraint("id", "run_id", name="uq_codex_pdf_analysis_sections_id_run"),
    )
    op.create_index(
        "ix_codex_pdf_analysis_sections_run_order", "codex_pdf_analysis_sections", ["run_id", "ordinal"]
    )

    op.create_table(
        "codex_pdf_analysis_topics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("topic_name", sa.String(length=255), nullable=False),
        sa.Column("classification_scope", sa.String(length=16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("pdf_count", sa.Integer(), nullable=False),
        sa.Column("total_download_count", sa.BigInteger(), nullable=False),
        sa.Column("average_download_count", sa.Float(), nullable=False),
        sa.Column("institution_count", sa.Integer(), nullable=False),
        sa.Column("keywords_text", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["codex_pdf_analysis_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "classification_scope", "topic_name",
            name="uq_codex_pdf_analysis_topics_scope_name",
        ),
        sa.UniqueConstraint(
            "run_id", "classification_scope", "rank",
            name="uq_codex_pdf_analysis_topics_scope_rank",
        ),
    )
    op.create_index(
        "ix_codex_pdf_analysis_topics_run_rank",
        "codex_pdf_analysis_topics", ["run_id", "classification_scope", "rank"],
    )

    op.create_table(
        "codex_pdf_analysis_rankings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("ranking_type", sa.String(length=64), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("topic_name", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["codex_pdf_analysis_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_id", "run_id"],
            ["codex_pdf_analysis_sources.id", "codex_pdf_analysis_sources.run_id"],
            ondelete="CASCADE", name="fk_codex_pdf_analysis_rankings_source_run",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "ranking_type", "topic_name", "rank",
            name="uq_codex_pdf_analysis_rankings_position",
        ),
    )
    op.create_index(
        "ix_codex_pdf_analysis_rankings_run_type",
        "codex_pdf_analysis_rankings", ["run_id", "ranking_type", "rank"],
    )

    op.create_table(
        "codex_pdf_analysis_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("main_topic", sa.String(length=255), nullable=True),
        sa.Column("pdf_count", sa.Integer(), nullable=False),
        sa.Column("total_download_count", sa.BigInteger(), nullable=False),
        sa.Column("average_download_count", sa.Float(), nullable=False),
        sa.Column("institution_count", sa.Integer(), nullable=False),
        sa.Column("institutions_text", sa.Text(), nullable=True),
        sa.Column("aggregation_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["codex_pdf_analysis_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "ticker", name="uq_codex_pdf_analysis_targets_ticker"),
    )
    op.create_index(
        "ix_codex_pdf_analysis_targets_run_downloads",
        "codex_pdf_analysis_targets", ["run_id", "total_download_count"],
    )

    op.create_table(
        "codex_pdf_analysis_candidates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_key", sa.String(length=80), nullable=False),
        sa.Column("track_code", sa.String(length=48), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("parent_question", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("new_evidence", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("uncertainty_text", sa.Text(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["codex_pdf_analysis_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "candidate_key", name="uq_codex_pdf_analysis_candidates_key"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_codex_pdf_analysis_candidates_ordinal"),
        sa.UniqueConstraint("id", "run_id", name="uq_codex_pdf_analysis_candidates_id_run"),
    )
    op.create_index(
        "ix_codex_pdf_analysis_candidates_run_order", "codex_pdf_analysis_candidates", ["run_id", "ordinal"]
    )

    op.create_table(
        "codex_pdf_analysis_candidate_sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("source_role", sa.String(length=48), nullable=False),
        sa.Column("selection_reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id", "run_id"],
            ["codex_pdf_analysis_candidates.id", "codex_pdf_analysis_candidates.run_id"],
            ondelete="CASCADE", name="fk_codex_pdf_analysis_candidate_sources_candidate_run",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "run_id"],
            ["codex_pdf_analysis_sources.id", "codex_pdf_analysis_sources.run_id"],
            ondelete="CASCADE", name="fk_codex_pdf_analysis_candidate_sources_source_run",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_id", "source_id", "source_role",
            name="uq_codex_pdf_analysis_candidate_sources_role",
        ),
    )
    op.create_index(
        "ix_codex_pdf_analysis_candidate_sources_run",
        "codex_pdf_analysis_candidate_sources", ["run_id"],
    )

    op.create_table(
        "codex_pdf_analysis_section_sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("source_role", sa.String(length=48), nullable=False),
        sa.Column("evidence_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["section_id", "run_id"],
            ["codex_pdf_analysis_sections.id", "codex_pdf_analysis_sections.run_id"],
            ondelete="CASCADE", name="fk_codex_pdf_analysis_section_sources_section_run",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "run_id"],
            ["codex_pdf_analysis_sources.id", "codex_pdf_analysis_sources.run_id"],
            ondelete="CASCADE", name="fk_codex_pdf_analysis_section_sources_source_run",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "section_id", "source_id", "source_role",
            name="uq_codex_pdf_analysis_section_sources_role",
        ),
    )
    op.create_index(
        "ix_codex_pdf_analysis_section_sources_run",
        "codex_pdf_analysis_section_sources", ["run_id"],
    )


def downgrade():
    op.drop_index(
        "ix_codex_pdf_analysis_section_sources_run", table_name="codex_pdf_analysis_section_sources"
    )
    op.drop_table("codex_pdf_analysis_section_sources")
    op.drop_index(
        "ix_codex_pdf_analysis_candidate_sources_run", table_name="codex_pdf_analysis_candidate_sources"
    )
    op.drop_table("codex_pdf_analysis_candidate_sources")
    op.drop_index(
        "ix_codex_pdf_analysis_candidates_run_order", table_name="codex_pdf_analysis_candidates"
    )
    op.drop_table("codex_pdf_analysis_candidates")
    op.drop_index(
        "ix_codex_pdf_analysis_targets_run_downloads", table_name="codex_pdf_analysis_targets"
    )
    op.drop_table("codex_pdf_analysis_targets")
    op.drop_index(
        "ix_codex_pdf_analysis_rankings_run_type", table_name="codex_pdf_analysis_rankings"
    )
    op.drop_table("codex_pdf_analysis_rankings")
    op.drop_index(
        "ix_codex_pdf_analysis_topics_run_rank", table_name="codex_pdf_analysis_topics"
    )
    op.drop_table("codex_pdf_analysis_topics")
    op.drop_index(
        "ix_codex_pdf_analysis_sections_run_order", table_name="codex_pdf_analysis_sections"
    )
    op.drop_table("codex_pdf_analysis_sections")
    op.drop_table("codex_pdf_analysis_sources")
    op.drop_index(
        "ix_codex_pdf_analysis_runs_user_created", table_name="codex_pdf_analysis_runs"
    )
    op.drop_index(
        "ix_codex_pdf_analysis_runs_user_status_created", table_name="codex_pdf_analysis_runs"
    )
    op.drop_index(
        "ix_codex_pdf_analysis_runs_user_id", table_name="codex_pdf_analysis_runs"
    )
    op.drop_table("codex_pdf_analysis_runs")
