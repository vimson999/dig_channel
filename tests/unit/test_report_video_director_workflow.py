import tempfile
import unittest
from pathlib import Path

import fitz

from services.report_video_director_workflow import (
    DirectorWorkflowError,
    attach_workflow_alignment,
    build_workflow_dispatch_bundle,
    create_workflow_state,
    record_workflow_human_review,
    record_workflow_previews,
)


class ReportVideoDirectorWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.pdf = root / "report.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 96), "Exhibit: Inventory declined to 35 days.")
        document.save(self.pdf)
        document.close()
        self.audio = root / "voice.wav"
        self.audio.write_bytes(b"RIFF" + b"\0" * 512)
        script = (
            "玻璃库存下降，供给冷修改变了短期平衡，价格出现连续回升。"
            "报告图表显示现金利润率改善，但产业链其他环节仍然偏弱。"
            "接下来需要验证库存、价格和终端需求，不应忽略风险边界。"
        )
        self.material = {
            "job_id": "source-task",
            "feature": "report_pdf_audio_workflow",
            "pdf": {
                "attachment_id": "pdf-asset",
                "filename": self.pdf.name,
                "path": str(self.pdf),
            },
            "audio_task_id": "source-task",
            "audio_path": str(self.audio),
            "structured_result": {
                "title": "玻璃盈利拐点",
                "final_script_for_tts": script,
            },
        }
        self.evidence = [
            {
                "evidence_id": f"ev-{index}",
                "segment_index": index,
                "page": 1,
                "kind": "table",
                "quote": "Exhibit: Inventory declined to 35 days.",
            }
            for index in range(1, 5)
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def _aligned(self, mode="production"):
        state = create_workflow_state(
            self.material,
            self.evidence,
            pdf_path=str(self.pdf),
            execution_mode=mode,
            workflow_task_id="workflow-001",
            target_segment_chars=60,
        )
        count = len(state["director_package"]["segments"])
        unit = 12 / count
        alignment = {
            "timeline": [
                {
                    "start_seconds": index * unit,
                    "end_seconds": (index + 1) * unit,
                    "duration_seconds": unit,
                    "confidence": 0.9,
                }
                for index in range(count)
            ]
        }
        return attach_workflow_alignment(
            state,
            alignment=alignment,
            audio_path=str(self.audio),
            audio_seconds=12,
        )

    @staticmethod
    def _artifacts(state):
        gate = state["director_package"]["preview_gate"]
        return {
            "keyframes": [
                {
                    "preview_id": item["preview_id"],
                    "status": "completed",
                    "artifact_id": f"kf:{item['preview_id']}",
                }
                for item in gate["keyframe_plan"]
            ],
            "preview_clips": [
                {
                    "preview_id": item["preview_id"],
                    "status": "completed",
                    "artifact_id": f"clip:{item['preview_id']}",
                }
                for item in gate["preview_clip_plan"]
            ],
        }

    def test_production_stays_blocked_until_hash_bound_human_review(self):
        state = self._aligned()
        state = record_workflow_previews(
            state,
            preview_artifacts=self._artifacts(state),
        )

        self.assertEqual(state["current_step"], "awaiting_human_review")
        self.assertFalse(state["full_render_gate"]["allowed"])
        with self.assertRaises(DirectorWorkflowError):
            build_workflow_dispatch_bundle(state)

        state = record_workflow_human_review(
            state,
            reviewer_id="reviewer-001",
            decision="approved",
        )
        self.assertEqual(state["current_step"], "full_render_authorized")
        self.assertTrue(state["full_render_gate"]["allowed"])
        dispatched = build_workflow_dispatch_bundle(state)
        self.assertEqual(dispatched["current_step"], "dispatch_ready")
        self.assertFalse(dispatched["dispatch_bundle"]["publish_allowed"])
        self.assertEqual(
            dispatched["dispatch_bundle"]["production_task_mutation_scope"],
            "new_child_tasks_only",
        )

    def test_modify_or_terminate_never_dispatches(self):
        for decision, step in (
            ("modify", "revision_required"),
            ("terminated", "terminated"),
        ):
            state = self._aligned()
            state = record_workflow_previews(
                state,
                preview_artifacts=self._artifacts(state),
            )
            state = record_workflow_human_review(
                state,
                reviewer_id="reviewer-001",
                decision=decision,
            )
            self.assertEqual(state["current_step"], step)
            with self.assertRaises(DirectorWorkflowError):
                build_workflow_dispatch_bundle(state)


if __name__ == "__main__":
    unittest.main()
