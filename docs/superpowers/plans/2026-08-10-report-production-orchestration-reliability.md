# Report Production Orchestration Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make “full landscape + scheme-3 vertical” report production complete without operator intervention by fixing passive-wait capacity accounting, waking parents on child completion, enforcing output contracts, and exposing actionable status.

**Architecture:** Incrementally harden the existing `PublicationTask`/Celery orchestration. Persist only bounded projections for hot-path status, use compare-and-set tokens for idempotent continuation events, retain delayed retries as recovery, and centralize artifact readiness in a pure completion-contract service.

**Tech Stack:** Python 3, Flask, SQLAlchemy, Celery, PostgreSQL/SQLite test database, unittest/pytest, vanilla JavaScript, FFprobe.

**Implementation status (2026-08-10):** Tasks 1–5 are code-complete. Deterministic verification passed with 1,137 unit tests and the 32-test recovery/duplicate-prevention group. Runtime status remains `CODE_MODIFIED` until a fresh three-PDF batch satisfies the acceptance boundary in the linked bug record.

## Global Constraints

- Preserve the existing `PublicationTask`, Celery queues, structured projection columns, and historical audit JSON.
- Do not rewrite all workflows or introduce new external infrastructure in this implementation cycle.
- Do not change Bilibili or other publishing permission boundaries; publishing remains explicitly opt-in.
- Reuse valid script, WAV, storyboard, image, and video artifacts even when a downstream parent status is `failed`.
- Retries must not create duplicate active children for the same role and input identity.
- Parent `completed` requires every configured landscape/vertical output to pass the completion contract.
- Lists and polling endpoints must not add full `params`/`result` N+1 queries.

---

### Task 1: Passive report-audio waits do not consume execution capacity

**Files:**
- Modify: `src/backend/services/runtime_guard.py`
- Modify: `tests/unit/test_runtime_guard.py`

**Interfaces:**
- Consumes: `PublicationTask.current_step` after `tasks.report_audio_workflow._current_step_code()` compression.
- Produces: `check_publication_task_admission(feature, mode="start")` excludes persisted `gem_*`, `doubao_*`, and `pub_*` child-wait steps from `active_count`.

- [ ] **Step 1: Write the failing compressed-step contract test**

  Replace the existing passive-step sample with both internal and persisted forms:

  ```python
  def test_report_audio_waiting_children_do_not_consume_start_capacity(self):
      passive_steps = (
          "gemini_waiting",
          "gem_waiting",
          "doubao_browser_1_waiting",
          "doubao_1_waiting",
          "scheme3_visual_waiti",
          "publisher_video_waiting",
          "pub_queued",
          "pub_waiting",
          "pub_v_synthesis",
      )
      for step in passive_steps:
          self._add_task("processing", step=step)

      start = runtime_guard.check_publication_task_admission(
          "report_pdf_audio_workflow",
          mode="start",
      )

      self.assertTrue(start["allowed"])
      self.assertEqual(start["active_count"], 0)
  ```

- [ ] **Step 2: Run the focused test red**

  Run:

  ```bash
  venv/bin/python -m unittest tests.unit.test_runtime_guard.RuntimeGuardTest.test_report_audio_waiting_children_do_not_consume_start_capacity -v
  ```

  Expected: FAIL because `pub_*`, `gem_*`, and `doubao_*` are not all excluded by `PASSIVE_RUNNING_STEP_PREFIXES`.

- [ ] **Step 3: Add persisted step prefixes**

  Extend both report-audio aliases without removing the readable internal prefixes:

  ```python
  "report_audio_workflow": (
      "gemini_", "gem_",
      "doubao_browser_", "doubao_",
      "scheme3_visual_",
      "publisher_video_", "pub_",
  ),
  "report_pdf_audio_workflow": (
      "gemini_", "gem_",
      "doubao_browser_", "doubao_",
      "scheme3_visual_",
      "publisher_video_", "pub_",
  ),
  ```

- [ ] **Step 4: Run runtime-guard regressions**

  Run:

  ```bash
  venv/bin/python -m unittest tests.unit.test_runtime_guard -v
  ```

  Expected: all tests pass; a control task at `review_cover_titles` still produces `active_count == 1`.

