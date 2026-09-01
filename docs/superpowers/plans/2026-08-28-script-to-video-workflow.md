# 文案到成片通用工作流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a menu-accessible staged workflow that turns user-provided long text into a complete WAV and an image-driven MP4, using reusable visual prompt schemes and the existing real-browser Doubao and Gemini workers.

**Architecture:** Keep `PublicationTask` as the durable parent/child task store and introduce `script_video_workflow` as a generic parent feature. A focused service owns text/scene contracts and output paths; a new Celery task module orchestrates Doubao children, Gemini image children, ASR alignment, and the existing FFmpeg scene renderer. The Flask API exposes explicit stage actions, while the page is a resumable wizard that reads all state from the server.

**Tech Stack:** Python 3, Flask, SQLAlchemy, Celery, Playwright-backed Doubao/Gemini workers, existing ASR cache and FFmpeg renderer, vanilla HTML/JavaScript, pytest/unittest and Node assertions.

**Spec:** `docs/superpowers/specs/2026-08-28-script-to-video-workflow-design.md`

## Global Constraints

- The parent feature is exactly `script_video_workflow`; its public UI must not call it a fire-stick-figure workflow.
- Final audio is WAV only; final video is MP4.
- Reuse `doubao_browser_tts`, the existing Gemini browser, `align_storyboard_to_audio`, and `render_scene_timeline`; do not call browser automation in Flask requests.
- A visual scheme changes only the final Gemini image prompt contract. Existing `stick_figure` and `doraemon` options remain valid.
- Audio chunks and visual scenes are independent. Default audio chunks are sentence-first and 800 characters; scene rows are shorter, editable, and must cover the source script in order.
- Persist audio and image work as child `PublicationTask` rows; retain compact stage summaries in the parent result and avoid a database migration in this first generic workflow.
- All external calls must be idempotent: reuse a completed matching child, preserve completed children, and permit retrying only failed items.
- Preserve unrelated dirty working-tree changes. Do not create commits from this shared `main` checkout.

---

### Task 1: Pure generic workflow contract service

**Files:**

- Create: `src/backend/services/script_video_workflow.py`
- Create: `tests/unit/test_script_video_workflow_service.py`

**Interfaces:**

- Produces `FEATURE_NAME = "script_video_workflow"`, `IMAGE_FEATURE_NAME = "script_video_image"`, `AUDIO_STEP = "audio_ready"`, and `VIDEO_STEP = "completed"`.
- Produces `normalize_script(text: str) -> str` that normalizes whitespace but rejects empty text.
- Produces `split_audio_chunks(text: str, max_chars: int = 800) -> list[str]` by delegating to the proven sentence-first splitter and enforcing `200 <= max_chars <= 5000`.
- Produces `build_initial_scenes(text: str, max_chars: int = 160) -> list[dict]` with one-based `sequence`, `script_text`, and blank `image_prompt`.
- Produces `validate_scene_rows(rows: list[dict], source_text: str, *, require_prompts: bool) -> list[dict]` that rejects missing sequences, duplicate/out-of-order rows, blank scene text, empty required prompts, and normalized text that does not exactly equal the source text.
- Produces `compose_scene_image_prompt(scene: Mapping, scheme_key: str) -> str`, delegating to `scene_image_prompt_for_scheme` so scheme-specific prompt contracts stay centralized.
- Produces `workflow_output_root(workflow_id)`, `audio_output_path(workflow_id)`, `scene_image_output_path(workflow_id, sequence)`, and `safe_output_file(root, candidate)` for bounded output paths.

- [ ] **Step 1: Write the failing service tests**

