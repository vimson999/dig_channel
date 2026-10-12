import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask
from sqlalchemy import UniqueConstraint, event


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import BilibiliCompetitor, BilibiliCompetitorVideo, SystemConfig, db
import run_bilibili_intelligence_scheduler as runner
from services import bilibili_intelligence_scheduler_service as schedule_service
from services import bilibili_intelligence_service as intelligence_service
from services.workflow_notification_service import workflow_notification_service


class BilibiliIntelligenceSchedulerTest(unittest.TestCase):
    def test_import_does_not_attach_production_log_handler(self):
        # Importing the runner for tests must not redirect every test logger
        # into the independently managed scheduler's production log.
        environment = dict(os.environ, PYTHONPATH=str(BACKEND_DIR))
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import logging, run_bilibili_intelligence_scheduler; "
                "print([getattr(h, 'baseFilename', '') "
                "for h in logging.getLogger().handlers])",
            ],
            cwd=BACKEND_DIR,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertNotIn("bilibili_intelligence_scheduler.log", result.stdout)

    def test_explicit_scheduler_logging_writes_to_its_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "scheduler.log"
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import logging, sys; from pathlib import Path; "
                    "import run_bilibili_intelligence_scheduler as runner; "
                    "runner.LOG_PATH = Path(sys.argv[1]); "
                    "runner._configure_logging(); "
                    "logging.getLogger('bilibili_intelligence_scheduler').error('scheduler startup marker')",
                    str(log_path),
                ],
                cwd=BACKEND_DIR,
                env=dict(os.environ, PYTHONPATH=str(BACKEND_DIR)),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("scheduler startup marker", log_path.read_text())

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
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

    def test_schedule_defaults_to_hourly_with_notifications_enabled(self):
        self.assertEqual(
            schedule_service.load_schedule_config(),
            {
                "enabled": True,
                "interval_minutes": 60,
                "timezone": "Asia/Shanghai",
                "page_size": 30,
                "notify_milestones": True,
            },
        )

    def test_bvid_has_one_unique_constraint_in_model_metadata(self):
        constraints = [
            constraint
            for constraint in BilibiliCompetitorVideo.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
            and tuple(column.name for column in constraint.columns) == ("bvid",)
        ]
        unique_indexes = [
            index
            for index in BilibiliCompetitorVideo.__table__.indexes
            if index.unique and tuple(column.name for column in index.columns) == ("bvid",)
        ]
        self.assertEqual(len(constraints) + len(unique_indexes), 1)

    def test_scheduler_singleton_lock_rejects_a_second_live_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "bilibili-intelligence.lock"
            with patch.object(runner, "LOCK_PATH", lock_path):
                first = runner._acquire_singleton_lock()
                self.assertIsNotNone(first)
                try:
                    self.assertIsNone(runner._acquire_singleton_lock())
                finally:
                    first.close()

                replacement = runner._acquire_singleton_lock()
                self.assertIsNotNone(replacement)
                replacement.close()

    def test_schedule_config_bounds_invalid_values(self):
        db.session.add(
            SystemConfig(
                key=schedule_service.CONFIG_KEY,
                scope=schedule_service.CONFIG_SCOPE,
                type=schedule_service.CONFIG_TYPE,
                status=1,
                value=json.dumps(
                    {
                        "enabled": "false",
                        "interval_minutes": 0,
                        "timezone": "Mars/Base",
                        "page_size": 999,
                        "notify_milestones": "false",
                    }
                ),
            )
        )
        db.session.commit()

        self.assertEqual(
            schedule_service.load_schedule_config(),
            {
                "enabled": False,
                "interval_minutes": 1,
                "timezone": "Asia/Shanghai",
                "page_size": 50,
                "notify_milestones": False,
            },
        )

    def test_partial_report_contains_success_and_failure_details(self):
        started_at = datetime(2026, 8, 18, 1, 0, 0)
        completed_at = started_at + timedelta(seconds=23.5)
        report = schedule_service.build_sync_report(
            {
                "synced": 1,
                "failed": 1,
                "results": [
                    {
                        "competitor_id": "ok-id",
                        "competitor_name": "成功账号",
                        "fetched": 30,
                        "created": 4,
                        "created_titles": ["新增研报标题"],
                        "updated": 26,
                    }
                ],
                "errors": [
                    {
                        "competitor_id": "bad-id",
                        "competitor_name": "失败账号",
                        "uid": "200",
                        "error": "接口超时",
                    }
                ],
            },
            started_at=started_at,
            completed_at=completed_at,
            trigger="scheduler",
        )

        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["duration_seconds"], 23.5)
        self.assertEqual(report["succeeded"][0]["competitor_name"], "成功账号")
        self.assertEqual(report["succeeded"][0]["created_titles"], ["新增研报标题"])
        self.assertEqual(report["failed"][0]["competitor_name"], "失败账号")
        self.assertEqual(report["failed"][0]["error"], "接口超时")

    def test_partial_run_notifies_with_success_and_failure_entries(self):
        sync_result = {
            "synced": 1,
            "failed": 1,
            "results": [
                {
                    "competitor_id": "ok-id",
                    "competitor_name": "成功账号",
                    "fetched": 30,
                    "created": 4,
                    "updated": 26,
                }
            ],
            "errors": [
                {
                    "competitor_id": "bad-id",
                    "competitor_name": "失败账号",
                    "uid": "200",
                    "error": "接口超时",
                }
            ],
        }
        notifications = []

        with (
            patch.object(
                runner,
                "load_schedule_config",
                return_value={
                    "enabled": True,
                    "interval_minutes": 60,
                    "timezone": "Asia/Shanghai",
                    "page_size": 30,
                    "notify_milestones": True,
                },
            ),
            patch.object(runner, "sync_all_competitors", return_value=sync_result),
            patch.object(
                runner,
                "_enqueue_notification",
                side_effect=lambda event_type, **kwargs: notifications.append((event_type, kwargs)),
            ),
            patch.object(runner, "release_db_session_quietly"),
        ):
            report = runner._run_once(self.app, "test")

        self.assertEqual(report["status"], "partial")
        self.assertEqual(len(notifications), 1)
        event_type, payload = notifications[0]
        self.assertEqual(event_type, "sync_failed")
        self.assertEqual(payload["report"]["succeeded"][0]["competitor_name"], "成功账号")
        self.assertEqual(payload["report"]["failed"][0]["competitor_name"], "失败账号")

    def test_notification_stage_lines_list_successes_and_failures(self):
        report = {
            "status": "partial",
            "duration_seconds": 23.5,
            "total_competitors": 2,
            "succeeded_count": 1,
            "failed_count": 1,
            "fetched": 30,
            "created": 4,
            "updated": 26,
            "succeeded": [
                {
                    "competitor_name": "成功账号",
                    "uid": "100",
                    "fetched": 30,
                    "created": 4,
                    "updated": 26,
                }
            ],
            "failed": [
                {
                    "competitor_name": "失败账号",
                    "uid": "200",
                    "error": "接口超时",
                }
            ],
        }

        lines = workflow_notification_service._stage_lines(
            {
                "stage_results": {
                    "sync": {
                        "status": "partial",
                        "summary": {"bilibili_intelligence": report},
                    }
                }
            }
        )
        text = "\n".join(lines)

        self.assertIn("成功账号", text)
        self.assertIn("失败账号", text)
        self.assertIn("接口超时", text)

    def test_notification_email_body_contains_partial_source_details(self):
        report = {
            "status": "partial",
            "duration_seconds": 23.5,
            "total_competitors": 2,
            "succeeded_count": 1,
            "failed_count": 1,
            "fetched": 30,
            "created": 4,
            "updated": 26,
            "succeeded": [
                {
                    "competitor_name": "成功账号",
                    "uid": "100",
                    "fetched": 30,
                    "created": 4,
                    "updated": 26,
                }
            ],
            "failed": [
                {
                    "competitor_name": "失败账号",
                    "uid": "200",
                    "error": "接口超时",
                }
            ],
        }

        subject, body = workflow_notification_service.build_email_content(
            {
                "event_type": "sync_failed",
                "workflow": {
                    "group_name": "B站内容情报台",
                    "status": "partial",
                    "trigger": "scheduler",
                    "stage_results": {
                        "sync": {
                            "status": "partial",
                            "summary": {"bilibili_intelligence": report},
                        }
                    },
                },
            }
        )

        self.assertIn("B站内容情报台", subject)
        self.assertIn("成功账号", body)
        self.assertIn("失败账号", body)
        self.assertIn("接口超时", body)

    def test_notification_email_body_lists_new_titles_by_source(self):
        report = {
            "status": "completed",
            "duration_seconds": 23.5,
            "total_competitors": 1,
            "succeeded_count": 1,
            "failed_count": 0,
            "fetched": 30,
            "created": 2,
            "updated": 28,
            "succeeded": [
                {
                    "competitor_name": "成功账号",
                    "uid": "100",
                    "fetched": 30,
                    "created": 2,
                    "created_titles": ["新增研报标题", "新增音频标题"],
                    "updated": 28,
                }
            ],
            "failed": [],
        }

        _subject, body = workflow_notification_service.build_email_content(
            {
                "event_type": "sync_completed",
                "workflow": {
                    "group_name": "B站内容情报台",
                    "status": "completed",
                    "trigger": "scheduler",
                    "stage_results": {
                        "sync": {
                            "status": "completed",
                            "summary": {"bilibili_intelligence": report},
                        }
                    },
                },
            }
        )

        self.assertIn("新增内容标题（2 条）：", body)
        self.assertIn("- 成功账号（UID 100）：新增研报标题", body)
        self.assertIn("- 成功账号（UID 100）：新增音频标题", body)

    def test_sync_all_competitors_adds_identity_to_failed_source(self):
        db.session.add_all(
            [
                BilibiliCompetitor(
                    id="ok-id",
                    uid="100",
                    name="成功账号",
                    content_type="report",
                    active=True,
                ),
                BilibiliCompetitor(
                    id="bad-id",
                    uid="200",
                    name="失败账号",
                    content_type="audio",
                    active=True,
                ),
            ]
        )
        db.session.commit()

        def sync_one(competitor_id, *, page_size):
            if competitor_id == "bad-id":
                raise RuntimeError("接口超时")
            return {
                "competitor_id": competitor_id,
                "competitor_name": "成功账号",
                "fetched": page_size,
                "created": 1,
                "updated": page_size - 1,
            }

        with patch.object(intelligence_service, "sync_competitor", side_effect=sync_one):
            result = intelligence_service.sync_all_competitors(page_size=30)

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["errors"][0]["competitor_name"], "失败账号")
        self.assertEqual(result["errors"][0]["uid"], "200")

    def test_sync_competitor_returns_titles_for_new_videos(self):
        db.session.add(
            BilibiliCompetitor(
                id="new-id",
                uid="300",
                name="标题账号",
                content_type="report",
                active=True,
            )
        )
        db.session.commit()

        with (
            patch.object(
                intelligence_service,
                "resolve_bilibili_auth",
                return_value={
                    "credential_id": "credential-1",
                    "sessdata": "sessdata",
                    "bili_jct": "bili-jct",
                    "buvid3": "buvid3",
                    "dedeuserid": "300",
                },
            ),
            patch.object(
                intelligence_service,
                "fetch_user_videos",
                return_value={
                    "page": {"count": 1},
                    "list": {
                        "vlist": [{
                            "bvid": "BVNEW",
                            "title": "新增投稿标题",
                            "created": 1784307539,
                        }]
                    },
                },
            ),
            patch.object(intelligence_service, "mark_credential_success"),
            patch.object(intelligence_service, "release_db_session_quietly"),
        ):
            result = intelligence_service.sync_competitor("new-id", page_size=30)

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["created_titles"], ["新增投稿标题"])

    def test_sync_competitor_does_not_rewrite_unchanged_raw_payload(self):
        source = {
            "bvid": "BVSAME",
            "aid": "12345",
            "title": "没有变化的投稿",
            "description": "正文",
            "pic": "https://example.test/cover.jpg",
            "length": "1:30",
            "created": 1784307539,
            "play": 100,
            "comment": 5,
            "video_review": 3,
            "is_charging_arc": False,
        }
        competitor = BilibiliCompetitor(
            id="same-id",
            uid="400",
            name="未变化账号",
            content_type="report",
            active=True,
        )
        db.session.add(competitor)
        db.session.add(
            BilibiliCompetitorVideo(
                id="video-same-id",
                competitor_id="same-id",
                content_type="report",
                bvid="BVSAME",
                aid="12345",
                title="没有变化的投稿",
                description="正文",
                cover_url="https://example.test/cover.jpg",
                duration_seconds=90,
                published_at=datetime.utcfromtimestamp(1784307539),
                play_count=100,
                comment_count=5,
                danmaku_count=3,
                is_charging=False,
                raw_payload=dict(source),
                last_seen_at=datetime(2026, 8, 18, 0, 0, 0),
            )
        )
        db.session.commit()

        statements = []
        update_rowcounts = []

        def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        def capture_rowcount(_conn, cursor, statement, _parameters, _context, _executemany):
            if statement.lower().lstrip().startswith("update bilibili_competitor_videos"):
                update_rowcounts.append((statement.lower(), cursor.rowcount))

        event.listen(db.engine, "before_cursor_execute", capture_statement)
        event.listen(db.engine, "after_cursor_execute", capture_rowcount)
        try:
            with (
                patch.object(
                    intelligence_service,
                    "resolve_bilibili_auth",
                    return_value={
                        "credential_id": "credential-1",
                        "sessdata": "sessdata",
                        "bili_jct": "bili-jct",
                        "buvid3": "buvid3",
                        "dedeuserid": "400",
                    },
                ),
                patch.object(
                    intelligence_service,
                    "fetch_user_videos",
                    return_value={
                        "page": {"count": 1},
                        "list": {"vlist": [dict(source)]},
                    },
                ),
                patch.object(intelligence_service, "mark_credential_success"),
                patch.object(intelligence_service, "release_db_session_quietly"),
            ):
                intelligence_service.sync_competitor("same-id", page_size=30)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)
            event.remove(db.engine, "after_cursor_execute", capture_rowcount)

        identity_selects = [
            statement.lower()
            for statement in statements
            if statement.lower().lstrip().startswith("select")
            and "from bilibili_competitor_videos" in statement.lower()
        ]
        self.assertEqual(len(identity_selects), 1)
        self.assertIn("bilibili_competitor_videos.id", identity_selects[0])
        self.assertIn("bilibili_competitor_videos.bvid", identity_selects[0])
        self.assertNotIn("raw_payload", identity_selects[0])
        self.assertNotIn("description", identity_selects[0])

        scalar_updates = [
            statement
            for statement, _rowcount in update_rowcounts
            if "last_seen_at" in statement
        ]
        self.assertEqual(len(scalar_updates), 1)
        raw_updates = [
            rowcount
            for statement, rowcount in update_rowcounts
            if "raw_payload" in statement
        ]
        self.assertTrue(all(rowcount == 0 for rowcount in raw_updates))

    def test_sync_competitor_persists_raw_only_change_without_wide_identity_read(self):
        original = {
            "bvid": "BVRAWCHANGE",
            "aid": "54321",
            "title": "仅审计载荷变化的投稿",
            "description": "正文",
            "pic": "https://example.test/raw-change.jpg",
            "length": "2:00",
            "created": 1784307539,
            "play": 200,
            "comment": 6,
            "video_review": 4,
            "is_charging_arc": False,
            "source_revision": "v1",
        }
        db.session.add(
            BilibiliCompetitor(
                id="raw-change-competitor-id",
                uid="401",
                name="审计载荷账号",
                content_type="report",
                active=True,
            )
        )
        db.session.add(
            BilibiliCompetitorVideo(
                id="video-raw-change-id",
                competitor_id="raw-change-competitor-id",
                content_type="report",
                bvid="BVRAWCHANGE",
                aid="54321",
                title="仅审计载荷变化的投稿",
                description="正文",
                cover_url="https://example.test/raw-change.jpg",
                duration_seconds=120,
                published_at=datetime.utcfromtimestamp(1784307539),
                play_count=200,
                comment_count=6,
                danmaku_count=4,
                is_charging=False,
                raw_payload=dict(original),
                last_seen_at=datetime(2026, 8, 18, 0, 0, 0),
            )
        )
        db.session.commit()

        statements = []

        def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        incoming = {**original, "source_revision": "v2"}
        event.listen(db.engine, "before_cursor_execute", capture_statement)
        try:
            with (
                patch.object(
                    intelligence_service,
                    "resolve_bilibili_auth",
                    return_value={
                        "credential_id": "credential-1",
                        "sessdata": "sessdata",
                        "bili_jct": "bili-jct",
                        "buvid3": "buvid3",
                        "dedeuserid": "401",
                    },
                ),
                patch.object(
                    intelligence_service,
                    "fetch_user_videos",
                    return_value={
                        "page": {"count": 1},
                        "list": {"vlist": [incoming]},
                    },
                ),
                patch.object(intelligence_service, "mark_credential_success"),
                patch.object(intelligence_service, "release_db_session_quietly"),
            ):
                result = intelligence_service.sync_competitor(
                    "raw-change-competitor-id",
                    page_size=30,
                )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)

        db.session.expire_all()
        refreshed = db.session.get(BilibiliCompetitorVideo, "video-raw-change-id")
        self.assertEqual(refreshed.raw_payload["source_revision"], "v2")
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 1)

        identity_selects = [
            statement.lower()
            for statement in statements
            if statement.lower().lstrip().startswith("select")
            and "from bilibili_competitor_videos" in statement.lower()
        ]
        self.assertEqual(len(identity_selects), 1)
        self.assertNotIn("raw_payload", identity_selects[0])
        self.assertNotIn("description", identity_selects[0])


if __name__ == "__main__":
    unittest.main()
