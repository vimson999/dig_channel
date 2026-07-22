import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class LocalPgBouncerConfigTest(unittest.TestCase):
    def test_existing_process_is_reloaded_after_config_regeneration(self):
        script = (PROJECT_ROOT / "scripts" / "dev" / "run-local-pgbouncer.sh").read_text(
            encoding="utf-8"
        )

        self.assertEqual(script.count('kill -HUP "$existing_pid"'), 1)
        self.assertEqual(script.count('kill -HUP "$listening_pid"'), 1)

    def test_remote_pool_fails_fast_and_keeps_connections_warm(self):
        generator = (PROJECT_ROOT / "scripts" / "dev" / "setup-local-pgbouncer.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("server_connect_timeout = 10", generator)
        self.assertIn("min_pool_size = 2", generator)
        self.assertIn("server_idle_timeout = 120", generator)
        self.assertIn("query_timeout = 30", generator)
        self.assertIn("tcp_keepalive = 1", generator)


if __name__ == "__main__":
    unittest.main()
