import unittest
from os import environ
from pathlib import Path
import runpy


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
        self.assertIn('"LOCAL_PGBOUNCER_SERVER_LIFETIME", "1800"', generator)
        self.assertIn("SUPPORTED_SERVER_LIFETIMES = (300, 900, 1800)", generator)
        self.assertIn("MIN_POOL_SIZE = max(6,", generator)
        self.assertIn("SERVER_IDLE_TIMEOUT = max(", generator)
        self.assertIn("SERVER_LIFETIME = max(", generator)
        self.assertIn("    600,", generator)
        self.assertIn("    120,", generator)
        self.assertIn("min_pool_size = {MIN_POOL_SIZE}", generator)
        self.assertIn("server_check_query = SELECT 1", generator)
        self.assertIn("server_check_delay = 5", generator)
        self.assertIn('"LOCAL_PGBOUNCER_QUERY_TIMEOUT", "20"', generator)
        self.assertIn("query_timeout = {QUERY_TIMEOUT}", generator)
        self.assertIn("query_wait_timeout = 15", generator)
        self.assertIn("tcp_keepalive = 1", generator)
        self.assertIn('"LOCAL_PGBOUNCER_TCP_KEEPCNT"', generator)
        self.assertIn('"LOCAL_PGBOUNCER_TCP_KEEPIDLE"', generator)
        self.assertIn('"LOCAL_PGBOUNCER_TCP_KEEPINTVL"', generator)

    def test_default_remote_pool_leaves_platform_keepalive_tunables_unset(self):
        module = runpy.run_path(str(PROJECT_ROOT / "scripts" / "dev" / "setup-local-pgbouncer.py"))
        config = module["_config_text"](
            {
                "host": "100.92.99.39",
                "port": 5432,
                "dbname": "show_ytb_data",
                "user": "cappa_rw",
                "password": "secret",
            }
        )

        self.assertIn("tcp_keepalive = 1", config)
        self.assertNotIn("tcp_keepcnt =", config)
        self.assertNotIn("tcp_keepidle =", config)
        self.assertNotIn("tcp_keepintvl =", config)

    def test_explicit_keepalive_tunables_remain_available_for_platform_ab(self):
        original = {
            key: environ.get(key)
            for key in (
                "LOCAL_PGBOUNCER_TCP_KEEPCNT",
                "LOCAL_PGBOUNCER_TCP_KEEPIDLE",
                "LOCAL_PGBOUNCER_TCP_KEEPINTVL",
            )
        }
        try:
            environ.update(
                {
                    "LOCAL_PGBOUNCER_TCP_KEEPCNT": "3",
                    "LOCAL_PGBOUNCER_TCP_KEEPIDLE": "10",
                    "LOCAL_PGBOUNCER_TCP_KEEPINTVL": "5",
                }
            )
            module = runpy.run_path(
                str(PROJECT_ROOT / "scripts" / "dev" / "setup-local-pgbouncer.py")
            )
            config = module["_config_text"](
                {
                    "host": "100.92.99.39",
                    "port": 5432,
                    "dbname": "show_ytb_data",
                    "user": "cappa_rw",
                    "password": "secret",
                }
            )
        finally:
            for key, value in original.items():
                if value is None:
                    environ.pop(key, None)
                else:
                    environ[key] = value

        self.assertIn("tcp_keepcnt = 3", config)
        self.assertIn("tcp_keepidle = 10", config)
        self.assertIn("tcp_keepintvl = 5", config)


if __name__ == "__main__":
    unittest.main()
