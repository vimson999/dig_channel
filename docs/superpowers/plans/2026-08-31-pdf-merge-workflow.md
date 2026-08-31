# PDF 合并工作流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-class, asynchronous PDF merge workbench that accepts ordered mixed local uploads and cleaned system PDF assets, then retains the merged output as a reusable canonical PDF asset.

**Architecture:** A new `pdf_merge_workflow` parent `PublicationTask` owns the frozen source snapshot, worker lifecycle, and per-user history. A pure service validates and merges PDF files with PyMuPDF; the worker registers the verified output through the existing `ContentPipelineAsset` ledger. The standalone frontend workbench reuses the current PDF asset catalogue and shared navigation while calling a small dedicated Flask API.

**Tech Stack:** Flask, SQLAlchemy, Celery, PyMuPDF (`fitz`), existing `PublicationTask`, `ContentPipelineAsset`, Tailwind CSS, vanilla JavaScript, Python `unittest`/pytest, Node static-contract tests.

**Spec:** `docs/superpowers/specs/2026-08-31-pdf-merge-workflow-design.md`

## Global Constraints

- Feature name is exactly `pdf_merge_workflow`; queue is exactly `content_pipeline`.
- Input order is immutable after `POST /jobs`; duplicate entries are intentionally preserved.
- Sources can only be task-owned uploads or ready `pipeline:<asset-id>` PDF assets.
- Initial limits are 2–30 PDFs, 100 MiB each, 500 MiB total; environment variables may lower or raise them within safe hard bounds.
- Every output must be reopened, page-count checked, atomically finalized, content-hashed, and registered as `ContentPipelineAsset(media_type="pdf", source_mode="pdf_merge", status="cleaned")`.
- Public responses and frontend rendering must never expose absolute paths or place untrusted filenames in `innerHTML`.
- No schema migration or external PDF-tool service dependency.

---

### Task 1: Pure PDF merge contracts and real-file engine

**Files:**
- Create: `src/backend/services/pdf_merge_workflow.py`
- Create: `tests/unit/test_pdf_merge_workflow_service.py`

**Interfaces:**
- Consumes: ordered source dictionaries containing `sequence`, `kind`, `filename`, `path`, `sha256`, and `bytes`.
- Produces: `PdfMergeError`, `normalize_output_filename(value)`, `fingerprint_file(path)`, `validate_pdf_source(source)`, `workflow_upload_root(task_id)`, `workflow_output_root(task_id)`, `merge_pdf_sources(sources, output_path, progress_callback=None)`.

- [ ] **Step 1: Write the failing tests for filenames, frozen-source fingerprints, and page order.**

```python
def test_merge_pdf_sources_preserves_the_frozen_source_order(self):
    first = self._pdf("first.pdf", "FIRST")
    second = self._pdf("second.pdf", "SECOND")
    result = merge_pdf_sources([self._source(first, 1), self._source(second, 2)], self.output)
    self.assertEqual(result.page_count, 2)
    with fitz.open(result.path) as merged:
        self.assertIn("FIRST", merged[0].get_text())
        self.assertIn("SECOND", merged[1].get_text())

def test_validate_pdf_source_rejects_changed_fingerprint(self):
    source = self._source(self._pdf("source.pdf", "old"), 1)
    Path(source["path"]).write_bytes(b"changed")
    with self.assertRaisesRegex(PdfMergeError, "文件内容已变化"):
        validate_pdf_source(source)
```

- [ ] **Step 2: Run the focused tests and confirm they fail because the service does not exist.**

Run: `PYTHONPATH=src/backend venv/bin/python -m pytest tests/unit/test_pdf_merge_workflow_service.py -q`

Expected: import failure for `services.pdf_merge_workflow`.

- [ ] **Step 3: Implement the smallest pure service.**

```python
def merge_pdf_sources(sources, output_path, progress_callback=None):
    output = fitz.open()
    expected_pages = 0
    for position, source in enumerate(sources, start=1):
        path, page_count = validate_pdf_source(source)
        with fitz.open(path) as document:
            output.insert_pdf(document)
        expected_pages += page_count
        if progress_callback:
            progress_callback(position, len(sources), expected_pages)
    temp_path = output_path.with_suffix(".part.pdf")
    output.save(temp_path)
    output.close()
    with fitz.open(temp_path) as verification:
        if verification.page_count != expected_pages:
            raise PdfMergeError("合并结果页数校验失败")
    temp_path.replace(output_path)
    return PdfMergeResult(path=output_path, page_count=expected_pages, bytes=output_path.stat().st_size)
```

