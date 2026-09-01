#!/usr/bin/env python3
"""Audit one historical index candidate; mutate only with explicit evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import psycopg2
from dotenv import dotenv_values
from psycopg2 import sql


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROTECTED_INDEXES = {
    "ix_zsxq_topics_group_tab_type_create_desc": (
        "hot production index: 2,990 scans in the current statistics window"
    ),
}
APPLY_ALLOWLIST = (
    "ix_external_documents_url_hash",
    "ix_publication_tasks_feature_status_updated",
    "ix_publication_tasks_feature_created_at_desc",
    "ix_publication_tasks_publisher_created_at_desc",
    "ix_publication_tasks_publisher_pending_updated_at",
    "ix_zsxq_topics_group_type_create_desc",
    "ix_zsxq_attachments_publish_status",
    "ix_zsxq_topic_comments_tab_id",
    "ix_zsxq_topic_comments_parent_comment_id",
    "ix_zsxq_topic_comments_owner_user_id",
    "ix_zsxq_topic_comments_owner_name",
    "ix_zsxq_topic_comments_create_time",
)
AUDIT_INDEXES = tuple(APPLY_ALLOWLIST) + tuple(PROTECTED_INDEXES)


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


def capture_snapshot(cursor, index_names, *, table_names=()):
    names = sorted({str(item) for item in index_names if item})
    cursor.execute("SELECT current_database(), current_user, pg_is_in_recovery()")
    database_name, current_user, in_recovery = cursor.fetchone()
    cursor.execute(
        "SELECT COALESCE(stats_reset, pg_postmaster_start_time()) "
        "FROM pg_stat_database WHERE datname = current_database()"
    )
    stats_reset_row = cursor.fetchone()
    stats_reset = stats_reset_row[0] if stats_reset_row else None
    cursor.execute(
        """
        SELECT i.relname,
               t.relname,
               x.indisvalid,
               x.indisready,
               x.indisunique,
               x.indisprimary,
               c.conname,
               pg_get_indexdef(i.oid),
               COALESCE(s.idx_scan, 0),
               s.last_idx_scan,
               pg_relation_size(i.oid),
               pg_get_userbyid(t.relowner),
               pg_has_role(current_user, t.relowner, 'MEMBER')
        FROM pg_class AS i
        JOIN pg_index AS x ON x.indexrelid = i.oid
        JOIN pg_class AS t ON t.oid = x.indrelid
        JOIN pg_namespace AS n ON n.oid = i.relnamespace
        LEFT JOIN pg_constraint AS c ON c.conindid = i.oid
        LEFT JOIN pg_stat_user_indexes AS s ON s.indexrelid = i.oid
        WHERE n.nspname = 'public'
          AND i.relname = ANY(%s)
        ORDER BY i.relname
        """,
        (names,),
    )
    indexes = {}
    for row in cursor.fetchall():
        indexes[row[0]] = {
            "table": row[1],
            "valid": row[2],
            "ready": row[3],
            "unique": row[4],
            "primary": row[5],
            "constraint_name": row[6],
            "definition": row[7],
            "scan_count": int(row[8] or 0),
            "last_idx_scan": row[9],
            "size_bytes": int(row[10] or 0),
            "table_owner": row[11],
            "owner_member": row[12],
        }

    table_names = sorted(
        {str(item) for item in table_names if item}
        | {item["table"] for item in indexes.values()}
    )
    cursor.execute(
        """
        SELECT count(*)
        FROM pg_locks AS l
        JOIN pg_class AS t ON t.oid = l.relation
        JOIN pg_namespace AS n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public'
          AND t.relname = ANY(%s)
          AND NOT l.granted
        """,
        (table_names,),
    )
    waiting_locks = int(cursor.fetchone()[0] or 0)

    all_table_indexes = {table_name: {} for table_name in table_names}
    if table_names:
        cursor.execute(
            """
            SELECT t.relname, i.relname, x.indisvalid, x.indisready,
                   pg_get_indexdef(i.oid)
            FROM pg_class AS i
            JOIN pg_index AS x ON x.indexrelid = i.oid
            JOIN pg_class AS t ON t.oid = x.indrelid
            JOIN pg_namespace AS n ON n.oid = i.relnamespace
            WHERE n.nspname = 'public'
              AND t.relname = ANY(%s)
            ORDER BY t.relname, i.relname
            """,
            (table_names,),
        )
        for table_name, index_name, valid, ready, definition in cursor.fetchall():
            all_table_indexes.setdefault(table_name, {})[index_name] = {
                "valid": valid,
                "ready": ready,
                "definition": definition,
            }

    table_rows = {}
    for table_name in table_names:
        cursor.execute(
            sql.SQL("SELECT count(*) FROM public.{}").format(
                sql.Identifier(table_name)
            )
        )
        table_rows[table_name] = int(cursor.fetchone()[0] or 0)

    return {
        "database": database_name,
        "user": current_user,
        "in_recovery": in_recovery,
        "stats_reset": stats_reset,
        "waiting_locks": waiting_locks,
        "table_rows": table_rows,
        "indexes": indexes,
        "all_table_indexes": all_table_indexes,
    }


def evaluate_evidence(target, evidence):
    evidence = evidence if isinstance(evidence, dict) else {}
    errors = []
    if evidence.get("target_index") != target:
        errors.append("evidence target does not match requested index")
    if evidence.get("decision") != "drop":
        errors.append("evidence decision must be drop")
    if not str(evidence.get("real_sql") or "").strip():
        errors.append("real SQL is missing")
    pg_stat = evidence.get("pg_stat") if isinstance(evidence.get("pg_stat"), dict) else {}
    if "idx_scan" not in pg_stat:
        errors.append("pg_stat idx_scan is missing")
    if not pg_stat.get("stats_reset"):
        errors.append("pg_stat stats_reset is missing")
    explain = evidence.get("explain") if isinstance(evidence.get("explain"), dict) else {}
    if not explain.get("with_target"):
        errors.append("with-target EXPLAIN evidence is missing")
    if not explain.get("without_target"):
        errors.append("without-target EXPLAIN evidence is missing")
    replacements = explain.get("replacement_indexes")
    if not isinstance(replacements, list) or not replacements:
        errors.append("replacement index evidence is missing")
    return errors


def evaluate_preconditions(target, snapshot, evidence):
    errors = []
    if target in PROTECTED_INDEXES:
        errors.append("index is protected")
    if target not in APPLY_ALLOWLIST:
        errors.append("index is not in the exact apply allowlist")
    errors.extend(evaluate_evidence(target, evidence))
    if snapshot.get("in_recovery"):
        errors.append("PostgreSQL is in recovery")
    if int(snapshot.get("waiting_locks") or 0):
        errors.append(f"waiting table locks={snapshot.get('waiting_locks')}")
    index = (snapshot.get("indexes") or {}).get(target)
    if not index:
        errors.append("target index is missing")
        return errors
    if not (index.get("valid") and index.get("ready")):
        errors.append("target index is not valid/ready")
    if index.get("primary"):
        errors.append("target is a primary-key index")
    if index.get("constraint_name"):
        errors.append(f"target owns constraint {index.get('constraint_name')}")
    if not index.get("owner_member"):
        errors.append(
            f"current role cannot act for table owner {index.get('table_owner')}"
        )
    pg_stat = evidence.get("pg_stat") if isinstance(evidence, dict) else {}
    if "idx_scan" in pg_stat:
        try:
            if int(pg_stat["idx_scan"]) != int(index.get("scan_count") or 0):
                errors.append(
                    "live idx_scan changed after evidence capture: "
                    f"evidence={pg_stat['idx_scan']} live={index.get('scan_count')}"
                )
        except (TypeError, ValueError):
            errors.append("evidence idx_scan is invalid")
    replacements = ((evidence.get("explain") or {}).get("replacement_indexes") or [])
    table_indexes = (snapshot.get("all_table_indexes") or {}).get(index.get("table"), {})
    for replacement in replacements:
        item = table_indexes.get(replacement)
        if not item or not (item.get("valid") and item.get("ready")):
            errors.append(f"replacement index is missing or invalid: {replacement}")
    return errors


def _rollback_sql(index_definition):
    definition = str(index_definition or "").strip().rstrip(";")
    if definition.startswith("CREATE UNIQUE INDEX "):
        definition = definition.replace(
            "CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX CONCURRENTLY ", 1
        )
    elif definition.startswith("CREATE INDEX "):
        definition = definition.replace(
            "CREATE INDEX ", "CREATE INDEX CONCURRENTLY ", 1
        )
    else:
        raise RuntimeError("cannot derive rollback SQL from index definition")
    return definition + ";"


def evaluate_postconditions(target, before, after):
    errors = []
    if target in (after.get("indexes") or {}):
        errors.append("drop target still exists")
    if after.get("table_rows") != before.get("table_rows"):
        errors.append(
            f"table row counts changed {before.get('table_rows')}->{after.get('table_rows')}"
        )
    before_tables = before.get("all_table_indexes") or {}
    after_tables = after.get("all_table_indexes") or {}
    for table_name, indexes in before_tables.items():
        for index_name in indexes:
            if index_name == target:
                continue
            after_item = (after_tables.get(table_name) or {}).get(index_name)
            if not after_item:
                errors.append(f"unrelated index disappeared: {index_name}")
            elif not (after_item.get("valid") and after_item.get("ready")):
                errors.append(f"unrelated index became invalid: {index_name}")
    return errors


def run_cleanup(connection, *, target, evidence, apply):
    with connection.cursor() as cursor:
        cursor.execute("SET lock_timeout = '5s'")
        cursor.execute("SET statement_timeout = '15min'")
        before = capture_snapshot(cursor, [target])
        errors = evaluate_preconditions(target, before, evidence)
        if errors:
            raise RuntimeError("; ".join(errors))
        rollback_sql = _rollback_sql(before["indexes"][target]["definition"])
        result = {
            "status": "ready",
            "target_index": target,
            "before": before,
            "evidence_sha256": hashlib.sha256(
                json.dumps(evidence, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest(),
            "rollback_sql": rollback_sql,
        }
        if not apply:
            return result

        started = time.monotonic()
        cursor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{target}")
        result["ddl_seconds"] = round(time.monotonic() - started, 4)
        after = capture_snapshot(
            cursor,
            [target],
            table_names=tuple(sorted(before.get("table_rows") or {})),
        )
        result["after"] = after
        post_errors = evaluate_postconditions(target, before, after)
        if post_errors:
            cursor.execute(rollback_sql)
            result["rollback_attempted"] = True
            restored = capture_snapshot(
                cursor,
                [target],
                table_names=tuple(sorted(before.get("table_rows") or {})),
            )
            result["rollback"] = restored
            restored_target = (restored.get("indexes") or {}).get(target)
            if not restored_target or not (
                restored_target.get("valid") and restored_target.get("ready")
            ):
                post_errors.append("rollback index is missing or invalid")
            raise RuntimeError("; ".join(post_errors))
        result["status"] = "applied"
        return result


def _load_evidence(path):
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("evidence JSON must contain an object")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Audit historical index candidates; mutate one exact index only with "
            "--apply and a complete evidence JSON file."
        )
    )
    parser.add_argument("--index", choices=AUDIT_INDEXES)
    parser.add_argument("--evidence-json", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.index:
        parser.error("--apply requires exactly one --index")
    if args.apply and not args.evidence_json:
        parser.error("--apply requires --evidence-json")

    database_url = _database_url()
    if not database_url.startswith(("postgresql://", "postgres://")):
        print("ERROR: PostgreSQL DIRECT_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        evidence = _load_evidence(args.evidence_json)
    except Exception as exc:
        print(f"ERROR: invalid evidence: {exc}", file=sys.stderr)
        return 2

    connection = psycopg2.connect(database_url, connect_timeout=5)
    connection.autocommit = True
    try:
        if not args.index:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
                cursor.execute("SET statement_timeout = '30s'")
                result = {
                    "status": "audit_only",
                    "protected_indexes": PROTECTED_INDEXES,
                    "apply_allowlist": APPLY_ALLOWLIST,
                    "snapshot": capture_snapshot(cursor, AUDIT_INDEXES),
                }
        else:
            result = run_cleanup(
                connection,
                target=args.index,
                evidence=evidence,
                apply=args.apply,
            )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
