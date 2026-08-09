import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from sqlalchemy import event


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import ReportStickFigureSceneAsset, ReportVisualPlanSegment, db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from tasks import doubao_browser_tts  # noqa: E402


class RetryScheduled(RuntimeError):
    pass


class FakeCeleryTask:
    def __init__(self, retries=0):
        self.request = SimpleNamespace(retries=retries)
        self.retry_kwargs = None

    def retry(self, **kwargs):
        self.retry_kwargs = kwargs
        raise RetryScheduled()


class DoubaoBrowserTTSTaskTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _create_task(self):
        text = "用一句话说明 AI 服务器为什么需要更高效的散热系统。" * 8
        task = PublicationTask(
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": "doubao_browser_tts",
                "doubao_browser_tts": {
                    "text": text,
                    "metadata": {"text_hash": "hash-1"},
                },
            },
            result={"metadata": {"text_hash": "hash-1"}, "audio": None, "logs": []},
        )
        db.session.add(task)
        db.session.commit()
        return task

    def test_missing_pcm_audio_is_retryable(self):
        error = RuntimeError("朗读已触发但未捕获到 PCM 音频")

        self.assertTrue(doubao_browser_tts._is_retryable_error(error))

    def _attach_scene_asset(self, task):
        payload = dict(task.params["doubao_browser_tts"])
        metadata = dict(payload.get("metadata") or {})
        asset = ReportStickFigureSceneAsset(
            workflow_task_id="workflow-1",
            storyboard_task_id="storyboard-1",
            segment_id="segment-1",
            sequence=1,
            script_text_hash="script-hash",
            visual_prompt_hash="prompt-hash",
            tts_task_id=str(task.id),
            tts_status="queued",
        )
        db.session.add(asset)
        db.session.flush()
        metadata["scene_asset_id"] = asset.id
        payload["metadata"] = metadata
        task.params = {**task.params, "doubao_browser_tts": payload}
        db.session.commit()
        return asset

    def test_short_audio_failure_auto_retries_before_final_failure(self):
        task = self._create_task()
        celery_task = FakeCeleryTask(retries=0)
        worker = SimpleNamespace(
            synthesize=lambda **kwargs: {"wav_path": "/tmp/short.wav", "seconds": 0.447, "bytes": 1024}
        )

        with patch.object(doubao_browser_tts, "DoubaoBrowserTTSWorker", return_value=worker), \
            patch.dict("os.environ", {"DOUBAO_BROWSER_TTS_RETRY_LIMIT": "2", "DOUBAO_BROWSER_TTS_RETRY_DELAY_SECONDS": "7"}, clear=False):
            with self.assertRaises(RetryScheduled):
                doubao_browser_tts._process_job(task.id, celery_task=celery_task)

        self.assertEqual(celery_task.retry_kwargs["countdown"], 7)
        self.assertEqual(celery_task.retry_kwargs["max_retries"], 2)
        db.session.refresh(task)
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.current_step, "auto_retry")
        self.assertIsNone(task.error_message)
        self.assertEqual(task.result["retry_state"]["retry_number"], 1)
        self.assertIn("豆包音频时长异常", task.result["last_error"])

    def test_short_audio_failure_marks_failed_after_retry_limit(self):
        task = self._create_task()
        celery_task = FakeCeleryTask(retries=2)
        worker = SimpleNamespace(
            synthesize=lambda **kwargs: {"wav_path": "/tmp/short.wav", "seconds": 0.447, "bytes": 1024}
        )

        with patch.object(doubao_browser_tts, "DoubaoBrowserTTSWorker", return_value=worker), \
            patch.dict("os.environ", {"DOUBAO_BROWSER_TTS_RETRY_LIMIT": "2"}, clear=False):
            ok = doubao_browser_tts._process_job(task.id, celery_task=celery_task)

        self.assertFalse(ok)
        db.session.refresh(task)
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.current_step, "failed")
        self.assertIn("豆包音频时长异常", task.error_message)

    def test_progress_logs_are_buffered_until_terminal_write(self):
        task = self._create_task()
        interim_logs = []

        def synthesize(**kwargs):
            callback = kwargs["progress_callback"]
            callback({
                "step": "submit_text",
                "message": "提交文本",
                "progress": 25,
                "updated_at": "2026-07-18T03:00:00Z",
            })
            db.session.expire_all()
            interim = PublicationTask.query.filter(PublicationTask.id == task.id).one()
            interim_logs.extend((interim.result or {}).get("logs") or [])
            callback({
                "step": "save_file",
                "message": "WAV 已保存",
                "progress": 96,
                "updated_at": "2026-07-18T03:00:01Z",
                "result": {"page_url": "https://example.test/doubao"},
            })
            return {"wav_path": "/tmp/final.wav", "seconds": 12.5, "bytes": 2048}

        worker = SimpleNamespace(synthesize=synthesize)
        with patch.object(doubao_browser_tts, "DoubaoBrowserTTSWorker", return_value=worker), patch.object(
            doubao_browser_tts,
            "validate_audio_duration_for_text",
            side_effect=lambda audio, _text: audio,
        ):
            ok = doubao_browser_tts._process_job(task.id)

        self.assertTrue(ok)
        self.assertEqual(interim_logs, [])
        db.session.refresh(task)
        self.assertEqual(task.status, "completed")
        self.assertEqual([item["step"] for item in task.result["logs"]], ["submit_text", "save_file"])
        self.assertEqual(task.result["page_url"], "https://example.test/doubao")
        self.assertEqual(task.result["audio"]["wav_path"], "/tmp/final.wav")

    def test_scene_progress_updates_projection_without_rewriting_task_row(self):
        task = self._create_task()
        asset = self._attach_scene_asset(task)
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("update"):
                statements.append(normalized)

        def synthesize(**kwargs):
            callback = kwargs["progress_callback"]
            for step, progress in (("submit_text", 25), ("wait_response", 60), ("capture_audio", 85)):
                callback({"step": step, "message": step, "progress": progress})
            return {"wav_path": "/tmp/scene.wav", "seconds": 12.5, "bytes": 2048}

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch.object(
                doubao_browser_tts,
                "DoubaoBrowserTTSWorker",
                return_value=SimpleNamespace(synthesize=synthesize),
            ), patch.object(
                doubao_browser_tts,
                "validate_audio_duration_for_text",
                side_effect=lambda audio, _text: audio,
            ):
                ok = doubao_browser_tts._process_job(task.id)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertTrue(ok)
        task_updates = [sql for sql in statements if sql.startswith("update publication_tasks")]
        asset_updates = [sql for sql in statements if sql.startswith("update report_stick_figure_scene_assets")]
        self.assertEqual(2, len(task_updates), statements)
        self.assertEqual(5, len(asset_updates), statements)
        db.session.refresh(task)
        db.session.refresh(asset)
        self.assertEqual("completed", task.status)
        self.assertEqual("completed", asset.tts_status)
        self.assertEqual(100, asset.tts_progress)
        self.assertEqual(
            ["submit_text", "wait_response", "capture_audio"],
            [item["step"] for item in task.result["logs"]],
        )

    def test_canceled_progress_failure_reloads_state_without_result_json(self):
        task = self._create_task()
        task_id = str(task.id)
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().lower().startswith("select") and "publication_tasks" in statement:
                statements.append(statement.lower())

        def synthesize(**kwargs):
            PublicationTask.query.filter(PublicationTask.id == task_id).update(
                {PublicationTask.status: "canceled"},
                synchronize_session=False,
            )
            db.session.commit()
            kwargs["progress_callback"]({
                "step": "clear_audio_cache",
                "message": "清空插件音频缓存",
                "progress": 12,
                "updated_at": "2026-07-18T08:51:09Z",
            })

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch.object(
                doubao_browser_tts,
                "DoubaoBrowserTTSWorker",
                return_value=SimpleNamespace(synthesize=synthesize),
            ):
                ok = doubao_browser_tts._process_job(task_id)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertFalse(ok)
        self.assertEqual(len(statements), 1)
        self.assertIn("publication_tasks.result", statements[0])

    def test_text_reference_reads_storyboard_segment_without_task_json_copy(self):
        storyboard = PublicationTask(
            status="completed",
            params={"feature": "report_visual_plan"},
            result={},
        )
        db.session.add(storyboard)
        db.session.flush()
        segment = ReportVisualPlanSegment(
            task_id=storyboard.id,
            sequence=1,
            scene_title="第一段",
            script_text="只从分镜表读取这一段正文。",
            visual_prompt="English visual prompt",
        )
        db.session.add(segment)
        db.session.commit()

        text = doubao_browser_tts._text_from_reference({
            "kind": "report_visual_plan_segment",
            "segment_id": segment.id,
        })

        self.assertEqual(text, "只从分镜表读取这一段正文。")

    def test_worker_state_is_projected_to_the_linked_scene_asset(self):
        task = self._create_task()
        asset = ReportStickFigureSceneAsset(
            id="asset-1",
            workflow_task_id="workflow-1",
            storyboard_task_id="storyboard-1",
            segment_id="segment-1",
            sequence=1,
            script_text_hash="script-hash",
            visual_prompt_hash="prompt-hash",
            tts_task_id=task.id,
            tts_status="queued",
            tts_progress=0,
        )
        db.session.add(asset)
        db.session.commit()

        updated = doubao_browser_tts._update_scene_asset_tts_state(
            asset.id,
            task.id,
            status="completed",
            progress=100,
            error=None,
            audio_path="/tmp/final.wav",
            audio_bytes=2048,
            audio_seconds=12.5,
        )
        db.session.commit()
        db.session.refresh(asset)

        self.assertEqual(1, updated)
        self.assertEqual("completed", asset.tts_status)
        self.assertEqual(100, asset.tts_progress)
        self.assertEqual("/tmp/final.wav", asset.audio_path)
        self.assertEqual(2048, asset.audio_bytes)
        self.assertEqual(12.5, asset.audio_seconds)


if __name__ == "__main__":
    unittest.main()
