#!/usr/bin/env python3
"""Fail closed when the database is only partially migrated for this release."""

import os
import signal
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_ID = "20260718_bili_upload_exec"
MIGRATION_SCRIPT = "docs/sql/bilibili_upload_execution_projection_dbeaver_20260718.sql"

REQUIRED_COLUMNS = {
    "publication_tasks": {
        "content_clean_output_dir",
        "content_clean_files_count",
        "content_clean_success_count",
        "content_clean_failed_count",
        "content_clean_message",
        "report_audio_vertical_cover_path",
        "report_audio_podcast_cover_path",
        "report_audio_bilibili_cover_path",
        "report_audio_video_path",
        "report_audio_cover_path",
        "publisher_bilibili_cover_path",
    },
    "zsxq_sync_runs": {"download_ledger_complete"},
    "zsxq_sync_run_downloads": {
        "run_id",
        "attachment_id",
        "group_id",
        "tab_id",
        "topic_id",
        "name",
        "local_path",
        "file_ext",
        "downloaded_at",
    },
    "publication_task_bilibili_uploads": {
        "task_id",
        "parent_task_id",
        "parent_feature",
        "source_label",
        "video_key",
        "cover_key",
        "title",
        "video_path",
        "cover_path",
        "description",
        "tags",
        "tid",
        "copyright",
        "no_reprint",
        "dynamic",
        "neutral_mark",
        "is_only_self",
        "space_hidden",
        "creation_statement_id",
        "credential_id",
        "credential_name",
        "charging_enabled",
        "preview_start_time",
        "preview_end_time",
        "upower_level_id",
        "upower_unit_price",
        "upower_mode",
        "member_first_exp_time",
        "delay_time",
        "season_id",
        "aid",
        "bvid",
        "queue_repair_count",
        "queue_repair_last_at",
        "queue_repair_previous_celery_id",
        "queue_repair_current_celery_id",
        "queue_repair_queue",
        "queue_repair_reason",
        "queue_repair_previous_message_visible",
        "canceled_at",
        "cancel_reason",
        "created_at",
        "updated_at",
    },
}

REQUIRED_PRIVILEGES = {
    "zsxq_sync_run_downloads": "SELECT, INSERT, UPDATE, DELETE",
    "publication_task_bilibili_uploads": "SELECT, INSERT, UPDATE, DELETE",
}

REQUIRED_INDEXES = {
    "ix_publication_tasks_parent_created",
    "ix_publication_tasks_source_created",
    "ix_zsxq_topics_group_tab_create_time_desc",
    "ix_zsxq_sync_run_downloads_run_ext",
    "ix_zsxq_sync_run_downloads_attachment",
    "ix_publication_task_bili_uploads_parent_source_created",
}

FORBIDDEN_DUPLICATE_INDEXES = {
    "ix_publication_tasks_parent_created_asc",
    "ix_publication_tasks_source_created_asc",
    "ix_zsxq_topics_group_tab_create_synced_desc",
}


class SchemaCheckTimeout(TimeoutError):
    pass


def _raise_schema_check_timeout(_signum, _frame):
    raise SchemaCheckTimeout("runtime schema check exceeded its hard timeout")


