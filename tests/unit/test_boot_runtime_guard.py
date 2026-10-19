import importlib.util
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = PROJECT_ROOT / "scripts" / "dev" / "check-boot-runtime-dependencies.py"


def load_guard_module():
    spec = importlib.util.spec_from_file_location("boot_runtime_guard", GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BootRuntimeGuardTest(unittest.TestCase):
    def test_tailscale_identity_must_be_online_with_an_address(self):
        guard = load_guard_module()

        self.assertEqual(
            guard.validate_tailscale_status(
                {
                    "BackendState": "Running",
                    "TailscaleIPs": ["100.96.3.12"],
                    "Self": {"Online": True},
                }
            ),
            [],
        )
        self.assertEqual(
            guard.validate_tailscale_status(
                {
                    "BackendState": "NeedsLogin",
                    "TailscaleIPs": None,
                    "Self": {"Online": False},
                }
            ),
            [
                "Tailscale backend is NeedsLogin, expected Running",
                "Tailscale has no assigned IP",
                "Tailscale self node is offline",
            ],
        )

    def test_database_route_rejects_proxy_tun_fallback(self):
        guard = load_guard_module()

        healthy = """
           route to: 100.92.99.39
        destination: 100.64.0.0
          interface: utun5
        """
        fallback = """
           route to: 100.92.99.39
        destination: 100.92.99.39
              gateway: 198.18.0.1
          interface: utun0
        """

        self.assertEqual(guard.validate_private_route(healthy), [])
        self.assertEqual(
            guard.validate_private_route(fallback),
            ["private service route is using proxy TUN address 198.18.0.1"],
        )

    def test_private_route_requires_a_utun_interface(self):
        guard = load_guard_module()

        self.assertEqual(
            guard.validate_private_route(
                "gateway: 192.168.1.1\ninterface: en0\n"
            ),
            ["private service route is not using a utun interface"],
        )

    def test_postgresql_probe_executes_authenticated_query(self):
        guard = load_guard_module()
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value

        with patch.object(guard.psycopg2, "connect", return_value=connection) as connect:
            guard.probe_runtime_url(
                "PostgreSQL",
                "postgresql://guard:secret@100.92.99.39:5432/runtime",
                2.2,
            )

        connect.assert_called_once_with(
            "postgresql://guard:secret@100.92.99.39:5432/runtime",
            connect_timeout=3,
            application_name="dig_ytb_boot_guard",
        )
        cursor.execute.assert_called_once_with("SELECT 1")
        connection.close.assert_called_once_with()

    def test_redis_probe_requires_ping_response(self):
        guard = load_guard_module()
        client = MagicMock()

        with patch.object(guard.redis.Redis, "from_url", return_value=client) as from_url:
            guard.probe_runtime_url(
                "Redis cache",
                "redis://:secret@100.92.99.39:6379/0",
                2.2,
            )

        from_url.assert_called_once_with(
            "redis://:secret@100.92.99.39:6379/0",
            socket_connect_timeout=2.2,
            socket_timeout=2.2,
        )
        client.ping.assert_called_once_with()
        client.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
