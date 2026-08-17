# Knowledge Planet Sidebar API-Owned State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 MP3/PDF 侧边栏的最近 3 天去重、manifest 持久化和重试幂等性迁移到本地 loopback API，使侧边栏不再直接读写 Downloads JSON。

**Architecture:** 新增 history 查询和内联 manifest 模式；本地服务读取既有 submit/result 账本并原子写入新 manifest。新增持久化 submission receipt，通过幂等键、payload hash、确定性 task ID 和 worker 原子 claim 防止重复同步 run、工作流和邮件；现有路径模式及下游 Celery 工作流保持兼容。

**Tech Stack:** Flask、SQLAlchemy、Alembic、Celery、Python `pathlib/json/hashlib/uuid`、pytest/unittest。

## Global Constraints

- 不修改既有知识星球 API 上游 `run_zsxq_sync`。
- 保留 `manifest_paths` preview/import 调用。
- history 与 import 新能力只允许 loopback 客户端。
- MP3 下载阈值严格 `>10`，PDF 严格 `>30`；48 小时、最近 3 天、最多 40 组/40 个不变。
- history 不完整时 fail closed，不允许侧边栏继续下载。
- 空 `files` 仍创建服务端 manifest、同步 run、工作流和通知。
- 同一幂等键和相同有效请求（manifest 加清洗/发布参数）只能产生一个逻辑任务、同步 run 和工作流；参数变化返回冲突。
- 保留工作区中与本任务无关的修改；当前共享工作区已有大量用户修改，本次不自动提交 Git commit。
- 计划任务保持 `PAUSED`，直至人工和 Codex 调用测试通过。

---

### Task 1: Server-owned recent download history

**Files:**
- Modify: `src/backend/services/zsxq_browser_import_service.py`
- Modify: `src/backend/apis/v1/zsxq_browser_import.py`
- Test: `tests/unit/test_zsxq_browser_import_service.py`
- Test: `tests/unit/test_zsxq_browser_import_api.py`

**Interfaces:**
- Produces: `BrowserSidebarImportService.recent_download_history(kind: str, days: int = 3, now: datetime | None = None) -> dict[str, Any]`.
- Produces: `POST /api/v1/zsxq/browser-import/history`.

- [ ] **Step 1: Write failing service tests**

  Add fixtures with one legacy result and two submit manifests. Assert 43 records collapse to 30 unique records with 13 duplicates, missing date files are ignored, PDF/MP3 are isolated, Chrome `(n)` suffixes normalize, and malformed recent JSON returns `complete: false`.

- [ ] **Step 2: Run the service tests and verify RED**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_service.py -k recent_download_history`

  Expected: failures because `recent_download_history` does not exist.

- [ ] **Step 3: Implement history aggregation**

  Add an `inbox_root` property under `downloads_root / "知识星球下载" / "inbox"`, enumerate only exact dates in the requested natural-day window, parse `sidebar-result-<kind>-YYYYMMDD.json` and `sidebar-submit-<kind>-YYYYMMDD*.json`, and collapse records using:

  ```python
  file_id or (
      _strip_chrome_duplicate_suffix(filename),
      published_at or "",
      source_url or "",
  )
  ```

  Preserve first `downloaded_at`, first download count, latest observed count and source statistics. Return `complete: false` plus structured errors if an existing recent file cannot be parsed.

- [ ] **Step 4: Write failing history API tests**

  Test valid request, invalid kind, days outside 1–7, mismatched tab ID, and non-loopback access.

- [ ] **Step 5: Implement and verify the history route**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_service.py tests/unit/test_zsxq_browser_import_api.py -k 'recent_download_history or history_endpoint'`

  Expected: all history-focused tests pass.

---

### Task 2: Inline manifest validation and server persistence

**Files:**
- Modify: `src/backend/services/zsxq_browser_import_service.py`
- Modify: `src/backend/apis/v1/zsxq_browser_import.py`
- Test: `tests/unit/test_zsxq_browser_import_service.py`
- Test: `tests/unit/test_zsxq_browser_import_api.py`