def evaluate_schema_snapshot(snapshot):
    errors = []
    tables = snapshot.get("tables") or {}
    for table_name, required_columns in REQUIRED_COLUMNS.items():
        actual_columns = set(tables.get(table_name) or ())
        if not actual_columns:
            errors.append(f"missing table public.{table_name}")
            continue
        missing_columns = sorted(required_columns - actual_columns)
        if missing_columns:
            errors.append(
                f"public.{table_name} missing columns: {', '.join(missing_columns)}"
            )

    privileges = snapshot.get("privileges") or {}
    for table_name, required in REQUIRED_PRIVILEGES.items():
        if privileges.get(table_name) is not True:
            errors.append(
                f"current user lacks {required} on public.{table_name}"
            )

    clean_tasks = snapshot.get("clean_tasks")
    projected_clean_tasks = snapshot.get("projected_clean_tasks")
    if (
        clean_tasks is not None
        and projected_clean_tasks is not None
        and clean_tasks != projected_clean_tasks
    ):
        errors.append(
            "content clean projection backfill is incomplete: "
            f"{projected_clean_tasks}/{clean_tasks} rows"
        )

    media_projection_mismatches = snapshot.get("media_projection_mismatches")
    if media_projection_mismatches is None:
        errors.append("media cover projection validation was not executed")
    elif media_projection_mismatches:
        errors.append(
            "media cover projection backfill is incomplete: "
            f"{media_projection_mismatches} mismatched row(s)"
        )

    bilibili_tasks = snapshot.get("bilibili_tasks")
    projected_bilibili_tasks = snapshot.get("projected_bilibili_tasks")
    if (
        bilibili_tasks is not None
        and projected_bilibili_tasks is not None
        and bilibili_tasks != projected_bilibili_tasks
    ):
        errors.append(
            "Bilibili upload structural backfill is incomplete: "
            f"{projected_bilibili_tasks}/{bilibili_tasks} rows"
        )
    executable_bilibili_tasks = snapshot.get("executable_bilibili_tasks")
    if (
        bilibili_tasks is not None
        and executable_bilibili_tasks is not None
        and bilibili_tasks != executable_bilibili_tasks
    ):
        errors.append(
            "Bilibili upload execution projection is incomplete: "
            f"{executable_bilibili_tasks}/{bilibili_tasks} rows"
        )

    indexes = set(snapshot.get("indexes") or ())
    missing_indexes = sorted(REQUIRED_INDEXES - indexes)
    if missing_indexes:
        errors.append(f"missing required indexes: {', '.join(missing_indexes)}")
    duplicate_indexes = sorted(FORBIDDEN_DUPLICATE_INDEXES & indexes)
    if duplicate_indexes:
        errors.append(
            "equivalent duplicate indexes still installed: "
            + ", ".join(duplicate_indexes)
        )
    return errors


def _local_database_url_is_available(database_url):
    try:
        parsed = urlparse(str(database_url or ""))
        host = parsed.hostname
        port = parsed.port or 5432
    except (TypeError, ValueError):
        return False
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return False
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _database_url():
    from dotenv import dotenv_values

    values = dotenv_values(PROJECT_ROOT / ".env")
    database_url = os.environ.get("DATABASE_URL") or values.get("DATABASE_URL")
    direct_url = os.environ.get("DIRECT_DATABASE_URL") or values.get("DIRECT_DATABASE_URL")
    value = database_url if _local_database_url_is_available(database_url) else direct_url or database_url or ""
    return str(value).replace("postgresql+psycopg2://", "postgresql://")


