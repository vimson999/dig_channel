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
from scripts.maintenance import backfill_content_pipeline_cleaned_assets as backfill  # noqa: E402
from services.content_pipeline.canonical_asset_service import (  # noqa: E402
    backfill_task_cleaned_outputs,
    resolve_cleaned_asset,
)


class ContentPipelineCleanedAssetBackfillTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_root = self.root / "runtime" / "content_pipeline" / "batches"
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.env_patch = patch.dict(
            os.environ,
            {"CONTENT_PIPELINE_OUTPUT_ROOT": str(self.output_root)},
            clear=False,
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.temp_dir.cleanup()

    def _legacy_pdf_task(self):
        source_root = self.root / "downloads"
        source_root.mkdir()
        raw = source_root / "report.pdf"
        raw.write_bytes(b"%PDF-raw")
        cleaned = self.output_root / "legacy-task" / "pdf_cleaned" / "report.pdf"
        cleaned.parent.mkdir(parents=True)
        cleaned.write_bytes(b"%PDF-cleaned")
        task = PublicationTask(
            feature="content_pdf_clean",
            status="completed",
            current_step="done",
            progress=100,
            params={
                "feature": "content_pdf_clean",
                "content_pipeline": {
                    "pdf_clean": {"source_root": str(source_root)},
                },
            },
            result={
                "cleaned_files": [
                    {
                        "source": "report.pdf",
                        "status": "SUCCESS",
                        "path": str(cleaned),
                    },
                ],
            },
        )
        db.session.add(task)
        db.session.flush()
        asset = ContentPipelineAsset(
            media_type="pdf",
            content_sha256="c" * 64,
            canonical_task_id=str(task.id),
            source_path=str(raw.resolve()),
            filename=raw.name,
            source_mode="sidebar",
            status="cleaned",
        )
        db.session.add(asset)
        db.session.commit()
        return task, asset, raw, cleaned

    def test_backfill_registers_legacy_relative_pdf_source_against_its_source_root(self):
        task, asset, raw, cleaned = self._legacy_pdf_task()

        report = backfill_task_cleaned_outputs(task)
        db.session.refresh(asset)
        raw.unlink()

        resolution = resolve_cleaned_asset(asset, media_type="pdf")
        self.assertEqual(report["registered_count"], 1)
        self.assertEqual(report["unresolved_count"], 0)
        self.assertEqual(asset.cleaned_path, str(cleaned.resolve()))
        self.assertEqual(resolution.state, "ready")
        self.assertEqual(resolution.path, cleaned.resolve())

    def test_backfill_dry_run_does_not_persist_cleaned_path(self):
        task, asset, _raw, cleaned = self._legacy_pdf_task()

        report = backfill_task_cleaned_outputs(task, dry_run=True)
        asset = db.session.get(ContentPipelineAsset, asset.id)

        self.assertEqual(report["registered_count"], 1)
        self.assertEqual(report["registered_paths"], [str(cleaned.resolve())])
        self.assertIsNone(asset.cleaned_path)

    def test_cli_loader_limits_historical_query_to_backfill_fields(self):
        """A large task table must not load unrelated JSON/projection columns first."""
        task = PublicationTask(
            feature="content_pdf_clean",
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": "content_pdf_clean", "content_pipeline": {}},
            result={"cleaned_files": []},
            error_message="unrelated detail payload",
            task_title="unrelated title",
        )
        db.session.add(task)
        db.session.commit()
        task_id = str(task.id)
        db.session.remove()

        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            loaded = backfill.load_backfill_task(
                PublicationTask,
                task_id,
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(task_id, str(loaded.id))
        select = next(
            statement
            for statement in statements
            if statement.startswith("select") and "from publication_tasks" in statement
        )
        self.assertIn("publication_tasks.params", select)
        self.assertIn("publication_tasks.result", select)
        self.assertNotIn("publication_tasks.error_message", select)
        self.assertNotIn("publication_tasks.publisher_vertical_cover_path", select)

    def test_cli_iterator_reads_payloads_one_task_at_a_time(self):
        """A 200-task backfill must not retrieve all params/result JSON in one query."""
        for index in range(2):
            db.session.add(PublicationTask(
                feature="content_pdf_clean",
                status="completed",
                current_step="done",
                progress=100,
                params={"feature": "content_pdf_clean", "sequence": index},
                result={"cleaned_files": []},
            ))
        db.session.commit()
        db.session.remove()

        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            tasks = list(backfill.iter_backfill_tasks(
                PublicationTask,
                task_ids=None,
                media_type=None,
                limit=2,
            ))
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(2, len(tasks))
        id_batch_selects = [
            statement
            for statement in statements
            if statement.startswith("select")
            and "from publication_tasks" in statement
            and "publication_tasks.params" not in statement
            and "publication_tasks.result" not in statement
        ]
        payload_point_selects = [
            statement
            for statement in statements
            if statement.startswith("select")
            and "from publication_tasks" in statement
            and "publication_tasks.params" in statement
            and "publication_tasks.result" in statement
            and "where publication_tasks.id" in statement
        ]
        self.assertEqual(1, len(id_batch_selects))
        self.assertEqual(2, len(payload_point_selects))


if __name__ == "__main__":
    unittest.main()
