from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_generation_mode_precedes_mode_specific_options():
    html = (
        PROJECT_ROOT / "src/frontend/report-audio-workflow-test.html"
    ).read_text(encoding="utf-8")

    mode_position = html.index('name="video_render_mode"')
    keyframe_position = html.index('id="keyframe-output-panel"')
    full_video_position = html.index('id="full-video-options-panel"')
    tts_position = html.index('id="tts-provider"')

    assert mode_position < keyframe_position < tts_position
    assert mode_position < full_video_position < tts_position
    for output in (
        "bilibili_cover",
        "vertical_cover",
        "landscape_keyframe",
        "vertical_keyframe_scheme1",
        "vertical_keyframe_scheme2",
        "vertical_keyframe_scheme3",
    ):
        assert f'value="{output}"' in html


def test_submission_sends_only_the_active_mode_options():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-test.js"
    ).read_text(encoding="utf-8")
    html = (
        PROJECT_ROOT / "src/frontend/report-audio-workflow-test.html"
    ).read_text(encoding="utf-8")

    assert "updateGenerationModeUi()" in javascript
    assert "renderMode === 'keyframes' ? this.selectedKeyframeOutputs() : []" in javascript
    assert "renderMode === 'full_video' && Boolean(this.els.landscapeVideoEnabled?.checked)" in javascript
    assert "form.append('keyframe_outputs', JSON.stringify(keyframeOutputs))" in javascript
    assert "form.append('pdf_evidence_enabled', this.els.pdfEvidenceEnabled?.checked ? '1' : '0')" in javascript
    assert 'id="pdf-evidence-enabled" type="checkbox"' in html
    assert 'id="pdf-evidence-enabled" type="checkbox" checked' not in html
    assert "PDF_EVIDENCE_V1_JSON" not in javascript


def test_synced_pdf_page_expands_all_ten_rows_without_nested_scrolling():
    html = (
        PROJECT_ROOT / "src/frontend/report-audio-workflow-test.html"
    ).read_text(encoding="utf-8")

    asset_list = html.split('id="pdf-asset-list"', 1)[1].split("</div>", 1)[0]

    assert 'class="mt-2 space-y-2"' in asset_list
    assert "max-h-" not in asset_list
    assert "overflow-" not in asset_list


def test_keyframe_confirmation_preserves_landscape_selection_and_busts_cache():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js"
    ).read_text(encoding="utf-8")
    html = (
        PROJECT_ROOT / "src/frontend/report-audio-workflow-detail.html"
    ).read_text(encoding="utf-8")

    assert "include_landscape: options.includeLandscape === true" in javascript
    assert "/confirm-keyframes" in javascript
    assert "vertical_video_schemes: selection.schemes" in javascript
    assert "selection_source: 'keyframe-confirmation'" in javascript
    assert "schemes: ['scheme3']" in javascript
    assert "启动白板 + 横屏 + 方案三" in javascript
    assert "selection_source: options.selectionSource || 'vertical-video-panel'" in javascript
    assert "browser-human-intervention.js?v=20260812-v1" in html
    assert "report-audio-workflow-detail.js?v=20260812-human-intervention-v1" in html


def test_bilibili_account_selection_applies_report_media_defaults():
    detail = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js"
    ).read_text(encoding="utf-8")
    panel = (
        PROJECT_ROOT / "src/frontend/js/bilibili-publish-panel.js"
    ).read_text(encoding="utf-8")

    assert "accountDefaults: (credential) => this.reportBilibiliAccountDefaults(credential)" in detail
    assert "label.includes('ai研报0709')" in detail
    assert "{ videoKey: 'vertical', coverKey: 'bilibili_cover' }" in detail
    assert "label.includes('小君君0708')" in detail
    assert "{ videoKey: 'landscape', coverKey: 'cover' }" in detail
    assert "onCredentialChange(credentialId)" in panel
    assert "this.formDraft = {" in panel