def read_schema_snapshot():
    import psycopg2

    database_url = _database_url()
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("DATABASE_URL is not a PostgreSQL connection URL")

    connection = psycopg2.connect(database_url, connect_timeout=5)
    connection.set_session(readonly=True, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '10s'")
            cursor.execute(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ANY(%s)
                """,
                (list(REQUIRED_COLUMNS),),
            )
            tables = {}
            for table_name, column_name in cursor.fetchall():
                tables.setdefault(table_name, set()).add(column_name)

            privileges = {}
            for table_name, required in REQUIRED_PRIVILEGES.items():
                if table_name not in tables:
                    privileges[table_name] = False
                    continue
                cursor.execute(
                    "SELECT has_table_privilege(current_user, %s, %s)",
                    (f"public.{table_name}", required),
                )
                privileges[table_name] = cursor.fetchone()[0] is True

            clean_tasks = None
            projected_clean_tasks = None
            bilibili_tasks = None
            projected_bilibili_tasks = None
            executable_bilibili_tasks = None
            media_projection_mismatches = None
            publication_columns = tables.get("publication_tasks") or set()
            if REQUIRED_COLUMNS["publication_tasks"] <= publication_columns:
                cursor.execute(
                    """
                    SELECT
                        count(*),
                        count(*) FILTER (
                            WHERE content_clean_files_count IS NOT NULL
                        )
                    FROM public.publication_tasks
                    WHERE feature IN ('content_pdf_clean', 'content_audio_clean')
                    """
                )
                clean_tasks, projected_clean_tasks = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT count(*)
                    FROM public.publication_tasks
                    WHERE (
                            feature IN ('report_pdf_audio_workflow', 'report_audio_workflow')
                            AND (
                                report_audio_video_path IS DISTINCT FROM LEFT(
                                    NULLIF(result::jsonb #>> '{video,video_path}', ''), 600
                                )
                                OR report_audio_cover_path IS DISTINCT FROM LEFT(
                                    NULLIF(result::jsonb #>> '{video,cover_path}', ''), 600
                                )
                                OR report_audio_vertical_cover_path IS DISTINCT FROM LEFT(COALESCE(
                                    NULLIF(result::jsonb #>> '{video,vertical_cover_path}', ''),
                                    NULLIF(result::jsonb #>> '{video,keyframes,vertical_cover,path}', '')
                                ), 600)
                                OR report_audio_podcast_cover_path IS DISTINCT FROM LEFT(COALESCE(
                                    NULLIF(result::jsonb #>> '{video,podcast_cover_path}', ''),
                                    NULLIF(result::jsonb #>> '{video,keyframes,podcast_cover,path}', '')
                                ), 600)
                                OR report_audio_bilibili_cover_path IS DISTINCT FROM LEFT(COALESCE(
                                    NULLIF(result::jsonb #>> '{video,bilibili_cover_path}', ''),
                                    NULLIF(result::jsonb #>> '{video,keyframes,bilibili_cover,path}', '')
                                ), 600)
                            )
                        )
                        OR (
                            feature = 'publisher'
                            AND publisher_bilibili_cover_path IS DISTINCT FROM LEFT(
                                NULLIF(result::jsonb ->> 'bilibili_cover_path', ''),
                                600
                            )
                        )
                    """
                )
                media_projection_mismatches = cursor.fetchone()[0]

            bilibili_columns = tables.get("publication_task_bilibili_uploads") or set()
            if REQUIRED_COLUMNS["publication_task_bilibili_uploads"] <= bilibili_columns:
                cursor.execute(
                    """
                    WITH expected AS (
                        SELECT id
                        FROM public.publication_tasks
                        WHERE feature = 'publisher'
                          AND COALESCE(
                              params::jsonb #>> '{metadata,source}',
                              result::jsonb #>> '{metadata,source}',
                              ''
                          ) LIKE '%:bilibili_upload'
                    )
                    SELECT
                        count(*),
                        count(projection.task_id),
                        count(projection.task_id) FILTER (
                            WHERE projection.video_path IS NOT NULL
                              AND projection.title IS NOT NULL
                              AND cardinality(projection.tags) > 0
                              AND projection.neutral_mark IS NOT NULL
                        )
                    FROM expected
                    LEFT JOIN public.publication_task_bilibili_uploads AS projection
                        ON projection.task_id = expected.id
                    """
                )
                (
                    bilibili_tasks,
                    projected_bilibili_tasks,
                    executable_bilibili_tasks,
                ) = cursor.fetchone()

            cursor.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                """
            )
            indexes = {row[0] for row in cursor.fetchall()}
        connection.rollback()
        return {
            "tables": tables,
            "privileges": privileges,
            "clean_tasks": clean_tasks,
            "projected_clean_tasks": projected_clean_tasks,
            "media_projection_mismatches": media_projection_mismatches,
            "bilibili_tasks": bilibili_tasks,
            "projected_bilibili_tasks": projected_bilibili_tasks,
            "executable_bilibili_tasks": executable_bilibili_tasks,
            "indexes": indexes,
        }
    finally:
        connection.close()


def main():
    timeout_seconds = max(
        5,
        int(os.environ.get("RUNTIME_SCHEMA_CHECK_TIMEOUT_SECONDS", "25")),
    )
    previous_handler = signal.signal(signal.SIGALRM, _raise_schema_check_timeout)
    signal.alarm(timeout_seconds)
    try:
        snapshot = read_schema_snapshot()
        errors = evaluate_schema_snapshot(snapshot)
    except Exception as exc:
        print(f"ERROR: runtime schema check failed: {exc}", file=sys.stderr)
        print(f"Required migration: {MIGRATION_SCRIPT}", file=sys.stderr)
        return 3
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)

    if not errors:
        print(f"OK: runtime schema contract {MIGRATION_ID} is complete.")
        return 0

    print(
        f"BLOCKED: runtime schema contract {MIGRATION_ID} is incomplete:",
        file=sys.stderr,
    )
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    print(f"Run the complete admin migration: {MIGRATION_SCRIPT}", file=sys.stderr)
    print("Refusing to start backend, Celery, or schedulers.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
