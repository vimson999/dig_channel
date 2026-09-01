import copy
import json
from pathlib import Path
import tempfile
import unittest

from services.report_video_director import (
    _avoid_repeated_media_grammar,
    build_director_package,
    director_package_hash,
    evaluate_full_render_gate,
    human_approval,
    validate_director_package,
)
from services.report_video_director_test_runner import (
    _audience_headline,
    _audience_takeaway,
    _build_asr_caption_cues,
    _hook_durations,
    _render_segment_durations,
)


class ReportVideoDirectorContractTest(unittest.TestCase):
    def setUp(self):
        self.material = {
            "job_id": "material-001",
            "feature": "report_script_material_workflow",
            "title": "算力产业链景气度研报",
            "pdf": {
                "attachment_id": "pdf-001",
                "filename": "算力产业链.pdf",
                "path": "/tmp/算力产业链.pdf",
            },
            "structured_result": {
                "title": "算力产业链景气度研报",
                "landscape_title": "算力需求继续上行",
                "vertical_title": "订单增长能持续吗",
                "final_script_for_tts": (
                    "这份研报的核心结论是算力需求仍在上行。"
                    "一季度服务器订单同比增长百分之三十，成为最直接的验证。"
                    "图表显示供给扩张仍慢于需求增长，价格因此保持韧性。"
                    "不过海外限制与资本开支波动仍是主要风险，需要持续跟踪。"
                ),
            },
        }
        self.evidence_map = [
            {
                "evidence_id": "ev-001",
                "page": 2,
                "quote": "算力需求仍在上行",
                "script_excerpt": "这份研报的核心结论是算力需求仍在上行。",
                "kind": "pdf_quote",
            },
            {
                "evidence_id": "ev-002",
                "page": 6,
                "quote": "一季度服务器订单同比增长30%",
                "script_excerpt": "一季度服务器订单同比增长百分之三十，成为最直接的验证。",
                "kind": "pdf_table",
            },
            {
                "evidence_id": "ev-003",
                "page": 9,
                "quote": "供给扩张仍慢于需求增长",
                "script_excerpt": "图表显示供给扩张仍慢于需求增长，价格因此保持韧性。",
                "kind": "pdf_chart",
            },
            {
                "evidence_id": "ev-004",
                "page": 15,
                "quote": "海外限制与资本开支波动",
                "script_excerpt": "不过海外限制与资本开支波动仍是主要风险，需要持续跟踪。",
                "kind": "pdf_quote",
            },
        ]

    def _package(self):
        return build_director_package(
            self.material,
            self.evidence_map,
            target_segment_chars=60,
        )

    def _completed_artifacts(self, package):
        gate = package["preview_gate"]
        return {
            "keyframes": [
                {
                    "preview_id": item["preview_id"],
                    "status": "completed",
                    "artifact_id": f"asset:{item['preview_id']}",
                }
                for item in gate["keyframe_plan"]
            ],
            "preview_clips": [
                {
                    "preview_id": item["preview_id"],
                    "status": "completed",
                    "artifact_id": f"asset:{item['preview_id']}",
                }
                for item in gate["preview_clip_plan"]
            ],
        }

    def test_builds_mixed_media_package_with_ratio_specific_contracts(self):
        package = self._package()

        self.assertTrue(package["validation"]["valid"], package["validation"])
        self.assertFalse(package["preview_gate"]["full_render"]["allowed"])
        self.assertGreaterEqual(len(package["preview_gate"]["preview_clip_plan"]), 2)
        self.assertLessEqual(len(package["preview_gate"]["preview_clip_plan"]), 3)
        media_types = {item["media"]["type"] for item in package["segments"]}
        self.assertGreaterEqual(len(media_types), 2)
        self.assertIn("title_card", media_types)
        self.assertTrue(
            media_types.intersection(
                {"chart", "pdf_highlight", "logic_card", "character_explainer"}
            )
        )
        for segment in package["segments"]:
            self.assertTrue(segment["evidence_refs"])
            self.assertIn("16:9", segment["variants"])
            self.assertIn("9:16", segment["variants"])
            self.assertFalse(segment["dispatch"]["full_asset_generation_allowed"])
        selected_media = {
            item["media_type"]
            for item in package["preview_gate"]["preview_clip_plan"]
        }
        self.assertTrue(
            selected_media.intersection(
                {"original_pdf", "pdf_highlight", "pdf_scroll", "chart"}
            )
        )
        self.assertEqual(
            package["production_metadata"]["visibility"],
            "manifest_only",
        )
        self.assertTrue(package["production_metadata"]["must_not_render"])
        self.assertEqual(
            package["audience_copy"]["source_attribution"]["display_policy"],
            "evidence_detail_only",
        )
        viewer_contract = package["viewer_quality_contract"]
        self.assertEqual(
            viewer_contract["opening_hook"]["conflict_by_seconds"],
            3,
        )
        self.assertEqual(
            viewer_contract["visual_storytelling"]["max_non_card_gap_seconds"],
            30,
        )
        self.assertFalse(
            viewer_contract["card_copy"]["automatic_ellipsis_allowed"]
        )
        self.assertEqual(
            viewer_contract["reference_library"]["version"],
            "director_reference_library_v1",
        )
        self.assertTrue(
            viewer_contract["visual_storytelling"]["shot_intent_required"]
        )
        self.assertTrue(
            viewer_contract["transition"]["semantic_transition_required"]
        )
        self.assertTrue(
            all(
                segment["direction"]["shot_intent"]
                and segment["direction"]["reference_rule_ids"]
                for segment in package["segments"]
            )
        )
        rendered_copy = " ".join(
            str(package["audience_copy"].get(key) or "")
            for key in (
                "program_label",
                "logic_label",
                "risk_label",
                "summary_label",
            )
        )
        self.assertNotRegex(
            rendered_copy.lower(),
            r"evidence|editor|director|preview|detail|task|approval",
        )

    def test_audience_copy_rejects_production_metadata_leak(self):
        package = self._package()
        package["audience_copy"]["program_label"] = "PDF EVIDENCE PREVIEW"
        package["package_hash"] = director_package_hash(package)

        validation = validate_director_package(package)

        self.assertFalse(validation["valid"])
        self.assertIn(
            "production_metadata_leak",
            {item["code"] for item in validation["errors"]},
        )

    def test_reference_contract_rejects_missing_shot_intent(self):
        package = self._package()
        package["segments"][1]["direction"].pop("shot_intent")
        package["package_hash"] = director_package_hash(package)

        validation = validate_director_package(package)

        self.assertFalse(validation["valid"])
        self.assertIn(
            "invalid_shot_intent",
            {item["code"] for item in validation["errors"]},
        )

    def test_reference_rule_breaks_third_identical_proof_grammar(self):
        replacement = _avoid_repeated_media_grammar(
            "pdf_scroll",
            [{"kind": "pdf_quote", "quote": "供需仍未平衡"}],
            ["pdf_scroll", "pdf_scroll"],
            is_last=False,
        )

        self.assertEqual(replacement, "logic_card")

    def test_render_headline_never_uses_internal_media_purpose(self):
        segment = {
            "narration": {
                "text": "七月份玻璃价格逆势上涨百分之六，库存同步回落。"
            },
            "media": {
                "type": "pdf_highlight",
                "purpose": "PDF EVIDENCE editor preview status",
            },
        }

        headline = _audience_headline(segment)
        takeaway = _audience_takeaway(segment)

        self.assertIn("玻璃价格", headline)
        self.assertNotIn("EVIDENCE", headline)
        self.assertNotIn("editor", headline)
        self.assertIn("库存", takeaway)

    def test_opening_hook_reserves_conflict_data_and_question_beats(self):
        self.assertEqual(_hook_durations(30.0)[:3], [3.0, 5.0, 7.0])
        self.assertEqual(sum(_hook_durations(30.0)), 30.0)
        self.assertEqual(sum(_hook_durations(13.5)), 13.5)

    def test_fine_grained_captions_use_asr_boundaries_but_keep_script_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "asr.json"
            cache_path.write_text(
                json.dumps(
                    [
                        {"start": 0.0, "end": 1.2, "text": "错误识别一"},
                        {"start": 1.2, "end": 2.8, "text": "错误识别二"},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            segment = {
                "segment_id": "seg-001",
                "narration": {"text": "真实脚本第一句，真实脚本第二句。"},
            }
            alignment = {
                "timeline": [
                    {
                        "segment_id": "seg-001",
                        "start_seconds": 0.0,
                        "end_seconds": 2.8,
                    }
                ],
                "asr_metadata": {"cache_path": str(cache_path)},
            }

            cues = _build_asr_caption_cues([segment], alignment, 2.8)

        self.assertGreaterEqual(len(cues), 1)
        self.assertEqual(cues[0]["start"], 0.0)
        self.assertEqual(cues[-1]["end"], 2.8)
        rendered = "".join(item["text"] for item in cues)
        self.assertIn("真实脚本", rendered)
        self.assertNotIn("错误识别", rendered)
        self.assertTrue(
            all(len("".join(item["text"].split())) >= 4 for item in cues),
            cues,
        )

    def test_missing_evidence_fails_closed(self):
        package = build_director_package(
            self.material,
            [],
            target_segment_chars=60,
        )

        self.assertFalse(package["validation"]["valid"])
        codes = {item["code"] for item in package["validation"]["errors"]}
        self.assertIn("missing_evidence", codes)
        gate = evaluate_full_render_gate(package)
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["next_step"], "fix_director_package")

    def test_full_render_requires_completed_previews_and_human_approval(self):
        package = self._package()
        review = human_approval(
            package,
            reviewer_id="human-reviewer",
            decision="approved",
            reviewed_at="2026-07-29T08:00:00+00:00",
        )

        without_previews = evaluate_full_render_gate(package, human_review=review)
        self.assertFalse(without_previews["allowed"])
        self.assertIn(
            "representative_keyframes_incomplete",
            {item["code"] for item in without_previews["blockers"]},
        )

        allowed = evaluate_full_render_gate(
            package,
            preview_artifacts=self._completed_artifacts(package),
            human_review=review,
        )
        self.assertTrue(allowed["allowed"], allowed)
        self.assertEqual(allowed["next_step"], "full_asset_production_may_be_dispatched")

    def test_package_edit_invalidates_prior_approval(self):
        package = self._package()
        review = human_approval(
            package,
            reviewer_id="human-reviewer",
            decision="approved",
        )
        edited = copy.deepcopy(package)
        edited["segments"][0]["direction"]["motion"] = "revised_layered_reveal"
        edited["revision"] += 1
        edited["package_hash"] = director_package_hash(edited)

        validation = validate_director_package(edited)
        self.assertTrue(validation["valid"], validation)
        gate = evaluate_full_render_gate(
            edited,
            preview_artifacts=self._completed_artifacts(edited),
            human_review=review,
        )
        self.assertFalse(gate["allowed"])
        self.assertIn(
            "human_approval_stale",
            {item["code"] for item in gate["blockers"]},
        )

    def test_modify_and_terminate_are_never_approvals(self):
        package = self._package()
        artifacts = self._completed_artifacts(package)
        for decision in ("modify", "terminated"):
            review = human_approval(
                package,
                reviewer_id="human-reviewer",
                decision=decision,
            )
            gate = evaluate_full_render_gate(
                package,
                preview_artifacts=artifacts,
                human_review=review,
            )
            self.assertFalse(gate["allowed"])
            self.assertIn(
                "human_approval_missing",
                {item["code"] for item in gate["blockers"]},
            )

    def test_isolated_test_mode_can_auto_authorize_after_previews(self):
        package = build_director_package(
            self.material,
            self.evidence_map,
            target_segment_chars=60,
            execution_mode="test",
        )
        gate = evaluate_full_render_gate(
            package,
            preview_artifacts=self._completed_artifacts(package),
            execution_mode="test",
            test_authorization={
                "isolated": True,
                "publish_allowed": False,
                "production_task_mutation_allowed": False,
                "test_task_id": "test:director-unit",
                "output_root": "/tmp/director-unit",
            },
        )
        self.assertTrue(gate["allowed"], gate)
        self.assertEqual(gate["status"], "test_auto_authorized")
        self.assertEqual(gate["next_step"], "isolated_test_render_may_be_dispatched")

    def test_test_mode_without_isolation_stays_blocked(self):
        package = build_director_package(
            self.material,
            self.evidence_map,
            target_segment_chars=60,
            execution_mode="test",
        )
        gate = evaluate_full_render_gate(
            package,
            preview_artifacts=self._completed_artifacts(package),
            execution_mode="test",
            test_authorization={"publish_allowed": False},
        )
        self.assertFalse(gate["allowed"])
        self.assertIn(
            "unsafe_test_authorization",
            {item["code"] for item in gate["blockers"]},
        )

    def test_production_package_cannot_be_overridden_to_test_mode(self):
        package = self._package()
        gate = evaluate_full_render_gate(
            package,
            preview_artifacts=self._completed_artifacts(package),
            execution_mode="test",
            test_authorization={
                "isolated": True,
                "publish_allowed": False,
                "production_task_mutation_allowed": False,
                "test_task_id": "test:mode-confusion",
                "output_root": "/tmp/mode-confusion",
            },
        )
        self.assertFalse(gate["allowed"])
        self.assertIn(
            "execution_mode_mismatch",
            {item["code"] for item in gate["blockers"]},
        )

    def test_full_render_uses_existing_asr_timeline_when_supplied(self):
        segments = [
            {"segment_id": "seg-001", "narration": {"text": "第一段"}},
            {"segment_id": "seg-002", "narration": {"text": "第二段"}},
        ]
        durations, policy = _render_segment_durations(
            segments,
            10.0,
            {
                "timeline": [
                    {
                        "segment_id": "seg-001",
                        "start_seconds": 0,
                        "end_seconds": 4.25,
                    },
                    {
                        "segment_id": "seg-002",
                        "start_seconds": 4.25,
                        "end_seconds": 10,
                    },
                ]
            },
        )
        self.assertEqual(policy, "existing_whole_audio_asr_alignment")
        self.assertEqual(durations, [4.25, 5.75])


if __name__ == "__main__":
    unittest.main()
