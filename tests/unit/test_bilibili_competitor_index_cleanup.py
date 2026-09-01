import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (
    PROJECT_ROOT
    / "scripts"
    / "maintenance"
    / "apply_bilibili_competitor_bvid_index_cleanup.py"
)


def load_cleanup_module():
    spec = importlib.util.spec_from_file_location(
        "apply_bilibili_competitor_bvid_index_cleanup",
        SCRIPT_PATH,
    )
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

        class CursorContext:
            def __enter__(self):
                return cursor

            def __exit__(self, exc_type, exc, traceback):
                return False

        return CursorContext()


class BilibiliCompetitorIndexCleanupTest(unittest.TestCase):
    def test_fresh_install_migration_does_not_recreate_standalone_unique_indexes(self):
        migration = (
            PROJECT_ROOT
            / "src"
            / "backend"
            / "migrations"
            / "versions"
            / "20260718_bilibili_intelligence.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "ix_bilibili_competitor_videos_bvid",
            migration,
        )
        self.assertNotIn(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_bilibili_competitors_uid",
            migration,
        )

    def setUp(self):
        self.module = load_cleanup_module()

    def valid_snapshot(self):
        return {
            "database": "show_ytb_data",
            "user": "cappa_rw",
            "in_recovery": False,
            "row_count": 1015,
            "duplicate_groups": 0,
            "waiting_locks": 0,
            "samples": ["BVONE", "BVTWO"],
            "indexes": {
                self.module.KEEP_INDEX: {
                    "unique": True,
                    "valid": True,
                    "ready": True,
                    "definition": (
                        "CREATE UNIQUE INDEX bilibili_competitor_videos_bvid_key "
                        "ON public.bilibili_competitor_videos USING btree (bvid)"
                    ),
                    "constraint_name": self.module.KEEP_INDEX,
                    "constraint_type": "u",
                    "table_owner": "cappa_rw",
                    "owner_member": True,
                    "scan_count": 0,
                    "size": "64 kB",
                },
                self.module.DROP_INDEX: {
                    "unique": True,
                    "valid": True,
                    "ready": True,
                    "definition": (
                        "CREATE UNIQUE INDEX ix_bilibili_competitor_videos_bvid "
                        "ON public.bilibili_competitor_videos USING btree (bvid)"
                    ),
                    "constraint_name": None,
                    "constraint_type": None,
                    "table_owner": "cappa_rw",
                    "owner_member": True,
                    "scan_count": 48782,
                    "size": "64 kB",
                },
            },
        }

    def valid_uid_snapshot(self):
        return {
            "database": "show_ytb_data",
            "user": "cappa_rw",
            "in_recovery": False,
            "row_count": 6,
            "duplicate_groups": 0,
            "waiting_locks": 0,
            "samples": ["100", "200"],
            "indexes": {
                self.module.UID_KEEP_INDEX: {
                    "unique": True,
                    "valid": True,
                    "ready": True,
                    "definition": (
                        "CREATE UNIQUE INDEX bilibili_competitors_uid_key "
                        "ON public.bilibili_competitors USING btree (uid)"
                    ),
                    "constraint_name": self.module.UID_KEEP_INDEX,
                    "constraint_type": "u",
                    "table_owner": "cappa_rw",
                    "owner_member": True,
                    "scan_count": 6,
                    "size": "16 kB",
                },
                self.module.UID_DROP_INDEX: {
                    "unique": True,
                    "valid": True,
                    "ready": True,
                    "definition": (
                        "CREATE UNIQUE INDEX ix_bilibili_competitors_uid "
                        "ON public.bilibili_competitors USING btree (uid)"
                    ),
                    "constraint_name": None,
                    "constraint_type": None,
                    "table_owner": "cappa_rw",
                    "owner_member": True,
                    "scan_count": 6,
                    "size": "16 kB",
                },
            },
        }

    def test_uid_preconditions_require_constraint_owner_plus_standalone_duplicate(self):
        self.assertEqual(
            self.module.evaluate_uid_preconditions(self.valid_uid_snapshot()),
            [],
        )

    def test_uid_apply_drops_only_standalone_index_and_preserves_lookup(self):
        connection = RecordingConnection()
        before = self.valid_uid_snapshot()
        after = self.valid_uid_snapshot()
        after["indexes"] = {
            self.module.UID_KEEP_INDEX: after["indexes"][self.module.UID_KEEP_INDEX]
        }
        lookup = {
            "returned_uids": ["100", "200"],
            "index_names": [],
            "execution_ms": 0.03,
        }
        with (
            patch.object(
                self.module,
                "capture_uid_snapshot",
                side_effect=[before, after],
            ),
            patch.object(self.module, "verify_uid_lookup", return_value=lookup),
        ):
            result = self.module.run_uid_cleanup(connection, apply=True)

        statements = [statement for statement, _ in connection.cursor_instance.statements]
        ddl = [statement for statement in statements if "DROP INDEX" in statement]
        self.assertEqual(
            ddl,
            [
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "public.ix_bilibili_competitors_uid"
            ],
        )
        self.assertEqual("applied", result["status"])
        self.assertEqual(["100", "200"], result["lookup"]["returned_uids"])

    def test_uid_apply_recreates_standalone_index_if_retained_index_disappears(self):
        connection = RecordingConnection()
        before = self.valid_uid_snapshot()
        broken = self.valid_uid_snapshot()
        broken["indexes"] = {}
        restored = self.valid_uid_snapshot()
        with patch.object(
            self.module,
            "capture_uid_snapshot",
            side_effect=[before, broken, restored],
        ):
            with self.assertRaisesRegex(RuntimeError, "retained uid index is missing"):
                self.module.run_uid_cleanup(connection, apply=True)

        statements = [statement for statement, _ in connection.cursor_instance.statements]
        self.assertTrue(
            any(
                statement.startswith(
                    "CREATE UNIQUE INDEX CONCURRENTLY "
                    "ix_bilibili_competitors_uid"
                )
                for statement in statements
            )
        )

    def test_preconditions_accept_only_constraint_owner_plus_standalone_duplicate(self):
        self.assertEqual(
            self.module.evaluate_preconditions(self.valid_snapshot()),
            [],
        )

    def test_preconditions_reject_duplicate_data_and_constraint_owned_drop_target(self):
        snapshot = self.valid_snapshot()
        snapshot["duplicate_groups"] = 2
        snapshot["indexes"][self.module.DROP_INDEX]["constraint_name"] = "wrong_owner"
        snapshot["indexes"][self.module.DROP_INDEX]["constraint_type"] = "u"

        errors = self.module.evaluate_preconditions(snapshot)

        self.assertIn("duplicate bvid groups=2", errors)
        self.assertIn("drop target owns constraint wrong_owner", errors)

    def test_check_mode_never_executes_ddl(self):
        connection = RecordingConnection()
        snapshot = self.valid_snapshot()
        with patch.object(self.module, "capture_snapshot", return_value=snapshot):
            result = self.module.run_cleanup(connection, apply=False)

        statements = [statement for statement, _ in connection.cursor_instance.statements]
        self.assertEqual(result["status"], "ready")
        self.assertFalse(any("DROP INDEX" in statement for statement in statements))
        self.assertFalse(any("CREATE INDEX" in statement for statement in statements))

    def test_apply_drops_only_the_standalone_index_after_rechecking(self):
        connection = RecordingConnection()
        before = self.valid_snapshot()
        after = self.valid_snapshot()
        after["indexes"] = {
            self.module.KEEP_INDEX: after["indexes"][self.module.KEEP_INDEX]
        }
        lookup = {
            "returned_bvids": ["BVONE", "BVTWO"],
            "index_names": [self.module.KEEP_INDEX],
            "execution_ms": 0.04,
        }
        with (
            patch.object(
                self.module,
                "capture_snapshot",
                side_effect=[before, after],
            ),
            patch.object(self.module, "verify_lookup", return_value=lookup),
        ):
            result = self.module.run_cleanup(connection, apply=True)

        statements = [statement for statement, _ in connection.cursor_instance.statements]
        ddl = [
            statement
            for statement in statements
            if "DROP INDEX" in statement or "CREATE INDEX" in statement
        ]
        self.assertEqual(
            ddl,
            [
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "public.ix_bilibili_competitor_videos_bvid"
            ],
        )
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["lookup"]["index_names"], [self.module.KEEP_INDEX])

    def test_apply_recreates_the_standalone_index_if_retained_index_disappears(self):
        connection = RecordingConnection()
        before = self.valid_snapshot()
        broken = self.valid_snapshot()
        broken["indexes"] = {}
        restored = self.valid_snapshot()
        with patch.object(
            self.module,
            "capture_snapshot",
            side_effect=[before, broken, restored],
        ):
            with self.assertRaisesRegex(RuntimeError, "retained index is missing"):
                self.module.run_cleanup(connection, apply=True)

        statements = [statement for statement, _ in connection.cursor_instance.statements]
        self.assertTrue(
            any(
                statement.startswith(
                    "CREATE UNIQUE INDEX CONCURRENTLY "
                    "ix_bilibili_competitor_videos_bvid"
                )
                for statement in statements
            )
        )

    def test_apply_recreates_standalone_index_if_retained_index_is_invalid(self):
        connection = RecordingConnection()
        before = self.valid_snapshot()
        broken = self.valid_snapshot()
        retained = broken["indexes"][self.module.KEEP_INDEX]
        retained["valid"] = False
        broken["indexes"] = {self.module.KEEP_INDEX: retained}
        restored = self.valid_snapshot()
        with patch.object(
            self.module,
            "capture_snapshot",
            side_effect=[before, broken, restored],
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "retained unique constraint index is invalid",
            ):
                self.module.run_cleanup(connection, apply=True)

        statements = [statement for statement, _ in connection.cursor_instance.statements]
        self.assertTrue(
            any(
                statement.startswith(
                    "CREATE UNIQUE INDEX CONCURRENTLY "
                    "ix_bilibili_competitor_videos_bvid"
                )
                for statement in statements
            )
        )

    def test_apply_recreates_standalone_index_if_lookup_rejects_retained_index(self):
        connection = RecordingConnection()
        before = self.valid_snapshot()
        after = self.valid_snapshot()
        after["indexes"] = {
            self.module.KEEP_INDEX: after["indexes"][self.module.KEEP_INDEX]
        }
        restored = self.valid_snapshot()
        lookup = {
            "returned_bvids": ["BVONE", "BVTWO"],
            "index_names": [],
            "execution_ms": 0.04,
        }
        with (
            patch.object(
                self.module,
                "capture_snapshot",
                side_effect=[before, after, restored],
            ),
            patch.object(self.module, "verify_lookup", return_value=lookup),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "post-DDL plan did not use retained index",
            ):
                self.module.run_cleanup(connection, apply=True)

        statements = [statement for statement, _ in connection.cursor_instance.statements]
        self.assertTrue(
            any(
                statement.startswith(
                    "CREATE UNIQUE INDEX CONCURRENTLY "
                    "ix_bilibili_competitor_videos_bvid"
                )
                for statement in statements
            )
        )


if __name__ == "__main__":
    unittest.main()
