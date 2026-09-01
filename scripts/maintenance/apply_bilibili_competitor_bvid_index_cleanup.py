#!/usr/bin/env python3
"""Check or apply explicitly authorized Bilibili duplicate unique-index cleanup."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import psycopg2
from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TABLE_NAME = "bilibili_competitor_videos"
KEEP_INDEX = "bilibili_competitor_videos_bvid_key"
DROP_INDEX = "ix_bilibili_competitor_videos_bvid"
UID_TABLE_NAME = "bilibili_competitors"
UID_KEEP_INDEX = "bilibili_competitors_uid_key"
UID_DROP_INDEX = "ix_bilibili_competitors_uid"


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


def capture_snapshot(cursor):
    cursor.execute("SELECT current_database(), current_user, pg_is_in_recovery()")
    database_name, current_user, in_recovery = cursor.fetchone()
    cursor.execute(
        "SELECT count(*) FROM public.bilibili_competitor_videos"
    )
    row_count = cursor.fetchone()[0]
    cursor.execute(
        """
        SELECT count(*)
        FROM (
            SELECT bvid
            FROM public.bilibili_competitor_videos
            GROUP BY bvid
            HAVING count(*) > 1
        ) AS duplicates
        """
    )
    duplicate_groups = cursor.fetchone()[0]
    cursor.execute(
        """
        SELECT count(*)
        FROM pg_locks
        WHERE relation = 'public.bilibili_competitor_videos'::regclass
          AND NOT granted
        """
    )
    waiting_locks = cursor.fetchone()[0]
    cursor.execute(
        """
        SELECT bvid
        FROM public.bilibili_competitor_videos
        ORDER BY last_seen_at DESC NULLS LAST
        LIMIT 2
        """
    )
    samples = [row[0] for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT i.relname,
               x.indisunique,
               x.indisvalid,
               x.indisready,
               pg_get_indexdef(i.oid),
               COALESCE(s.idx_scan, 0),
               pg_size_pretty(pg_relation_size(i.oid)),
               c.conname,
               c.contype,
               pg_get_userbyid(t.relowner),
               pg_has_role(current_user, t.relowner, 'MEMBER')
        FROM pg_class AS i
        JOIN pg_index AS x ON x.indexrelid = i.oid
        JOIN pg_class AS t ON t.oid = x.indrelid
        JOIN pg_namespace AS n ON n.oid = i.relnamespace
        LEFT JOIN pg_stat_user_indexes AS s ON s.indexrelid = i.oid
        LEFT JOIN pg_constraint AS c ON c.conindid = i.oid
        WHERE n.nspname = 'public'
          AND t.relname = 'bilibili_competitor_videos'
          AND i.relname IN (%s, %s)
        ORDER BY i.relname
        """,
        (KEEP_INDEX, DROP_INDEX),
    )
    indexes = {
        row[0]: {
            "unique": row[1],
            "valid": row[2],
            "ready": row[3],
            "definition": row[4],
            "scan_count": row[5],
            "size": row[6],
            "constraint_name": row[7],
            "constraint_type": row[8],
            "table_owner": row[9],
            "owner_member": row[10],
        }
        for row in cursor.fetchall()
    }
    return {
        "database": database_name,
        "user": current_user,
        "in_recovery": in_recovery,
        "row_count": row_count,
        "duplicate_groups": duplicate_groups,
        "waiting_locks": waiting_locks,
        "samples": samples,
        "indexes": indexes,
    }


