#!/usr/bin/env python3
"""Audit or repair task media projection columns derived from result JSON."""

import argparse
import os
import socket
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_COLUMNS_SQL = """
    LEFT(NULLIF(result::jsonb #>> '{video,video_path}', ''), 600) AS expected_video,
    LEFT(NULLIF(result::jsonb #>> '{video,cover_path}', ''), 600) AS expected_cover,
    LEFT(NULLIF(
        result::jsonb #>> '{video,keyframes,landscape_cover,path}', ''
    ), 600) AS expected_landscape,
    LEFT(COALESCE(
        NULLIF(result::jsonb #>> '{video,vertical_cover_path}', ''),
        NULLIF(result::jsonb #>> '{video,keyframes,vertical_cover,path}', '')
    ), 600) AS expected_vertical,
    LEFT(COALESCE(
        NULLIF(result::jsonb #>> '{video,podcast_cover_path}', ''),
        NULLIF(result::jsonb #>> '{video,keyframes,podcast_cover,path}', '')
    ), 600) AS expected_podcast,
    LEFT(COALESCE(
        NULLIF(result::jsonb #>> '{video,bilibili_cover_path}', ''),
        NULLIF(result::jsonb #>> '{video,keyframes,bilibili_cover,path}', '')
    ), 600) AS expected_bilibili,
    LEFT(NULLIF(result::jsonb ->> 'bilibili_cover_path', ''), 600) AS expected_publisher
"""

MISMATCH_SQL = f"""
    SELECT
        id::text,
        feature,
        status,
        current_step,
        created_at,
        updated_at,
        report_audio_video_path,
        report_audio_cover_path,
        report_audio_landscape_keyframe_path,
        report_audio_vertical_cover_path,
        report_audio_podcast_cover_path,
        report_audio_bilibili_cover_path,
        publisher_bilibili_cover_path,
        {EXPECTED_COLUMNS_SQL}
    FROM public.publication_tasks
    WHERE (
            feature IN ('report_pdf_audio_workflow', 'report_audio_workflow')
            AND (
                report_audio_video_path IS DISTINCT FROM LEFT(
                    NULLIF(result::jsonb #>> '{{video,video_path}}', ''), 600
                )
                OR report_audio_cover_path IS DISTINCT FROM LEFT(
                    NULLIF(result::jsonb #>> '{{video,cover_path}}', ''), 600
                )
                OR report_audio_landscape_keyframe_path IS DISTINCT FROM LEFT(
                    NULLIF(result::jsonb #>> '{{video,keyframes,landscape_cover,path}}', ''), 600
                )
                OR report_audio_vertical_cover_path IS DISTINCT FROM LEFT(COALESCE(
                    NULLIF(result::jsonb #>> '{{video,vertical_cover_path}}', ''),
                    NULLIF(result::jsonb #>> '{{video,keyframes,vertical_cover,path}}', '')
                ), 600)
                OR report_audio_podcast_cover_path IS DISTINCT FROM LEFT(COALESCE(
                    NULLIF(result::jsonb #>> '{{video,podcast_cover_path}}', ''),
                    NULLIF(result::jsonb #>> '{{video,keyframes,podcast_cover,path}}', '')
                ), 600)
                OR report_audio_bilibili_cover_path IS DISTINCT FROM LEFT(COALESCE(
                    NULLIF(result::jsonb #>> '{{video,bilibili_cover_path}}', ''),
                    NULLIF(result::jsonb #>> '{{video,keyframes,bilibili_cover,path}}', '')
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
    ORDER BY updated_at DESC, created_at DESC
"""

