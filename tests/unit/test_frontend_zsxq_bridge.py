import importlib.util
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = PROJECT_ROOT / "src" / "frontend" / "server.py"
SPEC = importlib.util.spec_from_file_location("frontend_server", SERVER_PATH)
frontend_server = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(frontend_server)


class _HealthBackendHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.server.received_paths.append(self.path)  # type: ignore[attr-defined]
        payload = json.dumps({"status": "healthy", "version": "1.0.0"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format, *_args):
        return


class _RunningServer:
    def __init__(self, server):
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _proxyless_open(url):
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
        url,
        timeout=2,
    )


class FrontendZsxqBridgeTest(unittest.TestCase):
    def _frontend(self, backend_url):
        class Handler(frontend_server.MyHTTPRequestHandler):
            zsxq_backend_url = backend_url

            def log_message(self, _format, *_args):
                return

        return _RunningServer(
            frontend_server.ReusableThreadingTCPServer(("127.0.0.1", 0), Handler)
        )

    def test_health_route_returns_the_real_backend_health_without_using_a_proxy(self):
        backend = ThreadingHTTPServer(("127.0.0.1", 0), _HealthBackendHandler)
        backend.received_paths = []
        with _RunningServer(backend) as running_backend:
            with self._frontend(running_backend.base_url) as frontend:
                with _proxyless_open(f"{frontend.base_url}/bridge/zsxq/health") as response:
                    body = json.load(response)

        self.assertEqual(response.status, 200)
        self.assertEqual(body, {"status": "healthy", "version": "1.0.0"})
        self.assertEqual(response.headers["Content-Type"], "application/json")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(backend.received_paths, ["/health"])

    def test_browser_page_renders_backend_health_as_html_instead_of_raw_json(self):
        backend = ThreadingHTTPServer(("127.0.0.1", 0), _HealthBackendHandler)
        backend.received_paths = []
        with _RunningServer(backend) as running_backend:
            with self._frontend(running_backend.base_url) as frontend:
                with _proxyless_open(
                    f"{frontend.base_url}/zsxq-sidebar-bridge.html"
                ) as response:
                    body = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("<title>知识星球本地桥接</title>", body)
        self.assertIn("服务状态：healthy", body)
        self.assertIn("版本：1.0.0", body)
        self.assertEqual(backend.received_paths, ["/health"])

    def test_health_route_returns_structured_502_when_backend_is_unavailable(self):
        with self._frontend("http://127.0.0.1:1") as frontend:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                _proxyless_open(f"{frontend.base_url}/bridge/zsxq/health")

        self.assertEqual(raised.exception.code, 502)
        self.assertEqual(
            json.load(raised.exception),
            {"status": "unavailable", "error": "zsxq_backend_unavailable"},
        )
        self.assertEqual(raised.exception.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
