"""add task query projection columns

Revision ID: 20260617_task_projection
Revises: 20260613_add_zsxq_workflow_runs
Create Date: 2026-06-17

"""
from alembic import op
import sqlalchemy as sa


revision = "20260617_task_projection"
down_revision = "20260613_add_zsxq_workflow_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("publication_tasks", "current_step", type_=sa.String(length=80), schema="public")

    for column in [
        sa.Column("feature", sa.String(length=80), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("parent_task_id", sa.String(length=36), nullable=True),
        sa.Column("source_task_id", sa.String(length=36), nullable=True),
        sa.Column("parent_feature", sa.String(length=80), nullable=True),
        sa.Column("source_feature", sa.String(length=80), nullable=True),
        sa.Column("task_title", sa.String(length=500), nullable=True),
        sa.Column("task_video_path", sa.String(length=600), nullable=True),
        sa.Column("report_audio_pdf_attachment_id", sa.String(length=36), nullable=True),
        sa.Column("report_audio_pdf_filename", sa.String(length=500), nullable=True),
        sa.Column("report_audio_pdf_bytes", sa.String(length=40), nullable=True),
        sa.Column("report_audio_pdf_topic_title", sa.String(length=500), nullable=True),
        sa.Column("report_audio_result_pdf_filename", sa.String(length=500), nullable=True),
        sa.Column("report_audio_result_pdf_bytes", sa.String(length=40), nullable=True),
        sa.Column("report_audio_result_pdf_topic_title", sa.String(length=500), nullable=True),
        sa.Column("report_audio_metadata_title", sa.String(length=500), nullable=True),
        sa.Column("report_audio_video_title", sa.String(length=500), nullable=True),
        sa.Column("report_audio_cover_title", sa.String(length=500), nullable=True),
        sa.Column("report_audio_structured_title", sa.String(length=500), nullable=True),
        sa.Column("report_audio_started_at", sa.String(length=64), nullable=True),
        sa.Column("report_audio_completed_at", sa.String(length=64), nullable=True),
        sa.Column("report_audio_error", sa.Text(), nullable=True),
        sa.Column("report_audio_output_path", sa.String(length=600), nullable=True),
        sa.Column("report_audio_wav_path", sa.String(length=600), nullable=True),
        sa.Column("report_audio_seconds", sa.String(length=40), nullable=True),
        sa.Column("report_audio_audio_bytes", sa.String(length=40), nullable=True),
        sa.Column("report_audio_video_enabled", sa.Boolean(), nullable=True),
        sa.Column("report_audio_video_status", sa.String(length=80), nullable=True),
        sa.Column("report_audio_video_publisher_task_id", sa.String(length=36), nullable=True),
        sa.Column("report_audio_video_message", sa.Text(), nullable=True),
        sa.Column("report_audio_video_path", sa.String(length=600), nullable=True),
        sa.Column("report_audio_cover_path", sa.String(length=600), nullable=True),
    ]:
        _add_column_if_not_exists("publication_tasks", column)

    _add_column_if_not_exists("zsxq_attachments", sa.Column("file_ext", sa.String(length=20), nullable=True))

    for column in [
        sa.Column("system_batch_id", sa.String(length=80), nullable=True),
        sa.Column("system_attempt_id", sa.String(length=80), nullable=True),
        sa.Column("system_round", sa.Integer(), nullable=True),
        sa.Column("system_max_rounds", sa.Integer(), nullable=True),
        sa.Column("system_trigger", sa.String(length=120), nullable=True),
        sa.Column("system_queued_at", sa.String(length=64), nullable=True),
        sa.Column("system_max_download_files", sa.Integer(), nullable=True),
        sa.Column("topics_fetched", sa.Integer(), nullable=True),
        sa.Column("topics_new", sa.Integer(), nullable=True),
        sa.Column("topics_updated", sa.Integer(), nullable=True),
        sa.Column("topics_existing", sa.Integer(), nullable=True),
        sa.Column("attachments_seen", sa.Integer(), nullable=True),
        sa.Column("attachments_downloaded", sa.Integer(), nullable=True),
        sa.Column("attachments_failed", sa.Integer(), nullable=True),
        sa.Column("attachments_skipped", sa.Integer(), nullable=True),
        sa.Column("download_limit_reached", sa.Boolean(), nullable=True),
    ]:
        _add_column_if_not_exists("zsxq_sync_runs", column)

    _backfill_projection_columns()

    _create_index_if_allowed(
        "ix_publication_tasks_feature_user_created_desc",
        "publication_tasks",
        "CREATE INDEX ix_publication_tasks_feature_user_created_desc "
        "ON public.publication_tasks (feature, user_id, created_at DESC)",
    )
    _create_index_if_allowed(
        "ix_publication_tasks_feature_created_desc_v2",
        "publication_tasks",
        "CREATE INDEX ix_publication_tasks_feature_created_desc_v2 "
        "ON public.publication_tasks (feature, created_at DESC)",
    )
    _create_index_if_allowed(
        "ix_publication_tasks_feature_status_updated",
        "publication_tasks",
        "CREATE INDEX ix_publication_tasks_feature_status_updated "
        "ON public.publication_tasks (feature, status, updated_at)",
    )
    _create_index_if_allowed(
        "ix_publication_tasks_parent_created",
        "publication_tasks",
        "CREATE INDEX ix_publication_tasks_parent_created "
        "ON public.publication_tasks (parent_task_id, created_at) "
        "WHERE parent_task_id IS NOT NULL",
    )
    _create_index_if_allowed(
        "ix_publication_tasks_source_created",
        "publication_tasks",
        "CREATE INDEX ix_publication_tasks_source_created "
        "ON public.publication_tasks (source_task_id, created_at) "
        "WHERE source_task_id IS NOT NULL",
    )
    _create_index_if_allowed(
        "ix_publication_tasks_report_audio_asset_created",
        "publication_tasks",
        "CREATE INDEX ix_publication_tasks_report_audio_asset_created "
        "ON public.publication_tasks (report_audio_pdf_attachment_id, created_at DESC) "
        "WHERE feature = 'report_pdf_audio_workflow' AND report_audio_pdf_attachment_id IS NOT NULL",
    )
    _create_index_if_allowed(
        "ix_zsxq_attachments_pdf_assets_order",
        "zsxq_attachments",
        "CREATE INDEX ix_zsxq_attachments_pdf_assets_order "
        "ON public.zsxq_attachments (download_status, file_ext, updated_at DESC, created_at DESC) "
        "WHERE local_path IS NOT NULL",
    )
    _create_index_if_allowed(
        "ix_zsxq_attachments_pdf_assets_group_tab",
        "zsxq_attachments",
        "CREATE INDEX ix_zsxq_attachments_pdf_assets_group_tab "
        "ON public.zsxq_attachments (group_id, tab_id, download_status, file_ext, updated_at DESC, created_at DESC) "
        "WHERE local_path IS NOT NULL",
    )
    _create_index_if_allowed(
        "ix_zsxq_sync_runs_sys_group_created_desc",
        "zsxq_sync_runs",
        "CREATE INDEX ix_zsxq_sync_runs_sys_group_created_desc "
        "ON public.zsxq_sync_runs (group_id, created_at DESC) "
        "WHERE mode LIKE 'sys%'",
    )
    _create_index_if_allowed(
        "ix_zsxq_sync_runs_sys_created_desc",
        "zsxq_sync_runs",
        "CREATE INDEX ix_zsxq_sync_runs_sys_created_desc "
        "ON public.zsxq_sync_runs (created_at DESC) "
        "WHERE mode LIKE 'sys%'",
    )
    _create_index_if_allowed(
        "ix_zsxq_sync_runs_system_batch_created_desc",
        "zsxq_sync_runs",
        "CREATE INDEX ix_zsxq_sync_runs_system_batch_created_desc "
        "ON public.zsxq_sync_runs (system_batch_id, created_at DESC) "
        "WHERE mode LIKE 'sys%' AND system_batch_id IS NOT NULL",
    )
    _create_index_if_allowed(
        "ix_zsxq_sync_runs_system_attempt_created_desc",
        "zsxq_sync_runs",
        "CREATE INDEX ix_zsxq_sync_runs_system_attempt_created_desc "
        "ON public.zsxq_sync_runs (system_attempt_id, created_at DESC) "
        "WHERE mode LIKE 'sys%' AND system_attempt_id IS NOT NULL",
    )
    _create_index_if_allowed(
        "ix_zsxq_topics_group_tab_create_time_desc",
        "zsxq_topics",
        "CREATE INDEX ix_zsxq_topics_group_tab_create_time_desc "
        "ON public.zsxq_topics (group_id, tab_id, create_time DESC, last_synced_at DESC)",
    )
    _create_index_if_allowed(
        "ix_zsxq_attachments_group_tab_topic_created",
        "zsxq_attachments",
        "CREATE INDEX ix_zsxq_attachments_group_tab_topic_created "
        "ON public.zsxq_attachments (group_id, tab_id, topic_id, created_at)",
    )


def downgrade():
    for name, table in [
        ("ix_zsxq_attachments_group_tab_topic_created", "zsxq_attachments"),
        ("ix_zsxq_topics_group_tab_create_time_desc", "zsxq_topics"),
        ("ix_zsxq_sync_runs_system_attempt_created_desc", "zsxq_sync_runs"),
        ("ix_zsxq_sync_runs_system_batch_created_desc", "zsxq_sync_runs"),
        ("ix_zsxq_sync_runs_sys_created_desc", "zsxq_sync_runs"),
        ("ix_zsxq_sync_runs_sys_group_created_desc", "zsxq_sync_runs"),
        ("ix_zsxq_attachments_pdf_assets_group_tab", "zsxq_attachments"),
        ("ix_zsxq_attachments_pdf_assets_order", "zsxq_attachments"),
        ("ix_publication_tasks_report_audio_asset_created", "publication_tasks"),
        ("ix_publication_tasks_source_created", "publication_tasks"),
        ("ix_publication_tasks_parent_created", "publication_tasks"),
        ("ix_publication_tasks_feature_status_updated", "publication_tasks"),
        ("ix_publication_tasks_feature_created_desc_v2", "publication_tasks"),
        ("ix_publication_tasks_feature_user_created_desc", "publication_tasks"),
    ]:
        op.drop_index(name, table_name=table, schema="public")

    op.drop_column("zsxq_attachments", "file_ext", schema="public")

    for column_name in [
        "download_limit_reached",
        "attachments_skipped",
        "attachments_failed",
        "attachments_downloaded",
        "attachments_seen",
        "topics_existing",
        "topics_updated",
        "topics_new",
        "topics_fetched",
        "system_max_download_files",
        "system_queued_at",
        "system_trigger",
        "system_max_rounds",
        "system_round",
        "system_attempt_id",
        "system_batch_id",
    ]:
        op.drop_column("zsxq_sync_runs", column_name, schema="public")

    for column_name in [
        "report_audio_cover_path",
        "report_audio_video_path",
        "report_audio_video_message",
        "report_audio_video_publisher_task_id",
        "report_audio_video_status",
        "report_audio_video_enabled",
        "report_audio_audio_bytes",
        "report_audio_seconds",
        "report_audio_wav_path",
        "report_audio_output_path",
        "report_audio_error",
        "report_audio_completed_at",
        "report_audio_started_at",
        "report_audio_structured_title",
        "report_audio_cover_title",
        "report_audio_video_title",
        "report_audio_metadata_title",
        "report_audio_result_pdf_topic_title",
        "report_audio_result_pdf_bytes",
        "report_audio_result_pdf_filename",
        "report_audio_pdf_topic_title",
        "report_audio_pdf_bytes",
        "report_audio_pdf_filename",
        "report_audio_pdf_attachment_id",
        "task_video_path",
        "task_title",
        "source_feature",
        "parent_feature",
        "source_task_id",
        "parent_task_id",
        "user_id",
        "feature",
    ]:
        op.drop_column("publication_tasks", column_name, schema="public")

    op.alter_column("publication_tasks", "current_step", type_=sa.String(length=20), schema="public")


def _backfill_projection_columns():
    op.execute(
        """
        UPDATE public.publication_tasks
        SET
            feature = COALESCE(NULLIF(params ->> 'feature', ''), 'publisher'),
            user_id = LEFT(NULLIF(params ->> 'user_id', ''), 64),
            parent_task_id = LEFT(NULLIF(params ->> 'parent_task_id', ''), 36),
            source_task_id = LEFT(NULLIF(COALESCE(params ->> 'source_task_id', params #>> '{zsxq_publish,source_task_id}', result ->> 'source_task_id'), ''), 36),
            parent_feature = LEFT(NULLIF(params ->> 'parent_feature', ''), 80),
            source_feature = LEFT(NULLIF(params ->> 'source_feature', ''), 80),
            task_title = LEFT(NULLIF(COALESCE(
                params #>> '{bilibili,title}',
                params #>> '{thumbnail,main_title}',
                params #>> '{content_pipeline,title}',
                params #>> '{zsxq_publish,title}',
                params #>> '{report_audio_workflow,metadata,title}',
                params #>> '{report_audio_workflow,video,title}',
                result #>> '{structured_result,cover_title}',
                result #>> '{structured_result,title}',
                result #>> '{pdf,topic_title}',
                params #>> '{report_audio_workflow,pdf,topic_title}',
                params #>> '{report_audio_workflow,pdf,filename}'
            ), ''), 500),
            task_video_path = LEFT(NULLIF(result ->> 'video_path', ''), 600),
            report_audio_pdf_attachment_id = LEFT(NULLIF(params #>> '{report_audio_workflow,pdf,attachment_id}', ''), 36),
            report_audio_pdf_filename = LEFT(NULLIF(params #>> '{report_audio_workflow,pdf,filename}', ''), 500),
            report_audio_pdf_bytes = LEFT(NULLIF(params #>> '{report_audio_workflow,pdf,bytes}', ''), 40),
            report_audio_pdf_topic_title = LEFT(NULLIF(params #>> '{report_audio_workflow,pdf,topic_title}', ''), 500),
            report_audio_result_pdf_filename = LEFT(NULLIF(result #>> '{pdf,filename}', ''), 500),
            report_audio_result_pdf_bytes = LEFT(NULLIF(result #>> '{pdf,bytes}', ''), 40),
            report_audio_result_pdf_topic_title = LEFT(NULLIF(result #>> '{pdf,topic_title}', ''), 500),
            report_audio_metadata_title = LEFT(NULLIF(params #>> '{report_audio_workflow,metadata,title}', ''), 500),
            report_audio_video_title = LEFT(NULLIF(params #>> '{report_audio_workflow,video,title}', ''), 500),
            report_audio_cover_title = LEFT(NULLIF(result #>> '{structured_result,cover_title}', ''), 500),
            report_audio_structured_title = LEFT(NULLIF(result #>> '{structured_result,title}', ''), 500),
            report_audio_started_at = LEFT(NULLIF(result ->> 'started_at', ''), 64),
            report_audio_completed_at = LEFT(NULLIF(result ->> 'completed_at', ''), 64),
            report_audio_error = NULLIF(result ->> 'error', ''),
            report_audio_output_path = LEFT(NULLIF(result ->> 'output_path', ''), 600),
            report_audio_wav_path = LEFT(NULLIF(result #>> '{audio,wav_path}', ''), 600),
            report_audio_seconds = LEFT(NULLIF(result #>> '{audio,seconds}', ''), 40),
            report_audio_audio_bytes = LEFT(NULLIF(result #>> '{audio,bytes}', ''), 40),
            report_audio_video_enabled = CASE lower(NULLIF(result #>> '{video,enabled}', ''))
                WHEN 'true' THEN TRUE
                WHEN '1' THEN TRUE
                WHEN 'yes' THEN TRUE
                WHEN 'on' THEN TRUE
                WHEN 'false' THEN FALSE
                WHEN '0' THEN FALSE
                WHEN 'no' THEN FALSE
                WHEN 'off' THEN FALSE
                ELSE NULL
            END,
            report_audio_video_status = LEFT(NULLIF(result #>> '{video,status}', ''), 80),
            report_audio_video_publisher_task_id = LEFT(NULLIF(result #>> '{video,publisher_task_id}', ''), 36),
            report_audio_video_message = NULLIF(result #>> '{video,message}', ''),
            report_audio_video_path = LEFT(NULLIF(result #>> '{video,video_path}', ''), 600),
            report_audio_cover_path = LEFT(NULLIF(result #>> '{video,cover_path}', ''), 600)
        """
    )

    op.execute(
        """
        UPDATE public.zsxq_attachments
        SET file_ext = LEFT(lower(COALESCE(
            substring(name from '[.]([^./]+)$'),
            substring(local_path from '[.]([^./]+)$')
        )), 20)
        """
    )
    op.execute(
        """
        WITH source AS (
            SELECT
                id,
                NULLIF(result #>> '{system_sync,batch_id}', '') AS system_batch_id_text,
                NULLIF(result #>> '{system_sync,attempt_id}', '') AS system_attempt_id_text,
                NULLIF(result #>> '{system_sync,round}', '') AS system_round_text,
                NULLIF(result #>> '{system_sync,max_rounds}', '') AS system_max_rounds_text,
                NULLIF(result #>> '{system_sync,trigger}', '') AS system_trigger_text,
                NULLIF(result #>> '{system_sync,queued_at}', '') AS system_queued_at_text,
                NULLIF(result #>> '{system_sync,max_download_files}', '') AS system_max_download_files_text,
                NULLIF(COALESCE(result ->> 'topics_fetched', result #>> '{partial,topics_fetched}'), '') AS topics_fetched_text,
                NULLIF(COALESCE(result ->> 'topics_new', result #>> '{partial,topics_new}'), '') AS topics_new_text,
                NULLIF(COALESCE(result ->> 'topics_updated', result #>> '{partial,topics_updated}'), '') AS topics_updated_text,
                NULLIF(COALESCE(result ->> 'topics_existing', result #>> '{partial,topics_existing}'), '') AS topics_existing_text,
                NULLIF(COALESCE(result ->> 'attachments_seen', result #>> '{partial,attachments_seen}'), '') AS attachments_seen_text,
                NULLIF(COALESCE(result ->> 'attachments_downloaded', result #>> '{partial,attachments_downloaded}'), '') AS attachments_downloaded_text,
                NULLIF(COALESCE(result ->> 'attachments_failed', result #>> '{partial,attachments_failed}'), '') AS attachments_failed_text,
                NULLIF(COALESCE(result ->> 'attachments_skipped', result #>> '{partial,attachments_skipped}'), '') AS attachments_skipped_text,
                NULLIF(COALESCE(result ->> 'download_limit_reached', result #>> '{partial,download_limit_reached}'), '') AS download_limit_reached_text
            FROM public.zsxq_sync_runs
        )
        UPDATE public.zsxq_sync_runs AS run
        SET
            system_batch_id = LEFT(source.system_batch_id_text, 80),
            system_attempt_id = LEFT(source.system_attempt_id_text, 80),
            system_round = CASE WHEN source.system_round_text ~ '^-?[0-9]+$' THEN source.system_round_text::integer ELSE NULL END,
            system_max_rounds = CASE WHEN source.system_max_rounds_text ~ '^-?[0-9]+$' THEN source.system_max_rounds_text::integer ELSE NULL END,
            system_trigger = LEFT(source.system_trigger_text, 120),
            system_queued_at = LEFT(source.system_queued_at_text, 64),
            system_max_download_files = CASE WHEN source.system_max_download_files_text ~ '^-?[0-9]+$' THEN source.system_max_download_files_text::integer ELSE NULL END,
            topics_fetched = CASE WHEN source.topics_fetched_text ~ '^-?[0-9]+$' THEN source.topics_fetched_text::integer ELSE 0 END,
            topics_new = CASE WHEN source.topics_new_text ~ '^-?[0-9]+$' THEN source.topics_new_text::integer ELSE 0 END,
            topics_updated = CASE WHEN source.topics_updated_text ~ '^-?[0-9]+$' THEN source.topics_updated_text::integer ELSE 0 END,
            topics_existing = CASE WHEN source.topics_existing_text ~ '^-?[0-9]+$' THEN source.topics_existing_text::integer ELSE 0 END,
            attachments_seen = CASE WHEN source.attachments_seen_text ~ '^-?[0-9]+$' THEN source.attachments_seen_text::integer ELSE 0 END,
            attachments_downloaded = CASE WHEN source.attachments_downloaded_text ~ '^-?[0-9]+$' THEN source.attachments_downloaded_text::integer ELSE 0 END,
            attachments_failed = CASE WHEN source.attachments_failed_text ~ '^-?[0-9]+$' THEN source.attachments_failed_text::integer ELSE 0 END,
            attachments_skipped = CASE WHEN source.attachments_skipped_text ~ '^-?[0-9]+$' THEN source.attachments_skipped_text::integer ELSE 0 END,
            download_limit_reached = CASE lower(source.download_limit_reached_text)
                WHEN 'true' THEN TRUE
                WHEN '1' THEN TRUE
                WHEN 'yes' THEN TRUE
                WHEN 'on' THEN TRUE
                WHEN 'false' THEN FALSE
                WHEN '0' THEN FALSE
                WHEN 'no' THEN FALSE
                WHEN 'off' THEN FALSE
                ELSE NULL
            END
        FROM source
        WHERE run.id = source.id
        """
    )


def _add_column_if_not_exists(table_name, column):
    exists = op.get_bind().execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :table_name
              AND column_name = :column_name
            LIMIT 1
            """
        ),
        {"table_name": table_name, "column_name": column.name},
    ).scalar()
    if exists:
        return
    dialect = op.get_bind().dialect
    column_type = column.type.compile(dialect=dialect)
    nullable = "" if column.nullable else " NOT NULL"
    op.execute(
        f"ALTER TABLE public.{table_name} "
        f"ADD COLUMN IF NOT EXISTS {column.name} {column_type}{nullable}"
    )


def _create_index_if_allowed(index_name, table_name, sql):
    bind = op.get_bind()
    exists = bind.execute(
        sa.text("SELECT to_regclass(:index_name)"),
        {"index_name": f"public.{index_name}"},
    ).scalar()
    if exists:
        return
    can_create = bind.execute(
        sa.text(
            """
            SELECT pg_has_role(c.relowner, 'MEMBER')
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = :table_name
            LIMIT 1
            """
        ),
        {"table_name": table_name},
    ).scalar()
    if not can_create:
        return
    op.execute(sql)
