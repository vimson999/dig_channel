# Gemini Login Human Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Gemini login loss into an explicit human-blocked workflow with an actionable email and safe same-task recovery.

**Architecture:** The browser raises a structured human-intervention exception after sending the alert. The storyboard task projects that exception into child and parent task state, while a guarded API endpoint requeues only `blocked_by_login` tasks. The detail page renders the blocked state and calls that endpoint after the operator restores the Profile login.

**Tech Stack:** Python 3, Flask, SQLAlchemy, Celery, unittest/pytest, vanilla JavaScript.

## Global Constraints

- Use the existing `PublicationTask.status` string field; no database migration.
- Never automatically retry login loss.
- Resume the existing storyboard task ID and preserve its source material and artifacts.
- Preserve the existing 30-minute per-Profile notification cooldown.
- Do not change unrelated dirty-worktree files.

---

### Task 1: Actionable structured login notification

**Files:**
- Modify: `src/backend/services/workflow_notification_service.py`
- Modify: `src/backend/services/gemini_browser.py`
- Test: `tests/unit/test_workflow_notification_service.py`
- Test: `tests/unit/test_report_visual_plan_storyboard.py`

**Interfaces:**
- Produces: `GeminiBrowserHumanInterventionError(reason, message, notification, context)`.
- Produces: `send_browser_human_intervention(..., workflow_url="") -> dict` with an actionable runbook in the email body.

- [ ] Write tests proving the email contains the exact Profile-oriented login sequence and workflow URL, and login detection exposes the notification result on a structured exception.
- [ ] Run the focused tests and confirm they fail because the runbook and exception type do not exist.
- [ ] Implement the exception and notification body with the minimum required context.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Project login loss into child and parent blocked state

**Files:**
- Modify: `src/backend/tasks/report_visual_plan.py`
- Modify: `src/backend/api/report_visual_plan.py`
- Modify: `src/backend/api/report_stick_figure_video_workflow.py`
- Test: `tests/unit/test_report_visual_plan_storyboard.py`

**Interfaces:**
- Produces: `_block_for_human_login(task_id, exc) -> False`.
- Produces: public API status `blocked_by_login` for child and parent payloads.

- [ ] Write tests proving login exceptions bypass retry, persist notification context, and project `blocked_by_login` to both task levels.
- [ ] Run the focused tests and confirm they fail on the missing state projection.
- [ ] Implement child/parent projection and status mappings.
- [ ] Run the focused tests and confirm they pass.

### Task 3: Guarded same-task recovery endpoint

**Files:**
- Modify: `src/backend/api/report_visual_plan.py`
- Test: `tests/unit/test_report_visual_plan_storyboard.py`

**Interfaces:**
- Produces: `POST /api/report-visual-plans/jobs/<job_id>/resume-after-login`.
- Consumes: `process_report_visual_plan_job` and existing stale-delivery protection.

- [ ] Write tests proving only an owned `blocked_by_login` task is reset and requeued with the same job ID.
- [ ] Run the focused tests and confirm they fail because the endpoint is absent.
- [ ] Implement guarded enqueue, child/parent state reset, cache invalidation, and enqueue-failure reporting.
- [ ] Run the focused tests and confirm they pass.

### Task 4: Human recovery UI

**Files:**
- Modify: `src/frontend/js/report-stick-figure-video-workflow-detail.js`
- Test: `tests/unit/test_report_visual_plan_storyboard.py`

**Interfaces:**
- Consumes: storyboard status `blocked_by_login` and the resume endpoint.
- Produces: visible runbook, notification state, and `data-resume-storyboard-login` action.

- [ ] Write tests proving the page labels login blocking distinctly, displays steps, and calls the resume endpoint.
- [ ] Run the focused tests and confirm they fail on the missing UI contract.
- [ ] Implement rendering, click handling, and post-resume polling.
- [ ] Run the focused tests and confirm they pass; run `node --check`.

### Task 5: Regression verification

**Files:**
- Verify all files above.

- [ ] Run workflow notification, storyboard, queue repair, and video workflow unit suites.
- [ ] Run Python compilation, JavaScript syntax check, and `git diff --check`.
- [ ] Review the final diff against every design requirement and record any operational restart requirement.