**Interfaces:**
- Produces: `canonical_manifest_payload(manifest: dict) -> tuple[dict, str]`, where the string is SHA-256 of canonical JSON.
- Produces: `persist_inline_manifest(manifest: dict, payload_sha256: str) -> Path`.
- Extends: `POST /api/v1/zsxq/browser-import` with mutually exclusive `manifest` and `manifest_paths` inputs.

- [ ] **Step 1: Write failing service tests**

  Test required `run_id`, fixed kind/tab mapping, max 40 files, strict MP3/PDF thresholds, empty manifest acceptance, deterministic file path, atomic JSON persistence, same payload path reuse, and same run ID with changed payload rejection.

- [ ] **Step 2: Run service tests and verify RED**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_service.py -k inline_manifest`

  Expected: failures for missing inline-manifest methods.

- [ ] **Step 3: Implement minimal inline persistence**

  Canonicalize with `json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`, validate fixed metadata and entries, write through a sibling `.tmp` followed by `os.replace`, and produce a safe deterministic filename containing kind, sanitized run ID and the first 12 hash characters.

- [ ] **Step 4: Write failing API tests and extend request parsing**

  Assert inline submissions are previewed using the persisted path, return `manifest_path`, reject both/neither input modes, and keep existing path-mode tests unchanged.

- [ ] **Step 5: Run focused regression tests**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_service.py tests/unit/test_zsxq_browser_import_api.py -k 'inline_manifest or import_validates or preview'`

  Expected: inline and legacy path modes pass.

---

### Task 3: Durable submission receipt and API idempotency

**Files:**
- Modify: `src/backend/models/zsxq_sync.py`
- Modify: `src/backend/models/__init__.py`
- Create: `src/backend/migrations/versions/20260817_zsxq_browser_submission.py`
- Modify: `src/backend/services/zsxq_browser_import_service.py`
- Modify: `src/backend/apis/v1/zsxq_browser_import.py`
- Test: `tests/unit/test_zsxq_browser_import_service.py`
- Test: `tests/unit/test_zsxq_browser_import_api.py`

**Interfaces:**
- Produces model: `ZsxqBrowserSubmission` with unique `idempotency_key` and unique `(kind, run_id)`.
- Produces service methods: `prepare_inline_submission(...)`, `mark_submission_queued(...)`, `submission_response(...)`.
- Consumes header: `Idempotency-Key: zsxq-sidebar:<kind>:<run_id>`.

- [ ] **Step 1: Write failing model/service tests**

  Assert the first submission creates one receipt, same key/same payload returns the same manifest and task ID with `replayed: true`, same key/different payload raises `idempotency_conflict`, and concurrent unique-key conflict resolves to the existing receipt.

- [ ] **Step 2: Run tests and verify RED**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_service.py -k idempotent_submission`

  Expected: model/method import failures.

- [ ] **Step 3: Add model and migration**

  Add fields `id`, `idempotency_key`, `run_id`, `kind`, `payload_sha256`, `manifest_path`, `celery_task_id`, `status`, `attempt`, `result`, `error_message`, `created_at`, `updated_at`, `queued_at`, `completed_at`. Use status values `received`, `queued`, `processing`, `completed`, `failed`, `dispatch_failed`.

- [ ] **Step 4: Implement receipt preparation and replay**

  Derive a deterministic UUID5 Celery task ID from the idempotency key. Commit the receipt and persisted manifest before dispatch. On replay, compare hashes and return the existing status without creating another receipt or manifest.

- [ ] **Step 5: Extend API tests for idempotency**

  Mock only the Celery dispatch boundary. Assert the first request calls `apply_async(..., task_id=<stable-id>)`, the replay does not dispatch again, an already-completed receipt returns HTTP 200, pending replay returns HTTP 202, and conflict returns HTTP 409.

- [ ] **Step 6: Run focused tests**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_service.py tests/unit/test_zsxq_browser_import_api.py -k 'idempotent or inline_manifest'`

  Expected: all receipt and API replay tests pass.

---

### Task 4: Celery worker claim and terminal receipt state

**Files:**
- Modify: `src/backend/tasks/zsxq_browser_import.py`
- Modify: `src/backend/services/zsxq_browser_import_service.py`
- Test: `tests/unit/test_zsxq_browser_import_task.py`

