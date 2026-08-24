import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENSURE_SCRIPT = PROJECT_ROOT / "scripts" / "dev" / "ensure-zsxq-sidebar-bridge.py"


def _unused_local_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class EnsureZsxqSidebarBridgeTest(unittest.TestCase):
    def test_starts_bridge_when_health_endpoint_is_unavailable(self):
        port = _unused_local_port()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            server_script = temp_path / "fake_bridge.py"
            pid_file = temp_path / "bridge.pid"
            log_file = temp_path / "bridge.log"
            server_script.write_text(
                """
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/zsxq-sidebar-bridge.html':
            self.send_response(404)
            self.end_headers()
            return
        body = '服务状态：healthy'.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return

ThreadingHTTPServer(
    ('127.0.0.1', int(os.environ['ZSXQ_BRIDGE_TEST_PORT'])), Handler
).serve_forever()
""".lstrip(),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["ZSXQ_BRIDGE_TEST_PORT"] = str(port)
            process = subprocess.run(
                [
                    sys.executable,
                    str(ENSURE_SCRIPT),
                    "--health-url",
                    f"http://127.0.0.1:{port}/zsxq-sidebar-bridge.html",
                    "--python",
                    sys.executable,
                    "--server-script",
                    str(server_script),
                    "--server-cwd",
                    str(temp_path),
                    "--pid-file",
                    str(pid_file),
                    "--log-file",
                    str(log_file),
                    "--timeout-seconds",
                    "5",
                ],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(
                process.returncode,
                0,
                f"stdout={process.stdout}\nstderr={process.stderr}",
            )
            self.assertIn("started", process.stdout)
            bridge_pid = int(pid_file.read_text(encoding="utf-8").strip())
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(
                    f"http://127.0.0.1:{port}/zsxq-sidebar-bridge.html",
                    timeout=2,
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn("服务状态：healthy", response.read().decode("utf-8"))
            finally:
                os.kill(bridge_pid, signal.SIGTERM)
                for _ in range(50):
                    try:
                        os.kill(bridge_pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.02)


if __name__ == "__main__":
    unittest.main()
