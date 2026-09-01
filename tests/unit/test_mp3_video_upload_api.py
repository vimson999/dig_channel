import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from sqlalchemy import event


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import mp3_video_workflow as mp3_api  # noqa: E402
from models import db  # noqa: E402
from models.bilibili_publish import PublicationTaskBilibiliUpload  # noqa: E402
from models.platform_credential import PlatformCredentialAccount  # noqa: E402
from models.task import PublicationTask  # noqa: E402


class Mp3VideoUploadApiTest(unittest.TestCase):
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

    def test_audio_asset_client_trace_is_allowlisted_for_structured_logs(self):
        with self.app.test_request_context(
            "/api/mp3-video-workflows/audio-assets"
            "?client_id=page-abc_123&client_seq=7"
            "&client_trigger=composition_end&client_inflight=1"
        ):
            trace = mp3_api._audio_asset_client_trace()

        self.assertEqual(trace, {
            "client_id": "page-abc_123",
            "client_seq": 7,
            "client_trigger": "composition_end",
            "client_inflight": 1,
        })

        with self.app.test_request_context(
            "/api/mp3-video-workflows/audio-assets"
            "?client_id=bad%0Alog&client_seq=999999999999"
            "&client_trigger=forged&client_inflight=999"
        ):
            invalid = mp3_api._audio_asset_client_trace()

        self.assertEqual(invalid, {
            "client_id": "unknown",
            "client_seq": 0,
            "client_trigger": "unknown",
            "client_inflight": 0,
        })

    def test_bilibili_submit_reads_one_narrow_parent_and_credential_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "landscape.mp4"
            cover_path = Path(tmpdir) / "cover.jpg"
            video_path.write_bytes(b"video")
            cover_path.write_bytes(b"cover")
            parent = PublicationTask(
                status="completed",
                current_step="done",
                progress=100,
                params={"feature": "mp3_video_workflow", "user_id": "user-1"},
                result={
                    "plan": {
                        "xiaojunjun_video_title": "横屏结构化标题",
                        "ciketc_video_title": "竖屏结构化标题",
                        "title": "通用结构化标题",
                    },
                    "landscape_video": {"video_path": str(video_path)},
                    "vertical_video": {"video_path": str(video_path)},
                    "artifacts": {
                        "xiaojunjun_cover": {"path": str(cover_path)},
                    },
                    "raw_audit_payload": "x" * 100_000,
                },
            )
            credential = PlatformCredentialAccount(
                platform="bilibili",
                name="主账号",
                owner_label="主账号展示名",
                encrypted_payload="encrypted",
                payload_fingerprint="mp3-bili-narrow-snapshot",
            )
            db.session.add_all([parent, credential])
            db.session.commit()
            parent_id = str(parent.id)
            credential_id = str(credential.id)
            statements = []

            def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
                statements.append(" ".join(statement.lower().split()))

            def enqueue_without_broker(child):
                child._bilibili_enqueue_response_payload = dict(
                    child._bilibili_create_response_payload
                )
                return child

            def probe_video_without_db_hold(_path):
                self.assertFalse(
                    db.session().in_transaction(),
                    "视频探测前必须释放父任务快照的数据库事务",
                )
                return True

            event.listen(db.engine, "before_cursor_execute", capture)
            try:
                with (
                    self.app.test_request_context(
                        f"/api/mp3-video-workflows/jobs/{parent_id}/bilibili-upload",
                        method="POST",
                        json={
                            "credential_id": credential_id,
                            "video_key": "landscape",
                            "cover_key": "",
                            "charging_pay": {"enabled": False},
                        },
                    ),
                    patch.object(
                        mp3_api,
                        "_video_file_ready",
                        side_effect=probe_video_without_db_hold,
                    ),
                    patch.object(
                        mp3_api,
                        "enqueue_bilibili_upload_task",
                        side_effect=enqueue_without_broker,
                    ),
                ):
                    response = mp3_api.submit_bilibili_upload.__wrapped__(
                        SimpleNamespace(id="user-1"),
                        parent_id,
                    )
            finally:
                event.remove(db.engine, "before_cursor_execute", capture)

        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["upload_task"]["title"], "横屏结构化标题")
        self.assertEqual(payload["upload_task"]["credential_name"], "主账号展示名")
        publication_selects = [
            statement
            for statement in statements
            if statement.startswith("select") and "publication_tasks" in statement
        ]
        self.assertEqual(len(publication_selects), 1)
        self.assertIn("platform_credential_accounts", publication_selects[0])
        self.assertNotIn(
            "publication_tasks.params as publication_tasks_params",
            publication_selects[0],
        )
        self.assertNotIn(
            "publication_tasks.result as publication_tasks_result",
            publication_selects[0],
        )
        standalone_credential_selects = [
            statement
            for statement in statements
            if statement.startswith("select")
            and "platform_credential_accounts" in statement
            and "publication_tasks" not in statement
        ]
        self.assertEqual(standalone_credential_selects, [])

    def test_bilibili_cancel_uses_narrow_state_and_releases_before_revoke(self):
        parent = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "mp3_video_workflow",
                "user_id": "user-1",
                "raw_request_audit": "p" * 100_000,
            },
            result={"raw_parent_audit": "r" * 100_000},
        )
        db.session.add(parent)
        db.session.flush()
        parent_id = str(parent.id)
        child = PublicationTask(
            feature="publisher",
            user_id="user-1",
            parent_task_id=parent_id,
            parent_feature="mp3_video_workflow",
            source_task_id=parent_id,
            source_feature="mp3_video_workflow",
            status="pending",
            current_step="queued",
            progress=0,
            celery_task_id="mp3-cancel-broker-id",
            params={
                "feature": "publisher",
                "user_id": "user-1",
                "parent_task_id": parent_id,
                "parent_feature": "mp3_video_workflow",
                "source_task_id": parent_id,
                "source_feature": "mp3_video_workflow",
                "raw_request_audit": "c" * 100_000,
            },
            result={"raw_child_audit": "d" * 100_000},
        )
        db.session.add(child)
        db.session.flush()
        child_id = str(child.id)
        db.session.add(PublicationTaskBilibiliUpload(
            task_id=child_id,
            parent_task_id=parent_id,
            parent_feature="mp3_video_workflow",
            source_label="mp3_video_workflow:bilibili_upload",
            video_key="landscape",
            title="MP3 取消窄快照",
            video_path="/tmp/mp3-cancel.mp4",
            tags=["财经"],
        ))
        db.session.commit()

        statements = []
        revoke_transaction_states = []
        phase = {"revoke_started": False}

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append((
                phase["revoke_started"],
                " ".join(statement.lower().split()),
            ))

        def revoke_without_broker(*_args, **_kwargs):
            revoke_transaction_states.append(db.session().in_transaction())
            phase["revoke_started"] = True

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with (
                self.app.test_request_context(
                    f"/api/mp3-video-workflows/jobs/{parent_id}/"
                    f"bilibili-upload/{child_id}/cancel",
                    method="POST",
                ),
                patch.object(
                    mp3_api.celery_app.control,
                    "revoke",
                    side_effect=revoke_without_broker,
                ),
            ):
                response = mp3_api.cancel_bilibili_upload.__wrapped__(
                    SimpleNamespace(id="user-1"),
                    parent_id,
                    child_id,
                )
                payload = response[0].get_json() if isinstance(response, tuple) else response.get_json()
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertTrue(payload["success"], payload)
        self.assertEqual(payload["upload_task"]["status"], "canceled")
        self.assertEqual(revoke_transaction_states, [False])
        task_selects = [
            statement
            for _after_revoke, statement in statements
            if statement.startswith("select") and "publication_tasks" in statement
        ]
        self.assertTrue(task_selects)
        for statement in task_selects:
            self.assertNotIn("publication_tasks.params", statement)
            self.assertNotIn("publication_tasks.result", statement)
        post_revoke_selects = [
            statement
            for after_revoke, statement in statements
            if after_revoke and statement.startswith("select")
        ]
        self.assertEqual(post_revoke_selects, [])

    def test_audio_assets_logs_request_ownership_without_extra_database_write(self):
        catalog = {
            "items": [{"attachment_id": "pipeline:1"}],
            "pagination": {
                "catalog_cache_hit": True,
                "catalog_cache_age_ms": 42,
            },
        }
        with (
            self.app.test_request_context(
                "/api/mp3-video-workflows/audio-assets"
                "?page=1&client_id=page-1&client_seq=2"
                "&client_trigger=composition_end&client_inflight=1"
            ),
            patch.object(mp3_api, "list_audio_assets", return_value=catalog) as list_assets,
            self.assertLogs(self.app.logger.name, level="INFO") as captured,
        ):
            response = mp3_api.audio_assets.__wrapped__(SimpleNamespace(id="user-1"))

        self.assertTrue(response.get_json()["success"])
        list_assets.assert_called_once()
        log_text = "\n".join(captured.output)
        self.assertIn("[MP3_AUDIO_ASSET_CLIENT]", log_text)
        self.assertIn("client_id=page-1", log_text)
        self.assertIn("client_seq=2", log_text)
        self.assertIn("client_trigger=composition_end", log_text)
        self.assertIn("client_inflight=1", log_text)
        self.assertIn("cache_hit=True", log_text)
        self.assertIn("items=1", log_text)

    def test_uploaded_audio_uses_the_same_workflow_task_shape(self):
        saved_audio = {
            "attachment_id": "upload-id",
            "filename": "本地调研.mp3",
            "name": "本地调研.mp3",
            "path": "/tmp/local-mp3.mp3",
            "bytes": 1234,
            "file_ext": "mp3",
            "duration_seconds": 1800,
            "topic_title": "本地调研.mp3",
            "tab_label": "本地上传",
            "source": "local_upload",
        }

        def save_upload(_file_storage, upload_id):
            return {**saved_audio, "attachment_id": upload_id}

        with (
            self.app.test_request_context(
                "/api/mp3-video-workflows/jobs",
                method="POST",
                data={
                    "audio": (io.BytesIO(b"not-used-by-mocked-save"), "local.mp3"),
                    "input_source": "ds_response",
                    "ds_response_text": "1：标题\n2：副标题\n3：短标题\n4：00:00-10:00\n5：金句",
                    "render_mode": "full_video",
                    "generate_subtitles": "0",
                    "metadata": json.dumps({"title": "本地上传测试"}, ensure_ascii=False),
                },
                content_type="multipart/form-data",
            ),
            patch.object(mp3_api, "_save_audio_upload", side_effect=save_upload),
            patch.object(
                mp3_api,
                "apply_async_after_db_release",
                return_value=SimpleNamespace(id="celery-upload"),
            ),
        ):
            response = mp3_api.create_job.__wrapped__(SimpleNamespace(id="user-1"))

        payload = response.get_json()
        self.assertTrue(payload["success"])
        task = PublicationTask.query.filter_by(id=payload["job_id"]).one()
        workflow = task.params["mp3_video_workflow"]
        self.assertEqual(task.source_feature, "local_mp3_upload")
        self.assertEqual(task.mp3_audio_filename, "本地调研.mp3")
        self.assertEqual(workflow["audio"]["source"], "local_upload")
        self.assertEqual(workflow["audio"]["attachment_id"], task.source_task_id)
        self.assertEqual(workflow["audio"]["topic_title"], "本地上传测试")
        self.assertEqual(workflow["render_mode"], "full_video")
        self.assertFalse(workflow["generate_subtitles"])
        list_payload = mp3_api._job_list_payload(task)
        self.assertEqual(list_payload["request"]["audio"]["filename"], "本地调研.mp3")

    def test_pipeline_audio_job_persists_uuid_source_identity_without_prefix_truncation(self):
        asset_id = "7a1f5b5e-eed3-4959-a676-d1a52fc2938d"
        asset_reference = f"pipeline:{asset_id}"
        resolved_audio = {
            "attachment_id": asset_reference,
            "asset_id": asset_id,
            "asset_kind": "content_pipeline",
            "canonical_asset_id": asset_id,
            "pipeline_task_id": "clean-task-1",
            "filename": "清洗产物.mp3",
            "name": "清洗产物.mp3",
            "path": "/tmp/cleaned-audio.mp3",
            "bytes": 1234,
            "file_ext": "mp3",
            "duration_seconds": 1800,
            "topic_title": "清洗产物",
            "tab_label": "内容流水线",
        }

        with (
            self.app.test_request_context(
                "/api/mp3-video-workflows/jobs",
                method="POST",
                json={
                    "audio_asset_id": asset_reference,
                    "input_source": "ds_response",
                    "ds_response_text": (
                        "1：标题\n2：副标题\n3：短标题\n"
                        "4：00:00-10:00\n5：金句"
                    ),
                },
            ),
            patch.object(
                mp3_api,
                "audio_meta_from_asset_reference",
                return_value=resolved_audio,
            ),
            patch.object(
                mp3_api,
                "apply_async_after_db_release",
                return_value=SimpleNamespace(id="celery-pipeline"),
            ),
        ):
            response = mp3_api.create_job.__wrapped__(SimpleNamespace(id="user-1"))

        payload = response.get_json()
        self.assertTrue(payload["success"])
        task = PublicationTask.query.filter_by(id=payload["job_id"]).one()
        self.assertEqual(asset_id, task.source_task_id)
        self.assertEqual("content_pipeline_asset", task.source_feature)
        self.assertEqual(asset_id, task.params["source_task_id"])
        self.assertEqual(asset_id, task.result["source_task_id"])
        self.assertEqual(
            asset_reference,
            task.params["mp3_video_workflow"]["audio_asset_id"],
        )
        self.assertEqual(
            asset_reference,
            mp3_api._job_list_payload(task)["audio"]["attachment_id"],
        )

    def test_pipeline_workflow_stats_map_normalized_and_legacy_source_keys_without_json_scan(self):
        legacy_asset_id = "7a1f5b5e-eed3-4959-a676-d1a52fc2938d"
        legacy_reference = f"pipeline:{legacy_asset_id}"
        legacy_id = "legacy-pipeline-task"
        db.session.execute(
            PublicationTask.__table__.insert().values(
                id=legacy_id,
                status="completed",
                current_step="done",
                progress=100,
                params={
                    "feature": "mp3_video_workflow",
                    "user_id": "user-1",
                    "source_feature": "zsxq_attachment",
                    "source_task_id": legacy_reference,
                    "mp3_video_workflow": {"audio_asset_id": legacy_reference},
                },
                result={"source_task_id": legacy_reference},
                feature="mp3_video_workflow",
                user_id="user-1",
                source_feature="zsxq_attachment",
                source_task_id=legacy_reference[:36],
            )
        )
        db.session.commit()
        legacy = PublicationTask.query.filter_by(id=legacy_id).one()
        legacy.current_step = "legacy-done"
        legacy.sync_projection_fields()
        db.session.commit()
        normalized_asset_id = "9e9eca32-deca-4f98-84b0-969a8e2cb1aa"
        normalized_reference = f"pipeline:{normalized_asset_id}"
        normalized = PublicationTask(
            status="failed",
            current_step="failed",
            progress=58,
            params={
                "feature": "mp3_video_workflow",
                "user_id": "user-1",
                "source_feature": "content_pipeline_asset",
                "source_task_id": normalized_asset_id,
                "mp3_video_workflow": {"audio_asset_id": normalized_reference},
            },
            result={"source_task_id": normalized_asset_id},
        )
        cross_source_collision = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "mp3_video_workflow",
                "user_id": "user-1",
                "source_feature": "local_mp3_upload",
                "source_task_id": normalized_asset_id,
                "mp3_video_workflow": {"audio_asset_id": normalized_asset_id},
            },
            result={"source_task_id": normalized_asset_id},
        )
        db.session.add_all([normalized, cross_source_collision])
        db.session.commit()
        self.assertEqual(legacy_reference[:36], legacy.source_task_id)

        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            stats = mp3_api._audio_asset_workflow_stats(
                [legacy_reference, normalized_reference],
                user_id="user-1",
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(1, stats[legacy_reference]["total"])
        self.assertEqual(1, stats[legacy_reference]["succeeded"])
        self.assertEqual(legacy.id, stats[legacy_reference]["latest"]["job_id"])
        self.assertEqual(1, stats[normalized_reference]["total"])
        self.assertEqual(1, stats[normalized_reference]["failed"])
        self.assertEqual(normalized.id, stats[normalized_reference]["latest"]["job_id"])
        task_selects = [
            statement
            for statement in statements
            if statement.startswith("select")
            and " from publication_tasks " in statement
        ]
        self.assertEqual(1, len(task_selects), task_selects)
        self.assertNotIn("publication_tasks.params", task_selects[0])

    def test_upload_rejects_non_audio_extension_before_enqueue(self):
        with self.app.test_request_context(
            "/api/mp3-video-workflows/jobs",
            method="POST",
            data={"audio": (io.BytesIO(b"file"), "notes.txt")},
            content_type="multipart/form-data",
        ):
            response, status = mp3_api.create_job.__wrapped__(SimpleNamespace(id="user-1"))

        self.assertEqual(status, 400)
        self.assertIn("只支持音频文件", response.get_json()["message"])
        self.assertEqual(PublicationTask.query.count(), 0)

    def test_audio_upload_filename_preserves_mixed_unicode_stem(self):
        storage = SimpleNamespace(filename="AI芯片研究报告.mp3")

        filename, extension = mp3_api._audio_upload_filename(storage)

        self.assertEqual(filename, "AI芯片研究报告.mp3")
        self.assertEqual(extension, "mp3")

    def test_artifact_regeneration_releases_read_session_and_uses_narrow_cas_update(self):
        task = PublicationTask(
            id="mp3-artifact-regeneration",
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "mp3_video_workflow",
                "user_id": "user-1",
                "mp3_video_workflow": {"metadata": {"title": "Session 边界测试"}},
            },
            result={
                "plan": {"title": "旧标题"},
                "audio": {"filename": "input.mp3"},
                "artifacts": {
                    "xiaojunjun_background": {
                        "path": "/tmp/old-background.jpg",
                        "label": "旧背景图",
                    }
                },
                "logs": [],
            },
        )
        task_id = task.id
        db.session.add(task)
        db.session.commit()
        db.session.remove()

        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        def regenerate(*_args, **_kwargs):
            call_order.append("render")
            session = db.session()
            self.assertFalse(session.in_transaction(), "文件/浏览器生成阶段不得持有数据库事务")
            self.assertFalse(session.new)
            self.assertFalse(session.dirty)
            return {
                "path": "/tmp/new-background.jpg",
                "label": "新版背景图",
                "manual_title_text": "新标题",
            }

        def load_children(_task_id):
            call_order.append("children")
            return []

        call_order = []
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with (
                self.app.test_request_context(
                    "/api/mp3-video-workflows/jobs/mp3-artifact-regeneration/"
                    "artifacts/xiaojunjun_background/regenerate",
                    method="POST",
                    json={"title_text": "新标题"},
                ),
                patch.object(mp3_api, "regenerate_visual_artifact", side_effect=regenerate),
                patch.object(mp3_api, "_bilibili_upload_children", side_effect=load_children),
            ):
                response = mp3_api.regenerate_artifact.__wrapped__(
                    SimpleNamespace(id="user-1"),
                    task_id,
                    "xiaojunjun_background",
                )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertFalse(
            isinstance(response, tuple),
            response[0].get_json() if isinstance(response, tuple) else None,
        )
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(["children", "render"], call_order)
        self.assertEqual(
            "/tmp/new-background.jpg",
            payload["raw_result"]["artifacts"]["xiaojunjun_background"]["path"],
        )
        task_sql = [statement for statement in statements if "publication_tasks" in statement]
        update_indexes = [index for index, statement in enumerate(task_sql) if statement.startswith("update")]
        self.assertEqual(1, len(update_indexes), task_sql)
        update_sql = task_sql[update_indexes[0]]
        self.assertIn("result=", update_sql)
        self.assertIn("updated_at=", update_sql)
        self.assertIn("publication_tasks.updated_at =", update_sql)
        self.assertNotIn("task_video_path=", update_sql)
        self.assertFalse(
            any(statement.startswith("select") for statement in task_sql[update_indexes[0] + 1 :]),
            task_sql,
        )

    def test_artifact_regeneration_does_not_overwrite_a_concurrent_task_change(self):
        task = PublicationTask(
            id="mp3-artifact-cas-conflict",
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "mp3_video_workflow",
                "user_id": "user-1",
                "mp3_video_workflow": {},
            },
            result={"plan": {"title": "旧标题"}, "artifacts": {}, "logs": []},
        )
        task_id = task.id
        db.session.add(task)
        db.session.commit()
        db.session.remove()

        def regenerate(*_args, **_kwargs):
            changed = PublicationTask.query.filter_by(id=task_id).one()
            changed.result = {**(changed.result or {}), "concurrent_marker": "preserve-me"}
            changed.status = "processing"
            db.session.commit()
            db.session.remove()
            return {
                "path": "/tmp/conflicting-background.jpg",
                "label": "冲突背景图",
                "manual_title_text": "新标题",
            }

        with (
            self.app.test_request_context(
                "/api/mp3-video-workflows/jobs/mp3-artifact-cas-conflict/"
                "artifacts/xiaojunjun_background/regenerate",
                method="POST",
                json={"title_text": "新标题"},
            ),
            patch.object(mp3_api, "regenerate_visual_artifact", side_effect=regenerate),
        ):
            response, status = mp3_api.regenerate_artifact.__wrapped__(
                SimpleNamespace(id="user-1"),
                task_id,
                "xiaojunjun_background",
            )

        self.assertEqual(409, status)
        self.assertIn("状态已变化", response.get_json()["message"])
        db.session.remove()
        stored = PublicationTask.query.filter_by(id=task_id).one()
        self.assertEqual("processing", stored.status)
        self.assertEqual("preserve-me", stored.result["concurrent_marker"])
        self.assertNotIn("xiaojunjun_background", stored.result.get("artifacts") or {})

    def test_continue_full_video_does_not_reload_wide_task_after_commit(self):
        task = PublicationTask(
            id="mp3-continue-full-video",
            status="completed",
            current_step="keyframes_ready",
            progress=68,
            params={
                "feature": "mp3_video_workflow",
                "user_id": "user-1",
                "mp3_video_workflow": {"render_mode": "keyframes"},
            },
            result={
                "render_mode": "keyframes",
                "keyframes": {"landscape": [{"path": "/tmp/keyframe.jpg"}]},
                "plan": {"audio": {"start_seconds": 0, "end_seconds": 60}},
                "logs": [],
            },
        )
        task_id = task.id
        db.session.add(task)
        db.session.commit()
        db.session.remove()

        statements = []
        call_order = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        def load_children(_task_id):
            call_order.append("children")
            return []

        def enqueue(*_args, **_kwargs):
            call_order.append("enqueue")
            self.assertFalse(
                db.session().in_transaction(),
                "broker 投递前不得因访问 commit 后过期 ORM 重新开启事务",
            )
            return SimpleNamespace(id="celery-full-video")

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with (
                self.app.test_request_context(
                    "/api/mp3-video-workflows/jobs/mp3-continue-full-video/continue-full-video",
                    method="POST",
                    json={},
                ),
                patch.object(mp3_api, "_bilibili_upload_children", side_effect=load_children),
                patch.object(mp3_api, "apply_async_after_db_release", side_effect=enqueue),
            ):
                response = mp3_api.continue_full_video.__wrapped__(
                    SimpleNamespace(id="user-1"),
                    task_id,
                )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertFalse(
            isinstance(response, tuple),
            response[0].get_json() if isinstance(response, tuple) else None,
        )
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(["children", "enqueue"], call_order)

        task_sql = [statement for statement in statements if "publication_tasks" in statement]
        update_indexes = [index for index, statement in enumerate(task_sql) if statement.startswith("update")]
        self.assertEqual(2, len(update_indexes), task_sql)
        first_update, final_update = update_indexes
        self.assertFalse(
            any(
                statement.startswith("select")
                and ("publication_tasks.params" in statement or "publication_tasks.result" in statement)
                for statement in task_sql[first_update + 1 :]
            ),
            task_sql,
        )
        celery_update = task_sql[final_update]
        self.assertIn("celery_task_id=", celery_update)
        self.assertNotIn("params=", celery_update)
        self.assertNotIn("result=", celery_update)
        self.assertNotIn("status=", celery_update)

        db.session.remove()
        stored = PublicationTask.query.filter_by(id=task_id).one()
        self.assertEqual("pending", stored.status)
        self.assertEqual("queued_full_video", stored.current_step)
        self.assertEqual("celery-full-video", stored.celery_task_id)

    def test_continue_full_video_enqueue_failure_uses_narrow_failure_update(self):
        task = PublicationTask(
            id="mp3-continue-full-video-enqueue-failure",
            status="completed",
            current_step="keyframes_ready",
            progress=68,
            params={
                "feature": "mp3_video_workflow",
                "user_id": "user-1",
                "mp3_video_workflow": {"render_mode": "keyframes"},
            },
            result={
                "render_mode": "keyframes",
                "keyframes": {"landscape": [{"path": "/tmp/keyframe.jpg"}]},
                "logs": [],
            },
        )
        task_id = task.id
        db.session.add(task)
        db.session.commit()
        db.session.remove()

        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with (
                self.app.test_request_context(
                    "/api/mp3-video-workflows/jobs/mp3-continue-full-video-enqueue-failure/"
                    "continue-full-video",
                    method="POST",
                    json={},
                ),
                patch.object(mp3_api, "_bilibili_upload_children", return_value=[]),
                patch.object(
                    mp3_api,
                    "apply_async_after_db_release",
                    side_effect=RuntimeError("broker unavailable"),
                ),
            ):
                response, status = mp3_api.continue_full_video.__wrapped__(
                    SimpleNamespace(id="user-1"),
                    task_id,
                )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(502, status)
        self.assertIn("broker unavailable", response.get_json()["message"])
        task_sql = [statement for statement in statements if "publication_tasks" in statement]
        update_indexes = [index for index, statement in enumerate(task_sql) if statement.startswith("update")]
        self.assertEqual(2, len(update_indexes), task_sql)
        self.assertFalse(
            any(
                statement.startswith("select")
                and ("publication_tasks.params" in statement or "publication_tasks.result" in statement)
                for statement in task_sql[update_indexes[0] + 1 :]
            ),
            task_sql,
        )
        failure_update = task_sql[update_indexes[-1]]
        self.assertIn("status=", failure_update)
        self.assertIn("current_step=", failure_update)
        self.assertIn("error_message=", failure_update)
        self.assertNotIn("params=", failure_update)
        self.assertNotIn("result=", failure_update)

        db.session.remove()
        stored = PublicationTask.query.filter_by(id=task_id).one()
        self.assertEqual("failed", stored.status)
        self.assertEqual("failed", stored.current_step)
        self.assertIn("broker unavailable", stored.error_message)

    def test_retry_and_regenerate_routes_share_the_safe_enqueue_helper(self):
        retry_task = PublicationTask(
            id="mp3-retry-safe-enqueue",
            status="failed",
            current_step="failed",
            progress=58,
            params={
                "feature": "mp3_video_workflow",
                "user_id": "user-1",
                "mp3_video_workflow": {},
            },
            result={"logs": [], "error": "old failure"},
        )
        regenerate_task = PublicationTask(
            id="mp3-regenerate-safe-enqueue",
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "mp3_video_workflow",
                "user_id": "user-1",
                "mp3_video_workflow": {},
            },
            result={"plan": {"audio": {"speed": 1.2}}, "logs": []},
        )
        db.session.add_all([retry_task, regenerate_task])
        db.session.commit()
        retry_task_id = retry_task.id
        regenerate_task_id = regenerate_task.id
        db.session.remove()

        retry_payload = {"job_id": retry_task_id, "plan": {"audio": {}}}
        with (
            self.app.test_request_context(
                "/api/mp3-video-workflows/jobs/mp3-retry-safe-enqueue/retry",
                method="POST",
                json={},
            ),
            patch.object(
                mp3_api,
                "_commit_and_enqueue_prepared_job",
                return_value=(retry_payload, None),
            ) as enqueue_retry,
        ):
            response = mp3_api.retry_job.__wrapped__(
                SimpleNamespace(id="user-1"),
                retry_task_id,
            )
        self.assertTrue(response.get_json()["success"])
        enqueue_retry.assert_called_once()
        self.assertEqual("续跑任务入队失败", enqueue_retry.call_args.kwargs["enqueue_error_prefix"])
        db.session.rollback()
        db.session.remove()

        regenerate_payload = {
            "job_id": regenerate_task_id,
            "plan": {"audio": {"speed": 1.2}},
        }
        with (
            self.app.test_request_context(
                "/api/mp3-video-workflows/jobs/mp3-regenerate-safe-enqueue/regenerate-videos",
                method="POST",
                json={},
            ),
            patch.object(
                mp3_api,
                "_prepare_job_for_video_regeneration",
                return_value=(True, "ok"),
            ),
            patch.object(
                mp3_api,
                "_commit_and_enqueue_prepared_job",
                return_value=(regenerate_payload, None),
            ) as enqueue_regenerate,
        ):
            response = mp3_api.regenerate_videos.__wrapped__(
                SimpleNamespace(id="user-1"),
                regenerate_task_id,
            )
        self.assertTrue(response.get_json()["success"])
        enqueue_regenerate.assert_called_once()
        self.assertEqual(
            "视频重生成任务入队失败",
            enqueue_regenerate.call_args.kwargs["enqueue_error_prefix"],
        )


if __name__ == "__main__":
    unittest.main()