def capture_uid_snapshot(cursor):
    cursor.execute("SELECT current_database(), current_user, pg_is_in_recovery()")
    database_name, current_user, in_recovery = cursor.fetchone()
    cursor.execute("SELECT count(*) FROM public.bilibili_competitors")
    row_count = cursor.fetchone()[0]
    cursor.execute(
        """
        SELECT count(*)
        FROM (
            SELECT uid
            FROM public.bilibili_competitors
            GROUP BY uid
            HAVING count(*) > 1
        ) AS duplicates
        """
    )
    duplicate_groups = cursor.fetchone()[0]
    cursor.execute(
        """
        SELECT count(*)
        FROM pg_locks
        WHERE relation = 'public.bilibili_competitors'::regclass
          AND NOT granted
        """
    )
    waiting_locks = cursor.fetchone()[0]
    cursor.execute(
        """
        SELECT uid
        FROM public.bilibili_competitors
        ORDER BY updated_at DESC NULLS LAST, uid
        LIMIT 2
        """
    )
    samples = [row[0] for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT i.relname,
               x.indisunique,
               x.indisvalid,
               x.indisready,
               pg_get_indexdef(i.oid),
               COALESCE(s.idx_scan, 0),
               pg_size_pretty(pg_relation_size(i.oid)),
               c.conname,
               c.contype,
               pg_get_userbyid(t.relowner),
               pg_has_role(current_user, t.relowner, 'MEMBER')
        FROM pg_class AS i
        JOIN pg_index AS x ON x.indexrelid = i.oid
        JOIN pg_class AS t ON t.oid = x.indrelid
        JOIN pg_namespace AS n ON n.oid = i.relnamespace
        LEFT JOIN pg_stat_user_indexes AS s ON s.indexrelid = i.oid
        LEFT JOIN pg_constraint AS c ON c.conindid = i.oid
        WHERE n.nspname = 'public'
          AND t.relname = 'bilibili_competitors'
          AND i.relname IN (%s, %s)
        ORDER BY i.relname
        """,
        (UID_KEEP_INDEX, UID_DROP_INDEX),
    )
    indexes = {
        row[0]: {
            "unique": row[1],
            "valid": row[2],
            "ready": row[3],
            "definition": row[4],
            "scan_count": row[5],
            "size": row[6],
            "constraint_name": row[7],
            "constraint_type": row[8],
            "table_owner": row[9],
            "owner_member": row[10],
        }
        for row in cursor.fetchall()
    }
    return {
        "database": database_name,
        "user": current_user,
        "in_recovery": in_recovery,
        "row_count": row_count,
        "duplicate_groups": duplicate_groups,
        "waiting_locks": waiting_locks,
        "samples": samples,
        "indexes": indexes,
    }


def evaluate_preconditions(snapshot):
    errors = []
    if snapshot.get("in_recovery"):
        errors.append("PostgreSQL is in recovery")
    duplicate_groups = int(snapshot.get("duplicate_groups") or 0)
    if duplicate_groups:
        errors.append(f"duplicate bvid groups={duplicate_groups}")
    waiting_locks = int(snapshot.get("waiting_locks") or 0)
    if waiting_locks:
        errors.append(f"waiting table locks={waiting_locks}")
    indexes = snapshot.get("indexes") or {}
    if KEEP_INDEX not in indexes:
        errors.append("retained index is missing")
    if DROP_INDEX not in indexes:
        errors.append("drop target is missing")
    for label, index_name in (("retained", KEEP_INDEX), ("drop target", DROP_INDEX)):
        index = indexes.get(index_name)
        if not index:
            continue
        if not (index.get("unique") and index.get("valid") and index.get("ready")):
            errors.append(f"{label} is not unique/valid/ready")
        if "USING btree (bvid)" not in str(index.get("definition") or ""):
            errors.append(f"{label} definition is not unique btree(bvid)")
        if not index.get("owner_member"):
            errors.append(
                f"current role cannot act for table owner {index.get('table_owner')}"
            )
    retained = indexes.get(KEEP_INDEX) or {}
    if retained and (
        retained.get("constraint_name") != KEEP_INDEX
        or retained.get("constraint_type") != "u"
    ):
        errors.append("retained index does not own the expected unique constraint")
    drop_target = indexes.get(DROP_INDEX) or {}
    if drop_target.get("constraint_name"):
        errors.append(
            f"drop target owns constraint {drop_target['constraint_name']}"
        )
    return errors


def evaluate_uid_preconditions(snapshot):
    errors = []
    if snapshot.get("in_recovery"):
        errors.append("PostgreSQL is in recovery")
    duplicate_groups = int(snapshot.get("duplicate_groups") or 0)
    if duplicate_groups:
        errors.append(f"duplicate uid groups={duplicate_groups}")
    waiting_locks = int(snapshot.get("waiting_locks") or 0)
    if waiting_locks:
        errors.append(f"waiting table locks={waiting_locks}")
    indexes = snapshot.get("indexes") or {}
    if UID_KEEP_INDEX not in indexes:
        errors.append("retained uid index is missing")
    if UID_DROP_INDEX not in indexes:
        errors.append("uid drop target is missing")
    for label, index_name in (
        ("retained uid", UID_KEEP_INDEX),
        ("uid drop target", UID_DROP_INDEX),
    ):
        index = indexes.get(index_name)
        if not index:
            continue
        if not (index.get("unique") and index.get("valid") and index.get("ready")):
            errors.append(f"{label} is not unique/valid/ready")
        if "USING btree (uid)" not in str(index.get("definition") or ""):
            errors.append(f"{label} definition is not unique btree(uid)")
        if not index.get("owner_member"):
            errors.append(
                f"current role cannot act for table owner {index.get('table_owner')}"
            )
    retained = indexes.get(UID_KEEP_INDEX) or {}
    if retained and (
        retained.get("constraint_name") != UID_KEEP_INDEX
        or retained.get("constraint_type") != "u"
    ):
        errors.append("retained uid index does not own the expected unique constraint")
    drop_target = indexes.get(UID_DROP_INDEX) or {}
    if drop_target.get("constraint_name"):
        errors.append(
            f"uid drop target owns constraint {drop_target['constraint_name']}"
        )
    return errors


def evaluate_postconditions(before, after):
    errors = []
    indexes = after.get("indexes") or {}
    if DROP_INDEX in indexes:
        errors.append("drop target still exists")
    retained = indexes.get(KEEP_INDEX)
    if retained is None:
        errors.append("retained index is missing")
    elif not (
        retained.get("unique")
        and retained.get("valid")
        and retained.get("ready")
        and retained.get("constraint_name") == KEEP_INDEX
        and retained.get("constraint_type") == "u"
    ):
        errors.append("retained unique constraint index is invalid")
    if after.get("row_count") != before.get("row_count"):
        errors.append(
            f"row count changed {before.get('row_count')}->{after.get('row_count')}"
        )
    if int(after.get("duplicate_groups") or 0):
        errors.append(
            f"duplicate bvid groups={after.get('duplicate_groups')} after DDL"
        )
    return errors


def evaluate_uid_postconditions(before, after):
    errors = []
    indexes = after.get("indexes") or {}
    if UID_DROP_INDEX in indexes:
        errors.append("uid drop target still exists")
    retained = indexes.get(UID_KEEP_INDEX)
    if retained is None:
        errors.append("retained uid index is missing")
    elif not (
        retained.get("unique")
        and retained.get("valid")
        and retained.get("ready")
        and retained.get("constraint_name") == UID_KEEP_INDEX
        and retained.get("constraint_type") == "u"
    ):
        errors.append("retained uid unique constraint index is invalid")
    if after.get("row_count") != before.get("row_count"):
        errors.append(
            f"uid table row count changed {before.get('row_count')}->{after.get('row_count')}"
        )
    if int(after.get("duplicate_groups") or 0):
        errors.append(
            f"duplicate uid groups={after.get('duplicate_groups')} after DDL"
        )
    return errors


def _plan_index_names(value):
    names = []
    if isinstance(value, dict):
        if value.get("Index Name"):
            names.append(value["Index Name"])
        for child in value.values():
            names.extend(_plan_index_names(child))
    elif isinstance(value, list):
        for child in value:
            names.extend(_plan_index_names(child))
    return names


def verify_lookup(cursor, samples):
    cursor.execute(
        """
        EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
        SELECT id, bvid
        FROM public.bilibili_competitor_videos
        WHERE bvid = ANY(%s)
        """,
        (list(samples),),
    )
    explain = cursor.fetchone()[0][0]
    cursor.execute(
        """
        SELECT bvid
        FROM public.bilibili_competitor_videos
        WHERE bvid = ANY(%s)
        ORDER BY bvid
        """,
        (list(samples),),
    )
    return {
        "returned_bvids": [row[0] for row in cursor.fetchall()],
        "index_names": _plan_index_names(explain),
        "execution_ms": explain.get("Execution Time"),
        "plan": explain.get("Plan"),
    }


def verify_uid_lookup(cursor, samples):
    cursor.execute(
        """
        EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
        SELECT id, uid
        FROM public.bilibili_competitors
        WHERE uid = ANY(%s)
        """,
        (list(samples),),
    )
    explain = cursor.fetchone()[0][0]
    cursor.execute(
        """
        SELECT uid
        FROM public.bilibili_competitors
        WHERE uid = ANY(%s)
        ORDER BY uid
        """,
        (list(samples),),
    )
    return {
        "returned_uids": [row[0] for row in cursor.fetchall()],
        "index_names": _plan_index_names(explain),
        "execution_ms": explain.get("Execution Time"),
        "plan": explain.get("Plan"),
    }


def _restore_standalone_index(cursor, result, snapshot):
    indexes = (snapshot or {}).get("indexes") or {}
    if DROP_INDEX in indexes:
        result["rollback"] = snapshot
        return
    cursor.execute(
        "CREATE UNIQUE INDEX CONCURRENTLY "
        "ix_bilibili_competitor_videos_bvid "
        "ON public.bilibili_competitor_videos USING btree (bvid)"
    )
    result["rollback"] = capture_snapshot(cursor)


def _restore_uid_standalone_index(cursor, result, snapshot):
    indexes = (snapshot or {}).get("indexes") or {}
    if UID_DROP_INDEX in indexes:
        result["rollback"] = snapshot
        return
    cursor.execute(
        "CREATE UNIQUE INDEX CONCURRENTLY "
        "ix_bilibili_competitors_uid "
        "ON public.bilibili_competitors USING btree (uid)"
    )
    result["rollback"] = capture_uid_snapshot(cursor)


def run_cleanup(connection, *, apply):
    with connection.cursor() as cursor:
        cursor.execute("SET lock_timeout = '5s'")
        cursor.execute("SET statement_timeout = '15min'")
        before = capture_snapshot(cursor)
        errors = evaluate_preconditions(before)
        if errors:
            raise RuntimeError("; ".join(errors))
        result = {
            "status": "ready",
            "drop_index": DROP_INDEX,
            "keep_index": KEEP_INDEX,
            "before": before,
        }
        if not apply:
            return result

        started = time.monotonic()
        cursor.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "public.ix_bilibili_competitor_videos_bvid"
        )
        result["ddl_seconds"] = round(time.monotonic() - started, 4)
        after = capture_snapshot(cursor)
        result["after"] = after
        try:
            post_errors = evaluate_postconditions(before, after)
            if post_errors:
                raise RuntimeError("; ".join(post_errors))

            lookup = verify_lookup(cursor, before.get("samples") or [])
            expected_bvids = sorted(before.get("samples") or [])
            if lookup.get("returned_bvids") != expected_bvids:
                raise RuntimeError(
                    "post-DDL lookup mismatch: "
                    f"expected={expected_bvids} returned={lookup.get('returned_bvids')}"
                )
            if KEEP_INDEX not in (lookup.get("index_names") or []):
                raise RuntimeError(
                    "post-DDL plan did not use retained index: "
                    f"{lookup.get('index_names')}"
                )
        except Exception:
            _restore_standalone_index(cursor, result, after)
            raise
        result["lookup"] = lookup
        result["status"] = "applied"
        return result


def run_uid_cleanup(connection, *, apply):
    with connection.cursor() as cursor:
        cursor.execute("SET lock_timeout = '5s'")
        cursor.execute("SET statement_timeout = '15min'")
        before = capture_uid_snapshot(cursor)
        errors = evaluate_uid_preconditions(before)
        if errors:
            raise RuntimeError("; ".join(errors))
        result = {
            "status": "ready",
            "drop_index": UID_DROP_INDEX,
            "keep_index": UID_KEEP_INDEX,
            "before": before,
        }
        if not apply:
            return result

        started = time.monotonic()
        cursor.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "public.ix_bilibili_competitors_uid"
        )
        result["ddl_seconds"] = round(time.monotonic() - started, 4)
        after = capture_uid_snapshot(cursor)
        result["after"] = after
        try:
            post_errors = evaluate_uid_postconditions(before, after)
            if post_errors:
                raise RuntimeError("; ".join(post_errors))

            lookup = verify_uid_lookup(cursor, before.get("samples") or [])
            expected_uids = sorted(before.get("samples") or [])
            if lookup.get("returned_uids") != expected_uids:
                raise RuntimeError(
                    "post-DDL uid lookup mismatch: "
                    f"expected={expected_uids} returned={lookup.get('returned_uids')}"
                )
            if UID_DROP_INDEX in (lookup.get("index_names") or []):
                raise RuntimeError(
                    "post-DDL uid plan still references dropped index: "
                    f"{lookup.get('index_names')}"
                )
        except Exception:
            _restore_uid_standalone_index(cursor, result, after)
            raise
        result["lookup"] = lookup
        result["status"] = "applied"
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Check one Bilibili duplicate unique-index contract; mutate only with --apply."
        )
    )
    parser.add_argument(
        "--target",
        choices=("video-bvid", "competitor-uid"),
        default="video-bvid",
        help="exact duplicate unique-index pair to audit",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="drop the exact redundant standalone index after all live gates pass",
    )
    args = parser.parse_args(argv)
    database_url = _database_url()
    if not database_url.startswith(("postgresql://", "postgres://")):
        print("ERROR: PostgreSQL DIRECT_DATABASE_URL is required", file=sys.stderr)
        return 2
    connection = psycopg2.connect(database_url, connect_timeout=5)
    connection.autocommit = True
    try:
        runner = run_uid_cleanup if args.target == "competitor-uid" else run_cleanup
        result = runner(connection, apply=args.apply)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