- [ ] **Step 5: Commit the isolated guardrail**

  ```bash
  git add src/backend/services/runtime_guard.py tests/unit/test_runtime_guard.py
  git commit -m "fix: exclude compressed report waits from capacity"
  ```

### Task 2: Publisher terminal events wake report-audio parents exactly once

**Files:**
- Create: `src/backend/services/report_audio_parent_wakeup.py`
- Create: `tests/unit/test_report_audio_parent_wakeup.py`
- Modify: `src/backend/tasks/publisher.py`

**Interfaces:**
- Consumes: `wake_report_audio_parent_for_child(child_task_id: str) -> dict` with a terminal Publisher child ID.
- Produces: one immediate `tasks.report_audio_workflow.process_job` delivery for the matching active parent; result keys `woken`, `reason`, `parent_task_id`, and `celery_task_id`.

- [ ] **Step 1: Write the terminal-event tests**

  Build a parent with `waiting_child_label="publisher_video"`, `waiting_child_id=child.id`, and a stable `waiting_child_updated_at` token. Cover completed and failed children, plus duplicate delivery:

  ```python
  first = wake_report_audio_parent_for_child(child.id)
  second = wake_report_audio_parent_for_child(child.id)

  self.assertTrue(first["woken"])
  self.assertFalse(second["woken"])
  self.assertEqual(second["reason"], "already_woken")
  mocked_apply.assert_called_once()
  ```

  Add negative cases for a non-terminal child, unrelated child ID, completed parent, and canceled parent.

- [ ] **Step 2: Run the new module red**

  Run:

  ```bash
  venv/bin/python -m unittest tests.unit.test_report_audio_parent_wakeup -v
  ```

  Expected: import failure because the wake-up service does not exist.

- [ ] **Step 3: Implement compare-and-set claim and preassigned broker ID**

  Implement the service with a bounded projection query and `uuid4()` broker ID. Claim only when all values still match:

  ```python
  updated = PublicationTask.query.filter(
      PublicationTask.id == parent_id,
      PublicationTask.feature.in_(REPORT_AUDIO_FEATURES),
      PublicationTask.status.in_(ACTIVE_STATUSES),
      PublicationTask.waiting_child_id == child_id,
      PublicationTask.waiting_child_updated_at == expected_token,
  ).update({
      PublicationTask.celery_task_id: broker_task_id,
      PublicationTask.current_step: "pub_resume_queued",
      PublicationTask.waiting_child_status: child_status,
      PublicationTask.waiting_child_step: child_step,
      PublicationTask.waiting_child_updated_at: wake_token,
      PublicationTask.updated_at: datetime.utcnow(),
  }, synchronize_session=False)
  ```

  Commit the claim before publishing. Call `maybe_guarded_apply_async(..., broker_task_id=broker_task_id)` so the task entry accepts the immediate delivery and rejects the older delayed delivery. If publication fails, compare-and-set the old broker ID and wait token back so the existing delayed retry remains valid.

- [ ] **Step 4: Wire Publisher success and failure terminal paths**

  After Publisher persists `completed` or `failed`, call the wake-up service in the same guarded continuation block used for report-audio finalization:

  ```python
  from services.report_audio_parent_wakeup import wake_report_audio_parent_for_child

  wake_report_audio_parent_for_child(task_id)
  ```

  A wake-up exception is logged and rolled back but never changes the already persisted Publisher terminal status.

- [ ] **Step 5: Run wake-up, Publisher, and report-audio regressions**

  Run:

  ```bash
  venv/bin/python -m unittest \
    tests.unit.test_report_audio_parent_wakeup \
    tests.unit.test_report_audio_workflow_task \
    tests.unit.test_publisher_db_retry -v
  ```

  Expected: all tests pass; duplicate Publisher terminal events dispatch one parent continuation.

- [ ] **Step 6: Commit the Publisher continuation**

  ```bash
  git add src/backend/services/report_audio_parent_wakeup.py tests/unit/test_report_audio_parent_wakeup.py src/backend/tasks/publisher.py
  git commit -m "fix: wake report parents on publisher completion"
  ```

