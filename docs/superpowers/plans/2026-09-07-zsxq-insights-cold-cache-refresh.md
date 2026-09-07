# ZSXQ Insights Cold Cache Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prewarm the real ZSXQ Insights default page and make stale responses trigger one bounded server-side refresh without changing query semantics or database schema.

**Architecture:** Normalize request arguments once, derive a v5 semantic cache identity, and move response-cache ownership into a focused service. A Web-only daemon refresher uses one local execution slot plus a token-protected Redis lock; the API returns stale data immediately, while the browser polls ordinary cache reads and retains `_refresh=1` only as a fail-open fallback.

**Tech Stack:** Python 3.11, Flask, SQLAlchemy/Flask-SQLAlchemy, Redis, JavaScript Fetch API, `unittest`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-09-07-zsxq-insights-cold-cache-refresh-design.md`

## Global Constraints

- Preserve the existing Insights JSON schema, theme rules, sorting, pagination, fresh TTL, stale TTL, and synchronous cold-miss fallback.
- Cache version is `v5`; identity includes normalized `group_id`, `theme_id`, `range_days`, `query`, `page`, `page_size`, and `sort`, never `_refresh`.
- Default warmup is exactly `1824528822 / ai-sales / 90 / empty query / page 1 / page_size 48 / latest`.
- Run at most one refresh thread per Web process and one refresh per cache identity across processes.
- Do not add or modify SQL, DDL, indexes, materialized views, Celery queues, workers, schedulers, or long-lived services.
- Never log query text, response bodies, credentials, Redis URLs, cookies, or tokens; logs may contain only the cache digest and non-sensitive dimensions.
- Redis and background failures are fail-open: stale stays available and cold misses retain the current synchronous path.
- Do not clear Redis or restart/deploy the live stack as part of this implementation.
- Preserve all unrelated modified and untracked files already present in the worktree.
- Tests use fakes and injected runners; they must not depend on a real Redis server, real database, `sleep`, or scheduling races.

---

### Task 1: One normalization contract for computation and cache identity

**Files:**
- Modify: `src/backend/services/zsxq_insights_service.py:200-238,808-832`
- Modify: `tests/unit/test_zsxq_insights_service.py:1-75`

**Interfaces:**
- Consumes: raw values accepted by the current `build_insights` signature.
- Produces: `normalize_insights_arguments(*, group_id, theme_id, range_days, query, page, page_size, sort) -> dict[str, object]`; `build_insights` uses the returned mapping as its sole normalized input.

- [ ] **Step 1: Add failing normalization-contract tests**

Add the public import and these cases to `ZsxqInsightsContractTest`:

```python
from services.zsxq_insights_service import normalize_insights_arguments

def test_insights_arguments_normalize_once_for_build_and_cache(self):
    normalized = normalize_insights_arguments(
        group_id=None,
        theme_id="unknown-theme",
        range_days="900",
        query="  Ozon  ",
        page="2",
        page_size="99999",
        sort="LATEST",
    )
    self.assertEqual(
        normalized,
        {
            "group_id": "1824528822",
            "theme_id": "ai-sales",
            "range_days": 730,
            "query": "ozon",
            "page": 2,
            "page_size": 100,
            "sort": "latest",
        },
    )

def test_insights_arguments_reject_invalid_page_before_work(self):
    with self.assertRaisesRegex(ValueError, "page 必须大于 0"):
        normalize_insights_arguments(page=0)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_zsxq_insights_service.ZsxqInsightsContractTest.test_insights_arguments_normalize_once_for_build_and_cache \
  tests.unit.test_zsxq_insights_service.ZsxqInsightsContractTest.test_insights_arguments_reject_invalid_page_before_work
```

Expected: import failure because `normalize_insights_arguments` does not exist.

- [ ] **Step 3: Implement the public normalizer and route `build_insights` through it**

Add beside the existing `_normalize_*` helpers:

```python
def normalize_insights_arguments(
    *,
    group_id=GROUP_ID_SHENGCAI,
    theme_id="ai-sales",
    range_days=90,
    query="",
    page=1,
    page_size=DEFAULT_PAGE_SIZE,
    sort="latest",
):
    normalized_theme = str(theme_id or "ai-sales").strip().lower()
    if normalized_theme not in THEME_LOOKUP:
        normalized_theme = "ai-sales"
    return {
        "group_id": str(group_id or GROUP_ID_SHENGCAI),
        "theme_id": normalized_theme,
        "range_days": _normalize_range_days(range_days),
        "query": str(query or "").strip().lower(),
        "page": _normalize_page(page),
        "page_size": _normalize_page_size(page_size),
        "sort": _normalize_sort(sort),
    }
```

At the start of `build_insights`, call the helper and unpack all seven values from its result. Remove the later duplicate unknown-theme fallback because the helper now owns that behavior.

- [ ] **Step 4: Run service and API baseline tests**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_zsxq_insights_service \
  tests.unit.test_zsxq_insights_api
```

Expected: all current assertions pass; `build_insights` still accepts raw string inputs.

- [ ] **Step 5: Commit Task 1 only**

```bash
git add src/backend/services/zsxq_insights_service.py tests/unit/test_zsxq_insights_service.py
git commit -m "refactor: normalize zsxq insights arguments once"
```

---

### Task 2: Semantic v5 cache storage service

**Files:**
- Create: `src/backend/services/zsxq_insights_cache.py`
- Create: `tests/unit/test_zsxq_insights_cache.py`
- Modify: `src/backend/apis/v1/zsxq_insights.py:1-130`

**Interfaces:**
- Consumes: normalized mappings returned by `normalize_insights_arguments` and a Flask app object.
- Produces: `OverviewCacheEntry`, `overview_cache_key(params, stale=False)`, `get_cached_overview(app, params)`, `store_overview_payload(app, params, payload)`, and `clear_local_overview_cache()`.

- [ ] **Step 1: Create failing semantic-key tests**

