import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.report_audio_gem_v2 import (  # noqa: E402
    build_gem_v2_repair_prompt,
    build_gem_v2_trigger,
    resolve_gem_v2_mode,
    to_report_audio_structured_result,
    validate_gem_v2_response,
)


PDF_FILENAME = "瑞银-美光科技（MU.O）：一点NAND噪音，但DRAM更紧张-260810.pdf"


def _candidate(candidate_id, format_name, title, subtitle, claim_ids=None):
    return {
        "candidate_id": candidate_id,
        "format": format_name,
        "angle": "data",
        "title": title,
        "subtitle": subtitle,
        "intro": "开场",
        "claim_ids": list(claim_ids or ["C001"]),
        "evidence_summary": "研报正文支持",
        "promise": "解释核心矛盾",
        "primary_risk": "预测可能不兑现",
        "scores": {
            "thesis_alignment": 9,
            "factuality": 9,
            "information_gain": 8,
            "readability": 8,
            "restraint": 9,
        },
    }


def valid_payload():
    script = "第一段解释DRAM供需为何比预期更紧。\n\n第二段说明NAND噪音和验证风险。"
    content = [
        _candidate(f"C_TITLE_{index}", "content", f"内容标题{index}", f"内容副标题{index}")
        for index in range(1, 4)
    ]
    landscape = [
        _candidate(f"L_TITLE_{index}", "landscape", f"横屏标题{index}", f"横屏副标题{index}")
        for index in range(1, 4)
    ]
    vertical = [
        _candidate(f"V_TITLE_{index}", "vertical", f"竖屏标题{index}", f"竖屏副标题{index}")
        for index in range(1, 4)
    ]
    evidence = {
        "pdf_file": PDF_FILENAME,
        "pdf_page": 2,
        "printed_page_label": "2",
        "evidence_type": "text_quote",
        "source_excerpt": "DRAM supply remains tight",
        "chart_or_table_title": "",
        "confidence": "high",
        "limitation": "",
        "crop": None,
    }
    report = {
        "report_id": "R1",
        "provider": "gemini_gem",
        "source_files": [PDF_FILENAME],
        "metadata": {
            "institution_full_name": "UBS",
            "institution_short_name": "瑞银",
            "publication_date": "2026-08-10",
            "original_report_title": "一点NAND噪音，但DRAM更紧张",
            "report_type": "公司研报",
            "research_objects": ["Micron"],
            "regions": ["美国"],
            "currencies": ["USD"],
            "reporting_periods": ["2026"],
            "actual_pdf_page_count": 12,
            "missing_fields": [],
        },
        "material_boundary": {
            "provided_material_only": True,
            "external_information_used": False,
            "external_sources": [],
        },
        "core_analysis": {
            "core_conclusion": "DRAM供需更紧",
            "core_tension": "NAND短期噪音与DRAM上行并存",
            "key_variables": ["DRAM价格"],
            "logic_chain": ["供给受限", "价格上行"],
            "verification_indicators": ["合约价"],
            "falsification_conditions": ["供给超预期释放"],
        },
        "fact_ledger": [{
            "claim_id": "C001",
            "claim_type": "institution_view",
            "statement": "瑞银认为DRAM供需更紧",
            "attribution": "瑞银",
            "qualifiers": ["预测"],
            "metrics": [],
            "evidence_status": "verified",
            "evidence": [copy.deepcopy(evidence)],
        }],
        "title": content[0]["title"],
        "summary": "DRAM供需紧张是主逻辑，NAND只是短期噪音。",
        "final_script_for_tts": script,
        "paragraph_map": [
            {
                "script_paragraph_index": 1,
                "paragraph_role": "hook",
                "claim_ids": ["C001"],
                "contains_forecast": True,
                "contains_derived_implication": False,
            },
            {
                "script_paragraph_index": 2,
                "paragraph_role": "risk",
                "claim_ids": ["C001"],
                "contains_forecast": False,
                "contains_derived_implication": False,
            },
        ],
        "title_candidates": {
            "content": content,
            "landscape": landscape,
            "vertical": vertical,
        },
        "selected_titles": {
            "content_candidate_id": "C_TITLE_1",
            "landscape_candidate_id": "L_TITLE_1",
            "vertical_candidate_id": "V_TITLE_1",
            "selection_reason": "最贴近主结论",
        },
        "landscape_title": landscape[0]["title"],
        "landscape_subtitle": landscape[0]["subtitle"],
        "vertical_title": vertical[0]["title"],
        "vertical_subtitle": vertical[0]["subtitle"],
        "cover_title": landscape[0]["title"],
        "cover_subtitle": landscape[0]["subtitle"],
        "corrections": [],
        "risk_notes": ["价格预测可能不兑现"],
        "editor_notes": "",
        "evidence_map": [
            {
                "script_paragraph_index": 1,
                "needs_pdf_evidence": True,
                "claim_ids": ["C001"],
                "evidence_items": [{"claim_ids": ["C001"], **copy.deepcopy(evidence)}],
            },
            {
                "script_paragraph_index": 2,
                "needs_pdf_evidence": True,
                "claim_ids": ["C001"],
                "evidence_items": [{"claim_ids": ["C001"], **copy.deepcopy(evidence)}],
            },
        ],
        "quality_control": {
            "script_char_count_without_whitespace": len("".join(script.split())),
            "script_paragraph_count": 2,
            "fact_claim_count": 1,
            "claims_with_pdf_evidence_count": 1,
            "unsupported_claim_ids": [],
            "unknown_claim_ids_in_paragraph_map": [],
            "unknown_claim_ids_in_titles": [],
            "unmapped_script_paragraphs": [],
            "title_consistency_passed": True,
            "evidence_page_numbers_verified": True,
            "prohibited_terms_found": [],
            "output_ready": True,
            "blocking_reasons": [],
        },
    }
    return {
        "schema_version": "report_audio_gem_v2",
        "language": "zh-CN",
        "output_mode": "separate",
        "reports": [report],
        "batch_quality_control": {
            "report_count": 1,
            "all_reports_output_ready": True,
            "blocking_reasons": [],
        },
    }


