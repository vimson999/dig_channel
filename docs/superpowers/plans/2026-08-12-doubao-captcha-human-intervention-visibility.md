# Doubao Captcha Human Intervention Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every terminal Doubao captcha block produce an actionable, traceable human handoff whose email outcome and recovery instructions are visible in the report-audio UI.

**Architecture:** Keep `blocked_by_captcha` as the terminal machine boundary, enrich its structured result before sending the existing deduplicated email, and project only the two required JSON branches through lightweight child queries. A DOM-independent frontend helper normalizes the handoff state for both the list page and the workflow detail page.

**Tech Stack:** Python 3, Flask, SQLAlchemy/PostgreSQL JSONB, Celery task code, browser JavaScript, Node `assert`, `unittest`/pytest.

## Global Constraints

- Do not attempt to solve or bypass third-party captcha challenges automatically.
- Do not automatically retry a child in `blocked_by_captcha`; recovery requires a human followed by the existing parent “从失败处续跑” action.
- Do not add a database migration or load the complete child `result` in list/status queries.
- Public summaries must not expose a complete email recipient; return only a masked `recipient_hint`.
- Preserve all unrelated user-owned changes in the current dirty worktree; do not stage or commit this implementation as part of this run.
- Do not mutate or resend the existing production incident task while implementing the code.

---

### Task 1: Actionable Captcha Handoff Contract

**Files:**
- Modify: `src/backend/services/doubao_browser_tts.py:51-70,650-705`
- Modify: `src/backend/services/workflow_notification_service.py:78-285`
- Modify: `src/backend/tasks/doubao_browser_tts.py:506-602`
- Create: `tests/unit/test_doubao_browser_tts_task.py`
- Modify: `tests/unit/test_doubao_browser_tts_service.py`
- Modify: `tests/unit/test_workflow_notification_service.py`

**Interfaces:**
- Produces: `_captcha_human_intervention_context(task, captcha) -> dict`
- Produces: `public_browser_human_intervention_notification(notification) -> dict`
- Extends: `send_browser_human_intervention(*, provider, reason, message, job_id="", page_url="", screenshot_path="", profile="", worker_id="", details=None, resolution_steps=None) -> dict`
- Persists: `result.captcha_blocked.workflow_url`, `profile`, `worker_id`, `parent_task_id`, `resolution_steps`
- Persists: `result.notifications.doubao_captcha_blocked`

- [x] **Step 1: Write failing tests for the handoff context and public notification**

```python
def test_captcha_context_contains_parent_workflow_link_and_recovery_steps(self):
    task = SimpleNamespace(id="child-1", params={"parent_task_id": "parent-1", "doubao_browser_tts": {"user_data_dir": "/profiles/p1", "metadata": {"parent_task_id": "parent-1", "worker_id": "worker-1"}}})
    context = _captcha_human_intervention_context(task, {"page_url": "https://www.doubao.com/chat/1"})
    self.assertEqual(context["workflow_url"], "http://localhost:5001/report-audio-workflow-detail.html?job_id=parent-1")
    self.assertIn("从失败处续跑", context["resolution_steps"][-1])

def test_public_notification_masks_recipient(self):
    summary = public_browser_human_intervention_notification({"sent": True, "to": "owner@example.test"})
    self.assertEqual(summary["recipient_hint"], "o***@example.test")
    self.assertNotIn("to", summary)
```

- [x] **Step 2: Run the tests and verify RED**

Run: `venv/bin/python -m pytest tests/unit/test_doubao_browser_tts_task.py -q`

Expected: import failure because the two production helpers do not exist.

- [x] **Step 3: Implement the minimal structured context and use it in the email path**

