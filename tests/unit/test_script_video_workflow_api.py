import io
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from sqlalchemy import event


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import script_video_workflow as api  # noqa: E402
from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services import script_video_workflow as service  # noqa: E402
from tasks.script_video_workflow import _scene_content_hash  # noqa: E402


class ScriptVideoWorkflowApiTest(unittest.TestCase):
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
        self.tmp = tempfile.TemporaryDirectory()
        self.user = SimpleNamespace(id="user-1")

    def tearDown(self):
        self.tmp.cleanup()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def make_workflow(self, *, text="第一句。第二句。", scenes=None):
        source = service.normalize_script(text)
        scenes = scenes or [
            {"sequence": 1, "script_text": "第一句。", "image_prompt": "画面一"},
            {"sequence": 2, "script_text": "第二句。", "image_prompt": "画面二"},
        ]
        task = PublicationTask(
            status="processing",
            current_step=service.AUDIO_STEP,
            progress=40,
            params={
                "feature": service.FEATURE_NAME,
                "user_id": self.user.id,
                "visual_scheme_key": "stick_figure",
                service.FEATURE_NAME: {
                    "source_text": source,
                    "source_text_hash": service.text_hash(source),
                    "audio_chunks": ["第一句。", "第二句。"],
                    "scenes": scenes,
                },
            },
            result={"audio": self.make_wav_payload(), "images": {}, "video": None, "logs": []},
        )
        db.session.add(task)
        db.session.commit()
        return task

    def make_wav_payload(self):
        path = Path(self.tmp.name) / "audio.wav"
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(b"\x00\x00" * 16000)
        return {"wav_path": str(path), "seconds": 1.0, "bytes": path.stat().st_size}

    def wav_bytes(self, *, seconds=1, sample_rate=16000):
        output = io.BytesIO()
        with wave.open(output, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(sample_rate)
            writer.writeframes(b"\x00\x00" * int(seconds * sample_rate))
        return output.getvalue()

    def make_pending_workflow(self, *, text="第一句。第二句。"):
        source = service.normalize_script(text)
        task = PublicationTask(
            status="pending",
            current_step="script_ready",
            progress=5,
            params={
                "feature": service.FEATURE_NAME,
                "user_id": self.user.id,
                "visual_scheme_key": "stick_figure",
                service.FEATURE_NAME: {
                    "source_text": source,
                    "source_text_hash": service.text_hash(source),
                    "audio_chunks": [source],
                    "scenes": [],
                    "visual_scheme_key": "stick_figure",
                },
            },
            result={"audio": {"chunks_total": 1, "chunks_completed": 0}, "images": {}, "video": None, "logs": []},
        )
        db.session.add(task)
        db.session.commit()
        return task

    def make_image_children(self, workflow):
        children = []
        for scene in workflow.params[service.FEATURE_NAME]["scenes"]:
            image_path = Path(self.tmp.name) / f"scene-{workflow.id}-{scene['sequence']}.png"
            image_path.write_bytes(b"image")
            child = PublicationTask(
                status="completed",
                current_step="completed",
                progress=100,
                params={
                    "feature": service.IMAGE_FEATURE_NAME,
                    "user_id": self.user.id,
                    "parent_feature": service.FEATURE_NAME,
                    "parent_task_id": workflow.id,
                    service.IMAGE_FEATURE_NAME: {
                        "sequence": scene["sequence"],
                        "scene_hash": _scene_content_hash(scene, "stick_figure"),
                        "attempt": 1,
                    },
                },
                result={
                    "metadata": {
                        "sequence": scene["sequence"],
                        "scene_hash": _scene_content_hash(scene, "stick_figure"),
                    },
                    "image": {
                        "path": str(image_path),
                        "sequence": scene["sequence"],
                        "scene_hash": _scene_content_hash(scene, "stick_figure"),
                    },
                },
            )
            db.session.add(child)
            children.append(child)
        db.session.commit()
        return children

    def publication_selects_during(self, callback):
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
            normalized = " ".join(str(statement).lower().split())
            if normalized.startswith("select") and "publication_tasks" in normalized:
                statements.append(normalized)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            result = callback()
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        return result, statements

    def test_create_job_returns_editable_audio_and_scene_drafts(self):
        with self.app.test_request_context(
            "/api/script-video-workflows/jobs",
            method="POST",
            json={"text": "第一句。第二句。", "visual_scheme_key": "stick_figure"},
        ):
            response = api.create_job.__wrapped__(self.user)
        body = response.get_json()

        self.assertTrue(body["success"])
        self.assertTrue(body["data"]["audio_chunks"])
        self.assertEqual([], body["data"]["scenes"])
        self.assertEqual("not_submitted", body["data"]["storyboard"]["status"])
        self.assertEqual("stick_figure", body["data"]["visual_scheme"]["key"])

    def test_submit_storyboard_queues_gemini_planning_before_images(self):
        workflow = self.make_workflow()
        with patch.object(
            api,
            "maybe_guarded_apply_async",
            return_value={"async_result": SimpleNamespace(id="storyboard-celery-1")},
        ) as enqueue:
            with self.app.test_request_context(
                f"/api/script-video-workflows/jobs/{workflow.id}/storyboard",
                method="POST",
                json={},
            ):
                response = api.submit_storyboard.__wrapped__(self.user, workflow.id)

        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertIn("Gemini", body["message"])
        enqueue.assert_called_once_with(
            api.generate_storyboard,
            args=[workflow.id],
            queue="report_pdf_gemini",
            feature=service.FEATURE_NAME,
            task_id=workflow.id,
            retry=False,
        )
        fresh = db.session.get(PublicationTask, workflow.id)
        self.assertEqual("storyboard_queued", fresh.current_step)
        self.assertEqual("storyboard_queued", fresh.result["stage"])

    def test_upload_audio_queues_normalization_and_keeps_storyboard_locked(self):
        """Catch a regression where an upload is treated as finalized audio."""

        workflow = self.make_pending_workflow()
        with patch.dict(os.environ, {"SCRIPT_VIDEO_WORKFLOW_OUTPUT_ROOT": self.tmp.name}):
            with patch.object(
                api,
                "maybe_guarded_apply_async",
                return_value={"async_result": SimpleNamespace(id="audio-upload-celery-1")},
            ) as enqueue:
                with self.app.test_request_context(
                    f"/api/script-video-workflows/jobs/{workflow.id}/audio/upload",
                    method="POST",
                    data={"audio": (io.BytesIO(self.wav_bytes(seconds=2)), "配音.wav")},
                    content_type="multipart/form-data",
                ):
                    response = api.upload_audio.__wrapped__(self.user, workflow.id)

            body = response.get_json()
            self.assertTrue(body["success"])
            self.assertEqual("audio_upload_queued", body["data"]["workflow"]["step"])
            fresh = db.session.get(PublicationTask, workflow.id)
            audio = fresh.result["audio"]
            self.assertEqual("upload_queued", audio["state"])
            self.assertEqual("配音.wav", audio["original_filename"])
            self.assertNotIn("wav_path", audio)
            uploaded = Path(audio["upload_path"])
            self.assertTrue(uploaded.is_file())
            self.assertTrue(uploaded.is_relative_to(Path(self.tmp.name).resolve()))
            enqueue.assert_called_once_with(
                api.prepare_uploaded_audio,
                args=[workflow.id],
                queue="report_audio_workflow",
                feature=service.FEATURE_NAME,
                task_id=workflow.id,
                retry=False,
            )

    def test_submit_audio_edit_queues_final_wav_without_treating_source_as_ready(self):
        """Catch a regression where source preview WAV can unlock Gemini directly."""

        workflow = self.make_pending_workflow()
        with patch.dict(os.environ, {"SCRIPT_VIDEO_WORKFLOW_OUTPUT_ROOT": self.tmp.name}):
            root = service.workflow_output_root(workflow.id)
            source = root / "audio" / "source.wav"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(self.wav_bytes(seconds=4))
            workflow.result = {
                "audio": {
                    "state": "edit_ready",
                    "source_type": "upload",
                    "source_wav_path": str(source),
                    "source_seconds": 4.0,
                },
                "images": {},
                "video": None,
                "logs": [],
            }
            workflow.current_step = "audio_edit_ready"
            db.session.commit()

            with patch.object(
                api,
                "maybe_guarded_apply_async",
                return_value={"async_result": SimpleNamespace(id="audio-edit-celery-1")},
            ) as enqueue:
                with self.app.test_request_context(
                    f"/api/script-video-workflows/jobs/{workflow.id}/audio/edit",
                    method="POST",
                    json={"trim_start_seconds": 0.5, "trim_end_seconds": 3.0, "speed": 1.5},
                ):
                    response = api.submit_audio_edit.__wrapped__(self.user, workflow.id)

            body = response.get_json()
            self.assertTrue(body["success"])
            self.assertEqual("audio_edit_queued", body["data"]["workflow"]["step"])
            fresh = db.session.get(PublicationTask, workflow.id)
            self.assertEqual("audio_edit_queued", fresh.current_step)
            self.assertEqual("audio_edit_queued", fresh.result["stage"])
            self.assertEqual(1.5, fresh.result["audio"]["edit"]["speed"])
            self.assertNotIn("wav_path", fresh.result["audio"])
            enqueue.assert_called_once_with(
                api.finalize_audio_edit,
                args=[workflow.id],
                queue="report_audio_workflow",
                feature=service.FEATURE_NAME,
                task_id=workflow.id,
                retry=False,
            )

    def test_get_job_query_budget_is_constant_and_uses_narrow_projections(self):
        workflow = self.make_workflow(
            text="第一句。第二句。第三句。",
            scenes=[
                {"sequence": 1, "script_text": "第一句。", "image_prompt": "画面一"},
                {"sequence": 2, "script_text": "第二句。", "image_prompt": "画面二"},
                {"sequence": 3, "script_text": "第三句。", "image_prompt": "画面三"},
            ],
        )
        self.make_image_children(workflow)
        workflow_id = workflow.id
        db.session.expunge_all()

        def request_job():
            with self.app.test_request_context(
                f"/api/script-video-workflows/jobs/{workflow_id}",
                method="GET",
            ):
                return api.get_job.__wrapped__(self.user, workflow_id)

        response, statements = self.publication_selects_during(request_job)
        body = response.get_json()

        self.assertTrue(body["success"])
        self.assertEqual(3, len(body["data"]["scenes"]))
        self.assertTrue(all(scene["image_task_id"] for scene in body["data"]["scenes"]))
        self.assertTrue(all(scene["image_scene_hash"] for scene in body["data"]["scenes"]))
        self.assertEqual(2, len(statements), statements)
        for statement in statements:
            self.assertNotIn("report_audio_pdf_bytes", statement)
            self.assertNotIn("publisher_vertical_video_path", statement)

    def test_get_job_poll_uses_dynamic_parent_projection_and_scene_deltas(self):
        workflow = self.make_workflow(
            text="第一句。第二句。第三句。",
            scenes=[
                {"sequence": 1, "script_text": "第一句。", "image_prompt": "画面一"},
                {"sequence": 2, "script_text": "第二句。", "image_prompt": "画面二"},
                {"sequence": 3, "script_text": "第三句。", "image_prompt": "画面三"},
            ],
        )
        self.make_image_children(workflow)
        workflow_id = workflow.id
        db.session.expunge_all()

        def request_job_poll():
            with self.app.test_request_context(
                f"/api/script-video-workflows/jobs/{workflow_id}?poll=1",
                method="GET",
            ):
                return api.get_job.__wrapped__(self.user, workflow_id)

        response, statements = self.publication_selects_during(request_job_poll)
        body = response.get_json()

        self.assertTrue(body["success"])
        self.assertTrue(body["data"]["partial"])
        self.assertNotIn("source_text", body["data"])
        self.assertNotIn("scenes", body["data"])
        self.assertEqual(3, len(body["data"]["scene_updates"]))
        self.assertTrue(all(update["image_scene_hash"] for update in body["data"]["scene_updates"]))
        self.assertEqual(2, len(statements), statements)
        for statement in statements:
            self.assertNotIn("publication_tasks.params as", statement)
            self.assertNotIn("report_audio_pdf_bytes", statement)

    def test_list_jobs_batches_image_children_across_parents(self):
        first = self.make_workflow(text="甲。乙。")
        second = self.make_workflow(text="丙。丁。")
        self.make_image_children(first)
        self.make_image_children(second)
        db.session.expunge_all()

        def request_jobs():
            with self.app.test_request_context(
                "/api/script-video-workflows/jobs?per_page=12",
                method="GET",
            ):
                return api.list_jobs.__wrapped__(self.user)

        response, statements = self.publication_selects_during(request_jobs)
        body = response.get_json()

        self.assertTrue(body["success"])
        self.assertEqual(2, len(body["data"]["items"]))
        self.assertEqual(3, len(statements), statements)
        self.assertTrue(all(len(item["scenes"]) == 2 for item in body["data"]["items"]))

    def test_list_jobs_summary_skips_json_payloads_and_image_children(self):
        workflow = self.make_workflow(text="甲。乙。")
        self.make_image_children(workflow)
        db.session.expunge_all()

        def request_jobs():
            with self.app.test_request_context(
                "/api/script-video-workflows/jobs?per_page=12&summary=1",
                method="GET",
            ):
                return api.list_jobs.__wrapped__(self.user)

        response, statements = self.publication_selects_during(request_jobs)
        body = response.get_json()

        self.assertTrue(body["success"])
        self.assertEqual(1, len(body["data"]["items"]))
        self.assertNotIn("scenes", body["data"]["items"][0])
        self.assertEqual(2, len(statements), statements)
        for statement in statements:
            self.assertNotIn("publication_tasks.params", statement)
            self.assertNotIn("publication_tasks.result", statement)

    def test_update_scenes_rejects_incomplete_source_coverage(self):
        workflow = self.make_workflow()
        with self.app.test_request_context(
            f"/api/script-video-workflows/jobs/{workflow.id}/scenes",
            method="PATCH",
            json={"scenes": [{"sequence": 1, "script_text": "第一句。", "image_prompt": "画面"}]},
        ):
            response, status = api.update_scenes.__wrapped__(self.user, workflow.id)

        self.assertEqual(400, status)
        self.assertIn("未完整覆盖", response.get_json()["message"])

    def test_update_scenes_rejects_edits_while_video_is_rendering(self):
        workflow = self.make_workflow()
        workflow.status = "processing"
        workflow.current_step = "render_video"
        workflow.result = {**workflow.result, "stage": "render_video"}
        db.session.commit()

        with self.app.test_request_context(
            f"/api/script-video-workflows/jobs/{workflow.id}/scenes",
            method="PATCH",
            json={"scenes": workflow.params[service.FEATURE_NAME]["scenes"]},
        ):
            response = api.update_scenes.__wrapped__(self.user, workflow.id)

        body, status = response if isinstance(response, tuple) else (response, response.status_code)
        self.assertEqual(409, status)
        self.assertIn("合成", body.get_json()["message"])

    def test_submit_render_does_not_duplicate_an_active_render_job(self):
        workflow = self.make_workflow()
        workflow.status = "processing"
        workflow.current_step = "render_video"
        workflow.result = {**workflow.result, "stage": "render_video"}
        db.session.commit()

        with patch.object(api, "_has_current_image", return_value=True), patch.object(
            api,
            "maybe_guarded_apply_async",
        ) as enqueue:
            with self.app.test_request_context(
                f"/api/script-video-workflows/jobs/{workflow.id}/render",
                method="POST",
                json={},
            ):
                response = api.submit_render.__wrapped__(self.user, workflow.id)

        body, status = response if isinstance(response, tuple) else (response, response.status_code)
        self.assertEqual(409, status)
        self.assertIn("正在", body.get_json()["message"])
        enqueue.assert_not_called()

    def test_submit_images_queues_one_child_per_valid_scene(self):
        workflow = self.make_workflow()
        queued = [SimpleNamespace(id="image-1"), SimpleNamespace(id="image-2")]
        with patch.object(api, "enqueue_image_stage", return_value=queued) as enqueue:
            with self.app.test_request_context(
                f"/api/script-video-workflows/jobs/{workflow.id}/images",
                method="POST",
                json={},
            ):
                response = api.submit_images.__wrapped__(self.user, workflow.id)
        body = response.get_json()

        self.assertTrue(body["success"])
        self.assertEqual(2, body["data"]["queued"])
        enqueue.assert_called_once()

    def test_download_rejects_artifact_outside_its_workflow_directory(self):
        workflow = self.make_workflow()
        with self.app.test_request_context(
            f"/api/script-video-workflows/jobs/{workflow.id}/audio/file",
            method="GET",
        ):
            response, status = api.download_audio.__wrapped__(self.user, workflow.id)

        self.assertEqual(404, status)
        self.assertIn("目录", response.get_json()["message"])

    def test_scene_edit_invalidates_only_the_changed_image_child(self):
        workflow = self.make_workflow()
        original_scenes = workflow.params[service.FEATURE_NAME]["scenes"]
        children = []
        for scene in original_scenes:
            child = PublicationTask(
                status="completed",
                current_step="completed",
                progress=100,
                params={
                    "feature": service.IMAGE_FEATURE_NAME,
                    "user_id": self.user.id,
                    "parent_feature": service.FEATURE_NAME,
                    "parent_task_id": workflow.id,
                    service.IMAGE_FEATURE_NAME: {
                        "sequence": scene["sequence"],
                        "scene_hash": _scene_content_hash(scene, "stick_figure"),
                        "attempt": 1,
                    },
                },
                result={"image": {}},
            )
            db.session.add(child)
            children.append(child)
        db.session.commit()
        changed_scenes = [
            dict(original_scenes[0]),
            {**original_scenes[1], "image_prompt": "更新后的第二张画面"},
        ]

        with self.app.test_request_context(
            f"/api/script-video-workflows/jobs/{workflow.id}/scenes",
            method="PATCH",
            json={"scenes": changed_scenes},
        ):
            response = api.update_scenes.__wrapped__(self.user, workflow.id)

        self.assertTrue(response.get_json()["success"])
        self.assertEqual("completed", db.session.get(PublicationTask, children[0].id).status)
        self.assertEqual("canceled", db.session.get(PublicationTask, children[1].id).status)

    def test_api_registration_exposes_the_generic_workflow_endpoint(self):
        registration = (BACKEND_DIR / "apis" / "__init__.py").read_text(encoding="utf-8")

        self.assertIn("script_video_workflow_bp", registration)
        self.assertIn("script_video_workflows", registration)


if __name__ == "__main__":
    unittest.main()