**Interfaces:**
- Extends task input with optional `submission_id` while preserving legacy path-only calls.
- Produces: `claim_submission(submission_id, celery_task_id) -> claim result` and `complete_submission(...)`/`fail_submission(...)`.

- [ ] **Step 1: Write failing task tests**

  Assert one worker claims and imports, a duplicate broker message for a processing/completed receipt returns existing state without calling `import_manifests` or starting a workflow, success persists import/workflow result, and failure persists error state.

- [ ] **Step 2: Run tests and verify RED**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_task.py -k submission`

  Expected: failures because task claim support is absent.

- [ ] **Step 3: Implement atomic worker claim and completion**

  Lock the receipt row in a transaction. Only `received`, `queued`, `dispatch_failed` or explicitly retried `failed` may enter `processing`; `processing/completed` returns replay state. Complete the receipt only after import and workflow handoff results are available.

- [ ] **Step 4: Run task and workflow regressions**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_task.py tests/unit/test_zsxq_browser_import_workflow.py`

  Expected: idempotency tests and existing zero-file/workflow tests pass.

---

### Task 5: Update MP3/PDF sidebar skills to API-owned state

**Files:**
- Modify: `/Users/v9/.codex/skills/zsxq-mp3-sidebar-download/SKILL.md`
- Modify: `/Users/v9/.codex/skills/zsxq-pdf-sidebar-download/SKILL.md`
- Modify: `/Users/v9/.codex/skills/zsxq-pdf-sidebar-download/agents/openai.yaml`
- Modify: `/Users/v9/.codex/automations/mp3-2/automation.toml` through the automation API

**Interfaces:**
- History request: `POST http://127.0.0.1:5001/api/v1/zsxq/browser-import/history`.
- Import request: inline `manifest` plus `Idempotency-Key`.

- [ ] **Step 1: Run a failing static skill contract check**

  Assert both skills still contain direct `sidebar-result-*` reads and `manifest_paths`, and therefore fail the new API-owned-state contract.

- [ ] **Step 2: Replace local JSON instructions**

  Require history API before scanning, fail closed on unavailable/incomplete history, keep one `run_id` and idempotency key across retries, submit inline manifest even for zero files, and never read/write/list the inbox directory from the sidebar.

- [ ] **Step 3: Remove the PDF authorization code**

  Remove `ZSXQ-PDF-INBOX-AUTH-V1` because the PDF skill no longer accesses local JSON. Keep browser download and localhost API permissions only.

- [ ] **Step 4: Update the paused automation prompt**

  Keep Codex-only browser preflight; send the short skill calls without a folder authorization code. Preserve `status = PAUSED`.

- [ ] **Step 5: Validate skills and contract**

  Run both `quick_validate.py` checks and static assertions that neither skill instructs local JSON access or `manifest_paths`, while both contain history API, inline manifest, fail-closed history handling and idempotency retry rules.

---

### Task 6: Full verification and user test handoff

**Files:**
- Test only; no new production files unless a failing regression requires a scoped fix.

- [ ] **Step 1: Run focused backend suite**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_service.py tests/unit/test_zsxq_browser_import_api.py tests/unit/test_zsxq_browser_import_task.py tests/unit/test_zsxq_browser_import_workflow.py`

- [ ] **Step 2: Run existing upstream regressions**

  Run: `pytest -q tests/unit/test_zsxq_system_sync_test.py tests/unit/test_zsxq_all_tabs_auto_sync.py`

- [ ] **Step 3: Verify current real history read-only**

  Call the service or loopback history API for MP3 with `days=3`. Assert `records_seen >= 43`, `unique_downloads = 30` for the two named 2026-08-17 manifests, and `duplicates_collapsed >= 13`; do not download or move files.

- [ ] **Step 4: Verify API idempotency with an empty test manifest**

  Submit the same zero-file inline manifest twice with one idempotency key. Assert both responses return the same task ID and only one submission receipt/logic task exists. Keep downstream auto-publish disabled for this diagnostic if it uses a live server.

- [ ] **Step 5: Confirm automation remains paused and notify user**

  Report test counts, history summary, idempotency evidence, exact manual MP3/PDF prompts and that the schedule remains paused until both manual tests pass.
