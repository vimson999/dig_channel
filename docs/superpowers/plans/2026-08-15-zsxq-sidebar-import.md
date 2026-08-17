# Knowledge Planet Sidebar Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Chrome 侧边栏下载 manifest 安全、幂等地接入现有知识星球入库和内容流水线。

**Architecture:** 新增一个纯 Python manifest 规范化/校验服务，新增两个本机 API 路由，并新增 Celery 入库任务。规范化服务只负责形成现有模型所需的 canonical records；每个 manifest 进入一个标准 `ZsxqWorkflowRun`，清洗和发布继续调用现有 `ZsxqSyncService.submit_*_clean_for_run`，节点邮件复用原有工作流通知机制。

**Tech Stack:** Flask、SQLAlchemy、Celery、pytest、SHA-256。

## Global Constraints

- 不修改既有知识星球 API 上游 `run_zsxq_sync`。
- manifest 中缺失的下载次数保持 `null`，不能伪造为 0。
- manifest 必须位于 `/Users/v9/Downloads`；首次导入的文件来自 Downloads，重复导入允许读取项目归档目录中的 `stored_path`。
- 正式导入将文件移动到 `data/zsxq/<group_id>/<tab_label>-<tab_id>/files/`，数据库路径同步指向归档位置。
- preview 不写数据库、不触发 Celery。
- 保留工作区中与本任务无关的既有修改，不覆盖或回滚它们。

---

### Task 1: Define manifest normalization and validation contract

**Files:**
- Create: `src/backend/services/zsxq_browser_import_service.py`
- Test: `tests/unit/test_zsxq_browser_import_service.py`

**Interfaces:**
- Consumes: a manifest path or parsed manifest and the fixed PDF/MP3 tab mapping.
- Produces: `BrowserImportPreview`-compatible dictionaries containing normalized files, SHA-256, stable IDs, duplicate state, and warnings.

- [ ] **Step 1: Write failing tests**

  Add tests for array manifests, object manifests with `files`, Downloads path containment, extension validation, threshold warnings, Chrome suffix normalization, stable IDs, and duplicate content detection.

- [ ] **Step 2: Run the focused test and verify it fails**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_service.py`

  Expected: import/attribute failures because the new service does not exist.

- [ ] **Step 3: Implement the minimal pure normalization service**

  Define `load_manifest(path)`, `preview_manifest(path, *, downloads_root=...)`, `normalize_manifest_entry(entry, *, batch_key, tab_config)`, and `sha256_file(path)`. Use deterministic `browser-topic-` and `browser-file-` IDs and never use a null download count as zero.

- [ ] **Step 4: Run the focused test and verify it passes**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_service.py`

  Expected: all focused tests pass.

### Task 2: Add database upsert and dry-run report

**Files:**
- Modify: `src/backend/services/zsxq_browser_import_service.py`
- Test: `tests/unit/test_zsxq_browser_import_service.py`

**Interfaces:**
- Consumes: normalized file records from Task 1 and SQLAlchemy models.
- Produces: `preview_import_manifests(paths)` and `import_manifests(paths, *, dispatch_clean=False)`.

- [ ] **Step 1: Write failing tests**

  Add tests with an isolated SQLAlchemy session or the repository test app verifying preview is write-free, new topics/attachments/run-download rows are created, repeated imports reuse canonical records, and different filenames with the same SHA-256 are duplicates.

- [ ] **Step 2: Run tests to verify the new assertions fail**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_service.py -k 'preview or import or duplicate'`

  Expected: failures for missing preview/import behavior.

- [ ] **Step 3: Implement transactional upsert**

  Create one `ZsxqSyncRun(mode="browser_sidebar")` per manifest, upsert `ZsxqTopic` and `ZsxqAttachment` using the stable synthetic keys, add `ZsxqSyncRunDownload`, store original records plus `sha256` in `raw_payload`, and set run summary fields. Commit only after all records for the manifest validate.

- [ ] **Step 4: Run focused tests**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_service.py`

  Expected: all focused tests pass and preview leaves row counts unchanged.

