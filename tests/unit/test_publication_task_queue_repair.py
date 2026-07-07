import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
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

    def tearDown(self):
        db.session.remove()
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

    def test_requeues_deferred_publisher_task_after_message_vanishes(self):
        task = self._pending_publisher_task(step="deferred_capacity")
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "run_publication_pipeline", dummy):
            result = publisher_queue_repair.repair_stale_publisher_pending_tasks_once()

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["requeued"], 1)
        db.session.refresh(task)
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.current_step, "queued")
        self.assertEqual(task.celery_task_id, "new-celery-id")
        self.assertIsNone(task.error_message)

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
        db.session.commit()
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(False, True)) as queue_scan, \
             patch("tasks.publisher.run_bilibili_upload_only", dummy):
            updated, requeued = publisher_queue_repair.maybe_requeue_stale_publisher_child(child)

        self.assertTrue(requeued)
        self.assertEqual(updated.celery_task_id, "new-celery-id")
        self.assertEqual(dummy.calls[0]["queue"], "platform_upload")
        self.assertEqual(queue_scan.call_args.kwargs["queue"], "platform_upload")
        self.assertEqual(updated.result["queue_repair"]["queue"], "platform_upload")

    def test_requeues_deferred_publisher_task_when_message_is_unacked_but_not_active(self):
        task = self._pending_publisher_task(step="deferred_capacity")
        dummy = DummyCeleryTask()

        with patch.object(publisher_queue_repair, "_message_visible_in_workers", return_value=(False, True)), \
             patch.object(publisher_queue_repair, "_message_visible_in_queue", return_value=(True, True)), \
             patch.object(publisher_queue_repair, "run_publication_pipeline", dummy):
            result = publisher_queue_repair.repair_stale_publisher_pending_tasks_once()

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["requeued"], 1)
        db.session.refresh(task)
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

    def test_cleans_publisher_child_when_parent_workflow_is_canceled(self):
        parent, child = self._parent_with_publisher_child(parent_status="canceled")

        with patch.object(publisher_queue_repair, "_revoke_celery_task") as revoke:
            result = publisher_queue_repair.cleanup_publisher_children_of_terminal_parents_once()

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["cleaned"], 1)
        revoke.assert_called_once()
        db.session.refresh(child)
        self.assertEqual(child.status, "canceled")
        self.assertEqual(child.current_step, "canceled")
        self.assertEqual(child.completed_at is not None, True)
        cleanup = child.result["orphan_child_cleanup"]
        self.assertEqual(cleanup["parent_task_id"], parent.id)
        self.assertEqual(cleanup["parent_status"], "canceled")

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
        db.session.commit()

        with patch.object(publisher_queue_repair, "_revoke_celery_task") as revoke:
            result = publisher_queue_repair.cleanup_publisher_children_of_terminal_parents_once()

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["cleaned"], 0)
        revoke.assert_not_called()
        db.session.refresh(child)
        self.assertEqual(child.status, "pending")
        self.assertEqual(child.current_step, "queued")

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
        self.assertEqual(child.result["bilibili_upload_cancel_reason"], "user_canceled_queued_upload")

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

        self.assertEqual(result["checked"], 1)
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
