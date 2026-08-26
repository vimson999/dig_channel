# Content Pipeline Canonical Cleaned Assets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make successfully cleaned MP3 and PDF outputs the only selectable, previewable, and newly orchestrated media references, so removing original Downloads files does not empty the workbench lists.

**Architecture:** Extend the existing shared `content_pipeline_assets` dedupe ledger with one verified cleaned-output record per canonical source asset. A new resolver/registry service owns output registration, availability checks, reference resolution, and historical backfill; all user-facing MP3/PDF consumers use it instead of checking raw source paths. The clean tasks register each successful output independently and pass only registered files to auto-publish, preserving partial-batch progress.

**Tech Stack:** Flask, SQLAlchemy, Alembic, Celery, Python `pathlib`/SHA-256, existing content-pipeline cleaners, unittest.

**Spec:** `docs/superpowers/specs/2026-08-25-content-pipeline-canonical-cleaned-assets-design.md`

## Global Constraints

- Keep `ZsxqAttachment.local_path`, `ContentPipelineAsset.source_path`, and already-created task snapshots immutable as source/audit records.
- A `cleaned` asset must never silently fall back to `source_path`; unavailable cleaned output produces a repairable error.
- Only files inside `CONTENT_PIPELINE_OUTPUT_ROOT` (default `runtime/content_pipeline/batches`) and with the expected media extension may be registered as canonical outputs.
- One failed file must not abort a batch that has at least one successfully registered output.
- Auto-publish must receive only successfully registered output paths, never the entire output directory without a whitelist.
- Preserve the user’s unrelated worktree changes, including `artifacts/` and current MP3 detail-page changes.

---

### Task 1: Persist and resolve canonical cleaned artifacts

**Files:**
- Create: `src/backend/migrations/versions/20260826_content_pipeline_canonical_cleaned_assets.py`
- Modify: `src/backend/models/content_pipeline_asset.py`
- Create: `src/backend/services/content_pipeline/canonical_asset_service.py`
- Create: `tests/unit/test_content_pipeline_canonical_asset_service.py`

**Interfaces:**
- Consumes: `ContentPipelineAsset(id, media_type, canonical_task_id, source_path, status)` and cleaner result entries containing source and output paths.
- Produces: `resolve_cleaned_asset(asset, *, media_type) -> CanonicalAssetResolution`, `resolve_asset_reference(reference, *, media_type, legacy_source_path=None) -> CanonicalAssetResolution`, and `register_cleaned_outputs(task_id, *, media_type, cleaned_files, source_root=None) -> CleanedOutputRegistration`.
- Produces: `CanonicalAssetResolution.path: Path | None`, `.state: str`, `.reason: str | None`, `.asset: ContentPipelineAsset | None`; `CleanedOutputRegistration.registered_paths: list[str]`, `.failed_asset_ids: list[str]`, `.skipped: list[dict]`.

- [ ] **Step 1: Write the failing canonical-output registry tests**

```python
def test_registers_verified_cleaned_output_and_resolves_after_raw_is_deleted(self):
    raw = self._write("source.mp3", b"raw")
    cleaned = self._write_output("batch/audio_cleaned/source.mp3", b"cleaned")
    asset = self._asset(media_type="audio", source_path=raw, status="cleaning")

    result = register_cleaned_outputs(
        asset.canonical_task_id,
        media_type="audio",
        cleaned_files=[{"source_path": str(raw), "output_path": str(cleaned)}],
    )
    raw.unlink()

    resolved = resolve_cleaned_asset(asset, media_type="audio")
    self.assertEqual(result.registered_paths, [str(cleaned.resolve())])
    self.assertEqual(resolved.state, "ready")
    self.assertEqual(resolved.path, cleaned.resolve())
```

Add cases for an output outside the configured content-pipeline root, a missing/zero-byte output, a wrong extension, `reserved`/`cleaning` assets, a `cleaned` asset whose output later disappears, and idempotent repeated registration.