`validate_pdf_source` must require a safe PDF extension, nonzero size, matching SHA-256 and bytes when supplied, successful `fitz.open`, and `not document.needs_pass`. `normalize_output_filename` must remove unsafe path components, add `.pdf`, and use a timestamped fallback.

- [ ] **Step 4: Run the focused tests and confirm they pass.**

Run: `PYTHONPATH=src/backend venv/bin/python -m pytest tests/unit/test_pdf_merge_workflow_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the tested service.**

```bash
git add src/backend/services/pdf_merge_workflow.py tests/unit/test_pdf_merge_workflow_service.py
git commit -m "feat: add pdf merge service"
```

### Task 2: Celery lifecycle and canonical output registration

**Files:**
- Create: `src/backend/tasks/pdf_merge_workflow.py`
- Create: `tests/unit/test_pdf_merge_workflow_tasks.py`
- Modify: `src/backend/celery_config.py`
- Modify: `src/backend/services/runtime_guard.py`
- Modify: `src/backend/api/report_audio_workflow.py`

**Interfaces:**
- Consumes: a persisted `PublicationTask` with `params["pdf_merge_workflow"]["sources"]`.
- Produces: Celery task `tasks.pdf_merge_workflow.process_job`, terminal result keys `output`, `asset_ref`, `asset_id`, `reused_asset`, `logs`, and canonical asset records.

- [ ] **Step 1: Write failing task tests using real two-page PDFs and an in-memory database.**

```python
def test_process_job_registers_a_ready_pdf_merge_asset(self):
    task = self._pending_task([self._upload_source("FIRST"), self._pipeline_source("SECOND")])
    self.assertTrue(process_job.run(task.id))
    stored = db.session.get(PublicationTask, task.id)
    asset = db.session.get(ContentPipelineAsset, stored.result["output"]["asset_id"])
    self.assertEqual(stored.status, "completed")
    self.assertEqual(asset.source_mode, "pdf_merge")
    self.assertEqual(asset.status, "cleaned")

def test_process_job_reuses_an_existing_canonical_output(self):
    task = self._pending_task([self._upload_source("FIRST"), self._upload_source("SECOND")])
    existing = self._existing_asset_with_same_merged_bytes(task)
    process_job.run(task.id)
    self.assertEqual(self._task(task).result["output"]["asset_id"], existing.id)
    self.assertTrue(self._task(task).result["output"]["reused_asset"])
```

- [ ] **Step 2: Run the focused task tests and confirm they fail because the task module does not exist.**

Run: `PYTHONPATH=src/backend venv/bin/python -m pytest tests/unit/test_pdf_merge_workflow_tasks.py -q`

Expected: import failure for `tasks.pdf_merge_workflow`.

- [ ] **Step 3: Implement task state updates, registration, routing, and source label.**

```python
@celery_app.task(bind=True, name="tasks.pdf_merge_workflow.process_job")
def process_job(self, task_id):
    app = _create_app()
    with app.app_context():
        guard_publication_task_start(self, task_id, feature=FEATURE_NAME, queue="content_pipeline")
        _mark_processing(task_id, "validating", 5)
        result = merge_pdf_sources(_resolved_sources(task_id), output_path, _progress)
        asset_result = _register_or_reuse_output(task_id, result.path)
        _mark_completed(task_id, result, asset_result)
        return True
```

Use `reserve_files` then `register_cleaned_outputs` for new output hashes. On an existing ready canonical hash, use the duplicate entry's canonical asset ID, delete the generated transient result, and expose the existing `cleaned_path` through the task result. Add the module to the Celery `include` list and route, and add `pdf_merge_workflow` admission limits. Extend the existing PDF asset source-label map with `pdf_merge: "PDF 合并"`.

- [ ] **Step 4: Run task and existing canonical asset consumer tests.**

Run: `PYTHONPATH=src/backend venv/bin/python -m pytest tests/unit/test_pdf_merge_workflow_tasks.py tests/unit/test_pdf_canonical_asset_consumers.py tests/unit/test_report_pdf_asset_merge.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the worker integration.**

```bash
git add src/backend/tasks/pdf_merge_workflow.py src/backend/celery_config.py src/backend/services/runtime_guard.py src/backend/api/report_audio_workflow.py tests/unit/test_pdf_merge_workflow_tasks.py
git commit -m "feat: register merged pdf workflow assets"
```

### Task 3: Authenticated API, snapshots, retry, cancel, and safe download

**Files:**
- Create: `src/backend/api/pdf_merge_workflow.py`
- Create: `tests/unit/test_pdf_merge_workflow_api.py`
- Modify: `src/backend/apis/__init__.py`

