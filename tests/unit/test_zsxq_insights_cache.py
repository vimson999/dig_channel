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
        first = normalize_insights_arguments(page_size=30)
        second = normalize_insights_arguments(page_size=48)
        self.assertNotEqual(overview_cache_key(first), overview_cache_key(second))

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


if __name__ == "__main__":
    unittest.main()