- [ ] **Step 2: Run the new registry tests to verify they fail**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_content_pipeline_canonical_asset_service -v`

Expected: FAIL because `canonical_asset_service` and cleaned-output fields do not exist.

- [ ] **Step 3: Add the model columns and Alembic migration**

Add nullable model columns and include them in `to_dict()`:

```python
cleaned_path = db.Column(db.String(600), nullable=True)
cleaned_sha256 = db.Column(db.String(64), nullable=True)
cleaned_bytes = db.Column(db.BigInteger, nullable=True)
cleaned_at = db.Column(db.DateTime, nullable=True)
```

Create migration revision `20260826_content_pipeline_canonical_cleaned_assets` with `down_revision = "20260825_zsxq_history_api_clients"`; `upgrade()` adds precisely these four columns and `downgrade()` removes them in reverse order. Do not run filesystem backfill inside Alembic.

- [ ] **Step 4: Implement the isolated resolver and registry service**

Implement the public service surface with deterministic path checks:

```python
def resolve_cleaned_asset(asset, *, media_type: str) -> CanonicalAssetResolution:
    if asset is None or asset.media_type != media_type:
        return CanonicalAssetResolution(None, "missing", "asset_not_found", asset)
    if asset.status != "cleaned":
        return CanonicalAssetResolution(None, "processing", "clean_not_completed", asset)
    path = _validated_cleaned_path(asset.cleaned_path, media_type)
    if path is None:
        return CanonicalAssetResolution(None, "unavailable", "cleaned_output_missing_or_invalid", asset)
    return CanonicalAssetResolution(path, "ready", None, asset)
```

`register_cleaned_outputs()` must normalize each `source_path` and output path, match only rows owned by `canonical_task_id`, fingerprint each valid output, fill the four new fields, mark only successful rows `cleaned`, mark owned rows represented by failed/invalid cleaner entries `failed`, and commit once. It must not infer an asset from filename alone.

- [ ] **Step 5: Run the registry tests to verify they pass**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_content_pipeline_canonical_asset_service tests.unit.test_content_pipeline_dedupe -v`

Expected: PASS; existing SHA-256 de-duplication behavior remains unchanged.

- [ ] **Step 6: Commit the persistence and resolver layer**

```bash
git add src/backend/migrations/versions/20260826_content_pipeline_canonical_cleaned_assets.py \
  src/backend/models/content_pipeline_asset.py \
  src/backend/services/content_pipeline/canonical_asset_service.py \
  tests/unit/test_content_pipeline_canonical_asset_service.py
git commit -m "feat: persist canonical cleaned media assets"
```

### Task 2: Register per-file cleaner results without batch-wide aborts

**Files:**
- Modify: `src/backend/services/content_pipeline/pdf_cleaner_adapter.py`
- Modify: `src/backend/tasks/content_pipeline.py`
- Modify: `tests/unit/test_content_pipeline_progress_updates.py`
- Create: `tests/unit/test_content_pipeline_partial_clean_registration.py`

**Interfaces:**
- Consumes: `register_cleaned_outputs()` from Task 1 and current PDF/audio cleaner result dictionaries.
- Produces: task result `cleaned_files` filtered to registered paths, accurate `success_count`/`failed_count`, and asset rows in `cleaned` or `failed` status.

- [ ] **Step 1: Write failing partial-success task tests**

```python
def test_audio_task_completes_and_publishes_only_registered_outputs(self):
    task_id = self._task_with_two_audio_sources()
    with patch.object(content_pipeline, "AudioCleanerAdapter", _OneSuccessOneFailureAdapter), \
         patch.object(content_pipeline, "_enqueue_auto_publish_task") as enqueue:
        self.assertTrue(content_pipeline._run_audio_clean_task_impl_guarded(task_id))

    task = db.session.get(PublicationTask, task_id)
    self.assertEqual(task.status, "completed")
    self.assertEqual(task.result["success_count"], 1)
    self.assertEqual(task.result["failed_count"], 1)
    self.assertEqual(len(task.result["cleaned_files"]), 1)
    self.assertEqual(enqueue.call_args.kwargs["registered_output_paths"], [self.cleaned_one])
```

