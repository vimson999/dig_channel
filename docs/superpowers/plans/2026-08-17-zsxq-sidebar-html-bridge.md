# Knowledge Planet Sidebar HTML Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add renderable localhost HTML pages that let the ChatGPT Pro Chrome sidebar query three-day Knowledge Planet download history and submit an idempotent MP3/PDF manifest without direct JSON navigation, Downloads access, or MCP Tunnel.

**Architecture:** Keep the existing Flask API on port 5001 authoritative for history, validation, durable idempotency, Celery, cleaning, publishing, and notifications. Add a narrow HTML adapter to the loopback-only port 8888 server: one server-rendered history page and one native submit form. The adapter owns only fixed-scope request construction, CSRF, request-size limits, HTML escaping, and conversion of backend JSON responses into renderable HTML.

**Tech Stack:** Python 3, `http.server`, `urllib.request`, `urllib.parse`, `hmac`, `secrets`, `unittest`, real loopback HTTP test servers with a fake 5001 backend.

## Global Constraints

- The fixed group is `28888112822211`.
- MP3 uses tab `88844545452542` / `调研纪要`; PDF uses tab `51184248544214` / `外资研报`.
- History is always three days; the sidebar cannot override group, tab, or days.
- A manifest contains zero through 40 files. Empty manifests remain valid and enter the normal sync workflow.
- The bridge always sends `dispatch_clean=true` and `auto_publish={"enabled": true}`.
- The bridge never accepts an arbitrary URL, backend path, filesystem path, or caller-supplied idempotency key.
- The bridge and backend stay bound to loopback.
- Bridge routes must not return wildcard CORS headers; unrelated frontend routes retain their existing behavior.
- Automated bridge tests use a fake backend and must not contact the live 5001 service, database, broker, or Celery.
- The four-hour automation remains paused until both skills pass manual tests and Codex passes one invocation test.
- Preserve unrelated working-tree changes. Stage and commit only files named by the current task.

## File structure

- Create `src/frontend/zsxq_sidebar_bridge.py`: fixed scopes, proxyless backend client, CSRF, manifest pre-validation, and server-rendered HTML results.
- Modify `src/frontend/server.py`: route dispatch, bounded form-body reading, HTML response headers, and route-specific CORS behavior.
- Modify `tests/unit/test_frontend_zsxq_bridge.py`: real HTTP contract tests against a recording fake backend.
- Modify `/Users/v9/.codex/skills/zsxq-mp3-sidebar-download/SKILL.md`: replace direct JSON API instructions with HTML history/form interaction.
- Modify `/Users/v9/.codex/skills/zsxq-mp3-sidebar-download/agents/openai.yaml`: describe the HTML bridge handoff accurately.
- Modify `/Users/v9/.codex/skills/zsxq-pdf-sidebar-download/SKILL.md`: apply the same HTML contract with PDF scope and threshold.
- Modify `/Users/v9/.codex/skills/zsxq-pdf-sidebar-download/agents/openai.yaml`: describe the HTML bridge handoff accurately.

---

### Task 1: Server-rendered history page

**Files:**
- Create: `src/frontend/zsxq_sidebar_bridge.py`
- Modify: `src/frontend/server.py`
- Test: `tests/unit/test_frontend_zsxq_bridge.py`

**Interfaces:**
- Produces: `TAB_SCOPES: dict[str, dict[str, str]]`.
- Produces: `BridgePage(status_code: int, body: str)`.
- Produces: `ZsxqSidebarHtmlBridge.render_history(kind: str) -> BridgePage`.
- Produces: `ZsxqSidebarHtmlBridge._request_json(method: str, path: str, payload: dict) -> tuple[int, dict]`.
- Produces: `ZsxqSidebarHtmlBridge._json_page(status_code: int, title: str, payload: dict, summary: str = "", pre_id: str = "bridge-json") -> BridgePage`.
- Consumes: backend `POST /api/v1/zsxq/browser-import/history`.

- [ ] **Step 1: Extend the fake backend and write the failing MP3 history test**

Replace the health-only fake with a recording handler that accepts queued responses, then add this observable contract test:

```python
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
```

The test catches a missing route, a GET sent to 5001, a caller-controlled scope, or a raw JSON response.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
venv/bin/python -m unittest tests/unit/test_frontend_zsxq_bridge.py -k history_page_posts_fixed_mp3_scope
```

Expected: FAIL because `/zsxq-sidebar-history.html` does not exist.

- [ ] **Step 3: Implement the minimal history bridge**

Create `src/frontend/zsxq_sidebar_bridge.py` with these concrete boundaries:

```python
from dataclasses import dataclass
import html
import json
import urllib.error
import urllib.request

TAB_SCOPES = {
    "mp3": {
        "group_id": "28888112822211",
        "tab_id": "88844545452542",
        "tab_label": "调研纪要",
    },
    "pdf": {
        "group_id": "28888112822211",
        "tab_id": "51184248544214",
        "tab_label": "外资研报",
    },
}

@dataclass(frozen=True)
class BridgePage:
    status_code: int
    body: str

class ZsxqSidebarHtmlBridge:
    def __init__(self, backend_url: str, csrf_secret: bytes, timeout: float = 3.0):
        self.backend_url = backend_url.rstrip("/")
        self.csrf_secret = csrf_secret
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def render_history(self, kind: str) -> BridgePage:
        scope = TAB_SCOPES.get(kind)
        if scope is None:
            return self._error_page(400, "invalid_kind", "kind 只允许 mp3 或 pdf")
        status, payload = self._request_json(
            "POST",
            "/api/v1/zsxq/browser-import/history",
            {
                "kind": kind,
                "days": 3,
                "group_id": scope["group_id"],
                "tab_id": scope["tab_id"],
            },
        )
        if not 200 <= status < 300 or payload.get("success") is not True:
            return self._json_page(status, "历史查询失败", payload)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        complete = "true" if data.get("complete") is True else "false"
        return self._json_page(
            200,
            f"知识星球 {scope['tab_label']} 最近 3 天历史",
            payload,
            summary=f"历史完整：{complete}",
        )
```

Implement `_request_json` with `urllib.request.Request`, UTF-8 JSON, `Content-Type: application/json`, the proxyless opener, and a three-second timeout. A transport error returns `(502, {"success": false, "code": "zsxq_backend_unavailable"})`; a non-object or non-JSON response returns `(502, {"success": false, "code": "zsxq_backend_invalid_response"})`; an `urllib.error.HTTPError` with an object JSON body preserves its HTTP status and payload. Render JSON with `json.dumps(payload, ensure_ascii=False, indent=2)` followed by `html.escape` inside `<pre id="history-json">`.

In `src/frontend/server.py`, route `GET /zsxq-sidebar-history.html` to a bridge created from the handler's `zsxq_backend_url` and process-level CSRF secret. Pass only `urllib.parse.parse_qs(query).get("kind", [""])[0]`.

```python
def _zsxq_bridge(self):
    return ZsxqSidebarHtmlBridge(
        self.zsxq_backend_url,
        self.zsxq_csrf_secret,
    )

def do_GET(self):
    parsed = urllib.parse.urlsplit(self.path)
    if parsed.path == "/zsxq-sidebar-history.html":
        query = urllib.parse.parse_qs(parsed.query)
        page = self._zsxq_bridge().render_history(query.get("kind", [""])[0])
        self._send_html(page.status_code, page.body)
        return
    # Preserve the existing health and static routes below this branch.
