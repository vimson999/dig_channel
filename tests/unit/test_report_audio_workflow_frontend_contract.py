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

    assert "updateGenerationModeUi()" in javascript
    assert "renderMode === 'keyframes' ? this.selectedKeyframeOutputs() : []" in javascript
    assert "renderMode === 'full_video' && Boolean(this.els.landscapeVideoEnabled?.checked)" in javascript
    assert "form.append('keyframe_outputs', JSON.stringify(keyframeOutputs))" in javascript
    assert "PDF_EVIDENCE_V1_JSON" in javascript
    assert '"schema_version":"report_pdf_evidence_v1"' in javascript


def test_keyframe_confirmation_preserves_landscape_selection_and_busts_cache():
    javascript = (
        PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js"
    ).read_text(encoding="utf-8")
    html = (
        PROJECT_ROOT / "src/frontend/report-audio-workflow-detail.html"
    ).read_text(encoding="utf-8")

    assert "include_landscape: options.includeLandscape === true" in javascript
    assert "selectionSource: 'keyframe-confirmation'" in javascript
    assert "selection_source: options.selectionSource || 'vertical-video-panel'" in javascript
    assert "report-audio-workflow-detail.js?v=20260729-sticky-sidebar-status-v7" in html


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
    assert "PDF 准备" in javascript
    assert "Gemini 解析" in javascript
    assert "文案整理" in javascript
    assert "语音生成" in javascript
    assert "视频生成" in javascript
    assert "关键帧/封皮" in javascript
    assert "reportWorkflowRenderMode(job)" in javascript
    assert "reportWorkflowOutputSummary(job)" in javascript
    assert "keyframeMode ? '关键帧/封皮' : '视频生成'" in javascript
    assert "`${this.verticalSchemeLabel(scheme)}竖版关键帧`" in javascript
    assert "任务完成" in javascript
    assert 'aria-label="任务流程进度"' in javascript


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
    html = (
        PROJECT_ROOT / "src/frontend/report-audio-workflow-detail.html"
    ).read_text(encoding="utf-8")
    assert "report-pdf-evidence-panel.js?v=20260729-evidence-preview-v6" in html
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