Add the corresponding PDF case, plus a case where zero outputs register and the task becomes `failed` with no publish handoff.

- [ ] **Step 2: Run the task tests to verify they fail**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_content_pipeline_progress_updates tests.unit.test_content_pipeline_partial_clean_registration -v`

Expected: FAIL because current tasks raise whenever `failed_count > 0` and bulk-mark every asset `cleaned`.

- [ ] **Step 3: Normalize PDF source paths at the cleaner boundary**

For every local PDF cleaner result item, preserve existing fields and add the exact source path:

```python
item = {
    "source_path": str(source),
    "source": rel_path.as_posix(),
    "cleaner_output_path": str(target),
    "path": str(target),
    # existing status/message/output/relative_path fields
}
```

Apply this to successful, skipped, and failed entries so the worker never relies on a filename or relative-path guess. Preserve the audio adapter’s existing `source_path`/`output_path` fields.

- [ ] **Step 4: Replace batch status promotion with per-file registration**

In both `_run_pdf_clean_task_impl_guarded()` and `_run_audio_clean_task_impl_guarded()`:

```python
registration = register_cleaned_outputs(
    task_key,
    media_type="pdf",  # or "audio"
    cleaned_files=result.get("files") or result.get("cleaned_files") or [],
    source_root=source_root,
)
merged_result = _result_with_registered_outputs(result, registration)
if not registration.registered_paths:
    raise RuntimeError("清洗完成但没有可用的规范清洗产物")
```

Remove the `failed_count > 0` abort condition. Keep original per-file failures in `files`, set aggregate counts from the registration result, and call auto-publish only with `registration.registered_paths`. Preserve the exception path that marks all still-owned assets `failed` only when the whole task cannot complete.

- [ ] **Step 5: Run the task tests to verify they pass**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_content_pipeline_progress_updates tests.unit.test_content_pipeline_partial_clean_registration tests.unit.test_content_pipeline_pdf_local_cleaner -v`

Expected: PASS; a one-success/one-failure batch ends `completed`, and a zero-success batch ends `failed`.

- [ ] **Step 6: Commit per-file clean registration**

```bash
git add src/backend/services/content_pipeline/pdf_cleaner_adapter.py \
  src/backend/tasks/content_pipeline.py \
  tests/unit/test_content_pipeline_progress_updates.py \
  tests/unit/test_content_pipeline_partial_clean_registration.py
git commit -m "fix: keep successful media when a clean batch partially fails"
```

### Task 3: Whitelist registered outputs for auto-publish

**Files:**
- Modify: `src/backend/tasks/content_pipeline.py`
- Modify: `src/backend/services/content_pipeline/auto_publish_handoff.py`
- Modify: `src/backend/services/zsxq_auto_publish_service.py`
- Modify: `tests/unit/test_zsxq_auto_publish_service.py`
- Create: `tests/unit/test_content_pipeline_auto_publish_whitelist.py`

**Interfaces:**
- Consumes: `registered_output_paths: list[str]` from Task 2.
- Produces: `submit_publish_clean_output_task(..., approved_paths=None)` and a transient auto-publish rule whose iterator yields only approved normalized paths when the list is supplied.

- [ ] **Step 1: Write failing publish-whitelist tests**

```python
def test_transient_audio_rule_ignores_unregistered_file_in_same_output_directory(self):
    approved = self._write("audio_cleaned/approved.mp3", b"ok")
    rejected = self._write("audio_cleaned/rejected.mp3", b"not registered")
    config = _build_transient_publish_config(
        file_type="audio",
        source_dir=approved.parent,
        options={"approved_paths": [str(approved)]},
    )
    rule = next(rule for rule in config["rules"] if rule["enabled"])
    self.assertEqual(list(_iter_rule_files(rule)), [approved])
```

Add a compatibility case where `approved_paths` is omitted and all normal eligible files remain visible.