### Task 3: Expose loopback preview and enqueue API

**Files:**
- Create: `src/backend/apis/v1/zsxq_browser_import.py`
- Modify: `src/backend/apis/v1/__init__.py`
- Test: `tests/unit/test_zsxq_browser_import_api.py`

**Interfaces:**
- `POST /api/v1/zsxq/browser-import/preview` with `{ "manifest_paths": ["..."] }`.
- `POST /api/v1/zsxq/browser-import` with `{ "manifest_paths": ["..."], "dispatch_clean": false }`.

- [ ] **Step 1: Write failing API tests**

  Test valid preview, missing manifest path, non-loopback request, and enqueue response shape. Mock only the Celery dispatch boundary; exercise real request validation and service calls.

- [ ] **Step 2: Run API tests to verify failure**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_api.py`

  Expected: route-not-found or missing blueprint failures.

- [ ] **Step 3: Implement the blueprint**

  Register the blueprint under `/api/v1/zsxq`; enforce loopback remote address and return structured validation errors. Preview calls the service directly; import calls the Celery task after validating the manifest paths.

- [ ] **Step 4: Run API tests**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_api.py`

  Expected: all API tests pass.

### Task 4: Add Celery task and downstream workflow handoff

**Files:**
- Create: `src/backend/tasks/zsxq_browser_import.py`
- Modify: `src/backend/celery_config.py` only if task discovery requires it
- Test: `tests/unit/test_zsxq_browser_import_task.py`

**Interfaces:**
- Celery task name: `tasks.zsxq_browser_import.run_browser_import`.
- Input: manifest paths plus `dispatch_clean` and cleaner configuration.
- Output: one result per manifest with run ID, imported/reused counts, warnings, workflow ID, and clean task IDs.

- [ ] **Step 1: Write failing task tests**

  Verify the task imports within an app context, returns idempotent results on repeated input, and when `dispatch_clean` is true creates a standard workflow for each manifest.

- [ ] **Step 2: Run task tests to verify failure**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_task.py`

  Expected: task import/registration failures.

- [ ] **Step 3: Implement task**

  Create the app context, call `import_manifests`, and start the existing workflow state machine for each manifest. The workflow submits the existing clean entrypoint, advances to the existing publish stage, and emits the standard milestone/terminal notifications. Persist handoff errors in the run result without deleting downloaded records.

- [ ] **Step 4: Run task tests**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_task.py`

  Expected: all task tests pass.

### Task 5: Validate the current real manifests without writing the database

**Files:**
- No source changes.
- Test artifact: `/Users/v9/Downloads/知识星球下载/inbox/sidebar-result-pdf-20260815.json`
- Test artifact: `/Users/v9/Downloads/知识星球下载/inbox/sidebar-result-mp3-20260815.json`

- [ ] **Step 1: Run all new focused tests**

  Run: `pytest -q tests/unit/test_zsxq_browser_import_service.py tests/unit/test_zsxq_browser_import_api.py tests/unit/test_zsxq_browser_import_task.py`

- [ ] **Step 2: Invoke preview against the two current manifests**

  Use the local API or the service preview entrypoint with both manifest paths. Confirm 94 PDF and 46 MP3 entries, zero missing files, zero extension mismatches, and explicit warnings for unknown download counts and known duplicate hashes.

- [ ] **Step 3: Run the relevant existing regression tests**

  Run: `pytest -q tests/unit/test_zsxq_system_sync_test.py tests/unit/test_zsxq_all_tabs_auto_sync.py`

- [ ] **Step 4: Only after preview evidence, submit the actual import task**

  Use the new API with the same two manifest paths and `dispatch_clean=false`; report the created run IDs and imported/reused counts. Do not enable downstream cleaning until the canonical rows are verified.
