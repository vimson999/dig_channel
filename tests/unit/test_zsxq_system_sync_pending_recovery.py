import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import zsxq_system_sync_test  # noqa: E402
from services.zsxq_sync_service import STALE_SYNC_RUN_STATUSES  # noqa: E402


class ZsxqSystemSyncPendingRecoveryTest(unittest.TestCase):
    def test_stale_recovery_includes_orphaned_queued_runs(self):
        self.assertIn("queued", STALE_SYNC_RUN_STATUSES)

    def test_stale_sync_runs_are_recovered_before_pending_check(self):
        with patch.object(
            zsxq_system_sync_test.zsxq_sync_service,
            "_mark_stale_runs_failed",
        ) as recover:
            zsxq_system_sync_test._recover_stale_sync_runs("group-1")

        recover.assert_called_once_with(group_id="group-1")

    def test_recovery_failure_resets_database_session(self):
        with patch.object(
            zsxq_system_sync_test.zsxq_sync_service,
            "_mark_stale_runs_failed",
            side_effect=RuntimeError("db unavailable"),
        ):
            with patch.object(zsxq_system_sync_test, "_reset_db_session") as reset:
                zsxq_system_sync_test._recover_stale_sync_runs("group-1")

        reset.assert_called_once_with("stale_sync_run_recovery")


if __name__ == "__main__":
    unittest.main()