**Interfaces:**
- Consumes: `multipart/form-data` with `files[]` and an `items` JSON array where uploads use `{"kind":"upload","upload_index":0}` and assets use `{"kind":"pipeline_asset","asset_ref":"pipeline:<id>"}`.
- Produces: `/api/pdf-merge-workflows/jobs` CRUD-style routes with owner-scoped public payloads.

- [ ] **Step 1: Write failing API tests for mixed-order snapshot creation, ownership, retry, and no-path payloads.**

```python
def test_create_job_freezes_interleaved_upload_and_pipeline_asset_order(self):
    response = self.client.post(
        "/api/pdf-merge-workflows/jobs",
        data={"items": json.dumps([{"kind": "pipeline_asset", "asset_ref": self.asset_ref}, {"kind": "upload", "upload_index": 0}]), "files": (io.BytesIO(self.pdf_bytes), "local.pdf")},
        content_type="multipart/form-data",
    )
    sources = response.get_json()["data"]["sources"]
    self.assertEqual([item["kind"] for item in sources], ["pipeline_asset", "upload"])
    self.assertNotIn(str(self.temp_dir), json.dumps(response.get_json()))

def test_other_user_cannot_read_or_download_a_merge_job(self):
    self._login_as("owner")
    job_id = self._create_job()["job_id"]
    self._login_as("other")
    self.assertEqual(self.client.get(f"/api/pdf-merge-workflows/jobs/{job_id}").status_code, 404)
    self.assertEqual(self.client.get(f"/api/pdf-merge-workflows/jobs/{job_id}/download").status_code, 404)
```

- [ ] **Step 2: Run API tests and confirm they fail because the blueprint does not exist.**

Run: `PYTHONPATH=src/backend venv/bin/python -m pytest tests/unit/test_pdf_merge_workflow_api.py -q`

Expected: import failure for `api.pdf_merge_workflow`.

- [ ] **Step 3: Implement the smallest API and registration.**

```python
pdf_merge_workflow_bp = Blueprint("pdf_merge_workflow", __name__, url_prefix="/api/pdf-merge-workflows")

@pdf_merge_workflow_bp.route("/jobs", methods=["POST"])
@require_login
def create_job(current_user):
    items = _parse_items(request.form.get("items"))
    parent = _create_pending_parent(current_user, items, request.files.getlist("files"))
    _enqueue(parent)
    return jsonify({"success": True, "job_id": parent.id, "data": _workflow_payload(parent)})
```

Save upload bytes only under `workflow_upload_root(parent.id)` after validating count, size, content, and the exact `upload_index`. Resolve asset refs only through `ContentPipelineAsset` and `resolve_cleaned_asset`; snapshot its current hash and byte size. `retry` accepts only failed/canceled tasks and calls the same guarded enqueue; `cancel` accepts only pending tasks; `download` uses the resolved canonical output and `send_file` with a safe user-visible name. Register the blueprint in `register_all_apis`.

- [ ] **Step 4: Run the API and submission regression suite.**

Run: `PYTHONPATH=src/backend venv/bin/python -m pytest tests/unit/test_pdf_merge_workflow_api.py tests/unit/test_content_pipeline_submission_query_discipline.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the API.**

```bash
git add src/backend/api/pdf_merge_workflow.py src/backend/apis/__init__.py tests/unit/test_pdf_merge_workflow_api.py
git commit -m "feat: add pdf merge workflow api"
```

### Task 4: Workbench, PDF tools entry, and shared navigation

**Files:**
- Create: `src/frontend/pdf-merge-workflow.html`
- Create: `src/frontend/js/pdf-merge-workflow.js`
- Create: `tests/frontend/pdf_merge_workflow_contract.js`
- Modify: `src/frontend/pdf-tools.html`
- Modify: `src/frontend/js/shared-navigation.js`

**Interfaces:**
- Consumes: `/api/report-audio-workflows/pdf-assets` catalogue and `/api/pdf-merge-workflows/jobs` API payloads.
- Produces: ordered client `items` array, a `FormData` request with JSON `items`, polling and history rendering with safe DOM construction.

- [ ] **Step 1: Write a static frontend contract test before page implementation.**

```javascript
assert(html.includes('id="merge-files-input"'));
assert(script.includes('/api/report-audio-workflows/pdf-assets'));
assert(script.includes('/api/pdf-merge-workflows/jobs'));
assert(script.includes('FormData'));
assert(script.includes('pipeline_asset_id'));
assert(script.includes('textContent'));
assert(!script.includes('innerHTML = item.filename'));
```

- [ ] **Step 2: Run the contract and confirm it fails because the workbench files do not exist.**

Run: `node tests/frontend/pdf_merge_workflow_contract.js`

Expected: file-read failure for `pdf-merge-workflow.html`.

- [ ] **Step 3: Implement the workbench and entry points.**

```javascript
const form = new FormData();
const payloadItems = this.items.map((item, uploadIndex) => item.kind === 'upload'
  ? {kind: 'upload', upload_index: uploadIndex}
  : {kind: 'pipeline_asset', asset_ref: item.assetRef});
