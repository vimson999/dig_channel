import unittest
from datetime import datetime
from unittest.mock import patch

from services.zsxq_insights_service import (
    _released_read_session,
    _normalize_page_size,
    _plain_topic_text,
    _normalize_page,
    _normalize_range_days,
    _normalize_sort,
    _subtopic_breakdown,
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
    @patch("services.zsxq_insights_service.release_db_session_quietly")
    def test_released_read_session_preserves_caller_execute_boundary(self, release):
        with _released_read_session() as session:
            self.assertIsNotNone(session)

        release.assert_called_once_with()

    def test_plain_topic_text_decodes_known_zsxq_rich_tokens(self):
        raw = (
            '<e type="text_bold" title="%E6%88%91%E4%BB%AC%E8%81%8A%E4%BA%86AI" />'
            '，参加<e type="mention" uid="1" title="%40Jacky" />交流'
        )
        self.assertEqual(_plain_topic_text(raw), "我们聊了AI，参加@Jacky交流")

    def test_range_and_page_size_are_bounded(self):
        self.assertEqual(_normalize_range_days(1), 30)
        self.assertEqual(_normalize_range_days(900), 730)
        self.assertEqual(_normalize_page_size(99999), 100)
        with self.assertRaisesRegex(ValueError, "range_days"):
            _normalize_range_days("abc")
        with self.assertRaisesRegex(ValueError, "page_size"):
            _normalize_page_size(0)
        self.assertEqual(_normalize_page(None), 1)
        self.assertEqual(_normalize_page(2), 2)
        with self.assertRaisesRegex(ValueError, "page"):
            _normalize_page(0)

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
        self.assertEqual(summary["yiren_recognition"], "not_found")
        self.assertEqual(summary["yiren_label"], "未见亦仁认可")

    def test_subtopic_breakdown_uses_database_match_flags_without_body(self):
        topics = [
            {"subtopic_matches": {"获客", "成交"}},
            {"subtopic_matches": {"获客"}},
        ]
        breakdown = _subtopic_breakdown("ai-sales", topics)
        counts = {item["label"]: item["count"] for item in breakdown}
        self.assertEqual(counts["获客"], 2)
        self.assertEqual(counts["成交"], 1)
        self.assertEqual(counts["客服"], 0)


if __name__ == "__main__":
    unittest.main()