- [ ] **Step 2: Run the whitelist tests to verify they fail**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_content_pipeline_auto_publish_whitelist tests.unit.test_zsxq_auto_publish_service -v`

Expected: FAIL because the current rule iterator recursively emits every matching file under `source_dir`.

- [ ] **Step 3: Add the approved-path contract to the handoff and iterator**

Extend handoff functions without changing the default behavior:

```python
def submit_publish_clean_output_task(..., approved_paths=None, ...):
    options = {**options, "approved_paths": list(approved_paths or [])}

def _iter_rule_files(rule):
    approved = _normalized_approved_paths(rule.get("approved_paths"), base)
    for candidate in iterator:
        if approved is not None and candidate.resolve() not in approved:
            continue
        yield candidate
```

Reject approved paths that are outside `source_dir`, missing, non-files, or use an unapproved extension. Pass the Task 2 registered list through `_enqueue_auto_publish_task()` to `submit_publish_clean_output_task()`.

- [ ] **Step 4: Run the whitelist tests to verify they pass**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_content_pipeline_auto_publish_whitelist tests.unit.test_zsxq_auto_publish_service -v`

Expected: PASS; no unregistered cleaner output can be selected for publish.

- [ ] **Step 5: Commit the publish whitelist**

```bash
git add src/backend/tasks/content_pipeline.py \
  src/backend/services/content_pipeline/auto_publish_handoff.py \
  src/backend/services/zsxq_auto_publish_service.py \
  tests/unit/test_content_pipeline_auto_publish_whitelist.py \
  tests/unit/test_zsxq_auto_publish_service.py
git commit -m "fix: publish only registered cleaned outputs"
```

### Task 4: Provide an idempotent cleaned-output backfill command

**Files:**
- Create: `scripts/maintenance/backfill_content_pipeline_cleaned_assets.py`
- Modify: `src/backend/services/content_pipeline/canonical_asset_service.py`
- Create: `tests/unit/test_content_pipeline_cleaned_asset_backfill.py`
- Modify: `scripts/maintenance/README.md`

**Interfaces:**
- Consumes: completed `content_pdf_clean`/`content_audio_clean` task JSON and the Task 1 registry service.
- Produces: `backfill_task_cleaned_outputs(task, *, dry_run=False) -> dict` and a CLI with `--dry-run`, `--task-id`, `--limit`, and `--media-type` switches.

- [ ] **Step 1: Write failing backfill tests**

```python
def test_backfill_registers_existing_audio_output_and_is_idempotent(self):
    task, asset, output = self._completed_audio_task_with_output()
    first = backfill_task_cleaned_outputs(task, dry_run=False)
    second = backfill_task_cleaned_outputs(task, dry_run=False)

    self.assertEqual(first["registered_count"], 1)
    self.assertEqual(second["registered_count"], 0)
    self.assertEqual(second["already_registered_count"], 1)
    self.assertEqual(asset.cleaned_path, str(output.resolve()))
```

Add an old PDF-result case where `source` is relative and only one source-root match exists, and an ambiguous/missing-output case that reports `skipped` without modifying the asset.

- [ ] **Step 2: Run the backfill tests to verify they fail**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_content_pipeline_cleaned_asset_backfill -v`

Expected: FAIL because no historical result scanner exists.

- [ ] **Step 3: Implement the backfill service and thin maintenance CLI**

Use the registry’s same validation/matching rules. The script must create an app context, select only completed clean tasks with bounded order/limit, and print JSON summary:

```bash
PYTHONPATH=src/backend venv/bin/python \
  scripts/maintenance/backfill_content_pipeline_cleaned_assets.py --dry-run --limit 200
```

For each task, use exact `source_path` first; for legacy PDF results resolve the relative `source` against the saved `source_root` only if it matches exactly one task asset. Never invent an asset hash or overwrite a different existing `cleaned_path`.

- [ ] **Step 4: Run the backfill tests to verify they pass**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_content_pipeline_cleaned_asset_backfill tests.unit.test_content_pipeline_canonical_asset_service -v`

Expected: PASS; repeated runs leave canonical fields stable.

- [ ] **Step 5: Commit the backfill tooling**

