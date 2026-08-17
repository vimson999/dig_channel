# Report Audio Gem V2 Structured Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the configured Gemini Gem return one validated `report_audio_gem_v2` package from a minimal per-run trigger, measure it in shadow mode, and allow a guarded direct path that skips DeepSeek normalization/title review only when the contract is valid.

**Architecture:** A pure service owns the trigger, JSON extraction, validation, repair prompt, and compatibility mapping. The report-audio orchestrator selects `off`, `shadow`, or `direct` mode; both V2 modes suppress the legacy generic Gemini review follow-up. Shadow mode records compact comparison diagnostics and preserves the current DeepSeek path. Direct mode accepts only a valid V2 package and otherwise falls back to the existing DeepSeek path. The generic Gemini browser task can perform one contract-aware repair turn in the same Gem session before returning the result.

**Tech Stack:** Python 3, Flask, SQLAlchemy, Celery, Playwright-backed Gemini browser worker, unittest/pytest.

## Global Constraints

- The Gem's persistent instructions remain the source of the long editorial prompt; per-run requests contain only document-specific values and the output contract name.
- Default to `shadow`; do not enable production-wide direct bypass during this implementation.
- Never accept malformed, incomplete, externally contaminated, or internally inconsistent JSON merely because it parses.
- A failed V2 validation must preserve the current DeepSeek normalizer and title-review behavior.
- Repair at most once and replace the candidate response; never concatenate two JSON documents.
- Do not start TTS, rendering, or publishing in the post-restart smoke test; use `script_material_only`.
- Preserve unrelated dirty-worktree changes. The user did not request commits, so this plan contains verification checkpoints rather than git commits.

## Execution status — 2026-08-12 18:16 CST

- Tasks 1–4 implemented and covered by focused tests.
- Task 5 verification: `364 passed, 41 subtests passed`; backend compilation and scoped whitespace checks passed.
- Task 5 restart/smoke is blocked: the safety check could not access the database in the sandbox, and the required read-only escalation was rejected by the current Codex usage limit. No force override, restart, or live production step was performed.

---

### Task 1: Pure V2 trigger, parser, validator, and compatibility mapper

**Files:**
- Create: `src/backend/services/report_audio_gem_v2.py`
- Create: `tests/unit/test_report_audio_gem_v2.py`

**Interfaces:**
- Produces: `resolve_gem_v2_mode(value=None) -> str` for `off|shadow|direct`, defaulting safely to `shadow`.
- Produces: `build_gem_v2_trigger(pdf_filename, minimum_script_chars, output_mode="separate", focus="", allow_external_information=False) -> str` without embedding the long Gem instructions.
- Produces: `validate_gem_v2_response(raw_text, minimum_script_chars, expected_pdf_filename, actual_pdf_page_count=None, allow_external_information=False) -> GemV2Validation`.
- Produces: `build_gem_v2_repair_prompt(errors) -> str` requesting one complete replacement JSON object.
- Produces: `to_report_audio_structured_result(validation) -> dict` preserving both current workflow fields and V2 evidence/title/QC fields.

- [ ] Write focused tests for pure JSON, fenced JSON, surrounding prose, invalid schema, short script, non-contiguous paragraph/evidence maps, unknown claim IDs, invalid selected candidate IDs, root/report mismatch, and forbidden external information.
- [ ] Write a test proving the per-run trigger is compact and does not repeat role/editorial boilerplate.
- [ ] Run the focused test file and confirm it fails because the module is absent.
- [ ] Implement only enough code to satisfy the contract tests with stable validation error codes.
- [ ] Run the focused test file and confirm it passes.

### Task 2: Report-audio runtime modes and DeepSeek bypass boundary

**Files:**
- Modify: `src/backend/tasks/report_audio_workflow.py`
- Modify: `src/backend/services/report_audio_workflow.py`
- Modify: `tests/unit/test_report_audio_workflow_task.py`
- Modify: `tests/unit/test_report_audio_workflow_service.py`

