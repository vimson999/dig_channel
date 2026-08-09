import unittest
from unittest.mock import Mock, patch

from flask import Flask

from apis.v1.zsxq_insights import (
    OVERVIEW_CACHE_TTL_SECONDS,
    OVERVIEW_STALE_CACHE_TTL_SECONDS,
    _cached_overview_response,
    _overview_cache_key,
    bp,
)


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
            range_days="180",
            query="Ozon",
            page="3",
            page_size="120",
            sort="signal",
        )

    def test_refresh_flag_does_not_change_cache_identity(self):
        with self.app.test_request_context("/api/v1/zsxq-insights/overview?theme=ai-sales&page=1"):
            normal_key = _overview_cache_key()
        with self.app.test_request_context(
            "/api/v1/zsxq-insights/overview?theme=ai-sales&page=1&_refresh=1"
        ):
            refresh_key = _overview_cache_key()
        self.assertEqual(normal_key, refresh_key)
        self.assertGreaterEqual(OVERVIEW_CACHE_TTL_SECONDS, 1800)
        self.assertGreaterEqual(OVERVIEW_STALE_CACHE_TTL_SECONDS, 86400)

    @patch("apis.v1.zsxq_insights._local_cached_overview", return_value=None)
    @patch("apis.v1.zsxq_insights._overview_redis_client")
    def test_expired_fresh_cache_serves_stale_snapshot(self, redis_client, _local_cache):
        client = Mock()
        client.get.side_effect = [None, b'{"success":true,"data":{"topics":[]}}']
        redis_client.return_value = client
        with self.app.test_request_context(
            "/api/v1/zsxq-insights/overview?theme=ai-sales&page=1"
        ):
            response = _cached_overview_response()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Insights-Cache"], "STALE")
        self.assertEqual(response.headers["X-Insights-Cache-Tier"], "redis-stale")

    @patch("apis.v1.zsxq_insights.build_insights", side_effect=ValueError("sort 仅支持 latest 或 signal"))
    def test_overview_returns_structured_400(self, _build_insights):
        response = self.client.get("/api/v1/zsxq-insights/overview?sort=unknown")
        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], 400)

    @patch("apis.v1.zsxq_insights.get_topic_detail", return_value=None)
    def test_missing_topic_returns_404(self, _get_topic_detail):
        response = self.client.get("/api/v1/zsxq-insights/topic/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"]["code"], 404)


if __name__ == "__main__":
    unittest.main()