```bash
git add scripts/maintenance/backfill_content_pipeline_cleaned_assets.py \
  scripts/maintenance/README.md \
  src/backend/services/content_pipeline/canonical_asset_service.py \
  tests/unit/test_content_pipeline_cleaned_asset_backfill.py
git commit -m "feat: backfill canonical cleaned media outputs"
```

### Task 5: Switch the MP3 workbench to canonical cleaned audio

**Files:**
- Modify: `src/backend/services/mp3_video_workflow.py`
- Modify: `src/frontend/mp3-video-workflow.html`
- Modify: `src/frontend/js/mp3-video-workflow.js`
- Modify: `tests/unit/test_mp3_video_audio_asset_merge.py`
- Create: `tests/frontend/mp3_canonical_cleaned_asset_contract.js`

**Interfaces:**
- Consumes: Task 1 resolver and ready `ContentPipelineAsset` rows.
- Produces: MP3 list items and metadata with stable `attachment_id = "pipeline:<asset-id>"`, `path` set to the cleaned file, and `storage_state = "ready"`.

- [ ] **Step 1: Write failing MP3 source-deletion tests**

```python
def test_list_and_metadata_use_cleaned_audio_after_download_source_is_deleted(self):
    raw, cleaned, asset = self._cleaned_audio_asset()
    raw.unlink()

    payload = list_audio_assets(page=1, per_page=30, scope="all")
    item = next(item for item in payload["items"] if item["asset_id"] == asset.id)
    self.assertEqual(item["attachment_id"], f"pipeline:{asset.id}")
    self.assertEqual(item["path"], str(cleaned))
    self.assertEqual(audio_meta_from_asset_reference(item["attachment_id"])["path"], str(cleaned))
```

Add the `cleaned_path` missing case (not selectable, `skipped_unavailable` increases) and an old ZSXQ attachment ID compatibility case that maps to its matching canonical asset rather than using `attachment.local_path`.

- [ ] **Step 2: Run MP3 tests to verify they fail**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_mp3_video_audio_asset_merge -v`

Expected: FAIL because the current payload resolves `source_path`/`local_path` directly.

- [ ] **Step 3: Make ready pipeline assets the MP3 catalog and resolver source**

Replace raw attachment scanning as the selectable source with ready canonical audio assets. Keep source metadata enrichment optional and non-blocking. Implement legacy attachment-ID handling as:

```python
attachment = _load_audio_attachment(asset_id)
resolution = resolve_asset_reference(
    asset_id,
    media_type="audio",
    legacy_source_path=attachment.local_path,
)
```

When `resolution.state != "ready"`, raise `ValueError("音频清洗产物缺失，请修复或重新投递")`; never probe the original path.

- [ ] **Step 4: Update MP3 workbench copy and source label rendering**

Change the page’s “已同步 MP3” copy to “已清洗 MP3”. Render `asset.source_label` for any `content_pipeline` item, append “已清洗”, and keep local upload behavior unchanged. The frontend contract test must assert the page no longer promises raw synchronized files and the JS uses `source_label` instead of hardcoding “手动投递”.

- [ ] **Step 5: Run MP3 tests to verify they pass**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_mp3_video_audio_asset_merge -v && node tests/frontend/mp3_canonical_cleaned_asset_contract.js`

Expected: PASS; deleting a raw source does not remove its cleaned MP3 from the catalog.

- [ ] **Step 6: Commit MP3 canonical resolution**

```bash
git add src/backend/services/mp3_video_workflow.py \
  src/frontend/mp3-video-workflow.html \
  src/frontend/js/mp3-video-workflow.js \
  tests/unit/test_mp3_video_audio_asset_merge.py \
  tests/frontend/mp3_canonical_cleaned_asset_contract.js
git commit -m "feat: use cleaned audio assets in mp3 workbench"
```

### Task 6: Switch all PDF workbench catalog, preview, and job creation paths

