import importlib.util
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "diagnostics"
    / "daily_db_runtime_snapshot.py"
)


def load_snapshot_module():
    spec = importlib.util.spec_from_file_location("daily_db_runtime_snapshot", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DailyDbRuntimeSnapshotTest(unittest.TestCase):
    def test_local_audit_window_is_converted_to_utc_naive_database_bounds(self):
        snapshot = load_snapshot_module()

        self.assertEqual(
            snapshot.utc_naive_database_boundary(
                "2026-10-16 06:00:00",
                timezone_name="Asia/Shanghai",
            ),
            datetime(2026, 10, 15, 22, 0, 0),
        )
        self.assertEqual(
            snapshot.utc_naive_database_boundary(
                "2026-10-17T06:00:00",
                timezone_name="Asia/Shanghai",
            ),
            datetime(2026, 10, 16, 22, 0, 0),
        )

    def test_task_root_summary_covers_non_publisher_features(self):
        snapshot = load_snapshot_module()

        summary = snapshot.summarize_task_roots(
            [
                {
                    "feature": "content_pdf_clean",
                    "old_root": True,
                    "current_root": False,
                },
                {
                    "feature": "content_pdf_clean",
                    "old_root": False,
                    "current_root": True,
                },
                {
                    "feature": "zsxq_clean_output_publish",
                    "old_root": True,
                    "current_root": False,
                },
            ]
        )

        self.assertEqual(
            summary,
            [
                {
                    "feature": "content_pdf_clean",
                    "total": 2,
                    "old_root": 1,
                    "current_root": 1,
                    "mixed_root_reference": 0,
                    "unclassified": 0,
                },
                {
                    "feature": "zsxq_clean_output_publish",
                    "total": 1,
                    "old_root": 1,
                    "current_root": 0,
                    "mixed_root_reference": 0,
                    "unclassified": 0,
                },
            ],
        )

    def test_task_root_summary_does_not_count_mixed_historical_reference_as_old_actor(self):
        snapshot = load_snapshot_module()

        summary = snapshot.summarize_task_roots(
            [
                {
                    "feature": "content_audio_clean",
                    "old_root": True,
                    "current_root": True,
                },
            ]
        )

        self.assertEqual(
            summary,
            [
                {
                    "feature": "content_audio_clean",
                    "total": 1,
                    "old_root": 0,
                    "current_root": 0,
                    "mixed_root_reference": 1,
                    "unclassified": 0,
                },
            ],
        )

    def test_publisher_summary_handles_jsonb_values_without_database_json_functions(self):
        snapshot = load_snapshot_module()
        summary = snapshot.summarize_publisher_rows(
            [
                {
                    "status": "completed",
                    "duration_s": 10.0,
                    "old_root": True,
                    "repair_reason": "pending_message_not_active_but_queue_visible",
                    "repair_history": [{"current_celery_id": "new-1"}],
                },
                {
                    "status": "failed",
                    "duration_s": 30.0,
                    "old_root": False,
                    "repair_reason": None,
                    "repair_history": None,
                },
            ]
        )

        self.assertEqual(
            summary,
            {
                "total": 2,
                "completed": 1,
                "failed": 1,
                "canceled": 0,
                "old_root": 1,
                "old_reason": 1,
                "replacement_history": 1,
                "processing_orphan_closed": 0,
                "processing_orphan_actor_missing": 0,
                "material_unavailable": 0,
                "material_unavailable_actor_missing": 0,
                "material_unavailable_local_paths_exist": 0,
                "upload_video_unavailable": 0,
                "upload_video_unavailable_actor_missing": 0,
                "upload_video_unavailable_local_path_existed_before_close": 0,
                "foreign_actor_upload_evidence": 0,
                "foreign_actor_evidence": 0,
                "total_s": 40.0,
                "p50_s": 20.0,
                "p95_s": 29.0,
                "max_s": 30.0,
            },
        )

    def test_publisher_summary_flags_missing_actor_when_local_materials_exist(self):
        snapshot = load_snapshot_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "audio.mp3"
            second = Path(temp_dir) / "background.jpg"
            first.write_bytes(b"audio")
            second.write_bytes(b"image")

            summary = snapshot.summarize_publisher_rows(
                [
                    {
                        "status": "failed",
                        "duration_s": 12.0,
                        "old_root": False,
                        "repair_reason": "required_material_unavailable",
                        "repair_actor": None,
                        "unavailable_materials": [str(first), str(second)],
                    },
                    {
                        "status": "failed",
                        "duration_s": 8.0,
                        "old_root": False,
                        "repair_reason": "required_material_unavailable",
                        "repair_actor": {"hostname": "known-worker"},
                        "unavailable_materials": [str(first)],
                    },
                ]
            )

        self.assertEqual(2, summary["material_unavailable"])
        self.assertEqual(1, summary["material_unavailable_actor_missing"])
        self.assertEqual(2, summary["material_unavailable_local_paths_exist"])
        self.assertEqual(1, summary["foreign_actor_evidence"])

    def test_publisher_summary_flags_missing_upload_actor_when_video_predates_failure(self):
        snapshot = load_snapshot_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "already-rendered.mp4"
            video.write_bytes(b"video")
            completed_at = datetime.utcnow()

            summary = snapshot.summarize_publisher_rows(
                [
                    {
                        "status": "failed",
                        "duration_s": 15.0,
                        "old_root": False,
                        "error": "无法补发 B站：找不到已生成的视频文件",
                        "upload_failure_actor": None,
                        "upload_video_path": str(video),
                        "completed_at": completed_at,
                    },
                    {
                        "status": "failed",
                        "duration_s": 10.0,
                        "old_root": False,
                        "error": "无法补发 B站：找不到已生成的视频文件",
                        "upload_failure_actor": {"hostname": "known-worker"},
                        "upload_video_path": str(video),
                        "completed_at": completed_at,
                    },
                ]
            )

        self.assertEqual(2, summary["upload_video_unavailable"])
        self.assertEqual(1, summary["upload_video_unavailable_actor_missing"])
        self.assertEqual(
            2,
            summary["upload_video_unavailable_local_path_existed_before_close"],
        )
        self.assertEqual(1, summary["foreign_actor_upload_evidence"])
        self.assertEqual(1, summary["foreign_actor_evidence"])

    def test_publisher_summary_distinguishes_processing_orphan_actor_identity(self):
        snapshot = load_snapshot_module()

        summary = snapshot.summarize_publisher_rows(
            [
                {
                    "status": "failed",
                    "current_step": "orphaned_processing",
                    "duration_s": 20.0,
                    "old_root": True,
                    "processing_repair_reason": "upload_step_not_requeued",
                    "processing_repair_actor": {
                        "hostname": "Mac-mini-3.local",
                        "deployment_root": "/current/root",
                    },
                },
                {
                    "status": "failed",
                    "current_step": "orphaned_processing",
                    "duration_s": 30.0,
                    "old_root": True,
                    "processing_repair_reason": "upload_step_not_requeued",
                    "processing_repair_actor": None,
                },
                {
                    "status": "completed",
                    "current_step": "done",
                    "duration_s": 10.0,
                    "old_root": True,
                    "processing_repair_reason": "required_material_unavailable",
                    "processing_repair_actor": {
                        "hostname": "Mac-mini-3.local",
                        "deployment_root": "/current/root",
                    },
                },
            ]
        )

        self.assertEqual(2, summary["processing_orphan_closed"])
        self.assertEqual(1, summary["processing_orphan_actor_missing"])
        self.assertEqual(0, summary["foreign_actor_evidence"])


if __name__ == "__main__":
    unittest.main()