Create `tests/unit/test_zsxq_insights_cache.py` with a minimal fake Redis and these tests:

```python
import unittest
from unittest.mock import patch

from flask import Flask

from services.zsxq_insights_cache import (
    clear_local_overview_cache,
    get_cached_overview,
    overview_cache_key,
    store_overview_payload,
)
from services.zsxq_insights_service import normalize_insights_arguments


class FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.values[key] = value
        return True


class ZsxqInsightsCacheTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=False)
        clear_local_overview_cache()

    def tearDown(self):
        clear_local_overview_cache()

    def test_semantically_equivalent_arguments_share_v5_key(self):
        implicit = normalize_insights_arguments()
        explicit = normalize_insights_arguments(
            group_id="1824528822", theme_id="AI-SALES", range_days="90",
            query="  ", page="1", page_size="30", sort="LATEST",
        )
        self.assertEqual(overview_cache_key(implicit), overview_cache_key(explicit))
        self.assertIn(":v5:", overview_cache_key(implicit))

    def test_refresh_control_never_enters_cache_identity(self):
        params = normalize_insights_arguments()
        self.assertNotIn("refresh", overview_cache_key(params))

    def test_real_dimensions_have_distinct_keys(self):
        first = normalize_insights_arguments(page_size=30)
        second = normalize_insights_arguments(page_size=48)
        self.assertNotEqual(overview_cache_key(first), overview_cache_key(second))
```

- [ ] **Step 2: Run key tests and verify RED**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_zsxq_insights_cache.ZsxqInsightsCacheTest.test_semantically_equivalent_arguments_share_v5_key \
  tests.unit.test_zsxq_insights_cache.ZsxqInsightsCacheTest.test_real_dimensions_have_distinct_keys
```

Expected: import failure because `services.zsxq_insights_cache` does not exist.

- [ ] **Step 3: Implement the cache identity and entry type**

Start `src/backend/services/zsxq_insights_cache.py` with focused constants, a frozen value type, and deterministic identity:

```python
from dataclasses import dataclass
import hashlib
import json

OVERVIEW_CACHE_VERSION = "v5"
OVERVIEW_CACHE_FIELDS = (
    "group_id", "theme_id", "range_days", "query", "page", "page_size", "sort",
)

@dataclass(frozen=True)
class OverviewCacheEntry:
    raw: str | bytes
    tier: str
    stale: bool = False

def overview_cache_digest(params):
    identity = {field: params[field] for field in OVERVIEW_CACHE_FIELDS}
    encoded = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def overview_cache_key(params, *, stale=False):
    suffix = ":stale" if stale else ""
    return f"shioa:zsxq-insights:overview:{OVERVIEW_CACHE_VERSION}:{overview_cache_digest(params)}{suffix}"
```

- [ ] **Step 4: Add failing storage-tier tests**

Append tests that patch `_redis_client_for_app` with `FakeRedis`:

```python
def test_store_then_read_uses_local_fresh_tier(self):
    redis = FakeRedis()
    params = normalize_insights_arguments()
    with patch("services.zsxq_insights_cache._redis_client_for_app", return_value=redis):
        store_overview_payload(self.app, params, {"success": True, "data": {"topics": []}})
        entry = get_cached_overview(self.app, params)
    self.assertEqual(entry.tier, "local")
    self.assertFalse(entry.stale)

def test_expired_fresh_redis_value_falls_back_to_stale(self):
    redis = FakeRedis()
    params = normalize_insights_arguments()
    redis.values[overview_cache_key(params, stale=True)] = '{"success":true}'
    with patch("services.zsxq_insights_cache._redis_client_for_app", return_value=redis):
        entry = get_cached_overview(self.app, params)
    self.assertEqual(entry.tier, "redis-stale")
    self.assertTrue(entry.stale)
```

- [ ] **Step 5: Implement bounded local/Redis fresh and stale storage**

Move the current TTL parsing, 64-entry `OrderedDict`, lock, Redis lookup, and `setex` behavior out of the API module. The service functions must accept an explicit Flask `app`, use `app.json.dumps(payload)`, return `OverviewCacheEntry | None`, and silently degrade on Redis read/write errors. `clear_local_overview_cache()` empties only the process-local mapping for tests.

Keep these exact limits from the current API:

```python
OVERVIEW_LOCAL_CACHE_MAX_ENTRIES = 64
DEFAULT_FRESH_TTL_SECONDS = 1800
DEFAULT_STALE_TTL_SECONDS = 86400
MAX_FRESH_TTL_SECONDS = 21600
MAX_STALE_TTL_SECONDS = 604800
```

The read order must be explicit and stop at the first usable entry:

```python
def get_cached_overview(app, params):
    fresh_key = overview_cache_key(params)
    raw = _get_local(fresh_key, app=app)
    if raw:
        return OverviewCacheEntry(raw=raw, tier="local")
    client = _redis_client_for_app(app)
    if client is None:
        return None
    try:
        raw = client.get(fresh_key)
        if raw:
            _store_local(fresh_key, raw, app=app)
            return OverviewCacheEntry(raw=raw, tier="redis")
        stale_raw = client.get(overview_cache_key(params, stale=True))
    except Exception:
        return None
    if not stale_raw:
        return None
    return OverviewCacheEntry(raw=stale_raw, tier="redis-stale", stale=True)
