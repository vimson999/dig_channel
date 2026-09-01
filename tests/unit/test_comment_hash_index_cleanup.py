import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (
    PROJECT_ROOT
    / "scripts"
    / "maintenance"
    / "apply_comment_hash_index_cleanup.py"
)
HISTORICAL_MIGRATION_PATH = (
    PROJECT_ROOT
    / "src"
    / "backend"
    / "migrations"
    / "versions"
    / "20260724_comment_hash_index_cleanup.py"
)
SCHEMA_GUARD_PATH = PROJECT_ROOT / "scripts" / "dev" / "check-runtime-schema.py"


def load_module():
    spec = importlib.util.spec_from_file_location("comment_hash_index_cleanup", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingCursor:
    def __init__(self):
        self.statements = []

    def execute(self, statement, parameters=None):
        self.statements.append((str(statement).strip(), parameters))


class RecordingConnection:
    def __init__(self):
        self.cursor_instance = RecordingCursor()

    def cursor(self):
        cursor = self.cursor_instance

        class Context:
            def __enter__(self):
                return cursor

            def __exit__(self, exc_type, exc, traceback):
                return False

        return Context()


class CommentHashIndexCleanupTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.target = "ix_zsxq_topics_group_type_create_desc"

    def snapshot(self, *, target_exists=True):
        target = {
            "table": "zsxq_topics",
            "valid": True,
            "ready": True,
            "unique": False,
            "primary": False,
            "constraint_name": None,
            "definition": (
                "CREATE INDEX ix_zsxq_topics_group_type_create_desc "
                "ON public.zsxq_topics USING btree (group_id, type, create_time DESC)"
            ),
            "scan_count": 1,
            "last_idx_scan": "2026-07-09T00:00:00",
            "size_bytes": 3700000,
            "owner_member": True,
        }
        indexes = {self.target: target} if target_exists else {}
        return {
            "database": "show_ytb_data",
            "user": "cappa_rw",
            "in_recovery": False,
            "waiting_locks": 0,
            "table_rows": {"zsxq_topics": 18000},
            "indexes": indexes,
            "all_table_indexes": {
                "zsxq_topics": {
                    "ix_zsxq_topics_group_tab_create_time_desc": {
                        "valid": True,
                        "ready": True,
                    },
                    **indexes,
                }
            },
        }

    def evidence(self):
        return {
            "target_index": self.target,
            "decision": "drop",
            "real_sql": (
                "SELECT * FROM zsxq_topics WHERE group_id = :group_id "
                "AND type = :type ORDER BY create_time DESC LIMIT :limit"
            ),
            "pg_stat": {"idx_scan": 1, "stats_reset": "2026-08-08T00:00:00Z"},
            "explain": {
                "replacement_indexes": [
                    "ix_zsxq_topics_group_tab_create_time_desc"
                ],
                "with_target": {"execution_ms": 0.4, "buffers": 12},
                "without_target": {"execution_ms": 0.5, "buffers": 13},
            },
        }

    def test_protected_hot_index_is_not_in_apply_allowlist(self):
        protected = "ix_zsxq_topics_group_tab_type_create_desc"

        self.assertIn(protected, self.module.PROTECTED_INDEXES)
        self.assertNotIn(protected, self.module.APPLY_ALLOWLIST)
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertNotIn("ALTER TABLE public.zsxq_topic_comments", source)
        self.assertNotIn("UPDATE public.alembic_version", source)
        migration_source = HISTORICAL_MIGRATION_PATH.read_text(encoding="utf-8")
        redundant_block = migration_source.split("REDUNDANT_INDEXES = (", 1)[1].split(
            ")", 1
        )[0]
        self.assertNotIn(protected, redundant_block)
        self.assertNotIn(self.target, redundant_block)
        guard_source = SCHEMA_GUARD_PATH.read_text(encoding="utf-8")
        forbidden_block = guard_source.split("FORBIDDEN_DUPLICATE_INDEXES = {", 1)[
            1
        ].split("}", 1)[0]
        self.assertNotIn(protected, forbidden_block)
        self.assertNotIn(self.target, forbidden_block)

    def test_evidence_requires_real_sql_stats_and_with_without_explain(self):
        evidence = self.evidence()
        self.assertEqual(
            self.module.evaluate_evidence(self.target, evidence),
            [],
        )

        evidence["real_sql"] = ""
        evidence["explain"].pop("without_target")
        errors = self.module.evaluate_evidence(self.target, evidence)
        self.assertIn("real SQL is missing", errors)
        self.assertIn("without-target EXPLAIN evidence is missing", errors)

    def test_preconditions_reject_protected_or_constraint_owned_index(self):
        protected = "ix_zsxq_topics_group_tab_type_create_desc"
        self.assertIn(
            "index is protected",
            self.module.evaluate_preconditions(
                protected,
                self.snapshot(),
                self.evidence(),
            ),
        )

        snapshot = self.snapshot()
        snapshot["indexes"][self.target]["constraint_name"] = "uq_business_key"
        self.assertIn(
            "target owns constraint uq_business_key",
            self.module.evaluate_preconditions(
                self.target,
                snapshot,
                self.evidence(),
            ),
        )

    def test_dry_run_never_executes_ddl(self):
        connection = RecordingConnection()
        before = self.snapshot()
        with patch.object(self.module, "capture_snapshot", return_value=before):
            result = self.module.run_cleanup(
                connection,
                target=self.target,
                evidence=self.evidence(),
                apply=False,
            )

        statements = [item[0] for item in connection.cursor_instance.statements]
        self.assertEqual("ready", result["status"])
        self.assertFalse(any("DROP INDEX" in item for item in statements))
        self.assertFalse(any("CREATE INDEX" in item for item in statements))

    def test_apply_drops_only_exact_allowlisted_target_and_preserves_rows(self):
        connection = RecordingConnection()
        before = self.snapshot()
        after = self.snapshot(target_exists=False)
        with patch.object(
            self.module,
            "capture_snapshot",
            side_effect=[before, after],
        ) as capture:
            result = self.module.run_cleanup(
                connection,
                target=self.target,
                evidence=self.evidence(),
                apply=True,
            )

        self.assertEqual(
            ("zsxq_topics",),
            capture.call_args_list[1].kwargs.get("table_names"),
        )

        ddl = [
            item[0]
            for item in connection.cursor_instance.statements
            if "DROP INDEX" in item[0] or "CREATE INDEX" in item[0]
        ]
        self.assertEqual(
            [
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "public.ix_zsxq_topics_group_type_create_desc"
            ],
            ddl,
        )
        self.assertEqual("applied", result["status"])
        self.assertIn("CREATE INDEX CONCURRENTLY", result["rollback_sql"])

    def test_apply_verifies_recreated_index_after_postcondition_failure(self):
        connection = RecordingConnection()
        before = self.snapshot()
        broken = self.snapshot(target_exists=False)
        broken["table_rows"]["zsxq_topics"] += 1
        restored = self.snapshot()
        with patch.object(
            self.module,
            "capture_snapshot",
            side_effect=[before, broken, restored],
        ) as capture:
            with self.assertRaisesRegex(RuntimeError, "table row counts changed"):
                self.module.run_cleanup(
                    connection,
                    target=self.target,
                    evidence=self.evidence(),
                    apply=True,
                )

        statements = [item[0] for item in connection.cursor_instance.statements]
        self.assertTrue(
            any(item.startswith("CREATE INDEX CONCURRENTLY") for item in statements)
        )
        self.assertEqual(3, capture.call_count)
        self.assertEqual(
            ("zsxq_topics",),
            capture.call_args_list[2].kwargs.get("table_names"),
        )


if __name__ == "__main__":
    unittest.main()