```python
def test_initial_scene_rows_cover_the_source_script_in_order():
    source = "第一句。第二句！第三句？"
    rows = service.build_initial_scenes(source, max_chars=5)

    assert [row["sequence"] for row in rows] == [1, 2, 3]
    assert "".join(row["script_text"] for row in rows) == source
    assert service.validate_scene_rows(rows, source, require_prompts=False) == rows


def test_scene_validation_rejects_a_gap_before_image_generation():
    with pytest.raises(ValueError, match="未完整覆盖"):
        service.validate_scene_rows(
            [{"sequence": 1, "script_text": "第一句。", "image_prompt": "画面 A"}],
            "第一句。第二句。",
            require_prompts=True,
        )


def test_image_prompt_includes_user_scene_context_and_scheme_contract(monkeypatch):
    monkeypatch.setattr(service, "scene_image_prompt_for_scheme", lambda prompt, text, scheme_key: f"{scheme_key}|{text}|{prompt}")
    assert service.compose_scene_image_prompt(
        {"script_text": "市场出现拐点。", "image_prompt": "折线向上穿越门槛"},
        "stick_figure",
    ) == "stick_figure|市场出现拐点。|折线向上穿越门槛"
```

- [ ] **Step 2: Verify the test is red**

Run: `venv/bin/python -m pytest tests/unit/test_script_video_workflow_service.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'services.script_video_workflow'`.

- [ ] **Step 3: Implement the minimal pure service**

```python
FEATURE_NAME = "script_video_workflow"
IMAGE_FEATURE_NAME = "script_video_image"


def split_audio_chunks(text: str, max_chars: int = 800) -> list[str]:
    bounded = max(200, min(int(max_chars or 800), 5000))
    return split_tts_text(normalize_script(text), max_chars=bounded)


def compose_scene_image_prompt(scene: Mapping, scheme_key: str) -> str:
    return scene_image_prompt_for_scheme(
        str(scene.get("image_prompt") or "").strip(),
        str(scene.get("script_text") or "").strip(),
        scheme_key=scheme_key,
    )
```

Implement the remaining validation and path helpers in this module only; do not duplicate style contracts from `report_visual_scheme.py`.

- [ ] **Step 4: Verify the service is green**

Run: `venv/bin/python -m pytest tests/unit/test_script_video_workflow_service.py -q`

Expected: PASS.

---

### Task 2: Durable Doubao audio stage and generic child projection

**Files:**

- Create: `src/backend/tasks/script_video_workflow.py`
- Create: `tests/unit/test_script_video_workflow_tasks.py`
- Modify: `src/backend/celery_config.py`

**Interfaces:**

- Produces Celery task `tasks.script_video_workflow.process_audio` routed to `report_audio_workflow`.
- Produces `_create_or_reuse_audio_children(parent, chunks) -> list[PublicationTask]` where each child is `feature="doubao_browser_tts"`, `parent_feature="script_video_workflow"`, and includes `chunk_index`, `chunk_total`, text hash, and source metadata.
- Produces `_finalize_audio_stage(parent_id) -> dict` that combines child WAVs in index order with `combine_wav_files`, validates the entire source text, saves `result["audio"]`, and changes the parent to `audio_ready` / `awaiting_storyboard`.
- Produces a parent progress model containing `audio: {chunks_total, chunks_completed, wav_path, seconds, bytes}` and never stores raw binary data in JSON.

- [ ] **Step 1: Write failing audio orchestration tests**

```python
def test_audio_children_are_created_in_source_order_with_generic_parent_metadata(app):
    parent = make_parent(app, text="第一句。第二句。")
    with patch.object(tasks, "apply_async_after_db_release", return_value=SimpleNamespace(id="celery-1")):
        children = tasks._create_or_reuse_audio_children(parent, ["第一句。", "第二句。"])

    assert [child.params["doubao_browser_tts"]["text"] for child in children] == ["第一句。", "第二句。"]
    assert all(child.parent_feature == "script_video_workflow" for child in children)
    assert [child.doubao_tts_chunk_index for child in children] == [1, 2]


def test_audio_finalization_combines_only_completed_children_in_chunk_order(app, tmp_path):
    parent = make_parent(app, text="第一句。第二句。")
    children = make_completed_audio_children(parent, tmp_path, order=[2, 1])
    with patch.object(tasks, "combine_wav_files", return_value={"wav_path": str(tmp_path / "all.wav"), "seconds": 4.2, "bytes": 120, "chunks": 2}):
        result = tasks._finalize_audio_stage(parent.id)

    assert result["chunks"] == 2
    assert db.session.get(PublicationTask, parent.id).current_step == "audio_ready"
```

