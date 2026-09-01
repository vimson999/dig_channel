import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from sqlalchemy import event


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.content_pipeline_asset import ContentPipelineAsset  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from models.zsxq_sync import ZsxqAttachment  # noqa: E402
from services import mp3_video_workflow as mp3_workflow_service  # noqa: E402
from services.mp3_video_workflow import (  # noqa: E402
    audio_meta_from_asset_reference,
    audio_meta_from_zsxq_attachment,
    clear_audio_asset_catalog_cache,
    list_audio_assets,
)


class Mp3VideoAudioAssetMergeTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.output_root = Path(self.temp_dir.name) / "content-pipeline-output"
        self.env_patch = patch.dict(
            os.environ,
            {"CONTENT_PIPELINE_OUTPUT_ROOT": str(self.output_root)},
            clear=False,
        )
        self.env_patch.start()
        clear_audio_asset_catalog_cache()

    def tearDown(self):
        clear_audio_asset_catalog_cache()
        self.env_patch.stop()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.temp_dir.cleanup()

    def test_audio_asset_list_uses_cleaned_output_after_raw_source_is_deleted(self):
        raw_path = Path(self.temp_dir.name) / "手动投递.mp3"
        raw_path.write_bytes(b"manual-audio")
        cleaned_path = self.output_root / "batch-1" / "audio_cleaned" / raw_path.name
        cleaned_path.parent.mkdir(parents=True)
        cleaned_path.write_bytes(b"cleaned-audio")
        task = PublicationTask(
            status="pending",
            current_step="init",
            progress=0,
            params={"feature": "content_audio_clean"},
        )
        db.session.add(task)
        db.session.flush()
        db.session.add(
            ContentPipelineAsset(
                media_type="audio",
                content_sha256="a" * 64,
                canonical_task_id=str(task.id),
                source_path=str(raw_path.resolve()),
                filename=raw_path.name,
                source_mode="manual_download",
                status="cleaned",
                cleaned_path=str(cleaned_path),
                cleaned_sha256="b" * 64,
                cleaned_bytes=cleaned_path.stat().st_size,
            )
        )
        db.session.commit()
        raw_path.unlink()

        payload = list_audio_assets(page=1, per_page=30, scope="all")

        manual = [item for item in payload["items"] if item.get("asset_kind") == "content_pipeline"]
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]["filename"], raw_path.name)
        self.assertEqual(manual[0]["source_label"], "手动投递")
        self.assertTrue(manual[0]["attachment_id"].startswith("pipeline:"))
        self.assertEqual(manual[0]["path"], str(cleaned_path.resolve()))
        resolved = audio_meta_from_asset_reference(manual[0]["attachment_id"])
        self.assertEqual(resolved["path"], str(cleaned_path.resolve()))

    def test_synced_attachment_uses_cleaned_output_after_raw_source_is_deleted(self):
        raw_path = Path(self.temp_dir.name) / "同步音频.mp3"
        raw_path.write_bytes(b"raw-synced-audio")
        cleaned_path = self.output_root / "batch-2" / "audio_cleaned" / raw_path.name
        cleaned_path.parent.mkdir(parents=True)
        cleaned_path.write_bytes(b"cleaned-synced-audio")
        task = PublicationTask(
            status="completed",
            current_step="completed",
            progress=100,
            params={"feature": "content_audio_clean"},
        )
        attachment = ZsxqAttachment(
            group_id="group-1",
            tab_id="tab-1",
            topic_id="topic-1",
            attachment_key="audio-1",
            attachment_type="file",
            name=raw_path.name,
            file_id="file-1",
            download_count=25,
            local_path=str(raw_path),
            download_status="downloaded",
        )
        db.session.add_all([task, attachment])
        db.session.flush()
        attachment_id = str(attachment.id)
        db.session.add(
            ContentPipelineAsset(
                media_type="audio",
                content_sha256="c" * 64,
                canonical_task_id=str(task.id),
                source_path=str(raw_path.resolve()),
                filename=raw_path.name,
                source_mode="sidebar",
                status="cleaned",
                cleaned_path=str(cleaned_path),
                cleaned_sha256="d" * 64,
                cleaned_bytes=cleaned_path.stat().st_size,
            )
        )
        db.session.commit()
        raw_path.unlink()

        payload = list_audio_assets(page=1, per_page=30, scope="all")

        synced = [item for item in payload["items"] if item.get("asset_kind") == "zsxq_attachment"]
        self.assertEqual(len(synced), 1)
        self.assertEqual(synced[0]["attachment_id"], attachment_id)
        self.assertEqual(synced[0]["source_label"], "知识星球·侧边栏下载")
        self.assertEqual(synced[0]["path"], str(cleaned_path.resolve()))
        resolved = audio_meta_from_zsxq_attachment(attachment_id)
        self.assertEqual(resolved["path"], str(cleaned_path.resolve()))

    def test_audio_asset_list_batches_missing_canonical_asset_lookups(self):
        """A historical page must not query canonical assets once per attachment."""
        for index in range(12):
            raw_path = Path(self.temp_dir.name) / f"历史附件-{index}.mp3"
            raw_path.write_bytes(f"raw-{index}".encode("utf-8"))
            db.session.add(
                ZsxqAttachment(
                    id=f"attachment-{index}",
                    group_id="group-1",
                    tab_id="tab-1",
                    topic_id=f"topic-{index}",
                    attachment_key=f"key-{index}",
                    attachment_type="file",
                    name=raw_path.name,
                    file_id=f"file-{index}",
                    download_count=1,
                    local_path=str(raw_path),
                    file_ext="mp3",
                    download_status="downloaded",
                )
            )

        cleaned_path = self.output_root / "batch-3" / "audio_cleaned" / "可用清洗产物.mp3"
        cleaned_path.parent.mkdir(parents=True)
        cleaned_path.write_bytes(b"cleaned-audio")
        task = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": "content_audio_clean"},
        )
        db.session.add(task)
        db.session.flush()
        asset = ContentPipelineAsset(
            media_type="audio",
            content_sha256="e" * 64,
            canonical_task_id=str(task.id),
            source_path=str(Path(self.temp_dir.name) / "manual-source.mp3"),
            filename="可用清洗产物.mp3",
            source_mode="manual_download",
            status="cleaned",
            cleaned_path=str(cleaned_path),
            cleaned_sha256="f" * 64,
            cleaned_bytes=cleaned_path.stat().st_size,
        )
        db.session.add(asset)
        db.session.commit()

        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            payload = list_audio_assets(page=1, per_page=10, scope="all")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        canonical_selects = [
            statement
            for statement in statements
            if statement.startswith("select") and " from content_pipeline_assets " in statement
        ]
        self.assertEqual([f"pipeline:{asset.id}"], [item["attachment_id"] for item in payload["items"]])
        self.assertEqual(1, len(canonical_selects), canonical_selects)

    def test_audio_asset_list_does_not_scan_stale_attachment_pages_before_canonical_assets(self):
        """Only canonical cleaned assets may drive the candidate scan budget."""
        for index in range(120):
            db.session.add(
                ZsxqAttachment(
                    id=f"stale-attachment-{index}",
                    group_id="group-1",
                    tab_id="tab-1",
                    topic_id=f"stale-topic-{index}",
                    attachment_key=f"stale-key-{index}",
                    attachment_type="file",
                    name=f"已删除原始音频-{index}.mp3",
                    file_id=f"stale-file-{index}",
                    download_count=1,
                    local_path=str(Path(self.temp_dir.name) / f"missing-{index}.mp3"),
                    file_ext="mp3",
                    download_status="downloaded",
                )
            )

        cleaned_path = self.output_root / "batch-4" / "audio_cleaned" / "唯一可用清洗产物.mp3"
        cleaned_path.parent.mkdir(parents=True)
        cleaned_path.write_bytes(b"canonical-audio")
        task = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": "content_audio_clean"},
        )
        db.session.add(task)
        db.session.flush()
        asset = ContentPipelineAsset(
            media_type="audio",
            content_sha256="1" * 64,
            canonical_task_id=str(task.id),
            source_path=str(Path(self.temp_dir.name) / "canonical-source.mp3"),
            filename=cleaned_path.name,
            source_mode="manual_download",
            status="cleaned",
            cleaned_path=str(cleaned_path),
            cleaned_sha256="2" * 64,
            cleaned_bytes=cleaned_path.stat().st_size,
        )
        db.session.add(asset)
        db.session.commit()

        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            payload = list_audio_assets(page=1, per_page=10, scope="all")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        attachment_selects = [
            statement
            for statement in statements
            if statement.startswith("select") and " from zsxq_attachments " in statement
        ]
        self.assertEqual([f"pipeline:{asset.id}"], [item["attachment_id"] for item in payload["items"]])
        self.assertEqual(1, payload["pagination"]["scanned"])
        self.assertLessEqual(len(attachment_selects), 1, attachment_selects)

    def test_audio_asset_searches_reuse_one_catalog_snapshot(self):
        """A burst of search terms must filter one bounded database snapshot."""
        cleaned_path = self.output_root / "batch-5" / "audio_cleaned" / "天风证券闭门会.mp3"
        cleaned_path.parent.mkdir(parents=True)
        cleaned_path.write_bytes(b"canonical-audio")
        task = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": "content_audio_clean"},
        )
        db.session.add(task)
        db.session.flush()
        asset = ContentPipelineAsset(
            media_type="audio",
            content_sha256="3" * 64,
            canonical_task_id=str(task.id),
            source_path=str(Path(self.temp_dir.name) / "research-source.mp3"),
            filename=cleaned_path.name,
            source_mode="manual_download",
            status="cleaned",
            cleaned_path=str(cleaned_path),
            cleaned_sha256="4" * 64,
            cleaned_bytes=cleaned_path.stat().st_size,
        )
        db.session.add(asset)
        db.session.commit()

        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            first = list_audio_assets(page=1, per_page=10, scope="all", q="天")
            latest = list_audio_assets(page=1, per_page=10, scope="all", q="天风")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        canonical_selects = [
            statement
            for statement in statements
            if statement.startswith("select") and " from content_pipeline_assets " in statement
        ]
        attachment_selects = [
            statement
            for statement in statements
            if statement.startswith("select") and " from zsxq_attachments " in statement
        ]
        expected_ids = [f"pipeline:{asset.id}"]
        self.assertEqual(expected_ids, [item["attachment_id"] for item in first["items"]])
        self.assertEqual(expected_ids, [item["attachment_id"] for item in latest["items"]])
        self.assertEqual(1, len(canonical_selects), canonical_selects)
        self.assertEqual(1, len(attachment_selects), attachment_selects)

    def test_audio_asset_catalog_releases_database_before_file_validation(self):
        """Filesystem validation must never run with a checked-out DB transaction."""
        cleaned_path = self.output_root / "batch-6" / "audio_cleaned" / "事务边界.mp3"
        cleaned_path.parent.mkdir(parents=True)
        cleaned_path.write_bytes(b"canonical-audio")
        task = PublicationTask(
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": "content_audio_clean"},
        )
        db.session.add(task)
        db.session.flush()
        db.session.add(
            ContentPipelineAsset(
                media_type="audio",
                content_sha256="5" * 64,
                canonical_task_id=str(task.id),
                source_path=str(Path(self.temp_dir.name) / "boundary-source.mp3"),
                filename=cleaned_path.name,
                source_mode="manual_download",
                status="cleaned",
                cleaned_path=str(cleaned_path),
                cleaned_sha256="6" * 64,
                cleaned_bytes=cleaned_path.stat().st_size,
            )
        )
        db.session.commit()

        original_resolver = mp3_workflow_service.resolve_cleaned_asset
        transaction_states = []

        def resolve_without_session_hold(*args, **kwargs):
            transaction_states.append(db.session().in_transaction())
            return original_resolver(*args, **kwargs)

        with patch.object(
            mp3_workflow_service,
            "resolve_cleaned_asset",
            side_effect=resolve_without_session_hold,
        ):
            payload = list_audio_assets(page=1, per_page=10, scope="all")

        self.assertEqual(1, len(payload["items"]))
        self.assertTrue(transaction_states)
        self.assertFalse(any(transaction_states), transaction_states)


if __name__ == "__main__":
    unittest.main()
