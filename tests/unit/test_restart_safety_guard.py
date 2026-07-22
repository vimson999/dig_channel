import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = PROJECT_ROOT / "scripts" / "dev" / "check-restart-safety.py"
SCHEMA_GUARD_PATH = PROJECT_ROOT / "scripts" / "dev" / "check-runtime-schema.py"
RESTART_PATH = PROJECT_ROOT / "scripts" / "dev" / "restart-dev-stack.sh"
QUIESCE_PATH = PROJECT_ROOT / "scripts" / "dev" / "quiesce-celery-consumers.py"
STOP_BACKEND_PATH = PROJECT_ROOT / "scripts" / "dev" / "stop-backend.sh"
MIGRATION_PATH = (
    PROJECT_ROOT
    / "docs"
    / "sql"
    / "content_pipeline_run_download_projection_dbeaver_20260718.sql"
)


def load_guard_module():
    spec = importlib.util.spec_from_file_location("restart_safety_guard", GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_schema_guard_module():
    spec = importlib.util.spec_from_file_location("runtime_schema_guard", SCHEMA_GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RestartSafetyGuardTest(unittest.TestCase):
    def test_real_work_steps_block_but_wait_states_do_not(self):
        guard = load_guard_module()

        for step in ("synthesis", "v_synthesis", "upload_bilibili", "gemini_processing", ""):
            self.assertTrue(guard.is_non_interruptible_step(step), step)
        for step in (
            "queued",
            "deferred_db_pressure",
            "landscape_video_waiting",
            "storyboard_queued",
            "waiting_child",
        ):
            self.assertFalse(guard.is_non_interruptible_step(step), step)

    def test_full_restart_runs_guard_before_stopping_backend(self):
        text = RESTART_PATH.read_text(encoding="utf-8")

        guard_call = '"$PROJECT_ROOT/venv/bin/python" "$SCRIPT_DIR/check-restart-safety.py"'
        schema_guard_call = '"$PROJECT_ROOT/venv/bin/python" "$SCRIPT_DIR/check-runtime-schema.py"'
        self.assertIn(guard_call, text)
        self.assertIn(schema_guard_call, text)
        self.assertIn("FORCE_RESTART_ACTIVE_TASKS", text)
        self.assertLess(text.index(schema_guard_call), text.index(guard_call))
        self.assertLess(text.index(guard_call), text.index('"$SCRIPT_DIR/stop-backend.sh"'))

    def test_full_restart_quiesces_consumers_and_rechecks_before_stopping_workers(self):
        text = RESTART_PATH.read_text(encoding="utf-8")

        watchdog_stop = '"$SCRIPT_DIR/stop-celery-watchdog.sh"'
        quiesce_call = '"$PROJECT_ROOT/venv/bin/python" "$SCRIPT_DIR/quiesce-celery-consumers.py"'
        backend_stop = '"$SCRIPT_DIR/stop-backend.sh"'
        worker_stop = '"$SCRIPT_DIR/stop-all-background-jobs.sh"'
        first_guard = text.index('"$PROJECT_ROOT/venv/bin/python" "$SCRIPT_DIR/check-restart-safety.py"')
        second_guard = text.index(
            '"$PROJECT_ROOT/venv/bin/python" "$SCRIPT_DIR/check-restart-safety.py"',
            first_guard + 1,
        )

        self.assertLess(text.index(watchdog_stop), text.index(quiesce_call))
        self.assertLess(text.index(quiesce_call), text.index(backend_stop))
        self.assertLess(text.index(backend_stop), second_guard)
        self.assertLess(second_guard, text.index(worker_stop))
        self.assertIn("RESTART_DRAIN_TIMEOUT_SECONDS", text)
        self.assertIn("拒绝强杀 worker", text)

    def test_quiesce_broadcasts_cancel_consumer_without_deleting_messages(self):
        text = QUIESCE_PATH.read_text(encoding="utf-8")

        self.assertIn('"cancel_consumer"', text)
        self.assertIn('reply=False', text)
        self.assertNotIn("purge", text.lower())

    def test_restart_guard_uses_direct_database_when_local_proxy_is_stopped(self):
        guard = load_guard_module()

        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://proxy@127.0.0.1:6432/app",
                "DIRECT_DATABASE_URL": "postgresql://direct@db.example:5432/app",
            },
        ), patch.object(guard, "_local_database_url_is_available", return_value=False):
            guard._use_direct_database_url()

            self.assertEqual(
                os.environ["DATABASE_URL"],
                "postgresql://direct@db.example:5432/app",
            )

    def test_restart_guard_keeps_live_local_database_proxy(self):
        guard = load_guard_module()

        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://proxy@127.0.0.1:6432/app",
                "DIRECT_DATABASE_URL": "postgresql://direct@db.example:5432/app",
            },
        ), patch.object(guard, "_local_database_url_is_available", return_value=True):
            guard._use_direct_database_url()

            self.assertEqual(
                os.environ["DATABASE_URL"],
                "postgresql://proxy@127.0.0.1:6432/app",
            )

    def test_backend_stop_does_not_kill_independent_service_sessions(self):
        text = STOP_BACKEND_PATH.read_text(encoding="utf-8")

        self.assertIn("process_group_id", text)
        self.assertIn("same_process_group_descendant_pids", text)
        self.assertIn('ps -o pgid=', text)
        self.assertNotIn('ps -o sess=', text)
        self.assertIn("*[!0-9]*", text)
        self.assertIn('= "$root_group"', text)
        self.assertNotIn('descendants="$(descendant_pids "$pid")"', text)

    def test_runtime_schema_guard_rejects_partial_migration(self):
        guard = load_schema_guard_module()
        snapshot = {
            "tables": {
                "publication_tasks": guard.REQUIRED_COLUMNS["publication_tasks"],
                "zsxq_sync_runs": {"id"},
            },
            "privileges": {},
            "clean_tasks": 386,
            "projected_clean_tasks": 0,
            "indexes": {
                "ix_publication_tasks_parent_created",
                "ix_publication_tasks_parent_created_asc",
                "ix_publication_tasks_source_created",
                "ix_publication_tasks_source_created_asc",
                "ix_zsxq_topics_group_tab_create_time_desc",
                "ix_zsxq_topics_group_tab_create_synced_desc",
            },
        }

        errors = guard.evaluate_schema_snapshot(snapshot)

        self.assertTrue(any("zsxq_sync_run_downloads" in item for item in errors))
        self.assertTrue(any("download_ledger_complete" in item for item in errors))
        self.assertTrue(any("backfill is incomplete" in item for item in errors))
        self.assertTrue(any("duplicate indexes" in item for item in errors))

    def test_runtime_schema_guard_accepts_complete_contract(self):
        guard = load_schema_guard_module()
        snapshot = {
            "tables": {
                name: set(columns)
                for name, columns in guard.REQUIRED_COLUMNS.items()
            },
            "privileges": {
                name: True for name in guard.REQUIRED_PRIVILEGES
            },
            "clean_tasks": 386,
            "projected_clean_tasks": 386,
            "media_projection_mismatches": 0,
            "bilibili_tasks": 12,
            "projected_bilibili_tasks": 12,
            "executable_bilibili_tasks": 12,
            "indexes": set(guard.REQUIRED_INDEXES),
        }

        self.assertEqual(guard.evaluate_schema_snapshot(snapshot), [])

    def test_runtime_schema_guard_has_a_hard_timeout(self):
        text = SCHEMA_GUARD_PATH.read_text(encoding="utf-8")

        self.assertIn("RUNTIME_SCHEMA_CHECK_TIMEOUT_SECONDS", text)
        self.assertIn("signal.alarm(timeout_seconds)", text)
        self.assertIn("signal.alarm(0)", text)

    def test_runtime_schema_guard_prefers_live_local_proxy(self):
        guard = load_schema_guard_module()

        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://proxy@127.0.0.1:6432/app",
                "DIRECT_DATABASE_URL": "postgresql://direct@db.example:5432/app",
            },
        ), patch.object(guard, "_local_database_url_is_available", return_value=True):
            self.assertEqual(
                guard._database_url(),
                "postgresql://proxy@127.0.0.1:6432/app",
            )

    def test_runtime_schema_guard_falls_back_to_direct_database(self):
        guard = load_schema_guard_module()

        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://proxy@127.0.0.1:6432/app",
                "DIRECT_DATABASE_URL": "postgresql://direct@db.example:5432/app",
            },
        ), patch.object(guard, "_local_database_url_is_available", return_value=False):
            self.assertEqual(
                guard._database_url(),
                "postgresql://direct@db.example:5432/app",
            )

    def test_structural_migration_refuses_live_application_connections(self):
        text = MIGRATION_PATH.read_text(encoding="utf-8")

        self.assertIn("BEGIN;", text)
        self.assertIn("COMMIT;", text)
        self.assertIn("FROM pg_stat_activity", text)
        self.assertIn("usename = 'cappa_rw'", text)
        self.assertIn("IF app_connections > 0 THEN", text)
        self.assertIn("Refusing online DDL", text)
        self.assertNotRegex(text, r"(?mi)^\s*CREATE INDEX CONCURRENTLY")
        self.assertIn(
            "ALTER TABLE public.publication_task_bilibili_uploads",
            text,
        )
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS queue_repair_count",
            text,
        )
        self.assertIn("LEFT JOIN public.platform_credential_accounts", text)
        self.assertIn("account.owner_label", text)


if __name__ == "__main__":
    unittest.main()
