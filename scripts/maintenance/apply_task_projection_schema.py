#!/usr/bin/env python3
"""Apply the task projection schema SQL through the app database config."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
DEFAULT_SQL = PROJECT_ROOT / "docs" / "sql" / "task_projection_schema_backfill_dbeaver_20260617.sql"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _split_sql(script: str):
    statements = []
    current = []
    in_single = False
    in_double = False
    i = 0
    while i < len(script):
        char = script[i]
        nxt = script[i + 1] if i + 1 < len(script) else ""
        if not in_single and not in_double and char == "-" and nxt == "-":
            while i < len(script) and script[i] != "\n":
                i += 1
            continue
        if char == "'" and not in_double:
            current.append(char)
            if in_single and nxt == "'":
                current.append(nxt)
                i += 2
                continue
            in_single = not in_single
            i += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            current.append(char)
            i += 1
            continue
        if char == ";" and not in_single and not in_double:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)
    return statements


def main():
    parser = argparse.ArgumentParser(description="Apply task projection schema and backfill SQL.")
    parser.add_argument("--sql", default=str(DEFAULT_SQL))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sql_path = Path(args.sql)
    if not sql_path.is_absolute():
        sql_path = PROJECT_ROOT / sql_path
    script = sql_path.read_text(encoding="utf-8")
    statements = _split_sql(script)

    if args.dry_run:
        print({"sql": str(sql_path), "statements": len(statements), "dry_run": True})
        return 0

    from app import create_app
    from models import db

    app = create_app()
    with app.app_context():
        raw = db.engine.raw_connection()
        try:
            cursor = raw.cursor()
            for index, statement in enumerate(statements, start=1):
                cursor.execute(statement)
                if cursor.description:
                    columns = [item[0] for item in cursor.description]
                    rows = cursor.fetchall()
                    print({"statement": index, "columns": columns, "rows": rows})
                else:
                    print({"statement": index, "rowcount": cursor.rowcount})
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    print({"sql": str(sql_path), "statements": len(statements), "applied": True})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