```

`store_overview_payload` serializes once, stores local fresh, writes Redis fresh with the bounded fresh TTL, and writes Redis stale with the bounded stale TTL. It returns the serialized value so the API uses exactly the bytes that were cached.

- [ ] **Step 6: Adapt the API imports without changing route behavior**

Replace the API-owned cache globals/helpers with imports from the new service. Keep a thin `_cached_response(entry)` HTTP formatter and keep `overview()` synchronous for misses at this stage. Normalize request arguments before cache lookup:

```python
params = normalize_insights_arguments(
    group_id=request.args.get("group_id"),
    theme_id=request.args.get("theme"),
    range_days=request.args.get("range_days"),
    query=request.args.get("query"),
    page=request.args.get("page"),
    page_size=request.args.get("page_size"),
    sort=request.args.get("sort"),
)
app = current_app._get_current_object()
entry = None if force_refresh else get_cached_overview(app, params)
```

Call `build_insights(**params)` for a miss, then `store_overview_payload(app, params, payload)` and construct the existing origin response.

- [ ] **Step 7: Run cache, service, and API tests**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_zsxq_insights_service \
  tests.unit.test_zsxq_insights_cache \
  tests.unit.test_zsxq_insights_api
```

Expected: all pass; API responses retain `HIT`, `STALE`, `MISS`, tier, and private cache-control headers.

- [ ] **Step 8: Commit Task 2 only**

```bash
git add \
  src/backend/services/zsxq_insights_cache.py \
  src/backend/apis/v1/zsxq_insights.py \
  tests/unit/test_zsxq_insights_cache.py \
  tests/unit/test_zsxq_insights_api.py
git commit -m "refactor: isolate zsxq insights response cache"
```

---

### Task 3: Bounded token-safe background refresher

**Files:**
- Modify: `src/backend/services/zsxq_insights_cache.py`
- Modify: `tests/unit/test_zsxq_insights_cache.py`

**Interfaces:**
- Consumes: explicit Flask app, normalized params, `build_insights(**params)`, and the v5 cache store from Task 2.
- Produces: `request_overview_refresh(app, params, reason) -> str`, returning exactly `scheduled`, `in-flight`, or `client-required`.

- [ ] **Step 1: Extend `FakeRedis` for atomic lock tests**

Add deterministic `set`, `eval`, and call tracking:

```python
def set(self, key, value, *, nx=False, ex=None):
    if nx and key in self.values:
        return False
    self.values[key] = value
    return True

def eval(self, script, key_count, key, token):
    if self.values.get(key) == token:
        del self.values[key]
        return 1
    return 0
```

- [ ] **Step 2: Add failing single-flight and success-cleanup tests**

Use an injected thread factory whose `start()` runs the target synchronously so tests contain no races:

```python
class InlineThread:
    def __init__(self, *, target, args=(), kwargs=None, **_options):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)

def test_refresh_builds_and_stores_under_same_identity(self):
    redis = FakeRedis()
    params = normalize_insights_arguments(page_size=48)
    with patch("services.zsxq_insights_cache._redis_client_for_app", return_value=redis), \
         patch("services.zsxq_insights_cache.threading.Thread", InlineThread), \
         patch("services.zsxq_insights_cache.build_insights", return_value={"topics": []}) as build, \
         patch("services.zsxq_insights_cache._remove_db_session") as remove:
        state = request_overview_refresh(self.app, params, reason="stale")
        entry = get_cached_overview(self.app, params)
    self.assertEqual(state, "scheduled")
    build.assert_called_once_with(**params)
    remove.assert_called_once_with()
    self.assertFalse(entry.stale)

def test_existing_redis_lock_reports_in_flight_without_build(self):
    redis = FakeRedis()
    params = normalize_insights_arguments()
    redis.values[overview_refresh_lock_key(params)] = "other-token"
    with patch("services.zsxq_insights_cache._redis_client_for_app", return_value=redis), \
         patch("services.zsxq_insights_cache.build_insights") as build:
        state = request_overview_refresh(self.app, params, reason="stale")
    self.assertEqual(state, "in-flight")
    build.assert_not_called()
```

- [ ] **Step 3: Run the new refresh tests and verify RED**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_zsxq_insights_cache.ZsxqInsightsCacheTest.test_refresh_builds_and_stores_under_same_identity \
  tests.unit.test_zsxq_insights_cache.ZsxqInsightsCacheTest.test_existing_redis_lock_reports_in_flight_without_build
```

Expected: import or attribute failure for the refresh functions.

- [ ] **Step 4: Implement local and Redis single-flight acquisition**

Add one module-level `threading.BoundedSemaphore(1)`, a lock key derived from the v5 digest, a random `uuid.uuid4().hex` token, and a bounded lock TTL:

```python
def overview_refresh_lock_key(params):
    return f"shioa:zsxq-insights:refresh-lock:v5:{overview_cache_digest(params)}"

def _refresh_lock_ttl_seconds():
    raw = os.environ.get("ZSXQ_INSIGHTS_REFRESH_LOCK_TTL_SECONDS", "120")
    try:
        value = int(raw or 120)
    except (TypeError, ValueError):
        value = 120
    return max(61, min(value, 600))
```

The 61-second floor remains above the repository's current 60-second statement timeout; the 120-second default leaves cleanup margin without permitting an unbounded orphan lock.

Acquire the local slot non-blockingly. If unavailable, return `in-flight`. Then obtain Redis via `_redis_client_for_app(app)`; if unavailable or the Redis `set` call with `nx=True` and the bounded expiry raises, release the local slot and return `client-required`. If `set` returns false, release the local slot and return `in-flight`.

- [ ] **Step 5: Implement the daemon worker and compare-and-delete release**

Start exactly one named daemon thread after both locks are acquired. Its target enters `app.app_context()`, calls `build_insights(**params)`, stores `{"success": True, "data": result}`, and logs only digest/reason/PID/duration/result. Its `finally` block performs all three cleanups in this order:

```python
try:
    _remove_db_session()
finally:
    try:
        redis_client.eval(_RELEASE_LOCK_LUA, 1, lock_key, token)
    finally:
        _refresh_slot.release()
