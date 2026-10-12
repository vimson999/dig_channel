# Diagnostics Scripts

This directory holds manual diagnostics for local DB, Redis, Celery, and publication task checks.

These scripts are not formal automated tests. Prefer adding reusable test coverage under `tests/`.

Run diagnostics from the project root unless a script says otherwise.

`daily_db_runtime_snapshot.py` provides the read-only durable-task and database-client
snapshot used by the daily DB audit. Pass UTC-naive `--since` and `--until` values that
correspond to the fixed Asia/Shanghai reporting window.
