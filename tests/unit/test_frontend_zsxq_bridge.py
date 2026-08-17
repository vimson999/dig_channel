import importlib.util
import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = PROJECT_ROOT / "src" / "frontend" / "server.py"
SPEC = importlib.util.spec_from_file_location("frontend_server", SERVER_PATH)
frontend_server = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(frontend_server)


class _LocalJsonBackend:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.received = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(handler):  # noqa: N802
                self._respond(handler)

            def do_POST(handler):  # noqa: N802
                self._respond(handler)

            def log_message(handler, _format, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.running = _RunningServer(self.server)

    @property
    def base_url(self):
        return self.running.base_url

    def __enter__(self):
        self.running.__enter__()
        return self

    def __exit__(self, *_args):
        self.running.__exit__()

    def _respond(self, handler):
        length = int(handler.headers.get("Content-Length", "0"))
        raw_body = handler.rfile.read(length)
        body = json.loads(raw_body or b"{}")
        self.received.append(
            {
                "method": handler.command,
                "path": handler.path,
                "headers": dict(handler.headers.items()),
                "raw_body": raw_body,
                "body": body,
            }
        )
        if self.responses:
            status, payload = self.responses.pop(0)
        else:
            status, payload = 200, {"status": "healthy", "version": "1.0.0"}
        encoded_payload = json.dumps(payload).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(encoded_payload)))
        handler.end_headers()
        handler.wfile.write(encoded_payload)


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


class _CsrfParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("name") == "csrf_token":
            self.token = values.get("value")


def _fetch_csrf(base_url, kind):
    with _proxyless_open(f"{base_url}/zsxq-sidebar-submit.html?kind={kind}") as response:
        parser = _CsrfParser()
        parser.feed(response.read().decode("utf-8"))
    return parser.token