- [ ] **Step 2: Verify the test is red**

Run: `venv/bin/python -m pytest tests/unit/test_script_video_workflow_tasks.py -q`

Expected: FAIL because `tasks.script_video_workflow` and the stated helpers do not exist.

- [ ] **Step 3: Implement child creation, re-entry, and finalization**

```python
@celery_app.task(bind=True, name="tasks.script_video_workflow.process_audio")
def process_audio(self, workflow_id):
    app = create_app()
    with app.app_context():
        return _process_audio(workflow_id, celery_task=self)


def _process_audio(workflow_id, *, celery_task):
    parent = _owned_workflow(workflow_id)
    chunks = list((parent.params[FEATURE_NAME] or {}).get("audio_chunks") or [])
    children = _create_or_reuse_audio_children(parent, chunks)
    pending = _unfinished(children)
    if pending:
        _set_waiting_child(parent.id, "doubao_audio", pending[0])
        raise celery_task.retry(countdown=10, max_retries=360)
    return _finalize_audio_stage(parent.id)
```

Use bounded, explicit retry handling for child `failed`, `blocked_by_captcha`, and stale states. Do not spin in a Flask request or start another Doubao browser worker.

- [ ] **Step 4: Register the module and queue route**

Add `tasks.script_video_workflow` to Celery `include` and route:

```python
'tasks.script_video_workflow.process_audio': {'queue': 'report_audio_workflow'},
'tasks.script_video_workflow.generate_image': {'queue': 'report_pdf_gemini'},
'tasks.script_video_workflow.render_video': {'queue': 'render'},
```

- [ ] **Step 5: Verify the audio task tests are green**

Run: `venv/bin/python -m pytest tests/unit/test_script_video_workflow_tasks.py -q`

Expected: PASS.

---

### Task 3: Gemini image children, whole-audio alignment, and MP4 rendering

**Files:**

- Modify: `src/backend/tasks/script_video_workflow.py`
- Modify: `tests/unit/test_script_video_workflow_tasks.py`

**Interfaces:**

- Produces Celery task `tasks.script_video_workflow.generate_image(child_task_id)` that calls `GeminiBrowserWorker.generate_image` once with the composed scheme prompt and writes the output to the generic workflow directory.
- Produces `_image_children_for_parent(parent_id) -> list[PublicationTask]`, each with feature `script_video_image`, sequence, scene text, prompt hash, image path, status and error.
- Produces Celery task `tasks.script_video_workflow.render_video(workflow_id)` that calls `transcribe_with_cache(..., include_metadata=True)`, `align_storyboard_to_audio`, and `render_scene_timeline(..., audio_mode="whole_audio")`.
- Produces `result["alignment"]` with timeline summary and `result["video"]` with MP4, manifest, subtitle, duration and bytes.

- [ ] **Step 1: Write failing image and render tests**

```python
def test_image_child_uses_only_the_scheme_composed_prompt(app, tmp_path):
    child = make_image_child(app, scheme="doraemon")
    with patch.object(tasks, "GeminiBrowserWorker") as worker, \
         patch.object(tasks, "compose_scene_image_prompt", return_value="combined prompt"):
        worker.return_value.generate_image.return_value = {"path": str(tmp_path / "scene.png"), "bytes": 12}
        tasks._generate_image(child.id)

    assert worker.return_value.generate_image.call_args.kwargs["prompt"] == "combined prompt"
    assert db.session.get(PublicationTask, child.id).status == "completed"


def test_render_requires_all_scene_images_and_persists_continuous_timeline(app, tmp_path):
    parent = make_audio_ready_parent(app, tmp_path)
    make_completed_image_children(parent, tmp_path, count=2)
    with patch.object(tasks, "transcribe_with_cache", return_value={"segments": [{"text": "甲乙", "start": 0, "end": 2}], "metadata": {"cache_key": "abc"}}), \
         patch.object(tasks, "align_storyboard_to_audio", return_value={"timeline": [{"sequence": 1, "start_seconds": 0, "end_seconds": 1}, {"sequence": 2, "start_seconds": 1, "end_seconds": 2}]}), \
         patch.object(tasks, "render_scene_timeline", return_value={"video_path": str(tmp_path / "video.mp4"), "duration_seconds": 2}):
        tasks._render_video(parent.id)

    assert db.session.get(PublicationTask, parent.id).status == "completed"
```

