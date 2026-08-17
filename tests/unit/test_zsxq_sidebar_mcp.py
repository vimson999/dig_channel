import json
import sys
import threading
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
PROJECT_ROOT = BACKEND_DIR.parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from mcp_servers.zsxq_sidebar import (  # noqa: E402
    LocalApiError,
    ZsxqSidebarApiClient,
    create_mcp_server,
)


class _RecordingHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self._respond()

    def do_POST(self):  # noqa: N802
        self._respond()

    def _respond(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b""
        self.server.received.append(  # type: ignore[attr-defined]
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body": json.loads(raw_body) if raw_body else None,
            }
        )
        status, payload = self.server.responses.pop(0)  # type: ignore[attr-defined]
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format, *_args):
        return


class _LocalJsonServer:
    def __init__(self, *responses):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
        self.server.responses = list(responses)
        self.server.received = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    @property
    def received(self):
        return self.server.received

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _manifest(kind="mp3", run_id="sidebar-mp3-20260817T140000+0800"):
    scope = {
        "mp3": ("88844545452542", "调研纪要"),
        "pdf": ("51184248544214", "外资研报"),
    }[kind]
    return {
        "run_id": run_id,
        "kind": kind,
        "group_id": "28888112822211",
        "tab_id": scope[0],
        "tab_label": scope[1],
        "scan_summary": {"scanned_topics": 40, "new_downloads": 0},
        "history_observations": [],
        "files": [],
    }


class ZsxqSidebarApiClientTest(unittest.TestCase):
    def test_health_calls_only_the_loopback_health_endpoint(self):
        with _LocalJsonServer((200, {"status": "healthy", "version": "1.0.0"})) as api:
            client = ZsxqSidebarApiClient(api.base_url, retry_delay=0)

            result = client.health()

        self.assertEqual(result, {"status": "healthy", "version": "1.0.0"})
        self.assertEqual(api.received[0]["method"], "GET")
        self.assertEqual(api.received[0]["path"], "/health")

    def test_history_injects_the_fixed_group_and_matching_tab_scope(self):
        backend_result = {
            "success": True,
            "data": {
                "kind": "pdf",
                "days": 3,
                "complete": True,
                "files": [],
            },
        }
        with _LocalJsonServer((200, backend_result)) as api:
            client = ZsxqSidebarApiClient(api.base_url, retry_delay=0)

            result = client.get_history("pdf", days=3)

        self.assertEqual(result, backend_result["data"])
        self.assertEqual(
            api.received[0]["body"],
            {
                "kind": "pdf",
                "days": 3,
                "group_id": "28888112822211",
                "tab_id": "51184248544214",
            },
        )

    def test_history_rejects_unsupported_kind_before_making_a_request(self):
        client = ZsxqSidebarApiClient("http://127.0.0.1:5001", retry_delay=0)

        with self.assertRaisesRegex(ValueError, "mp3.*pdf"):
            client.get_history("video", days=3)

    def test_client_rejects_non_loopback_base_urls(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            ZsxqSidebarApiClient("https://example.com")

    def test_submit_computes_the_idempotency_key_and_enables_pipeline(self):
        manifest = _manifest()
        backend_result = {
            "success": True,
            "queued": True,
            "task_id": "task-123",
            "manifest_path": "/Users/v9/Downloads/知识星球下载/inbox/server-submit.json",
        }
        with _LocalJsonServer((202, backend_result)) as api:
            client = ZsxqSidebarApiClient(api.base_url, retry_delay=0)

            result = client.submit_manifest(manifest)

        self.assertEqual(result, backend_result)
        request = api.received[0]
        self.assertEqual(
            request["headers"]["Idempotency-Key"],
            f"zsxq-sidebar:mp3:{manifest['run_id']}",
        )
        self.assertEqual(
            request["body"],
            {
                "manifest": manifest,
                "dispatch_clean": True,
                "auto_publish": {"enabled": True},
            },
        )

    def test_submit_retries_transient_failure_with_identical_key_and_payload(self):
        manifest = _manifest("pdf", "sidebar-pdf-20260817T140000+0800")
        with _LocalJsonServer(
            (503, {"success": False, "error": "queue temporarily unavailable"}),
            (202, {"success": True, "queued": True, "task_id": "stable-task"}),
        ) as api:
            client = ZsxqSidebarApiClient(api.base_url, retry_delay=0)

            result = client.submit_manifest(manifest)

        self.assertEqual(result["task_id"], "stable-task")
        self.assertEqual(len(api.received), 2)
        self.assertEqual(api.received[0]["body"], api.received[1]["body"])
        self.assertEqual(
            api.received[0]["headers"]["Idempotency-Key"],
            api.received[1]["headers"]["Idempotency-Key"],
        )

    def test_idempotency_conflict_is_not_retried_and_preserves_backend_details(self):
        conflict = {
            "success": False,
            "code": "idempotency_conflict",
            "error": "payload changed",
        }
        with _LocalJsonServer((409, conflict)) as api:
            client = ZsxqSidebarApiClient(api.base_url, retry_delay=0)

            with self.assertRaises(LocalApiError) as raised:
                client.submit_manifest(_manifest())

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.payload, conflict)
        self.assertEqual(len(api.received), 1)