```

Use fixed event names in the message template so the audit parser can match them without reading free text:

```python
logger.info(
    "[ZSXQ_INSIGHTS_CACHE] event=refresh_started reason=%s digest=%s pid=%s",
    reason, digest, os.getpid(),
)
logger.info(
    "[ZSXQ_INSIGHTS_CACHE] event=refresh_completed reason=%s digest=%s pid=%s duration_ms=%.2f",
    reason, digest, os.getpid(), duration_ms,
)
logger.warning(
    "[ZSXQ_INSIGHTS_CACHE] event=refresh_failed reason=%s digest=%s pid=%s duration_ms=%.2f error_type=%s",
    reason, digest, os.getpid(), duration_ms, type(error).__name__,
)
```

Local-busy, Redis-lock-held, Redis-error, and thread-start-error exits log `event=refresh_skipped` with a fixed non-sensitive `result` value. Task 5 logs `event=warmup_scheduled` when its bootstrap thread starts.

If `thread.start()` raises, execute equivalent lock/slot cleanup synchronously and return `client-required`. `_remove_db_session()` imports `models.db` inside the function and calls `db.session.remove()`; it swallows cleanup errors without hiding the refresh result.

- [ ] **Step 6: Add failure, token-mismatch, local-busy, and log-redaction tests**

Add these concrete tests, importing the module as `cache_module` so private cleanup boundaries can be patched without production-only hooks:

```python
from unittest.mock import Mock
from services import zsxq_insights_cache as cache_module

def test_refresh_failure_preserves_stale_and_releases_resources(self):
    redis = FakeRedis()
    params = normalize_insights_arguments()
    stale_key = overview_cache_key(params, stale=True)
    lock_key = overview_refresh_lock_key(params)
    redis.values[stale_key] = '{"success":true,"data":{"marker":"stale"}}'
    with patch.object(cache_module, "_redis_client_for_app", return_value=redis), \
         patch.object(cache_module.threading, "Thread", InlineThread), \
         patch.object(cache_module, "build_insights", side_effect=RuntimeError("boom")), \
         patch.object(cache_module, "_remove_db_session") as remove:
        state = request_overview_refresh(self.app, params, reason="stale")
    self.assertEqual(state, "scheduled")
    self.assertEqual(redis.values[stale_key], '{"success":true,"data":{"marker":"stale"}}')
    self.assertNotIn(lock_key, redis.values)
    remove.assert_called_once_with()

def test_compare_delete_keeps_lock_owned_by_new_token(self):
    redis = FakeRedis()
    lock_key = "refresh-lock"
    redis.values[lock_key] = "new-token"
    cache_module._release_refresh_lock(redis, lock_key, "old-token")
    self.assertEqual(redis.values[lock_key], "new-token")

def test_busy_local_slot_creates_no_thread(self):
    params = normalize_insights_arguments()
    slot = Mock()
    slot.acquire.return_value = False
    with patch.object(cache_module, "_refresh_slot", slot), \
         patch.object(cache_module.threading, "Thread") as thread_factory:
        state = request_overview_refresh(self.app, params, reason="stale")
    self.assertEqual(state, "in-flight")
    thread_factory.assert_not_called()

def test_refresh_request_starts_daemon_without_running_build_inline(self):
    redis = FakeRedis()
    params = normalize_insights_arguments()
    slot = Mock()
    slot.acquire.return_value = True
    thread = Mock()
    with patch.object(cache_module, "_refresh_slot", slot), \
         patch.object(cache_module, "_redis_client_for_app", return_value=redis), \
         patch.object(cache_module.threading, "Thread", return_value=thread) as thread_factory, \
         patch.object(cache_module, "build_insights") as build:
        state = request_overview_refresh(self.app, params, reason="stale")
    self.assertEqual(state, "scheduled")
    build.assert_not_called()
    thread.start.assert_called_once_with()
    self.assertTrue(thread_factory.call_args.kwargs["daemon"])

def test_refresh_logs_digest_without_query_text(self):
    redis = FakeRedis()
    params = normalize_insights_arguments(query="private-search-text")
    with patch.object(cache_module, "_redis_client_for_app", return_value=redis), \
         patch.object(cache_module.threading, "Thread", InlineThread), \
         patch.object(cache_module, "build_insights", return_value={"topics": []}), \
         patch.object(cache_module.logger, "info") as info:
        request_overview_refresh(self.app, params, reason="stale")
    logged = " ".join(str(value) for call in info.call_args_list for value in call.args)
    self.assertNotIn("private-search-text", logged)
    self.assertIn(overview_cache_digest(params), logged)

def test_thread_start_failure_returns_client_required_and_unlocks(self):
    redis = FakeRedis()
    params = normalize_insights_arguments()
    slot = Mock()
    slot.acquire.return_value = True
    thread = Mock()
    thread.start.side_effect = RuntimeError("cannot start")
    with patch.object(cache_module, "_refresh_slot", slot), \
         patch.object(cache_module, "_redis_client_for_app", return_value=redis), \
         patch.object(cache_module.threading, "Thread", return_value=thread):
        state = request_overview_refresh(self.app, params, reason="stale")
    self.assertEqual(state, "client-required")
    self.assertNotIn(overview_refresh_lock_key(params), redis.values)
    slot.release.assert_called_once_with()
```

The failure test installs stale data before a failing build and verifies that only the refresh lock disappears. The redaction test uses a distinctive private query and inspects every emitted info argument.

- [ ] **Step 7: Run the complete cache test module**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_zsxq_insights_cache
```

Expected: all cache identity, storage, concurrency, cleanup, and redaction tests pass.

- [ ] **Step 8: Commit Task 3 only**

```bash
git add src/backend/services/zsxq_insights_cache.py tests/unit/test_zsxq_insights_cache.py
git commit -m "feat: add single-flight insights cache refresh"
```

---

### Task 4: Make stale API responses truthful and self-refreshing

**Files:**
- Modify: `src/backend/apis/v1/zsxq_insights.py:85-175`
- Modify: `tests/unit/test_zsxq_insights_api.py`

**Interfaces:**
- Consumes: `OverviewCacheEntry` and `request_overview_refresh(app, params, reason="stale")` from Tasks 2-3.
- Produces: stale responses with `X-Insights-Refresh` and a Warning value consistent with the owner of refresh.

