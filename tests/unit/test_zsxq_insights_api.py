import unittest
from unittest.mock import patch

from flask import Flask

from apis.v1.zsxq_insights import bp


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
            page_size="120",
            sort="signal",
        )

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