class _FakeLocalApiClient:
    def __init__(self):
        self.calls = []

    def health(self):
        self.calls.append(("health",))
        return {"status": "healthy", "version": "1.0.0"}

    def get_history(self, kind, *, days):
        self.calls.append(("history", kind, days))
        return {
            "kind": kind,
            "days": days,
            "complete": True,
            "files": [],
            "summary": {"unique_downloads": 0},
        }

    def submit_manifest(self, manifest):
        self.calls.append(("submit", manifest))
        return {
            "success": True,
            "queued": True,
            "task_id": "stable-task-id",
            "manifest_path": "/tmp/server-submit.json",
            "replayed": False,
        }


class ZsxqSidebarMcpServerTest(unittest.IsolatedAsyncioTestCase):
    async def test_server_creation_keeps_stdio_free_of_sdk_warnings(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            create_mcp_server(_FakeLocalApiClient())

        self.assertEqual(caught, [])

    async def test_server_exposes_only_three_scoped_tools_with_safety_annotations(self):
        server = create_mcp_server(_FakeLocalApiClient())

        tools = {tool.name: tool for tool in await server.list_tools()}

        self.assertEqual(
            set(tools),
            {"zsxq_health", "zsxq_get_history", "zsxq_submit_manifest"},
        )
        self.assertTrue(tools["zsxq_health"].annotations.readOnlyHint)
        self.assertTrue(tools["zsxq_get_history"].annotations.readOnlyHint)
        self.assertFalse(tools["zsxq_submit_manifest"].annotations.readOnlyHint)
        self.assertTrue(tools["zsxq_submit_manifest"].annotations.idempotentHint)
        self.assertFalse(tools["zsxq_submit_manifest"].annotations.openWorldHint)
        self.assertEqual(
            set(tools["zsxq_get_history"].inputSchema["properties"]),
            {"kind", "days"},
        )
        self.assertEqual(
            set(tools["zsxq_submit_manifest"].inputSchema["properties"]),
            {"manifest"},
        )

    async def test_tools_return_structured_backend_results(self):
        client = _FakeLocalApiClient()
        server = create_mcp_server(client)
        manifest = _manifest()

        _health_content, health = await server.call_tool("zsxq_health", {})
        _history_content, history = await server.call_tool(
            "zsxq_get_history", {"kind": "mp3", "days": 3}
        )
        _submitted_content, submitted = await server.call_tool(
            "zsxq_submit_manifest", {"manifest": manifest}
        )

        self.assertEqual(health["backend"]["status"], "healthy")
        self.assertTrue(history["complete"])
        self.assertEqual(submitted["task_id"], "stable-task-id")
        self.assertEqual(
            client.calls,
            [
                ("health",),
                ("history", "mp3", 3),
                ("submit", manifest),
            ],
        )

    async def test_plugin_launcher_serves_tools_over_real_stdio_transport(self):
        backend_result = {
            "success": True,
            "data": {
                "kind": "mp3",
                "days": 3,
                "complete": True,
                "files": [],
                "summary": {"unique_downloads": 0},
            },
        }
        launcher = (
            PROJECT_ROOT
            / "plugins"
            / "zsxq-sidebar-sync"
            / "scripts"
            / "run-zsxq-sidebar-mcp.sh"
        )
        with _LocalJsonServer(
            (200, {"status": "healthy", "version": "1.0.0"}),
            (200, backend_result),
        ) as api:
            params = StdioServerParameters(
                command=str(launcher),
                env={"ZSXQ_LOCAL_API_URL": api.base_url},
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    health = await session.call_tool("zsxq_health", {})
                    history = await session.call_tool(
                        "zsxq_get_history", {"kind": "mp3", "days": 3}
                    )

        self.assertEqual(
            {tool.name for tool in listed.tools},
            {"zsxq_health", "zsxq_get_history", "zsxq_submit_manifest"},
        )
        self.assertFalse(health.isError)
        self.assertEqual(health.structuredContent["backend"]["status"], "healthy")
        self.assertFalse(history.isError)
        self.assertTrue(history.structuredContent["complete"])


if __name__ == "__main__":
    unittest.main()
