import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from sqlalchemy import event


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.bilibili_publish import PublicationTaskBilibiliUpload  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from api.mp3_video_workflow import _cancel_bilibili_upload_child  # noqa: E402
from services import publisher_queue_repair  # noqa: E402


class DummyCeleryTask:
    def __init__(self):
        self.calls = []

    def apply_async(self, *, args=None, queue=None, countdown=None, retry=None):
        self.calls.append({
            "args": args,
            "queue": queue,
            "countdown": countdown,
            "retry": retry,
        })
        return SimpleNamespace(id="new-celery-id")


class PublicationTaskQueueRepairTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        PublicationTask.__table__.create(bind=db.engine)
        PublicationTaskBilibiliUpload.__table__.create(bind=db.engine)

    def tearDown(self):
        db.session.remove()
        PublicationTaskBilibiliUpload.__table__.drop(bind=db.engine)
        PublicationTask.__table__.drop(bind=db.engine)
        self.ctx.pop()

    def _task(self, *, feature="content_audio_clean", step="init", minutes_old=10):
        now = datetime.utcnow()
        task = PublicationTask(
            status="pending",
            current_step=step,
            progress=0,
            params={"feature": feature},
            result={},
            celery_task_id="old-celery-id",
            created_at=now - timedelta(minutes=minutes_old),
            updated_at=now - timedelta(minutes=minutes_old),
        )
        db.session.add(task)
        db.session.commit()
        return task

    def _parent_with_publisher_child(self, *, parent_status="canceled"):
        now = datetime.utcnow()
        parent = PublicationTask(
            feature="mp3_video_workflow",
            status=parent_status,
            current_step=parent_status,
            progress=92,
            params={"feature": "mp3_video_workflow"},
            result={},
            created_at=now - timedelta(minutes=20),
            updated_at=now - timedelta(minutes=15),
            completed_at=now - timedelta(minutes=10) if parent_status != "processing" else None,
        )
        db.session.add(parent)
        db.session.commit()

        child = PublicationTask(
            feature="publisher",
            status="pending",
            current_step="queued",
            progress=0,
            parent_task_id=parent.id,
            source_task_id=parent.id,
            parent_feature="mp3_video_workflow",
            source_feature="mp3_video_workflow",
            params={
                "feature": "publisher",
                "parent_task_id": parent.id,
                "source_task_id": parent.id,
                "metadata": {"parent_task_id": parent.id},
            },
            result={"video_path": None},
            celery_task_id="old-render-celery-id",
            created_at=now - timedelta(minutes=18),
            updated_at=now - timedelta(minutes=18),
        )
        db.session.add(child)
        db.session.commit()
        return parent, child

    def _processing_publisher_task(self, *, step="v_synthesis", minutes_old=20):
        now = datetime.utcnow()
        task = PublicationTask(
            feature="publisher",
            status="processing",
            current_step=step,
            progress=30,
            params={"feature": "publisher", "video": {"enabled": True}},
            result={},
            celery_task_id="old-render-celery-id",
            created_at=now - timedelta(minutes=minutes_old),
            updated_at=now - timedelta(minutes=minutes_old),
        )
        db.session.add(task)
        db.session.commit()
        return task

    def _processing_business_task(self, *, feature, step, minutes_old=20):
        now = datetime.utcnow()
        task = PublicationTask(
            feature=feature,
            status="processing",
            current_step=step,
            progress=38,
            params={"feature": feature},
            result={},
            celery_task_id="old-business-celery-id",
            created_at=now - timedelta(minutes=minutes_old),
            updated_at=now - timedelta(minutes=minutes_old),
        )
        db.session.add(task)
        db.session.commit()
        return task

    def _pending_publisher_task(self, *, step="deferred_capacity", minutes_old=20):
        now = datetime.utcnow()
        task = PublicationTask(
            feature="publisher",
            status="pending",
            current_step=step,
            progress=0,
            params={"feature": "publisher", "video": {"enabled": True}},
            result={},
            error_message="系统保护：publisher 同类任务达到当前运行上限 4，稍后自动重试。",
            celery_task_id="old-render-celery-id",
            created_at=now - timedelta(minutes=minutes_old),
            updated_at=now - timedelta(minutes=minutes_old),
        )
        db.session.add(task)
        db.session.commit()
        return task

    def test_requeues_pending_task_when_celery_message_is_missing(self):
        task = self._task()
        dummy = DummyCeleryTask()

        with patch.dict(os.environ, {
            "BUSINESS_TASK_QUEUE_REPAIR_ENABLED": "1",
            "BUSINESS_TASK_QUEUE_REPAIR_LIMIT": "3",
        }), \
             patch.object(publisher_queue_repair, "_celery_message_visible", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_repair_target_for_feature", return_value=dummy):
            updated, requeued = publisher_queue_repair.maybe_requeue_stale_publication_task(task)

        self.assertTrue(requeued)
        self.assertEqual(updated.celery_task_id, "new-celery-id")
        self.assertEqual(updated.current_step, "queued")
        self.assertIsNone(updated.error_message)
        self.assertEqual(dummy.calls[0]["queue"], "content_pipeline")
        repair = updated.result["queue_repair"]
        self.assertEqual(repair["previous_celery_id"], "old-celery-id")
        self.assertEqual(repair["current_celery_id"], "new-celery-id")
        self.assertEqual(repair["feature"], "content_audio_clean")

    def test_scan_recovers_doubao_auto_retry_message_lost_during_restart(self):
        task = self._task(
            feature="doubao_browser_tts",
            step="auto_retry",
            minutes_old=20,
        )
        task.progress = 96
        task.result = {
            "retry_state": {
                "retry_number": 1,
                "retry_limit": 2,
                "retry_delay_seconds": 20,
            }
        }
        task.updated_at = datetime.utcnow() - timedelta(minutes=20)
        db.session.commit()
        task_id = task.id
        dummy = DummyCeleryTask()

        with patch.object(
            publisher_queue_repair,
            "_celery_message_visible",
            return_value=(False, True),
        ), patch.object(
            publisher_queue_repair,
            "_repair_target_for_feature",
            return_value=dummy,
        ):
            result = publisher_queue_repair.repair_stale_publication_tasks_once()

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["requeued"], 1)
        task = db.session.get(PublicationTask, task_id)
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.current_step, "queued")
        self.assertEqual(task.progress, 0)
        self.assertEqual(task.celery_task_id, "new-celery-id")
        self.assertEqual(dummy.calls[0]["queue"], "doubao_browser_tts")

    def test_scan_recovers_report_visual_plan_auto_retry_message_lost_during_restart(self):
        task = self._task(
            feature="report_visual_plan",
            step="auto_retry",
            minutes_old=20,
        )
        task.progress = 5
        task.result = {
            "auto_retry_history": [{
                "attempt": 1,
                "error": "Gemini 浏览器 profile 正在被其他 Chrome 占用",
                "delay_seconds": 30,
            }],
            "last_retry_error": "Gemini 浏览器 profile 正在被其他 Chrome 占用",
        }
        task.updated_at = datetime.utcnow() - timedelta(minutes=20)
        db.session.commit()
        task_id = task.id
        dummy = DummyCeleryTask()

        with patch.object(
            publisher_queue_repair,
            "_celery_message_visible",
            return_value=(False, True),
        ), patch.object(
            publisher_queue_repair,
            "_repair_target_for_feature",
            return_value=dummy,
        ):
            result = publisher_queue_repair.repair_stale_publication_tasks_once()

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["requeued"], 1)
        task = db.session.get(PublicationTask, task_id)
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.current_step, "queued")
        self.assertEqual(task.progress, 0)
        self.assertEqual(task.celery_task_id, "new-celery-id")
        self.assertEqual(dummy.calls[0]["queue"], "report_visual_plan")

    def test_pending_repair_releases_db_transaction_before_celery_inspection(self):
        task = self._task()
        dummy = DummyCeleryTask()
        transaction_states = []

        def capture_visibility(*_args, **_kwargs):
            transaction_states.append(db.session().in_transaction())
            return False, True

        with patch.object(
            publisher_queue_repair,
            "_celery_message_visible",
            side_effect=capture_visibility,
        ), patch.object(
            publisher_queue_repair,
            "_repair_target_for_feature",
            return_value=dummy,
        ):
            updated, requeued = publisher_queue_repair.maybe_requeue_stale_publication_task(task)

        self.assertTrue(requeued)
        self.assertEqual(updated.celery_task_id, "new-celery-id")
        self.assertEqual(transaction_states, [False])

    def test_does_not_requeue_when_existing_message_is_visible(self):
        task = self._task()
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_celery_message_visible", return_value=(True, True)), \
             patch.object(publisher_queue_repair, "_repair_target_for_feature", return_value=dummy):
            updated, requeued = publisher_queue_repair.maybe_requeue_stale_publication_task(task)

        self.assertFalse(requeued)
        self.assertEqual(updated.celery_task_id, "old-celery-id")
        self.assertEqual(dummy.calls, [])

    def test_closes_pending_task_when_repair_limit_is_reached_and_message_missing(self):
        task = self._task(step="deferred_capacity")
        task.result = {"queue_repair": {"count": 3}}
        task.updated_at = datetime.utcnow() - timedelta(minutes=10)
        db.session.commit()
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_celery_message_visible", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_repair_target_for_feature", return_value=dummy):
            updated, requeued = publisher_queue_repair.maybe_requeue_stale_publication_task(task)

        self.assertFalse(requeued)
        self.assertEqual(updated.status, "failed")
        self.assertEqual(updated.current_step, "failed")
        self.assertIn("修复已达到上限", updated.error_message)
        self.assertEqual(updated.result["queue_repair"]["reason"], "queue_repair_limit_reached")
        self.assertEqual(updated.result["error_type"], "queue_repair_limit_reached")
        self.assertEqual(dummy.calls, [])

    def test_requeues_deferred_publish_task_after_message_vanishes(self):
        task = self._task(feature="zsxq_clean_output_publish", step="deferred_capacity")
        task.error_message = "系统保护：达到当前运行上限，稍后自动重试。"
        task.updated_at = datetime.utcnow() - timedelta(minutes=10)
        db.session.commit()
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_celery_message_visible", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_repair_target_for_feature", return_value=dummy):
            updated, requeued = publisher_queue_repair.maybe_requeue_stale_publication_task(task)

        self.assertTrue(requeued)
        self.assertEqual(updated.celery_task_id, "new-celery-id")
        self.assertEqual(updated.current_step, "queued")
        self.assertIsNone(updated.error_message)
        self.assertEqual(dummy.calls[0]["queue"], "zsxq_publish")

    def test_requeues_visual_plan_lost_during_worker_restart(self):
        task = self._task(
            feature="report_visual_plan",
            step="deferred_backlog",
            minutes_old=20,
        )
        task.updated_at = datetime.utcnow() - timedelta(minutes=20)
        db.session.commit()
        dummy = DummyCeleryTask()

        with patch.object(
            publisher_queue_repair,
            "_celery_message_visible",
            return_value=(False, True),
        ), patch.object(
            publisher_queue_repair,
            "_repair_target_for_feature",
            return_value=dummy,
        ):
            updated, requeued = (
                publisher_queue_repair.maybe_requeue_stale_publication_task(task)
            )

        self.assertTrue(requeued)
        self.assertEqual(updated.celery_task_id, "new-celery-id")
        self.assertEqual(dummy.calls[0]["queue"], "report_visual_plan")
        self.assertEqual(
            updated.result["queue_repair"]["feature"],
            "report_visual_plan",
        )

    def test_requeues_pending_report_audio_parent_after_message_vanishes(self):
        task = self._task(
            feature="report_pdf_audio_workflow",
            step="queued",
        )
        task.progress = 71
        task.result = {
            "waiting_child": {
                "label": "doubao_browser_3",
                "child_id": "failed-child-id",
                "status": "processing",
            }
        }
        task.updated_at = datetime.utcnow() - timedelta(minutes=10)
        db.session.commit()
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_celery_message_visible", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_repair_target_for_feature", return_value=dummy):
            updated, requeued = publisher_queue_repair.maybe_requeue_stale_publication_task(task)

        self.assertTrue(requeued)
        self.assertEqual(updated.status, "pending")
        self.assertEqual(updated.current_step, "queued")
        self.assertEqual(updated.progress, 0)
        self.assertEqual(updated.celery_task_id, "new-celery-id")
        self.assertEqual(dummy.calls[0]["queue"], "report_audio_workflow")
        self.assertEqual(
            updated.result["waiting_child"]["child_id"],
            "failed-child-id",
        )

    def test_requeues_deferred_publisher_task_after_message_vanishes(self):
        task = self._pending_publisher_task(step="deferred_capacity")
        task_id = task.id
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "run_publication_pipeline", dummy):
            result = publisher_queue_repair.repair_stale_publisher_pending_tasks_once()

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["requeued"], 1)
        task = db.session.get(PublicationTask, task_id)
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.current_step, "queued")
        self.assertEqual(task.celery_task_id, "new-celery-id")
        self.assertIsNone(task.error_message)

    def test_publisher_pending_repair_releases_transaction_before_external_checks(self):
        task = self._pending_publisher_task(step="deferred_capacity")
        dummy = DummyCeleryTask()
        transaction_states = []

        def worker_visibility(*_args, **_kwargs):
            transaction_states.append(("worker", db.session().in_transaction()))
            return False, True

        def queue_visibility(*_args, **_kwargs):
            transaction_states.append(("queue", db.session().in_transaction()))
            return False, True

        with patch.object(
            publisher_queue_repair,
            "_message_visible_in_workers",
            side_effect=worker_visibility,
        ), patch.object(
            publisher_queue_repair,
            "_message_visible_in_queue",
            side_effect=queue_visibility,
        ), patch.object(
            publisher_queue_repair,
            "run_publication_pipeline",
            dummy,
        ):
            updated, requeued = (
                publisher_queue_repair.maybe_requeue_stale_publisher_child(task)
            )

        self.assertTrue(requeued)
        self.assertEqual("new-celery-id", updated.celery_task_id)
        self.assertEqual(
            [("worker", False), ("queue", False)],
            transaction_states,
        )

    def test_superseded_repair_releases_transaction_before_revoke(self):
        task = self._pending_publisher_task()
        task_id = task.id
        transaction_states = []

        # Reproduce the post-publish CAS/state-check phase: loading the row opens
        # a transaction that must be closed before the Celery control request.
        self.assertIsNotNone(db.session.get(PublicationTask, task_id))
        self.assertTrue(db.session().in_transaction())

        def capture_revoke(*_args, **_kwargs):
            transaction_states.append(db.session().in_transaction())

        with patch.object(
            publisher_queue_repair,
            "_revoke_published_repair_message",
            side_effect=capture_revoke,
        ) as revoke:
            publisher_queue_repair._release_then_revoke_published_repair_message(
                SimpleNamespace(id="superseded-celery-id"),
                task_id=task_id,
                log=publisher_queue_repair.logger,
            )

        revoke.assert_called_once()
        self.assertEqual(transaction_states, [False])

    def test_requeues_post_completion_bilibili_upload_to_platform_upload_queue(self):
        _, child = self._parent_with_publisher_child(parent_status="completed")
        child.params = {
            **child.params,
            "video": {
                "enabled": False,
                "media_type": "existing_video",
                "video_path": "/tmp/done.mp4",
            },
            "bilibili": {"enabled": True, "title": "内资闭门会 | 测试 | 2026-07-07"},
            "metadata": {
                "source": "mp3_video_workflow:bilibili_upload",
                "parent_task_id": child.parent_task_id,
            },
        }
        child.updated_at = datetime.utcnow() - timedelta(minutes=18)
        projection = PublicationTaskBilibiliUpload(
            task_id=child.id,
            parent_task_id=child.parent_task_id,
            parent_feature="mp3_video_workflow",
            source_label="mp3_video_workflow:bilibili_upload",
            video_key="landscape",
            title="内资闭门会 | 测试 | 2026-07-07",
            video_path="/tmp/done.mp4",
            tags=["财经"],
        )
        db.session.add(projection)
        db.session.commit()
        projection_task_id = projection.task_id
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(False, True)) as queue_scan, \
             patch("tasks.publisher.run_bilibili_upload_only", dummy):
            updated, requeued = publisher_queue_repair.maybe_requeue_stale_publisher_child(child)

        self.assertTrue(requeued)
        self.assertEqual(updated.celery_task_id, "new-celery-id")
        self.assertEqual(dummy.calls[0]["queue"], "platform_upload")
        self.assertEqual(queue_scan.call_args.kwargs["queue"], "platform_upload")
        projection = db.session.get(
            PublicationTaskBilibiliUpload,
            projection_task_id,
        )
        self.assertEqual(projection.queue_repair_count, 1)
        self.assertEqual(projection.queue_repair_queue, "platform_upload")
        self.assertEqual(projection.queue_repair_current_celery_id, "new-celery-id")
        self.assertNotIn("queue_repair", updated.result)

    def test_bilibili_watchdog_repair_never_selects_task_payload_json(self):
        _, child = self._parent_with_publisher_child(parent_status="completed")
        child.params = {
            **child.params,
            "video": {"enabled": False, "media_type": "existing_video"},
            "bilibili": {"enabled": True, "title": "测试投稿"},
            "metadata": {
                "source": "mp3_video_workflow:bilibili_upload",
                "parent_task_id": child.parent_task_id,
            },
        }
        child.updated_at = datetime.utcnow() - timedelta(minutes=18)
        db.session.add(PublicationTaskBilibiliUpload(
            task_id=child.id,
            parent_task_id=child.parent_task_id,
            parent_feature="mp3_video_workflow",
            source_label="mp3_video_workflow:bilibili_upload",
            video_key="landscape",
            title="测试投稿",
            video_path="/tmp/done.mp4",
            tags=["财经"],
        ))
        db.session.commit()
        db.session.expunge_all()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.lower())

        event.listen(db.engine, "before_cursor_execute", capture)
        dummy = DummyCeleryTask()
        try:
            with patch.object(
                publisher_queue_repair,
                "_message_visible_in_workers",
                return_value=(False, True),
            ), patch.object(
                publisher_queue_repair,
                "_message_visible_in_queue",
                return_value=(False, True),
            ), patch("tasks.publisher.run_bilibili_upload_only", dummy):
                result = publisher_queue_repair.repair_stale_publisher_pending_tasks_once()
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        publication_selects = [
            statement
            for statement in statements
            if statement.lstrip().startswith("select") and "publication_tasks" in statement
        ]
        self.assertEqual(result["requeued"], 1)
        self.assertTrue(publication_selects)
        self.assertTrue(
            all("publication_tasks.params" not in statement for statement in publication_selects),
            publication_selects,
        )
        self.assertTrue(
            all("publication_tasks.result" not in statement for statement in publication_selects),
            publication_selects,
        )
        self.assertEqual(len(publication_selects), 2, publication_selects)

    def test_requeues_deferred_publisher_task_when_message_is_unacked_but_not_active(self):
        task = self._pending_publisher_task(step="deferred_capacity")
        task_id = task.id
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(True, True)), \
             patch.object(publisher_queue_repair, "run_publication_pipeline", dummy):
            result = publisher_queue_repair.repair_stale_publisher_pending_tasks_once()

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["requeued"], 1)
        task = db.session.get(PublicationTask, task_id)
        self.assertEqual(task.celery_task_id, "new-celery-id")
        repair = task.result["queue_repair"]
        self.assertEqual(repair["reason"], "pending_message_not_active_but_queue_visible")
        self.assertTrue(repair["previous_message_visible_in_queue"])

    def test_scan_excludes_paused_features(self):
        self._task(feature="content_audio_clean")
        self._task(feature="zsxq_clean_output_publish")
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_celery_message_visible", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_repair_target_for_feature", return_value=dummy):
            result = publisher_queue_repair.repair_stale_publication_tasks_once(
                excluded_features={"content_audio_clean"},
            )

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["requeued"], 1)
        self.assertEqual(result["items"][0]["feature"], "zsxq_clean_output_publish")

    def test_generic_requeue_does_not_reload_result_after_message_publish(self):
        self._task(feature="content_audio_clean", step="queued")
        db.session.remove()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select") and " from publication_tasks " in normalized:
                statements.append(normalized)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch.object(
                publisher_queue_repair,
                "_celery_message_visible",
                return_value=(False, True),
            ), patch.object(
                publisher_queue_repair,
                "_repair_target_for_feature",
                return_value=DummyCeleryTask(),
            ):
                result = publisher_queue_repair.repair_stale_publication_tasks_once()
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(1, result["requeued"])
        self.assertEqual(1, sum("publication_tasks.result" in statement for statement in statements))
        self.assertTrue(
            all(
                "publication_tasks.result" not in statement
                for statement in statements
                if "count(publication_tasks.id)" in statement
            ),
            statements,
        )

    def test_scan_does_not_load_payload_json_when_message_is_visible(self):
        self._task(feature="content_audio_clean", step="queued")
        db.session.remove()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch.object(
                publisher_queue_repair,
                "_celery_message_visible",
                return_value=(True, True),
            ):
                result = publisher_queue_repair.repair_stale_publication_tasks_once()
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        publication_selects = [
            statement for statement in statements
            if statement.startswith("select") and " from publication_tasks " in statement
        ]
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["requeued"], 0)
        self.assertEqual(len(publication_selects), 1, publication_selects)
        self.assertNotIn("publication_tasks.params", publication_selects[0])
        self.assertNotIn("publication_tasks.result", publication_selects[0])

    def test_cleans_publisher_child_when_parent_workflow_is_canceled(self):
        parent, child = self._parent_with_publisher_child(parent_status="canceled")
        parent_id = parent.id
        child_id = child.id

        with patch.object(publisher_queue_repair, "_revoke_celery_task") as revoke:
            result = publisher_queue_repair.cleanup_publisher_children_of_terminal_parents_once()

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["cleaned"], 1)
        revoke.assert_called_once()
        child = db.session.get(PublicationTask, child_id)
        self.assertEqual(child.status, "canceled")
        self.assertEqual(child.current_step, "canceled")
        self.assertEqual(child.completed_at is not None, True)
        cleanup = child.result["orphan_child_cleanup"]
        self.assertEqual(cleanup["parent_task_id"], parent_id)
        self.assertEqual(cleanup["parent_status"], "canceled")

    def test_terminal_parent_cleanup_releases_db_before_revoke(self):
        self._parent_with_publisher_child(parent_status="canceled")
        transaction_states = []

        def capture_revoke(*_args, **_kwargs):
            transaction_states.append(db.session().in_transaction())

        with patch.object(
            publisher_queue_repair,
            "_revoke_celery_task",
            side_effect=capture_revoke,
        ):
            result = (
                publisher_queue_repair.cleanup_publisher_children_of_terminal_parents_once()
            )

        self.assertEqual(result["cleaned"], 1)
        self.assertEqual(transaction_states, [False])

    def test_cleans_doubao_child_left_processing_after_parent_failed(self):
        now = datetime.utcnow()
        parent = PublicationTask(
            feature="report_pdf_audio_workflow",
            status="failed",
            current_step="failed",
            progress=69,
            params={"feature": "report_pdf_audio_workflow"},
            result={},
            completed_at=now - timedelta(minutes=45),
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(minutes=45),
        )
        db.session.add(parent)
        db.session.commit()
        child = PublicationTask(
            feature="doubao_browser_tts",
            status="processing",
            current_step="capture_audio",
            progress=72,
            parent_task_id=parent.id,
            parent_feature=parent.feature,
            params={"feature": "doubao_browser_tts", "parent_task_id": parent.id},
            result={},
            celery_task_id="interrupted-doubao-message",
            created_at=now - timedelta(minutes=55),
            updated_at=now - timedelta(minutes=50),
        )
        db.session.add(child)
        db.session.commit()
        parent_id = parent.id
        child_id = child.id

        with patch.object(publisher_queue_repair, "_revoke_celery_task") as revoke:
            result = publisher_queue_repair.cleanup_publisher_children_of_terminal_parents_once()

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["cleaned"], 1)
        revoke.assert_called_once()
        child = db.session.get(PublicationTask, child_id)
        self.assertEqual(child.status, "canceled")
        self.assertEqual(child.current_step, "canceled")
        self.assertIn("遗留子任务自动收口", child.error_message)
        cleanup = child.result["orphan_child_cleanup"]
        self.assertEqual(cleanup["parent_task_id"], parent_id)
        self.assertEqual(cleanup["parent_feature"], "report_pdf_audio_workflow")
        self.assertEqual(cleanup["previous_step"], "capture_audio")

    def test_cleans_gemini_children_for_all_gemini_parent_workflows(self):
        now = datetime.utcnow()
        children = []
        for parent_feature in ("report_script_material_workflow", "report_top10_workflow"):
            parent = PublicationTask(
                feature=parent_feature,
                status="failed",
                current_step="failed",
                progress=50,
                params={"feature": parent_feature},
                result={},
                completed_at=now - timedelta(minutes=30),
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(minutes=30),
            )
            db.session.add(parent)
            db.session.flush()
            child = PublicationTask(
                feature="report_pdf_gemini",
                status="processing",
                current_step="browser_submit",
                progress=30,
                parent_task_id=parent.id,
                parent_feature=parent_feature,
                params={
                    "feature": "report_pdf_gemini",
                    "parent_feature": parent_feature,
                    "parent_task_id": parent.id,
                },
                result={},
                celery_task_id=f"interrupted-{parent_feature}",
                created_at=now - timedelta(minutes=50),
                updated_at=now - timedelta(minutes=45),
            )
            db.session.add(child)
            children.append(child)
        db.session.commit()
        child_ids = [child.id for child in children]

        with patch.object(publisher_queue_repair, "_revoke_celery_task") as revoke:
            result = publisher_queue_repair.cleanup_publisher_children_of_terminal_parents_once()

        self.assertEqual(result["checked"], 2)
        self.assertEqual(result["cleaned"], 2)
        self.assertEqual(revoke.call_count, 2)
        for child_id in child_ids:
            child = db.session.get(PublicationTask, child_id)
            self.assertEqual(child.status, "canceled")
            self.assertEqual(child.current_step, "canceled")
            self.assertEqual(
                child.result["orphan_child_cleanup"]["parent_feature"],
                child.parent_feature,
            )

    def test_keeps_post_completion_bilibili_upload_child_when_parent_completed(self):
        _, child = self._parent_with_publisher_child(parent_status="completed")
        child.params = {
            **child.params,
            "video": {
                "enabled": False,
                "media_type": "existing_video",
                "video_path": "/tmp/done.mp4",
            },
            "bilibili": {"enabled": True, "title": "内资闭门会 | 测试 | 2026-07-07"},
            "metadata": {
                "source": "mp3_video_workflow:bilibili_upload",
                "parent_task_id": child.parent_task_id,
            },
        }
        projection = PublicationTaskBilibiliUpload(
            task_id=child.id,
            parent_task_id=child.parent_task_id,
            parent_feature="mp3_video_workflow",
            source_label="mp3_video_workflow:bilibili_upload",
            video_key="landscape",
            title="内资闭门会 | 测试 | 2026-07-07",
            video_path="/tmp/done.mp4",
            tags=["财经"],
        )
        db.session.add(projection)
        db.session.commit()

        with patch.object(publisher_queue_repair, "_revoke_celery_task") as revoke:
            result = publisher_queue_repair.cleanup_publisher_children_of_terminal_parents_once()

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["cleaned"], 0)
        revoke.assert_not_called()
        db.session.refresh(child)
        self.assertEqual(child.status, "pending")
        self.assertEqual(child.current_step, "queued")

    def test_keeps_report_audio_vertical_backfill_when_parent_completed(self):
        parent, child = self._parent_with_publisher_child(parent_status="completed")
        child.params = {
            **child.params,
            "source_task_id": parent.id,
            "vertical_video": {"enabled": True, "scheme": "scheme2"},
            "metadata": {
                "source": "report_audio_workflow_vertical_backfill",
                "parent_task_id": parent.id,
                "is_preview": False,
            },
        }
        db.session.commit()

        with patch.object(publisher_queue_repair, "_revoke_celery_task") as revoke:
            result = publisher_queue_repair.cleanup_publisher_children_of_terminal_parents_once()

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["cleaned"], 0)
        revoke.assert_not_called()
        db.session.refresh(child)
        self.assertEqual(child.status, "pending")
        self.assertEqual(child.current_step, "queued")

    def test_cleans_report_audio_vertical_backfill_when_parent_canceled(self):
        parent, child = self._parent_with_publisher_child(parent_status="canceled")
        child.params = {
            **child.params,
            "source_task_id": parent.id,
            "vertical_video": {"enabled": True, "scheme": "scheme2"},
            "metadata": {
                "source": "report_audio_workflow_vertical_backfill",
                "parent_task_id": parent.id,
                "is_preview": False,
            },
        }
        db.session.commit()
        child_id = child.id

        with patch.object(publisher_queue_repair, "_revoke_celery_task") as revoke:
            result = publisher_queue_repair.cleanup_publisher_children_of_terminal_parents_once()

        self.assertEqual(result["cleaned"], 1)
        revoke.assert_called_once()
        child = db.session.get(PublicationTask, child_id)
        self.assertEqual(child.status, "canceled")
        self.assertIn("父编排任务已终止(canceled)", child.error_message)

    def test_cancel_bilibili_upload_child_marks_pending_task_canceled(self):
        _, child = self._parent_with_publisher_child(parent_status="completed")
        child.params = {
            **child.params,
            "video": {
                "enabled": False,
                "media_type": "existing_video",
                "video_path": "/tmp/done.mp4",
            },
            "bilibili": {"enabled": True, "title": "内资闭门会 | 测试 | 2026-07-07"},
            "metadata": {
                "source": "mp3_video_workflow:bilibili_upload",
                "parent_task_id": child.parent_task_id,
            },
        }
        projection = PublicationTaskBilibiliUpload(
            task_id=child.id,
            parent_task_id=child.parent_task_id,
            parent_feature="mp3_video_workflow",
            source_label="mp3_video_workflow:bilibili_upload",
            video_key="landscape",
            title="内资闭门会 | 测试 | 2026-07-07",
            video_path="/tmp/done.mp4",
            tags=["财经"],
        )
        db.session.add(projection)
        db.session.commit()

        with patch("api.mp3_video_workflow.celery_app.control.revoke") as revoke:
            ok, message = _cancel_bilibili_upload_child(child)

        self.assertTrue(ok)
        self.assertIn("已取消", message)
        revoke.assert_called_once_with("old-render-celery-id", terminate=False)
        db.session.refresh(child)
        self.assertEqual(child.status, "canceled")
        self.assertEqual(child.current_step, "canceled")
        self.assertIsNone(child.error_message)
        db.session.refresh(projection)
        self.assertEqual(projection.cancel_reason, "user_canceled_queued_upload")
        self.assertIsNotNone(projection.canceled_at)
        self.assertNotIn("bilibili_upload_cancel_reason", child.result)

    def test_cancel_bilibili_upload_child_keeps_terminal_guard_when_revoke_fails(self):
        _, child = self._parent_with_publisher_child(parent_status="completed")
        projection = PublicationTaskBilibiliUpload(
            task_id=child.id,
            parent_task_id=child.parent_task_id,
            parent_feature="mp3_video_workflow",
            source_label="mp3_video_workflow:bilibili_upload",
            video_key="landscape",
            title="broker 不可用时仍持久取消",
            video_path="/tmp/cancel-before-revoke.mp4",
            tags=["财经"],
        )
        db.session.add(projection)
        db.session.commit()
        child_id = str(child.id)

        with patch(
            "api.mp3_video_workflow.celery_app.control.revoke",
            side_effect=RuntimeError("broker control unavailable"),
        ):
            try:
                ok, message = _cancel_bilibili_upload_child(child)
            except RuntimeError as exc:
                self.fail(f"持久化取消不能被 broker revoke 失败回滚: {exc}")

        self.assertTrue(ok)
        self.assertIn("已取消", message)
        persisted = db.session.get(PublicationTask, child_id)
        self.assertEqual(persisted.status, "canceled")
        self.assertEqual(persisted.current_step, "canceled")
        persisted_projection = PublicationTaskBilibiliUpload.query.filter_by(
            task_id=child_id,
        ).one()
        self.assertEqual(
            persisted_projection.cancel_reason,
            "user_canceled_queued_upload",
        )

    def test_cancel_bilibili_upload_child_rejects_processing_task(self):
        _, child = self._parent_with_publisher_child(parent_status="completed")
        child.status = "processing"
        child.current_step = "upload_bilibili"
        child.params = {
            **child.params,
            "video": {
                "enabled": False,
                "media_type": "existing_video",
                "video_path": "/tmp/done.mp4",
            },
            "bilibili": {"enabled": True, "title": "内资闭门会 | 测试 | 2026-07-07"},
            "metadata": {
                "source": "mp3_video_workflow:bilibili_upload",
                "parent_task_id": child.parent_task_id,
            },
        }
        db.session.commit()

        with patch("api.mp3_video_workflow.celery_app.control.revoke") as revoke:
            ok, message = _cancel_bilibili_upload_child(child)

        self.assertFalse(ok)
        self.assertIn("已开始运行", message)
        revoke.assert_not_called()
        db.session.refresh(child)
        self.assertEqual(child.status, "processing")
        self.assertEqual(child.current_step, "upload_bilibili")

    def test_keeps_publisher_child_when_parent_workflow_is_processing(self):
        _, child = self._parent_with_publisher_child(parent_status="processing")

        result = publisher_queue_repair.cleanup_publisher_children_of_terminal_parents_once()

        self.assertEqual(result["checked"], 0)
        self.assertEqual(result["cleaned"], 0)
        db.session.refresh(child)
        self.assertEqual(child.status, "pending")
        self.assertEqual(child.current_step, "queued")

    def test_requeues_stale_processing_publisher_task_when_celery_message_is_missing(self):
        task = self._processing_publisher_task(step="v_synthesis")
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "run_publication_pipeline", dummy):
            updated, repaired, action = publisher_queue_repair.maybe_repair_stale_publisher_processing_task(task)

        self.assertTrue(repaired)
        self.assertEqual(action, "requeued")
        self.assertEqual(updated.status, "pending")
        self.assertEqual(updated.current_step, "queued")
        self.assertEqual(updated.progress, 0)
        self.assertEqual(updated.celery_task_id, "new-celery-id")
        self.assertIsNone(updated.error_message)
        self.assertEqual(dummy.calls[0]["queue"], "render")
        repair = updated.result["processing_orphan_repair"]
        self.assertEqual(repair["previous_celery_id"], "old-render-celery-id")
        self.assertEqual(repair["previous_step"], "v_synthesis")

    def test_publisher_processing_repair_releases_db_before_visibility_checks(self):
        task = self._processing_publisher_task(step="v_synthesis")
        dummy = DummyCeleryTask()
        transaction_states = []

        def worker_visibility(*_args, **_kwargs):
            transaction_states.append(("worker", db.session().in_transaction()))
            return False, True

        def queue_visibility(*_args, **_kwargs):
            transaction_states.append(("queue", db.session().in_transaction()))
            return False, True

        with patch.object(
            publisher_queue_repair,
            "_message_visible_in_workers",
            side_effect=worker_visibility,
        ), patch.object(
            publisher_queue_repair,
            "_message_visible_in_queue",
            side_effect=queue_visibility,
        ), patch.object(
            publisher_queue_repair,
            "run_publication_pipeline",
            dummy,
        ):
            _updated, repaired, action = (
                publisher_queue_repair.maybe_repair_stale_publisher_processing_task(
                    task
                )
            )

        self.assertTrue(repaired)
        self.assertEqual(action, "requeued")
        self.assertEqual(transaction_states, [("worker", False), ("queue", False)])

    def test_publisher_processing_close_releases_db_before_revoke(self):
        task = self._processing_publisher_task(step="upload_bilibili")
        transaction_states = []

        def capture_revoke(*_args, **_kwargs):
            transaction_states.append(db.session().in_transaction())

        with patch.object(
            publisher_queue_repair,
            "_message_visible_in_workers",
            return_value=(False, True),
        ), patch.object(
            publisher_queue_repair,
            "_revoke_celery_task",
            side_effect=capture_revoke,
        ):
            _updated, repaired, action = (
                publisher_queue_repair.maybe_repair_stale_publisher_processing_task(
                    task
                )
            )

        self.assertTrue(repaired)
        self.assertEqual(action, "closed")
        self.assertEqual(transaction_states, [False])

    def test_processing_orphan_marker_persists_before_direct_revoke(self):
        task = self._processing_publisher_task(step="upload_bilibili")
        task_id = str(task.id)
        transaction_states = []
        durable_statuses = []

        def capture_revoke(*_args, **_kwargs):
            transaction_states.append(db.session().in_transaction())
            durable_statuses.append(
                db.session.get(PublicationTask, task_id).status
            )

        with patch.object(
            publisher_queue_repair.celery_app.control,
            "revoke",
            side_effect=capture_revoke,
        ):
            publisher_queue_repair._mark_processing_publisher_orphan_failed(
                task,
                queue="platform_upload",
                reason="direct_marker_test",
            )

        self.assertEqual(transaction_states, [False])
        self.assertEqual(durable_statuses, ["failed"])
        persisted = db.session.get(PublicationTask, task_id)
        self.assertEqual(persisted.status, "failed")
        self.assertEqual(persisted.current_step, "orphaned_processing")

    def test_requeues_stale_processing_local_clean_task_when_celery_message_is_missing(self):
        cases = (
            ("content_pdf_clean", "local_cleaning"),
            ("content_audio_clean", "cleaning"),
        )
        for feature, step in cases:
            with self.subTest(feature=feature, step=step):
                task = self._processing_business_task(feature=feature, step=step)
                dummy = DummyCeleryTask()

                with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
                     patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(False, True)), \
                     patch.object(publisher_queue_repair, "_repair_target_for_feature", return_value=dummy):
                    updated, repaired, action = publisher_queue_repair.maybe_repair_stale_business_processing_task(task)

                self.assertTrue(repaired)
                self.assertEqual(action, "requeued")
                self.assertEqual(updated.status, "pending")
                self.assertEqual(updated.current_step, "queued")
                self.assertEqual(updated.progress, 0)
                self.assertEqual(updated.celery_task_id, "new-celery-id")
                self.assertEqual(dummy.calls[0]["queue"], "content_pipeline")
                repair = updated.result["processing_orphan_repair"]
                self.assertEqual(repair["previous_celery_id"], "old-business-celery-id")
                self.assertEqual(repair["previous_step"], step)

    def test_requeues_stale_processing_report_audio_parent_when_message_is_missing(self):
        cases = (
            ("report_audio_workflow", "doubao_2_reuse"),
            ("report_pdf_audio_workflow", "pub_synthesis"),
        )
        for feature, step in cases:
            with self.subTest(feature=feature, step=step):
                task = self._processing_business_task(feature=feature, step=step)
                dummy = DummyCeleryTask()

                with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
                     patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(False, True)), \
                     patch.object(publisher_queue_repair, "_repair_target_for_feature", return_value=dummy):
                    updated, repaired, action = publisher_queue_repair.maybe_repair_stale_business_processing_task(task)

                self.assertTrue(repaired)
                self.assertEqual(action, "requeued")
                self.assertEqual(updated.status, "pending")
                self.assertEqual(updated.current_step, "queued")
                self.assertEqual(dummy.calls[0]["queue"], "report_audio_workflow")

    def test_age_seconds_normalizes_naive_database_local_time(self):
        now_utc = datetime(2026, 8, 5, 8, 12, 0)
        now_local = datetime(2026, 8, 5, 16, 12, 0)

        age = publisher_queue_repair._age_seconds(
            datetime(2026, 8, 5, 15, 58, 0),
            now_utc=now_utc,
            now_local=now_local,
        )

        self.assertEqual(age, 14 * 60)

    def test_requeues_stale_processing_browser_children_when_message_is_missing(self):
        cases = (
            ("doubao_browser_tts", "wait_response", "doubao_browser_tts"),
            ("report_pdf_gemini", "wait_response", "report_pdf_gemini"),
            ("report_pdf_gemini", "response_contract_repair_1_wait", "report_pdf_gemini"),
            ("gemini_browser", "upload_pdf", "report_pdf_gemini"),
            ("report_visual_plan", "gemini_storyboard", "report_visual_plan"),
            ("report_stick_figure_image", "wait_generated_image", "report_pdf_gemini"),
            ("report_stick_figure_audio_alignment", "transcribe_whole_audio", "render"),
            ("report_stick_figure_video_render", "render_video", "render"),
        )
        for feature, step, queue in cases:
            with self.subTest(feature=feature, step=step):
                task = self._processing_business_task(feature=feature, step=step)
                dummy = DummyCeleryTask()

                with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
                     patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(False, True)), \
                     patch.object(publisher_queue_repair, "_repair_target_for_feature", return_value=dummy):
                    updated, repaired, action = publisher_queue_repair.maybe_repair_stale_business_processing_task(task)

                self.assertTrue(repaired)
                self.assertEqual(action, "requeued")
                self.assertEqual(updated.status, "pending")
                self.assertEqual(updated.current_step, "queued")
                self.assertEqual(dummy.calls[0]["queue"], queue)

    def test_processing_repair_demotes_when_original_message_is_still_queued(self):
        task = self._processing_business_task(
            feature="doubao_browser_tts",
            step="wait_response",
        )
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(True, True)), \
             patch.object(publisher_queue_repair, "_repair_target_for_feature", return_value=dummy):
            updated, repaired, action = publisher_queue_repair.maybe_repair_stale_business_processing_task(task)

        self.assertTrue(repaired)
        self.assertEqual(action, "demoted")
        self.assertEqual(updated.status, "pending")
        self.assertEqual(updated.current_step, "queued")
        self.assertEqual(updated.celery_task_id, "old-business-celery-id")
        self.assertEqual(dummy.calls, [])
        self.assertEqual(
            updated.result["processing_orphan_repair"]["reason"],
            "processing_not_active_but_message_visible",
        )

    def test_processing_repair_releases_db_transaction_before_external_checks(self):
        task = self._processing_business_task(
            feature="doubao_browser_tts",
            step="wait_response",
        )
        dummy = DummyCeleryTask()
        transaction_states = []

        def worker_visibility(*_args, **_kwargs):
            transaction_states.append(("worker", db.session().in_transaction()))
            return False, True

        def queue_visibility(*_args, **_kwargs):
            transaction_states.append(("queue", db.session().in_transaction()))
            return False, True

        with patch.object(publisher_queue_repair, "_message_visible_in_workers", side_effect=worker_visibility), \
             patch.object(publisher_queue_repair, "_message_visible_in_queue", side_effect=queue_visibility), \
             patch.object(publisher_queue_repair, "_repair_target_for_feature", return_value=dummy):
            _updated, repaired, action = publisher_queue_repair.maybe_repair_stale_business_processing_task(task)

        self.assertTrue(repaired)
        self.assertEqual(action, "requeued")
        self.assertEqual(transaction_states, [("worker", False), ("queue", False)])

    def test_does_not_requeue_clean_task_after_publish_handoff_begins(self):
        task = self._processing_business_task(
            feature="content_pdf_clean",
            step="enqueue_publish",
        )
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_repair_target_for_feature", return_value=dummy):
            updated, repaired, action = publisher_queue_repair.maybe_repair_stale_business_processing_task(task)

        self.assertFalse(repaired)
        self.assertEqual(action, "step_not_requeueable")
        self.assertEqual(updated.status, "processing")
        self.assertEqual(updated.current_step, "enqueue_publish")
        self.assertEqual(dummy.calls, [])

    def test_closes_stale_bilibili_upload_processing_task_on_platform_upload_queue(self):
        task = self._processing_publisher_task(step="upload_bilibili")
        task.params = {
            "feature": "publisher",
            "video": {
                "enabled": False,
                "media_type": "existing_video",
                "video_path": "/tmp/done.mp4",
            },
            "bilibili": {"enabled": True, "title": "内资闭门会 | 测试 | 2026-07-07"},
            "metadata": {
                "source": "mp3_video_workflow:bilibili_upload",
                "parent_task_id": "parent-1",
            },
        }
        task.updated_at = datetime.utcnow() - timedelta(minutes=20)
        db.session.commit()

        with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_revoke_celery_task"), \
             patch.object(publisher_queue_repair, "_message_visible_in_queue") as queue_scan:
            updated, repaired, action = publisher_queue_repair.maybe_repair_stale_publisher_processing_task(task)

        self.assertTrue(repaired)
        self.assertEqual(action, "closed")
        self.assertEqual(updated.status, "failed")
        self.assertEqual(updated.current_step, "orphaned_processing")
        self.assertEqual(updated.result["processing_orphan_repair"]["queue"], "platform_upload")
        queue_scan.assert_not_called()

    def test_closes_upload_processing_task_after_short_upload_grace(self):
        task = self._processing_publisher_task(step="upload_bilibili")
        task.params = {
            "feature": "publisher",
            "video": {
                "enabled": False,
                "media_type": "existing_video",
                "video_path": "/tmp/done.mp4",
            },
            "bilibili": {"enabled": True, "title": "内资闭门会 | 测试 | 2026-07-07"},
            "metadata": {
                "source": "mp3_video_workflow:bilibili_upload",
                "parent_task_id": "parent-1",
            },
        }
        task.updated_at = datetime.utcnow() - timedelta(minutes=2)
        db.session.commit()

        with patch.dict(os.environ, {
            "PUBLISHER_PROCESSING_ORPHAN_GRACE_SECONDS": "600",
            "PUBLISHER_UPLOAD_PROCESSING_ORPHAN_GRACE_SECONDS": "90",
        }), \
             patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_revoke_celery_task"):
            updated, repaired, action = publisher_queue_repair.maybe_repair_stale_publisher_processing_task(task)

        self.assertTrue(repaired)
        self.assertEqual(action, "closed")
        self.assertEqual(updated.status, "failed")
        self.assertEqual(updated.result["processing_orphan_repair"]["reason"], "upload_step_not_requeued")

    def test_does_not_repair_processing_publisher_task_when_celery_message_is_visible(self):
        task = self._processing_publisher_task(step="v_synthesis")
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(True, True)), \
             patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "run_publication_pipeline", dummy):
            updated, repaired, action = publisher_queue_repair.maybe_repair_stale_publisher_processing_task(task)

        self.assertFalse(repaired)
        self.assertEqual(action, "visible_in_worker")
        self.assertEqual(updated.status, "processing")
        self.assertEqual(dummy.calls, [])

    def test_demotes_processing_publisher_task_when_message_is_queued_but_not_active(self):
        task = self._processing_publisher_task(step="prepare")
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(True, True)), \
             patch.object(publisher_queue_repair, "run_publication_pipeline", dummy):
            updated, repaired, action = publisher_queue_repair.maybe_repair_stale_publisher_processing_task(task)

        self.assertTrue(repaired)
        self.assertEqual(action, "demoted")
        self.assertEqual(updated.status, "pending")
        self.assertEqual(updated.current_step, "queued")
        self.assertEqual(updated.celery_task_id, "old-render-celery-id")
        self.assertEqual(dummy.calls, [])
        repair = updated.result["processing_orphan_repair"]
        self.assertEqual(repair["reason"], "processing_not_active_but_message_visible")

    def test_closes_stale_upload_processing_publisher_task_without_requeue(self):
        task = self._processing_publisher_task(step="upload")
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(True, True)), \
             patch.object(publisher_queue_repair, "_revoke_celery_task") as revoke, \
             patch.object(publisher_queue_repair, "run_publication_pipeline", dummy):
            updated, repaired, action = publisher_queue_repair.maybe_repair_stale_publisher_processing_task(task)

        self.assertTrue(repaired)
        self.assertEqual(action, "closed")
        self.assertEqual(updated.status, "failed")
        self.assertEqual(updated.current_step, "orphaned_processing")
        self.assertIn("释放并发槽位", updated.error_message)
        self.assertEqual(dummy.calls, [])
        revoke.assert_called_once()


if __name__ == "__main__":
    unittest.main()