### Task 3: Shared landscape and vertical completion contract

**Files:**
- Create: `src/backend/services/report_audio_completion_contract.py`
- Create: `tests/unit/test_report_audio_completion_contract.py`
- Modify: `src/backend/services/report_audio_finalization.py`
- Modify: `src/backend/tasks/report_audio_workflow.py`

**Interfaces:**
- Produces: `evaluate_report_video_completion(*, include_landscape: bool, vertical_schemes: list[str], landscape_path: str, vertical_paths: dict[str, str], audio_path: str = "", probe: Callable[[str], dict] | None = None) -> dict`.
- Result: `{"ready": bool, "outputs": {name: {"ready": bool, "path": str, "reason": str | None}}, "missing": list[str]}`.

- [ ] **Step 1: Write path and media-probe contract tests**

  Cover valid combined output, missing landscape, missing scheme 3, zero-byte file, probe without video/audio stream, and duration mismatch:

  ```python
  result = evaluate_report_video_completion(
      include_landscape=True,
      vertical_schemes=["scheme3"],
      landscape_path=str(landscape),
      vertical_paths={"scheme3": str(vertical)},
      audio_path=str(wav),
      probe=fake_probe,
  )
  self.assertTrue(result["ready"])
  self.assertEqual(result["missing"], [])
  ```

- [ ] **Step 2: Run the contract tests red**

  Run:

  ```bash
  venv/bin/python -m unittest tests.unit.test_report_audio_completion_contract -v
  ```

  Expected: import failure because the shared contract service does not exist.

- [ ] **Step 3: Implement bounded file and media validation**

  Normalize schemes to `scheme1|scheme2|scheme3`, reject missing/empty files before invoking FFprobe, and accept an injected probe for unit tests. The production probe returns duration plus audio/video stream counts and uses a finite subprocess timeout.

- [ ] **Step 4: Replace duplicated path-only checks**

  Change `report_audio_finalization._output_paths_ready()` and the final `report_audio_workflow` completion write to call the shared evaluator. Persist only the bounded summary under `result["completion_contract"]`; keep full Publisher audit payloads unchanged.

- [ ] **Step 5: Run finalization and workflow regressions**

  Run:

  ```bash
  venv/bin/python -m unittest \
    tests.unit.test_report_audio_completion_contract \
    tests.unit.test_report_audio_finalization \
    tests.unit.test_report_audio_workflow_task -v
  ```

  Expected: missing configured output prevents the parent/finalization from being marked complete and returns a specific missing key.

- [ ] **Step 6: Commit the completion contract**

  ```bash
  git add src/backend/services/report_audio_completion_contract.py tests/unit/test_report_audio_completion_contract.py src/backend/services/report_audio_finalization.py src/backend/tasks/report_audio_workflow.py
  git commit -m "feat: enforce report video completion contract"
  ```

### Task 4: Actionable orchestration status in the detail API and workbench

**Files:**
- Modify: `src/backend/api/report_audio_workflow.py`
- Modify: `src/frontend/js/report-audio-workflow-detail.js`
- Modify: `tests/unit/test_report_audio_workflow_api.py`
- Modify: `tests/unit/test_report_audio_workflow_frontend_contract.py`

**Interfaces:**
- Produces API field `orchestration` with bounded `stages`, `waiting`, `capacity`, and `completion_contract` summaries.
- Frontend consumes only those summaries for the stage bar and recovery copy; raw `params/result` remain available only in explicit detail/raw views.

- [ ] **Step 1: Write API response tests**

  Assert a capacity-deferred task reports `active_count`, `limit`, `countdown`, and a calculated `next_retry_at`; a Publisher wait reports child ID/status/step; a completed task reports both required outputs ready.

- [ ] **Step 2: Run API tests red**

  Run:

  ```bash
  venv/bin/python -m unittest tests.unit.test_report_audio_workflow_api -v
  ```

  Expected: `orchestration` is absent.

- [ ] **Step 3: Add bounded orchestration projection**

  Build `orchestration` from existing projection columns, `runtime_guard`, `waiting_child`, visual summaries, and the new completion-contract summary. Do not issue per-item queries inside `_job_payload`; detailed list pages must continue using batch finalization loading.