```

- [ ] **Step 4: Add PDF mapping and invalid-kind tests**

Add one PDF test that asserts tab `51184248544214`, and one invalid-kind test:

```python
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
```

- [ ] **Step 5: Run the history and existing health tests**

Run:

```bash
venv/bin/python -m unittest tests/unit/test_frontend_zsxq_bridge.py
```

Expected: all health and history tests PASS; no request reaches real port 5001.

- [ ] **Step 6: Commit only the history bridge files**

```bash
git add src/frontend/zsxq_sidebar_bridge.py src/frontend/server.py tests/unit/test_frontend_zsxq_bridge.py
git commit -m "feat: add sidebar history html bridge"
```

---

### Task 2: Native submit form and CSRF boundary

**Files:**
- Modify: `src/frontend/zsxq_sidebar_bridge.py`
- Modify: `src/frontend/server.py`
- Test: `tests/unit/test_frontend_zsxq_bridge.py`

**Interfaces:**
- Produces: `MAX_FORM_BODY_BYTES = 524288`.
- Produces: `ZsxqSidebarHtmlBridge.render_submit_form(kind: str) -> BridgePage`.
- Produces: `ZsxqSidebarHtmlBridge.valid_csrf(kind: str, token: str) -> bool`.

- [ ] **Step 1: Write the failing form test**

```python
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
```

The test catches JavaScript-only submission, a mutable scope, or a form without CSRF.

- [ ] **Step 2: Run the form test and verify RED**

Run:

```bash
venv/bin/python -m unittest tests/unit/test_frontend_zsxq_bridge.py -k submit_page_contains_native_form
```

Expected: FAIL because the submit GET route does not exist.

- [ ] **Step 3: Implement deterministic per-process CSRF and the native form**

Use an HMAC token bound to `kind`:

```python
import hashlib
import hmac

MAX_FORM_BODY_BYTES = 512 * 1024

