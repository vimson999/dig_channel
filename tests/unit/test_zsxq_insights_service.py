import unittest
from datetime import datetime

from services.zsxq_insights_service import (
    _normalize_page_size,
    _normalize_range_days,
    _normalize_sort,
    _topic_item,
    _weekly_trend,
    matches_theme,
)


class ZsxqInsightsClassifierTest(unittest.TestCase):
    def test_ai_sales_requires_both_ai_and_sales_signals(self):
        self.assertTrue(matches_theme("用 AI 自动跟进私域客户并提升成交率", "ai-sales"))
        self.assertFalse(matches_theme("私域客户跟进复盘", "ai-sales"))
        self.assertFalse(matches_theme("Claude Code 编程实践", "ai-sales"))

    def test_crossborder_matches_ozon_case_insensitively(self):
        self.assertTrue(matches_theme("Ozon 五个店实操一个月", "crossborder"))

    def test_agent_workflow_matches_common_tools(self):
        self.assertTrue(matches_theme("用 Dify 搭建客服工作流", "agent-workflow"))


class ZsxqInsightsContractTest(unittest.TestCase):
    def test_range_and_page_size_are_bounded(self):
        self.assertEqual(_normalize_range_days(1), 30)
        self.assertEqual(_normalize_range_days(900), 730)
        self.assertEqual(_normalize_page_size(99999), 5000)
        with self.assertRaisesRegex(ValueError, "range_days"):
            _normalize_range_days("abc")
        with self.assertRaisesRegex(ValueError, "page_size"):
            _normalize_page_size(0)

    def test_sort_rejects_unknown_value(self):
        self.assertEqual(_normalize_sort("signal"), "signal")
        with self.assertRaisesRegex(ValueError, "sort"):
            _normalize_sort("popular")

    def test_weekly_trend_exposes_cross_year_filter_keys(self):
        topics = [
            {"created_at": datetime(2025, 12, 30), "has_proof": True},
            {"created_at": datetime(2026, 1, 2), "has_proof": False},
        ]
        trend = _weekly_trend(topics, datetime(2026, 1, 5), 30)
        self.assertEqual(len(trend["keys"]), len(trend["labels"]))
        self.assertIn("2025-12-29", trend["keys"])
        self.assertEqual(sum(trend["topics"]), 2)

    def test_topic_summary_does_not_duplicate_full_content(self):
        item = {
            "topic_id": "1",
            "title": "测试主题",
            "content": "正文" * 200,
            "article_excerpt": "",
            "author_name": "作者",
            "create_time": "2026-07-15 10:00:00",
            "tab_label": "最新",
            "has_proof": True,
            "has_demand": False,
            "files_count": 0,
            "images_count": 0,
        }
        summary = _topic_item(item)
        self.assertNotIn("content", summary)
        self.assertLessEqual(len(summary["excerpt"]), 180)


if __name__ == "__main__":
    unittest.main()
