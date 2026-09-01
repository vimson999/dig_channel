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

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services import script_video_workflow as service  # noqa: E402
from tasks import script_video_workflow as tasks  # noqa: E402


class ScriptVideoWorkflowTaskTest(unittest.TestCase):
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

    def tearDown(self):
        self.tmp.cleanup()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def make_parent(
        self,
        text="第一句。第二句。",
        *,
        audio_chunks=None,
        scenes=None,
        scheme_key="stick_figure",
    ):
        source = service.normalize_script(text)
        audio_chunks = list(audio_chunks or service.split_audio_chunks(source))
        scenes = list(scenes or service.build_initial_scenes(source))
        task = PublicationTask(
            status="pending",
            current_step="script_ready",
            progress=5,
            params={
                "feature": service.FEATURE_NAME,
                "user_id": "user-1",
                "visual_scheme_key": scheme_key,
                service.FEATURE_NAME: {
                    "source_text": source,
                    "source_text_hash": service.text_hash(source),
                    "audio_chunks": audio_chunks,
                    "scenes": scenes,
                },
            },
            result={"audio": None, "images": {}, "video": None, "logs": []},
        )
        db.session.add(task)
        db.session.commit()
        return task

    def make_completed_audio_children(self, parent, *, order):
        chunks = parent.params[service.FEATURE_NAME]["audio_chunks"]
        children = []
        for index in order:
            wav_path = Path(self.tmp.name) / f"{index}.wav"
            wav_path.write_bytes(b"RIFF....WAVE")
            text = chunks[index - 1]
            metadata = {
                "source": service.FEATURE_NAME,
                "parent_task_id": parent.id,
                "chunk_index": index,
                "chunk_total": len(chunks),
                "chunk_attempt": 1,
                "text_hash": service.text_hash(text),
            }
            child = PublicationTask(
                status="completed",
                current_step="completed",
                progress=100,
                params={
                    "feature": "doubao_browser_tts",
                    "user_id": "user-1",
                    "parent_feature": service.FEATURE_NAME,
                    "parent_task_id": parent.id,
                    "doubao_browser_tts": {"text": text, "metadata": metadata},
                },
                result={
                    "metadata": metadata,
                    "audio": {"wav_path": str(wav_path), "seconds": 1.5, "bytes": wav_path.stat().st_size},
                },
            )
            db.session.add(child)
            children.append(child)
        db.session.commit()
        return children

    def make_wav(self, path, *, seconds, sample_rate=16000):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(sample_rate)
            writer.writeframes(b"\x00\x00" * int(seconds * sample_rate))
        return path

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

    def test_audio_children_are_created_in_source_order_with_generic_parent_metadata(self):
        parent = self.make_parent(
            audio_chunks=["第一句。", "第二句。"],
        )
        chunks = ["第一句。", "第二句。"]

        with patch.object(
            tasks,
            "apply_async_after_db_release",
            return_value=SimpleNamespace(id="celery-1"),
        ):
            children = tasks._create_or_reuse_audio_children(parent, chunks)

        self.assertEqual(
            chunks,
            [child.params["doubao_browser_tts"]["text"] for child in children],
        )
        self.assertTrue(all(child.parent_feature == service.FEATURE_NAME for child in children))
        self.assertEqual([1, 2], [child.doubao_tts_chunk_index for child in children])
        self.assertEqual(
            [service.text_hash(chunk) for chunk in chunks],
            [child.doubao_tts_text_hash for child in children],
        )

    def test_audio_finalization_combines_completed_children_into_an_editable_source(self):
        parent = self.make_parent(
            audio_chunks=["第一句。", "第二句。"],
        )
        self.make_completed_audio_children(parent, order=[2, 1])
        output = Path(self.tmp.name) / "merged.wav"

        with patch.object(tasks, "workflow_output_root", return_value=Path(self.tmp.name) / "workflow"), patch.object(
            tasks,
            "combine_wav_files",
            return_value={
                "wav_path": str(output),
                "seconds": 4.2,
                "bytes": 120,
                "sample_rate": 24000,
                "chunks": 2,
            },
        ) as combine:
            result = tasks._finalize_audio_stage(parent.id)

        self.assertEqual(2, result["chunks"])
        self.assertEqual(
            [str(Path(self.tmp.name) / "1.wav"), str(Path(self.tmp.name) / "2.wav")],
            combine.call_args.args[0],
        )
        fresh = db.session.get(PublicationTask, parent.id)
        self.assertEqual("audio_edit_ready", fresh.current_step)
        self.assertEqual(str(output), fresh.result["audio"]["source_wav_path"])
        self.assertEqual("edit_ready", fresh.result["audio"]["state"])
        self.assertNotIn("wav_path", fresh.result["audio"])
        self.assertEqual(2, fresh.result["audio"]["chunks_total"])

    def test_uploaded_audio_is_normalized_for_editing_without_unlocking_storyboard(self):
        """Catch a regression where a merely uploaded source is treated as final WAV."""

        parent = self.make_parent(text="第一句。")
        root = Path(self.tmp.name) / "workflow"
        uploaded = self.make_wav(root / "audio" / "uploads" / "原始.wav", seconds=3)
        parent.result = {
            "audio": {
                "state": "upload_queued",
                "source_type": "upload",
                "upload_path": str(uploaded),
                "original_filename": "原始.wav",
            },
            "images": {},
            "video": None,
            "logs": [],
        }
        parent.current_step = "audio_upload_queued"
        db.session.commit()

        with patch.object(tasks, "workflow_output_root", return_value=root):
            audio = tasks._prepare_uploaded_audio(parent.id)

        fresh = db.session.get(PublicationTask, parent.id)
        self.assertEqual("edit_ready", audio["state"])
        self.assertEqual("audio_edit_ready", fresh.current_step)
        self.assertTrue(Path(audio["source_wav_path"]).is_file())
        self.assertTrue(Path(audio["source_wav_path"]).is_relative_to(root))
        self.assertGreater(audio["source_seconds"], 2.9)
        self.assertNotIn("wav_path", audio)
        self.assertFalse(tasks._audio_is_ready(fresh))

    def test_audio_edit_trims_and_speeds_source_into_the_only_final_wav(self):
        """Catch a regression where preview settings are saved but never rendered."""

        parent = self.make_parent(text="第一句。")
        root = Path(self.tmp.name) / "workflow"
        source = self.make_wav(root / "audio" / "source.wav", seconds=4)
        parent.status = "pending"
        parent.current_step = "audio_edit_queued"
        parent.result = {
            "audio": {
                "state": "edit_queued",
                "source_type": "upload",
                "source_wav_path": str(source),
                "source_seconds": 4.0,
                "edit": {
                    "trim_start_seconds": 0.5,
                    "trim_end_seconds": 2.5,
                    "speed": 2.0,
                    "revision": 1,
                },
            },
            "alignment": {"scene_count": 1},
            "video": {"video_path": str(root / "video" / "old.mp4")},
            "images": {},
            "logs": [],
        }
        db.session.commit()

        with patch.object(tasks, "workflow_output_root", return_value=root):
            audio = tasks._finalize_audio_edit(parent.id)

        fresh = db.session.get(PublicationTask, parent.id)
        self.assertEqual("finalized", audio["state"])
        self.assertTrue(Path(audio["wav_path"]).is_file())
        self.assertTrue(Path(audio["wav_path"]).is_relative_to(root))
        self.assertGreater(audio["seconds"], 0.9)
        self.assertLess(audio["seconds"], 1.1)
        self.assertEqual("audio_ready", fresh.current_step)
        self.assertTrue(tasks._audio_is_ready(fresh))
        self.assertIsNone(fresh.result["alignment"])
        self.assertIsNone(fresh.result["video"])

    def test_completed_matching_audio_child_is_reused_without_new_browser_submission(self):
        parent = self.make_parent(text="第一句。")
        self.make_completed_audio_children(parent, order=[1])

        with patch.object(tasks, "apply_async_after_db_release") as enqueue:
            children = tasks._create_or_reuse_audio_children(
                parent,
                parent.params[service.FEATURE_NAME]["audio_chunks"],
            )

        self.assertEqual(1, len(children))
        self.assertEqual("completed", children[0].status)
        enqueue.assert_not_called()

    def test_image_child_batch_has_constant_select_budget(self):
        parent = self.make_parent(
            text="第一句。第二句。第三句。第四句。",
            scenes=[
                {"sequence": 1, "script_text": "第一句。", "image_prompt": "图一"},
                {"sequence": 2, "script_text": "第二句。", "image_prompt": "图二"},
                {"sequence": 3, "script_text": "第三句。", "image_prompt": "图三"},
                {"sequence": 4, "script_text": "第四句。", "image_prompt": "图四"},
            ],
        )
        scenes = parent.params[service.FEATURE_NAME]["scenes"]

        with patch.object(
            tasks,
            "apply_async_after_db_release",
            return_value=SimpleNamespace(id="unused"),
        ):
            children, statements = self.publication_selects_during(
                lambda: tasks._create_or_reuse_image_children(parent, scenes)
            )

        self.assertEqual(4, len(children))
        self.assertLessEqual(len(statements), 2, statements)
        self.assertTrue(all("report_audio_pdf_bytes" not in sql for sql in statements))

    def test_image_children_persist_stable_broker_ids_before_publish(self):
        parent = self.make_parent(
            text="第一句。第二句。",
            scenes=[
                {"sequence": 1, "script_text": "第一句。", "image_prompt": "图一"},
                {"sequence": 2, "script_text": "第二句。", "image_prompt": "图二"},
            ],
        )
        scenes = parent.params[service.FEATURE_NAME]["scenes"]
        observed = []

        def publish(_task, **options):
            child = db.session.get(PublicationTask, options["args"][0])
            observed.append({
                "child_id": child.id,
                "stored_id": child.celery_task_id,
                "published_id": options.get("task_id"),
                "in_transaction": db.session().in_transaction(),
            })
            db.session.rollback()
            return SimpleNamespace(id=options.get("task_id"))

        with patch.object(tasks, "apply_async_after_db_release", side_effect=publish):
            children = tasks._create_or_reuse_image_children(parent, scenes)

        self.assertEqual(2, len(children))
        self.assertEqual(2, len(observed))
        self.assertTrue(all(item["stored_id"] for item in observed))
        self.assertTrue(all(item["stored_id"] == item["published_id"] for item in observed))
        self.assertTrue(all(item["in_transaction"] is False for item in observed))

    def test_image_batch_marks_only_failed_broker_publish_after_durable_insert(self):
        parent = self.make_parent(
            text="第一句。第二句。",
            scenes=[
                {"sequence": 1, "script_text": "第一句。", "image_prompt": "图一"},
                {"sequence": 2, "script_text": "第二句。", "image_prompt": "图二"},
            ],
        )
        scenes = parent.params[service.FEATURE_NAME]["scenes"]
        published = []

        def publish(_task, **options):
            published.append(dict(options))
            if len(published) == 1:
                raise RuntimeError("broker unavailable")
            return SimpleNamespace(id=options["task_id"])

        with patch.object(tasks, "apply_async_after_db_release", side_effect=publish):
            children = tasks._create_or_reuse_image_children(parent, scenes)

        db.session.expire_all()
        persisted = [db.session.get(PublicationTask, child.id) for child in children]
        self.assertEqual(["failed", "pending"], [child.status for child in persisted])
        self.assertEqual("queue_failed", persisted[0].current_step)
        self.assertIn("broker unavailable", persisted[0].error_message)
        self.assertEqual(
            [child.celery_task_id for child in persisted],
            [item["task_id"] for item in published],
        )

    def test_enqueue_image_stage_never_loads_unrelated_task_payload_columns(self):
        parent = self.make_parent(
            text="第一句。第二句。第三句。",
            scenes=[
                {"sequence": 1, "script_text": "第一句。", "image_prompt": "图一"},
                {"sequence": 2, "script_text": "第二句。", "image_prompt": "图二"},
                {"sequence": 3, "script_text": "第三句。", "image_prompt": "图三"},
            ],
        )
        parent_id = parent.id
        db.session.expunge_all()

        with patch.object(
            tasks,
            "apply_async_after_db_release",
            return_value=SimpleNamespace(id="unused"),
        ):
            children, statements = self.publication_selects_during(
                lambda: tasks.enqueue_image_stage(SimpleNamespace(id=parent_id))
            )

        self.assertEqual(3, len(children))
        self.assertLessEqual(len(statements), 4, statements)
        for statement in statements:
            self.assertNotIn("report_audio_pdf_bytes", statement)
            self.assertNotIn("publisher_vertical_video_path", statement)

    def test_celery_configuration_routes_all_generic_stages_to_existing_worker_queues(self):
        config = (BACKEND_DIR / "celery_config.py").read_text(encoding="utf-8")

        self.assertIn("tasks.script_video_workflow", config)
        self.assertIn("tasks.script_video_workflow.process_audio", config)
        self.assertIn("tasks.script_video_workflow.prepare_uploaded_audio", config)
        self.assertIn("tasks.script_video_workflow.finalize_audio_edit", config)
        self.assertIn("tasks.script_video_workflow.generate_storyboard", config)
        self.assertIn("tasks.script_video_workflow.generate_image", config)
        self.assertIn("tasks.script_video_workflow.render_video", config)

    def test_gemini_storyboard_replaces_local_draft_with_returned_scene_prompts(self):
        parent = self.make_parent(
            text="第一句。第二句。",
            scenes=[{
                "sequence": 1,
                "script_text": "第一句。第二句。",
                "image_prompt": "旧的本地草稿",
            }],
        )
        root = Path(self.tmp.name) / "workflow"
        normalized = {
            "title_scene": {
                "sequence": 0,
                "scene_title": "标题镜头",
                "script_text": "测试标题",
                "visual_prompt": "标题画面",
                "estimated_seconds": 3,
            },
            "segments": [
                {
                    "sequence": 1,
                    "scene_title": "分镜 1",
                    "script_text": "第一句。",
                    "visual_prompt": "画面一",
                    "estimated_seconds": 2,
                },
                {
                    "sequence": 2,
                    "scene_title": "分镜 2",
                    "script_text": "第二句。",
                    "visual_prompt": "画面二",
                    "estimated_seconds": 2,
                },
            ],
        }

        with patch.object(tasks, "workflow_output_root", return_value=root), patch.object(
            tasks,
            "visual_scheme",
            return_value=SimpleNamespace(storyboard_gemini_url="https://gemini.example/gem"),
        ), patch.object(tasks, "storyboard_prompt", return_value="规划分镜"), patch.object(
            tasks,
            "normalize_storyboard_response",
            return_value=normalized,
        ), patch.object(tasks, "GeminiBrowserWorker") as worker:
            worker.return_value.generate.return_value = {"final_response_text": "Gemini 原始分镜"}
            result = tasks._generate_storyboard(parent.id)

        self.assertEqual(2, result["segment_count"])
        self.assertEqual(
            [
                {"sequence": 1, "script_text": "第一句。", "image_prompt": "画面一"},
                {"sequence": 2, "script_text": "第二句。", "image_prompt": "画面二"},
            ],
            db.session.get(PublicationTask, parent.id).params[service.FEATURE_NAME]["scenes"],
        )
        fresh = db.session.get(PublicationTask, parent.id)
        self.assertEqual("storyboard_ready", fresh.current_step)
        self.assertEqual("storyboard_ready", fresh.result["stage"])
        self.assertEqual("标题画面", fresh.result["storyboard"]["title_scene"]["visual_prompt"])
        self.assertEqual("https://gemini.example/gem", worker.return_value.generate.call_args.kwargs["target_url"])

    def test_image_child_uses_only_the_scheme_composed_prompt(self):
        scenes = [{
            "sequence": 1,
            "script_text": "市场出现拐点。",
            "image_prompt": "折线向上穿越门槛",
        }]
        parent = self.make_parent(
            text="市场出现拐点。",
            scenes=scenes,
            scheme_key="doraemon",
        )
        image_path = Path(self.tmp.name) / "scene.png"
        image_path.write_bytes(b"png")

        with patch.object(
            tasks,
            "apply_async_after_db_release",
            return_value=SimpleNamespace(id="image-celery-1"),
        ):
            child = tasks._create_or_reuse_image_children(parent, scenes)[0]
        with patch.object(tasks, "GeminiBrowserWorker") as worker, patch.object(
            tasks,
            "compose_scene_image_prompt",
            return_value="combined prompt",
        ), patch.object(tasks, "scene_image_output_path", return_value=image_path):
            worker.return_value.generate_image.return_value = {
                "path": str(image_path),
                "bytes": image_path.stat().st_size,
            }
            self.assertTrue(tasks._generate_image(child.id))

        self.assertEqual(
            "combined prompt",
            worker.return_value.generate_image.call_args.kwargs["prompt"],
        )
        self.assertEqual(
            "completed",
            db.session.get(PublicationTask, child.id).status,
        )

    def test_stale_image_delivery_cannot_replace_an_edited_scene_asset(self):
        scenes = [{"sequence": 1, "script_text": "第一句。", "image_prompt": "原始画面"}]
        parent = self.make_parent(text="第一句。", scenes=scenes)
        image_path = Path(self.tmp.name) / "stale.png"
        with patch.object(
            tasks,
            "apply_async_after_db_release",
            return_value=SimpleNamespace(id="image-celery-1"),
        ):
            child = tasks._create_or_reuse_image_children(parent, scenes)[0]

        def finish_after_scene_was_edited(**_kwargs):
            image_path.write_bytes(b"png")
            stale = db.session.get(PublicationTask, child.id)
            stale.status = "canceled"
            stale.current_step = "scene_changed"
            db.session.commit()
            return {"path": str(image_path), "bytes": image_path.stat().st_size}

        with patch.object(tasks, "GeminiBrowserWorker") as worker, patch.object(
            tasks,
            "scene_image_output_path",
            return_value=image_path,
        ):
            worker.return_value.generate_image.side_effect = finish_after_scene_was_edited
            self.assertFalse(tasks._generate_image(child.id))

        fresh = db.session.get(PublicationTask, child.id)
        self.assertEqual("canceled", fresh.status)
        self.assertIsNone(fresh.result.get("image"))

    def test_render_requires_all_scene_images_and_persists_continuous_timeline(self):
        scenes = [
            {"sequence": 1, "script_text": "第一句。", "image_prompt": "画面一"},
            {"sequence": 2, "script_text": "第二句。", "image_prompt": "画面二"},
        ]
        parent = self.make_parent(
            scenes=scenes,
            audio_chunks=["第一句。", "第二句。"],
        )
        audio_path = Path(self.tmp.name) / "merged.wav"
        with wave.open(str(audio_path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(b"\x00\x00" * 32000)
        parent.status = "processing"
        parent.current_step = service.AUDIO_STEP
        parent.result = {
            "audio": {"wav_path": str(audio_path), "seconds": 2.0, "bytes": audio_path.stat().st_size},
            "images": {},
            "video": None,
            "logs": [],
        }
        db.session.commit()

        with patch.object(
            tasks,
            "apply_async_after_db_release",
            return_value=SimpleNamespace(id="image-celery-1"),
        ):
            children = tasks._create_or_reuse_image_children(parent, scenes)
        for child in children:
            sequence = child.params[service.IMAGE_FEATURE_NAME]["sequence"]
            image_path = Path(self.tmp.name) / f"{sequence}.png"
            image_path.write_bytes(b"png")
            child.status = "completed"
            child.current_step = "completed"
            child.progress = 100
            child.result = {"image": {"path": str(image_path), "bytes": image_path.stat().st_size}}
        db.session.commit()
        video_path = Path(self.tmp.name) / "video.mp4"
        video_path.write_bytes(b"mp4")

        with patch.object(
            tasks,
            "transcribe_with_cache",
            return_value={
                "segments": [{"text": "第一句。第二句。", "start": 0, "end": 2}],
                "metadata": {"cache_key": "abc"},
            },
        ), patch.object(
            tasks,
            "align_storyboard_to_audio",
            return_value={
                "timeline": [
                    {"sequence": 1, "start_seconds": 0, "end_seconds": 1, "alignment_confidence": 0.9},
                    {"sequence": 2, "start_seconds": 1, "end_seconds": 2, "alignment_confidence": 0.9},
                ],
                "scene_count": 2,
            },
        ), patch.object(
            tasks,
            "render_scene_timeline",
            return_value={"video_path": str(video_path), "duration_seconds": 2, "bytes": video_path.stat().st_size},
        ):
            self.assertTrue(tasks._render_video(parent.id))

        fresh = db.session.get(PublicationTask, parent.id)
        self.assertEqual("completed", fresh.status)
        self.assertEqual("completed", fresh.current_step)
        self.assertEqual(str(video_path), fresh.result["video"]["video_path"])
        self.assertEqual(2, fresh.result["alignment"]["scene_count"])

    def test_render_refuses_missing_image_before_starting_asr(self):
        scene = {"sequence": 1, "script_text": "第一句。", "image_prompt": "画面"}
        parent = self.make_parent(text="第一句。", scenes=[scene])
        audio_path = Path(self.tmp.name) / "merged.wav"
        with wave.open(str(audio_path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(b"\x00\x00" * 16000)
        parent.status = "processing"
        parent.current_step = service.AUDIO_STEP
        parent.result = {"audio": {"wav_path": str(audio_path), "seconds": 1.0}, "logs": []}
        db.session.commit()

        with patch.object(tasks, "transcribe_with_cache") as transcribe:
            with self.assertRaisesRegex(ValueError, "图片"):
                tasks._render_video(parent.id)

        transcribe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