**Files:**
- Modify: `src/backend/api/report_audio_workflow.py`
- Modify: `src/backend/api/report_script_material_workflow.py`
- Modify: `src/backend/api/report_top10_workflow.py`
- Modify: `src/backend/services/report_top10_workflow.py`
- Modify: `src/frontend/report-audio-workflow-test.html`
- Modify: `src/frontend/js/report-audio-workflow-test.js`
- Modify: `src/frontend/report-script-material-workflow-test.html`
- Modify: `src/frontend/js/report-script-material-workflow-test.js`
- Modify: `src/frontend/report-top10-workflow-test.html`
- Modify: `src/frontend/js/report-top10-workflow-test.js`
- Modify: `src/frontend/report-stick-figure-video-workflow.html`
- Modify: `src/frontend/js/report-stick-figure-video-workflow.js`
- Modify: `src/frontend/report-visual-plan-test.html`
- Modify: `src/frontend/js/report-visual-plan-test.js`
- Create: `tests/unit/test_pdf_canonical_asset_consumers.py`
- Modify: `tests/unit/test_report_pdf_asset_merge.py`

**Interfaces:**
- Consumes: Task 1 resolver and canonical PDF assets.
- Produces: every `/pdf-assets` consumer list and preview/job resolver accepts `pipeline:<asset-id>` and returns/uses only `cleaned_path`.

- [ ] **Step 1: Write failing cross-consumer PDF tests**

```python
def test_every_pdf_consumer_resolves_pipeline_reference_after_raw_source_deletion(self):
    raw, cleaned, asset = self._cleaned_pdf_asset()
    raw.unlink()
    reference = f"pipeline:{asset.id}"

    self.assertEqual(report_audio_api._pdf_meta_from_asset_reference(reference, None)["path"], str(cleaned))
    self.assertEqual(report_script_api._pdf_meta_from_asset_reference(reference)["path"], str(cleaned))
    self.assertEqual(report_top10_service.pdf_meta_from_asset_reference(reference)["path"], str(cleaned))
```

Add list tests asserting a ready manual, sidebar, and scheduled asset each appears once; an unavailable output appears in `skipped_unavailable` but is not selectable; and an attachment-ID compatibility lookup does not use the raw file.

- [ ] **Step 2: Run PDF consumer tests to verify they fail**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_report_pdf_asset_merge tests.unit.test_pdf_canonical_asset_consumers -v`

Expected: FAIL because the three APIs still build their catalogs from `ZsxqAttachment.local_path` and only one API has partial pipeline support.

- [ ] **Step 3: Use shared canonical PDF resolution in all four consumer modules**

Add a local, thin payload adapter in each API/service that calls Task 1 rather than duplicating path validation. Replace the selectable raw attachment catalog with ready pipeline assets; preserve topic/download metadata only as optional enrichment. Update preview endpoints and job creation to call the `pipeline:<asset-id>` resolver first, then use the old attachment ID solely to locate a canonical asset by immutable source path.

Retire `legacy:<task-id>:<index>` from new selectable lists. If a caller supplies a legacy reference without a canonical asset, return `"尚无可用清洗产物，请重新投递"` rather than reopening its raw source.

- [ ] **Step 4: Update PDF workbench copy and status badge**

Change visible “已同步 PDF” wording to “已清洗 PDF” in the report-audio, script-material, Top10, stick-figure-video, and visual-plan pages; show the existing source label plus “已清洗” for `content_pipeline` entries. Those last two pages already call the script-material PDF endpoint, so their page copy and empty-state copy must change in the same commit. Keep explicit local upload controls separate: an uploaded file creates a new clean submission rather than being mistaken for a ready asset.

- [ ] **Step 5: Run PDF consumer tests to verify they pass**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_report_pdf_asset_merge tests.unit.test_pdf_canonical_asset_consumers tests.unit.test_report_audio_workflow_api -v`

Expected: PASS; every displayed/selectable PDF resolves to a validated clean output.

- [ ] **Step 6: Commit PDF canonical resolution**

