# Knowledge Planet Sidebar Deduplication and Manifest Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent historical and same-run duplicate Knowledge Planet downloads, reject inconsistent browser manifests before idempotency reservation, and make MP3/PDF sidebar runs safe enough for bounded manual and scheduled acceptance.

**Architecture:** Keep the backend history endpoint authoritative. Canonicalize browser-history identities in the import service, filter new deterministic manifests through durable submission receipts, validate manifest counters/names/content before reserving the run ID, and make each sidebar skill bind candidate → modal → landed file before including a download. Scheduled automation remains paused until explicit acceptance gates pass.

**Tech Stack:** Python 3, Flask/SQLAlchemy, unittest/pytest, Markdown-based Codex skills, local HTML sidebar bridge.

## Global constraints

- Preserve unrelated dirty worktree files and stage only files named below.
- Use RED → GREEN → regression for each backend behavior.
- Keep current production skill settings at 48 hours / 40 unique topic groups / 40 maximum downloads during repair and first manual acceptance.
- Keep MP3 threshold strictly greater than 10 and PDF threshold strictly greater than 30.
- Keep the recent-history window at three days.
- Do not resume automation `mp3-2` during implementation.
- Do not delete user downloads as part of recovery.

---

### Task 1: Canonical browser-history identity

**Files:**
- Modify: `src/backend/services/zsxq_browser_import_service.py`
- Test: `tests/unit/test_zsxq_browser_import_service.py`

**Interfaces:**
- Produce: `_normalize_browser_filename(filename: str) -> str`
- Produce: `_canonical_browser_timestamp(value: str | None) -> str`
- Modify: `_history_identity(record: dict) -> tuple`
- Extend history records with `normalized_filename`, `published_at_canonical`, and `dedupe_key`.

- [x] Add a failing test proving timestamps with and without seconds collapse into one history record.
- [x] Add a failing test proving source URL variants do not split the same fixed-tab file.
- [x] Add a failing test proving Chrome `(1)` suffixes normalize to the base filename.
- [x] Add a failing test proving the same recent normalized filename remains downloaded even when published-time metadata differs.
- [x] Run the focused tests and record RED failures.
- [x] Implement minimal Unicode filename and ISO timestamp canonicalization.
- [x] Run the focused tests and verify GREEN.

### Task 2: Trust only accepted deterministic inline manifests

**Files:**
- Modify: `src/backend/services/zsxq_browser_import_service.py`
- Modify: `src/backend/apis/v1/zsxq_browser_import.py` if route wiring is required.
- Test: `tests/unit/test_zsxq_browser_import_service.py`
- Test: `tests/unit/test_zsxq_browser_import_api.py`

**Interfaces:**
- Modify: `recent_download_history(..., require_submission_receipt: bool = False)`.
- Add summary field: `ignored_untrusted_manifests`.
- Consume: `ZsxqBrowserSubmission.manifest_path`.

- [x] Add a failing service test where a 12-hex deterministic manifest has no receipt and is ignored.
- [x] Add a failing service test where the same manifest has a receipt and is included.
- [x] Add/adjust an API test proving the history route enables receipt enforcement.
- [x] Run focused tests and record RED failures.
- [x] Implement receipt filtering while retaining legacy no-hash history files.
- [x] Fail closed when receipt enforcement cannot query durable state.
- [x] Run focused tests and verify GREEN.

### Task 3: Reject internally inconsistent manifests

**Files:**
- Modify: `src/backend/services/zsxq_browser_import_service.py`
- Test: `tests/unit/test_zsxq_browser_import_service.py`

**Interfaces:**
- Validate `scan_summary.new_download_success == len(files)` when supplied.
- Reject duplicate normalized actual basenames.
- Reject preview `duplicate_content > 0` before submission reservation.

- [x] Add failing tests for counter mismatch, duplicate normalized basename, and duplicate file content.
- [x] Run the focused tests and record RED failures.
- [x] Implement validation before `ZsxqBrowserSubmission` reservation.
- [x] Prove definite validation failures leave no durable run-ID receipt.
- [x] Run focused tests and verify GREEN.

### Task 4: Harden MP3 and PDF sidebar skills

**Files:**
- Modify: `plugins/zsxq-sidebar-sync/skills/zsxq-mp3-sidebar-download/SKILL.md`
- Modify: `plugins/zsxq-sidebar-sync/skills/zsxq-pdf-sidebar-download/SKILL.md`
- Mirror after verification: `/Users/v9/.codex/skills/zsxq-mp3-sidebar-download/SKILL.md`
- Mirror after verification: `/Users/v9/.codex/skills/zsxq-pdf-sidebar-download/SKILL.md`

- [x] Add explicit candidate, modal, and actual-file binding rules to both skills.
- [x] Require exactly one in-flight download and bind its completion event before processing another candidate.
- [x] Add a two-second same-modal reread for filename mismatch/missing data.
- [x] Add same-run candidate and actual-basename sets with terminal safety counters.
- [x] Require final report counters to derive from the frozen manifest and submit response.
- [x] Keep 48/40/40 settings unchanged.
- [x] Pressure-test the stale-modal, Chrome `(1)`, timestamp variant, source URL variant, and invalid-manifest scenarios against both skill texts.
- [x] Mirror project skills to installed skill paths and verify byte-for-byte equality.

### Task 5: Regression, service restart, and failed-run recovery

**Files:**
- Test: `tests/unit/test_zsxq_browser_import_service.py`
- Test: `tests/unit/test_zsxq_browser_import_api.py`
- Test: other existing ZSXQ sidebar bridge suites selected by impact.

- [x] Run all browser-import service/API tests.
- [x] Run relevant frontend bridge tests.
- [x] Review diff for unrelated files and accidental production-setting changes.
- [x] Restart backend and confirm health.
- [x] Confirm the malformed 2026-08-18 deterministic manifest is ignored by trusted history.
- [x] Build a recovery manifest containing only the six confirmed new unique MP3 files.
- [x] Submit recovery once, record task ID, and verify downstream dispatch is queued.
- [x] Commit and push only relevant files.

### Task 6: Manual acceptance handoff

- [ ] Notify the user that implementation is ready for manual acceptance.
- [ ] Provide one exact MP3 prompt and one exact PDF prompt.
- [ ] Require evidence: scan count, new downloads, duplicate counters, manifest path, task ID, replayed state, and downstream queue status.
- [ ] Keep automation paused while waiting for the user's verdict.

### Task 7: Bounded operational test after acceptance

**Files:**
- Temporarily modify and mirror both MP3/PDF skill files above.

- [ ] Only after explicit manual acceptance, change both skills to 24-hour window / 10 unique topic groups / maximum 5 new downloads.
- [ ] Verify both installed and project copies and ask the user to run the bounded test.
- [ ] If the bounded test succeeds, restore both skills to 48 hours / 40 topic groups / maximum 40 downloads.
- [ ] Verify restored values and byte-for-byte skill copies.
- [ ] Re-enable/update automation `mp3-2` only after the user confirms the bounded test succeeded.
- [ ] Observe the first scheduled run before declaring operational acceptance.