- [ ] **Step 1: Replace request-context cache tests with route-level failing tests**

Patch `get_cached_overview` to return a stale entry and assert the API schedules refresh:

```python
@patch("apis.v1.zsxq_insights.request_overview_refresh", return_value="scheduled")
@patch("apis.v1.zsxq_insights.get_cached_overview")
def test_stale_response_schedules_server_refresh(self, cached, refresh):
    cached.return_value = OverviewCacheEntry(
        raw='{"success":true,"data":{"topics":[]}}',
        tier="redis-stale",
        stale=True,
    )
    response = self.client.get("/api/v1/zsxq-insights/overview?theme=ai-sales&page=1")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.headers["X-Insights-Cache"], "STALE")
    self.assertEqual(response.headers["X-Insights-Refresh"], "scheduled")
    self.assertIn("refreshing in background", response.headers["Warning"])
    refresh.assert_called_once()
```

Add concrete `in-flight` and `client-required` cases; the last must use exactly `110 - "Response is stale"` and must not contain `refreshing`:

```python
@patch("apis.v1.zsxq_insights.request_overview_refresh", return_value="in-flight")
@patch("apis.v1.zsxq_insights.get_cached_overview")
def test_stale_response_reports_existing_server_refresh(self, cached, _refresh):
    cached.return_value = OverviewCacheEntry('{"success":true}', "redis-stale", True)
    response = self.client.get("/api/v1/zsxq-insights/overview")
    self.assertEqual(response.headers["X-Insights-Refresh"], "in-flight")
    self.assertIn("refreshing in background", response.headers["Warning"])

@patch("apis.v1.zsxq_insights.request_overview_refresh", return_value="client-required")
@patch("apis.v1.zsxq_insights.get_cached_overview")
def test_stale_response_does_not_claim_failed_server_refresh(self, cached, _refresh):
    cached.return_value = OverviewCacheEntry('{"success":true}', "redis-stale", True)
    response = self.client.get("/api/v1/zsxq-insights/overview")
    self.assertEqual(response.headers["X-Insights-Refresh"], "client-required")
    self.assertEqual(response.headers["Warning"], '110 - "Response is stale"')
```

- [ ] **Step 2: Run stale API tests and verify RED**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_zsxq_insights_api.ZsxqInsightsApiTest.test_stale_response_schedules_server_refresh
```

Expected: missing refresh integration or header assertion failure.

- [ ] **Step 3: Integrate stale refresh and truthful headers**

When `entry.stale` is true, call the refresher before formatting the response and pass its return value to `_cached_response`. Implement exact Warning behavior:

```python
response.headers["X-Insights-Refresh"] = refresh_state
if refresh_state in {"scheduled", "in-flight"}:
    response.headers["Warning"] = '110 - "Response is stale; refreshing in background"'
else:
    response.headers["Warning"] = '110 - "Response is stale"'
```

Fresh hits must not contain `X-Insights-Refresh` or `Warning`. `_refresh=1` must bypass reads, invoke one synchronous build, and write the same normalized v5 identity.

- [ ] **Step 4: Update forwarding and validation assertions**

Change the API forwarding test to expect normalized integers/lowercase values:

```python
build_insights.assert_called_once_with(
    group_id="1824528822",
    theme_id="crossborder",
    range_days=180,
    query="ozon",
    page=3,
    page_size=100,
    sort="signal",
)
```

Patch `get_cached_overview` in the invalid-sort test and assert it is not called, proving validation precedes cache access.

- [ ] **Step 5: Run API, cache, and service tests**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_zsxq_insights_api \
  tests.unit.test_zsxq_insights_cache \
  tests.unit.test_zsxq_insights_service
```

Expected: all pass with no real Redis or database access.

- [ ] **Step 6: Commit Task 4 only**

```bash
git add src/backend/apis/v1/zsxq_insights.py tests/unit/test_zsxq_insights_api.py
git commit -m "feat: refresh stale insights responses server-side"
```

---

### Task 5: Web-only default-page startup warmup

**Files:**
- Modify: `src/backend/services/zsxq_insights_cache.py`
- Modify: `src/backend/app.py:55-175`
- Modify: `tests/unit/test_zsxq_insights_cache.py`
- Modify: `tests/unit/test_runtime_initialization_idempotency.py:330-370`

**Interfaces:**
- Consumes: the Task 3 refresh worker, `register_apis`, and existing `_start_web_background_threads(app)` decision.
- Produces: `schedule_default_overview_warmup(app) -> bool`; returns immediately after arranging one daemon bootstrap thread.

- [ ] **Step 1: Add failing default-parameter and non-blocking warmup tests**

In the cache tests, patch the thread class with a recorder that does not execute the target:

```python
def test_default_warmup_is_daemon_and_uses_real_page_identity(self):
    created = []

    class RecordedThread:
        def __init__(self, **kwargs):
            created.append(kwargs)
        def start(self):
            return None

    with patch("services.zsxq_insights_cache.threading.Thread", RecordedThread):
        self.assertTrue(schedule_default_overview_warmup(self.app))
    self.assertEqual(len(created), 1)
    self.assertTrue(created[0]["daemon"])
    self.assertEqual(created[0]["name"], "zsxq-insights-warmup")
```

Add synchronous target tests for the internal `_run_default_warmup(app)` function. A fresh entry skips refresh; a miss requests the exact real-page mapping:

```python
def test_default_warmup_target_skips_existing_fresh_entry(self):
    fresh = OverviewCacheEntry('{"success":true}', "redis", False)
    with patch.object(cache_module, "get_cached_overview", return_value=fresh), \
         patch.object(cache_module, "request_overview_refresh") as refresh:
        cache_module._run_default_warmup(self.app)
    refresh.assert_not_called()

def test_default_warmup_target_refreshes_exact_real_page(self):
    with patch.object(cache_module, "get_cached_overview", return_value=None), \
         patch.object(cache_module, "request_overview_refresh", return_value="scheduled") as refresh:
        cache_module._run_default_warmup(self.app)
    refresh.assert_called_once_with(
        self.app,
        {
            "group_id": "1824528822",
            "theme_id": "ai-sales",
            "range_days": 90,
            "query": "",
            "page": 1,
            "page_size": 48,
            "sort": "latest",
        },
        reason="startup",
    )
```