def test_report_audio_polling_pauses_for_hidden_tabs_and_uses_safe_defaults():
    list_javascript = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-test.js"
    ).read_text(encoding="utf-8")
    detail_javascript = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js"
    ).read_text(encoding="utf-8")
    list_html = (
        PROJECT_ROOT / "src/frontend/report-audio-workflow-test.html"
    ).read_text(encoding="utf-8")

    assert "if (document.hidden || this.pollInFlight || this.historyLoading) return" in list_javascript
    assert "if (document.hidden) return" in list_javascript
    assert "Math.max(5000, Number(this.els.pollDelay.value) || 15000)" in list_javascript
    assert 'min="5000" step="1000" value="15000"' in list_html
    assert "this.autoRefreshMs = 10000" in detail_javascript
    assert "if (document.hidden) this.clearAutoRefresh()" in detail_javascript
    assert "if (document.hidden) return" in detail_javascript


def test_report_audio_detail_polling_recovers_after_transient_request_failure():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js"
    ).read_text(encoding="utf-8")

    load_body = javascript.split("async load(options = {}) {", 1)[1].split(
        "\n    setRefreshBusy(", 1
    )[0]
    retry_body = javascript.split("scheduleRetryRefresh() {", 1)[1].split(
        "\n    async refreshBilibiliUploads()", 1
    )[0]
    assert "this.scheduleRetryRefresh();" in load_body
    assert "this.load({ background: Boolean(this.job) });" in retry_body
    assert "Math.min(this.autoRefreshMs, 5000)" in retry_body


def test_video_actions_are_grouped_by_aspect_and_registry_driven_scheme():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js"
    ).read_text(encoding="utf-8")

    assert "成片方案中心" in javascript
    assert "横屏方案" in javascript
    assert "竖屏方案" in javascript
    assert "方案 1 · 研报截图成片" in javascript
    assert "this.verticalVideoSchemes = [" in javascript
    assert "视觉方案注册表" in javascript
    assert "方案三视觉素材源" in javascript
    assert ".map((item, index) => {" in javascript
    assert "data-detail-vertical-scheme]:checked" in javascript


def test_visual_workflow_detail_entries_remain_available_per_scheme():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js"
    ).read_text(encoding="utf-8")

    assert 'data-visual-scheme-key="${this.escapeAttr(item.key)}"' in javascript
    assert "创建${this.escape(item.name)}详情" in javascript
    assert "openStickFigureWorkflow(jobId, visualSchemeKey = '')" in javascript
    assert "visual_scheme_key: requestedSchemeKey" in javascript


def test_detail_summary_uses_semantic_workflow_stages_instead_of_a_progress_bar():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js"
    ).read_text(encoding="utf-8")

    assert "reportWorkflowStages(job)" in javascript
    assert "reportWorkflowStageIndex(step, progress)" in javascript
    assert "job?.orchestration?.stages" in javascript
    assert "输入" in javascript
    assert "文案" in javascript
    assert "音频" in javascript
    assert "视觉" in javascript
    assert "成片" in javascript
    assert "验收" in javascript
    assert "发布" in javascript
    assert "reportWorkflowRenderMode(job)" in javascript
    assert "reportWorkflowOutputSummary(job)" in javascript
    assert "`${this.verticalSchemeLabel(scheme)}竖版关键帧`" in javascript
    assert 'aria-label="任务流程进度"' in javascript


def test_detail_orchestration_status_distinguishes_reuse_waiting_and_attention():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js"
    ).read_text(encoding="utf-8")

    assert "reused:" in javascript
    assert "waiting:" in javascript
    assert "needs_attention:" in javascript
    assert "已复用" in javascript
    assert "等待" in javascript
    assert "需要处理" in javascript
    assert "orchestrationNotice(job)" in javascript
    assert "当前活动数" in javascript
    assert "next_retry_at" in javascript
    assert "缺少或未通过" in javascript


def test_failed_detail_exposes_resume_action_using_existing_retry_endpoint():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js"
    ).read_text(encoding="utf-8")

    assert "data-detail-action=\"retry-workflow\"" in javascript
    assert "从失败处续跑" in javascript
    assert "/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}/retry" in javascript
    assert "this.retryRequestIds.has(jobId)" in javascript


def test_audio_script_exposes_independent_pro_regeneration_action():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js"
    ).read_text(encoding="utf-8")
    api = (
        PROJECT_ROOT / "src/backend/api/report_audio_workflow.py"
    ).read_text(encoding="utf-8")

    assert 'data-detail-action="regenerate-script-pro"' in javascript
    assert "Pro 重写 · 至少 1500 字" in javascript
    assert "/regenerate-script" in javascript
    assert "原文案、音频和已生成视频都不会被覆盖" in javascript
    assert '"force_gemini_regeneration": True' in api
    assert '"gemini_require_pro_model": True' in api
    assert '"gemini_min_final_chars": minimum_chars' in api


