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

    def test_remote_pool_recycles_and_validates_server_connections(self):
        generator = (PROJECT_ROOT / "scripts" / "dev" / "setup-local-pgbouncer.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("server_connect_timeout = 10", generator)
        self.assertIn("server_reset_query = DISCARD ALL", generator)
        self.assertIn("server_reset_query_always = 1", generator)
        self.assertIn('"LOCAL_PGBOUNCER_MIN_POOL_SIZE", "6"', generator)
        self.assertIn('"LOCAL_PGBOUNCER_SERVER_IDLE_TIMEOUT", "600"', generator)
        self.assertIn('"LOCAL_PGBOUNCER_SERVER_LIFETIME", "21600"', generator)
        self.assertIn("MIN_POOL_SIZE = max(6,", generator)
        self.assertIn("SERVER_IDLE_TIMEOUT = max(", generator)
        self.assertIn("SERVER_LIFETIME = max(", generator)
        self.assertIn("    600,", generator)
        self.assertIn("    21600,", generator)
        self.assertIn("min_pool_size = {MIN_POOL_SIZE}", generator)
        self.assertIn("server_check_query = SELECT 1", generator)
        self.assertIn("server_check_delay = 5", generator)
        self.assertIn("query_timeout = 60", generator)
        self.assertIn("query_wait_timeout = 15", generator)
        self.assertIn("tcp_keepalive = 1", generator)


if __name__ == "__main__":
    unittest.main()