def _post_form(base_url, *, kind, csrf_token, manifest):
    encoded_body = urllib.parse.urlencode(
        {
            "kind": kind,
            "csrf_token": csrf_token,
            "manifest_json": json.dumps(manifest),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/zsxq-sidebar-submit.html",
        data=encoded_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with _proxyless_open(request) as response:
        return response, response.read().decode("utf-8")


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
        with _LocalJsonBackend() as backend:
            with self._frontend(backend.base_url) as frontend:
                with _proxyless_open(f"{frontend.base_url}/bridge/zsxq/health") as response:
                    body = json.load(response)

        self.assertEqual(response.status, 200)
        self.assertEqual(body, {"status": "healthy", "version": "1.0.0"})
        self.assertEqual(response.headers["Content-Type"], "application/json")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(len(backend.received), 1)
        self.assertEqual(backend.received[0]["method"], "GET")
        self.assertEqual(backend.received[0]["path"], "/health")
        self.assertEqual(backend.received[0]["body"], {})

    def test_browser_page_renders_backend_health_as_html_instead_of_raw_json(self):
        with _LocalJsonBackend() as backend:
            with self._frontend(backend.base_url) as frontend:
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
        self.assertEqual(len(backend.received), 1)
        self.assertEqual(backend.received[0]["method"], "GET")
        self.assertEqual(backend.received[0]["path"], "/health")
        self.assertEqual(backend.received[0]["body"], {})

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

    def test_history_page_posts_fixed_mp3_scope_and_renders_html(self):
        backend_payload = {
            "success": True,
            "data": {
                "kind": "mp3",
                "days": 3,
                "complete": True,
                "files": [{"filename": "already-downloaded.mp3"}],
                "summary": {"unique_downloads": 1, "duplicates_collapsed": 2},
            },
        }
        with _LocalJsonBackend((200, backend_payload)) as backend:
            with self._frontend(backend.base_url) as frontend:
                with _proxyless_open(
                    f"{frontend.base_url}/zsxq-sidebar-history.html?kind=mp3"
                ) as response:
                    body = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("历史完整：true", body)
        self.assertIn("already-downloaded.mp3", body)
        self.assertEqual(len(backend.received), 1)
        request = backend.received[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/api/v1/zsxq/browser-import/history")
        self.assertEqual(
            request["body"],
            {
                "kind": "mp3",
                "days": 3,
                "group_id": "28888112822211",
                "tab_id": "88844545452542",
            },
        )

    def test_bridge_pages_omit_wildcard_cors_while_unrelated_routes_retain_it(self):
        history_payload = {"success": True, "data": {}}
        with _LocalJsonBackend((200, history_payload)) as backend:
            with self._frontend(backend.base_url) as frontend:
                with _proxyless_open(
                    f"{frontend.base_url}/zsxq-sidebar-history.html?kind=mp3"
                ) as history_response:
                    history_response.read()
                with self.assertRaises(urllib.error.HTTPError) as unrelated:
                    _proxyless_open(f"{frontend.base_url}/not-a-zsxq-bridge-route")

        self.assertNotIn("Access-Control-Allow-Origin", history_response.headers)
        self.assertEqual(
            unrelated.exception.headers["Access-Control-Allow-Origin"], "*"
        )

    def test_history_page_posts_fixed_pdf_scope(self):
        with _LocalJsonBackend((200, {"success": True, "data": {}})) as backend:
            with self._frontend(backend.base_url) as frontend:
                with _proxyless_open(
                    f"{frontend.base_url}/zsxq-sidebar-history.html?kind=pdf"
                ) as response:
                    response.read()

        self.assertEqual(response.status, 200)
        self.assertEqual(len(backend.received), 1)
        self.assertEqual(
            backend.received[0]["body"],
            {
                "kind": "pdf",
                "days": 3,
                "group_id": "28888112822211",
                "tab_id": "51184248544214",
            },
        )

    def test_history_page_rejects_unknown_kind_without_backend_call(self):
        with _LocalJsonBackend() as backend:
            with self._frontend(backend.base_url) as frontend:
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    _proxyless_open(
                        f"{frontend.base_url}/zsxq-sidebar-history.html?kind=video"
                    )

        self.assertEqual(raised.exception.code, 400)
        self.assertEqual(raised.exception.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(backend.received, [])

    def test_submit_page_contains_native_form_fixed_kind_and_csrf(self):
        with self._frontend("http://127.0.0.1:1") as frontend:
            with _proxyless_open(
                f"{frontend.base_url}/zsxq-sidebar-submit.html?kind=pdf"
            ) as response:
                body = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertIn('method="post"', body.lower())
        self.assertIn('action="/zsxq-sidebar-submit.html"', body)
        self.assertIn('name="kind" value="pdf"', body)
        self.assertIn('name="csrf_token"', body)
        self.assertIn('name="manifest_json"', body)
        self.assertIn("提交同步编排", body)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(
            response.headers["Content-Security-Policy"],
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
        )
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_valid_manifest_post_forwards_fixed_payload_and_idempotency_key(self):
        manifest = {
            "run_id": "sidebar-mp3-20260817T160000+0800-a1b2",
            "kind": "mp3",
            "group_id": "28888112822211",
            "tab_id": "88844545452542",
            "tab_label": "调研纪要",
            "scan_summary": {"scanned_topics": 0, "new_downloads": 0},
            "history_observations": [],
            "files": [],
        }
        backend_payload = {
            "success": True,
            "queued": True,
            "task_id": "stable-task-123",
            "manifest_path": "/Users/v9/Downloads/知识星球下载/inbox/server-submit.json",
            "replayed": False,
        }
        with _LocalJsonBackend((202, backend_payload)) as backend:
            with self._frontend(backend.base_url) as frontend:
                csrf = _fetch_csrf(frontend.base_url, "mp3")
                response, body = _post_form(
                    frontend.base_url,
                    kind="mp3",
                    csrf_token=csrf,
                    manifest=manifest,
                )

        self.assertEqual(response.status, 202)
        self.assertIn("stable-task-123", body)
        self.assertEqual(
            backend.received[0]["headers"]["Idempotency-Key"],
            "zsxq-sidebar:mp3:sidebar-mp3-20260817T160000+0800-a1b2",
        )
        self.assertEqual(
            backend.received[0]["body"],
            {
                "manifest": manifest,
                "dispatch_clean": True,
                "auto_publish": {"enabled": True},
            },
        )

    def test_manifest_retry_reuses_the_same_backend_idempotency_handoff(self):
        manifest = {
            "run_id": "sidebar-mp3-20260817T160000+0800-a1b2",
            "kind": "mp3",
            "group_id": "28888112822211",
            "tab_id": "88844545452542",
            "tab_label": "调研纪要",
            "scan_summary": {"scanned_topics": 0, "new_downloads": 0},
            "history_observations": [],
            "files": [],
        }
        initial_payload = {
            "success": True,
            "queued": True,
            "task_id": "stable-task-123",
            "replayed": False,
        }
        replayed_payload = {**initial_payload, "replayed": True}
        with _LocalJsonBackend(
            (202, initial_payload),
            (202, replayed_payload),
        ) as backend:
            with self._frontend(backend.base_url) as frontend:
                csrf = _fetch_csrf(frontend.base_url, "mp3")
                first_response, _ = _post_form(
                    frontend.base_url,
                    kind="mp3",
                    csrf_token=csrf,
                    manifest=manifest,
                )
                second_response, second_body = _post_form(
                    frontend.base_url,
                    kind="mp3",
                    csrf_token=csrf,
                    manifest=manifest,
                )

        self.assertEqual(first_response.status, 202)
        self.assertEqual(second_response.status, 202)
        self.assertEqual(backend.received[0]["raw_body"], backend.received[1]["raw_body"])
        self.assertEqual(
            backend.received[0]["headers"]["Idempotency-Key"],
            backend.received[1]["headers"]["Idempotency-Key"],
        )
        self.assertIn("stable-task-123", second_body)
        self.assertIn("replayed", second_body)


if __name__ == "__main__":
    unittest.main()