def test_audio_script_exposes_independent_tts_continuation_action():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js"
    ).read_text(encoding="utf-8")
    api = (
        PROJECT_ROOT / "src/backend/api/report_audio_workflow.py"
    ).read_text(encoding="utf-8")

    assert 'data-detail-action="generate-audio-from-script"' in javascript
    assert "用此文案生成语音" in javascript
    assert "/generate-audio" in javascript
    assert "不会重新请求 Gemini" in javascript
    assert 'AUDIO_GENERATION_PARENT_FEATURE = "report_audio_script_tts_generation"' in api
    assert '"structured_result": structured' in api
    assert '"script_material_only": False' in api
    assert '"force_gemini_regeneration": False' in api


def test_failed_bilibili_upload_exposes_safe_retry_action():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/bilibili-publish-panel.js"
    ).read_text(encoding="utf-8")
    detail = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js"
    ).read_text(encoding="utf-8")
    api = (
        PROJECT_ROOT / "src/backend/api/report_audio_workflow.py"
    ).read_text(encoding="utf-8")
    service = (
        PROJECT_ROOT / "src/backend/services/bilibili_publish.py"
    ).read_text(encoding="utf-8")

    assert "data-bili-shared-retry" in javascript
    assert "只有失败的 B站上传任务可以重投" in service
    assert "retry_bilibili_upload_child" in api
    assert "onAfterRetry" in javascript
    assert "retryPath: (jobId, uploadTaskId)" in detail


def test_sticky_sidebar_exposes_shared_status_and_refresh_controls():
    html = (
        PROJECT_ROOT / "src/frontend/report-audio-workflow-detail.html"
    ).read_text(encoding="utf-8")
    javascript = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js"
    ).read_text(encoding="utf-8")

    assert 'id="sidebar-task-status"' in html
    assert 'id="sidebar-refresh-btn"' in html
    assert 'max-h-[calc(100vh-7rem)]' in html
    assert "this.els.sidebarRefreshBtn?.addEventListener('click', () => this.load({ manual: true }))" in javascript
    assert "renderSidebarStatus(job)" in javascript
    assert "const stages = this.reportWorkflowStages(job)" in javascript
    assert 'aria-label="左侧任务流程状态"' in javascript
    assert "最近刷新" in javascript


def test_backend_cors_allows_patch_for_evidence_editor():
    app_source = (PROJECT_ROOT / "src/backend/app.py").read_text(encoding="utf-8")

    assert '"PATCH"' in app_source


def test_evidence_panel_distinguishes_persistence_failure_from_legacy_task():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/report-pdf-evidence-panel.js"
    ).read_text(encoding="utf-8")
    api_source = (
        PROJECT_ROOT / "src/backend/api/report_pdf_evidence.py"
    ).read_text(encoding="utf-8")

    assert "persistence_failed" in javascript
    assert "证据已经生成，但数据库保存失败" in javascript
    assert "generation_failed" in javascript
    assert "_orphaned_artifact_summary" in api_source
    assert '"legacy_unavailable"' in api_source
    assert "explicitly_disabled" in api_source
    assert "本任务未启用 PDF 证据映射" in javascript
    html = (
        PROJECT_ROOT / "src/frontend/report-audio-workflow-detail.html"
    ).read_text(encoding="utf-8")
    assert "report-pdf-evidence-panel.js?v=20260809-evidence-disabled-v1" in html
    assert 'data-evidence-action="preview-image"' in javascript
    assert 'data-evidence-action="download-image"' in javascript
    assert "showImagePreview" in javascript
    assert "downloadImage" in javascript


def test_evidence_panel_explains_bulk_selection_is_a_candidate_pool():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/report-pdf-evidence-panel.js"
    ).read_text(encoding="utf-8")
    api_source = (
        PROJECT_ROOT / "src/backend/api/report_pdf_evidence.py"
    ).read_text(encoding="utf-8")

    assert "全部加入证据候选池" in javascript
    assert "不代表每张候选图都会进入最终成片" in javascript
    assert "select_all_visuals" in javascript
    assert "select_all_visuals" in api_source
    assert "manual_confirmed" in api_source
