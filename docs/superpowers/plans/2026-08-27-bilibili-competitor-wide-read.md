# Bilibili Competitor Wide-Read Elimination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the Bilibili competitor sync query that reads 30 stored `raw_payload` JSON documents back from PostgreSQL while preserving full incoming audit JSON, unchanged-row write protection, created/updated counts, and notification titles.

**Architecture:** Keep the existing external fetch and transaction boundary. Replace the wide ORM reconciliation read with an `id,bvid` identity snapshot, then persist existing rows through two bounded batch updates: one for scalar projections/`last_seen_at`, and one conditional `raw_payload IS DISTINCT FROM incoming` update. PostgreSQL uses one `UPDATE ... FROM (VALUES ...)` per shape; SQLite keeps a typed executemany fallback for deterministic unit tests. New videos continue through the ORM insert path with the complete source JSON.

**Tech Stack:** Python 3.11, Flask-SQLAlchemy, SQLAlchemy Core/ORM, PostgreSQL JSONB, SQLite unit tests, pytest.

**Spec:** `docs/bugs/reviews/2026-08-27-db-performance-audit.md`

## Global Constraints

- Do not add or execute production DDL, `CREATE/DROP INDEX`, table rewrites, or data backfills.
- Preserve complete incoming `raw_payload`; never replace audit JSON with a hash, subset, or synthetic payload.
- Existing rows use O(1) database round trips per fetched page; no per-video SELECT or UPDATE loop.
- The identity read may return only `id` and `bvid`; it must not load `raw_payload`, `description`, or other source projections.
- Unchanged raw JSON must not rewrite the JSONB column; changed JSON that does not alter scalar projections must still persist.
- Preserve `created`, `created_titles`, `updated`, competitor summary fields, credential success/error handling, and Session release behavior.
- Work in the current user-selected checkout and preserve unrelated dirty changes; do not commit unless the user explicitly requests it.

---

### Task 1: Lock the identity-read and audit-JSON contracts

**Files:**
- Modify: `tests/unit/test_bilibili_intelligence_scheduler.py:382-515`

**Interfaces:**
- Consumes: `services.bilibili_intelligence_service.sync_competitor(competitor_id: str, *, page_size: int = 30) -> Dict[str, Any]`
- Produces: regression evidence that the reconciliation SELECT is narrow and that raw JSON changes survive without scalar changes.

- [x] **Step 1: Extend the unchanged-row test to capture the actual SQL boundary**

Collect statements containing `FROM bilibili_competitor_videos`, then assert the identity SELECT contains `id` and `bvid` but excludes `raw_payload` and `description`. Capture `after_cursor_execute` row counts and assert any UPDATE mentioning `raw_payload` affects zero rows for the unchanged fixture.

```python
identity_selects = [
    statement.lower()
    for statement in statements
    if statement.lower().lstrip().startswith("select")
    and "from bilibili_competitor_videos" in statement.lower()
]
self.assertEqual(len(identity_selects), 1)
self.assertIn("bilibili_competitor_videos.id", identity_selects[0])
self.assertIn("bilibili_competitor_videos.bvid", identity_selects[0])
self.assertNotIn("raw_payload", identity_selects[0])
self.assertNotIn("description", identity_selects[0])
```

- [x] **Step 2: Add a raw-only change test**

Seed one existing video, fetch the same scalar fields plus `source_revision="v2"`, call `sync_competitor`, expire the Session, and assert the stored `raw_payload["source_revision"]` equals `"v2"`. Also assert the identity SELECT remains narrow.

```python
refreshed = db.session.get(BilibiliCompetitorVideo, "video-raw-change-id")
self.assertEqual(refreshed.raw_payload["source_revision"], "v2")
self.assertEqual(result["created"], 0)
self.assertEqual(result["updated"], 1)
```

- [x] **Step 3: Run the new tests and verify RED**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m pytest -q \
  tests/unit/test_bilibili_intelligence_scheduler.py \
  -k 'does_not_rewrite_unchanged_raw_payload or persists_raw_only_change_without_wide_identity_read'
