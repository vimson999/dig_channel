import tempfile
import unittest
from pathlib import Path

import fitz

from services.report_video_director_test_runner import run_isolated_director_test


class ReportVideoDirectorEndToEndTest(unittest.TestCase):
    def test_isolated_director_run_generates_real_previews_and_reel(self):
        with tempfile.TemporaryDirectory(prefix="report_director_e2e_") as tmpdir:
            root = Path(tmpdir)
            pdf_path = root / "controlled-test-report.pdf"
            self._write_controlled_pdf(pdf_path)
            material, evidence_map = self._input_payload()

            result = run_isolated_director_test(
                material,
                evidence_map,
                pdf_path=str(pdf_path),
                output_root=str(root / "output"),
                test_task_id="test:report-director-e2e",
            )

            self.assertEqual(result["status"], "completed", result)
            self.assertEqual(result["execution_mode"], "test")
            self.assertTrue(result["full_render_gate"]["allowed"])
            self.assertEqual(result["full_render_gate"]["status"], "test_auto_authorized")
            self.assertFalse(result["publish_attempted"])
            self.assertFalse(result["production_task_mutated"])
            self.assertEqual(
                result["source_trace"]["source_type"],
                "synthetic_test_fixture",
            )
            self.assertEqual(
                result["source_trace"]["material_task_id"],
                "test-material-001",
            )
            self.assertEqual(
                result["source_trace"]["pdf_filename"],
                "controlled-test-report.pdf",
            )
            self.assertEqual(result["source_trace"]["evidence_pages"], [1, 2, 3, 4])
            self.assertEqual(len(result["source_trace"]["pdf_sha256"]), 64)
            self.assertGreaterEqual(len(result["preview_artifacts"]["keyframes"]), 1)
            self.assertGreaterEqual(len(result["preview_artifacts"]["preview_clips"]), 2)
            self.assertLessEqual(len(result["preview_artifacts"]["preview_clips"]), 3)
            for artifact in (
                result["preview_artifacts"]["keyframes"]
                + result["preview_artifacts"]["preview_clips"]
            ):
                path = Path(artifact["file_path"])
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 0)
                attribution = artifact["audience_source_attribution"]
                if attribution:
                    self.assertTrue(attribution.startswith("来源："))
                    self.assertNotRegex(
                        attribution.lower(),
                        r"evidence|editor|director|preview|detail|task|approval",
                    )
            reel = Path(result["test_reel"]["file_path"])
            self.assertTrue(reel.is_file())
            self.assertGreater(result["test_reel"]["duration_seconds"], 6.0)
            self.assertTrue(Path(result["manifest_path"]).is_file())

    def test_rejects_output_directory_without_test_isolation_marker(self):
        material, evidence_map = self._input_payload()
        with tempfile.TemporaryDirectory(prefix="report_director_pdf_") as tmpdir:
            pdf_path = Path(tmpdir) / "controlled.pdf"
            self._write_controlled_pdf(pdf_path)
            with self.assertRaisesRegex(ValueError, "测试输出目录必须"):
                run_isolated_director_test(
                    material,
                    evidence_map,
                    pdf_path=str(pdf_path),
                    output_root=str(Path.home() / "report-director-output"),
                    test_task_id="test:unsafe-output",
                )

    @staticmethod
    def _write_controlled_pdf(path):
        document = fitz.open()
        pages = [
            (
                "Controlled research report - executive conclusion",
                "Compute infrastructure demand remains on an upward trend.",
            ),
            (
                "Order evidence",
                "First-quarter server orders increased by 30 percent year over year.",
            ),
            (
                "Supply and demand chart evidence",
                "Supply expansion remains slower than demand growth; pricing stays resilient.",
            ),
            (
                "Risk disclosure",
                "Export restrictions and capital expenditure volatility remain key risks.",
            ),
        ]
        for index, (title, body) in enumerate(pages, start=1):
            page = document.new_page(width=595, height=842)
            page.insert_text((54, 72), title, fontsize=18, fontname="helv")
            page.insert_textbox(
                fitz.Rect(54, 112, 540, 260),
                body,
                fontsize=12,
                fontname="helv",
            )
            page.draw_rect(
                fitz.Rect(54, 300, 540, 540),
                color=(0.1, 0.3, 0.6),
                fill=(0.93, 0.96, 1.0),
                width=2,
            )
            page.insert_text(
                (72, 335),
                f"Evidence panel {index}",
                fontsize=15,
                fontname="helv",
            )
            page.insert_text(
                (500, 800),
                str(index),
                fontsize=10,
                fontname="helv",
            )
        document.save(path)
        document.close()

    @staticmethod
    def _input_payload():
        script = (
            "Compute infrastructure demand remains on an upward trend. "
            "First-quarter server orders increased by 30 percent year over year. "
            "Supply expansion remains slower than demand growth, so pricing stays resilient. "
            "Export restrictions and capital expenditure volatility remain key risks."
        )
        material = {
            "job_id": "test-material-001",
            "source_type": "synthetic_test_fixture",
            "feature": "report_script_material_workflow",
            "title": "Controlled compute infrastructure report",
            "pdf": {
                "attachment_id": "test-pdf-001",
                "filename": "controlled-test-report.pdf",
            },
            "structured_result": {
                "title": "Controlled compute infrastructure report",
                "landscape_title": "Demand and orders remain resilient",
                "vertical_title": "Can the 30 percent order growth last?",
                "final_script_for_tts": script,
            },
        }
        evidence_map = [
            {
                "evidence_id": f"ev-{index:03d}",
                "page": index,
                "quote": excerpt,
                "script_excerpt": excerpt,
                "kind": kind,
                "confidence": 1.0,
            }
            for index, (excerpt, kind) in enumerate(
                [
                    (
                        "Compute infrastructure demand remains on an upward trend.",
                        "pdf_quote",
                    ),
                    (
                        "First-quarter server orders increased by 30 percent year over year.",
                        "pdf_table",
                    ),
                    (
                        "Supply expansion remains slower than demand growth, so pricing stays resilient.",
                        "pdf_chart",
                    ),
                    (
                        "Export restrictions and capital expenditure volatility remain key risks.",
                        "pdf_quote",
                    ),
                ],
                start=1,
            )
        ]
        return material, evidence_map


if __name__ == "__main__":
    unittest.main()
