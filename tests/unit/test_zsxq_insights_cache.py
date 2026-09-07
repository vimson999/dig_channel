import os
import threading
import unittest
from unittest.mock import Mock, patch

from flask import Flask, has_app_context

from services import zsxq_insights_cache as cache_module
from services.zsxq_insights_cache import (
    OverviewCacheEntry,
    clear_local_overview_cache,
    get_cached_overview,
    overview_cache_digest,
    overview_cache_key,
    overview_refresh_lock_key,
    request_overview_refresh,
    schedule_default_overview_warmup,
    store_overview_payload,
)
from services.zsxq_insights_service import normalize_insights_arguments


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.set_calls = []
        self.eval_calls = []

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.values[key] = value
        return True

    def set(self, key, value, *, nx=False, ex=None):
        self.set_calls.append((key, value, nx, ex))
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, script, key_count, *args):
        self.eval_calls.append((script, key_count, *args))
        if script == cache_module._STORE_IF_LOCK_OWNED_LUA:
            lock_key, fresh_key, stale_key, token, fresh_ttl, stale_ttl, value = args
            if self.values.get(lock_key) != token:
                return 0
            self.values[fresh_key] = value
            if int(stale_ttl) > 0:
                self.values[stale_key] = value
            return 1
        if script == cache_module._RENEW_LOCK_LUA:
            key, token, _ttl = args
            return int(self.values.get(key) == token)

        key, token = args
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0


class ExpiringFakeRedis(FakeRedis):
    def __init__(self):
        super().__init__()
        self.now = 0
        self.expirations = {}

    def _expire(self, key):
        expires_at = self.expirations.get(key)
        if expires_at is not None and expires_at <= self.now:
            self.values.pop(key, None)
            self.expirations.pop(key, None)

    def set(self, key, value, *, nx=False, ex=None):
        self._expire(key)
        stored = super().set(key, value, nx=nx, ex=ex)
        if stored and ex is not None:
            self.expirations[key] = self.now + int(ex)
        return stored

    def eval(self, script, key_count, *args):
        if script == cache_module._RENEW_LOCK_LUA:
            key, token, ttl = args
            self._expire(key)
            self.eval_calls.append((script, key_count, *args))
            if self.values.get(key) != token:
                return 0
            self.expirations[key] = self.now + int(ttl)
            return 1
        return super().eval(script, key_count, *args)


class InlineThread:
    def __init__(self, *, target, args=(), kwargs=None, **_options):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


class ZsxqInsightsCacheTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=False)
        clear_local_overview_cache()
        with cache_module._refresh_state_lock:
            cache_module._active_refresh_digest = None

    def tearDown(self):
        clear_local_overview_cache()
        with cache_module._refresh_state_lock:
            cache_module._active_refresh_digest = None

    def test_default_warmup_is_daemon_and_uses_real_page_identity(self):
        created = []

        class RecordedThread:
            def __init__(self, **kwargs):
                created.append(kwargs)

            def start(self):
                return None

        with patch.object(cache_module, "_warmup_pid", None), \
             patch.object(cache_module, "_warmup_thread", None), \
             patch("services.zsxq_insights_cache.threading.Thread", RecordedThread):
            self.assertTrue(schedule_default_overview_warmup(self.app))
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0]["daemon"])
        self.assertEqual(created[0]["name"], "zsxq-insights-warmup")

    def test_default_warmup_thread_constructor_failure_is_fail_open(self):
        with patch.object(cache_module, "_warmup_pid", None), \
             patch.object(cache_module, "_warmup_thread", None), \
             patch.object(
                 cache_module.threading,
                 "Thread",
                 side_effect=RuntimeError("cannot construct"),
             ):
            self.assertFalse(schedule_default_overview_warmup(self.app))
            self.assertIsNone(cache_module._warmup_pid)
            self.assertIsNone(cache_module._warmup_thread)

    def test_default_warmup_target_skips_existing_fresh_entry(self):
        fresh = OverviewCacheEntry('{"success":true}', "redis", False)
        with patch.object(cache_module, "get_cached_overview", return_value=fresh), \
             patch.object(cache_module, "request_overview_refresh") as refresh:
            cache_module._run_default_warmup(self.app)
        refresh.assert_not_called()

    def test_default_warmup_target_refreshes_exact_real_page(self):
        with patch.object(cache_module, "get_cached_overview", return_value=None), \
             patch.object(
                 cache_module,
                 "request_overview_refresh",
                 return_value="scheduled",
             ) as refresh:
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

    def test_semantically_equivalent_arguments_share_v5_key(self):
        implicit = normalize_insights_arguments()
        explicit = normalize_insights_arguments(
            group_id="1824528822",
            theme_id="AI-SALES",
            range_days="90",
            query="  ",
            page="1",
            page_size="30",
            sort="LATEST",
        )
        self.assertEqual(overview_cache_key(implicit), overview_cache_key(explicit))
        self.assertIn(":v5:", overview_cache_key(implicit))

    def test_refresh_control_never_enters_cache_identity(self):
        params = normalize_insights_arguments()
        self.assertNotIn("refresh", overview_cache_key(params))

    def test_real_dimensions_have_distinct_keys(self):
        baseline = overview_cache_key(normalize_insights_arguments())
        variants = {
            "group_id": {"group_id": "other-group"},
            "theme_id": {"theme_id": "crossborder"},
            "range_days": {"range_days": 180},
            "query": {"query": "Private Term"},
            "page": {"page": 2},
            "page_size": {"page_size": 48},
            "sort": {"sort": "signal"},
        }
        for dimension, arguments in variants.items():
            with self.subTest(dimension=dimension):
                self.assertNotEqual(
                    baseline,
                    overview_cache_key(normalize_insights_arguments(**arguments)),
                )

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

    def test_refresh_builds_and_stores_under_same_identity(self):
        redis = FakeRedis()
        params = normalize_insights_arguments(page_size=48)
        cleanup_contexts = []
        with patch("services.zsxq_insights_cache._redis_client_for_app", return_value=redis), \
             patch("services.zsxq_insights_cache.threading.Thread", InlineThread), \
             patch("services.zsxq_insights_cache.build_insights", return_value={"topics": []}) as build, \
             patch(
                 "services.zsxq_insights_cache._remove_db_session",
                 side_effect=lambda: cleanup_contexts.append(has_app_context()),
             ) as remove:
            state = request_overview_refresh(self.app, params, reason="stale")
            entry = get_cached_overview(self.app, params)
        self.assertEqual(state, "scheduled")
        build.assert_called_once_with(**params)
        remove.assert_called_once_with()
        self.assertEqual(cleanup_contexts, [True])
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
        self.assertEqual(
            redis.values[stale_key],
            '{"success":true,"data":{"marker":"stale"}}',
        )
        self.assertNotIn(lock_key, redis.values)
        remove.assert_called_once_with()

    def test_lease_thread_constructor_failure_releases_refresh_resources(self):
        redis = FakeRedis()
        params = normalize_insights_arguments()
        lock_key = overview_refresh_lock_key(params)
        with patch.object(cache_module, "_redis_client_for_app", return_value=redis), \
             patch.object(cache_module.threading, "Thread", InlineThread), \
             patch.object(
                 cache_module,
                 "_lease_thread_factory",
                 side_effect=RuntimeError("cannot construct lease keeper"),
             ), \
             patch.object(cache_module, "build_insights") as build:
            state = request_overview_refresh(self.app, params, reason="stale")

        self.assertEqual(state, "scheduled")
        build.assert_not_called()
        self.assertNotIn(lock_key, redis.values)
        self.assertIsNone(cache_module._active_refresh_digest)

    def test_refresh_starts_and_stops_daemon_lease_keeper(self):
        redis = FakeRedis()
        params = normalize_insights_arguments()
        lease_thread = Mock()
        with patch.object(cache_module, "_redis_client_for_app", return_value=redis), \
             patch.object(cache_module.threading, "Thread", InlineThread), \
             patch.object(
                 cache_module,
                 "_lease_thread_factory",
                 return_value=lease_thread,
             ) as lease_factory, \
             patch.object(cache_module, "build_insights", return_value={"topics": []}):
            state = request_overview_refresh(self.app, params, reason="stale")

        self.assertEqual(state, "scheduled")
        self.assertTrue(lease_factory.call_args.kwargs["daemon"])
        self.assertTrue(
            lease_factory.call_args.kwargs["name"].startswith(
                "zsxq-insights-lease-"
            )
        )
        lease_thread.start.assert_called_once_with()
        lease_thread.join.assert_called_once_with(timeout=1.0)

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
             patch.object(
                 cache_module,
                 "_active_refresh_digest",
                 overview_cache_digest(params),
             ), \
             patch.object(cache_module.threading, "Thread") as thread_factory:
            state = request_overview_refresh(self.app, params, reason="stale")
        self.assertEqual(state, "in-flight")
        thread_factory.assert_not_called()

    def test_busy_local_slot_for_other_identity_requires_client_refresh(self):
        active = normalize_insights_arguments(theme_id="ai-sales")
        requested = normalize_insights_arguments(theme_id="crossborder")
        slot = Mock()
        slot.acquire.return_value = False
        with patch.object(cache_module, "_refresh_slot", slot), \
             patch.object(
                 cache_module,
                 "_active_refresh_digest",
                 overview_cache_digest(active),
             ), \
             patch.object(cache_module.threading, "Thread") as thread_factory:
            state = request_overview_refresh(self.app, requested, reason="stale")
        self.assertEqual(state, "client-required")
        thread_factory.assert_not_called()

    def test_same_identity_is_published_before_blocking_redis_lock_call(self):
        params = normalize_insights_arguments()
        redis = FakeRedis()
        set_entered = threading.Event()
        continue_set = threading.Event()
        original_set = redis.set

        def blocking_set(*args, **kwargs):
            set_entered.set()
            if not continue_set.wait(timeout=2):
                raise RuntimeError("test did not release Redis set")
            return original_set(*args, **kwargs)

        redis.set = blocking_set
        slot = threading.BoundedSemaphore(1)
        background_thread = Mock()
        outer_result = []
        outer_thread = threading.Thread(
            target=lambda: outer_result.append(
                request_overview_refresh(self.app, params, reason="stale")
            )
        )
        with patch.object(cache_module, "_refresh_slot", slot), \
             patch.object(cache_module, "_active_refresh_digest", None), \
             patch.object(cache_module, "_redis_client_for_app", return_value=redis), \
             patch.object(
                 cache_module.threading,
                 "Thread",
                 return_value=background_thread,
             ):
            outer_thread.start()
            self.assertTrue(set_entered.wait(timeout=2))
            concurrent_state = request_overview_refresh(
                self.app,
                params,
                reason="stale",
            )
            continue_set.set()
            outer_thread.join(timeout=2)

        self.assertFalse(outer_thread.is_alive())
        self.assertEqual(outer_result, ["scheduled"])
        self.assertEqual(concurrent_state, "in-flight")

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
        logged = " ".join(
            str(value)
            for call in info.call_args_list
            for value in call.args
        )
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

    def test_thread_constructor_failure_returns_client_required_and_unlocks(self):
        redis = FakeRedis()
        params = normalize_insights_arguments()
        slot = Mock()
        slot.acquire.return_value = True
        with patch.object(cache_module, "_refresh_slot", slot), \
             patch.object(cache_module, "_active_refresh_digest", None), \
             patch.object(cache_module, "_redis_client_for_app", return_value=redis), \
             patch.object(
                 cache_module.threading,
                 "Thread",
                 side_effect=RuntimeError("cannot construct"),
             ):
            state = request_overview_refresh(self.app, params, reason="stale")
        self.assertEqual(state, "client-required")
        self.assertNotIn(overview_refresh_lock_key(params), redis.values)
        self.assertIsNone(cache_module._active_refresh_digest)
        slot.release.assert_called_once_with()

    def test_refresh_lock_budget_covers_all_query_timeouts_plus_margin(self):
        with patch.dict(
            os.environ,
            {
                "DB_STATEMENT_TIMEOUT_MS": "60000",
                "ZSXQ_INSIGHTS_REFRESH_LOCK_TTL_SECONDS": "120",
            },
            clear=False,
        ):
            self.assertGreaterEqual(cache_module._refresh_lock_ttl_seconds(), 360)

    def test_token_safe_renewal_prevents_second_owner_past_original_expiry(self):
        redis = ExpiringFakeRedis()
        lock_key = "refresh-lock"
        self.assertTrue(redis.set(lock_key, "old-token", nx=True, ex=60))

        redis.now = 50
        self.assertTrue(
            cache_module._renew_refresh_lock(
                redis,
                lock_key,
                "old-token",
                60,
            )
        )
        redis.now = 61
        self.assertFalse(redis.set(lock_key, "new-token", nx=True, ex=60))

        redis.now = 111
        self.assertTrue(redis.set(lock_key, "new-token", nx=True, ex=60))
        self.assertFalse(
            cache_module._renew_refresh_lock(
                redis,
                lock_key,
                "old-token",
                60,
            )
        )
        self.assertEqual(redis.values[lock_key], "new-token")

    def test_lost_refresh_lease_preserves_stale_and_fences_old_result(self):
        redis = FakeRedis()
        params = normalize_insights_arguments()
        fresh_key = overview_cache_key(params)
        stale_key = overview_cache_key(params, stale=True)
        lock_key = overview_refresh_lock_key(params)
        stale_value = '{"success":true,"data":{"marker":"stale"}}'
        redis.values[stale_key] = stale_value

        def lose_lease_before_returning(**_params):
            redis.values[lock_key] = "new-owner-token"
            return {"marker": "obsolete"}

        with patch.object(cache_module, "_redis_client_for_app", return_value=redis), \
             patch.object(cache_module.threading, "Thread", InlineThread), \
             patch.object(
                 cache_module,
                 "build_insights",
                 side_effect=lose_lease_before_returning,
             ):
            state = request_overview_refresh(self.app, params, reason="stale")

        self.assertEqual(state, "scheduled")
        self.assertNotIn(fresh_key, redis.values)
        self.assertEqual(redis.values[stale_key], stale_value)
        self.assertEqual(redis.values[lock_key], "new-owner-token")


if __name__ == "__main__":
    unittest.main()