def _csrf_token(self, kind: str) -> str:
    return hmac.new(
        self.csrf_secret,
        f"zsxq-sidebar-submit:{kind}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

def valid_csrf(self, kind: str, token: str) -> bool:
    return hmac.compare_digest(self._csrf_token(kind), str(token or ""))
```

`render_submit_form` must reject unknown kinds and return a server-rendered form with escaped hidden values, a `<textarea name="manifest_json" required></textarea>`, and a `<button type="submit">提交同步编排</button>`. It must not use script tags or fetch.

Generate one process secret in `server.py` with `secrets.token_bytes(32)`. Route `GET /zsxq-sidebar-submit.html` to `render_submit_form`.

- [ ] **Step 4: Remove wildcard CORS only from bridge routes**

Change `end_headers` so paths beginning with `/zsxq-sidebar-` or `/bridge/zsxq/` do not receive `Access-Control-Allow-Origin`. Preserve the existing headers for unrelated routes. Add to bridge HTML responses:

```python
def _is_zsxq_bridge_request(self):
    path = urllib.parse.urlsplit(self.path).path
    return path.startswith("/zsxq-sidebar-") or path.startswith("/bridge/zsxq/")

def end_headers(self):
    if not self._is_zsxq_bridge_request():
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
    super().end_headers()
```

```text
Cache-Control: no-store
Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'
Referrer-Policy: no-referrer
X-Content-Type-Options: nosniff
```

Add assertions that the submit page has no `Access-Control-Allow-Origin` and does include the four headers above.

- [ ] **Step 5: Run all bridge tests and commit**

Run:

```bash
venv/bin/python -m unittest tests/unit/test_frontend_zsxq_bridge.py
```

Expected: PASS.

Commit:

```bash
git add src/frontend/zsxq_sidebar_bridge.py src/frontend/server.py tests/unit/test_frontend_zsxq_bridge.py
git commit -m "feat: add protected sidebar submit form"
```

---

### Task 3: Valid manifest POST and durable idempotency handoff

**Files:**
- Modify: `src/frontend/zsxq_sidebar_bridge.py`
- Modify: `src/frontend/server.py`
- Test: `tests/unit/test_frontend_zsxq_bridge.py`

**Interfaces:**
- Produces: `ZsxqSidebarHtmlBridge.submit(kind: str, csrf_token: str, manifest_json: str) -> BridgePage`.
- Consumes: backend `POST /api/v1/zsxq/browser-import`.
- Emits: `Idempotency-Key: zsxq-sidebar:<kind>:<run_id>`.

- [ ] **Step 1: Write the failing valid-submit test**

Use a literal empty MP3 manifest and extract the hidden token from the GET form with `html.parser.HTMLParser`. POST it as `application/x-www-form-urlencoded`:

```python
from html.parser import HTMLParser

class _CsrfParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("name") == "csrf_token":
            self.token = values.get("value")

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
```

- [ ] **Step 2: Run the submit test and verify RED**

Run:

```bash
venv/bin/python -m unittest tests/unit/test_frontend_zsxq_bridge.py -k valid_manifest_post
```

Expected: FAIL because the handler has no submit POST behavior.

- [ ] **Step 3: Implement bounded form parsing in the HTTP handler**

In `server.py`, accept POST only for `/zsxq-sidebar-submit.html`. Require `application/x-www-form-urlencoded`, a numeric content length from 1 through `MAX_FORM_BODY_BYTES`, decode UTF-8, and parse the form exactly as follows:

```python
content_type = self.headers.get_content_type()
if content_type != "application/x-www-form-urlencoded":
    self._send_bridge_error(415, "unsupported_media_type")
    return
raw_length = self.headers.get("Content-Length", "")
try:
    content_length = int(raw_length)
except ValueError:
    self._send_bridge_error(400, "invalid_content_length")
    return
if content_length < 1 or content_length > MAX_FORM_BODY_BYTES:
    self._send_bridge_error(
        413 if content_length > MAX_FORM_BODY_BYTES else 400,
        "request_body_too_large" if content_length > MAX_FORM_BODY_BYTES else "empty_request_body",
    )
    return
decoded_body = self.rfile.read(content_length).decode("utf-8")
fields = urllib.parse.parse_qs(decoded_body, keep_blank_values=True)
page = self._zsxq_bridge().submit(
    fields.get("kind", [""])[0],
    fields.get("csrf_token", [""])[0],
    fields.get("manifest_json", [""])[0],
)
```

Return HTML for 400, 403, 413, and 415 errors.

- [ ] **Step 4: Implement manifest pre-validation and backend forwarding**

In `ZsxqSidebarHtmlBridge.submit`:

```python
RUN_ID = re.compile(r"^[A-Za-z0-9._:+-]{1,180}$")

manifest = json.loads(manifest_json)
if not isinstance(manifest, dict):
    return self._error_page(400, "invalid_manifest", "manifest 必须是 JSON 对象")
scope = TAB_SCOPES.get(kind)
if scope is None or manifest.get("kind") != kind:
    return self._error_page(400, "scope_mismatch", "manifest.kind 与页面不匹配")
for field in ("group_id", "tab_id", "tab_label"):
    if str(manifest.get(field) or "") != scope[field]:
        return self._error_page(400, "scope_mismatch", f"manifest.{field} 不匹配")
run_id = str(manifest.get("run_id") or "")
if RUN_ID.fullmatch(run_id) is None:
    return self._error_page(400, "invalid_run_id", "run_id 格式无效")
files = manifest.get("files")
if not isinstance(files, list) or len(files) > 40:
    return self._error_page(400, "invalid_files", "files 必须是最多 40 项的数组")
```

Verify CSRF before parsing the manifest. Forward only the fixed payload and computed idempotency header. Preserve a backend HTTP 200, 202, 400, 409, 500, or 503 as the HTML response status and render the escaped backend body in `<pre id="submit-result-json">`.

- [ ] **Step 5: Write and pass the replay contract test**

Queue two fake backend responses with the same `task_id`, the second containing `replayed: true`. Submit the identical manifest twice and assert both recorded requests have byte-equivalent JSON bodies and identical `Idempotency-Key` headers. Assert the second HTML page displays `replayed` and the stable task ID.

Run:

```bash
venv/bin/python -m unittest tests/unit/test_frontend_zsxq_bridge.py
```

Expected: PASS.

- [ ] **Step 6: Commit the POST handoff**

```bash
git add src/frontend/zsxq_sidebar_bridge.py src/frontend/server.py tests/unit/test_frontend_zsxq_bridge.py
git commit -m "feat: submit sidebar manifests through html bridge"
```

---

### Task 4: Failure-closed and security regression coverage

**Files:**
- Modify: `src/frontend/zsxq_sidebar_bridge.py`
- Modify: `src/frontend/server.py`
- Test: `tests/unit/test_frontend_zsxq_bridge.py`

**Interfaces:**
- Strengthens the Task 1–3 interfaces without adding public routes.

- [ ] **Step 1: Add failing table-driven rejection tests**

Add literal cases for malformed JSON, manifest kind mismatch, wrong group, wrong tab, wrong label, invalid run ID, non-list files, and 41 files:

```python
cases = [
    ("malformed-json", "not-json", 400),
    ("kind-mismatch", {**valid_manifest, "kind": "pdf"}, 400),
    ("wrong-group", {**valid_manifest, "group_id": "wrong"}, 400),
    ("wrong-tab", {**valid_manifest, "tab_id": "wrong"}, 400),
    ("wrong-label", {**valid_manifest, "tab_label": "wrong"}, 400),
    ("invalid-run-id", {**valid_manifest, "run_id": "bad run id"}, 400),
    ("non-list-files", {**valid_manifest, "files": {}}, 400),
    ("too-many-files", {**valid_manifest, "files": [{} for _ in range(41)]}, 400),
]
```

Add separate real-HTTP tests for invalid CSRF (403), wrong content type (415), and a `MAX_FORM_BODY_BYTES + 1` body (413). Every case asserts `backend.received == []`.

- [ ] **Step 2: Run rejection tests and verify at least one expected RED failure**

Run:

```bash
venv/bin/python -m unittest tests/unit/test_frontend_zsxq_bridge.py -k reject
```

Expected: one or more FAIL results for branches not yet enforced; syntax or test-setup errors do not count as RED.

- [ ] **Step 3: Add the minimal missing guards and rerun GREEN**

Implement only the branches exposed by Step 2. Keep validation before `_request_json` so rejected requests never touch the backend.

```python
if not self.valid_csrf(kind, csrf_token):
    return self._error_page(403, "invalid_csrf", "提交令牌无效")
try:
    manifest = json.loads(manifest_json)
except (TypeError, json.JSONDecodeError):
    return self._error_page(400, "invalid_manifest", "manifest JSON 无法解析")
```

- [ ] **Step 4: Add backend and escaping tests**

Test these literal outcomes:

- unreachable backend returns HTTP 502 HTML containing `zsxq_backend_unavailable`;
- backend non-JSON returns HTTP 502 HTML containing `zsxq_backend_invalid_response`;
- backend HTTP 409 with `idempotency_conflict` remains HTTP 409;
- backend string `<script>alert(1)</script>` appears only as escaped text and never as a script element;
- history `complete=false` remains visible as `历史完整：false` so the skill can fail closed;
- bridge responses have no wildcard CORS, while an unrelated static response keeps the existing CORS behavior.

- [ ] **Step 5: Run the complete fake-backend suite and syntax checks**

Run:

```bash
venv/bin/python -m unittest tests/unit/test_frontend_zsxq_bridge.py
venv/bin/python -m py_compile src/frontend/server.py src/frontend/zsxq_sidebar_bridge.py
```

Expected: all tests PASS with no warnings; `py_compile` exits 0.

- [ ] **Step 6: Commit the hardening**

```bash
git add src/frontend/zsxq_sidebar_bridge.py src/frontend/server.py tests/unit/test_frontend_zsxq_bridge.py
git commit -m "test: harden sidebar html bridge"
```

---

### Task 5: Restart 8888 and complete the non-mutating browser gate

**Files:**
- No file changes.

**Interfaces:**
- Consumes: live loopback 8888 and read-only history calls to 5001.
- Produces: user-confirmed Browser Use compatibility before skill edits.

- [ ] **Step 1: Restart only the 8888 frontend process**

Stop the existing `scripts/dev/run-frontend.sh` session and start the same script again. Do not restart 5001, PostgreSQL, Redis, or Celery.

- [ ] **Step 2: Verify the existing health and static pages**

Open or request:

```text
http://localhost:8888/zsxq-sidebar-bridge.html
http://localhost:8888/index.html
```

Expected: health page says `healthy`; Shioa index remains available.

- [ ] **Step 3: Ask the user to test both read-only history pages with Web preview**

Exact URLs:

```text
http://localhost:8888/zsxq-sidebar-history.html?kind=mp3
http://localhost:8888/zsxq-sidebar-history.html?kind=pdf
```

Expected: each page displays `历史完整：true`, the correct kind, and recent files. Neither page triggers a download, file move, database write, or Celery task.

- [ ] **Step 4: Test submit form rendering without a valid submission**

Open:

```text
http://localhost:8888/zsxq-sidebar-submit.html?kind=mp3
```

Fill `manifest_json` with the literal text `not-json` and click `提交同步编排`. Expected: HTTP 400 HTML with `invalid_manifest`; no request reaches 5001 and no Celery task is created.

- [ ] **Step 5: Stop at the manual gate if any page is blocked**

If Web preview reports `ERR_BLOCKED_BY_CLIENT`, lacks a form, or cannot submit the invalid payload, keep both skills unchanged and debug the bridge with a failing automated reproduction before continuing.

---

### Task 6: Update and verify the MP3 sidebar skill

**Files:**
- Modify: `/Users/v9/.codex/skills/zsxq-mp3-sidebar-download/SKILL.md`
- Modify: `/Users/v9/.codex/skills/zsxq-mp3-sidebar-download/agents/openai.yaml`

**Interfaces:**
- Consumes history page: `http://localhost:8888/zsxq-sidebar-history.html?kind=mp3`.
- Consumes submit page: `http://localhost:8888/zsxq-sidebar-submit.html?kind=mp3`.
- Preserves: 48 hours, strict `>10`, three-day dedupe, 40 topic groups, 40 downloads, fixed target URL, and zero-file submission.

- [ ] **Step 1: Record the existing RED evidence before editing**

Use the already observed manual result as the baseline pressure test: the current skill tries direct `127.0.0.1:5001`/curl access and stops with `history_api_unavailable` or `tool_route_mismatch`, despite the HTML health page being reachable. Record that exact failure in the execution notes.

- [ ] **Step 2: Replace the history interaction with a positive HTML recipe**

State the required sequence explicitly:

1. Open the fixed MP3 history HTML URL using the same Web preview page-opening method that opened the health page.
2. Read the visible JSON from `history-json`.
3. Continue only when `success=true` and `data.complete=true`.
4. Do not use curl, `exec_command`, terminal, direct port 5001, local JSON files, or Downloads directory access.

- [ ] **Step 3: Replace direct API submission with the native form recipe**

Require the skill to open the fixed MP3 submit URL, fill `manifest_json` with the exact in-memory manifest, click `提交同步编排`, and read `task_id`, `manifest_path`, and `replayed` from the result page. A transport retry must reuse the identical manifest and `run_id`; it must not rescan or redownload.

- [ ] **Step 4: Validate the skill package**

Run:

```bash
venv/bin/python /Users/v9/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/v9/.codex/skills/zsxq-mp3-sidebar-download
```

Expected: validation succeeds with no frontmatter or package errors.

- [ ] **Step 5: Run the MP3 GREEN pressure scenario in the sidebar**

On the already-open exact MP3 target page, start a new sidebar chat and send only:

```text
请调用技能：知识星球浏览器侧边栏mp3下载。
```

Expected: it uses the HTML history page, never asks for Downloads permission, performs the MP3 scan/download rules, submits through the HTML form, and reports a task ID. If the manifest has zero files, it still reports a queued zero-file sync.

- [ ] **Step 6: Stop before editing PDF until MP3 passes**

This is the mandatory per-skill deployment gate. A failed MP3 run is debugged and retested before the PDF skill is changed.

---

### Task 7: Update and verify the PDF sidebar skill

**Files:**
- Modify: `/Users/v9/.codex/skills/zsxq-pdf-sidebar-download/SKILL.md`
- Modify: `/Users/v9/.codex/skills/zsxq-pdf-sidebar-download/agents/openai.yaml`

**Interfaces:**
- Consumes history page: `http://localhost:8888/zsxq-sidebar-history.html?kind=pdf`.
- Consumes submit page: `http://localhost:8888/zsxq-sidebar-submit.html?kind=pdf`.
- Preserves: 48 hours, strict `>30`, three-day dedupe, 40 topic groups, 40 downloads, fixed target URL, and zero-file submission.

- [ ] **Step 1: Record the existing RED evidence before editing**

Use the observed PDF baseline: direct local JSON reads fail with `EPERM`, direct 5001 access fails or is blocked, and the obsolete authorization code does not grant macOS filesystem permission.

- [ ] **Step 2: Apply the verified MP3 HTML interaction shape with PDF constants**

Use only the PDF history and submit URLs above. Keep the PDF target page, strict `>30` threshold, `kind=pdf`, tab `51184248544214`, and label `外资研报`. Remove the authorization-code requirement because the skill no longer accesses Downloads JSON.

- [ ] **Step 3: Validate the PDF skill package**

Run:

```bash
venv/bin/python /Users/v9/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/v9/.codex/skills/zsxq-pdf-sidebar-download
```

Expected: validation succeeds with no frontmatter or package errors.

- [ ] **Step 4: Run the PDF GREEN pressure scenario in the sidebar**

On the already-open exact PDF target page, start a new sidebar chat and send only:

```text
请调用技能：知识星球浏览器侧边栏 PDF 下载。
```

Expected: it uses the HTML history page, never asks for a folder authorization code, scans/downloads only PDFs under the fixed rules, submits through the HTML form, and reports a task ID or a queued zero-file sync.

- [ ] **Step 5: Compare both final skill reports**

Verify both reports include scanned topics, discovered files, new downloads, dedupe summary, backend manifest path, task ID, and replay status. Verify neither report mentions direct curl, direct port 5001, inbox JSON, `Operation not permitted`, or `ZSXQ-PDF-INBOX-AUTH-V1`.

---

### Task 8: Codex invocation test and automation decision

**Files:**
- Modify only through the automation API after explicit user approval: automation `mp3-2`.

**Interfaces:**
- Consumes: both manually verified skills.
- Produces: one Codex-driven end-to-end invocation result and a go/no-go decision for the four-hour schedule.

- [ ] **Step 1: Keep automation paused during Codex testing**

Confirm `mp3-2` remains paused. Do not let a scheduled run overlap the manual or Codex test.

- [ ] **Step 2: Run the Codex preflight plus short MP3 skill call**

Codex locates the `vimson5000` Chrome profile and exact MP3 URL, refreshes and waits for content, confirms the ChatGPT sidebar is open, starts a new chat, and sends only the MP3 skill invocation. Codex does not perform the downloads itself.

- [ ] **Step 3: Run the Codex preflight plus short PDF skill call**

Repeat with the exact PDF URL and only the PDF skill invocation. Codex does not navigate from inside the skill and does not submit a second manifest for the same run.

- [ ] **Step 4: Verify backend idempotency and downstream visibility**

For each returned task ID, confirm one submission receipt, one logical Celery task, one sync run, one workflow, and one notification chain. A replayed submission must point to the original task ID.

- [ ] **Step 5: Ask before resuming the schedule**

Report the MP3 and PDF results. Resume the four-hour automation only after the user explicitly approves; otherwise leave it paused.

---

## Final verification matrix

Before declaring the feature complete, run or obtain evidence for every row:

| Layer | Verification | Required result |
|---|---|---|
| Unit/integration | `venv/bin/python -m unittest tests/unit/test_frontend_zsxq_bridge.py` | All pass against fake backend |
| Syntax | `venv/bin/python -m py_compile src/frontend/server.py src/frontend/zsxq_sidebar_bridge.py` | Exit 0 |
| Existing UI | `/index.html` and `/zsxq-sidebar-bridge.html` | Render normally |
| History Browser Use | MP3 and PDF history HTML pages | Both render `complete=true` |
| Submit safety | Invalid manifest form submission | HTML 400, no backend/Celery call |
| MP3 skill | Manual sidebar run | HTML history + form submit + task ID |
| PDF skill | Manual sidebar run | No authorization prompt + task ID |
| Codex invocation | One MP3 and one PDF run | Sidebar executes; Codex only performs preflight |
| Idempotency | Replay same manifest | Same task ID and one workflow |
| Automation | `mp3-2` | Remains paused until explicit approval |
