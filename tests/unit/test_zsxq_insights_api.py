import unittest
from unittest.mock import patch

from flask import Flask

from apis.v1.zsxq_insights import (
    OVERVIEW_CACHE_TTL_SECONDS,
    OVERVIEW_STALE_CACHE_TTL_SECONDS,
    bp,
)
from services.zsxq_insights_cache import OverviewCacheEntry, overview_cache_key
from services.zsxq_insights_service import normalize_insights_arguments


class ZsxqInsightsApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    @patch("apis.v1.zsxq_insights.build_insights")
    def test_overview_forwards_filters_and_pagination(self, build_insights):
        build_insights.return_value = {"selected": {"id": "crossborder"}, "topics": []}
        response = self.client.get(
            "/api/v1/zsxq-insights/overview",
            query_string={
                "group_id": "1824528822",
                "theme": "crossborder",
                "range_days": "180",
                "query": "Ozon",
                "page": "3",
                "page_size": "120",
                "sort": "signal",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        build_insights.assert_called_once_with(
            group_id="1824528822",
            theme_id="crossborder",
            range_days=180,
            query="ozon",
            page=3,
            page_size=100,
            sort="signal",
        )

    def test_refresh_flag_does_not_change_cache_identity(self):
        normal_key = overview_cache_key(
            normalize_insights_arguments(theme_id="ai-sales", page=1)
        )
        refresh_key = overview_cache_key(
            normalize_insights_arguments(theme_id="ai-sales", page=1)
        )
        self.assertEqual(normal_key, refresh_key)
        self.assertGreaterEqual(OVERVIEW_CACHE_TTL_SECONDS, 1800)
        self.assertGreaterEqual(OVERVIEW_STALE_CACHE_TTL_SECONDS, 86400)

    @patch("apis.v1.zsxq_insights.request_overview_refresh", return_value="scheduled")
    @patch("apis.v1.zsxq_insights.get_cached_overview")
    def test_stale_response_schedules_server_refresh(self, cached, refresh):
        cached.return_value = OverviewCacheEntry(
            raw='{"success":true,"data":{"topics":[]}}',
            tier="redis-stale",
            stale=True,
        )
        response = self.client.get(
            "/api/v1/zsxq-insights/overview?theme=ai-sales&page=1"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Insights-Cache"], "STALE")
        self.assertEqual(response.headers["X-Insights-Cache-Tier"], "redis-stale")
        self.assertEqual(response.headers["X-Insights-Refresh"], "scheduled")
        self.assertIn("refreshing in background", response.headers["Warning"])
        refresh.assert_called_once()

    @patch("apis.v1.zsxq_insights.request_overview_refresh", return_value="in-flight")
    @patch("apis.v1.zsxq_insights.get_cached_overview")
    def test_stale_response_reports_existing_server_refresh(self, cached, _refresh):
        cached.return_value = OverviewCacheEntry(
            '{"success":true}',
            "redis-stale",
            True,
        )
        response = self.client.get("/api/v1/zsxq-insights/overview")
        self.assertEqual(response.headers["X-Insights-Refresh"], "in-flight")
        self.assertIn("refreshing in background", response.headers["Warning"])

    @patch("apis.v1.zsxq_insights.request_overview_refresh", return_value="client-required")
    @patch("apis.v1.zsxq_insights.get_cached_overview")
    def test_stale_response_does_not_claim_failed_server_refresh(self, cached, _refresh):
        cached.return_value = OverviewCacheEntry(
            '{"success":true}',
            "redis-stale",
            True,
        )
        response = self.client.get("/api/v1/zsxq-insights/overview")
        self.assertEqual(response.headers["X-Insights-Refresh"], "client-required")
        self.assertEqual(response.headers["Warning"], '110 - "Response is stale"')

    @patch("apis.v1.zsxq_insights.request_overview_refresh")
    @patch("apis.v1.zsxq_insights.get_cached_overview")
    def test_fresh_hit_has_no_refresh_or_warning_headers(self, cached, refresh):
        cached.return_value = OverviewCacheEntry('{"success":true}', "redis")
        response = self.client.get("/api/v1/zsxq-insights/overview")
        self.assertEqual(response.headers["X-Insights-Cache"], "HIT")
        self.assertNotIn("X-Insights-Refresh", response.headers)
        self.assertNotIn("Warning", response.headers)
        refresh.assert_not_called()

    @patch("apis.v1.zsxq_insights.store_overview_payload", return_value='{"success":true}')
    @patch("apis.v1.zsxq_insights.build_insights", return_value={"topics": []})
    @patch("apis.v1.zsxq_insights.get_cached_overview")
    def test_force_refresh_bypasses_read_and_writes_normalized_identity(
        self,
        cached,
        build,
        store,
    ):
        response = self.client.get(
            "/api/v1/zsxq-insights/overview?_refresh=1&theme=AI-SALES&page_size=48"
        )
        self.assertEqual(response.status_code, 200)
        cached.assert_not_called()
        params = build.call_args.kwargs
        self.assertEqual(params["theme_id"], "ai-sales")
        self.assertEqual(params["page_size"], 48)
        self.assertEqual(store.call_args.args[1], params)

    @patch("apis.v1.zsxq_insights.get_cached_overview")
    def test_overview_returns_structured_400(self, get_cached_overview):
        response = self.client.get("/api/v1/zsxq-insights/overview?sort=unknown")
        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], 400)
        get_cached_overview.assert_not_called()

    @patch("apis.v1.zsxq_insights.get_topic_detail", return_value=None)
    def test_missing_topic_returns_404(self, _get_topic_detail):
        response = self.client.get("/api/v1/zsxq-insights/topic/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"]["code"], 404)


if __name__ == "__main__":
    unittest.main()
