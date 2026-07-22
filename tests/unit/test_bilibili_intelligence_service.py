import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.bilibili_intelligence_service import (  # noqa: E402
    deepseek_match_batch,
    _deepseek_model,
    _normalize_ai_match,
    _video_values,
    normalize_match_text,
    rank_local_candidates,
    title_similarity,
)
from apis.v1.bilibili_intelligence import VIDEO_LIST_COLUMNS  # noqa: E402


class BilibiliIntelligenceMatchingTest(unittest.TestCase):
    def test_institution_aliases_are_normalized_before_recall(self):
        self.assertIn("摩根士丹利", normalize_match_text("大摩：光模块行业更新"))
        self.assertIn("摩根士丹利", normalize_match_text("Morgan Stanley Optical Transceiver.pdf"))

    def test_same_research_subject_scores_above_unrelated_subject(self):
        competitor = "大摩：光模块市场增长，中际旭创盈利预测上调"
        related = "Morgan Stanley：中际旭创与光模块需求展望.pdf"
        unrelated = "瑞银：猪周期拐点与牧原股份盈利预测"

        self.assertGreater(title_similarity(competitor, related), title_similarity(competitor, unrelated))

    def test_rule_recall_returns_highest_scoring_candidate_first(self):
        candidates = [
            {"key": "attachment:unrelated", "title": "白酒库存与估值", "local_type": "zsxq_attachment", "local_id": "unrelated"},
            {"key": "attachment:target", "title": "长鑫上市与半导体设备投资机会", "local_type": "zsxq_attachment", "local_id": "target"},
        ]

        ranked = rank_local_candidates(
            "从长鑫上市看半导体设备投资机会",
            "report",
            candidates=candidates,
            limit=2,
        )

        self.assertEqual(ranked[0]["key"], "attachment:target")
        self.assertGreater(ranked[0]["rule_score"], ranked[1]["rule_score"])

    def test_ai_cannot_select_candidate_outside_recalled_set(self):
        normalized = _normalize_ai_match(
            {
                "match_status": "matched",
                "candidate_key": "attachment:invented",
                "confidence": 0.99,
                "relation": "same_source",
                "reason": "模型幻觉候选",
            },
            {"attachment:real": {"key": "attachment:real", "local_id": "real"}},
        )

        self.assertEqual(normalized["match_status"], "insufficient_evidence")
        self.assertIsNone(normalized["candidate"])
        self.assertEqual(normalized["confidence"], 0.0)

    def test_same_topic_cannot_be_promoted_to_definite_match(self):
        candidate = {"key": "attachment:real", "local_id": "real"}
        normalized = _normalize_ai_match(
            {
                "match_status": "matched",
                "candidate_key": "attachment:real",
                "confidence": 0.9,
                "relation": "same_topic",
            },
            {"attachment:real": candidate},
        )

        self.assertEqual(normalized["match_status"], "possible")
        self.assertEqual(normalized["relation"], "same_topic")

    def test_low_confidence_match_remains_possible(self):
        candidate = {"key": "attachment:real", "local_id": "real"}
        normalized = _normalize_ai_match(
            {
                "match_status": "matched",
                "candidate_key": "attachment:real",
                "confidence": 0.52,
                "relation": "same_source",
            },
            {"attachment:real": candidate},
        )

        self.assertEqual(normalized["match_status"], "possible")

    def test_video_list_payload_is_projected_from_bilibili_library_result(self):
        payload = _video_values(
            {
                "bvid": "BV1TEST",
                "aid": 123,
                "title": "测试投稿",
                "description": "简介",
                "pic": "//i.example/cover.jpg",
                "created": 1784307539,
                "length": "01:02:03",
                "play": "456",
                "comment": 7,
                "video_review": 8,
                "is_charging_arc": True,
            },
            competitor_id="competitor-1",
            content_type="report",
        )

        self.assertEqual(payload["bvid"], "BV1TEST")
        self.assertEqual(payload["duration_seconds"], 3723)
        self.assertEqual(payload["play_count"], 456)
        self.assertTrue(payload["is_charging"])

    def test_hot_video_list_excludes_cold_json_columns(self):
        column_names = {column.key for column in VIDEO_LIST_COLUMNS}

        self.assertNotIn("raw_payload", column_names)
        self.assertNotIn("match_payload", column_names)
        self.assertNotIn("description", column_names)

    def test_match_model_uses_feature_specific_override(self):
        with patch.dict(os.environ, {
            "BILIBILI_INTELLIGENCE_MATCH_MODEL": "feature-model",
            "DEEPSEEK_FLASH_MODEL": "flash-model",
            "DEEPSEEK_MODEL": "global-model",
        }, clear=False):
            self.assertEqual(_deepseek_model(), "feature-model")

    def test_deepseek_batch_uses_strict_low_temperature_contract(self):
        class FakeResponse:
            status_code = 200
            text = ""

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    "choices": [{
                        "message": {
                            "content": '{"matches":[{"video_id":"v1","match_status":"not_found","candidate_key":null,"confidence":0,"relation":"unrelated","reason":"无对应候选","evidence":[],"conflicts":[]}]}'
                        }
                    }]
                }

        with patch.dict(os.environ, {
            "DEEPSEEK_API_KEY": "test-key",
            "BILIBILI_INTELLIGENCE_MATCH_MODEL": "deepseek-v4-flash",
        }, clear=False), patch(
            "services.bilibili_intelligence_service.requests.post",
            return_value=FakeResponse(),
        ) as post:
            payload = deepseek_match_batch([{
                "video_id": "v1",
                "content_type": "report",
                "title": "对手标题",
                "candidates": [],
            }])

        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["matches"][0]["match_status"], "not_found")
        request_payload = post.call_args.kwargs["json"]
        self.assertEqual(request_payload["temperature"], 0.05)
        self.assertEqual(request_payload["messages"][0]["role"], "system")


if __name__ == "__main__":
    unittest.main()
