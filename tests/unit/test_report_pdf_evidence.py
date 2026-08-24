import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import (  # noqa: E402
    ReportPdfEvidenceClaim,
    ReportVisualPlanOutput,
    ReportVisualPlanSegment,
    ReportVisualPlanSegmentEvidence,
    db,
)
from models.task import PublicationTask  # noqa: E402
from services.report_audio_workflow import (  # noqa: E402
    DEFAULT_REVIEW_PROMPT_BASE,
    PDF_EVIDENCE_REVIEW_CONTRACT,
    ReportAudioWorkflowError,
    assert_final_tts_text_clean,
    ensure_pdf_evidence_review_contract,
    review_prompt_for_pdf_evidence,
    script_regeneration_prompts,
    split_tts_text,
)
from services.report_pdf_evidence import (  # noqa: E402
    SCHEMA_VERSION,
    bulk_set_claim_visuals,
    build_and_store_evidence,
    extract_evidence_candidates,
    extract_pdf_pages,
    link_storyboard_evidence,
    render_claim_screenshot,
    set_visuals_enabled,
    update_claim,
    verify_candidate,
)
from services import report_pdf_evidence as evidence_service  # noqa: E402


class ReportPdfEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        self.tempdir.cleanup()

    def pdf(self, pages):
        import fitz

        path = Path(self.tempdir.name) / "source.pdf"
        document = fitz.open()
        for text in pages:
            page = document.new_page()
            page.insert_text((72, 72), text, fontsize=12)
        document.save(path)
        document.close()
        return path

    def marked_pdf(self, path, *, body, watermark=None):
        import fitz

        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), body, fontsize=12)
        if watermark:
            page.insert_text(
                (180, 300),
                watermark,
                fontsize=42,
                color=(1, 0, 0),
            )
        document.save(path)
        document.close()
        return path

    def rasterized_pdf(self, source, target):
        import fitz

        output = fitz.open()
        with fitz.open(source) as document:
            for source_page in document:
                page = output.new_page(
                    width=source_page.rect.width,
                    height=source_page.rect.height,
                )
                pixmap = source_page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                page.insert_image(page.rect, stream=pixmap.tobytes("png"))
        output.save(target)
        output.close()
        return target

    def source_task(self, script):
        task = PublicationTask(
            id="source-task",
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": "report_pdf_audio_workflow"},
            result={"structured_result": {"final_script_for_tts": script}},
        )
        task.user_id = "user-1"
        db.session.add(task)
        db.session.commit()
        return task

    def test_parser_accepts_unfenced_multiple_blocks_and_deduplicates(self):
        claim = {
            "claim_text": "收入同比增长20%",
            "script_quote": "收入同比增长20%",
            "pdf_page": 2,
            "pdf_quote": "收入同比增长20%",
        }
        envelope = {"schema_version": SCHEMA_VERSION, "claims": [claim]}
        raw = (
            "普通说明 {bad json} "
            f"PDF_EVIDENCE_V1_JSON: {json.dumps(envelope, ensure_ascii=False)} "
            f"重复块 {json.dumps(envelope, ensure_ascii=False)}"
        )

        claims = extract_evidence_candidates(raw)

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["pdf_page"], 2)

    def test_parser_survives_oversized_text_and_late_candidate(self):
        raw = ("前置分析" * 20000) + json.dumps({
            "evidence_map": [{
                "claim": "目标价上调",
                "script": "目标价上调",
                "page": 8,
                "quote": "目标价上调至100元",
            }],
        }, ensure_ascii=False)

        claims = extract_evidence_candidates(raw)

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["claim_text"], "目标价上调")

    def test_server_appends_evidence_contract_to_legacy_review_prompt(self):
        legacy = "请复核事实并输出最终文案。"

        merged = ensure_pdf_evidence_review_contract(legacy)
        repeated = ensure_pdf_evidence_review_contract(merged)

        self.assertTrue(merged.startswith(legacy))
        self.assertIn("PDF_EVIDENCE_V1_JSON", merged)
        self.assertIn('"schema_version":"report_pdf_evidence_v1"', merged)
        self.assertEqual(merged.count(PDF_EVIDENCE_REVIEW_CONTRACT), 1)
        self.assertEqual(repeated, merged)

    def test_optional_evidence_prompt_defaults_off_without_losing_review_instructions(self):
        stale_prompt = ensure_pdf_evidence_review_contract("请复核事实并输出最终文案。")

        disabled = review_prompt_for_pdf_evidence(stale_prompt, enabled=False)
        enabled = review_prompt_for_pdf_evidence(DEFAULT_REVIEW_PROMPT_BASE, enabled=True)

        self.assertEqual(disabled, "请复核事实并输出最终文案。")
        self.assertNotIn("PDF_EVIDENCE_V1_JSON", disabled)
        self.assertEqual(enabled.count(PDF_EVIDENCE_REVIEW_CONTRACT), 1)

    def test_optional_evidence_prompt_strips_cached_contract_whitespace_variant(self):
        cached_contract = PDF_EVIDENCE_REVIEW_CONTRACT.replace("必须包含claim_text", "必须包含 claim_text")
        stale_prompt = f"请复核事实并输出最终文案。{cached_contract}"

        disabled = review_prompt_for_pdf_evidence(stale_prompt, enabled=False)
        reenabled = review_prompt_for_pdf_evidence(stale_prompt, enabled=True)

        self.assertEqual(disabled, "请复核事实并输出最终文案。")
        self.assertNotIn("PDF_EVIDENCE_V1_JSON", disabled)
        self.assertEqual(reenabled.count("PDF_EVIDENCE_V1_JSON"), 1)
        self.assertEqual(reenabled.count(PDF_EVIDENCE_REVIEW_CONTRACT), 1)

    def test_script_regeneration_prompt_requires_full_1500_char_body_without_evidence(self):
        prompts = script_regeneration_prompts(
            "解析 PDF。",
            ensure_pdf_evidence_review_contract("复核事实。"),
            minimum_chars=1200,
        )

        self.assertIn("全新重写", prompts["prompt"])
        self.assertIn("不少于 1500 字", prompts["review_prompt"])
        self.assertNotIn("PDF_EVIDENCE_V1_JSON", prompts["review_prompt"])

    def test_script_regeneration_strips_cached_contract_variant(self):
        cached_contract = PDF_EVIDENCE_REVIEW_CONTRACT.replace("必须包含claim_text", "必须包含 claim_text")

        prompts = script_regeneration_prompts(
            "解析 PDF。",
            f"复核事实。{cached_contract}",
            minimum_chars=1500,
        )

        self.assertNotIn("PDF_EVIDENCE_V1_JSON", prompts["review_prompt"])
        self.assertIn("复核事实。", prompts["review_prompt"])

    def test_parser_maps_current_gem_v6_evidence_map_and_verifies_locally(self):
        final_script = "收入增长20%。利润率提升到15%。风险来自需求回落。"
        path = self.pdf([
            "Revenue increased 20%. Margin improved to 15%.",
            "Demand slowdown is the primary risk.",
        ])
        pages = extract_pdf_pages(path)
        raw = json.dumps({
            "evidence_map": [{
                "script_paragraph_index": 2,
                "needs_pdf_evidence": True,
                "evidence_type": "key_data",
                "pdf_pages": [1],
                "source_excerpt": "Margin improved to 15%.",
                "capture_instruction": "截取利润率数据",
                "confidence": "high",
            }, {
                "script_paragraph_index": 3,
                "needs_pdf_evidence": False,
                "evidence_type": "none",
                "pdf_pages": [],
                "source_excerpt": "",
            }],
        }, ensure_ascii=False)

        candidates = extract_evidence_candidates(raw)
        verified = verify_candidate(
            candidates[0],
            final_script,
            pages,
            sequence=1,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["script_paragraph_index"], 2)
        self.assertEqual(candidates[0]["pdf_page"], 1)
        self.assertEqual(verified["script_quote"], "利润率提升到15%。")
        self.assertEqual(verified["claim_text"], "利润率提升到15%。")
        self.assertEqual(verified["verification_status"], "verified")
        self.assertTrue(verified["use_as_visual"])
        self.assertEqual(verified["source_trace"], "gemini_v6_evidence_map:2")

    def test_parser_returns_no_candidates_for_plain_text_without_evidence(self):
        self.assertEqual(
            extract_evidence_candidates("这是完整正文，但没有任何证据 JSON。"),
            [],
        )

    def test_verifier_rejects_page_out_of_range_and_quote_mismatch(self):
        path = self.pdf(["第一页包含营业收入增长。"])
        pages = extract_pdf_pages(path)

        result = verify_candidate({
            "claim_text": "营业收入增长",
            "script_quote": "营业收入增长",
            "pdf_page": 9,
            "pdf_quote": "不存在的原文",
        }, "营业收入增长，因此盈利能力改善。", pages, sequence=1)

        self.assertEqual(result["verification_status"], "unverified")

    def test_verifier_tolerates_invalid_confidence(self):
        path = self.pdf(["Revenue increased."])
        pages = extract_pdf_pages(path)

        result = verify_candidate({
            "claim_text": "Revenue increased",
            "script_quote": "Revenue increased",
            "pdf_page": 1,
            "pdf_quote": "Revenue increased",
            "confidence": "not-a-number",
        }, "Revenue increased.", pages, sequence=1)

        self.assertEqual(result["verification_status"], "verified")
        self.assertGreaterEqual(result["confidence"], 0.0)

    def test_build_edit_visual_enable_and_segment_link(self):
        script = "Company revenue increased twenty percent, mainly from cloud."
        self.source_task(script)
        path = self.pdf(["Company revenue increased twenty percent, mainly from cloud."])
        raw = json.dumps({
            "claims": [{
                "claim_text": "Revenue increased twenty percent",
                "script_quote": "Company revenue increased twenty percent",
                "pdf_page": 1,
                "pdf_quote": "Company revenue increased twenty percent",
                "claim_type": "data",
                "evidence_kind": "chart",
                "confidence": 0.95,
                "use_as_visual": True,
            }],
        }, ensure_ascii=False)
        output = build_and_store_evidence(
            source_task_id="source-task",
            pdf_path=str(path),
            final_script=script,
            raw_gemini_text=raw,
            pdf_filename=path.name,
        )
        claim = ReportPdfEvidenceClaim.query.filter_by(output_id=output.id).one()
        self.assertEqual(output.status, "ready")
        self.assertEqual(claim.verification_status, "verified")
        cached = build_and_store_evidence(
            source_task_id="source-task",
            pdf_path=str(path),
            final_script=script,
            raw_gemini_text=raw,
            pdf_filename=path.name,
        )
        self.assertEqual(cached.id, output.id)
        self.assertEqual(
            ReportPdfEvidenceClaim.query.filter_by(output_id=output.id).count(),
            1,
        )

        storyboard = PublicationTask(
            id="storyboard-task",
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": "report_visual_plan"},
            result={},
        )
        db.session.add(storyboard)
        db.session.add(ReportVisualPlanOutput(
            task_id=storyboard.id,
            source_task_id="source-task",
            schema_version="storyboard-v1",
            source_script_hash="a" * 64,
            source_script_chars=len(script),
            covered_script_chars=len(script),
            segment_count=1,
            visual_scheme_key="stick_figure",
            visual_scheme_name="白板火柴人",
            gemini_url="https://example.invalid",
        ))
        segment = ReportVisualPlanSegment(
            task_id=storyboard.id,
            sequence=1,
            scene_title="增长",
            script_text=script,
            visual_prompt="表现云业务增长",
        )
        db.session.add(segment)
        db.session.commit()

        update_claim(claim, {
            "crop_x": 0.0,
            "crop_y": 0.0,
            "crop_width": 1.0,
            "crop_height": 1.0,
            "enabled": True,
            "use_as_visual": True,
        })
        set_visuals_enabled(output, True)
        links = link_storyboard_evidence(storyboard.id)

        link = ReportVisualPlanSegmentEvidence.query.filter_by(segment_id=segment.id).one()
        self.assertEqual(links, 1)
        self.assertTrue(link.use_as_visual)
        self.assertIsNone(claim.screenshot_path)
        screenshot_path = render_claim_screenshot(output, claim)
        self.assertTrue(Path(screenshot_path).is_file())

    def test_manual_confirmation_and_bulk_visual_selection_are_lazy(self):
        script = "Revenue increased twenty percent."
        self.source_task(script)
        path = self.pdf(["Revenue increased twenty percent."])
        raw = json.dumps({"claims": [{
            "claim_text": "Revenue increased",
            "script_quote": "Revenue increased",
            "pdf_page": 1,
            "pdf_quote": "An imprecise excerpt",
            "use_as_visual": False,
        }]})
        output = build_and_store_evidence(
            source_task_id="source-task",
            pdf_path=str(path),
            final_script=script,
            raw_gemini_text=raw,
            pdf_filename=path.name,
        )
        claim = ReportPdfEvidenceClaim.query.filter_by(output_id=output.id).one()
        self.assertNotEqual(claim.verification_status, "verified")

        changed = bulk_set_claim_visuals(output, enabled=True)

        self.assertEqual(changed, 1)
        self.assertTrue(output.visuals_enabled)
        self.assertEqual(claim.verification_status, "manual_verified")
        self.assertTrue(claim.use_as_visual)
        self.assertTrue(claim.user_edited)
        self.assertIsNone(claim.screenshot_path)

        update_claim(claim, {
            "enabled": True,
            "use_as_visual": True,
            "manual_confirmed": True,
        })
        self.assertEqual(claim.verification_status, "manual_verified")
        self.assertTrue(claim.use_as_visual)
        self.assertIsNone(claim.screenshot_path)

        bulk_set_claim_visuals(output, enabled=False)
        self.assertFalse(output.visuals_enabled)
        self.assertFalse(claim.use_as_visual)

    def test_new_output_is_complete_before_clean_pdf_lookup_can_autoflush(self):
        script = "Company revenue increased twenty percent."
        self.source_task(script)
        path = self.pdf([script])
        raw = json.dumps({"claims": [{
            "claim_text": script,
            "script_quote": script,
            "pdf_page": 1,
            "pdf_quote": script,
        }]})

        def query_during_clean_pdf_lookup(**_kwargs):
            pending_outputs = [
                item for item in db.session.new
                if item.__class__.__name__ == "ReportPdfEvidenceOutput"
            ]
            self.assertEqual([], pending_outputs)
            db.session.query(PublicationTask.id).first()
            return None

        with patch.object(
            evidence_service,
            "_cleaned_pdf_path_from_workflow",
            side_effect=query_during_clean_pdf_lookup,
        ):
            output = build_and_store_evidence(
                source_task_id="source-task",
                pdf_path=str(path),
                final_script=script,
                raw_gemini_text=raw,
                pdf_attachment_id="attachment-1",
            )

        self.assertEqual(64, len(output.final_script_hash))
        self.assertEqual(64, len(output.raw_response_hash))
        self.assertEqual("local_pdf_verify_v2", output.parser_version)

    def test_evidence_screenshot_prefers_verified_clean_pdf(self):
        script = "Company revenue increased twenty percent."
        self.source_task(script)
        root = Path(self.tempdir.name)
        original = self.marked_pdf(
            root / "original.pdf",
            body=script,
            watermark="WATERMARK",
        )
        cleaned = self.marked_pdf(
            root / "cleaned.pdf",
            body=script,
        )
        raw = json.dumps({"claims": [{
            "claim_text": script,
            "script_quote": script,
            "pdf_page": 1,
            "pdf_quote": script,
            "use_as_visual": True,
        }]})

        with patch.object(
            evidence_service,
            "_cleaned_pdf_path_from_workflow",
            return_value=cleaned,
        ):
            output = build_and_store_evidence(
                source_task_id="source-task",
                pdf_path=str(original),
                final_script=script,
                raw_gemini_text=raw,
                pdf_attachment_id="attachment-1",
            )

        claim = ReportPdfEvidenceClaim.query.filter_by(output_id=output.id).one()
        screenshot = Path(render_claim_screenshot(output, claim))
        self.assertEqual("content_pdf_clean", output.visual_pdf_source)
        self.assertTrue(cleaned.samefile(output.visual_pdf_path))
        with Image.open(screenshot).convert("RGB") as image:
            red_pixels = sum(
                1
                for red, green, blue in image.getdata()
                if red > 180 and green < 100 and blue < 100
            )
        self.assertEqual(0, red_pixels)

    def test_clean_pdf_without_same_evidence_falls_back_to_source(self):
        script = "Company revenue increased twenty percent."
        self.source_task(script)
        root = Path(self.tempdir.name)
        original = self.marked_pdf(root / "original.pdf", body=script)
        cleaned = self.marked_pdf(root / "cleaned.pdf", body="Different page content.")
        raw = json.dumps({"claims": [{
            "claim_text": script,
            "script_quote": script,
            "pdf_page": 1,
            "pdf_quote": script,
        }]})

        with patch.object(
            evidence_service,
            "_cleaned_pdf_path_from_workflow",
            return_value=cleaned,
        ):
            output = build_and_store_evidence(
                source_task_id="source-task",
                pdf_path=str(original),
                final_script=script,
                raw_gemini_text=raw,
                pdf_attachment_id="attachment-1",
            )

        self.assertEqual("source_pdf", output.visual_pdf_source)
        self.assertTrue(original.samefile(output.visual_pdf_path))

    def test_rasterized_clean_pdf_uses_visual_page_alignment(self):
        script = "Company revenue increased twenty percent."
        self.source_task(script)
        root = Path(self.tempdir.name)
        original = self.marked_pdf(
            root / "original.pdf",
            body=script,
            watermark="WATERMARK",
        )
        watermark_free = self.marked_pdf(root / "watermark-free.pdf", body=script)
        cleaned = self.rasterized_pdf(watermark_free, root / "cleaned.pdf")
        raw = json.dumps({"claims": [{
            "claim_text": script,
            "script_quote": script,
            "pdf_page": 1,
            "pdf_quote": script,
        }]})

        with patch.object(
            evidence_service,
            "_cleaned_pdf_path_from_workflow",
            return_value=cleaned,
        ):
            output = build_and_store_evidence(
                source_task_id="source-task",
                pdf_path=str(original),
                final_script=script,
                raw_gemini_text=raw,
                pdf_attachment_id="attachment-1",
            )

        self.assertEqual("content_pdf_clean", output.visual_pdf_source)
        self.assertTrue(cleaned.samefile(output.visual_pdf_path))
        claim = ReportPdfEvidenceClaim.query.filter_by(output_id=output.id).one()
        screenshot = Path(render_claim_screenshot(output, claim))
        with Image.open(screenshot).convert("RGB") as image:
            red_pixels = sum(
                1
                for red, green, blue in image.getdata()
                if red > 180 and green < 100 and blue < 100
            )
        self.assertEqual(0, red_pixels)

    def test_rasterized_clean_pdf_accepts_manually_verified_claims(self):
        script = "Company revenue increased twenty percent."
        self.source_task(script)
        root = Path(self.tempdir.name)
        original = self.marked_pdf(
            root / "original.pdf",
            body=script,
            watermark="WATERMARK",
        )
        watermark_free = self.marked_pdf(root / "watermark-free.pdf", body=script)
        cleaned = self.rasterized_pdf(watermark_free, root / "cleaned.pdf")
        raw = json.dumps({"claims": [{
            "claim_text": script,
            "script_quote": script,
            "pdf_page": 1,
            "pdf_quote": "Imprecise human-confirmed excerpt.",
        }]})

        with patch.object(
            evidence_service,
            "_cleaned_pdf_path_from_workflow",
            return_value=None,
        ):
            output = build_and_store_evidence(
                source_task_id="source-task",
                pdf_path=str(original),
                final_script=script,
                raw_gemini_text=raw,
                pdf_attachment_id="attachment-1",
            )
        claim = ReportPdfEvidenceClaim.query.filter_by(output_id=output.id).one()
        update_claim(claim, {
            "enabled": True,
            "use_as_visual": True,
            "manual_confirmed": True,
        })

        with patch.object(
            evidence_service,
            "_cleaned_pdf_path_from_workflow",
            return_value=cleaned,
        ):
            changed = evidence_service._refresh_visual_pdf_binding(output)

        self.assertTrue(changed)
        self.assertEqual("manual_verified", claim.verification_status)
        self.assertEqual("content_pdf_clean", output.visual_pdf_source)
        self.assertTrue(cleaned.samefile(output.visual_pdf_path))

    def test_tts_terminal_gate_rejects_evidence_and_reference_pollution(self):
        rejected = (
            "正文后残留 https://example.com/source",
            "正文后残留 citation 12",
            '正文后残留 {"pdf_quote":"原文"}',
            "正文后残留 ```json",
            "正文后残留【12】",
        )
        for text in rejected:
            with self.subTest(text=text):
                with self.assertRaises(ReportAudioWorkflowError):
                    assert_final_tts_text_clean(text)
                with self.assertRaises(ReportAudioWorkflowError):
                    split_tts_text(text)

        self.assertEqual(split_tts_text("这是纯净的最终口播正文。"), ["这是纯净的最终口播正文。"])

    def test_storyboard_clause_can_link_to_longer_verified_claim(self):
        from services.report_pdf_evidence import _segment_claim_score

        claim = "全球流动性领先指标已经正式掉头向下，科技半导体的系统性调整窗口正在打开。"
        self.assertGreaterEqual(
            _segment_claim_score("科技半导体的系统性调整窗口正在打开。", claim),
            0.58,
        )
        self.assertLess(
            _segment_claim_score("窗口打开", claim),
            0.58,
        )


if __name__ == "__main__":
    unittest.main()