- [ ] **Step 2: Verify the tests are red**

Run: `venv/bin/python -m pytest tests/unit/test_script_video_workflow_tasks.py -q`

Expected: FAIL because image and render helpers are not yet implemented.

- [ ] **Step 3: Implement image child generation**

```python
def _generate_image(child_id: str) -> bool:
    child, parent, scene = _load_image_child(child_id)
    prompt = compose_scene_image_prompt(scene, parent.report_visual_scheme_key)
    image = GeminiBrowserWorker().generate_image(
        job_id=child.id,
        prompt=prompt,
        image_output_path=scene_image_output_path(parent.id, scene["sequence"]),
        target_url=visual_scheme(parent.report_visual_scheme_key).image_gemini_url,
        progress_callback=lambda event: _update_image_progress(child.id, event),
    )
    _mark_image_completed(child.id, image)
    return True
```

Classify Gemini login/captcha/quota errors into durable, actionable child states. Preserve successful images and only retry explicit failed sequences.

- [ ] **Step 4: Implement whole-audio render**

```python
asr = transcribe_with_cache(audio_path, model_size="base", initial_prompt=source_text, include_metadata=True)
alignment = align_storyboard_to_audio(
    render_scenes,
    asr["segments"],
    audio_seconds=audio_seconds,
    source_script=source_text,
)
video = render_scene_timeline(
    workflow_id,
    scenes_with_image_paths_and_timeline(alignment),
    audio_mode="whole_audio",
    whole_audio_path=audio_path,
    render_options={"subtitles_enabled": True, "watermark_enabled": False, "speed": 1.0},
)
```

Check that every submitted scene image exists before ASR or FFmpeg begins. Update parent status only after the renderer returns a valid MP4 result.

- [ ] **Step 5: Verify the task tests are green**

Run: `venv/bin/python -m pytest tests/unit/test_script_video_workflow_tasks.py -q`

Expected: PASS.

---

### Task 4: Stage-oriented Flask API and service registration

**Files:**

- Create: `src/backend/api/script_video_workflow.py`
- Create: `tests/unit/test_script_video_workflow_api.py`
- Modify: `src/backend/apis/__init__.py`

**Interfaces:**

- Adds blueprint prefix `/api/script-video-workflows`.
- Adds `GET /status`, `POST /jobs`, `GET /jobs/<id>`, `GET /jobs`, `PATCH /jobs/<id>/audio-chunks`, `POST /jobs/<id>/audio`, `PATCH /jobs/<id>/scenes`, `POST /jobs/<id>/images`, `POST /jobs/<id>/scenes/<sequence>/image/retry`, `POST /jobs/<id>/render`.
- Adds downloads: `GET /jobs/<id>/audio/file`, `GET /jobs/<id>/images/<sequence>`, and `GET /jobs/<id>/video/file`.
- `POST /jobs` response includes `job_id`, normalized text statistics, editable `audio_chunks`, editable `scenes`, and `visual_schemes`.

- [ ] **Step 1: Write failing API tests**

```python
def test_create_job_returns_editable_audio_and_scene_drafts(client, current_user):
    with client.application.test_request_context(
        "/api/script-video-workflows/jobs",
        method="POST",
        json={"text": "第一句。第二句。", "visual_scheme_key": "stick_figure"},
    ):
        response = create_job.__wrapped__(current_user)
    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["audio_chunks"]
    assert body["data"]["scenes"][0]["image_prompt"] == ""


def test_update_scenes_rejects_incomplete_source_coverage(client, current_user, workflow):
    with client.application.test_request_context(
        f"/api/script-video-workflows/jobs/{workflow.id}/scenes",
        method="PATCH",
        json={"scenes": [{"sequence": 1, "script_text": "第一句。", "image_prompt": "画面"}]},
    ):
        response, status = update_scenes.__wrapped__(current_user, workflow.id)
    assert status == 400
    assert "未完整覆盖" in response.get_json()["message"]


def test_submit_images_queues_one_child_per_valid_scene(client, current_user, workflow):
    with patch.object(api, "maybe_guarded_apply_async", return_value={"async_result": SimpleNamespace(id="broker-1")}):
        response = submit_images.__wrapped__(current_user, workflow.id)
    assert response.get_json()["data"]["queued"] == 2
```