- [ ] **Step 2: Run warmup tests and verify RED**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_zsxq_insights_cache.ZsxqInsightsCacheTest.test_default_warmup_is_daemon_and_uses_real_page_identity
```

Expected: import or attribute failure for `schedule_default_overview_warmup`.

- [ ] **Step 3: Implement the default warmup bootstrap**

Define an immutable default mapping through the public normalizer:

```python
def default_overview_params():
    return normalize_insights_arguments(
        group_id=GROUP_ID_SHENGCAI,
        theme_id="ai-sales",
        range_days=90,
        query="",
        page=1,
        page_size=48,
        sort="latest",
    )
```

`schedule_default_overview_warmup` creates one `zsxq-insights-warmup` daemon thread and returns immediately. Its target enters the app context, skips if a fresh entry exists, and otherwise invokes the same refresh engine with reason `startup`. A module-level PID/thread guard prevents duplicate scheduling inside one process.

- [ ] **Step 4: Add failing app-factory role tests**

Extract a tiny function in `app.py` so its gate can be tested without constructing the complete application:

```python
def _schedule_zsxq_insights_warmup(app, *, register_apis, start_background_threads):
    if not register_apis or not start_background_threads:
        return False
    from services.zsxq_insights_cache import schedule_default_overview_warmup
    return schedule_default_overview_warmup(app)
```

Test all gates:

```python
def test_insights_warmup_requires_web_api_process(self):
    from app import _schedule_zsxq_insights_warmup
    app = MagicMock()
    with patch("services.zsxq_insights_cache.schedule_default_overview_warmup") as schedule:
        self.assertFalse(_schedule_zsxq_insights_warmup(app, register_apis=False, start_background_threads=True))
        self.assertFalse(_schedule_zsxq_insights_warmup(app, register_apis=True, start_background_threads=False))
        self.assertTrue(_schedule_zsxq_insights_warmup(app, register_apis=True, start_background_threads=True))
    schedule.assert_called_once_with(app)
```

- [ ] **Step 5: Wire the gate into `create_app`**

Immediately before `create_app` returns, after API registration, basic routes, and the authentication hook are fully configured, call:

```python
_schedule_zsxq_insights_warmup(
    app,
    register_apis=register_apis,
    start_background_threads=start_background_threads,
)
```

Do not join the thread, touch `/health`, or start warmup from worker/scheduler/test factories.

- [ ] **Step 6: Run warmup and runtime-initialization tests**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_zsxq_insights_cache \
  tests.unit.test_runtime_initialization_idempotency \
  tests.unit.test_worker_app_factory_boundary \
  tests.unit.test_security_boundary
```

Expected: all pass and no real background thread, Redis, or database is required by the new tests.

- [ ] **Step 7: Commit Task 5 only**

```bash
git add \
  src/backend/services/zsxq_insights_cache.py \
  src/backend/app.py \
  tests/unit/test_zsxq_insights_cache.py \
  tests/unit/test_runtime_initialization_idempotency.py
git commit -m "feat: prewarm the default insights page"
```

---

### Task 6: Bounded browser polling with force-refresh fallback

**Files:**
- Create: `src/frontend/js/zsxq-insights-refresh.js`
- Create: `tests/frontend/zsxq-insights-refresh.test.cjs`
- Modify: `src/frontend/js/zsxq-insights.js:246-360`
- Modify: `src/frontend/zsxq-insights.html:210-215`
- Modify: `tests/unit/test_zsxq_insights_frontend_contract.py:1-25`

**Interfaces:**
- Consumes: `X-Insights-Cache` and `X-Insights-Refresh` from Task 4.
- Produces: `window.ZsxqInsightsRefresh.refresh(options)` and the page adapter `refreshInsightsInBackground(url, context, refreshState)`; server-owned refresh uses bounded ordinary GET polling, while `client-required` retains one `_refresh=1` request.

- [ ] **Step 1: Create a failing Node behavior test and Python runner**

Create `tests/frontend/zsxq-insights-refresh.test.cjs`. Require the not-yet-created helper and exercise real asynchronous behavior with injected request and wait functions:

```javascript
const assert = require('node:assert/strict');
const path = require('node:path');

const helper = require(path.resolve(
    __dirname,
    '../../src/frontend/js/zsxq-insights-refresh.js',
));

async function main() {
    const waits = [];
    const urls = [];
    const freshPayloads = [];
    const responses = [
        {ok: true, cacheState: 'STALE', payload: {success: true, data: {version: 1}}},
        {ok: true, cacheState: 'STALE', payload: {success: true, data: {version: 1}}},
        {ok: true, cacheState: 'HIT', payload: {success: true, data: {version: 2}}},
    ];
    const serverResult = await helper.refresh({
        url: '/api/v1/zsxq-insights/overview?a=1',
        refreshState: 'scheduled',
        wait: async (delayMs) => { waits.push(delayMs); },
        request: async (url) => { urls.push(url); return responses.shift(); },
        isCurrent: () => true,
        onFresh: (payload) => { freshPayloads.push(payload); },
    });
    assert.equal(serverResult, 'fresh');
    assert.deepEqual(waits, [500, 1000, 2000]);
    assert.deepEqual(urls, [
        '/api/v1/zsxq-insights/overview?a=1',
        '/api/v1/zsxq-insights/overview?a=1',
        '/api/v1/zsxq-insights/overview?a=1',
    ]);
    assert.equal(freshPayloads.length, 1);
    assert.equal(freshPayloads[0].data.version, 2);

    const fallbackUrls = [];
    await helper.refresh({
        url: '/api/v1/zsxq-insights/overview?a=1',
        refreshState: 'client-required',
        request: async (url) => {
            fallbackUrls.push(url);
            return {ok: true, cacheState: 'MISS', payload: {success: true, data: {version: 3}}};
        },
        isCurrent: () => true,
        onFresh: () => {},
    });
    assert.deepEqual(fallbackUrls, ['/api/v1/zsxq-insights/overview?a=1&_refresh=1']);

    let stalePersisted = false;
    const exhausted = await helper.refresh({
        url: '/overview',
        refreshState: 'in-flight',
        wait: async () => {},
        request: async () => ({ok: true, cacheState: 'STALE', payload: {success: true}}),
        isCurrent: () => true,
        onFresh: () => { stalePersisted = true; },
    });
    assert.equal(exhausted, 'stale');
    assert.equal(stalePersisted, false);

    let supersededRequests = 0;
    const superseded = await helper.refresh({
        url: '/overview',
        refreshState: 'scheduled',
        wait: async () => {},
        request: async () => { supersededRequests += 1; },
        isCurrent: () => false,
        onFresh: () => {},
    });
    assert.equal(superseded, 'superseded');
    assert.equal(supersededRequests, 0);
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
```

