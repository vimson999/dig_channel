#!/usr/bin/env python3
"""Apply the online-safe part of the 2026-07-24 DB cleanup."""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import dotenv_values
from psycopg2 import sql


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INDEXES = (
    "ix_external_documents_url_hash",
    "ix_publication_tasks_feature_status_updated",
    "ix_publication_tasks_feature_created_at_desc",
    "ix_publication_tasks_publisher_created_at_desc",
    "ix_publication_tasks_publisher_pending_updated_at",
    "ix_zsxq_topics_group_tab_type_create_desc",
    "ix_zsxq_topics_group_type_create_desc",
    "ix_zsxq_attachments_publish_status",
    "ix_zsxq_topic_comments_tab_id",
    "ix_zsxq_topic_comments_parent_comment_id",
    "ix_zsxq_topic_comments_owner_user_id",
    "ix_zsxq_topic_comments_owner_name",
    "ix_zsxq_topic_comments_create_time",
)


def _database_url():
    values = dotenv_values(PROJECT_ROOT / ".env")
    value = (
        os.environ.get("DIRECT_DATABASE_URL")
        or values.get("DIRECT_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or values.get("DATABASE_URL")
        or ""
    )
    return str(value).replace("postgresql+psycopg2://", "postgresql://")


def main():
    database_url = _database_url()
    if not database_url.startswith(("postgresql://", "postgres://")):
        print("ERROR: PostgreSQL DIRECT_DATABASE_URL is required", file=sys.stderr)
        return 2

    connection = psycopg2.connect(database_url, connect_timeout=5)
    connection.autocommit = True
    skipped = []
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '5s'")
            cursor.execute("SET statement_timeout = '10min'")
            cursor.execute(
                "ALTER TABLE public.zsxq_topic_comments "
                "ADD COLUMN IF NOT EXISTS raw_payload_hash varchar(32)"
            )
            cursor.execute(
                """
                UPDATE public.zsxq_topic_comments
                SET raw_payload_hash = md5(raw_payload::jsonb::text)
                WHERE raw_payload IS NOT NULL
                  AND raw_payload_hash IS NULL
                """
            )
            print(f"comment hash backfilled: {cursor.rowcount}")

            cursor.execute(
                """
                SELECT i.relname, pg_get_userbyid(t.relowner)
                FROM pg_class AS i
                JOIN pg_index AS x ON x.indexrelid = i.oid
                JOIN pg_class AS t ON t.oid = x.indrelid
                JOIN pg_namespace AS n ON n.oid = i.relnamespace
                WHERE n.nspname = 'public'
                  AND i.relname = ANY(%s)
                """,
                (list(INDEXES),),
            )
            owners = dict(cursor.fetchall())
            cursor.execute("SELECT current_user")
            current_user = cursor.fetchone()[0]

            for index_name in INDEXES:
                owner = owners.get(index_name)
                if owner and owner != current_user:
                    skipped.append((index_name, owner))
                    print(
                        f"SKIP {index_name}: table owner is {owner}, "
                        f"current user is {current_user}"
                    )
                    continue
                cursor.execute(
                    sql.SQL("DROP INDEX CONCURRENTLY IF EXISTS public.{}").format(
                        sql.Identifier(index_name)
                    )
                )
                print(f"dropped or absent: {index_name}")

            cursor.execute(
                """
                SELECT count(*)
                FROM public.zsxq_topic_comments
                WHERE raw_payload IS NOT NULL
                  AND raw_payload_hash IS NULL
                """
            )
            missing_hashes = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname = ANY(%s)
                ORDER BY indexname
                """,
                (list(INDEXES),),
            )
            remaining = [row[0] for row in cursor.fetchall()]
            if not remaining:
                cursor.execute(
                    """
                    UPDATE public.alembic_version
                    SET version_num = '20260724_comment_hash_indexes'
                    WHERE version_num = '20260722_zsxq_topic_payload_hash'
                    """
                )
                print(f"alembic version advanced: {cursor.rowcount}")
    finally:
        connection.close()

    print(f"missing comment hashes: {missing_hashes}")
    print(f"remaining cleanup indexes: {len(remaining)}")
    for index_name in remaining:
        print(f"  - {index_name}")
    if missing_hashes:
        return 3
    if skipped:
        print("Admin action is still required for indexes owned by another role.")
        return 4
    return 0 if not remaining else 5


if __name__ == "__main__":
    raise SystemExit(main())