- [ ] **Step 4: Write and implement frontend contract**

  Add a seven-stage row: `输入 / 文案 / 音频 / 视觉 / 成片 / 验收 / 发布`. Render `已复用`, `等待`, and `需要处理` distinctly. When `capacity` exists, show `当前活动数/上限` and next retry time. Keep publishing controls disabled unless the existing explicit switches authorize them.

- [ ] **Step 5: Run API and frontend contracts**

  Run:

  ```bash
  venv/bin/python -m unittest \
    tests.unit.test_report_audio_workflow_api \
    tests.unit.test_report_audio_workflow_frontend_contract -v
  ```

  Expected: response compatibility remains intact and no Bilibili control becomes enabled by orchestration state alone.

- [ ] **Step 6: Commit the workbench status**

  ```bash
  git add src/backend/api/report_audio_workflow.py src/frontend/js/report-audio-workflow-detail.js tests/unit/test_report_audio_workflow_api.py tests/unit/test_report_audio_workflow_frontend_contract.py
  git commit -m "feat: expose actionable report orchestration status"
  ```

### Task 5: End-to-end recovery and duplicate-prevention contracts

**Files:**
- Create: `tests/integration/test_report_audio_full_video_recovery.py`
- Modify: `tests/unit/test_report_audio_workflow_scheme3_dependency.py`
- Modify: `tests/unit/test_report_stick_figure_report_audio_retry.py`
- Modify: `docs/bugs/README.md`
- Create: `docs/bugs/2026-08-10-report-production-orchestration-manual-recovery.md`

**Interfaces:**
- Consumes all earlier tasks.
- Produces a repeatable three-parent scenario proving capacity, reuse, one Publisher per parent, completion contracts, and no Bilibili dispatch.

- [ ] **Step 1: Build the integration fixture**

  Create three parents configured for full landscape plus scheme 3. Mark scripts/WAVs valid, create visual timelines, leave the first two Publisher children active, and assert the third parent passes start admission because the first two parents are passive waits.

- [ ] **Step 2: Exercise terminal events and retries**

  Complete each Publisher child and invoke the terminal callback twice. Assert each parent receives one accepted wake-up, reuses the existing WAV/visual assets, creates no replacement Publisher, and clears `waiting_child` after completion.

- [ ] **Step 3: Assert the production contract and publication boundary**

  Assert each parent has one landscape plus one scheme-3 path, `completion_contract.ready is True`, and no Bilibili upload/execution child exists.

- [ ] **Step 4: Run focused and grouped suites**

  Run:

  ```bash
  venv/bin/pytest \
    tests/integration/test_report_audio_full_video_recovery.py \
    tests/unit/test_runtime_guard.py \
    tests/unit/test_report_audio_parent_wakeup.py \
    tests/unit/test_report_audio_completion_contract.py \
    tests/unit/test_report_audio_workflow_scheme3_dependency.py \
    tests/unit/test_report_stick_figure_report_audio_retry.py -q
  ```

  Expected: all tests pass.

- [ ] **Step 5: Run project verification**

  Run:

  ```bash
  venv/bin/python -m compileall -q src/backend
  venv/bin/pytest tests/unit -q
  git diff --check
  ```

  Expected: compile and unit suite pass; diff check has no whitespace errors.

- [ ] **Step 6: Record the root cause and runtime acceptance boundary**

  Document the exact mismatch (`publisher_video_*` internal name versus persisted `pub_*`), the one-minute parent polling delay, the new guardrails, and runtime acceptance conditions. Keep runtime status at `CODE_MODIFIED` until a fresh three-PDF batch proves no manual recovery and no duplicate Publisher tasks.

- [ ] **Step 7: Commit integration evidence**

  ```bash
  git add tests/integration/test_report_audio_full_video_recovery.py tests/unit/test_report_audio_workflow_scheme3_dependency.py tests/unit/test_report_stick_figure_report_audio_retry.py docs/bugs/README.md docs/bugs/2026-08-10-report-production-orchestration-manual-recovery.md
  git commit -m "test: cover unattended report video recovery"
  ```