this.items.filter((item) => item.kind === 'upload').forEach((item) => form.append('files', item.file, item.file.name));
form.append('items', JSON.stringify(payloadItems));
```

Use `document.createElement` and `textContent` for every dynamic list row. Disable submit below two entries; show source badge, sequence, size, removal button, API error, current progress, result download, retry, cancel, and recent jobs. Use `pipeline_asset_id` to convert catalogue rows to canonical `pipeline:` refs. Add the landing card and navigation title mapping for `pdf-merge-workflow.html`.

- [ ] **Step 4: Run the frontend contract and current PDF tools static checks.**

Run: `node tests/frontend/pdf_merge_workflow_contract.js && node tests/frontend/pdf_canonical_cleaned_asset_contract.js`

Expected: both scripts print `ok` and exit 0.

- [ ] **Step 5: Commit the frontend.**

```bash
git add src/frontend/pdf-merge-workflow.html src/frontend/js/pdf-merge-workflow.js src/frontend/pdf-tools.html src/frontend/js/shared-navigation.js tests/frontend/pdf_merge_workflow_contract.js
git commit -m "feat: add pdf merge workbench"
```

### Task 5: End-to-end acceptance and documentation verification

**Files:**
- Modify: `tests/unit/test_pdf_merge_workflow_tasks.py`
- Modify: `tests/unit/test_pdf_merge_workflow_api.py`
- Modify: `docs/superpowers/specs/2026-08-31-pdf-merge-workflow-design.md` only if implementation forces a user-visible contract correction

**Interfaces:**
- Consumes: real PyMuPDF fixtures, task service, Flask test client, and canonical asset resolver.
- Produces: automated proof that a mixed-source task produces a downloadable reusable asset in exactly the requested order.

- [ ] **Step 1: Add a failing combined acceptance test.**

```python
def test_mixed_source_job_merges_in_order_and_is_reusable_by_existing_pdf_consumers(self):
    job = self._submit_asset_then_upload_job(asset_text="ASSET", upload_text="UPLOAD")
    self.assertTrue(process_job.run(job["job_id"]))
    result = self._get_job(job["job_id"])
    self.assertEqual(result["output"]["page_count"], 2)
    merged_path = self._download_to_temp(job["job_id"])
    with fitz.open(merged_path) as document:
        self.assertIn("ASSET", document[0].get_text())
        self.assertIn("UPLOAD", document[1].get_text())
    self.assertTrue(result["output"]["asset_ref"].startswith("pipeline:"))
    self.assertEqual(report_audio_api._pdf_meta_from_asset_reference(result["output"]["asset_ref"], None)["path"], str(merged_path))
```

- [ ] **Step 2: Run the acceptance test and confirm it fails only if a required integration is still missing.**

Run: `PYTHONPATH=src/backend venv/bin/python -m pytest tests/unit/test_pdf_merge_workflow_api.py tests/unit/test_pdf_merge_workflow_tasks.py -q`

Expected: PASS after Tasks 1–4; otherwise repair the smallest missing behavior before proceeding.

- [ ] **Step 3: Run all relevant backend, frontend, and static quality checks.**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m pytest \
  tests/unit/test_pdf_merge_workflow_service.py \
  tests/unit/test_pdf_merge_workflow_tasks.py \
  tests/unit/test_pdf_merge_workflow_api.py \
  tests/unit/test_content_pipeline_canonical_asset_service.py \
  tests/unit/test_content_pipeline_dedupe.py \
  tests/unit/test_pdf_canonical_asset_consumers.py \
  tests/unit/test_report_pdf_asset_merge.py -q
node tests/frontend/pdf_merge_workflow_contract.js
node tests/frontend/pdf_canonical_cleaned_asset_contract.js
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 4: Inspect the final diff against the spec and commit any last acceptance-test adjustment.**

```bash
git diff --check
git status --short
git add tests/unit/test_pdf_merge_workflow_api.py tests/unit/test_pdf_merge_workflow_tasks.py
git commit -m "test: cover pdf merge end to end"
```

Only create this final commit if the acceptance test changed after earlier commits; otherwise leave the working tree clean and report the existing commits.
