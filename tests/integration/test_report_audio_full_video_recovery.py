import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.bilibili_publish import PublicationTaskBilibiliUpload  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services import report_audio_parent_wakeup, runtime_guard  # noqa: E402
from services.report_audio_completion_contract import (  # noqa: E402
    evaluate_report_video_completion,
)
from tasks import report_audio_workflow  # noqa: E402


class ReportAudioFullVideoRecoveryIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        runtime_guard._DB_PRESSURE_CACHE.update(expires_at=0.0, snapshot=None)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.tmpdir.cleanup()

    def _file(self, name, payload=b"artifact"):
        path = self.root / name
        path.write_bytes(payload)
        return str(path)

    def _parent_and_publisher(self, index):
        wav_path = self._file(f"audio-{index}.wav")
        scene_path = self._file(f"scene-{index}.png")
        child = PublicationTask(
            feature="publisher",
            status="processing",
            current_step="v_synthesis",
            progress=70,
            params={
                "feature": "publisher",
                "video": {"enabled": True},
                "vertical_video": {"enabled": True, "scheme": "scheme3"},
                "bilibili": {"enabled": False},
            },
            result={},
        )
        db.session.add(child)
        db.session.flush()
        waiting = {
            "label": "publisher_video",
            "child_id": str(child.id),
            "status": "processing",
            "step": "v_synthesis",
            "updated_at": f"2026-08-10T02:0{index}:00Z",
        }
        parent = PublicationTask(
            feature="report_pdf_audio_workflow",
            celery_task_id=f"delayed-parent-{index}",
            status="processing",
            current_step="pub_waiting",
            progress=95,
            params={
                "feature": "report_pdf_audio_workflow",
                "report_audio_workflow": {
                    "video": {
                        "enabled": True,
                        "vertical_enabled": True,
                        "vertical_video_schemes": ["scheme3"],
                        "bilibili_enabled": False,
                    }
                },
            },
            result={
                "structured_result": {
                    "final_script_for_tts": f"复用文案 {index}" * 20,
                },
                "script_material": {
                    "mode": "reused_script_material",
                    "source_task_id": f"script-source-{index}",
                },
                "audio": {"wav_path": wav_path, "seconds": 30.0},
                "audio_reuse": {
                    "reused": True,
                    "source_task_id": f"audio-source-{index}",
                },
                "visual_assets": {
                    "reused": True,
                    "scene_paths": [scene_path],
                },
                "waiting_child": waiting,
                "children": {"publisher_video": {"job_id": str(child.id)}},
            },
        )
        db.session.add(parent)
        db.session.flush()
        child.source_task_id = str(parent.id)
        child.params = {
            **dict(child.params or {}),
            "source_task_id": str(parent.id),
            "metadata": {"source_task_id": str(parent.id)},
        }
        db.session.commit()
        return parent, child, wav_path, scene_path

    @staticmethod
    def _probe(path):
        if str(path).endswith(".wav"):
            return {"duration": 30.0, "video_streams": 0, "audio_streams": 1}
        return {"duration": 30.0, "video_streams": 1, "audio_streams": 1}

    def test_three_passive_parents_recover_once_without_publish_dispatch(self):
        fixtures = [self._parent_and_publisher(index) for index in range(1, 4)]

        admission = runtime_guard.check_publication_task_admission(
            "report_pdf_audio_workflow",
            mode="start",
        )
        self.assertTrue(admission["allowed"], admission)
        self.assertEqual(admission["active_count"], 0)
        self.assertEqual(admission["limit"], 2)

        broker_ids = iter(f"immediate-parent-{index}" for index in range(1, 4))

        def dispatch(_callable, **kwargs):
            return {
                "async_result": SimpleNamespace(id=kwargs["broker_task_id"]),
                "admission": {"allowed": True},
                "deferred": False,
            }

        with patch.object(
            report_audio_parent_wakeup,
            "uuid4",
            side_effect=lambda: next(broker_ids),
        ), patch.object(
            report_audio_parent_wakeup,
            "maybe_guarded_apply_async",
            side_effect=dispatch,
        ) as publish_resume:
            for index, (parent, child, wav_path, scene_path) in enumerate(fixtures, start=1):
                landscape = self._file(f"landscape-{index}.mp4")
                vertical = self._file(f"scheme3-{index}.mp4")
                child.status = "completed"
                child.current_step = "done"
                child.progress = 100
                child.task_video_path = landscape
                child.publisher_vertical_video_path = vertical
                child.result = {
                    "video_path": landscape,
                    "vertical_video_path": vertical,
                    "vertical_video_paths": {"scheme3": vertical},
                }
                db.session.commit()

                first = report_audio_parent_wakeup.wake_report_audio_parent_for_child(child.id)
                duplicate = report_audio_parent_wakeup.wake_report_audio_parent_for_child(child.id)
                self.assertTrue(first["woken"])
                self.assertFalse(duplicate["woken"])
                self.assertEqual(duplicate["reason"], "already_woken")

                completed_child = report_audio_workflow._wait_child(
                    str(parent.id),
                    str(child.id),
                    "publisher_video",
                    start_progress=95,
                    end_progress=99,
                )
                self.assertEqual(completed_child.status, "completed")
                contract = evaluate_report_video_completion(
                    include_landscape=True,
                    vertical_schemes=["scheme3"],
                    landscape_path=landscape,
                    vertical_paths={"scheme3": vertical},
                    audio_path=wav_path,
                    probe=self._probe,
                )
                self.assertTrue(contract["ready"], contract)

                refreshed = db.session.get(PublicationTask, parent.id)
                result = dict(refreshed.result or {})
                result["completion_contract"] = contract
                refreshed.result = result
                refreshed.status = "completed"
                refreshed.current_step = "done"
                refreshed.progress = 100
                db.session.commit()
                db.session.refresh(refreshed)

                self.assertIsNone(refreshed.waiting_child_id)
                self.assertNotIn("waiting_child", refreshed.result)
                self.assertEqual(refreshed.result["audio"]["wav_path"], wav_path)
                self.assertEqual(refreshed.result["visual_assets"]["scene_paths"], [scene_path])
                self.assertTrue(refreshed.result["completion_contract"]["ready"])
                self.assertEqual(
                    PublicationTask.query.filter_by(
                        feature="publisher",
                        source_task_id=str(parent.id),
                    ).count(),
                    1,
                )

        self.assertEqual(publish_resume.call_count, 3)
        self.assertEqual(PublicationTaskBilibiliUpload.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