```python
def _captcha_human_intervention_context(task, captcha):
    params = task.params if isinstance(task.params, dict) else {}
    payload = params.get("doubao_browser_tts") if isinstance(params.get("doubao_browser_tts"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    parent_id = str(metadata.get("parent_task_id") or params.get("parent_task_id") or "").strip()
    base_url = str(os.getenv("REPORT_AUDIO_WORKFLOW_FRONTEND_BASE_URL") or os.getenv("REPORT_WORKFLOW_FRONTEND_BASE_URL") or os.getenv("FRONTEND_BASE_URL") or "http://localhost:5001").strip().rstrip("/")
    return {
        "parent_task_id": parent_id,
        "workflow_url": f"{base_url}/report-audio-workflow-detail.html?job_id={quote(parent_id)}" if parent_id else "",
        "profile": str(payload.get("user_data_dir") or ""),
        "worker_id": str(metadata.get("worker_id") or ""),
        "resolution_steps": [
            "使用通知中指定的浏览器 Profile 打开豆包页面",
            "完成验证码或安全验证，并确认页面可以正常发起朗读",
            "返回工作流详情页，点击“从失败处续跑”",
        ],
    }

def public_browser_human_intervention_notification(notification):
    allowed = {key: notification.get(key) for key in ("sent", "reason", "sent_at", "retry_after_seconds", "event_type", "error", "missing") if notification.get(key) is not None}
    if notification.get("to"):
        allowed["recipient_hint"] = _mask_email(notification["to"])
    return allowed
```

Before calling `send_browser_human_intervention`, merge the context into `captcha_blocked`, pass `工作流详情` and the three steps through the email details, and persist the final notification result. Keep the existing early return for an already-sent task only after the structured context is available.

- [x] **Step 4: Run the tests and verify GREEN**

Run: `venv/bin/python -m pytest tests/unit/test_doubao_browser_tts_task.py -q`

Expected: all tests pass.

### Task 2: Lightweight Public Projection

**Files:**
- Modify: `src/backend/tasks/report_audio_workflow.py:4935-4965`
- Modify: `src/backend/api/report_audio_workflow.py:4400-4510,4930-4995,5220-5495`
- Modify: `tests/unit/test_report_audio_workflow_task.py`
- Modify: `tests/unit/test_report_audio_workflow_api.py`

**Interfaces:**
- Consumes: `public_browser_human_intervention_notification(notification) -> dict`
- Produces public child fields: `captcha_blocked: dict | null`, `notifications.doubao_captcha_blocked: dict`
- Query labels: `captcha_blocked`, `captcha_notification`

- [x] **Step 1: Write failing tests for task and API summaries**

```python
def test_public_child_summary_keeps_captcha_notification(self):
    summary = report_audio_workflow._task_public_summary({"job_id": "child", "status": "blocked_by_captcha", "result": {"captcha_blocked": {"requires_human_action": True}, "notifications": {"doubao_captcha_blocked": {"sent": True, "to": "owner@example.test"}}}})
    self.assertTrue(summary["notifications"]["doubao_captcha_blocked"]["sent"])
    self.assertNotIn("to", summary["notifications"]["doubao_captcha_blocked"])

def test_lightweight_child_row_exposes_captcha_handoff(self):
    row = SimpleNamespace(
        id="child", celery_task_id=None, status="blocked_by_captcha",
        current_step="blocked_by_captcha", progress=45,
        error_message="需要人机验证", feature="doubao_browser_tts",
        parent_task_id="parent", source_task_id=None, task_title=None,
        task_video_path=None, audio_wav_path=None, audio_seconds=None,
        audio_bytes=None, created_at=None, completed_at=None,
        captcha_blocked={"requires_human_action": True},
        captcha_notification={"sent": False, "reason": "missing_config"},
    )
    payload = report_audio_workflow_api._child_task_summary_row(row)
    self.assertEqual(payload["notifications"]["doubao_captcha_blocked"]["reason"], "missing_config")
```

- [x] **Step 2: Run the two focused test modules and verify RED**

Run: `venv/bin/python -m pytest tests/unit/test_report_audio_workflow_task.py tests/unit/test_report_audio_workflow_api.py -q`

Expected: missing `notifications`/`captcha_blocked` fields in public summaries.

- [x] **Step 3: Add sanitized summary fields and two JSON projections**

Add `captcha_blocked` and the sanitized `doubao_captcha_blocked` notification to both rich and lightweight summary builders. Extend `_child_task_summary_query()` with only:

```python
PublicationTask.result["captcha_blocked"].label("captcha_blocked"),
PublicationTask.result["notifications"]["doubao_captcha_blocked"].label("captcha_notification"),
```

Do not select `PublicationTask.result` itself.

