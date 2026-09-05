import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.zsxq_analysis_service import (  # noqa: E402
    ANALYSIS_VERSION,
    ZsxqAnalysisValidationError,
    analyze_source_bytes,
    build_analysis_run_detail,
    create_analysis_run,
    get_analysis_run,
    list_analysis_items,
    list_analysis_runs,
)
from models import User, db  # noqa: E402


def _payload(kind="pdf", items=None, summary=None):
    return {
        "schema": "zsxq-list-export/v1",
        "kind": kind,
        "exportedAt": "2026-09-02T02:31:01.385Z",
        "summary": summary or {"total": 999999, "downloaded": 999999},
        "items": items
        or [
            {
                "downloadCount": 12,
                "duration": 0,
                "fileId": "file-1",
                "fileSize": 1024,
                "groupName": "研究资料",
                "name": "AI大模型与数据中心-260901.pdf",
                "status": "done",
                "topicId": "topic-1",
                "uploadTime": "2026-09-01T12:00:00+08:00",
            },
            {
                "downloadCount": 3,
                "duration": 7200,
                "fileId": "file-2",
                "fileSize": 2048,
                "groupName": "研究资料",
                "name": "AI大模型产业链交流-260901.pdf",
                "status": "pending",
                "topicId": "topic-1",
                "uploadTime": "2026-09-01T13:00:00+08:00",
            },
        ],
    }


class ZsxqAnalysisServiceTest(unittest.TestCase):
    def test_recomputes_summary_and_builds_topic_and_keyword_projections(self):
        result = analyze_source_bytes(
            json.dumps(_payload()).encode("utf-8"),
            filename="pdf-export.json",
            expected_kind="pdf",
        )

        self.assertEqual(result.analysis_version, ANALYSIS_VERSION)
        self.assertEqual(result.kind, "pdf")
        self.assertEqual(result.summary["item_count"], 2)
        self.assertEqual(result.summary["done_count"], 1)
        self.assertEqual(result.summary["pending_count"], 1)
        self.assertEqual(result.summary["total_download_count"], 15)
        self.assertEqual(result.summary["total_file_size_bytes"], 3072)
        self.assertEqual(result.summary["unique_topic_count"], 1)
        self.assertEqual(
            result.summary["period_start"].isoformat(), "2026-09-01T12:00:00"
        )
        self.assertEqual(
            result.summary["period_end"].isoformat(), "2026-09-01T13:00:00"
        )
        self.assertEqual(result.topics[0]["topic_id"], "topic-1")
        self.assertEqual(result.topics[0]["item_count"], 2)
        self.assertEqual(result.topics[0]["total_download_count"], 15)
        self.assertIn("AI", {row["keyword"] for row in result.keywords})
        self.assertIn("大模型", {row["keyword"] for row in result.keywords})

    def test_client_summary_is_not_used_as_analysis_result(self):
        result = analyze_source_bytes(
            json.dumps(
                _payload(
                    summary={
                        "total": 1,
                        "downloaded": 0,
                        "totalDownloadCount": 0,
                    }
                )
            ).encode("utf-8"),
            filename="export.json",
        )

        self.assertEqual(result.summary["item_count"], 2)
        self.assertEqual(result.summary["total_download_count"], 15)

    def test_audio_analysis_is_a_separate_kind_with_duration_metrics(self):
        result = analyze_source_bytes(
            json.dumps(
                _payload(
                    kind="audio",
                    items=[
                        {
                            "downloadCount": 5,
                            "duration": 60,
                            "fileId": "audio-1",
                            "fileSize": 100,
                            "groupName": "闭门会",
                            "name": "宏观与科技博弈.mp3",
                            "status": "done",
                            "topicId": "audio-topic",
                            "uploadTime": "2026-09-01T10:00:00+08:00",
                        },
                        {
                            "downloadCount": 1,
                            "duration": 180,
                            "fileId": "audio-2",
                            "fileSize": 200,
                            "groupName": "闭门会",
                            "name": "宏观策略复盘.mp3",
                            "status": "pending",
                            "topicId": "audio-topic",
                            "uploadTime": "2026-09-01T11:00:00+08:00",
                        },
                    ],
                )
            ).encode("utf-8"),
            filename="audio-export.json",
            expected_kind="audio",
        )

        self.assertEqual(result.kind, "audio")
        self.assertEqual(result.summary["total_duration_seconds"], 240)
        self.assertEqual(result.summary["duration_median_seconds"], 120)
        self.assertEqual(result.summary["item_count"], 2)
        self.assertTrue(all(row["kind"] == "audio" for row in result.keywords))

    def test_rejects_invalid_schema_kind_and_negative_metrics(self):
        invalid_schema = _payload()
        invalid_schema["schema"] = "other/v1"
        with self.assertRaises(ZsxqAnalysisValidationError):
            analyze_source_bytes(json.dumps(invalid_schema).encode("utf-8"))

        with self.assertRaises(ZsxqAnalysisValidationError):
            analyze_source_bytes(
                json.dumps(_payload(kind="audio")).encode("utf-8"),
                expected_kind="pdf",
            )

        invalid_item = _payload()
        invalid_item["items"][0]["downloadCount"] = -1
        with self.assertRaises(ZsxqAnalysisValidationError):
            analyze_source_bytes(json.dumps(invalid_item).encode("utf-8"))

    def test_requires_export_metadata_contract_fields(self):
        missing_exported_at = _payload()
        missing_exported_at.pop("exportedAt")
        missing_summary = _payload()
        missing_summary.pop("summary")
        invalid_summary = _payload()
        invalid_summary["summary"] = []

        for payload in (missing_exported_at, missing_summary, invalid_summary):
            with self.subTest(payload=payload):
                with self.assertRaises(ZsxqAnalysisValidationError):
                    analyze_source_bytes(json.dumps(payload).encode("utf-8"))

    def test_duplicate_file_ids_are_preserved_and_counted(self):
        payload = _payload()
        payload["items"].append(
            dict(payload["items"][0], fileId="file-1", topicId="topic-2")
        )

        result = analyze_source_bytes(json.dumps(payload).encode("utf-8"))

        self.assertEqual(len(result.items), 3)
        self.assertEqual(result.summary["duplicate_file_id_count"], 1)
        self.assertTrue(result.items[2]["is_duplicate_file"])


class ZsxqAnalysisPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        from flask import Flask

        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            ZSXQ_ANALYSIS_ARTIFACT_ROOT=self.temp_dir.name,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add_all(
            [
                User(
                    username="analysis-one",
                    email="analysis-one@example.com",
                    password_hash="x",
                ),
                User(
                    username="analysis-two",
                    email="analysis-two@example.com",
                    password_hash="x",
                ),
            ]
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.temp_dir.cleanup()

    def test_persists_projections_and_keeps_source_as_artifact(self):
        source = json.dumps(_payload(), ensure_ascii=False).encode("utf-8")
        run = create_analysis_run(
            user_id=1,
            source_bytes=source,
            source_filename="snapshot.json",
            expected_kind="pdf",
        )

        self.assertEqual(run.item_count, 2)
        source_path = (
            Path(self.temp_dir.name) / "users" / "1" / f"{run.id}.json"
        )
        self.assertEqual(source_path.read_bytes(), source)

        history, total = list_analysis_runs(
            user_id=1, kind="pdf", page=1, per_page=20
        )
        self.assertEqual(total, 1)
        self.assertEqual(history[0]["id"], run.id)
        self.assertNotIn("source_artifact_path", history[0])
        self.assertNotIn("items", history[0])

        detail = build_analysis_run_detail(run)
        self.assertEqual(detail["run"]["id"], run.id)
        self.assertEqual(
            detail["run"]["source_sha256"], hashlib.sha256(source).hexdigest()
        )
        self.assertEqual(len(detail["top_items"]), 2)
        self.assertTrue(detail["keywords"])
        self.assertTrue(detail["timeline"])

        items, item_total = list_analysis_items(
            user_id=1, run_id=run.id, page=1, per_page=1, sort="downloads"
        )
        self.assertEqual(item_total, 2)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["file_id"], "file-1")

        second_run = create_analysis_run(
            user_id=1,
            source_bytes=source,
            source_filename="snapshot-again.json",
            expected_kind="pdf",
        )
        self.assertNotEqual(second_run.id, run.id)
        history, total = list_analysis_runs(
            user_id=1, kind="pdf", page=1, per_page=20
        )
        self.assertEqual(total, 2)
        self.assertEqual(
            {row["id"] for row in history}, {run.id, second_run.id}
        )

    def test_run_lookup_is_user_scoped(self):
        run = create_analysis_run(
            user_id=1,
            source_bytes=json.dumps(_payload()).encode("utf-8"),
            source_filename="snapshot.json",
        )

        self.assertIsNone(get_analysis_run(user_id=2, run_id=run.id))
        items, total = list_analysis_items(
            user_id=2, run_id=run.id, page=1, per_page=20
        )
        self.assertEqual(items, [])
        self.assertEqual(total, 0)

    def test_audio_detail_includes_deterministic_duration_distribution(self):
        items = []
        for index, duration in enumerate((0, 899, 900, 2699, 2700), start=1):
            items.append({
                "downloadCount": index,
                "duration": duration,
                "fileId": f"audio-{index}",
                "fileSize": 100,
                "groupName": "闭门会",
                "name": f"音频-{index}.mp3",
                "status": "done",
                "topicId": "audio-topic",
                "uploadTime": f"2026-09-02T1{index}:00:00+08:00",
            })
        run = create_analysis_run(
            user_id=1,
            source_bytes=json.dumps(
                _payload(kind="audio", items=items)
            ).encode("utf-8"),
            source_filename="audio-duration.json",
            expected_kind="audio",
        )

        detail = build_analysis_run_detail(run)

        distribution = {
            bucket["key"]: bucket["count"]
            for bucket in detail["run"]["duration_distribution"]
        }
        self.assertEqual(distribution, {
            "unknown": 1,
            "short": 1,
            "medium": 2,
            "long": 1,
        })


if __name__ == "__main__":
    unittest.main()
