import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tasks import report_stick_figure_assets as image_task  # noqa: E402


class ReportStickFigureAssetSessionBoundaryTest(unittest.TestCase):
    def test_browser_progress_writes_are_coalesced_but_preserve_final_milestone(self):
        self.assertFalse(image_task._should_persist_progress(3, 15))
        self.assertTrue(image_task._should_persist_progress(3, 24))
        self.assertFalse(image_task._should_persist_progress(24, 32))
        self.assertTrue(image_task._should_persist_progress(24, 65))
        self.assertTrue(image_task._should_persist_progress(65, 96))

    def test_external_image_work_uses_detached_snapshots(self):
        text = (BACKEND_DIR / "tasks" / "report_stick_figure_assets.py").read_text(
            encoding="utf-8"
        )
        body = text.split("def _generate_scene_image(", 1)[1].split(
            "\ndef _update_progress(",
            1,
        )[0]
        browser_boundary = body.split(
            '_update_progress(task_id, asset_id, "prepare_browser", 3)',
            1,
        )[1]
        progress_body = text.split("def _update_progress(", 1)[1].split(
            "\ndef _should_persist_progress(",
            1,
        )[0]

        self.assertIn("image_target_url = str(", body)
        self.assertNotIn("scheme_output.", browser_boundary)
        self.assertIn("target_url=image_target_url", browser_boundary)
        self.assertIn("db.session.commit()", progress_body)
        self.assertIn("db.session.remove()", progress_body)

    def test_evidence_selection_returns_render_dtos_not_orm_entities(self):
        text = (BACKEND_DIR / "services" / "report_pdf_evidence.py").read_text(
            encoding="utf-8"
        )
        body = text.split("def evidence_visual_for_segment(", 1)[1].split(
            "\ndef render_claim_screenshot(",
            1,
        )[0]

        self.assertIn("EvidenceOutputRenderSnapshot(", body)
        self.assertIn("EvidenceClaimRenderSnapshot(", body)
        self.assertNotIn(
            "db.session.query(ReportPdfEvidenceOutput, ReportPdfEvidenceClaim)",
            body,
        )


if __name__ == "__main__":
    unittest.main()