- [ ] **Step 2: Verify the API tests are red**

Run: `venv/bin/python -m pytest tests/unit/test_script_video_workflow_api.py -q`

Expected: FAIL because the blueprint and handlers do not yet exist.

- [ ] **Step 3: Implement thin, authenticated handlers**

Keep parsing, validation, ownership checks and response projection in the API module. Put external work into the new Celery tasks. Each enqueue must call `maybe_guarded_apply_async` or `apply_async_after_db_release` after committing the durable task state.

```python
@script_video_workflow_bp.route("/jobs/<job_id>/audio", methods=["POST"])
@require_login
def submit_audio(current_user, job_id):
    parent = _owned_parent(current_user, job_id)
    task = enqueue_audio_stage(parent)
    return jsonify({"success": True, "data": {"job_id": parent.id, "celery_id": task.id}})
```

- [ ] **Step 4: Register the blueprint and public API endpoint map**

```python
from api.script_video_workflow import script_video_workflow_bp
app.register_blueprint(script_video_workflow_bp)
endpoints['script_video_workflows'] = '/api/script-video-workflows'
```

- [ ] **Step 5: Verify the API tests are green**

Run: `venv/bin/python -m pytest tests/unit/test_script_video_workflow_api.py -q`

Expected: PASS.

---

### Task 5: Resumable workbench page and global menu entry

**Files:**

- Create: `src/frontend/script-video-workflow.html`
- Create: `src/frontend/js/script-video-workflow.js`
- Create: `tests/unit/test_script_video_workflow_frontend_contract.py`
- Modify: `src/frontend/js/shared-navigation.js`

**Interfaces:**

- Page identifier is `script-video-workflow`; its title and menu label are both “文案到成片”.
- Frontend API root is `/api/script-video-workflows`.
- The page exposes the five ordered panels: `文案`, `语音`, `分镜`, `图片`, `对齐与成片`.
- JavaScript provides `submitDraft()`, `saveAudioChunks()`, `startAudio()`, `saveScenes()`, `startImages()`, `retryImage(sequence)`, `startRender()`, `loadWorkflow()`, and a single hidden-tab-aware polling scheduler.

- [ ] **Step 1: Write failing frontend contract tests**

```python
def test_workbench_has_all_five_stages_and_shared_navigation():
    html = (PROJECT_ROOT / "src/frontend/script-video-workflow.html").read_text(encoding="utf-8")
    assert "文案" in html and "语音" in html and "分镜" in html and "图片" in html and "对齐与成片" in html
    assert "js/shared-navigation.js" in html
    assert "js/script-video-workflow.js" in html


def test_frontend_uses_stage_endpoints_and_pauses_polling_when_hidden():
    js = (PROJECT_ROOT / "src/frontend/js/script-video-workflow.js").read_text(encoding="utf-8")
    assert "/api/script-video-workflows/jobs" in js
    assert "/audio" in js and "/images" in js and "/render" in js
    assert "document.hidden" in js
    assert "visibilitychange" in js
```

- [ ] **Step 2: Verify the test is red**

Run: `venv/bin/python -m pytest tests/unit/test_script_video_workflow_frontend_contract.py -q`

Expected: FAIL because the new page and JavaScript do not exist.

- [ ] **Step 3: Build the page and stateful client**

Render editable rows with DOM APIs and escape every user-derived value before assigning HTML. Disable a stage action until its server-side prerequisite is satisfied. For each image row, display status, preview image URL, error text, and a retry button. Restore an existing job ID from `?job_id=` and poll only while the document is visible and the task is nonterminal.