class ReportAudioGemV2Test(unittest.TestCase):
    def validate(self, payload=None, **kwargs):
        return validate_gem_v2_response(
            json.dumps(payload or valid_payload(), ensure_ascii=False),
            minimum_script_chars=20,
            expected_pdf_filename=PDF_FILENAME,
            **kwargs,
        )

    def test_valid_payload_is_accepted_and_mapped_without_losing_v2_fields(self):
        validation = self.validate(actual_pdf_page_count=12)

        self.assertTrue(validation.valid, validation.errors)
        self.assertEqual(validation.error_codes, ())
        structured = to_report_audio_structured_result(validation)
        self.assertEqual(structured["schema_version"], "report_audio_gem_v2")
        self.assertEqual(structured["provider"], "gemini_gem")
        self.assertEqual(structured["fact_ledger"][0]["claim_id"], "C001")
        self.assertEqual(structured["title_candidates"]["landscape"][0]["candidate_id"], "L_TITLE_1")
        self.assertEqual(structured["cover_title"], structured["landscape_title"])

    def test_fenced_json_and_surrounding_prose_are_tolerated(self):
        raw = "分析完成。\n```json\n" + json.dumps(valid_payload(), ensure_ascii=False) + "\n```\n请查收。"

        validation = validate_gem_v2_response(
            raw,
            minimum_script_chars=20,
            expected_pdf_filename=PDF_FILENAME,
        )

        self.assertTrue(validation.valid, validation.errors)

    def test_invalid_schema_and_short_script_are_rejected(self):
        payload = valid_payload()
        payload["schema_version"] = "report_audio_gem_v1"
        payload["reports"][0]["final_script_for_tts"] = "太短"

        validation = self.validate(payload)

        self.assertIn("schema_version", validation.error_codes)
        self.assertIn("script_too_short", validation.error_codes)

    def test_paragraph_and_evidence_indexes_must_be_contiguous(self):
        payload = valid_payload()
        payload["reports"][0]["paragraph_map"][1]["script_paragraph_index"] = 3
        payload["reports"][0]["evidence_map"][1]["script_paragraph_index"] = 4

        validation = self.validate(payload)

        self.assertIn("paragraph_map_indexes", validation.error_codes)
        self.assertIn("evidence_map_indexes", validation.error_codes)

    def test_unknown_claim_references_are_rejected_everywhere(self):
        payload = valid_payload()
        payload["reports"][0]["paragraph_map"][0]["claim_ids"] = ["C999"]
        payload["reports"][0]["title_candidates"]["content"][0]["claim_ids"] = ["C998"]
        payload["reports"][0]["evidence_map"][0]["evidence_items"][0]["claim_ids"] = ["C997"]

        validation = self.validate(payload)

        self.assertIn("unknown_paragraph_claim_ids", validation.error_codes)
        self.assertIn("unknown_title_claim_ids", validation.error_codes)
        self.assertIn("unknown_evidence_claim_ids", validation.error_codes)

    def test_selected_candidate_and_compatibility_fields_must_match(self):
        payload = valid_payload()
        report = payload["reports"][0]
        report["selected_titles"]["landscape_candidate_id"] = "L_TITLE_999"
        report["title"] = "不一致的内容标题"
        report["cover_title"] = "不一致的封面标题"

        validation = self.validate(payload)

        self.assertIn("selected_landscape_candidate", validation.error_codes)
        self.assertIn("content_title_mismatch", validation.error_codes)
        self.assertIn("cover_title_mismatch", validation.error_codes)

    def test_candidate_arrays_require_three_real_candidates(self):
        payload = valid_payload()
        payload["reports"][0]["title_candidates"]["vertical"] = payload["reports"][0]["title_candidates"]["vertical"][:1]

        validation = self.validate(payload)

        self.assertIn("vertical_candidate_count", validation.error_codes)

    def test_external_information_is_rejected_by_default(self):
        payload = valid_payload()
        boundary = payload["reports"][0]["material_boundary"]
        boundary["external_information_used"] = True
        boundary["external_sources"] = ["新闻网站"]

        validation = self.validate(payload)

        self.assertIn("external_information_used", validation.error_codes)

    def test_evidence_page_must_be_inside_actual_pdf(self):
        payload = valid_payload()
        payload["reports"][0]["fact_ledger"][0]["evidence"][0]["pdf_page"] = 13

        validation = self.validate(payload, actual_pdf_page_count=12)

        self.assertIn("evidence_page_out_of_range", validation.error_codes)

    def test_output_ready_and_blocking_reasons_are_enforced(self):
        payload = valid_payload()
        payload["reports"][0]["quality_control"]["output_ready"] = False
        payload["reports"][0]["quality_control"]["blocking_reasons"] = ["关键页无法定位"]

        validation = self.validate(payload)

        self.assertIn("output_not_ready", validation.error_codes)
        self.assertIn("blocking_reasons", validation.error_codes)

    def test_minimal_trigger_contains_only_runtime_contract(self):
        prompt = build_gem_v2_trigger(
            PDF_FILENAME,
            minimum_script_chars=1500,
            focus="重点解释供需验证指标",
        )

        self.assertLess(len(prompt), 900)
        self.assertIn("report_audio_gem_v2", prompt)
        self.assertIn(PDF_FILENAME, prompt)
        self.assertIn("1500", prompt)
        self.assertIn("重点解释供需验证指标", prompt)
        self.assertNotIn("首席内容主笔", prompt)
        self.assertNotIn("角色", prompt)

    def test_mode_defaults_to_shadow_and_rejects_unknown_values(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_gem_v2_mode(), "shadow")
        self.assertEqual(resolve_gem_v2_mode("DIRECT"), "direct")
        with self.assertRaises(ValueError):
            resolve_gem_v2_mode("experimental")

    def test_repair_prompt_requests_one_complete_replacement_object(self):
        validation = self.validate({"schema_version": "wrong"})

        prompt = build_gem_v2_repair_prompt(validation.errors)

        self.assertIn("完整替换", prompt)
        self.assertIn("一个顶层 JSON 对象", prompt)
        self.assertIn("schema_version", prompt)
        self.assertIn("不是局部补丁", prompt)


if __name__ == "__main__":
    unittest.main()
