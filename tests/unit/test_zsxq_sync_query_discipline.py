import sys
import tempfile
import time
import unittest
import inspect as python_inspect
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from sqlalchemy import event, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import load_only


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.zsxq_sync import (  # noqa: E402
    ZsxqAttachment,
    ZsxqSyncRun,
    ZsxqSyncRunDownload,
    ZsxqTopic,
    ZsxqTopicComment,
)
from services.zsxq_sync_service import TOPIC_UPDATE_SNAPSHOT_FIELDS, ZsxqSyncService  # noqa: E402


class ZsxqSyncQueryDisciplineTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            ZSXQ_DATA_DIR=self.temp_dir.name,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.temp_dir.cleanup()

    @contextmanager
    def _capture_sql(self):
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            yield statements
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

    def _add_downloaded_attachment(self, attachment_id, topic_id, path):
        attachment = ZsxqAttachment(
            id=attachment_id,
            group_id="group-1",
            tab_id="tab-1",
            topic_id=topic_id,
            attachment_key=f"key-{attachment_id}",
            attachment_type="file",
            name=f"{attachment_id}.pdf",
            download_count=50,
            local_path=str(path),
            download_status="downloaded",
            raw_payload={"download_url": "https://example.invalid/large-payload"},
        )
        db.session.add(attachment)
        return attachment

    @staticmethod
    def _comment_topic(text="内容未变化"):
        return {
            "topic_id": "topic-comment-1",
            "show_comments": [{
                "comment_id": "comment-1",
                "owner": {"user_id": "user-1", "name": "测试用户"},
                "text": text,
                "create_time": "2026-07-24T12:00:00+08:00",
                "likes_count": 3,
                "replies_count": 0,
                "rewards_count": 0,
                "sticky": False,
            }],
        }

    def test_unchanged_topic_comment_does_not_issue_update(self):
        service = ZsxqSyncService()
        tab = {"id": "tab-1", "label": "外资研报"}
        service._upsert_topic_comments("group-1", tab, self._comment_topic())
        db.session.commit()
        db.session.remove()

        with self._capture_sql() as statements:
            stats = service._upsert_topic_comments(
                "group-1",
                tab,
                self._comment_topic(),
            )
            db.session.flush()

        updates = [
            sql for sql in statements
            if sql.startswith("update zsxq_topic_comments set")
        ]
        self.assertEqual(0, stats["comments_updated"])
        self.assertEqual([], updates)
        record = ZsxqTopicComment.query.filter_by(comment_id="comment-1").one()
        self.assertIsNotNone(record.raw_payload_hash)

    def test_changed_topic_comment_updates_payload_and_hash_once(self):
        service = ZsxqSyncService()
        tab = {"id": "tab-1", "label": "外资研报"}
        service._upsert_topic_comments("group-1", tab, self._comment_topic())
        db.session.commit()
        db.session.remove()

        with self._capture_sql() as statements:
            stats = service._upsert_topic_comments(
                "group-1",
                tab,
                self._comment_topic("内容发生变化"),
            )
            db.session.flush()

        updates = [
            sql for sql in statements
            if sql.startswith("update zsxq_topic_comments set")
        ]
        self.assertEqual(1, stats["comments_updated"])
        self.assertEqual(1, len(updates), updates)
        self.assertIn("raw_payload", updates[0])
        self.assertIn("raw_payload_hash", updates[0])
        self.assertIn("text=", updates[0])

    def test_postgres_schema_contract_uses_one_compact_catalog_query(self):
        service = ZsxqSyncService()
        row = SimpleNamespace(
            existing_tables=[
                "zsxq_sync_runs",
                "zsxq_sync_run_downloads",
                "zsxq_topics",
                "zsxq_topic_comments",
                "zsxq_attachments",
                "zsxq_sync_cursors",
            ],
            table_columns={
                "zsxq_sync_runs": ["id", "download_ledger_complete"],
                "zsxq_attachments": ["id", "publish_status"],
            },
            has_download_ledger_privilege=True,
        )
        result = SimpleNamespace(one=lambda: row)

        with patch.object(db.session, "execute", return_value=result) as execute:
            existing_tables, table_columns, has_privilege = service._postgres_schema_contract()

        execute.assert_called_once()
        sql = str(execute.call_args.args[0]).lower()
        self.assertIn("information_schema.columns", sql)
        self.assertIn("jsonb_object_agg", sql)
        self.assertIn("has_table_privilege", sql)
        self.assertNotIn("pg_catalog.pg_attribute", sql)
        self.assertEqual(set(row.existing_tables), existing_tables)
        self.assertEqual({"id", "download_ledger_complete"}, table_columns["zsxq_sync_runs"])
        self.assertTrue(has_privilege)

    def test_dispatch_commits_do_not_expire_next_attachment_or_run_into_full_refresh(self):
        run = ZsxqSyncRun(
            id="run-1",
            group_id="group-1",
            group_name="前沿信息收录",
            result={},
        )
        db.session.add(run)
        paths = []
        for index in range(2):
            path = Path(self.temp_dir.name) / f"report-{index}.pdf"
            path.write_bytes(b"%PDF-1.4\nfixture")
            paths.append(path)
            self._add_downloaded_attachment(f"attachment-{index}", f"topic-{index}", path)
        db.session.commit()
        run = (
            ZsxqSyncRun.query
            .options(load_only(ZsxqSyncRun.id, ZsxqSyncRun.group_id, ZsxqSyncRun.group_name))
            .filter(ZsxqSyncRun.id == "run-1")
            .one()
        )

        def commit_dispatch(**_kwargs):
            db.session.commit()
            return {"queued": True, "task_id": "queued-task"}

        sync_result = {
            "downloaded_pdf_files": [
                {"attachment_id": "attachment-0", "path": str(paths[0])},
                {"attachment_id": "attachment-1", "path": str(paths[1])},
            ]
        }
        config = {"enabled": True, "pdf_min_download_count": 40, "wait_for_completion": False}

        service = ZsxqSyncService()
        with patch.object(service, "_find_existing_auto_script_material_task", return_value=None):
            with patch.object(service, "_create_auto_report_script_material_task", side_effect=commit_dispatch):
                with self._capture_sql() as statements:
                    summary = service._submit_auto_script_material_dispatch_for_run(
                        run,
                        sync_result,
                        config,
                    )

        self.assertEqual(summary["pdf"]["queued"], 2)
        attachment_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_attachments " in sql
        ]
        self.assertEqual(len(attachment_selects), 1)
        self.assertNotIn("raw_payload", attachment_selects[0])
        self.assertFalse(any(" from zsxq_sync_runs " in sql for sql in statements))

    def test_sync_freezes_run_identity_before_first_commit(self):
        source = python_inspect.getsource(ZsxqSyncService.sync_tabs)
        identity_offset = source.index("run_identity = SimpleNamespace(")
        first_commit_offset = source.index("db.session.commit()")
        dispatch_offset = source.index("run=run_identity")

        self.assertLess(identity_offset, first_commit_offset)
        self.assertGreater(dispatch_offset, first_commit_offset)

    def test_remote_io_and_waits_release_database_transaction_first(self):
        boundaries = (
            (
                ZsxqSyncService._get_topics_with_retry,
                'end_db_transaction_before_external_io("ZSXQ topic-list request")',
                "zsxq_service.get_topics(",
            ),
            (
                ZsxqSyncService._hydrate_truncated_topic_comments,
                'end_db_transaction_before_external_io("ZSXQ comment hydration")',
                "zsxq_service.get_topic_comments(",
            ),
            (
                ZsxqSyncService._download_attachment_file,
                'end_db_transaction_before_external_io("ZSXQ attachment download")',
                "zsxq_service.fetch_topic_file(",
            ),
            (
                ZsxqSyncService._human_pause,
                'end_db_transaction_before_external_io("ZSXQ pacing wait")',
                "time.sleep(",
            ),
        )

        for method, boundary, external_io in boundaries:
            with self.subTest(method=method.__name__):
                source = python_inspect.getsource(method)
                self.assertIn(boundary, source)
                self.assertLess(source.index(boundary), source.index(external_io))

    def test_page_snapshot_transaction_ends_before_topic_processing(self):
        source = python_inspect.getsource(ZsxqSyncService._sync_one_tab)
        refresh_offset = source.index(
            "self._refresh_existing_attachment_snapshots_for_page("
        )
        commit_offset = source.index("db.session.commit()", refresh_offset)
        topic_loop_offset = source.index("for topic in topics:", commit_offset)

        self.assertLess(refresh_offset, commit_offset)
        self.assertLess(commit_offset, topic_loop_offset)

    def test_pacing_wait_ends_live_read_transaction_before_sleep(self):
        service = ZsxqSyncService()
        db.session.execute(text("SELECT 1"))
        self.assertTrue(db.session().in_transaction())

        with patch("services.zsxq_sync_service.time.sleep") as sleep:
            service._human_pause(0, 0)

        self.assertFalse(db.session().in_transaction())
        sleep.assert_called_once_with(0.0)

    def test_embedded_publish_source_refs_skip_clean_task_payload_query(self):
        service = ZsxqSyncService()
        snapshot = SimpleNamespace(
            id="publish-1",
            status="completed",
            feature="zsxq_clean_output_publish",
            source_task_id="clean-1",
            params={
                "feature": "zsxq_clean_output_publish",
                "zsxq_publish": {
                    "source_task_id": "clean-1",
                    "metadata": {
                        "source_refs": [{
                            "attachment_id": "attachment-1",
                            "group_id": "group-1",
                            "tab_id": "tab-1",
                            "topic_id": "topic-1",
                            "source_path": "/tmp/source.pdf",
                            "output_paths": ["/tmp/clean/report.pdf"],
                        }],
                    },
                },
            },
            result={"status": "completed", "verified_count": 1},
            completed_at=None,
            updated_at=None,
        )

        with patch.object(service, "_sync_tables_ready", return_value=True), \
             patch.object(
                 service,
                 "_apply_publish_context_to_attachments",
                 return_value={"updated": 1, "matched": 1},
             ) as apply_context:
            with self._capture_sql() as statements:
                result = service.update_attachment_publish_status_from_publish_task(snapshot)

        self.assertEqual(result, {"updated": 1, "matched": 1})
        self.assertFalse(any(" from publication_tasks " in sql for sql in statements), statements)
        context = apply_context.call_args.args[0]
        self.assertEqual(context["source_refs"][0]["attachment_id"], "attachment-1")
        self.assertEqual(
            context["source_refs"][0]["output_paths"],
            [str(Path("/tmp/clean/report.pdf").resolve())],
        )

    def test_publish_attachment_status_loads_all_source_refs_in_one_query(self):
        for index in range(9):
            attachment = self._add_downloaded_attachment(
                f"publish-attachment-{index}",
                f"publish-topic-{index}",
                Path(self.temp_dir.name) / f"publish-{index}.pdf",
            )
            attachment.name = f"publish-{index}.pdf"
        db.session.commit()
        db.session.remove()

        source_refs = [
            {
                "attachment_id": f"publish-attachment-{index}",
                "group_id": "group-1",
                "tab_id": "tab-1",
                "topic_id": f"publish-topic-{index}",
                "name": f"publish-{index}.pdf",
            }
            for index in range(9)
        ]
        context = {
            "status": {
                "publish_status": "submitted",
                "publish_task_id": "publish-task-1",
                "publish_source_task_id": "clean-task-1",
                "publish_submitted_count": 9,
            },
            "outcomes": {"by_path": {}, "by_name": {}},
            "source_refs": source_refs,
        }

        service = ZsxqSyncService()
        with patch.object(
            service,
            "_status_for_source_ref",
            return_value=context["status"],
        ):
            with self._capture_sql() as statements:
                result = service._apply_publish_context_to_attachments(context)
                db.session.commit()

        attachment_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_attachments " in sql
        ]
        attachment_updates = [
            sql for sql in statements
            if sql.startswith("update zsxq_attachments ")
        ]
        self.assertEqual({"updated": 9, "matched": 9}, result)
        self.assertEqual(1, len(attachment_selects), attachment_selects)
        self.assertNotIn("raw_payload", attachment_selects[0])
        self.assertEqual(1, len(attachment_updates), attachment_updates)

    def test_reusable_downloads_never_read_raw_payload_or_refresh_rows_after_commit(self):
        for index in range(2):
            path = Path(self.temp_dir.name) / f"cached-{index}.pdf"
            path.write_bytes(b"%PDF-1.4\nfixture")
            self._add_downloaded_attachment(f"cached-{index}", "topic-1", path)
        db.session.commit()

        service = ZsxqSyncService()
        with self._capture_sql() as statements:
            result = service._download_topic_attachments(
                cookie="cookie",
                group_id="group-1",
                tab={"id": "tab-1", "label": "外资研报"},
                topic={"topic_id": "topic-1"},
                download_file_types={"pdf"},
            )

        self.assertEqual(result["attachments_reused_local"], 2)
        attachment_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_attachments " in sql
        ]
        self.assertEqual(len(attachment_selects), 1)
        self.assertNotIn("raw_payload", attachment_selects[0])

    def test_new_download_persists_attachment_state_and_run_ledger_together(self):
        target = Path(self.temp_dir.name) / "new-report.pdf"
        target.write_bytes(b"%PDF-1.4\nfixture")
        run = ZsxqSyncRun(
            id="run-new-download-1",
            group_id="group-1",
            status="running",
            result={},
        )
        attachment = ZsxqAttachment(
            id="new-download-1",
            group_id="group-1",
            tab_id="tab-1",
            topic_id="topic-1",
            attachment_key="new-download-1",
            attachment_type="file",
            name="new-report.pdf",
            file_ext="pdf",
            download_status="pending",
            raw_payload={"download_url": "https://example.invalid/report.pdf"},
        )
        db.session.add_all([run, attachment])
        db.session.commit()

        service = ZsxqSyncService()
        with patch.object(service, "_attachment_download_pause"), patch.object(
            service,
            "_download_attachment_file",
            return_value=str(target),
        ):
            result = service._download_topic_attachments(
                cookie="cookie",
                group_id="group-1",
                tab={"id": "tab-1", "label": "外资研报"},
                topic={"topic_id": "topic-1"},
                download_file_types={"pdf"},
                remaining_downloads=1,
                run_id=run.id,
            )

        self.assertEqual(result["attachments_downloaded"], 1)
        persisted_attachment = db.session.get(ZsxqAttachment, attachment.id)
        ledger = db.session.get(ZsxqSyncRunDownload, (run.id, attachment.id))
        self.assertEqual(persisted_attachment.download_status, "downloaded")
        self.assertEqual(persisted_attachment.local_path, str(target))
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger.local_path, str(target))
        self.assertEqual(ledger.file_ext, "pdf")

    def test_download_result_db_disconnect_rolls_back_and_remains_retryable(self):
        target = Path(self.temp_dir.name) / "downloaded-before-disconnect.pdf"
        target.write_bytes(b"%PDF-1.4\nfixture")
        attachment = ZsxqAttachment(
            id="download-disconnect-1",
            group_id="group-1",
            tab_id="tab-1",
            topic_id="topic-1",
            attachment_key="download-disconnect-1",
            attachment_type="file",
            name="download-disconnect.pdf",
            file_ext="pdf",
            download_status="pending",
            raw_payload={"download_url": "https://example.invalid/report.pdf"},
        )
        db.session.add(attachment)
        db.session.commit()
        disconnect = OperationalError(
            "SELECT zsxq_attachments",
            {},
            Exception("server closed the connection unexpectedly"),
        )

        service = ZsxqSyncService()
        with patch.object(service, "_attachment_download_pause"), patch.object(
            service,
            "_download_attachment_file",
            return_value=str(target),
        ), patch.object(
            service,
            "_load_attachment_for_download_update",
            side_effect=disconnect,
        ) as load_for_update:
            with self.assertRaises(OperationalError):
                service._download_topic_attachments(
                    cookie="cookie",
                    group_id="group-1",
                    tab={"id": "tab-1", "label": "外资研报"},
                    topic={
                        "topic_id": "topic-1",
                        "files": [{
                            "file_id": "download-disconnect-1",
                            "name": "download-disconnect.pdf",
                            "mime_type": "application/pdf",
                        }],
                    },
                    download_file_types={"pdf"},
                    remaining_downloads=1,
                )

        load_for_update.assert_called_once_with(attachment.id)
        db.session.remove()
        persisted = db.session.get(ZsxqAttachment, attachment.id)
        self.assertEqual(persisted.download_status, "pending")
        self.assertIsNone(persisted.local_path)

    def test_retryable_sync_error_does_not_persist_terminal_failed_state(self):
        run = ZsxqSyncRun(
            id="sync-disconnect-1",
            group_id="group-1",
            status="queued",
            requested_tabs=[{"id": "tab-1", "label": "外资研报"}],
            result={"queued": True},
        )
        db.session.add(run)
        db.session.commit()
        run_id = run.id
        disconnect = OperationalError(
            "SELECT zsxq_topics",
            {},
            Exception("server closed the connection unexpectedly"),
        )
        service = ZsxqSyncService()

        with patch.object(service, "ensure_schema"), patch.object(
            service,
            "_sync_one_tab",
            side_effect=disconnect,
        ):
            with self.assertRaises(OperationalError):
                service.sync_tabs(
                    run_id=run_id,
                    cookie="cookie",
                    group_id="group-1",
                    tabs=[{"id": "tab-1", "label": "外资研报"}],
                )

        db.session.remove()
        persisted = db.session.get(ZsxqSyncRun, run_id)
        self.assertEqual(persisted.status, "running")
        self.assertIsNone(persisted.completed_at)
        self.assertIsNone(persisted.error_message)

    def test_current_topic_payload_avoids_immediate_json_roundtrip(self):
        path = Path(self.temp_dir.name) / "fresh-payload-cache"
        path.write_bytes(b"%PDF-1.4\nfixture")
        payload = {
            "file_id": "fresh-payload-1",
            "name": "fresh-payload-cache",
            "mime_type": "application/pdf",
            "download_count": 50,
        }
        db.session.add(
            ZsxqAttachment(
                id="fresh-payload-row",
                group_id="group-1",
                tab_id="tab-1",
                topic_id="topic-fresh",
                attachment_key=payload["file_id"],
                attachment_type="file",
                name=payload["name"],
                download_count=50,
                local_path=str(path),
                download_status="downloaded",
                raw_payload=payload,
            )
        )
        db.session.commit()

        service = ZsxqSyncService()
        with self._capture_sql() as statements:
            result = service._download_topic_attachments(
                cookie="cookie",
                group_id="group-1",
                tab={"id": "tab-1", "label": "外资研报"},
                topic={"topic_id": "topic-fresh", "files": [payload]},
                download_file_types={"pdf"},
            )

        self.assertEqual(result["attachments_reused_local"], 1)
        attachment_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_attachments " in sql
        ]
        self.assertEqual(len(attachment_selects), 1)
        self.assertNotIn("raw_payload", attachment_selects[0])

    def test_attachment_upsert_and_download_share_one_lightweight_snapshot(self):
        path = Path(self.temp_dir.name) / "batched-existing.pdf"
        path.write_bytes(b"%PDF-1.4\nfixture")
        payload = {
            "file_id": "batched-file-1",
            "name": "batched-existing.pdf",
            "mime_type": "application/pdf",
            "download_count": 80,
        }
        self._add_downloaded_attachment("batched-row-1", "topic-batched", path)
        attachment = db.session.get(ZsxqAttachment, "batched-row-1")
        attachment.attachment_key = payload["file_id"]
        db.session.commit()
        db.session.remove()

        service = ZsxqSyncService()
        topic = {"topic_id": "topic-batched", "files": [payload]}
        with self._capture_sql() as statements:
            upsert_stats, records = service._upsert_attachments(
                "group-1",
                {"id": "tab-1", "label": "外资研报"},
                topic,
            )
            download_stats = service._download_topic_attachments(
                cookie="cookie",
                group_id="group-1",
                tab={"id": "tab-1", "label": "外资研报"},
                topic=topic,
                download_file_types={"pdf"},
                records=records,
            )

        self.assertEqual(upsert_stats["attachments_seen"], 1)
        self.assertEqual(download_stats["attachments_reused_local"], 1)
        attachment_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_attachments " in sql
        ]
        self.assertEqual(len(attachment_selects), 1, attachment_selects)
        self.assertIn("zsxq_attachments.local_path", attachment_selects[0])
        self.assertNotIn("raw_payload", attachment_selects[0])

    def test_attachment_upsert_batches_existing_rows_into_one_update(self):
        payloads = []
        for index in range(2):
            path = Path(self.temp_dir.name) / f"batched-update-{index}.pdf"
            path.write_bytes(b"%PDF-1.4\nfixture")
            attachment = self._add_downloaded_attachment(
                f"batched-update-row-{index}",
                "topic-batched-updates",
                path,
            )
            attachment.attachment_key = f"batched-update-file-{index}"
            payloads.append({
                "file_id": f"batched-update-file-{index}",
                "name": f"renamed-{index}.pdf",
                "mime_type": "application/pdf",
                "download_count": 80 + index,
            })
        db.session.commit()
        db.session.remove()

        service = ZsxqSyncService()
        with self._capture_sql() as statements:
            stats, _records = service._upsert_attachments(
                "group-1",
                {"id": "tab-1", "label": "外资研报"},
                {"topic_id": "topic-batched-updates", "files": payloads},
            )
            db.session.commit()

        attachment_updates = [
            sql for sql in statements
            if sql.startswith("update zsxq_attachments ")
        ]
        self.assertEqual(2, stats["attachments_seen"])
        self.assertEqual(1, len(attachment_updates), attachment_updates)
        refreshed = db.session.get(ZsxqAttachment, "batched-update-row-1")
        self.assertEqual("renamed-1.pdf", refreshed.name)
        self.assertEqual(81, refreshed.download_count)

        unchanged_updated_at = refreshed.updated_at
        db.session.remove()
        service._upsert_attachments(
            "group-1",
            {"id": "tab-1", "label": "外资研报"},
            {"topic_id": "topic-batched-updates", "files": payloads},
        )
        db.session.commit()
        unchanged = db.session.get(ZsxqAttachment, "batched-update-row-1")
        self.assertEqual(unchanged_updated_at, unchanged.updated_at)

    def test_postgres_attachment_refresh_compiles_as_one_set_update(self):
        service = ZsxqSyncService()
        table = ZsxqAttachment.__table__
        fields = (
            "group_id",
            "tab_id",
            "topic_id",
            "attachment_key",
            "attachment_type",
            "name",
            "file_id",
            "download_count",
            "file_ext",
            "raw_payload",
        )
        incoming = service._postgres_attachment_projection_values(
            table,
            fields,
            [{"id": "row-1", **{field: None for field in fields}}],
        )
        statement = service._postgres_attachment_projection_update(
            table,
            fields,
            incoming,
        )
        compiled = str(statement.compile(dialect=postgresql.dialect())).lower()

        self.assertIn("update zsxq_attachments", compiled)
        self.assertIn(" from (values ", compiled)
        self.assertIn("incoming_attachments", compiled)
        self.assertIn("cast(incoming_attachments.id as varchar(36))", compiled)
        self.assertIn("cast(incoming_attachments.file_id as varchar(128))", compiled)
        self.assertIn("cast(incoming_attachments.download_count as integer)", compiled)
        self.assertIn("cast(incoming_attachments.raw_payload as jsonb)", compiled)

    def test_unchanged_attachment_download_state_is_not_rewritten(self):
        attachment = ZsxqAttachment(
            id="unchanged-download-state",
            group_id="group-1",
            tab_id="tab-1",
            topic_id="topic-1",
            attachment_key="unchanged-download-state",
            attachment_type="file",
            name="unchanged.pdf",
            download_status="skipped",
            error_message="低于下载阈值",
        )
        db.session.add(attachment)
        db.session.commit()
        original_updated_at = attachment.updated_at
        db.session.remove()

        service = ZsxqSyncService()
        with self._capture_sql() as statements:
            service._update_attachment_download_state(
                attachment.id,
                download_status="skipped",
                error_message="低于下载阈值",
            )
            db.session.commit()

        refreshed = db.session.get(ZsxqAttachment, attachment.id)
        attachment_updates = [
            sql for sql in statements
            if sql.startswith("update zsxq_attachments ")
        ]
        self.assertEqual(original_updated_at, refreshed.updated_at)
        self.assertEqual(1, len(attachment_updates), statements)
        self.assertIn("download_status is not", attachment_updates[0])

    def test_attachment_snapshots_are_loaded_once_per_page_and_survive_commits(self):
        topics = []
        for index in range(2):
            path = Path(self.temp_dir.name) / f"page-batched-{index}.pdf"
            path.write_bytes(b"%PDF-1.4\nfixture")
            attachment = self._add_downloaded_attachment(
                f"page-row-{index}",
                f"topic-page-{index}",
                path,
            )
            attachment.attachment_key = f"page-file-{index}"
            topics.append({
                "topic_id": f"topic-page-{index}",
                "files": [{
                    "file_id": f"page-file-{index}",
                    "name": f"page-batched-{index}.pdf",
                    "mime_type": "application/pdf",
                    "download_count": 80,
                }],
            })
        db.session.commit()
        db.session.remove()

        service = ZsxqSyncService()
        tab = {"id": "tab-1", "label": "外资研报"}
        with self._capture_sql() as statements:
            snapshots = service._load_existing_attachment_snapshots(
                "group-1",
                "tab-1",
                topics,
            )
            refreshed_count = service._refresh_existing_attachment_snapshots_for_page(
                "group-1",
                tab,
                topics,
                snapshots,
            )
            db.session.commit()
            for topic in topics:
                topic_id = topic["topic_id"]
                upsert_stats, records = service._upsert_attachments(
                    "group-1",
                    tab,
                    topic,
                    existing_records=snapshots[topic_id],
                    existing_records_persisted=True,
                )
                download_stats = service._download_topic_attachments(
                    cookie="cookie",
                    group_id="group-1",
                    tab=tab,
                    topic=topic,
                    download_file_types={"pdf"},
                    records=records,
                )
                self.assertEqual(upsert_stats["attachments_seen"], 1)
                self.assertEqual(download_stats["attachments_reused_local"], 1)
                db.session.commit()

        attachment_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_attachments " in sql
        ]
        self.assertEqual(len(attachment_selects), 1, attachment_selects)
        self.assertIn("zsxq_attachments.topic_id in", attachment_selects[0])
        self.assertNotIn("raw_payload", attachment_selects[0])
        attachment_updates = [
            sql for sql in statements
            if sql.startswith("update zsxq_attachments ")
        ]
        self.assertEqual(2, refreshed_count)
        self.assertEqual(1, len(attachment_updates), attachment_updates)

    def test_existing_topics_are_loaded_once_per_page_as_lightweight_snapshots(self):
        for index in range(2):
            db.session.add(ZsxqTopic(
                id=f"topic-row-{index}",
                group_id="group-1",
                group_name="前沿信息收录",
                tab_id="tab-1",
                tab_label="外资研报",
                topic_id=f"topic-{index}",
                title=f"标题 {index}",
                raw_payload={"large": "payload"},
            ))
        db.session.commit()
        db.session.remove()

        service = ZsxqSyncService()
        with self._capture_sql() as statements:
            snapshots = service._load_existing_topic_snapshots(
                "group-1",
                "tab-1",
                [{"topic_id": "topic-0"}, {"topic_id": "topic-1"}],
            )
            db.session.commit()

        self.assertEqual(set(snapshots), {"topic-0", "topic-1"})
        topic_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_topics " in sql
        ]
        self.assertEqual(len(topic_selects), 1, topic_selects)
        self.assertNotIn("zsxq_topics.raw_payload as", topic_selects[0])
        self.assertNotIn("zsxq_topics.content as", topic_selects[0])
        self.assertNotIn("zsxq_topics.question_text as", topic_selects[0])
        self.assertNotIn("zsxq_topics.answer_text as", topic_selects[0])
        self.assertNotIn("zsxq_topics.article_excerpt as", topic_selects[0])

    def test_topic_snapshot_survives_attachment_commits_without_full_row_refresh(self):
        db.session.add(
            ZsxqTopic(
                id="topic-row-1",
                group_id="group-1",
                group_name="前沿信息收录",
                tab_id="tab-1",
                tab_label="外资研报",
                topic_id="topic-1",
                title="旧标题",
                author_name="旧作者",
                content="旧内容",
                create_time="2026-07-18T10:00:00+08:00",
                last_sync_run_id="old-run",
                raw_payload={},
            )
        )
        db.session.commit()
        topic_record = (
            ZsxqTopic.query
            .options(load_only(*(getattr(ZsxqTopic, field) for field in ("id", *TOPIC_UPDATE_SNAPSHOT_FIELDS))))
            .filter(ZsxqTopic.id == "topic-row-1")
            .one()
        )
        snapshot = {field: getattr(topic_record, field) for field in TOPIC_UPDATE_SNAPSHOT_FIELDS}
        db.session.commit()

        payload = {
            "topic_id": "topic-1",
            "talk": {
                "text": "新内容",
                "create_time": "2026-07-18T11:00:00+08:00",
                "owner": {"name": "新作者"},
            },
        }
        service = ZsxqSyncService()
        with self._capture_sql() as statements:
            changed, topic_for_external_scan = service._update_existing_topic(
                topic_record,
                payload,
                {"id": "tab-1", "label": "外资研报"},
                SimpleNamespace(id="run-2"),
                previous=snapshot,
            )
            service._scan_external_document_links(topic_for_external_scan)
            db.session.flush()

        self.assertTrue(changed)
        self.assertFalse(
            any(sql.startswith("select") and " from zsxq_topics " in sql for sql in statements),
            statements,
        )

    def test_unchanged_topic_payload_skips_json_rewrite(self):
        payload = {
            "topic_id": "topic-unchanged",
            "talk": {
                "text": "内容不变",
                "create_time": "2026-07-22T10:00:00+08:00",
                "owner": {"name": "作者"},
            },
        }
        service = ZsxqSyncService()
        meta = service._topic_meta(payload)
        db.session.add(ZsxqTopic(
            id="topic-row-unchanged",
            group_id="group-1",
            group_name="前沿信息收录",
            tab_id="tab-1",
            tab_label="外资研报",
            topic_id="topic-unchanged",
            title=meta["title"],
            author_name=meta["author_name"],
            content=meta["content"],
            create_time=meta["create_time"],
            raw_json_path="data/raw/topic-unchanged.json",
            raw_payload=payload,
            last_sync_run_id="run-1",
        ))
        db.session.commit()

        snapshots = service._load_existing_topic_snapshots(
            "group-1",
            "tab-1",
            [payload],
        )
        with patch.object(service, "_write_topic_json") as write_json:
            with self._capture_sql() as statements:
                changed, _topic = service._update_existing_topic(
                    None,
                    payload,
                    {"id": "tab-1", "label": "外资研报"},
                    SimpleNamespace(id="run-2"),
                    previous=snapshots["topic-unchanged"],
                )
                db.session.flush()

        updates = [
            sql for sql in statements
            if sql.startswith("update zsxq_topics set")
        ]
        self.assertFalse(changed)
        write_json.assert_not_called()
        self.assertEqual(1, len(updates), updates)
        self.assertNotIn("raw_payload", updates[0])
        self.assertNotIn("content=", updates[0])
        self.assertIn("last_sync_run_id", updates[0])

    def test_changed_topic_payload_keeps_full_json_and_projection(self):
        original = {
            "topic_id": "topic-changed",
            "talk": {"text": "旧内容", "owner": {"name": "作者"}},
        }
        db.session.add(ZsxqTopic(
            id="topic-row-changed",
            group_id="group-1",
            group_name="前沿信息收录",
            tab_id="tab-1",
            tab_label="外资研报",
            topic_id="topic-changed",
            raw_json_path="data/raw/topic-changed.json",
            raw_payload=original,
            last_sync_run_id="run-1",
        ))
        db.session.commit()
        service = ZsxqSyncService()
        snapshots = service._load_existing_topic_snapshots(
            "group-1",
            "tab-1",
            [original],
        )
        changed_payload = {
            "topic_id": "topic-changed",
            "talk": {"text": "新内容", "owner": {"name": "作者"}},
        }

        with patch.object(
            service,
            "_write_topic_json",
            return_value="data/raw/topic-changed.json",
        ) as write_json:
            with self._capture_sql() as statements:
                changed, _topic = service._update_existing_topic(
                    None,
                    changed_payload,
                    {"id": "tab-1", "label": "外资研报"},
                    SimpleNamespace(id="run-2"),
                    previous=snapshots["topic-changed"],
                )
                db.session.flush()

        updates = [
            sql for sql in statements
            if sql.startswith("update zsxq_topics set")
        ]
        self.assertTrue(changed)
        write_json.assert_called_once()
        self.assertEqual(1, len(updates), updates)
        self.assertIn("raw_payload", updates[0])
        self.assertIn("raw_payload_hash", updates[0])
        self.assertIn("content=", updates[0])

    def test_legacy_topic_schema_omits_payload_hash_from_select_and_update(self):
        payload = {
            "topic_id": "topic-legacy-schema",
            "talk": {"text": "兼容旧表", "owner": {"name": "作者"}},
        }
        db.session.add(ZsxqTopic(
            id="topic-row-legacy-schema",
            group_id="group-1",
            group_name="前沿信息收录",
            tab_id="tab-1",
            tab_label="外资研报",
            topic_id="topic-legacy-schema",
            raw_json_path="data/raw/topic-legacy-schema.json",
            raw_payload=payload,
            last_sync_run_id="run-1",
        ))
        db.session.commit()
        service = ZsxqSyncService()

        with self._capture_sql() as statements:
            snapshots = service._load_existing_topic_snapshots(
                "group-1",
                "tab-1",
                [payload],
                payload_hash_column_available=False,
            )
        topic_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_topics " in sql
        ]
        self.assertEqual(1, len(topic_selects), topic_selects)
        self.assertNotIn("raw_payload_hash", topic_selects[0])

        with patch.object(
            service,
            "_write_topic_json",
            return_value="data/raw/topic-legacy-schema.json",
        ):
            with self._capture_sql() as statements:
                changed, _topic = service._update_existing_topic(
                    None,
                    payload,
                    {"id": "tab-1", "label": "外资研报"},
                    SimpleNamespace(id="run-2"),
                    previous=snapshots["topic-legacy-schema"],
                    payload_hash_column_available=False,
                )
                db.session.flush()

        updates = [sql for sql in statements if sql.startswith("update zsxq_topics set")]
        self.assertTrue(changed)
        self.assertEqual(1, len(updates), updates)
        self.assertNotIn("raw_payload_hash", updates[0])
        self.assertIn("raw_payload", updates[0])

    def test_progress_heartbeat_throttles_result_json_but_keeps_structured_columns_current(self):
        run = ZsxqSyncRun(
            id="run-progress-1",
            group_id="group-1",
            status="running",
            result={"topics_fetched": 1, "audit": "old"},
        )
        db.session.add(run)
        db.session.commit()
        run = (
            ZsxqSyncRun.query
            .options(load_only(ZsxqSyncRun.id))
            .filter(ZsxqSyncRun.id == "run-progress-1")
            .one()
        )
        run._zsxq_cached_run_id = "run-progress-1"
        run._zsxq_result_persisted_monotonic = time.monotonic()

        service = ZsxqSyncService()
        next_result = {"topics_fetched": 9, "audit": "new"}
        with patch.object(service, "_progress_result_persist_seconds", return_value=60):
            with self._capture_sql() as statements:
                service._update_run_progress_result(run, next_result)

        updates = [sql for sql in statements if sql.startswith("update zsxq_sync_runs set")]
        self.assertEqual(len(updates), 1)
        self.assertNotIn("result=", updates[0])
        persisted = db.session.query(
            ZsxqSyncRun.result,
            ZsxqSyncRun.topics_fetched,
        ).filter(ZsxqSyncRun.id == "run-progress-1").one()
        self.assertEqual(persisted.result["audit"], "old")
        self.assertEqual(persisted.topics_fetched, 9)

        with self._capture_sql() as statements:
            service._update_run_progress_result(run, next_result, force_result=True)
        updates = [sql for sql in statements if sql.startswith("update zsxq_sync_runs set")]
        self.assertEqual(len(updates), 1)
        self.assertIn("result=", updates[0])
        persisted_result = db.session.query(ZsxqSyncRun.result).filter(
            ZsxqSyncRun.id == "run-progress-1"
        ).scalar()
        self.assertEqual(persisted_result["audit"], "new")

    def test_public_sync_payload_reads_structured_columns_and_injects_known_result(self):
        db.session.add(
            ZsxqSyncRun(
                id="run-public-1",
                group_id="group-1",
                status="completed",
                result={"large": "stored"},
            )
        )
        db.session.commit()

        service = ZsxqSyncService()
        with self._capture_sql() as statements:
            payload = service._sync_run_public_payload(
                "run-public-1",
                result={"large": "already-in-memory"},
            )

        run_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_sync_runs " in sql
        ]
        self.assertEqual(len(run_selects), 1)
        self.assertNotIn("zsxq_sync_runs.result", run_selects[0])
        self.assertEqual(payload["result"], {"large": "already-in-memory"})

    def test_clean_submissions_share_one_run_result_without_lost_updates(self):
        pdf_path = Path(self.temp_dir.name) / "report.pdf"
        audio_path = Path(self.temp_dir.name) / "recording.mp3"
        pdf_path.write_bytes(b"%PDF-1.4\nfixture")
        audio_path.write_bytes(b"ID3fixture")
        db.session.add(
            ZsxqSyncRun(
                id="run-clean-context-1",
                group_id="group-1",
                group_name="前沿信息收录",
                status="completed",
                download_ledger_complete=True,
                result={
                    "attachments_downloaded": 2,
                    "audit": "must-survive-atomic-patches",
                    "downloaded_pdf_files": [{"path": str(pdf_path)}],
                    "downloaded_files": [{"path": str(audio_path)}],
                },
            )
        )
        pdf_attachment = ZsxqAttachment(
            id="attachment-pdf-1",
            group_id="group-1",
            tab_id="tab-1",
            topic_id="topic-pdf-1",
            attachment_key="attachment-pdf-1",
            attachment_type="file",
            name="report.pdf",
            local_path=str(pdf_path),
            file_ext="pdf",
            download_status="downloaded",
        )
        audio_attachment = ZsxqAttachment(
            id="attachment-audio-1",
            group_id="group-1",
            tab_id="tab-1",
            topic_id="topic-audio-1",
            attachment_key="attachment-audio-1",
            attachment_type="file",
            name="recording.mp3",
            local_path=str(audio_path),
            file_ext="mp3",
            download_status="downloaded",
        )
        db.session.add_all([
            pdf_attachment,
            audio_attachment,
            ZsxqSyncRunDownload(
                run_id="run-clean-context-1",
                attachment_id=pdf_attachment.id,
                group_id="group-1",
                tab_id="tab-1",
                topic_id="topic-pdf-1",
                name="report.pdf",
                local_path=str(pdf_path),
                file_ext="pdf",
            ),
            ZsxqSyncRunDownload(
                run_id="run-clean-context-1",
                attachment_id=audio_attachment.id,
                group_id="group-1",
                tab_id="tab-1",
                topic_id="topic-audio-1",
                name="recording.mp3",
                local_path=str(audio_path),
                file_ext="mp3",
            ),
        ])
        db.session.commit()

        service = ZsxqSyncService()
        run_context = {}
        with patch(
            "services.content_pipeline.pdf_clean_task_service.submit_pdf_clean_task",
            return_value={"id": "pdf-task-1", "celery_task_id": "pdf-celery-1"},
        ), patch(
            "services.content_pipeline.audio_clean_task_service.submit_audio_clean_task",
            return_value={"id": "audio-task-1", "celery_task_id": "audio-celery-1"},
        ), self._capture_sql() as statements:
            pdf_result = service.submit_pdf_clean_for_run(
                "run-clean-context-1",
                run_context=run_context,
            )
            audio_result = service.submit_audio_clean_for_run(
                "run-clean-context-1",
                run_context=run_context,
            )

        run_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_sync_runs " in sql
        ]
        self.assertEqual(len(run_selects), 1, statements)
        self.assertNotIn("zsxq_sync_runs.result", run_selects[0])
        ledger_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_sync_run_downloads " in sql
        ]
        self.assertEqual(len(ledger_selects), 2, statements)
        self.assertEqual(pdf_result["task_id"], "pdf-task-1")
        self.assertEqual(audio_result["task_id"], "audio-task-1")
        self.assertEqual(
            run_context["result"]["content_pipeline"]["pdf_clean"]["task_id"],
            "pdf-task-1",
        )
        self.assertEqual(
            run_context["result"]["content_pipeline"]["audio_clean"]["task_id"],
            "audio-task-1",
        )
        persisted = db.session.query(ZsxqSyncRun.result).filter(
            ZsxqSyncRun.id == "run-clean-context-1"
        ).scalar()
        self.assertEqual(persisted["audit"], "must-survive-atomic-patches")
        self.assertIn("downloaded_pdf_files", persisted)
        self.assertIn("downloaded_files", persisted)
        self.assertEqual(persisted["content_pipeline"], run_context["result"]["content_pipeline"])

    def test_complete_ledger_does_not_fallback_to_result_when_target_file_is_absent(self):
        missing_path = Path(self.temp_dir.name) / "already-removed.pdf"
        run = ZsxqSyncRun(
            id="run-complete-ledger-1",
            group_id="group-1",
            status="completed",
            download_ledger_complete=True,
            result={
                "attachments_downloaded": 1,
                "downloaded_files": [{"path": "/tmp/large-audit.mp3"}],
            },
        )
        attachment = ZsxqAttachment(
            id="attachment-missing-1",
            group_id="group-1",
            tab_id="tab-1",
            topic_id="topic-1",
            attachment_key="attachment-missing-1",
            attachment_type="file",
            name="already-removed.pdf",
            local_path=str(missing_path),
            file_ext="pdf",
            download_status="downloaded",
        )
        db.session.add_all([
            run,
            attachment,
            ZsxqSyncRunDownload(
                run_id=run.id,
                attachment_id=attachment.id,
                group_id="group-1",
                tab_id="tab-1",
                topic_id="topic-1",
                name=attachment.name,
                local_path=str(missing_path),
                file_ext="pdf",
            ),
        ])
        db.session.commit()
        run_id = "run-complete-ledger-1"

        service = ZsxqSyncService()
        with self._capture_sql() as statements:
            cleanable = service.downloaded_audio_files_for_run(run_id)

        self.assertEqual(cleanable["files"], [])
        run_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_sync_runs " in sql
        ]
        self.assertEqual(len(run_selects), 1, statements)
        self.assertNotIn("zsxq_sync_runs.result", run_selects[0])

    def test_legacy_run_without_ledger_keeps_result_compatibility(self):
        pdf_path = Path(self.temp_dir.name) / "legacy.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nfixture")
        db.session.add(ZsxqSyncRun(
            id="run-legacy-result-1",
            group_id="group-1",
            status="completed",
            result={
                "attachments_downloaded": 1,
                "downloaded_pdf_files": [{"path": str(pdf_path)}],
            },
        ))
        db.session.commit()

        service = ZsxqSyncService()
        with self._capture_sql() as statements:
            cleanable = service.downloaded_pdf_files_for_run("run-legacy-result-1")

        self.assertEqual(
            [Path(item["path"]).resolve() for item in cleanable["files"]],
            [pdf_path.resolve()],
        )
        run_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from zsxq_sync_runs " in sql
        ]
        self.assertEqual(len(run_selects), 2, statements)
        self.assertNotIn("zsxq_sync_runs.result", run_selects[0])
        self.assertIn("zsxq_sync_runs.result", run_selects[1])


if __name__ == "__main__":
    unittest.main()