Extend `tests/unit/test_zsxq_insights_frontend_contract.py` with a subprocess test so both pytest and the full Python unit discovery can invoke the Node contract:

```python
import subprocess

def test_insights_stale_refresh_behavior():
    contract = PROJECT_ROOT / "tests/frontend/zsxq-insights-refresh.test.cjs"
    completed = subprocess.run(
        ["node", str(contract)], cwd=PROJECT_ROOT,
        check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
```

Run:

```bash
node tests/frontend/zsxq-insights-refresh.test.cjs
```

Expected: `MODULE_NOT_FOUND` because `src/frontend/js/zsxq-insights-refresh.js` does not exist.

- [ ] **Step 2: Implement the isolated refresh policy**

Create a UMD helper matching existing frontend utility conventions:

```javascript
(function registerZsxqInsightsRefresh(root, factory) {
    const helper = factory();
    if (typeof module === 'object' && module.exports) module.exports = helper;
    if (root) root.ZsxqInsightsRefresh = helper;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
    const POLL_DELAYS_MS = Object.freeze([500, 1000, 2000, 4000]);
    const defaultWait = (delayMs) => new Promise((resolve) => setTimeout(resolve, delayMs));

    async function refresh(options) {
        const url = String(options.url || '');
        const state = String(options.refreshState || 'client-required');
        const request = options.request;
        const wait = options.wait || defaultWait;
        const isCurrent = options.isCurrent || (() => true);
        const onFresh = options.onFresh || (() => {});
        if (typeof request !== 'function') throw new TypeError('request is required');

        if (!['scheduled', 'in-flight'].includes(state)) {
            if (!isCurrent()) return 'superseded';
            const separator = url.includes('?') ? '&' : '?';
            const result = await request(`${url}${separator}_refresh=1`);
            if (!result?.ok || !result.payload?.success) return 'failed';
            if (!isCurrent()) return 'superseded';
            onFresh(result.payload);
            return 'fresh';
        }

        for (const delayMs of POLL_DELAYS_MS) {
            await wait(delayMs);
            if (!isCurrent()) return 'superseded';
            const result = await request(url);
            if (!result?.ok || !result.payload?.success) return 'failed';
            if (result.cacheState === 'STALE') continue;
            if (!isCurrent()) return 'superseded';
            onFresh(result.payload);
            return 'fresh';
        }
        return 'stale';
    }

    return {POLL_DELAYS_MS, refresh};
}));
```

- [ ] **Step 3: Run the Node behavior test and verify GREEN**

Run:

```bash
node tests/frontend/zsxq-insights-refresh.test.cjs
```

Expected: exit 0; server-owned polling uses three ordinary URLs before the fresh response, the fallback uses exactly one `_refresh=1`, four stale responses never invoke `onFresh`, and an obsolete request issues no fetch.

- [ ] **Step 4: Add a failing page-adapter integration assertion**

Update the existing Python contract to require the helper script before the page script and to run the Node behavior test. The HTML ordering assertion is a static asset-loading contract; polling correctness remains covered by real Node execution:

```python
html = (PROJECT_ROOT / "src/frontend/zsxq-insights.html").read_text(encoding="utf-8")
helper_tag = 'js/zsxq-insights-refresh.js?v=20260907-server-refresh'
page_tag = 'js/zsxq-insights.js?v=20260907-server-refresh'
assert html.index(helper_tag) < html.index(page_tag)
```

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m pytest -q \
  tests/unit/test_zsxq_insights_frontend_contract.py
```

Expected: failure because the HTML does not load the helper and the page adapter still owns the old force-refresh-only implementation.

- [ ] **Step 5: Wire the helper into the real page**

Load `js/zsxq-insights-refresh.js?v=20260907-server-refresh` immediately before the versioned main page script. When the initial response is stale, read the server owner and call:

```javascript
const refreshState = response.headers.get('X-Insights-Refresh') || 'client-required';
refreshInsightsInBackground(url, { append, page, requestId }, refreshState);
```

Replace the old function body with an adapter that turns Fetch responses into the helper's neutral result and persists only `onFresh` payloads:

```javascript
async function refreshInsightsInBackground(url, { append, page, requestId }, refreshState) {
    try {
        await window.ZsxqInsightsRefresh.refresh({
            url,
            refreshState,
            request: async (requestUrl) => {
                const response = await fetch(requestUrl);
                const payload = await readJsonResponse(response);
                return {
                    ok: response.ok,
                    cacheState: response.headers.get('X-Insights-Cache'),
                    payload,
                };
            },
            isCurrent: () => requestId === state.requestId,
            onFresh: (payload) => {
                storeCachedResponse(url, payload);
                if (requestId === state.requestId) {
                    applyInsightsPayload(payload, { append, page });
                }
            },
        });
    } catch (error) {
        console.warn('[ZSXQ_INSIGHTS] 静默刷新失败', error);
    }
}
```

The helper never invokes `onFresh` for stale poll results, so those responses cannot renew `savedAt`. The adapter has one catch boundary and never clears the current page.

- [ ] **Step 6: Run all Insights frontend and backend tests**

Run:

```bash
node tests/frontend/zsxq-insights-refresh.test.cjs