REPAIR_SQL = """
    UPDATE public.publication_tasks
    SET
        report_audio_video_path = CASE
            WHEN feature IN ('report_pdf_audio_workflow', 'report_audio_workflow')
            THEN LEFT(NULLIF(result::jsonb #>> '{video,video_path}', ''), 600)
            ELSE report_audio_video_path
        END,
        report_audio_cover_path = CASE
            WHEN feature IN ('report_pdf_audio_workflow', 'report_audio_workflow')
            THEN LEFT(NULLIF(result::jsonb #>> '{video,cover_path}', ''), 600)
            ELSE report_audio_cover_path
        END,
        report_audio_landscape_keyframe_path = CASE
            WHEN feature IN ('report_pdf_audio_workflow', 'report_audio_workflow')
            THEN LEFT(NULLIF(
                result::jsonb #>> '{video,keyframes,landscape_cover,path}', ''
            ), 600)
            ELSE report_audio_landscape_keyframe_path
        END,
        report_audio_vertical_cover_path = CASE
            WHEN feature IN ('report_pdf_audio_workflow', 'report_audio_workflow')
            THEN LEFT(COALESCE(
                NULLIF(result::jsonb #>> '{video,vertical_cover_path}', ''),
                NULLIF(result::jsonb #>> '{video,keyframes,vertical_cover,path}', '')
            ), 600)
            ELSE report_audio_vertical_cover_path
        END,
        report_audio_podcast_cover_path = CASE
            WHEN feature IN ('report_pdf_audio_workflow', 'report_audio_workflow')
            THEN LEFT(COALESCE(
                NULLIF(result::jsonb #>> '{video,podcast_cover_path}', ''),
                NULLIF(result::jsonb #>> '{video,keyframes,podcast_cover,path}', '')
            ), 600)
            ELSE report_audio_podcast_cover_path
        END,
        report_audio_bilibili_cover_path = CASE
            WHEN feature IN ('report_pdf_audio_workflow', 'report_audio_workflow')
            THEN LEFT(COALESCE(
                NULLIF(result::jsonb #>> '{video,bilibili_cover_path}', ''),
                NULLIF(result::jsonb #>> '{video,keyframes,bilibili_cover,path}', '')
            ), 600)
            ELSE report_audio_bilibili_cover_path
        END,
        publisher_bilibili_cover_path = CASE
            WHEN feature = 'publisher'
            THEN LEFT(NULLIF(result::jsonb ->> 'bilibili_cover_path', ''), 600)
            ELSE publisher_bilibili_cover_path
        END,
        updated_at = updated_at
    WHERE id = ANY(%s::text[])
"""


def _database_url():
    from dotenv import dotenv_values

    values = dotenv_values(PROJECT_ROOT / ".env")
    pooled = os.environ.get("DATABASE_URL") or values.get("DATABASE_URL") or ""
    direct = os.environ.get("DIRECT_DATABASE_URL") or values.get("DIRECT_DATABASE_URL") or ""
    value = pooled if _database_endpoint_is_reachable(pooled) else direct or pooled
    return str(value).replace("postgresql+psycopg2://", "postgresql://")


def _database_endpoint_is_reachable(value):
    try:
        parsed = urlparse(str(value).replace("postgresql+psycopg2://", "postgresql://"))
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return False
        with socket.create_connection((parsed.hostname, parsed.port or 5432), timeout=0.5):
            return True
    except (OSError, TypeError, ValueError):
        return False


def _print_rows(rows):
    print(f"media projection mismatches: {len(rows)}")
    for row in rows:
        mismatches = []
        names = (
            ("video", 6, 13),
            ("cover", 7, 14),
            ("landscape", 8, 15),
            ("vertical", 9, 16),
            ("podcast", 10, 17),
            ("bilibili", 11, 18),
            ("publisher", 12, 19),
        )
        for label, actual_index, expected_index in names:
            if row[actual_index] != row[expected_index]:
                mismatches.append(label)
        print(
            f"- id={row[0]} feature={row[1]} status={row[2]} "
            f"step={row[3]} created_at={row[4]} updated_at={row[5]} "
            f"columns={','.join(mismatches)}"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Repair only rows currently failing the projection contract.",
    )
    args = parser.parse_args()

    import psycopg2

    connection = psycopg2.connect(_database_url(), connect_timeout=5)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute(MISMATCH_SQL)
            rows = cursor.fetchall()
            _print_rows(rows)
            if not args.apply or not rows:
                connection.rollback()
                return 0 if not rows else 2

            ids = [row[0] for row in rows]
            cursor.execute(REPAIR_SQL, (ids,))
            repaired = cursor.rowcount
            connection.commit()
            print(f"repaired rows: {repaired}")

        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute(MISMATCH_SQL)
            remaining = cursor.fetchall()
            _print_rows(remaining)
            connection.rollback()
            return 0 if not remaining else 3
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