**Interfaces:**
- Shadow/direct modes send the minimal V2 trigger and an empty generic `followup_prompts` list.
- Shadow mode records V2 validation/candidate metrics, then calls the existing DeepSeek normalizer and title review.
- Direct mode skips both DeepSeek stages only for a valid `report_audio_gem_v2` response.
- Direct mode with invalid V2 output records a reason and executes the unchanged DeepSeek fallback.
- `_validate_structured_result()` preserves V2 extension fields when reading cached/direct results.

- [ ] Add failing task tests for minimal prompt/no generic review, shadow preserving both DeepSeek calls, valid direct bypassing both calls, and invalid direct falling back to both calls.
- [ ] Add a failing service test proving V2 extension fields survive workflow validation/cache reuse.
- [ ] Run the focused task/service tests and confirm each new assertion fails for the intended reason.
- [ ] Implement runtime routing and compact persisted diagnostics without duplicating the full Gem response on the parent task.
- [ ] Run the focused tests and confirm they pass.

### Task 3: One targeted repair turn in the Gemini browser task

**Files:**
- Modify: `src/backend/services/gemini_browser.py`
- Modify: `src/backend/tasks/gemini_browser.py`
- Modify: `src/backend/tasks/report_audio_workflow.py`
- Create or modify: `tests/unit/test_report_pdf_gemini_task.py`
- Modify: `tests/unit/test_gemini_profile_recovery.py` only if its existing worker fixtures require the new optional arguments.

**Interfaces:**
- The report-audio child payload declares `response_contract="report_audio_gem_v2"`, the expected filename, and minimum script length.
- The Gemini worker validates the first response, sends exactly one targeted repair prompt when invalid, validates the replacement, and exposes compact contract diagnostics.
- Other Gemini workflows remain unchanged when `response_contract` is absent.

- [ ] Add failing tests proving valid output gets no repair, invalid output gets exactly one repair, the repaired response replaces the original, and a second invalid response is returned for orchestrator fallback without another turn.
- [ ] Run the focused Gemini tests and confirm failure on the missing contract hook.
- [ ] Implement the optional hook with no behavior change for callers that omit it.
- [ ] Run the focused Gemini and report-audio task tests and confirm they pass.

### Task 4: API/runtime configuration and operator traceability

**Files:**
- Modify: `src/backend/api/report_audio_workflow.py`
- Modify: `tests/unit/test_report_audio_workflow_api.py`
- Modify: `docs/prompts/report-audio-gem-v2.txt` only if a contract mismatch is discovered.
- Create: `docs/planning/2026-08-12-report-audio-gem-v2-rollout.md`

**Interfaces:**
- Accepts/persists `gem_v2_mode` with only `off|shadow|direct` values; runtime environment `REPORT_AUDIO_GEM_V2_MODE` supplies the default.
- V2 requests do not receive the legacy 1,500-character prompt suffix because script length is a structured validation field.
- The rollout record defines comparison metrics, fallback signals, direct-cutover criteria, and rollback (`off`).

- [ ] Add failing API tests for accepted/rejected modes and V2 prompt persistence behavior while preserving the legacy `off` contract.
- [ ] Run the API tests and confirm the new cases fail for the expected reason.
- [ ] Implement request persistence and write the rollout/runbook document.
- [ ] Run API and prompt-contract tests and confirm they pass.

### Task 5: Regression, safe restart, and loaded-code smoke test

**Files:**
- Verify all files above.
- Update: `/Users/v9/.codex/automations/automation-2/memory.md` after verification.

- [ ] Run all focused V2, report-audio workflow, Gemini task, API, and profile-recovery unit tests.
- [ ] Run Python compilation for modified backend modules and `git diff --check`.
- [ ] Run the project's restart safety check; stop if it reports non-interruptible active work.
- [ ] Run `./scripts/dev/restart-dev-stack.sh` only after the safety gate passes.
- [ ] Verify backend health, relevant Celery worker processes/queues, and that a fresh worker process imports the new V2 module/default mode.
- [ ] Submit one explicitly authorized `script_material_only` shadow smoke job against the existing canary PDF, or perform a read-only module/API smoke if duplicate orchestration policy prevents safe submission; do not invoke TTS, video rendering, or publishing.
- [ ] Record exact tests, restart evidence, smoke result, limitations, and direct-cutover status in the rollout document and automation memory.
