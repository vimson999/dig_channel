import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from apis.v1 import content_pipeline as content_pipeline_api  # noqa: E402
from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402


class ContentPipelineWebSubmissionStatsApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(content_pipeline_api.bp)
        self.client = self.app.test_client()
        self.auth_patch = patch(
            "utils.auth_utils.get_current_user",
            return_value=SimpleNamespace(id=42, is_admin=True),
        )
        self.auth_patch.start()

    def tearDown(self):
        self.auth_patch.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def _clean_task(task_id, kind, *, user_id="42"):
        feature = "content_pdf_clean" if kind == "pdf" else "content_audio_clean"
        return PublicationTask(
            id=task_id,
            status="completed",
            created_at=datetime(2026, 10, 14, 1, 0),
            params={
                "feature": feature,
                "user_id": user_id,
                "content_pipeline": {
                    "metadata": {
                        "submission_channel": "content_pipeline_web",
                        "web_submission_id": f"submission-{task_id}",
                    },
                },
            },
            result={},
        )

    @staticmethod
    def _publish_task(task_id, source_task_id, kind, completed_at, files, *, confirmed, user_id="42"):
        return PublicationTask(
            id=task_id,
            status="completed" if confirmed else "failed",
            completed_at=completed_at,
            params={
                "feature": "zsxq_clean_output_publish",
                "user_id": user_id,
                "source_task_id": source_task_id,
                "zsxq_publish": {
                    "source_task_id": source_task_id,
                    "file_type": kind,
                },
            },
            result={
                "rounds": [
                    {
                        "rules": [
                            {
                                "file_type": kind,
                                "verified_count": sum(
                                    1 for item in files if item.get("status") == "uploaded"
                                ) if confirmed else 0,
                                "verification": {"confirmed": confirmed},
                                "files": files,
                            }
                        ]
                    }
                ]
            },
        )

    def test_statistics_count_only_verified_files_across_day_week_month_and_year(self):
        with self.app.app_context():
            db.create_all()
            try:
                pdf_task = self._clean_task("pdf-clean-1", "pdf")
                mp3_task = self._clean_task("mp3-clean-1", "mp3")
                failed_pdf_task = self._clean_task("pdf-clean-failed", "pdf")
                other_user_task = self._clean_task("other-user-clean", "pdf", user_id="99")
                db.session.add_all(
                    [
                        pdf_task,
                        mp3_task,
                        failed_pdf_task,
                        other_user_task,
                        self._publish_task(
                            "pdf-publish-1",
                            "pdf-clean-1",
                            "pdf",
                            datetime(2026, 10, 14, 2, 0),
                            [
                                {
                                    "name": "高盛-Apple（AAPL.US）：业绩展望.pdf",
                                    "path": "/clean/高盛-Apple（AAPL.US）：业绩展望.pdf",
                                    "status": "uploaded",
                                    "topic_id": "12345678901234",
                                },
                                {
                                    "name": "高盛-Microsoft（MSFT.O）.pdf",
                                    "path": "/clean/microsoft.pdf",
                                    "status": "uploaded",
                                },
                                {"name": "upload-error.pdf", "status": "failed"},
                            ],
                            confirmed=True,
                        ),
                        self._publish_task(
                            "mp3-publish-1",
                            "mp3-clean-1",
                            "audio",
                            datetime(2026, 10, 14, 3, 0),
                            [
                                {
                                    "name": "261012-中信建投政策经纬谈.mp3",
                                    "path": "/clean/261012-中信建投政策经纬谈.mp3",
                                    "status": "uploaded",
                                }
                            ],
                            confirmed=True,
                        ),
                        self._publish_task(
                            "pdf-publish-unverified",
                            "pdf-clean-failed",
                            "pdf",
                            datetime(2026, 10, 14, 4, 0),
                            [
                                {
                                    "name": "高盛-Microsoft（MSFT.O）.pdf",
                                    "path": "/clean/unverified.pdf",
                                    "status": "uploaded",
                                }
                            ],
                            confirmed=False,
                        ),
                        self._publish_task(
                            "other-user-publish",
                            "other-user-clean",
                            "pdf",
                            datetime(2026, 10, 14, 5, 0),
                            [
                                {
                                    "name": "高盛-Microsoft（MSFT.O）.pdf",
                                    "path": "/clean/other-user.pdf",
                                    "status": "uploaded",
                                }
                            ],
                            confirmed=True,
                            user_id="99",
                        ),
                    ]
                )
                db.session.commit()

                expected_periods = {
                    "day": "2026-10-14",
                    "week": "2026-W42",
                    "month": "2026-10",
                    "year": "2026",
                }
                for granularity, period in expected_periods.items():
                    with self.subTest(granularity=granularity):
                        response = self.client.get(
                            "/api/v1/content-pipeline/web-submissions/statistics"
                            f"?period={granularity}&from=2026-10-14&to=2026-10-15"
                        )

                        self.assertEqual(response.status_code, 200)
                        data = response.get_json()["data"]
                        self.assertEqual(data["totals"], {"pdf": 2, "mp3": 1, "total": 3})
                        self.assertEqual(len(data["rows"]), 2)
                        rows = {row["investment_bank"]: row for row in data["rows"]}
                        self.assertEqual(rows["高盛"], {
                            "period": period,
                            "investment_bank": "高盛",
                            "pdf": 2,
                            "mp3": 0,
                            "total": 2,
                        })
                        self.assertEqual(rows["内资机构"]["mp3"], 1)

                details_url = "/api/v1/content-pipeline/web-submissions/files"
                first = self.client.get(
                    details_url + "?bank=高盛&from=2026-10-14&to=2026-10-15&page_size=1"
                )
                self.assertEqual(first.status_code, 200)
                first_data = first.get_json()["data"]
                self.assertEqual(first_data["totals"], {"pdf": 2, "mp3": 0, "total": 2})
                self.assertEqual(first_data["total_items"], 2)
                self.assertTrue(first_data["has_more"])
                self.assertEqual(len(first_data["items"]), 1)
                self.assertIn("Apple", first_data["items"][0]["filename"])
                self.assertEqual(first_data["items"][0]["publish_url"],
                                 "https://wx.zsxq.com/dweb2/index/topic_detail/12345678901234")
                self.assertNotIn("path", first_data["items"][0])
                second = self.client.get(
                    details_url + "?bank=高盛&from=2026-10-14&to=2026-10-15&page_size=1&page=2"
                ).get_json()["data"]
                self.assertIn("Microsoft", second["items"][0]["filename"])
                self.assertFalse(second["has_more"])
                domestic = self.client.get(
                    details_url + "?bank=内资机构&kind=mp3&from=2026-10-14&to=2026-10-15"
                ).get_json()["data"]
                self.assertEqual(domestic["totals"], {"pdf": 0, "mp3": 1, "total": 1})
                self.assertIn("中信建投", domestic["items"][0]["filename"])
                empty = self.client.get(
                    details_url + "?bank=高盛&from=2026-10-15&to=2026-10-15"
                ).get_json()["data"]
                self.assertEqual(empty["items"], [])
                self.assertEqual(empty["totals"]["total"], 0)

                filtered = self.client.get(
                    "/api/v1/content-pipeline/web-submissions/statistics"
                    "?period=day&from=2026-10-14&to=2026-10-15&bank=高盛"
                )
                self.assertEqual(filtered.status_code, 200)
                self.assertEqual(filtered.get_json()["data"]["totals"], {
                    "pdf": 2,
                    "mp3": 0,
                    "total": 2,
                })
            finally:
                db.session.remove()
                db.drop_all()

    def test_file_details_reject_invalid_filters_before_querying(self):
        endpoint = "/api/v1/content-pipeline/web-submissions/files"
        for query in ("", "?bank=高盛&page=0", "?bank=高盛&page=bad",
                      "?bank=高盛&page_size=101", "?bank=高盛&kind=exe",
                      "?bank=高盛&from=invalid", "?bank=高盛&from=2026-10-16&to=2026-10-15"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(endpoint + query).status_code, 400)

    def test_legacy_counts_and_retry_files_match_details_without_exposing_paths(self):
        from services.content_pipeline.web_submission_stats import (
            collect_verified_publication_files, normalize_publication_bank, paginate_publication_files,
        )

        first = SimpleNamespace(
            id="publish-1", source_task_id="clean-pdf", completed_at=datetime(2026, 10, 14, 16, 5),
            result={"rules": [{
                "file_type": "pdf", "verified_count": 1, "topic_id": "12345678901234",
                "files": [{"name": "大摩-report.pdf", "path": "/private/local/report.pdf", "status": "uploaded"}],
            }]},
        )
        retry = SimpleNamespace(**{**first.__dict__, "id": "publish-retry"})
        legacy = SimpleNamespace(
            id="publish-legacy", source_task_id="clean-audio", completed_at=datetime(2026, 10, 14, 15, 59),
            result={"rules": [{"file_type": "audio", "verified_count": 3}]},
        )
        files = collect_verified_publication_files(
            [first, retry, legacy], {"clean-pdf": "pdf", "clean-audio": "mp3"}, include_details=True,
        )
        self.assertEqual(sum(row["count"] for row in files), 4)
        domestic = paginate_publication_files(
            files, bank="内资机构", start_date=datetime(2026, 10, 14).date(), end_date=datetime(2026, 10, 14).date(),
        )
        self.assertEqual(domestic["totals"], {"pdf": 0, "mp3": 3, "total": 3})
        self.assertEqual(domestic["items"][0]["filename"], "")
        self.assertEqual(domestic["items"][0]["count"], 3)
        self.assertEqual(domestic["items"][0]["publish_url"], "")
        morgan = paginate_publication_files(
            files, bank="摩根士丹利", start_date=datetime(2026, 10, 15).date(), end_date=datetime(2026, 10, 15).date(),
        )
        self.assertEqual(len(morgan["items"]), 1)
        self.assertEqual(morgan["items"][0]["published_at"], "2026-10-15T00:05:00+08:00")
        self.assertNotIn("/private/local", str(morgan))
        self.assertEqual(normalize_publication_bank("大摩"), "摩根士丹利")
        self.assertEqual(normalize_publication_bank("小摩"), "摩根大通")


if __name__ == "__main__":
    unittest.main()