PYTHONPATH=src/backend venv/bin/python -m pytest -q \
  tests/unit/test_zsxq_insights_frontend_contract.py

PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_zsxq_insights_service \
  tests.unit.test_zsxq_insights_cache \
  tests.unit.test_zsxq_insights_api \
  tests.unit.test_runtime_initialization_idempotency
```

Expected: all pass; the legacy force-refresh string remains only in the fallback branch.

- [ ] **Step 7: Commit Task 6 only**

```bash
git add \
  src/frontend/js/zsxq-insights-refresh.js \
  src/frontend/js/zsxq-insights.js \
  src/frontend/zsxq-insights.html \
  tests/frontend/zsxq-insights-refresh.test.cjs \
  tests/unit/test_zsxq_insights_frontend_contract.py
git commit -m "feat: poll server-owned insights refreshes"
```

---

### Task 7: Full verification and completion audit

**Files:**
- Verify: `src/backend/services/zsxq_insights_service.py`
- Verify: `src/backend/services/zsxq_insights_cache.py`
- Verify: `src/backend/apis/v1/zsxq_insights.py`
- Verify: `src/backend/app.py`
- Verify: `src/frontend/js/zsxq-insights-refresh.js`
- Verify: `src/frontend/js/zsxq-insights.js`
- Verify: `src/frontend/zsxq-insights.html`
- Verify: all tests changed in Tasks 1-6

**Interfaces:**
- Consumes: the complete implementation and the design acceptance matrix.
- Produces: test, compile, diff, and commit evidence sufficient to claim code/test completion without claiming runtime deployment.

- [ ] **Step 1: Run the directed regression suite**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_zsxq_insights_service \
  tests.unit.test_zsxq_insights_cache \
  tests.unit.test_zsxq_insights_api \
  tests.unit.test_runtime_initialization_idempotency \
  tests.unit.test_worker_app_factory_boundary \
  tests.unit.test_security_boundary

PYTHONPATH=src/backend venv/bin/python -m pytest -q \
  tests/unit/test_zsxq_insights_frontend_contract.py

node tests/frontend/zsxq-insights-refresh.test.cjs
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run the complete unit suite**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest discover -s tests/unit -p 'test_*.py'
```

Expected: `OK`; if the sandbox blocks loopback ports, rerun the identical command with the required local permission rather than changing tests.

- [ ] **Step 3: Run static and repository gates**

Run:

```bash
venv/bin/python -m compileall -q src/backend tests/unit
venv/bin/python scripts/dev/check-celery-revoke-boundary.py
venv/bin/python scripts/dev/review_bug_records.py --strict
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 4: Audit implementation against every specification requirement**

Confirm with code and test evidence:

```text
[ ] v5 semantic identity and normalized defaults
[ ] real page_size=48 startup warmup
[ ] Web-only, non-blocking startup gate
[ ] one local refresh slot and token-safe Redis lock
[ ] truthful scheduled/in-flight/client-required headers and Warning
[ ] synchronous cold miss and client force-refresh fallback
[ ] bounded ordinary-GET browser polling
[ ] stale preservation and lock/session cleanup on every failure path
[ ] no SQL/DDL/Celery/deployment changes
[ ] logs contain digest but no query text or payload
```

Expected: each line maps to at least one passing directed test and the corresponding implementation branch.

- [ ] **Step 5: Review the exact branch diff without disturbing existing user changes**

Run:

```bash
git log --oneline --decorate aa98156..HEAD
git diff --stat aa98156..HEAD
git diff --check aa98156..HEAD
git status --short --branch
```

Expected: commits after design commit `aa98156` contain only the plan and scoped implementation; pre-existing modified/untracked audit files remain preserved.

- [ ] **Step 6: Commit the implementation plan if it is still uncommitted**

```bash
git add docs/superpowers/plans/2026-09-07-zsxq-insights-cold-cache-refresh.md
git commit -m "docs: add insights cache refresh implementation plan"
```

Do not restart the backend or claim production latency acceptance. Report code/test completion separately from the later optional runtime rollout described by the specification.

---

### Post-review hardening addendum (2026-09-07)

Independent review added these required acceptance checks before final verification:

- [ ] Treat only positive Web-server commands (`app.py`, Flask `run`, or a known WSGI server) as eligible for startup warmup; Flask DB commands, maintenance scripts, workers, schedulers, watchdogs, and unknown commands remain ineligible.
- [ ] Expose `X-Insights-Cache`, `X-Insights-Cache-Tier`, and `X-Insights-Refresh` through CORS and prove an allowed cross-origin response can read them.
- [ ] Track the active local refresh digest. A busy slot returns `in-flight` only for the same digest and `client-required` for a different digest.
- [ ] Catch both thread construction and `start()` failures, restoring every PID/thread guard, Redis lock, active digest, and local slot acquired by that path.
- [ ] Budget the Redis lease for all four timeout-bounded reads plus cleanup margin, cap it at 600 seconds, renew it every one-third TTL with token-safe Lua while the refresh lives, and atomically fence fresh/stale writes on lock-token ownership so an expired worker cannot overwrite a newer owner.
- [ ] Run explicit database-session cleanup before leaving the Flask app context.
- [ ] Expand cache-identity tests across every semantic field and exercise `_refresh=1` at the route boundary.

These items supersede the earlier 120-second single-statement lease assumption and the negative-only process-role gate in Tasks 3 and 5.