```bash
git add src/backend/api/report_audio_workflow.py \
  src/backend/api/report_script_material_workflow.py \
  src/backend/api/report_top10_workflow.py \
  src/backend/services/report_top10_workflow.py \
  src/frontend/report-audio-workflow-test.html \
  src/frontend/js/report-audio-workflow-test.js \
  src/frontend/report-script-material-workflow-test.html \
  src/frontend/js/report-script-material-workflow-test.js \
  src/frontend/report-top10-workflow-test.html \
  src/frontend/js/report-top10-workflow-test.js \
  src/frontend/report-stick-figure-video-workflow.html \
  src/frontend/js/report-stick-figure-video-workflow.js \
  src/frontend/report-visual-plan-test.html \
  src/frontend/js/report-visual-plan-test.js \
  tests/unit/test_pdf_canonical_asset_consumers.py \
  tests/unit/test_report_pdf_asset_merge.py
git commit -m "feat: use cleaned pdf assets across report workbenches"
```

### Task 7: Document, migrate, backfill, and verify the live workflow

**Files:**
- Modify: `docs/PROJECT_CONTEXT_MAP.md`
- Modify: `docs/README.md`
- Modify: `docs/WORKFLOW_EMAIL_NOTIFICATIONS.md`
- Modify: `docs/superpowers/specs/2026-08-25-content-pipeline-canonical-cleaned-assets-design.md`
- Create: `tests/unit/test_content_pipeline_canonical_asset_docs.py`

**Interfaces:**
- Consumes: schema migration, backfill CLI, Tasks 1–6 tests.
- Produces: an operator-facing documented migration sequence and live verification evidence.

- [ ] **Step 1: Write the documentation assertions before editing docs**

Add a lightweight static test or extend an existing documentation contract to require these operator commands and rules:

```text
alembic upgrade head
PYTHONPATH=src/backend venv/bin/python scripts/maintenance/backfill_content_pipeline_cleaned_assets.py --dry-run --limit 200
PYTHONPATH=src/backend venv/bin/python scripts/maintenance/backfill_content_pipeline_cleaned_assets.py --limit 200
```

The documentation must state that original download files remain required until their clean task reaches `completed`, and may be removed after a ready canonical output is verified.

- [ ] **Step 2: Run the documentation assertion to verify it fails**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_content_pipeline_canonical_asset_docs -v`

Expected: FAIL because the canonical cleaned-asset operational steps are not documented yet.

- [ ] **Step 3: Update project and operations documentation**

Document the canonical data flow, the no-raw-fallback rule, partial batch behavior, output retention responsibility, backfill operation, and the three upstream modes that now converge on the same cleaned asset ledger. Update the design document status from `待实施` to `已实施` only after all verifications below succeed.

- [ ] **Step 4: Run migrations and the historical backfill safely**

Run the dry run first, inspect its registered/skipped counts, then execute the same command without `--dry-run`. Verify the running 2026-08-26 manual MP3 task and recent completed PDF/audio tasks retain correct source audits while their new canonical fields point only to valid runtime outputs.

- [ ] **Step 5: Run full targeted verification**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_content_pipeline_canonical_asset_service \
  tests.unit.test_content_pipeline_partial_clean_registration \
  tests.unit.test_content_pipeline_cleaned_asset_backfill \
  tests.unit.test_content_pipeline_dedupe \
  tests.unit.test_content_pipeline_progress_updates \
  tests.unit.test_content_pipeline_auto_publish_whitelist \
  tests.unit.test_mp3_video_audio_asset_merge \
  tests.unit.test_report_pdf_asset_merge \
  tests.unit.test_pdf_canonical_asset_consumers \
  tests.unit.test_content_pipeline_canonical_asset_docs -v
node tests/frontend/mp3_canonical_cleaned_asset_contract.js
```

Expected: all pass. Then manually verify a cleaned MP3 and PDF remain listed, previewable, and selectable after moving only their raw source files out of Downloads; do not delete any file during verification.

- [ ] **Step 6: Commit final docs and verification contract**

```bash
git add docs/PROJECT_CONTEXT_MAP.md docs/README.md docs/WORKFLOW_EMAIL_NOTIFICATIONS.md \
  docs/superpowers/specs/2026-08-25-content-pipeline-canonical-cleaned-assets-design.md \
  tests/unit/test_content_pipeline_canonical_asset_docs.py
git commit -m "docs: document canonical cleaned asset operations"
```