```

Expected: FAIL because the current reconciliation SELECT includes `raw_payload` and all `_VIDEO_SYNC_COLUMNS`.

### Task 2: Replace the wide ORM reconciliation with bounded batch updates

**Files:**
- Modify: `src/backend/services/bilibili_intelligence_service.py:1-270`
- Test: `tests/unit/test_bilibili_intelligence_scheduler.py`

**Interfaces:**
- Consumes: list of `_video_values()` mappings with full `raw_payload`.
- Produces: `_update_existing_video_rows(mappings: Sequence[Dict[str, Any]]) -> None`; each mapping contains `id` plus all source projection values.

- [x] **Step 1: Define scalar and raw update shapes**

```python
_VIDEO_SCALAR_UPDATE_FIELDS = (
    "competitor_id", "content_type", "aid", "title", "description",
    "cover_url", "duration_seconds", "published_at", "play_count",
    "comment_count", "danmaku_count", "is_charging", "last_seen_at",
)
```

Do not include `bvid`, `id`, match/decision fields, `first_seen_at`, or `raw_payload` in this tuple.

- [x] **Step 2: Add the PostgreSQL batch builder**

Build a typed `VALUES` alias containing `id` and the requested fields. Cast `raw_payload` to PostgreSQL `JSONB`, matching the authoritative production schema, and generate `UPDATE ... FROM incoming WHERE target.id=incoming.id AND any field IS DISTINCT FROM incoming`.

```python
incoming_values = {
    field: cast(
        incoming.c[field],
        JSONB if field == "raw_payload" else table.c[field].type,
    )
    for field in fields
}
```

- [x] **Step 3: Add the portable typed executemany fallback**

For SQLite/non-PostgreSQL, bind `id` and every field with the destination SQLAlchemy column type. Add the same `IS DISTINCT FROM` predicate and execute one parameter batch per shape.

- [x] **Step 4: Persist scalar projections and audit JSON in separate shapes**

Call the batch helper once with `_VIDEO_SCALAR_UPDATE_FIELDS`, then once with `("raw_payload",)`. The second statement must carry `WHERE stored.raw_payload IS DISTINCT FROM incoming.raw_payload`, so unchanged JSON affects zero rows while raw-only changes persist.

- [x] **Step 5: Narrow the reconciliation read**

Replace the `load_only(id, *_VIDEO_SYNC_COLUMNS)` ORM query with:

```python
existing = {
    row.bvid: row.id
    for row in db.session.query(
        BilibiliCompetitorVideo.id,
        BilibiliCompetitorVideo.bvid,
    ).filter(BilibiliCompetitorVideo.bvid.in_(bvids)).all()
}
```

Create new ORM rows as before; accumulate existing mappings and pass them to `_update_existing_video_rows()` once after the loop.

- [x] **Step 6: Run the RED tests and the full Bilibili unit slice**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m pytest -q \
  tests/unit/test_bilibili_intelligence_scheduler.py \
  tests/unit/test_bilibili_intelligence_service.py
```

Expected: 23 tests pass after extending the unchanged-row case and adding the raw-only change case.

### Task 3: Verify PostgreSQL SQL shape and runtime replacement

**Files:**
- Modify: `docs/bugs/2026-08-19-bilibili-intelligence-duplicate-scheduler-write-amplification.md`
- Modify: `docs/bugs/reviews/2026-08-27-db-performance-audit.md`
- Modify: `docs/bugs/recurrence-registry.json`
- Modify: `/Users/v9/.codex/automations/db/memory.md`

**Interfaces:**
- Consumes: compiled PostgreSQL statements, unit evidence, restart safety gate, scheduler/API runtime.
- Produces: a complete `CODE_CHANGED` or `RUNTIME_REPLACED / OBSERVING` evidence chain without claiming CLOSED prematurely.

- [x] **Step 1: Compile and inspect the PostgreSQL statements**

Compile both batch shapes with the PostgreSQL dialect and assert/inspect that identity SELECT excludes `raw_payload`, raw UPDATE casts incoming JSON to JSONB, and both updates use `IS DISTINCT FROM`. Do not execute production DDL.

- [x] **Step 2: Run code-quality and regression gates**

```bash
PYTHONPATH=src/backend venv/bin/python -m pytest -q tests/unit
venv/bin/python -m compileall -q src/backend
git diff --check
jq empty docs/bugs/recurrence-registry.json
venv/bin/python scripts/dev/review_bug_records.py --strict
```

- [x] **Step 3: Replace runtime only through the restart gate**

Run `venv/bin/python scripts/dev/check-restart-safety.py`. Only if it reports no non-interruptible tasks, run `scripts/dev/restart-dev-stack.sh`; otherwise keep the code as `CODE_CHANGED / OBSERVING` and record the blocking task IDs.

- [x] **Step 4: Run one real bounded Bilibili sync and inspect logs**

Use the existing scheduler `once` entry point. Require 6/6 sources or record partial failures, confirm the old wide SELECT fingerprint is absent after the new PID cutover, and retain full raw JSON for one changed/new row. Do not publish external content.

- [x] **Step 5: Update root-cause records and next-window acceptance**

Record before/after SELECT columns, request/scheduler origin, sync duration, SQL count, JSON preservation, test output, runtime PID, and remaining PgBouncer/index risk. Keep status `OBSERVING` until a subsequent complete window has wide-read fingerprint 0 and no audit loss.

## Self-review

- Spec coverage: the plan covers the report's Bilibili wide-read root cause, payload completeness, no-DDL constraint, query budget, tests, runtime replacement, and recurrence evidence. Celery control-plane and PgBouncer route/timeouts are independent subsystems and remain separate OPEN workstreams rather than being bundled into this code change.
- Placeholder scan: complete; every code, test, command, and runtime step has an explicit target and expected result.
- Type consistency: all update mappings use string field names from `_VIDEO_SCALAR_UPDATE_FIELDS`; `raw_payload` is a dict and is explicitly cast to production JSONB; identity mappings are `bvid -> id` strings.