```javascript
async startRender() {
  await this.request(`/api/script-video-workflows/jobs/${encodeURIComponent(this.jobId)}/render`, {method: 'POST'});
  await this.loadWorkflow();
}

schedulePolling() {
  clearTimeout(this.pollTimer);
  if (!this.jobId || document.hidden || this.isTerminal()) return;
  this.pollTimer = setTimeout(() => this.loadWorkflow().finally(() => this.schedulePolling()), 3500);
}
```

- [ ] **Step 4: Add the global navigation entry**

Add `script-video-workflow.html` to `getCurrentPageName`, `getPageTitle`, and the existing video/workflow menu section. Do not alter existing menu IDs or destinations.

- [ ] **Step 5: Verify the frontend contract is green**

Run: `venv/bin/python -m pytest tests/unit/test_script_video_workflow_frontend_contract.py -q`

Expected: PASS.

---

### Task 6: Cross-stage verification and regression protection

**Files:**

- Modify: `tests/unit/test_script_video_workflow_tasks.py`
- Modify: `tests/unit/test_script_video_workflow_api.py`
- Modify: `tests/unit/test_script_video_workflow_frontend_contract.py`

**Interfaces:**

- Ensures completed audio/image children are reused after retry.
- Ensures visual-scheme prompt composition changes only the Gemini request, not source text, WAV, or render mode.
- Ensures missing/failed image children block rendering with a helpful error.
- Ensures generated file endpoints reject paths outside the workflow output root.

- [ ] **Step 1: Add failing cross-stage tests**

```python
def test_visual_scheme_prompt_composition_does_not_change_completed_audio(app):
    parent = make_audio_ready_parent(app)
    before = dict(parent.result["audio"])
    stick = compose_scene_image_prompt({"script_text": "第一句。", "image_prompt": "向上箭头"}, "stick_figure")
    doraemon = compose_scene_image_prompt({"script_text": "第一句。", "image_prompt": "向上箭头"}, "doraemon")
    assert stick != doraemon
    assert db.session.get(PublicationTask, parent.id).result["audio"] == before


def test_render_refuses_missing_image_before_asr(app):
    parent = make_audio_ready_parent(app)
    make_image_child(parent, status="failed")
    with pytest.raises(ValueError, match="图片"):
        tasks._render_video(parent.id)
```

- [ ] **Step 2: Verify the cross-stage tests are red**

Run: `venv/bin/python -m pytest tests/unit/test_script_video_workflow_tasks.py tests/unit/test_script_video_workflow_api.py -q`

Expected: FAIL until the idempotency and render guards are complete.

- [ ] **Step 3: Implement only the missing guards and state transitions**

Ensure `PATCH /scenes` invalidates only image child work whose scene text/prompt hash changed. Visual scheme remains immutable for first-version tasks, so completed audio is never affected by image-prompt changes. Ensure render checks every sequence before ASR work starts.

- [ ] **Step 4: Run focused and neighboring regression tests**

Run:

```bash
venv/bin/python -m pytest \
  tests/unit/test_script_video_workflow_service.py \
  tests/unit/test_script_video_workflow_tasks.py \
  tests/unit/test_script_video_workflow_api.py \
  tests/unit/test_script_video_workflow_frontend_contract.py \
  tests/unit/test_doubao_browser_tts_task.py \
  tests/unit/test_doubao_browser_tts_api.py \
  tests/unit/test_report_stick_figure_video_render.py \
  tests/unit/test_report_visual_plan_storyboard.py -q
```

Expected: PASS with no new warnings or collection errors.

- [ ] **Step 5: Compile and inspect changed files**

Run:

```bash
venv/bin/python -m compileall -q src/backend/api/script_video_workflow.py src/backend/services/script_video_workflow.py src/backend/tasks/script_video_workflow.py
git diff --check
git status --short
```

Expected: no Python compilation errors, no whitespace errors, and only intentional new/modified feature files plus pre-existing user changes.
