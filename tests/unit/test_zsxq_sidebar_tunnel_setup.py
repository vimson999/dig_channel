import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETUP_SCRIPT = (
    PROJECT_ROOT
    / "plugins"
    / "zsxq-sidebar-sync"
    / "scripts"
    / "connect-secure-tunnel.sh"
)


class ZsxqSidebarTunnelSetupTest(unittest.TestCase):
    def _fake_tunnel_client(self, temp_dir: Path) -> tuple[Path, Path]:
        binary = temp_dir / "tunnel-client"
        call_log = temp_dir / "calls.log"
        binary.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                printf '%s\\n' "$@" >> "$FAKE_TUNNEL_LOG"
                if [ "$1" = "runtimes" ] && [ "$2" = "connect" ]; then
                  printf '%s\\n' '{"alias":"zsxq-sidebar","launched":true}'
                  exit 0
                fi
                if [ "$1" = "runtimes" ] && [ "$2" = "status" ]; then
                  printf '%s\\n' '{"process_running":true,"healthy":true,"ready":true}'
                  exit 0
                fi
                exit 0
                """
            ),
            encoding="utf-8",
        )
        binary.chmod(0o755)
        return binary, call_log

    def test_connect_stores_hidden_runtime_key_in_restricted_file_and_uses_reference(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            binary, call_log = self._fake_tunnel_client(temp_dir)
            secret_file = temp_dir / "secrets" / "runtime-api-key"
            runtime_key = "sk-test-secret-never-put-on-argv"
            env = {
                **os.environ,
                "FAKE_TUNNEL_LOG": str(call_log),
                "ZSXQ_TUNNEL_CLIENT_BIN": str(binary),
                "ZSXQ_TUNNEL_SECRET_FILE": str(secret_file),
            }

            result = subprocess.run(
                [
                    str(SETUP_SCRIPT),
                    "--tunnel-id",
                    "tunnel_0123456789abcdef0123456789abcdef",
                    "--runtime-key-stdin",
                ],
                input=f"{runtime_key}\n",
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(secret_file.read_text(encoding="utf-8"), runtime_key)
            self.assertEqual(stat.S_IMODE(secret_file.stat().st_mode), 0o600)
            calls = call_log.read_text(encoding="utf-8")
            self.assertIn("runtimes\nconnect", calls)
            self.assertIn(f"file:{secret_file}", calls)
            self.assertIn("run-zsxq-sidebar-mcp.sh", calls)
            self.assertNotIn(runtime_key, calls)
            self.assertNotIn(runtime_key, result.stdout)
            self.assertNotIn(runtime_key, result.stderr)

    def test_connect_rejects_invalid_tunnel_id_before_writing_or_launching(self):
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            binary, call_log = self._fake_tunnel_client(temp_dir)
            secret_file = temp_dir / "secrets" / "runtime-api-key"
            env = {
                **os.environ,
                "FAKE_TUNNEL_LOG": str(call_log),
                "ZSXQ_TUNNEL_CLIENT_BIN": str(binary),
                "ZSXQ_TUNNEL_SECRET_FILE": str(secret_file),
            }

            result = subprocess.run(
                [str(SETUP_SCRIPT), "--tunnel-id", "not-a-tunnel-id"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("tunnel_", result.stderr)
            self.assertFalse(secret_file.exists())
            self.assertFalse(call_log.exists())


if __name__ == "__main__":
    unittest.main()