- [x] **Step 4: Run the focused modules and verify GREEN**

Run: `venv/bin/python -m pytest tests/unit/test_report_audio_workflow_task.py tests/unit/test_report_audio_workflow_api.py -q`

Expected: all tests pass.

### Task 3: Shared Frontend Handoff State and UI

**Files:**
- Create: `src/frontend/js/browser-human-intervention.js`
- Modify: `src/frontend/js/report-audio-workflow-test.js:2157-2340`
- Modify: `src/frontend/js/report-audio-workflow-detail.js:750-800,1920-1960`
- Modify: `src/frontend/report-audio-workflow-test.html:307`
- Modify: `src/frontend/report-audio-workflow-detail.html:79`
- Create: `tests/frontend/browser-human-intervention.test.cjs`

**Interfaces:**
- Produces: `BrowserHumanIntervention.resolve(child, options) -> null | HandoffViewModel`
- Consumes: `child.captcha_blocked`, `child.notifications.doubao_captcha_blocked`
- `HandoffViewModel` fields: `state`, `label`, `sentAt`, `recipientHint`, `workflowUrl`, `pageUrl`, `screenshotPath`, `profile`, `resolutionSteps`

- [x] **Step 1: Write the Node behavior test**

```javascript
const sent = helper.resolve({status: 'blocked_by_captcha', captcha_blocked: {requires_human_action: true}, notifications: {doubao_captcha_blocked: {sent: true, sent_at: '2026-08-12T08:34:14Z'}}});
assert.equal(sent.state, 'sent');
assert.match(sent.label, /已邮件通知/);
assert.equal(helper.resolve({status: 'completed'}), null);
```

Include separate literal assertions for `cooldown` and `missing_config`.

- [x] **Step 2: Run the Node test and verify RED**

Run: `node tests/frontend/browser-human-intervention.test.cjs`

Expected: module-not-found because the shared helper has not been created.

- [x] **Step 3: Implement the helper and render it in all three UI locations**

Create a UMD-style global/CommonJS helper with no DOM dependency. Load it before both page scripts. In the list child card, render the compact status line; in the child modal, render steps and links; in the detail summary, render the same actionable panel immediately after the workflow error. Continue using each page's existing `escape`/`escapeAttr` methods.

- [x] **Step 4: Run Node behavior and syntax checks**

Run: `node tests/frontend/browser-human-intervention.test.cjs`

Run: `node --check src/frontend/js/browser-human-intervention.js`

Run: `node --check src/frontend/js/report-audio-workflow-test.js`

Run: `node --check src/frontend/js/report-audio-workflow-detail.js`

Expected: all commands exit 0.

### Task 4: Regression Verification

**Files:**
- Verify all files changed in Tasks 1-3.

**Interfaces:**
- No new interfaces; this task validates the complete contract.

- [x] **Step 1: Run focused regression tests**

Run: `venv/bin/python -m pytest tests/unit/test_doubao_browser_tts_task.py tests/unit/test_report_audio_workflow_task.py tests/unit/test_report_audio_workflow_api.py tests/unit/test_workflow_notification_service.py -q`

- [x] **Step 2: Run the complete unit suite**

Run: `venv/bin/python -m pytest tests/unit -q`

Observed with the repository-required `PYTHONPATH=src/backend`: 1177 passed, 3 skipped, and 3 unrelated failures in the pre-existing in-progress Gemini contract-repair work because `_contract_diagnostics` is not defined.

- [x] **Step 3: Run static checks**

Run: `venv/bin/python -m py_compile src/backend/tasks/doubao_browser_tts.py src/backend/tasks/report_audio_workflow.py src/backend/api/report_audio_workflow.py`

Run: `node tests/frontend/browser-human-intervention.test.cjs`

Run: `node --check src/frontend/js/browser-human-intervention.js`

Run: `node --check src/frontend/js/report-audio-workflow-test.js`

Run: `node --check src/frontend/js/report-audio-workflow-detail.js`

Run: `git diff --check`

- [x] **Step 4: Review the final diff against the design**

Confirm the diff has no database migration, no captcha bypass, no automatic retry, no full child-result selection in the list query, no unmasked recipient in public summaries, and no mutation of the historical incident task.
