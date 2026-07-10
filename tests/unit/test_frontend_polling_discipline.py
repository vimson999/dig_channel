import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FrontendPollingDisciplineTest(unittest.TestCase):
    def test_zsxq_sync_polling_is_single_flight(self):
        source = (PROJECT_ROOT / "src" / "frontend" / "js" / "zsxq-lab.js").read_text(encoding="utf-8")
        polling = source.split("function startSyncPolling()", 1)[1].split("\nfunction stopSyncPolling()", 1)[0]

        self.assertIn("syncPollInFlight", source)
        self.assertIn("if (state.syncPollInFlight) return", polling)
        self.assertIn("state.syncPollInFlight = true", polling)
        self.assertIn("finally", polling)
        self.assertIn("state.syncPollInFlight = false", polling)


if __name__ == "__main__":
    unittest.main()
