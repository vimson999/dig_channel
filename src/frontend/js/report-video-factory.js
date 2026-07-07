const ReportVideoApp = {
    API_BASE: window.API_BASE || 'http://localhost:5001/api/v1',
    tasks: [],
    activeTaskId: null,
    activeTab: 'reports',
    selectedTopicId: null,
    selectedPromptId: null,
    draggedReportId: null,
    refreshTimer: null,
    toastTimer: null,
    autoScan: null,
    assetScheduler: null,
    prompts: [],
    flowGraph: null,
    storyboardRatio: '16:9',

    els: {
        taskForm: document.getElementById('task-form'),
        taskName: document.getElementById('task-name'),
        videoType: document.getElementById('video-type'),
        segmentCount: document.getElementById('segment-count'),
        videoStyle: document.getElementById('video-style'),
        generateSubtitles: document.getElementById('generate-subtitles'),
        taskList: document.getElementById('task-list'),
        refreshTasks: document.getElementById('refresh-tasks'),
        emptyState: document.getElementById('empty-state'),
        workbench: document.getElementById('task-workbench'),
        activeTaskName: document.getElementById('active-task-name'),
        activeTaskMeta: document.getElementById('active-task-meta'),
        activeTaskStatus: document.getElementById('active-task-status'),
        pdfInput: document.getElementById('pdf-input'),
        pdfFileSummary: document.getElementById('pdf-file-summary'),
        uploadPdfBtn: document.getElementById('upload-pdf-btn'),
        reportList: document.getElementById('report-list'),
        reportSummary: document.getElementById('report-summary'),
        generateTopicCardsBtn: document.getElementById('generate-topic-cards-btn'),
        generateTopicClustersBtn: document.getElementById('generate-topic-clusters-btn'),
        topicCardList: document.getElementById('topic-card-list'),
        topicCardSummary: document.getElementById('topic-card-summary'),
        topicRankList: document.getElementById('topic-rank-list'),
        topicRankSummary: document.getElementById('topic-rank-summary'),
        topicDetailSummary: document.getElementById('topic-detail-summary'),
        topicDetailPanel: document.getElementById('topic-detail-panel'),
        generateTopicScriptBtn: document.getElementById('generate-topic-script-btn'),
        applyTopicScriptBtn: document.getElementById('apply-topic-script-btn'),
        generateAiBtn: document.getElementById('generate-ai-btn'),
        scriptList: document.getElementById('script-list'),
        scriptSummary: document.getElementById('script-summary'),
        mindmapList: document.getElementById('mindmap-list'),
        mindmapSummary: document.getElementById('mindmap-summary'),
        ttsScriptList: document.getElementById('tts-script-list'),
        ttsScriptSummary: document.getElementById('tts-script-summary'),
        submitDoubaoTtsBtn: document.getElementById('submit-doubao-tts-btn'),
        syncDoubaoTtsBtn: document.getElementById('sync-doubao-tts-btn'),
        doubaoTtsStatus: document.getElementById('doubao-tts-status'),
        copyAllTtsBtn: document.getElementById('copy-all-tts-btn'),
        copyTtsManifestBtn: document.getElementById('copy-tts-manifest-btn'),
        audioInput: document.getElementById('audio-input'),
        timelineInput: document.getElementById('timeline-input'),
        timelineTextInput: document.getElementById('timeline-text-input'),
        forceAsrTimeline: document.getElementById('force-asr-timeline'),
        audioSpeedSelect: document.getElementById('audio-speed-select'),
        audioFileSummary: document.getElementById('audio-file-summary'),
        timelineFileSummary: document.getElementById('timeline-file-summary'),
        uploadAudioBtn: document.getElementById('upload-audio-btn'),
        audioList: document.getElementById('audio-list'),
        audioSummary: document.getElementById('audio-summary'),
        generateVideoBtn: document.getElementById('generate-video-btn'),
        generateRemotionVideoBtn: document.getElementById('generate-remotion-video-btn'),
        videoOutputList: document.getElementById('video-output-list'),
        videoSummary: document.getElementById('video-summary'),
        generateKeyframesBtn: document.getElementById('generate-keyframes-btn'),
        approveKeyframesBtn: document.getElementById('approve-keyframes-btn'),
        rejectKeyframesBtn: document.getElementById('reject-keyframes-btn'),
        generatePreviewClipBtn: document.getElementById('generate-preview-clip-btn'),
        approvePreviewClipBtn: document.getElementById('approve-preview-clip-btn'),
        rejectPreviewClipBtn: document.getElementById('reject-preview-clip-btn'),
        renderPreviewSummary: document.getElementById('render-preview-summary'),
        renderPreviewList: document.getElementById('render-preview-list'),
        autoScanEnabled: document.getElementById('auto-scan-enabled'),
        autoScanDir: document.getElementById('auto-scan-dir'),
        autoScanTime: document.getElementById('auto-scan-time'),
        autoScanInterval: document.getElementById('auto-scan-interval'),
        autoScanMaxFiles: document.getElementById('auto-scan-max-files'),
        autoScanSegmentCount: document.getElementById('auto-scan-segment-count'),
        autoScanRatio16: document.getElementById('auto-scan-ratio-16'),
        autoScanRatio9: document.getElementById('auto-scan-ratio-9'),
        saveAutoScanBtn: document.getElementById('save-auto-scan-btn'),
        previewAutoScanBtn: document.getElementById('preview-auto-scan-btn'),
        runAutoScanBtn: document.getElementById('run-auto-scan-btn'),
        autoScanSummary: document.getElementById('auto-scan-summary'),
        autoScanList: document.getElementById('auto-scan-list'),
        assetSchedulerEnabled: document.getElementById('asset-scheduler-enabled'),
        assetSchedulerTime: document.getElementById('asset-scheduler-time'),
        assetSchedulerInterval: document.getElementById('asset-scheduler-interval'),
        assetSchedulerDateOffset: document.getElementById('asset-scheduler-date-offset'),
        assetSchedulerMaxFiles: document.getElementById('asset-scheduler-max-files'),
        assetSchedulerTabKeyword: document.getElementById('asset-scheduler-tab-keyword'),
        assetSchedulerParsePdfs: document.getElementById('asset-scheduler-parse-pdfs'),
        assetSchedulerSaveBtn: document.getElementById('asset-scheduler-save-btn'),
        assetSchedulerStartBtn: document.getElementById('asset-scheduler-start-btn'),
        assetSchedulerStopBtn: document.getElementById('asset-scheduler-stop-btn'),
        assetSchedulerRefreshBtn: document.getElementById('asset-scheduler-refresh-btn'),
        assetSchedulerRunOnceBtn: document.getElementById('asset-scheduler-run-once-btn'),
        assetSchedulerLogsBtn: document.getElementById('asset-scheduler-logs-btn'),
        assetSchedulerBadge: document.getElementById('asset-scheduler-badge'),
        assetSchedulerStatus: document.getElementById('asset-scheduler-status'),
        assetSchedulerLogs: document.getElementById('asset-scheduler-logs'),
        refreshPromptsBtn: document.getElementById('refresh-prompts-btn'),
        promptList: document.getElementById('prompt-list'),
        promptEditorTitle: document.getElementById('prompt-editor-title'),
        promptEditorMeta: document.getElementById('prompt-editor-meta'),
        promptSystemInput: document.getElementById('prompt-system-input'),
        promptUserInput: document.getElementById('prompt-user-input'),
        savePromptBtn: document.getElementById('save-prompt-btn'),
        resetPromptBtn: document.getElementById('reset-prompt-btn'),
        textModal: document.getElementById('text-modal'),
        textModalTitle: document.getElementById('text-modal-title'),
        textModalMeta: document.getElementById('text-modal-meta'),
        textModalBody: document.getElementById('text-modal-body'),
        closeTextModal: document.getElementById('close-text-modal'),
        storyboardPanel: document.getElementById('report-video-storyboard'),
        storyboardSummary: document.getElementById('storyboard-summary'),
        storyboardFrameList: document.getElementById('storyboard-frame-list'),
        storyboardRatioButtons: document.querySelectorAll('[data-storyboard-ratio]')
    },

    init() {
        this.activeTaskId = this.initialTaskId();
        this.initFlowGraph();
        this.bindEvents();
        this.renderStoryboard(null);
        this.updateAudioModeUi();
        this.loadTasks();
        this.loadAutoScan();
        this.loadAssetScheduler();
        this.loadPrompts();
        this.refreshTimer = window.setInterval(() => this.refreshIfBusy(), 5000);
    },

    initFlowGraph() {
        const root = document.getElementById('report-video-flow-map');
        if (!root || !window.ShioaThreeGraph) return;
        this.flowGraph = window.ShioaThreeGraph.mount(root, {
            kicker: 'Video Pipeline',
            title: '研报视频制作链路',
            subtitle: 'PDF、主题、脚本、导图、音频和视频输出的当前连接。',
        });
        this.updateFlowGraph();
    },

    updateFlowGraph() {
        if (!this.flowGraph) return;
        this.flowGraph.update(this.buildFlowGraphModel());
    },

    buildFlowGraphModel() {
        const task = this.getActiveTask();
        const reports = task?.reports || [];
        const planning = task?.result?.topic_planning || {};
        const rankedTopics = planning.ranked_topics || [];
        const audios = task?.audios || [];
        const outputs = task?.outputs || [];
        const scriptReady = reports.filter(report => report.analysis || (report.segments || []).length);
        const mindmapReady = reports.filter(report => report.mindmap);
        const ttsRows = this.buildTtsRows(reports);
        const nodes = [
            { id: 'task', type: 'stage', label: task?.task_name || '选择任务', value: task ? 7 : 3, status: task ? this.graphStatus(task.status) : 'idle' },
            { id: 'pdf', type: 'attachment', label: `PDF ${reports.length}`, value: Math.max(2, reports.length), status: reports.length ? 'success' : 'idle' },
            { id: 'topic', type: 'topic', label: `主题 ${rankedTopics.length}`, value: Math.max(2, rankedTopics.length), status: rankedTopics.length ? 'success' : 'idle' },
            { id: 'script', type: 'summary', label: `文案 ${scriptReady.length}`, value: Math.max(2, scriptReady.length), status: scriptReady.length ? 'success' : 'idle' },
            { id: 'mindmap', type: 'tab', label: `导图 ${mindmapReady.length}`, value: Math.max(2, mindmapReady.length), status: mindmapReady.length ? 'success' : 'idle' },
            { id: 'tts', type: 'audio', label: `TTS ${ttsRows.length}`, value: Math.max(2, ttsRows.length / 2), status: ttsRows.length ? 'success' : 'idle' },
            { id: 'audio', type: 'audio', label: `音频 ${audios.length}`, value: Math.max(2, audios.length), status: audios.length ? 'success' : 'idle' },
            { id: 'video', type: 'video', label: `视频 ${outputs.length}`, value: Math.max(2, outputs.length), status: outputs.some(output => output.status === 'completed') ? 'success' : (outputs.length ? 'running' : 'idle') },
        ];
        const links = [
            { source: 'task', target: 'pdf' },
            { source: 'pdf', target: 'topic' },
            { source: 'topic', target: 'script' },
            { source: 'script', target: 'mindmap' },
            { source: 'script', target: 'tts' },
            { source: 'tts', target: 'audio' },
            { source: 'audio', target: 'video' },
            { source: 'mindmap', target: 'video' },
        ];

        reports.slice(0, 8).forEach(report => {
            const id = `report:${report.id}`;
            nodes.push({
                id,
                type: 'attachment',
                label: report.report_code || report.file_name || 'PDF',
                value: report.page_count ? Math.min(8, 2 + report.page_count / 20) : 3,
                status: this.graphStatus(report.parse_status),
            });
            links.push({ source: 'pdf', target: id, opacity: 0.3 });
            if (report.analysis || (report.segments || []).length) {
                const scriptId = `script:${report.id}`;
                nodes.push({
                    id: scriptId,
                    type: 'summary',
                    label: report.analysis?.report_title || report.report_code || 'AI 文案',
                    value: Math.max(3, (report.segments || []).length),
                    status: 'success',
                });
                links.push({ source: id, target: scriptId, opacity: 0.35 });
            }
            if (report.mindmap) {
                const mapId = `mindmap:${report.id}`;
                nodes.push({
                    id: mapId,
                    type: 'tab',
                    label: report.mindmap.center || report.report_code || '导图',
                    value: Math.max(3, (report.mindmap.nodes || []).length / 2),
                    status: 'success',
                });
                links.push({ source: id, target: mapId, opacity: 0.35 });
            }
        });

        rankedTopics.slice(0, 6).forEach(topic => {
            const id = `topic:${topic.topic_id || topic.rank}`;
            nodes.push({
                id,
                type: 'topic',
                label: topic.topic_name || `主题 ${topic.rank || ''}`,
                value: Math.min(8, 2 + Number(topic.final_score || 0) / 20),
                status: topic.topic_id === this.selectedTopicId ? 'success' : 'idle',
            });
            links.push({ source: 'topic', target: id, opacity: 0.28 });
        });

        audios.slice(0, 6).forEach(audio => {
            const id = `audio:${audio.id}`;
            nodes.push({
                id,
                type: 'audio',
                label: audio.file_name || '音频',
                value: 3,
                status: ['matched', 'timeline_matched'].includes(audio.match_status) ? 'success' : 'warning',
            });
            links.push({ source: 'audio', target: id, opacity: 0.28 });
        });

        outputs.slice(0, 6).forEach(output => {
            const id = `output:${output.id}`;
            nodes.push({
                id,
                type: 'video',
                label: `${output.ratio || ''} ${output.video_type || '视频'}`.trim(),
                value: 4,
                status: this.graphStatus(output.status),
            });
            links.push({ source: 'video', target: id, opacity: 0.3 });
        });

        return {
            title: task?.task_name || '研报导图视频工厂',
            subtitle: task
                ? `${this.formatStatus(task.status)} · ${reports.length} 篇 PDF · ${ttsRows.length} 段 TTS`
                : '创建或选择任务后生成制作链路',
            metrics: [
                { label: '任务', value: this.tasks.length },
                { label: 'PDF', value: reports.length },
                { label: '文案', value: scriptReady.length },
                { label: '视频', value: outputs.length },
            ],
            legend: [
                { label: 'PDF', color: window.ShioaThreeGraph.colors.attachment },
                { label: '主题', color: window.ShioaThreeGraph.colors.topic },
                { label: 'AI 文案', color: window.ShioaThreeGraph.colors.summary },
                { label: '导图', color: window.ShioaThreeGraph.colors.tab },
                { label: '音频', color: window.ShioaThreeGraph.colors.audio },
                { label: '视频', color: window.ShioaThreeGraph.colors.video },
            ],
            nodes,
            links,
        };
    },

    renderStoryboard(task) {
        if (!this.els.storyboardFrameList) return;
        this.els.storyboardRatioButtons?.forEach(button => {
            button.classList.toggle('active', button.dataset.storyboardRatio === this.storyboardRatio);
        });
        this.els.storyboardFrameList.classList.toggle('is-vertical', this.storyboardRatio === '9:16');

        if (!task) {
            this.els.storyboardSummary.textContent = '0 帧';
            this.els.storyboardFrameList.innerHTML = '<div class="rv-empty">选择任务并生成文案后会显示视频内容帧。</div>';
            return;
        }
        if (task.summary_only) {
            this.els.storyboardSummary.textContent = '加载中';
            this.els.storyboardFrameList.innerHTML = '<div class="rv-empty">正在加载任务详情...</div>';
            return;
        }
        const keyframePreview = this.latestRenderPreview(task, 'keyframes', this.storyboardRatio);
        if (
            keyframePreview
            && ['completed', 'approved'].includes(keyframePreview.status)
            && (keyframePreview.file_paths || []).length
        ) {
            this.renderStoryboardKeyframes(keyframePreview);
            return;
        }

        const frames = this.buildStoryboardFrames(task);
        if (!frames.length) {
            this.els.storyboardSummary.textContent = '0 帧';
            this.els.storyboardFrameList.innerHTML = '<div class="rv-empty">还没有可绘制的脚本段落。先生成主题脚本或单篇 AI 文案。</div>';
            return;
        }

        const totalDuration = frames.reduce((sum, frame) => sum + Number(frame.duration || 0), 0);
        this.els.storyboardSummary.textContent = `${frames.length} 帧草图 · ${this.storyboardRatio}${totalDuration ? ` · ${this.formatDuration(totalDuration)}` : ''}`;
        this.els.storyboardFrameList.innerHTML = frames
            .slice(0, 12)
            .map((frame, index) => this.renderStoryboardFrame(frame, index))
            .join('');
    },

    renderStoryboardKeyframes(preview) {
        const files = preview.file_paths || [];
        const milestones = preview.meta?.milestones || [];
        this.els.storyboardSummary.textContent = `${files.length} 张真实关键帧 · ${preview.ratio}`;
        this.els.storyboardFrameList.innerHTML = files.map((_, index) => {
            const milestone = milestones[index] || {};
            const caption = milestone.label || `关键帧 ${index + 1}`;
            const href = `${this.API_BASE}/report-video/previews/${preview.id}/files/${index}`;
            return `
                <article class="rv-story-real-frame">
                    <a href="${href}" target="_blank" rel="noopener" title="打开大图">
                        <img src="${href}" alt="${this.escape(caption)}">
                    </a>
                    <div class="rv-story-frame-meta">
                        <span>${this.escape(String(index + 1).padStart(3, '0'))}</span>
                        <span>${this.escape(caption)}</span>
                    </div>
                </article>
            `;
        }).join('');
    },

    buildStoryboardFrames(task) {
        if (!task || task.summary_only) return [];
        const planning = task.result?.topic_planning || {};
        const topic = this.resolveStoryboardTopic(planning);
        const topicScripts = planning.topic_scripts || {};
        const topicScript = topic ? topicScripts[topic.topic_id] : null;
        if (topic && topicScript && Array.isArray(topicScript.sections) && topicScript.sections.length) {
            return this.buildTopicStoryboardFrames(task, planning, topic, topicScript);
        }

        const reportFrames = this.buildReportStoryboardFrames(task.reports || []);
        if (reportFrames.length) return reportFrames;

        return this.buildPlanningStoryboardFrames(planning);
    },

    resolveStoryboardTopic(planning) {
        const topics = Array.isArray(planning.topics) ? planning.topics : [];
        const rankedTopics = Array.isArray(planning.ranked_topics) ? planning.ranked_topics : [];
        const topicMap = new Map(topics.map(topic => [topic.topic_id, topic]));
        const preferredId = this.selectedTopicId || planning.applied_topic_id || rankedTopics[0]?.topic_id || topics[0]?.topic_id;
        if (preferredId && topicMap.has(preferredId)) return topicMap.get(preferredId);
        if (preferredId) return rankedTopics.find(topic => topic.topic_id === preferredId) || null;
        return rankedTopics[0] || null;
    },

    buildTopicStoryboardFrames(task, planning, topic, script) {
        const sections = Array.isArray(script.sections) ? script.sections : [];
        const cards = Array.isArray(planning.report_cards) ? planning.report_cards : [];
        const cardMap = new Map(cards.map(card => [card.report_id || card.report_code, card]));
        const supportingCards = (topic.reports || []).map(id => cardMap.get(id)).filter(Boolean);
        const themeBullets = [
            topic.topic_summary,
            ...(topic.common_views || []),
            ...(topic.conflicting_views || []).map(item => `风险：${item}`),
        ].filter(Boolean);
        const frames = [{
            kind: 'opening',
            source: '主题脚本',
            title: script.video_title || topic.topic_name || '主题视频',
            subtitle: topic.recommended_video_angle || topic.topic_summary || script.opening_text || '',
            voiceText: script.opening_text || topic.topic_summary || '',
            bullets: themeBullets,
            nodes: supportingCards.map(card => card.institution || card.report_id).filter(Boolean),
            duration: 8,
            score: topic.final_score ?? topic.internal_heat_score,
        }];

        sections.forEach((section, index) => {
            const sourceCard = supportingCards[index % Math.max(1, supportingCards.length)] || {};
            frames.push({
                kind: 'topic-section',
                source: sourceCard.report_id || sourceCard.report_code || '主题脚本',
                title: section.section_title || section.segment_title || `${this.formatChineseOrdinal(index + 1)}段`,
                subtitle: section.key_visual || sourceCard.report_title || topic.topic_name || '',
                voiceText: section.voice_text || '',
                bullets: (section.bullet_points || []).concat(themeBullets).filter(Boolean),
                focusNodeId: section.focus_node_id,
                nodes: [
                    sourceCard.institution,
                    ...(section.bullet_points || []),
                ].filter(Boolean),
                duration: section.estimated_duration_sec || section.duration_sec || 18,
                score: topic.final_score ?? sourceCard.video_value_score,
            });
        });
        return frames;
    },

    buildReportStoryboardFrames(reports) {
        const segmentFrames = reports.flatMap(report => {
            const analysis = report.analysis || {};
            const segments = Array.isArray(report.segments) ? report.segments : [];
            if (!segments.length) return [];
            return segments.map((segment, index) => {
                const focus = this.findMindmapNode(report, segment.focus_node_id);
                const nodes = this.collectMindmapLabels(report, segment.focus_node_id);
                return {
                    kind: 'segment',
                    source: report.report_code || report.file_name || '研报',
                    title: segment.segment_title || analysis.report_title || report.file_name || `第 ${index + 1} 段`,
                    subtitle: focus?.label || analysis.core_view || analysis.theme || '',
                    voiceText: segment.voice_text || '',
                    bullets: [
                        focus?.label,
                        ...(analysis.key_points || []),
                    ].filter(Boolean),
                    focusNodeId: segment.focus_node_id,
                    nodes,
                    duration: segment.estimated_duration_sec || segment.duration_sec,
                    score: analysis.video_value_score,
                };
            });
        });
        if (segmentFrames.length) return segmentFrames;
        return reports
            .filter(report => report.file_name || report.report_code)
            .slice(0, 8)
            .map(report => {
                const extractionMethod = report.raw_meta?.extraction_method || report.raw_meta?.scan_stats?.extraction_method || report.parse_status;
                return {
                    kind: 'pdf-material',
                    source: report.report_code || 'PDF',
                    title: String(report.file_name || report.report_code || '研报素材').replace(/\.pdf$/i, ''),
                    subtitle: `${report.page_count || 0} 页 · 清洗后 ${report.cleaned_text_chars || 0} 字符`,
                    voiceText: `${report.report_code || 'PDF'} · ${report.page_count || 0} 页 · ${this.formatExtractionMethod(extractionMethod)}`,
                    bullets: [
                        this.formatStatus(report.parse_status),
                        report.is_scanned ? '扫描版' : '文本型',
                        report.cleaned_text_chars ? `${report.cleaned_text_chars} 字符` : null,
                    ].filter(Boolean),
                    nodes: [
                        report.report_code,
                        this.formatExtractionMethod(extractionMethod),
                        report.page_count ? `${report.page_count} 页` : null,
                    ].filter(Boolean),
                    duration: 6,
                };
            });
    },

    buildPlanningStoryboardFrames(planning) {
        const rankedTopics = Array.isArray(planning.ranked_topics) ? planning.ranked_topics : [];
        const cards = Array.isArray(planning.report_cards) ? planning.report_cards : [];
        if (rankedTopics.length) {
            return rankedTopics.slice(0, 8).map(topic => ({
                kind: 'topic-rank',
                source: `${topic.report_count || 0} 篇研报`,
                title: topic.topic_name || '主题',
                subtitle: (topic.institutions || []).slice(0, 4).join('、') || topic.topic_category || '',
                voiceText: topic.topic_summary || topic.recommended_video_angle || '',
                bullets: [topic.reason, topic.topic_category].filter(Boolean),
                nodes: topic.institutions || [],
                duration: 12,
                score: topic.final_score,
            }));
        }
        return cards.slice(0, 8).map(card => ({
            kind: 'report-card',
            source: card.report_id || card.report_code || '摘要卡',
            title: card.report_title || card.file_name || '研报摘要',
            subtitle: card.institution || card.primary_theme || '',
            voiceText: card.one_sentence_summary || card.core_view || '',
            bullets: card.theme_tags || card.key_points || [],
            nodes: [card.institution, card.primary_theme].filter(Boolean),
            duration: 10,
            score: card.video_value_score,
        }));
    },

    renderStoryboardFrame(frame, index) {
        const ratioClass = this.storyboardRatio === '9:16' ? 'is-vertical' : 'is-wide';
        const toneClass = `tone-${index % 5}`;
        const frameNo = String(index + 1).padStart(3, '0');
        const title = this.excerptText(frame.title || '视频帧', this.storyboardRatio === '9:16' ? 24 : 34);
        const subtitle = this.excerptText(frame.subtitle || frame.voiceText || '', this.storyboardRatio === '9:16' ? 42 : 72);
        const voiceText = this.excerptText(frame.voiceText || frame.subtitle || '', this.storyboardRatio === '9:16' ? 58 : 88);
        const bullets = (frame.bullets || []).filter(Boolean).slice(0, 3);
        return `
            <article class="rv-story-frame ${ratioClass} ${toneClass}">
                <div class="rv-video-frame">
                    <div class="rv-video-frame-top">
                        <span>FRAME ${frameNo}</span>
                        <span>${this.escape(frame.source || '-')}</span>
                    </div>
                    <div class="rv-video-frame-stage">
                        <div class="rv-video-frame-copy">
                            <em>${this.escape(frame.kind === 'opening' ? 'OPENING' : (frame.focusNodeId || frame.kind || 'SEGMENT'))}</em>
                            <strong>${this.escape(title)}</strong>
                            <p>${this.escape(subtitle)}</p>
                        </div>
                        ${this.renderFrameVisual(frame, index)}
                    </div>
                    <div class="rv-video-frame-bottom">
                        <p>${this.escape(voiceText || '等待口播文本')}</p>
                        <span>${this.escape(this.formatDuration(frame.duration))}</span>
                    </div>
                </div>
                <div class="rv-story-frame-meta">
                    <span>${this.escape(frame.score != null ? `价值 ${frame.score}` : (frame.focusNodeId || frame.kind || '-'))}</span>
                    <span>${bullets.map(item => this.escape(this.excerptText(item, 18))).join(' · ') || '内容帧'}</span>
                </div>
            </article>
        `;
    },

    renderFrameVisual(frame, index) {
        const nodes = (frame.nodes || frame.bullets || []).filter(Boolean).slice(0, 5);
        const bars = [0, 1, 2, 3].map(offset => {
            const value = 34 + ((index + 2 + offset) * 17) % 52;
            return `<i style="--bar:${value}%"></i>`;
        }).join('');
        const nodeMarkup = nodes.length
            ? nodes.map((node, nodeIndex) => `<b class="node-${nodeIndex}">${this.escape(this.excerptText(node, 12))}</b>`).join('')
            : '<b class="node-0">观点</b><b class="node-1">数据</b><b class="node-2">风险</b>';
        return `
            <div class="rv-video-frame-visual">
                <div class="rv-frame-node-map">${nodeMarkup}</div>
                <div class="rv-frame-bars">${bars}</div>
            </div>
        `;
    },

    findMindmapNode(report, nodeId) {
        if (!nodeId) return null;
        const nodes = report.mindmap?.nodes || [];
        return nodes.find(node => String(node.id) === String(nodeId)) || null;
    },

    collectMindmapLabels(report, focusNodeId) {
        const nodes = report.mindmap?.nodes || [];
        if (!nodes.length) return [];
        const focus = this.findMindmapNode(report, focusNodeId);
        const related = nodes.filter(node => {
            if (!focusNodeId) return node.id !== 'root';
            return String(node.parent_id || node.parent || '') === String(focusNodeId) || String(node.id) === String(focusNodeId);
        });
        return [focus, ...related]
            .filter(Boolean)
            .map(node => node.label || node.title || node.id)
            .filter(Boolean)
            .slice(0, 5);
    },

    excerptText(value, maxLength = 80) {
        const text = String(value || '').replace(/\s+/g, ' ').trim();
        if (!text) return '';
        return text.length > maxLength ? `${text.slice(0, Math.max(0, maxLength - 1))}…` : text;
    },

    graphStatus(status) {
        const value = String(status || '').toLowerCase();
        if (['completed', 'succeeded', 'parsed', 'matched', 'timeline_matched'].includes(value)) return 'success';
        if (['failed', 'error', 'parse_failed', 'unmatched'].includes(value)) return 'failed';
        if (['processing', 'running', 'queued', 'pending'].includes(value)) return 'running';
        return 'idle';
    },

    bindEvents() {
        this.els.taskForm?.addEventListener('submit', (event) => {
            event.preventDefault();
            this.createTask();
        });
        this.els.refreshTasks?.addEventListener('click', () => this.loadTasks());
        this.els.uploadPdfBtn?.addEventListener('click', () => this.uploadReports());
        this.els.pdfInput?.addEventListener('change', () => this.updatePdfFileSummary());
        this.els.generateTopicCardsBtn?.addEventListener('click', () => this.generateTopicCards());
        this.els.generateTopicClustersBtn?.addEventListener('click', () => this.generateTopicClusters());
        this.els.generateTopicScriptBtn?.addEventListener('click', () => this.generateTopicScript());
        this.els.applyTopicScriptBtn?.addEventListener('click', () => this.applyTopicScript());
        this.els.generateAiBtn?.addEventListener('click', () => this.generateAiScripts());
        this.els.submitDoubaoTtsBtn?.addEventListener('click', () => this.submitDoubaoTts());
        this.els.syncDoubaoTtsBtn?.addEventListener('click', () => this.syncDoubaoTts());
        this.els.copyAllTtsBtn?.addEventListener('click', () => this.copyAllTtsScripts());
        this.els.copyTtsManifestBtn?.addEventListener('click', () => this.copyTtsManifest());
        this.els.audioInput?.addEventListener('change', () => this.updateAudioFileSummary());
        this.els.timelineInput?.addEventListener('change', () => this.updateTimelineFileSummary());
        document.querySelectorAll('input[name="audio-mode"]').forEach(input => {
            input.addEventListener('change', () => this.updateAudioModeUi());
        });
        this.els.uploadAudioBtn?.addEventListener('click', () => this.uploadAudios());
        this.els.generateKeyframesBtn?.addEventListener('click', () => this.generateRenderPreview('keyframes'));
        this.els.approveKeyframesBtn?.addEventListener('click', () => this.approveRenderPreview('keyframes'));
        this.els.rejectKeyframesBtn?.addEventListener('click', () => this.rejectRenderPreview('keyframes'));
        this.els.generatePreviewClipBtn?.addEventListener('click', () => this.generateRenderPreview('clip'));
        this.els.approvePreviewClipBtn?.addEventListener('click', () => this.approveRenderPreview('clip'));
        this.els.rejectPreviewClipBtn?.addEventListener('click', () => this.rejectRenderPreview('clip'));
        this.els.generateVideoBtn?.addEventListener('click', () => this.generateVideos());
        this.els.generateRemotionVideoBtn?.addEventListener('click', () => this.generateRemotionVideos());
        this.els.renderPreviewList?.addEventListener('click', (event) => {
            const button = event.target.closest('[data-generate-milestone-clip]');
            if (!button) return;
            this.generateMilestoneClip(button.dataset.previewId, Number(button.dataset.milestoneIndex || 0), button);
        });
        this.els.saveAutoScanBtn?.addEventListener('click', () => this.saveAutoScanConfig());
        this.els.previewAutoScanBtn?.addEventListener('click', () => this.previewAutoScan());
        this.els.runAutoScanBtn?.addEventListener('click', () => this.runAutoScan());
        this.els.assetSchedulerSaveBtn?.addEventListener('click', () => this.saveAssetSchedulerConfig());
        this.els.assetSchedulerStartBtn?.addEventListener('click', () => this.startAssetScheduler());
        this.els.assetSchedulerStopBtn?.addEventListener('click', () => this.stopAssetScheduler());
        this.els.assetSchedulerRefreshBtn?.addEventListener('click', () => this.loadAssetScheduler());
        this.els.assetSchedulerRunOnceBtn?.addEventListener('click', () => this.runAssetSchedulerOnce());
        this.els.assetSchedulerLogsBtn?.addEventListener('click', () => this.toggleAssetSchedulerLogs());
        this.els.refreshPromptsBtn?.addEventListener('click', () => this.loadPrompts());
        this.els.savePromptBtn?.addEventListener('click', () => this.saveSelectedPrompt());
        this.els.resetPromptBtn?.addEventListener('click', () => this.resetSelectedPrompt());
        this.els.closeTextModal?.addEventListener('click', () => this.closeTextModal());
        this.els.textModal?.addEventListener('click', (event) => {
            if (event.target === this.els.textModal) this.closeTextModal();
        });
        this.els.storyboardRatioButtons?.forEach(button => {
            button.addEventListener('click', () => {
                this.storyboardRatio = button.dataset.storyboardRatio || '16:9';
                this.els.storyboardRatioButtons.forEach(item => {
                    item.classList.toggle('active', item === button);
                });
                this.renderStoryboard(this.getActiveTask());
            });
        });
        document.querySelectorAll('.rv-tabs button').forEach((button) => {
            button.addEventListener('click', () => this.switchTab(button.dataset.tab));
        });
    },

    initialTaskId() {
        const params = new URLSearchParams(window.location.search);
        return (params.get('task_id') || params.get('task') || '').trim() || null;
    },

    async api(path, options = {}) {
        const response = await fetch(`${this.API_BASE}${path}`, options);
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.success === false) {
            throw new Error(this.formatApiError(data, response.status));
        }
        return data;
    },

    formatApiError(data, status) {
        const payload = data?.error ?? data?.message ?? data?.detail ?? data;
        if (typeof payload === 'string') return payload;
        if (payload && typeof payload === 'object') {
            if (typeof payload.message === 'string') return payload.message;
            if (typeof payload.error === 'string') return payload.error;
            try {
                return JSON.stringify(payload, null, 2);
            } catch (_) {
                return `请求失败: ${status}`;
            }
        }
        return `请求失败: ${status}`;
    },

    async loadTasks() {
        try {
            const data = await this.api('/report-video/tasks');
            this.tasks = this.mergeTaskSummaries(data.data || []);
            if (this.activeTaskId && !this.tasks.some(task => task.id === this.activeTaskId)) {
                try {
                    await this.loadTaskDetail(this.activeTaskId);
                } catch (_) {
                    this.activeTaskId = null;
                }
            }
            if (!this.activeTaskId && this.tasks.length) {
                this.activeTaskId = this.tasks[0].id;
            }
            this.renderTasks();
            await this.ensureActiveTaskDetail();
            this.renderTasks();
            this.renderActiveTask();
        } catch (error) {
            this.renderError(this.els.taskList, error.message);
        }
    },

    mergeTaskSummaries(summaries) {
        const existingById = new Map(this.tasks.map(task => [task.id, task]));
        return summaries.map(summary => {
            const existing = existingById.get(summary.id);
            if (!existing || existing.summary_only) return summary;
            return {
                ...existing,
                ...summary,
                result: existing.result || {},
                reports: existing.reports || [],
                audios: existing.audios || [],
                outputs: existing.outputs || [],
                summary_only: false
            };
        });
    },

    async loadTaskDetail(taskId) {
        if (!taskId) return null;
        const data = await this.api(`/report-video/tasks/${taskId}`);
        const task = {
            ...(data.data || {}),
            summary_only: false
        };
        this.upsertTask(task);
        return task;
    },

    async ensureActiveTaskDetail() {
        const task = this.getActiveTask();
        if (!task || (!task.summary_only && Array.isArray(task.reports))) return task;
        return this.loadTaskDetail(task.id);
    },

    async loadAutoScan() {
        if (!this.els.autoScanList) return;
        try {
            const data = await this.api('/report-video/auto-scan');
            this.autoScan = data.data || null;
            this.renderAutoScanConfig(this.autoScan?.config || {});
            this.renderAutoScanPreview(this.autoScan?.preview || null);
        } catch (error) {
            this.renderError(this.els.autoScanList, error.message);
        }
    },

    collectAutoScanConfig() {
        const outputRatios = [];
        if (this.els.autoScanRatio16?.checked) outputRatios.push('16:9');
        if (this.els.autoScanRatio9?.checked) outputRatios.push('9:16');
        return {
            enabled: !!this.els.autoScanEnabled?.checked,
            scan_dir: this.els.autoScanDir?.value?.trim() || '伯恩斯坦',
            schedule_time: this.els.autoScanTime?.value || '00:00',
            interval_minutes: this.els.autoScanInterval?.value ? Number(this.els.autoScanInterval.value) : null,
            max_files_per_run: Number(this.els.autoScanMaxFiles?.value || 1),
            default_segment_count: Number(this.els.autoScanSegmentCount?.value || 4),
            output_ratios: outputRatios.length ? outputRatios : ['16:9'],
            style: 'professional_finance',
            generate_subtitles: true
        };
    },

    async saveAutoScanConfig() {
        this.setButtonLoading(this.els.saveAutoScanBtn, true, '保存中...');
        try {
            const data = await this.api('/report-video/auto-scan/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this.collectAutoScanConfig())
            });
            this.autoScan = data.data;
            this.renderAutoScanConfig(data.data.config || {});
            this.renderAutoScanPreview(data.data.preview || null);
        } catch (error) {
            alert(`保存计划失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.saveAutoScanBtn, false, '保存计划配置');
        }
    },

    async previewAutoScan() {
        this.setButtonLoading(this.els.previewAutoScanBtn, true, '扫描中...');
        try {
            const data = await this.api('/report-video/auto-scan/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ config: this.collectAutoScanConfig() })
            });
            this.renderAutoScanPreview(data.data);
        } catch (error) {
            alert(`扫描预览失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.previewAutoScanBtn, false, '扫描预览');
        }
    },

    async runAutoScan() {
        this.setButtonLoading(this.els.runAutoScanBtn, true, '入队中...');
        try {
            const data = await this.api('/report-video/auto-scan/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ config: this.collectAutoScanConfig(), trigger: 'manual_page' })
            });
            alert(`自动扫描任务已入队：${data.data.celery_id || '-'}`);
            window.setTimeout(() => this.loadTasks(), 1200);
        } catch (error) {
            alert(`立即扫描失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.runAutoScanBtn, false, '立即扫描入队');
        }
    },

    async loadAssetScheduler() {
        if (!this.els.assetSchedulerStatus) return;
        try {
            const data = await this.api('/report-video/asset-scan/scheduler/status');
            const scheduler = data.data?.scheduler || null;
            this.assetScheduler = scheduler;
            this.renderAssetSchedulerConfig(scheduler?.config || {});
            this.renderAssetSchedulerStatus(scheduler);
        } catch (error) {
            this.renderError(this.els.assetSchedulerStatus, error.message);
        }
    },

    collectAssetSchedulerConfig() {
        return {
            enabled: !!this.els.assetSchedulerEnabled?.checked,
            schedule_time: this.els.assetSchedulerTime?.value || '02:30',
            interval_minutes: this.els.assetSchedulerInterval?.value ? Number(this.els.assetSchedulerInterval.value) : null,
            run_on_start: false,
            parse_pdfs: !!this.els.assetSchedulerParsePdfs?.checked,
            target_date_offset_days: Number(this.els.assetSchedulerDateOffset?.value || 1),
            config: {
                max_files: Number(this.els.assetSchedulerMaxFiles?.value || 50),
                tab_label_keyword: this.els.assetSchedulerTabKeyword?.value?.trim() || '外资研报',
                output_ratios: ['16:9'],
                default_segment_count: 4,
                style: 'professional_finance',
                generate_subtitles: true
            }
        };
    },

    async saveAssetSchedulerConfig() {
        this.setButtonLoading(this.els.assetSchedulerSaveBtn, true, '保存中...');
        try {
            await this.api('/report-video/asset-scan/scheduler/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this.collectAssetSchedulerConfig())
            });
            await this.loadAssetScheduler();
            this.renderToastHint('研报资产补漏配置已保存');
        } catch (error) {
            alert(`保存补漏配置失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.assetSchedulerSaveBtn, false, '保存补漏配置');
        }
    },

    async startAssetScheduler() {
        this.setButtonLoading(this.els.assetSchedulerStartBtn, true, '启动中...');
        try {
            await this.api('/report-video/asset-scan/scheduler/start', { method: 'POST' });
            await this.loadAssetScheduler();
        } catch (error) {
            alert(`启动补漏计划失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.assetSchedulerStartBtn, false, '启动计划');
        }
    },

    async stopAssetScheduler() {
        this.setButtonLoading(this.els.assetSchedulerStopBtn, true, '停止中...');
        try {
            await this.api('/report-video/asset-scan/scheduler/stop', { method: 'POST' });
            await this.loadAssetScheduler();
        } catch (error) {
            alert(`停止补漏计划失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.assetSchedulerStopBtn, false, '停止计划');
        }
    },

    async runAssetSchedulerOnce() {
        this.setButtonLoading(this.els.assetSchedulerRunOnceBtn, true, '入队中...');
        try {
            const data = await this.api('/report-video/asset-scan/scheduler/run-once', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            const summary = data.data?.scheduler?.summary || {};
            this.renderToastHint(summary.queued ? `补漏已入队：${summary.celery_id || '-'}` : '补漏检查完成，暂无新资产');
            await this.loadAssetScheduler();
            window.setTimeout(() => this.loadTasks(), 1200);
        } catch (error) {
            alert(`立即补漏失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.assetSchedulerRunOnceBtn, false, '立即补漏一次');
        }
    },

    async toggleAssetSchedulerLogs() {
        if (!this.els.assetSchedulerLogs) return;
        const hidden = this.els.assetSchedulerLogs.classList.contains('hidden');
        this.els.assetSchedulerLogs.classList.toggle('hidden', !hidden);
        if (hidden) await this.loadAssetSchedulerLogs();
    },

    async loadAssetSchedulerLogs() {
        if (!this.els.assetSchedulerLogs) return;
        try {
            const data = await this.api('/report-video/asset-scan/scheduler/logs?lines=120');
            const lines = data.data?.lines || [];
            this.els.assetSchedulerLogs.textContent = lines.length ? lines.join('\n') : '暂无日志。';
        } catch (error) {
            this.els.assetSchedulerLogs.textContent = `读取日志失败：${error.message || error}`;
        }
    },

    async loadPrompts() {
        if (!this.els.promptList) return;
        try {
            const data = await this.api('/report-video/ai-prompts');
            this.prompts = data.data || [];
            if (!this.selectedPromptId && this.prompts.length) {
                this.selectedPromptId = this.prompts[0].id;
            }
            this.renderPrompts();
        } catch (error) {
            this.renderError(this.els.promptList, error.message);
        }
    },

    async saveSelectedPrompt() {
        const prompt = this.getSelectedPrompt();
        if (!prompt) return;
        this.setButtonLoading(this.els.savePromptBtn, true, '保存中...');
        try {
            const data = await this.api(`/report-video/ai-prompts/${encodeURIComponent(prompt.id)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    system_prompt: this.els.promptSystemInput.value,
                    user_prompt: this.els.promptUserInput.value
                })
            });
            this.prompts = this.prompts.map(item => item.id === prompt.id ? data.data : item);
            this.renderPrompts();
        } catch (error) {
            alert(`保存提示词失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.savePromptBtn, false, '保存提示词');
        }
    },

    async resetSelectedPrompt() {
        const prompt = this.getSelectedPrompt();
        if (!prompt) return;
        if (!confirm(`确认恢复默认提示词？\n${prompt.name}`)) return;
        this.setButtonLoading(this.els.resetPromptBtn, true, '恢复中...');
        try {
            const data = await this.api(`/report-video/ai-prompts/${encodeURIComponent(prompt.id)}/reset`, { method: 'POST' });
            this.prompts = this.prompts.map(item => item.id === prompt.id ? data.data : item);
            this.renderPrompts();
        } catch (error) {
            alert(`恢复默认失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.resetPromptBtn, false, '恢复默认');
        }
    },

    async refreshIfBusy() {
        const activeTask = this.getActiveTask();
        if (this.hasDoubaoTtsInFlight(activeTask)) {
            await this.syncDoubaoTts({ silent: true });
            return;
        }
        const busyStatuses = new Set([
            'parsing',
            'ai_generating',
            'video_generating',
            'remotion_generating',
            'topic_cards_generating',
            'topic_clustering',
            'topic_script_generating',
            'audio_processing',
            'keyframes_generating',
            'preview_generating',
        ]);
        if (this.tasks.some(task => busyStatuses.has(task.status))) {
            await this.loadTasks();
        }
    },

    async createTask() {
        const ratios = Array.from(document.querySelectorAll('input[name="ratio"]:checked')).map(input => input.value);
        if (!ratios.length) {
            alert('至少选择一个输出比例');
            return;
        }
        const payload = {
            task_name: this.els.taskName.value.trim(),
            video_type: this.els.videoType.value,
            default_segment_count: Number(this.els.segmentCount.value),
            output_ratios: ratios,
            style: this.els.videoStyle.value,
            generate_subtitles: this.els.generateSubtitles.checked
        };
        try {
            const data = await this.api('/report-video/tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            this.activeTaskId = data.data.id;
            this.els.taskName.value = '';
            await this.loadTasks();
        } catch (error) {
            alert(`创建失败：${error.message}`);
        }
    },

    async uploadReports() {
        const task = this.getActiveTask();
        if (!task) {
            alert('请先选择任务');
            return;
        }
        const files = Array.from(this.els.pdfInput.files || []);
        if (!files.length) {
            alert('请选择 PDF 文件');
            return;
        }
        const formData = new FormData();
        files.forEach(file => formData.append('files', file));
        this.setButtonLoading(this.els.uploadPdfBtn, true, '解析中...');
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/reports`, {
                method: 'POST',
                body: formData
            });
            this.els.pdfInput.value = '';
            this.updatePdfFileSummary();
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
            const failed = data.data.failed || [];
            if (failed.length) {
                alert(`部分文件失败：${failed.map(item => `${item.file_name}: ${item.error}`).join('\n')}`);
            }
        } catch (error) {
            alert(`上传解析失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.uploadPdfBtn, false, '上传并解析');
        }
    },

    updatePdfFileSummary() {
        if (!this.els.pdfFileSummary || !this.els.pdfInput) return;
        const files = Array.from(this.els.pdfInput.files || []);
        if (!files.length) {
            this.els.pdfFileSummary.textContent = '未选择文件';
            return;
        }
        if (files.length === 1) {
            this.els.pdfFileSummary.textContent = files[0].name;
            return;
        }
        this.els.pdfFileSummary.textContent = `已选择 ${files.length} 个 PDF`;
    },

    updateAudioFileSummary() {
        if (!this.els.audioFileSummary || !this.els.audioInput) return;
        const files = Array.from(this.els.audioInput.files || []);
        if (!files.length) {
            this.els.audioFileSummary.textContent = '未选择文件';
            return;
        }
        if (files.length === 1) {
            this.els.audioFileSummary.textContent = files[0].name;
            return;
        }
        this.els.audioFileSummary.textContent = `已选择 ${files.length} 个音频`;
    },

    updateTimelineFileSummary() {
        if (!this.els.timelineFileSummary || !this.els.timelineInput) return;
        const files = Array.from(this.els.timelineInput.files || []);
        this.els.timelineFileSummary.textContent = files.length ? files[0].name : '未选择文件';
    },

    getAudioMode() {
        return document.querySelector('input[name="audio-mode"]:checked')?.value || 'segments';
    },

    updateAudioModeUi() {
        const actions = document.querySelector('.rv-upload-actions');
        if (!actions) return;
        actions.classList.toggle('full-timeline', this.getAudioMode() === 'full_audio_srt');
    },

    async openReportText(reportId, type = 'cleaned') {
        try {
            const data = await this.api(`/report-video/reports/${reportId}/text?type=${encodeURIComponent(type)}`);
            const report = data.data.report;
            this.els.textModalTitle.textContent = report.file_name;
            this.els.textModalMeta.textContent = `${type === 'raw' ? 'raw_text' : 'cleaned_text'} · ${data.data.text.length} 字符`;
            this.els.textModalBody.textContent = data.data.text || '暂无文本';
            this.els.textModal.classList.remove('hidden');
        } catch (error) {
            alert(`读取文本失败：${error.message}`);
        }
    },

    async reparseReport(reportId) {
        try {
            const data = await this.api(`/report-video/reports/${reportId}/reparse`, { method: 'POST' });
            const task = this.getActiveTask();
            if (task) {
                task.reports = (task.reports || []).map(report => report.id === reportId ? data.data : report);
                if (data.queued) task.status = 'parsing';
                this.renderActiveTask();
            }
            if (data.queued) this.loadTasks();
        } catch (error) {
            alert(`重新解析失败：${error.message}`);
        }
    },

    async deleteReport(reportId) {
        const task = this.getActiveTask();
        const report = task?.reports?.find(item => item.id === reportId);
        if (!report) return;
        if (!confirm(`确认删除这篇研报？\n${report.file_name}`)) return;

        try {
            const data = await this.api(`/report-video/reports/${reportId}`, { method: 'DELETE' });
            this.upsertTask(data.data);
            this.renderTasks();
            this.renderActiveTask();
        } catch (error) {
            alert(`删除失败：${error.message}`);
        }
    },

    async generateAiScripts() {
        const task = this.getActiveTask();
        if (!task) {
            alert('请先选择任务');
            return;
        }
        const parsedCount = (task.reports || []).filter(report => report.parse_status === 'parsed').length;
        if (!parsedCount) {
            alert('当前任务没有解析成功的研报，不能生成文案');
            return;
        }

        this.setButtonLoading(this.els.generateAiBtn, true, '生成中...');
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/ai-scripts`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ segment_count: task.default_segment_count || 4 })
            });
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
            const failed = data.data.failed || [];
            if (failed.length) {
                alert(`部分文案生成失败：${failed.map(item => `${item.file_name}: ${item.error}`).join('\n')}`);
            }
        } catch (error) {
            alert(`AI 文案生成失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.generateAiBtn, false, '生成 AI 文案');
        }
    },

    async generateTopicCards() {
        const task = this.getActiveTask();
        if (!task) {
            alert('请先选择任务');
            return;
        }
        const reportCount = (task.reports || []).length;
        if (!reportCount) {
            alert('当前任务还没有研报，不能生成摘要卡');
            return;
        }
        this.setButtonLoading(this.els.generateTopicCardsBtn, true, '生成中...');
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/topic-planning/cards`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
        } catch (error) {
            alert(`摘要卡生成失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.generateTopicCardsBtn, false, '生成摘要卡');
        }
    },

    async generateTopicClusters() {
        const task = this.getActiveTask();
        if (!task) {
            alert('请先选择任务');
            return;
        }
        this.setButtonLoading(this.els.generateTopicClustersBtn, true, '聚类中...');
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/topic-planning/clusters`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
        } catch (error) {
            alert(`主题榜生成失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.generateTopicClustersBtn, false, '生成主题榜');
        }
    },

    async generateTopicScript() {
        const task = this.getActiveTask();
        const topicId = this.selectedTopicId;
        if (!task || !topicId) {
            alert('请先选择一个主题');
            return;
        }
        this.setButtonLoading(this.els.generateTopicScriptBtn, true, '生成中...');
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/topics/${encodeURIComponent(topicId)}/script`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
            const updatedTask = data.data.task;
            const scripts = updatedTask?.result?.topic_planning?.topic_scripts || {};
            if (scripts[topicId]) {
                this.renderToastHint('主题脚本已生成。下一步点击“应用为视频脚本”，然后进入音频匹配。');
            }
        } catch (error) {
            alert(`主题脚本生成失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.generateTopicScriptBtn, false, '生成主题脚本');
        }
    },

    async applyTopicScript() {
        const task = this.getActiveTask();
        const topicId = this.selectedTopicId;
        if (!task || !topicId) {
            alert('请先选择一个主题');
            return;
        }
        this.setButtonLoading(this.els.applyTopicScriptBtn, true, '应用中...');
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/topics/${encodeURIComponent(topicId)}/apply-script`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
            this.switchTab('audio');
            this.renderToastHint('主题脚本已应用为视频脚本。现在可以复制口播稿或上传音频。');
        } catch (error) {
            alert(`应用主题脚本失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.applyTopicScriptBtn, false, '应用为视频脚本');
        }
    },

    async submitDoubaoTts() {
        const task = this.getActiveTask();
        const rows = this.buildTtsRows(task?.reports || []);
        if (!task || !rows.length) {
            alert('还没有可投递的 TTS 段落');
            return;
        }
        this.setButtonLoading(this.els.submitDoubaoTtsBtn, true, '投递中...');
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/tts/doubao/submit`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reuse_completed: true })
            });
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
            const summary = data.data.doubao_tts?.summary || {};
            this.renderToastHint(`豆包 TTS 已投递：${summary.submitted || 0} 段，已就绪 ${summary.audio_ready || 0}/${summary.total || rows.length}`);
        } catch (error) {
            alert(`投递豆包 TTS 失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.submitDoubaoTtsBtn, false, '投递豆包 TTS');
        }
    },

    async syncDoubaoTts(options = {}) {
        const task = this.getActiveTask();
        if (!task) return;
        if (!options.silent) {
            this.setButtonLoading(this.els.syncDoubaoTtsBtn, true, '同步中...');
        }
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/tts/doubao/sync`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
            if (!options.silent) {
                const summary = data.data.doubao_tts?.summary || {};
                this.renderToastHint(`TTS 同步完成：音频就绪 ${summary.audio_ready || 0}/${summary.total || 0}`);
            }
        } catch (error) {
            if (!options.silent) {
                alert(`同步 TTS 音频失败：${error.message}`);
            }
        } finally {
            if (!options.silent) {
                this.setButtonLoading(this.els.syncDoubaoTtsBtn, false, '同步 TTS 音频');
            }
        }
    },

    async uploadAudios() {
        const task = this.getActiveTask();
        if (!task) {
            alert('请先选择任务');
            return;
        }
        const files = Array.from(this.els.audioInput.files || []);
        if (!files.length) {
            alert('请选择音频文件');
            return;
        }
        const audioMode = this.getAudioMode();
        const forceAsrTimeline = audioMode === 'full_audio_srt' && Boolean(this.els.forceAsrTimeline?.checked);
        const audioSpeed = audioMode === 'full_audio_srt' ? (this.els.audioSpeedSelect?.value || '1.2') : '1.0';
        if (audioMode === 'full_audio_srt' && files.length !== 1) {
            alert('整片音频 + 时间轴模式请只上传 1 个完整音频');
            return;
        }
        const timelineFile = this.els.timelineInput?.files?.[0] || null;
        const timelineText = String(this.els.timelineTextInput?.value || '').trim();
        const formData = new FormData();
        formData.append('audio_mode', audioMode);
        formData.append('timeline_calibration_mode', 'sampled_asr');
        formData.append('timeline_calibration_model', 'medium');
        formData.append('sample_interval_sec', '180');
        formData.append('sample_duration_sec', '10');
        formData.append('min_anchor_confidence', '0.75');
        formData.append('enable_vad', 'true');
        formData.append('force_asr_timeline', forceAsrTimeline ? 'true' : 'false');
        formData.append('audio_speed', audioSpeed);
        files.forEach(file => formData.append('files', file));
        if (timelineFile) formData.append('timeline_file', timelineFile);
        if (timelineText) formData.append('timeline_text', timelineText);
        this.setButtonLoading(this.els.uploadAudioBtn, true, '匹配中...');
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/audios`, {
                method: 'POST',
                body: formData
            });
            this.els.audioInput.value = '';
            if (this.els.timelineInput) this.els.timelineInput.value = '';
            this.updateAudioFileSummary();
            this.updateTimelineFileSummary();
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
            const failed = data.data.failed || [];
            if (failed.length) {
                alert(`部分音频失败：${failed.map(item => `${item.file_name}: ${item.error}`).join('\n')}`);
            }
            if (data.data.timeline) {
                if (data.data.timeline.queued) {
                    alert(data.data.timeline.message || '已投递 ASR 字幕生成队列，稍后自动刷新状态。');
                    return;
                }
                const source = data.data.timeline.timeline_source || '';
                const prefix = source === 'full_audio_asr'
                    ? '已通过整片 ASR 生成字幕时间轴，'
                    : data.data.timeline.auto_generated
                        ? '已根据口播稿自动生成时间轴，'
                        : '';
                const retime = data.data.timeline?.retime || data.data.task?.result?.full_audio?.retime || {};
                const calibrationText = retime?.message
                    ? `\n${retime.message}`
                    : retime?.status === 'calibrated'
                        ? `\n抽样 ASR 校准完成：${retime.anchor_count || 0} 个锚点，平均偏移 ${retime.average_offset_sec || 0}s`
                        : '';
                alert(`${prefix}时间轴匹配完成：${data.data.timeline.matched || 0} 个 segment${calibrationText}`);
            }
        } catch (error) {
            alert(`音频上传失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.uploadAudioBtn, false, '上传并匹配');
        }
    },

    async generateVideos() {
        const task = this.getActiveTask();
        if (!task) {
            alert('请先选择任务');
            return;
        }
        const readyReports = (task.reports || []).filter(report => report.analysis && (report.segments || []).length);
        if (!readyReports.length) {
            alert('请先生成单篇 AI 文案，或在主题策划中生成并应用主题脚本');
            return;
        }
        const missingPreview = this.previewApprovalMissing(task);
        if (missingPreview.length) {
            alert(`请先完成正式生成前确认：\n${missingPreview.join('\n')}`);
            return;
        }
        this.setButtonLoading(this.els.generateVideoBtn, true, '生成中...');
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/videos/generate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ratios: task.output_ratios || ['16:9'] })
            });
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
            const failed = data.data.failed || [];
            if (failed.length) {
                alert(`部分视频生成失败：${failed.map(item => `${item.file_name} ${item.ratio}: ${item.error}`).join('\n')}`);
            }
        } catch (error) {
            alert(`视频生成失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.generateVideoBtn, false, '生成视频');
        }
    },

    async generateRemotionVideos() {
        const task = this.getActiveTask();
        if (!task) {
            alert('请先选择任务');
            return;
        }
        const readyReports = (task.reports || []).filter(report => report.analysis && (report.segments || []).length);
        if (!readyReports.length) {
            alert('请先生成单篇 AI 文案，或在主题策划中生成并应用主题脚本');
            return;
        }
        this.setButtonLoading(this.els.generateRemotionVideoBtn, true, 'Remotion 入队中...');
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/videos/remotion/generate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ratios: task.output_ratios || ['16:9'] })
            });
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
        } catch (error) {
            alert(`Remotion 视频生成失败：${error.message}`);
        } finally {
            this.setButtonLoading(this.els.generateRemotionVideoBtn, false, '生成 Remotion 试验版');
        }
    },

    async moveReport(reportId, direction) {
        const task = this.getActiveTask();
        const reports = [...(task?.reports || [])];
        const index = reports.findIndex(report => report.id === reportId);
        const nextIndex = index + direction;
        if (index < 0 || nextIndex < 0 || nextIndex >= reports.length) return;

        const [item] = reports.splice(index, 1);
        reports.splice(nextIndex, 0, item);

        await this.persistReportOrder(reports);
    },

    async persistReportOrder(reports) {
        const task = this.getActiveTask();
        if (!task) return;
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/reports/reorder`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ report_ids: reports.map(report => report.id) })
            });
            this.upsertTask(data.data);
            this.renderTasks();
            this.renderActiveTask();
        } catch (error) {
            alert(`调整顺序失败：${error.message}`);
        }
    },

    closeTextModal() {
        this.els.textModal.classList.add('hidden');
    },

    switchTab(tab) {
        this.activeTab = tab || 'reports';
        document.querySelectorAll('.rv-tabs button').forEach((button) => {
            button.classList.toggle('active', button.dataset.tab === this.activeTab);
        });
        document.querySelectorAll('.rv-tab-panel').forEach((panel) => {
            panel.classList.toggle('hidden', panel.id !== `tab-${this.activeTab}`);
        });
    },

    renderTasks() {
        if (!this.tasks.length) {
            this.els.taskList.innerHTML = '<div class="rv-empty">暂无任务</div>';
            this.updateFlowGraph();
            return;
        }
        this.els.taskList.innerHTML = this.tasks.map(task => `
            <button class="rv-task-item ${task.id === this.activeTaskId ? 'active' : ''}" type="button" data-task-id="${task.id}">
                <h3>${this.escape(task.task_name)}</h3>
                <p>${this.formatStatus(task.status)} · ${task.report_count || 0} 篇研报 · ${this.formatTime(task.created_at)}</p>
            </button>
        `).join('');
        this.els.taskList.querySelectorAll('[data-task-id]').forEach(button => {
            button.addEventListener('click', async () => {
                this.activeTaskId = button.dataset.taskId;
                this.selectedTopicId = null;
                this.renderTasks();
                this.renderActiveTask();
                try {
                    await this.ensureActiveTaskDetail();
                    this.renderTasks();
                    this.renderActiveTask();
                } catch (error) {
                    this.renderError(this.els.reportList, error.message);
                }
            });
        });
        this.updateFlowGraph();
    },

    renderActiveTask() {
        const task = this.getActiveTask();
        this.els.emptyState.classList.toggle('hidden', !!task);
        this.els.workbench.classList.toggle('hidden', !task);
        if (!task) {
            this.renderStoryboard(null);
            this.renderRenderPreviews(null);
            this.updateFlowGraph();
            return;
        }

        const reports = task.reports || [];
        this.els.activeTaskName.textContent = task.task_name;
        this.els.activeTaskMeta.textContent = `${this.formatVideoType(task.video_type)} · 默认 ${task.default_segment_count} 段 · ${task.output_ratios.join(' + ')}`;
        this.els.activeTaskStatus.textContent = this.formatStatus(task.status);
        if (task.summary_only) {
            this.els.reportSummary.textContent = `${task.report_count || 0} 篇`;
            this.els.reportList.innerHTML = '<div class="rv-empty">正在加载任务详情...</div>';
            this.els.scriptSummary.textContent = '加载中';
            this.els.scriptList.innerHTML = '<div class="rv-empty">正在加载任务详情...</div>';
            this.els.mindmapSummary.textContent = '加载中';
            this.els.mindmapList.innerHTML = '<div class="rv-empty">正在加载任务详情...</div>';
            this.els.ttsScriptSummary.textContent = '加载中';
            this.els.ttsScriptList.innerHTML = '<div class="rv-empty">正在加载任务详情...</div>';
            this.els.audioSummary.textContent = '加载中';
            this.els.audioList.innerHTML = '<div class="rv-empty">正在加载任务详情...</div>';
            this.els.videoSummary.textContent = '加载中';
            this.els.videoOutputList.innerHTML = '<div class="rv-empty">正在加载任务详情...</div>';
            this.renderStoryboard(task);
            this.renderRenderPreviews(task);
            this.updateFlowGraph();
            return;
        }
        this.els.reportSummary.textContent = `${reports.length} 篇`;
        this.renderReports(reports);
        this.renderTopicPlanning(task);
        this.renderScripts(reports);
        this.renderMindmaps(reports);
        this.renderTtsScripts(reports);
        this.renderDoubaoTtsStatus(task);
        this.renderAudios(task);
        this.renderRenderPreviews(task);
        this.renderOutputs(task.outputs || []);
        this.renderStoryboard(task);
        this.updateFlowGraph();
    },

    renderAutoScanConfig(config) {
        if (!this.els.autoScanDir) return;
        this.els.autoScanEnabled.checked = !!config.enabled;
        this.els.autoScanDir.value = config.scan_dir || '伯恩斯坦';
        this.els.autoScanTime.value = config.schedule_time || '00:00';
        this.els.autoScanInterval.value = config.interval_minutes || '';
        this.els.autoScanMaxFiles.value = config.max_files_per_run || 2;
        this.els.autoScanSegmentCount.value = String(config.default_segment_count || 4);
        const ratios = config.output_ratios || ['16:9'];
        this.els.autoScanRatio16.checked = ratios.includes('16:9');
        this.els.autoScanRatio9.checked = ratios.includes('9:16');
    },

    renderAutoScanPreview(preview) {
        if (!this.els.autoScanList) return;
        if (!preview) {
            this.els.autoScanSummary.textContent = '尚未扫描';
            this.els.autoScanList.innerHTML = '<div class="rv-empty">点击“扫描预览”查看文件夹里的 PDF 状态。</div>';
            return;
        }
        this.els.autoScanSummary.textContent = [
            `PDF ${preview.total_pdf_count || 0}`,
            `待制作 ${preview.new_count || 0}`,
            `已完成 ${preview.completed_count || 0}`,
            `处理中 ${preview.queued_count || 0}`,
        ].join(' · ');
        const items = preview.items || [];
        if (!items.length) {
            const message = preview.scan_dir_exists === false ? '扫描文件夹不存在。' : '该文件夹下没有 PDF。';
            this.els.autoScanList.innerHTML = `<div class="rv-empty">${this.escape(message)}</div>`;
            return;
        }
        this.els.autoScanList.innerHTML = items.map(item => {
            const cls = item.status === 'new' ? 'warn' : (item.status === 'completed' ? 'ok' : '');
            return `
                <article class="rv-auto-item">
                    <div>
                        <h4>${this.escape(item.file_name)}</h4>
                        <p>${this.escape(item.display_path || item.path || '')}</p>
                        ${item.task_id ? `<p>关联任务：<code>${this.escape(item.task_id)}</code></p>` : ''}
                    </div>
                    <span class="rv-pill ${cls}">${this.escape(this.formatAutoScanStatus(item.status))}</span>
                </article>
            `;
        }).join('');
    },

    renderAssetSchedulerConfig(config) {
        if (!this.els.assetSchedulerEnabled) return;
        const nested = config.config || {};
        this.els.assetSchedulerEnabled.checked = config.enabled !== false;
        this.els.assetSchedulerTime.value = config.schedule_time || '02:30';
        this.els.assetSchedulerInterval.value = config.interval_minutes || '';
        this.els.assetSchedulerDateOffset.value = config.target_date_offset_days ?? 1;
        this.els.assetSchedulerMaxFiles.value = nested.max_files || 50;
        this.els.assetSchedulerTabKeyword.value = nested.tab_label_keyword || '外资研报';
        this.els.assetSchedulerParsePdfs.checked = config.parse_pdfs !== false;
    },

    renderAssetSchedulerStatus(scheduler) {
        if (!this.els.assetSchedulerStatus) return;
        if (!scheduler) {
            this.els.assetSchedulerBadge.textContent = '未知';
            this.els.assetSchedulerStatus.innerHTML = '<div class="rv-empty">暂无补漏计划状态。</div>';
            return;
        }
        const running = !!scheduler.running;
        const config = scheduler.config || {};
        const nested = config.config || {};
        const last = scheduler.last_result || config.last_result || {};
        this.els.assetSchedulerBadge.textContent = running ? '运行中' : '未运行';
        this.els.assetSchedulerBadge.className = `rv-pill ${running ? 'ok' : ''}`;
        const scheduleText = config.interval_minutes
            ? `每 ${config.interval_minutes} 分钟`
            : `每天 ${config.schedule_time || '02:30'}`;
        const lastText = last.completed_at
            ? [
                `目标 ${last.target_date || '-'}`,
                `候选 ${last.candidate_count || 0}`,
                `新增 ${last.new_count || 0}`,
                `已存在 ${last.existing_count || 0}`,
                last.queued ? `已入队 ${last.celery_id || '-'}` : '未入队'
            ].join(' · ')
            : '暂无执行记录';
        this.els.assetSchedulerStatus.innerHTML = `
            <article class="rv-auto-item">
                <div>
                    <h4>${this.escape(running ? `运行中 · PID ${scheduler.pid || '-'}` : '未运行')}</h4>
                    <p>${this.escape(config.enabled === false ? '配置：已禁用' : `配置：已启用 · ${scheduleText}`)}</p>
                    <p>${this.escape(`扫描：T-${config.target_date_offset_days ?? 1} · 单次 ${nested.max_files || 50} 个 · ${nested.tab_label_keyword || '外资研报'} · ${config.parse_pdfs === false ? '不解析' : '解析 PDF'}`)}</p>
                    <p>${this.escape(`下次执行：${this.formatTime(scheduler.next_run_at || config.next_run_at)}`)}</p>
                    <p>${this.escape(`上次执行：${this.formatTime(scheduler.last_run_at || config.last_run_at)}`)}</p>
                    <p>${this.escape(`最近结果：${lastText}`)}</p>
                    <p>日志：<code>${this.escape(scheduler.log_path || config.log_path || '-')}</code></p>
                </div>
                <span class="rv-pill ${last.error_count ? 'warn' : (last.queued ? 'ok' : '')}">${this.escape(last.error_count ? '失败' : (last.queued ? '已入队' : '待命'))}</span>
            </article>
        `;
    },

    renderPrompts() {
        if (!this.els.promptList) return;
        if (!this.prompts.length) {
            this.els.promptList.innerHTML = '<div class="rv-empty">暂无提示词配置。</div>';
            this.renderPromptEditor(null);
            return;
        }
        this.els.promptList.innerHTML = this.prompts.map(prompt => `
            <button class="rv-prompt-item ${prompt.id === this.selectedPromptId ? 'active' : ''}" type="button" data-prompt-id="${this.escape(prompt.id)}">
                <strong>${this.escape(prompt.name)}</strong>
                <span>${this.escape(prompt.stage || '-')}</span>
                ${prompt.is_custom ? '<em>已自定义</em>' : ''}
            </button>
        `).join('');
        this.els.promptList.querySelectorAll('[data-prompt-id]').forEach(button => {
            button.addEventListener('click', () => {
                this.selectedPromptId = button.dataset.promptId;
                this.renderPrompts();
            });
        });
        this.renderPromptEditor(this.getSelectedPrompt());
    },

    renderPromptEditor(prompt) {
        const disabled = !prompt;
        this.els.savePromptBtn.disabled = disabled;
        this.els.resetPromptBtn.disabled = disabled;
        this.els.promptSystemInput.disabled = disabled;
        this.els.promptUserInput.disabled = disabled;
        if (!prompt) {
            this.els.promptEditorTitle.textContent = '选择一个提示词';
            this.els.promptEditorMeta.textContent = '可查看用途、系统提示词和用户提示词。';
            this.els.promptSystemInput.value = '';
            this.els.promptUserInput.value = '';
            return;
        }
        this.els.promptEditorTitle.textContent = prompt.name || prompt.id;
        const custom = prompt.is_custom ? '已自定义' : '默认模板';
        this.els.promptEditorMeta.textContent = `${prompt.stage || '-'} · ${custom} · ${prompt.description || ''}`;
        this.els.promptSystemInput.value = prompt.system_prompt || '';
        this.els.promptUserInput.value = prompt.user_prompt || '';
    },

    renderReports(reports) {
        if (!reports.length) {
            this.els.reportList.innerHTML = '<div class="rv-empty">还没有上传 PDF。</div>';
            return;
        }
        this.els.reportList.innerHTML = reports.map((report, index) => {
            const statusClass = report.parse_status === 'parsed' ? 'ok' : (report.parse_status === 'parse_failed' ? 'bad' : 'warn');
            const scannedClass = report.is_scanned ? 'warn' : 'ok';
            const extractionMethod = report.raw_meta?.extraction_method || report.raw_meta?.scan_stats?.extraction_method || '';
            return `
                <article class="rv-report-item" draggable="true" data-report-id="${report.id}">
                    <div class="rv-report-top">
                        <div>
                            <h4><span class="rv-drag-grip" title="拖动调整顺序">⋮⋮</span>${report.report_code} · ${this.escape(report.file_name)}</h4>
                            <div class="rv-report-meta">
                                <span class="rv-pill ${statusClass}">${this.formatStatus(report.parse_status)}</span>
                                <span class="rv-pill ${scannedClass}">${report.is_scanned ? '疑似扫描版' : '文本型 PDF'}</span>
                                ${extractionMethod ? `<span class="rv-pill">${this.formatExtractionMethod(extractionMethod)}</span>` : ''}
                                <span class="rv-pill">${report.page_count || 0} 页</span>
                                <span class="rv-pill">清洗后 ${report.cleaned_text_chars || 0} 字符</span>
                            </div>
                            ${report.error_message ? `<p class="rv-error-text">${this.escape(report.error_message)}</p>` : ''}
                        </div>
                        <div class="rv-report-actions">
                            <button type="button" data-move-id="${report.id}" data-move-dir="-1" ${index === 0 ? 'disabled' : ''}>上移</button>
                            <button type="button" data-move-id="${report.id}" data-move-dir="1" ${index === reports.length - 1 ? 'disabled' : ''}>下移</button>
                            <button type="button" data-text-id="${report.id}" data-text-type="cleaned">正文</button>
                            <button type="button" data-text-id="${report.id}" data-text-type="raw">原文</button>
                            <button type="button" data-reparse-id="${report.id}">重解析</button>
                            <button class="danger" type="button" data-delete-id="${report.id}">删除</button>
                        </div>
                    </div>
                </article>
            `;
        }).join('');

        this.els.reportList.querySelectorAll('[data-text-id]').forEach(button => {
            button.addEventListener('click', () => this.openReportText(button.dataset.textId, button.dataset.textType));
        });
        this.els.reportList.querySelectorAll('[data-reparse-id]').forEach(button => {
            button.addEventListener('click', () => this.reparseReport(button.dataset.reparseId));
        });
        this.els.reportList.querySelectorAll('[data-delete-id]').forEach(button => {
            button.addEventListener('click', () => this.deleteReport(button.dataset.deleteId));
        });
        this.els.reportList.querySelectorAll('[data-move-id]').forEach(button => {
            button.addEventListener('click', () => this.moveReport(button.dataset.moveId, Number(button.dataset.moveDir)));
        });
        this.bindReportDragEvents();
    },

    renderTopicPlanning(task) {
        const planning = task?.result?.topic_planning || {};
        const cards = planning.report_cards || [];
        const topics = planning.topics || [];
        const rankedTopics = planning.ranked_topics || [];
        const scripts = planning.topic_scripts || {};
        const appliedTopicId = planning.applied_topic_id || null;
        const topicMap = new Map(topics.map(topic => [topic.topic_id, topic]));

        if (this.els.topicCardSummary) {
            this.els.topicCardSummary.textContent = `${cards.length} 张`;
        }
        if (this.els.topicRankSummary) {
            const external = planning.external_heat?.search_status === 'not_enabled' ? ' · 外部热度未启用' : '';
            this.els.topicRankSummary.textContent = `${topics.length} 个主题${external}`;
        }

        if (this.els.topicCardList) {
            if (!cards.length) {
                this.els.topicCardList.innerHTML = '<div class="rv-empty">还没有生成摘要卡。</div>';
            } else {
                this.els.topicCardList.innerHTML = cards.map(card => `
                    <article class="rv-topic-card-item">
                        <div class="rv-topic-card-head">
                            <div>
                                <h4>${this.escape(card.report_id || card.report_code || '-')} · ${this.escape(card.report_title || card.file_name || '-')}</h4>
                                <p>${this.escape(card.institution || '机构未识别')} · ${this.escape(card.primary_theme || '主题未识别')}</p>
                            </div>
                            <span class="rv-score small">${this.escape(card.video_value_score ?? '-')}</span>
                        </div>
                        <p class="rv-topic-summary">${this.escape(card.one_sentence_summary || card.core_view || '')}</p>
                        <div class="rv-key-points">
                            ${(card.theme_tags || []).slice(0, 6).map(tag => `<span>${this.escape(tag)}</span>`).join('')}
                        </div>
                    </article>
                `).join('');
            }
        }

        if (!this.selectedTopicId || !topicMap.has(this.selectedTopicId)) {
            this.selectedTopicId = (rankedTopics[0]?.topic_id || topics[0]?.topic_id || null);
        }

        if (this.els.topicRankList) {
            if (!rankedTopics.length) {
                this.els.topicRankList.innerHTML = '<div class="rv-empty">生成主题榜后会显示可制作主题。</div>';
            } else {
                this.els.topicRankList.innerHTML = rankedTopics.map(row => {
                    const active = row.topic_id === this.selectedTopicId;
                    return `
                        <button class="rv-topic-rank-item ${active ? 'active' : ''}" type="button" data-topic-id="${this.escape(row.topic_id)}">
                            <span class="rv-rank-no">#${row.rank}</span>
                            <span class="rv-rank-body">
                                <strong>${this.escape(row.topic_name || '-')}</strong>
                                <em>${this.escape(row.report_count || 0)} 篇 · ${this.escape((row.institutions || []).slice(0, 4).join('、') || '机构未识别')}</em>
                            </span>
                            <span class="rv-rank-score">${this.escape(row.final_score ?? '-')}</span>
                        </button>
                    `;
                }).join('');
                this.els.topicRankList.querySelectorAll('[data-topic-id]').forEach(button => {
                    button.addEventListener('click', () => {
                        this.selectedTopicId = button.dataset.topicId;
                        this.renderTopicPlanning(this.getActiveTask());
                        this.renderStoryboard(this.getActiveTask());
                    });
                });
            }
        }

        const topic = this.selectedTopicId ? topicMap.get(this.selectedTopicId) : null;
        const topicScript = topic ? (scripts[topic.topic_id] || null) : null;
        const isAppliedTopic = !!topic && appliedTopicId === topic.topic_id;
        if (this.els.generateTopicScriptBtn) {
            this.els.generateTopicScriptBtn.disabled = !topic;
        }
        if (this.els.applyTopicScriptBtn) {
            this.els.applyTopicScriptBtn.disabled = !topicScript;
            this.els.applyTopicScriptBtn.textContent = isAppliedTopic ? '重新应用脚本' : '应用为视频脚本';
        }
        if (this.els.topicDetailSummary) {
            this.els.topicDetailSummary.textContent = topic
                ? `${topic.topic_name} · ${topic.report_count || 0} 篇研报 · ${
                    isAppliedTopic ? '已应用为视频脚本，可进入音频匹配' : (topicScript ? '脚本已生成，等待应用' : '可生成主题级脚本')
                }`
                : '选择一个主题查看详情，并生成主题级视频脚本。';
        }
        if (!this.els.topicDetailPanel) return;
        if (!topic) {
            this.els.topicDetailPanel.innerHTML = '<div class="rv-empty">请先在主题榜里选择一个主题。</div>';
            this.updateFlowGraph();
            return;
        }

        const reportCardsById = new Map(cards.map(card => [card.report_id, card]));
        const supportingReports = (topic.reports || []).map(id => reportCardsById.get(id)).filter(Boolean);
        this.els.topicDetailPanel.innerHTML = `
            <article class="rv-topic-detail-card">
                <div class="rv-topic-detail-head">
                    <div>
                        <h4>${this.escape(topic.topic_name || '-')}</h4>
                        <p>${this.escape(topic.topic_category || '')}</p>
                    </div>
                    <span class="rv-pill ok">总分 ${this.escape(topic.final_score ?? topic.internal_heat_score ?? '-')}</span>
                    ${isAppliedTopic ? '<span class="rv-pill ok">已应用</span>' : ''}
                </div>
                <p class="rv-topic-summary strong">${this.escape(topic.topic_summary || topic.recommended_video_angle || '')}</p>
                <div class="rv-topic-columns">
                    <div>
                        <h5>共同观点</h5>
                        <ul>${(topic.common_views || []).slice(0, 5).map(item => `<li>${this.escape(item)}</li>`).join('') || '<li>暂无</li>'}</ul>
                    </div>
                    <div>
                        <h5>分歧/风险</h5>
                        <ul>${(topic.conflicting_views || []).slice(0, 4).map(item => `<li>${this.escape(item)}</li>`).join('') || '<li>暂无明确分歧</li>'}</ul>
                    </div>
                </div>
                <div class="rv-topic-source-list">
                    <h5>支撑研报</h5>
                    ${supportingReports.map(card => `
                        <div class="rv-topic-source-item">
                            <strong>${this.escape(card.report_id)}</strong>
                            <span>${this.escape(card.institution || '机构未识别')}</span>
                            <em>${this.escape(card.report_title || card.file_name || '-')}</em>
                        </div>
                    `).join('') || '<p class="rv-topic-muted">暂无支撑研报明细。</p>'}
                </div>
                <div class="rv-topic-script-preview">
                    <h5>主题脚本</h5>
                    ${topicScript ? this.renderTopicScriptPreview(topicScript) : '<p class="rv-topic-muted">还没有生成主题脚本。</p>'}
                    ${topicScript ? `
                        <div class="rv-topic-next-step ${isAppliedTopic ? 'applied' : ''}">
                            ${isAppliedTopic
                                ? '当前主题脚本已经写入视频脚本，可直接到“音频匹配”复制口播稿或上传音频。'
                                : '主题脚本生成后还需要点击“应用为视频脚本”，系统才会把它写入音频匹配和视频生成链路。'}
                        </div>
                    ` : ''}
                </div>
            </article>
        `;
        this.updateFlowGraph();
    },

    renderTopicScriptPreview(script) {
        const sections = script.sections || [];
        return `
            <div class="rv-topic-script-head">
                <strong>${this.escape(script.video_title || '-')}</strong>
                <span>${this.escape(script.provider || '-')} · ${sections.length} 段</span>
            </div>
            <p class="rv-topic-summary">${this.escape(script.opening_text || '')}</p>
            <div class="rv-segment-list compact">
                ${sections.map(section => `
                    <div class="rv-segment-item">
                        <div class="rv-segment-title">
                            <strong>${this.escape(section.section_title || section.section_id)}</strong>
                            <code>${this.escape(section.focus_node_id || '-')}</code>
                        </div>
                        <p>${this.escape(section.voice_text || '')}</p>
                        <div class="rv-key-points">
                            ${(section.bullet_points || []).slice(0, 4).map(point => `<span>${this.escape(point)}</span>`).join('')}
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
    },

    bindReportDragEvents() {
        const items = Array.from(this.els.reportList.querySelectorAll('.rv-report-item[draggable="true"]'));
        items.forEach(item => {
            item.addEventListener('dragstart', (event) => {
                this.draggedReportId = item.dataset.reportId;
                item.classList.add('dragging');
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', this.draggedReportId);
            });
            item.addEventListener('dragend', () => {
                item.classList.remove('dragging');
                this.els.reportList.querySelectorAll('.drag-over').forEach(node => node.classList.remove('drag-over'));
            });
        });

        this.els.reportList.ondragover = (event) => {
            if (!this.draggedReportId) return;
            event.preventDefault();
            const dragging = this.els.reportList.querySelector('.rv-report-item.dragging');
            if (!dragging) return;
            const afterElement = this.getDragAfterReportElement(event.clientY);
            if (afterElement == null) this.els.reportList.appendChild(dragging);
            else this.els.reportList.insertBefore(dragging, afterElement);
        };

        this.els.reportList.ondrop = async (event) => {
            if (!this.draggedReportId) return;
            event.preventDefault();
            const task = this.getActiveTask();
            const currentReports = task?.reports || [];
            const orderedIds = Array.from(this.els.reportList.querySelectorAll('.rv-report-item[data-report-id]'))
                .map(item => item.dataset.reportId);
            this.draggedReportId = null;
            if (!task || orderedIds.length !== currentReports.length) return;
            const oldIds = currentReports.map(report => report.id);
            if (orderedIds.join('|') === oldIds.join('|')) return;
            const reportMap = new Map(currentReports.map(report => [report.id, report]));
            await this.persistReportOrder(orderedIds.map(id => reportMap.get(id)).filter(Boolean));
        };
    },

    getDragAfterReportElement(y) {
        const candidates = Array.from(this.els.reportList.querySelectorAll('.rv-report-item:not(.dragging)'));
        return candidates.reduce((closest, child) => {
            const box = child.getBoundingClientRect();
            const offset = y - box.top - box.height / 2;
            if (offset < 0 && offset > closest.offset) {
                return { offset, element: child };
            }
            return closest;
        }, { offset: Number.NEGATIVE_INFINITY, element: null }).element;
    },

    renderScripts(reports) {
        if (!this.els.scriptList) return;
        const readyReports = reports.filter(report => report.analysis || (report.segments || []).length);
        this.els.scriptSummary.textContent = `${readyReports.length} 篇就绪`;
        if (!readyReports.length) {
            this.els.scriptList.innerHTML = '<div class="rv-empty">还没有生成文案。</div>';
            return;
        }

        this.els.scriptList.innerHTML = readyReports.map(report => {
            const analysis = report.analysis || {};
            const segments = report.segments || [];
            const provider = analysis.raw_payload?.provider || '-';
            return `
                <article class="rv-script-item">
                    <div class="rv-script-head">
                        <div>
                            <h4>${report.report_code} · ${this.escape(analysis.report_title || report.file_name)}</h4>
                            <p>${this.escape(analysis.theme || '未识别主题')} · ${segments.length} 段 · ${this.escape(provider)}</p>
                        </div>
                        <span class="rv-score">${analysis.video_value_score ?? '-'}</span>
                    </div>
                    <div class="rv-core-view">${this.escape(analysis.core_view || '暂无核心观点')}</div>
                    <div class="rv-key-points">
                        ${(analysis.key_points || []).slice(0, 5).map(point => `<span>${this.escape(point)}</span>`).join('')}
                    </div>
                    <div class="rv-segment-list">
                        ${segments.map(segment => `
                            <div class="rv-segment-item">
                                <div class="rv-segment-title">
                                    <strong>${this.escape(segment.segment_title || segment.segment_id)}</strong>
                                    <code>${this.escape(segment.focus_node_id || '-')}</code>
                                </div>
                                <p>${this.escape(segment.voice_text || '')}</p>
                            </div>
                        `).join('')}
                    </div>
                </article>
            `;
        }).join('');
    },

    renderMindmaps(reports) {
        if (!this.els.mindmapList) return;
        const readyReports = reports.filter(report => report.mindmap);
        this.els.mindmapSummary.textContent = `${readyReports.length} 个导图`;
        if (!readyReports.length) {
            this.els.mindmapList.innerHTML = '<div class="rv-empty">生成单篇 AI 文案，或应用主题脚本后会显示导图节点。</div>';
            return;
        }

        this.els.mindmapList.innerHTML = readyReports.map(report => {
            const mindmap = report.mindmap || {};
            const nodes = mindmap.nodes || [];
            const root = nodes.find(node => node.id === 'root') || nodes[0] || {};
            const children = nodes.filter(node => node.id !== root.id);
            return `
                <article class="rv-mindmap-item">
                    <h4>${report.report_code} · ${this.escape(mindmap.center || report.file_name)}</h4>
                    <div class="rv-mindmap-center">${this.escape(root.label || mindmap.center || '中心主题')}</div>
                    <div class="rv-mindmap-nodes">
                        ${children.map(node => `<span><code>${this.escape(node.id)}</code>${this.escape(node.label || '')}</span>`).join('')}
                    </div>
                </article>
            `;
        }).join('');
    },

    buildTtsRows(reports) {
        return reports.flatMap(report => (report.segments || []).map(segment => ({
            report,
            segment,
            recommendedFileName: `${report.report_code}_seg_${String(segment.segment_index || 0).padStart(3, '0')}.mp3`
        })));
    },

    renderTtsScripts(reports) {
        if (!this.els.ttsScriptList) return;
        const rows = this.buildTtsRows(reports);
        this.els.ttsScriptSummary.textContent = `${rows.length} 段`;
        if (this.els.submitDoubaoTtsBtn) {
            this.els.submitDoubaoTtsBtn.disabled = !rows.length;
        }
        if (this.els.syncDoubaoTtsBtn) {
            this.els.syncDoubaoTtsBtn.disabled = !rows.length;
        }
        if (!rows.length) {
            this.els.ttsScriptList.innerHTML = '<div class="rv-empty">生成单篇 AI 文案，或应用主题脚本后会显示可用于 TTS 的口播稿。</div>';
            return;
        }

        this.els.ttsScriptList.innerHTML = rows.map(({ report, segment, recommendedFileName }) => `
            <article class="rv-tts-item">
                <div class="rv-tts-head">
                    <div>
                        <h4>${this.escape(report.report_code)} · ${this.escape(segment.segment_title || segment.segment_id)}</h4>
                        <p>${this.escape(report.file_name)} · ${this.formatDuration(segment.estimated_duration_sec)} · 聚焦 ${this.escape(segment.focus_node_id || '-')}</p>
                    </div>
                    <button type="button" data-copy-tts="${this.escape(segment.id)}">复制本段</button>
                </div>
                <div class="rv-tts-file">建议文件名：<code>${this.escape(recommendedFileName)}</code></div>
                <p class="rv-tts-text">${this.escape(segment.voice_text || '')}</p>
            </article>
        `).join('');

        this.els.ttsScriptList.querySelectorAll('[data-copy-tts]').forEach(button => {
            button.addEventListener('click', () => {
                const row = rows.find(item => item.segment.id === button.dataset.copyTts);
                if (!row) return;
                this.copyText(row.segment.voice_text || '');
            });
        });
    },

    renderDoubaoTtsStatus(task) {
        if (!this.els.doubaoTtsStatus) return;
        const state = task?.result?.doubao_tts || null;
        const jobs = Array.isArray(state?.jobs) ? state.jobs : [];
        if (!jobs.length) {
            this.els.doubaoTtsStatus.innerHTML = `
                <div class="rv-doubao-tts-empty">
                    豆包 TTS 尚未投递。可以直接点击“投递豆包 TTS”，系统会按当前段落逐条生成音频并自动回填。
                </div>
            `;
            return;
        }

        const summary = state.summary || {};
        const statusClass = this.doubaoTtsStatusClass(state.status);
        this.els.doubaoTtsStatus.innerHTML = `
            <section class="rv-doubao-tts-panel">
                <div class="rv-doubao-tts-head">
                    <div>
                        <h4>豆包 TTS 任务</h4>
                        <p>音频就绪 ${summary.audio_ready || 0}/${summary.total || jobs.length} · 处理中 ${(summary.queued || 0) + (summary.running || 0)} · 失败/阻塞 ${(summary.failed || 0) + (summary.blocked || 0)}</p>
                    </div>
                    <span class="rv-pill ${statusClass}">${this.formatDoubaoTtsStatus(state.status)}</span>
                </div>
                <div class="rv-doubao-tts-jobs">
                    ${jobs.map(job => `
                        <div class="rv-doubao-tts-job">
                            <div>
                                <strong>${this.escape(job.recommended_file_name || job.segment_title || job.segment_id || '-')}</strong>
                                <span>${this.escape(job.segment_title || job.segment_id || '-')}</span>
                                ${job.error ? `<em>${this.escape(job.error)}</em>` : ''}
                            </div>
                            <span class="rv-pill ${this.doubaoJobStatusClass(job.status)}">${this.formatDoubaoJobStatus(job.status)}${job.progress ? ` · ${job.progress}%` : ''}</span>
                        </div>
                    `).join('')}
                </div>
            </section>
        `;
    },

    hasDoubaoTtsInFlight(task) {
        const state = task?.result?.doubao_tts || null;
        if (!state) return false;
        if (['queued', 'running'].includes(state.status)) return true;
        return (state.jobs || []).some(job => ['queued', 'running', 'succeeded'].includes(job.status));
    },

    formatTtsManifestRowText({ report, segment, recommendedFileName }) {
        return [
            `文件名：${recommendedFileName}`,
            `报告：${report.report_code} · ${report.file_name}`,
            `段落：${segment.segment_title || segment.segment_id}`,
            `focus_node_id：${segment.focus_node_id || '-'}`,
            '',
            segment.voice_text || ''
        ].join('\n');
    },

    formatVideoVoiceScript(reports) {
        const readyReports = (reports || []).filter(report => (report.segments || []).some(segment => segment.voice_text));
        if (!readyReports.length) return '';

        const singleReportPayload = readyReports.length === 1 ? (readyReports[0].analysis?.raw_payload || {}) : {};
        const intro = singleReportPayload.opening_text || (readyReports.length > 1
            ? `大家好，今天快速看 ${readyReports.length} 份研报的核心观点。`
            : '');
        const parts = readyReports.map((report, reportIndex) => {
            const title = report.analysis?.report_title || String(report.file_name || '').replace(/\\.pdf$/i, '');
            const transition = readyReports.length > 1
                ? `${this.formatChineseOrdinal(reportIndex + 1)}份，${title}。`
                : '';
            const body = (report.segments || [])
                .map(segment => String(segment.voice_text || '').trim())
                .filter(Boolean)
                .join('\n\n');
            return [transition, body].filter(Boolean).join('\n\n');
        });
        const outro = singleReportPayload.closing_text || (readyReports.length > 1 ? '以上就是本期研报重点。' : '');
        return [intro, ...parts, outro].filter(Boolean).join('\n\n');
    },

    copyAllTtsScripts() {
        const task = this.getActiveTask();
        const text = this.formatVideoVoiceScript(task?.reports || []);
        if (!text) {
            alert('还没有可复制的 TTS 文案，请先生成单篇 AI 文案，或应用主题脚本');
            return;
        }
        this.copyText(text);
    },

    copyTtsManifest() {
        const task = this.getActiveTask();
        const rows = this.buildTtsRows(task?.reports || []);
        if (!rows.length) {
            alert('还没有可复制的制作清单，请先生成单篇 AI 文案，或应用主题脚本');
            return;
        }
        this.copyText(rows.map(row => this.formatTtsManifestRowText(row)).join('\n\n---\n\n'));
    },

    async copyText(text) {
        try {
            await navigator.clipboard.writeText(text);
            alert('已复制到剪贴板');
        } catch (_) {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.setAttribute('readonly', '');
            textarea.style.position = 'fixed';
            textarea.style.left = '-9999px';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            textarea.remove();
            alert('已复制到剪贴板');
        }
    },

    getActiveFullAudioInfo(task) {
        const fullAudio = task?.result?.full_audio || null;
        if (!fullAudio?.audio_id) return null;
        return {
            audioId: fullAudio.audio_id,
            filePath: fullAudio.file_path || '',
            fileName: fullAudio.file_name || '',
            matchedCount: fullAudio.matched_segment_count || 0
        };
    },

    renderAudioCard(audio, options = {}) {
        const { active = false, staleDuplicate = false } = options;
        const statusClass = ['matched', 'timeline_matched'].includes(audio.match_status) ? 'ok' : (audio.match_status === 'unmatched' ? 'warn' : '');
        const cardClass = [
            'rv-audio-item',
            active ? 'is-active' : '',
            staleDuplicate ? 'is-stale' : ''
        ].filter(Boolean).join(' ');
        const extraBadges = [
            active ? '<span class="rv-pill ok strong">当前生效音频</span>' : '',
            staleDuplicate ? '<span class="rv-pill">旧上传记录</span>' : ''
        ].filter(Boolean).join('');
        return `
            <article class="${cardClass}">
                <div>
                    <h4>${this.escape(audio.file_name)}</h4>
                    <p>${this.formatDuration(audio.duration_sec)} · ${this.escape(audio.segment_id || '未匹配 segment')}</p>
                    ${audio.error_message ? `<p class="rv-error-text">${this.escape(audio.error_message)}</p>` : ''}
                </div>
                <div class="rv-audio-status">
                    ${extraBadges}
                    <span class="rv-pill ${statusClass}">${this.formatStatus(audio.match_status)}</span>
                </div>
            </article>
        `;
    },

    renderAudios(task) {
        if (!this.els.audioList) return;
        const audios = task?.audios || [];
        this.els.audioSummary.textContent = `${audios.length} 个音频`;
        if (!audios.length) {
            this.els.audioList.innerHTML = '<div class="rv-empty">还没有上传音频。</div>';
            return;
        }
        const activeInfo = this.getActiveFullAudioInfo(task);
        const activeAudio = activeInfo ? audios.find(audio => audio.id === activeInfo.audioId) : null;
        const staleDuplicates = activeAudio
            ? audios.filter(audio =>
                audio.id !== activeAudio.id &&
                audio.match_status === 'unmatched' &&
                audio.file_name === activeAudio.file_name
            )
            : [];
        const staleIds = new Set(staleDuplicates.map(audio => audio.id));
        const primaryAudios = audios.filter(audio => !staleIds.has(audio.id) && (!activeAudio || audio.id !== activeAudio.id));

        const summaryText = activeAudio
            ? `<div class="rv-audio-callout">当前生成将使用 <strong>${this.escape(activeAudio.file_name)}</strong>，匹配到 ${activeInfo.matchedCount || 0} 个 segment。</div>`
            : '';
        const activeBlock = activeAudio ? this.renderAudioCard(activeAudio, { active: true }) : '';
        const primaryBlock = primaryAudios.map(audio => this.renderAudioCard(audio)).join('');
        const staleBlock = staleDuplicates.length ? `
            <details class="rv-audio-stale-group">
                <summary>旧上传记录 ${staleDuplicates.length} 条</summary>
                <div class="rv-audio-stale-list">
                    ${staleDuplicates.map(audio => this.renderAudioCard(audio, { staleDuplicate: true })).join('')}
                </div>
            </details>
        ` : '';

        this.els.audioList.innerHTML = [summaryText, activeBlock, primaryBlock, staleBlock].filter(Boolean).join('');
    },

    latestRenderPreview(task, previewType, ratio) {
        const normalized = previewType === 'clip' ? 'preview_clip' : previewType;
        return (task?.render_previews || [])
            .filter(preview => preview.preview_type === normalized && preview.ratio === ratio)
            .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))[0] || null;
    },

    previewApprovalMissing(task) {
        const ratios = task?.output_ratios || ['16:9'];
        const missing = [];
        ratios.forEach(ratio => {
            const keyframes = this.latestRenderPreview(task, 'keyframes', ratio);
            const clip = this.latestRenderPreview(task, 'preview_clip', ratio);
            if (!keyframes || keyframes.status !== 'approved') missing.push(`${ratio} 关键帧`);
            if (!clip || clip.status !== 'approved') missing.push(`${ratio} 里程碑样片组`);
        });
        return missing;
    },

    renderRenderPreviews(task) {
        if (!this.els.renderPreviewList) return;
        const ratios = task?.output_ratios || ['16:9'];
        if (!task) {
            this.els.renderPreviewSummary.textContent = '等待任务';
            this.els.renderPreviewList.innerHTML = '<div class="rv-empty">选择任务后生成里程碑关键帧和样片组。</div>';
            return;
        }
        if (task.summary_only) {
            this.els.renderPreviewSummary.textContent = '加载中';
            this.els.renderPreviewList.innerHTML = '<div class="rv-empty">正在加载任务详情...</div>';
            return;
        }

        const missing = this.previewApprovalMissing(task);
        this.els.renderPreviewSummary.textContent = missing.length ? `待确认 ${missing.length} 项` : '预览已确认';
        this.els.generateVideoBtn.disabled = !!missing.length;
        this.els.renderPreviewList.innerHTML = ratios.map(ratio => {
            const keyframes = this.latestRenderPreview(task, 'keyframes', ratio);
            const clip = this.latestRenderPreview(task, 'preview_clip', ratio);
            const ratioMissing = missing.filter(item => item.startsWith(ratio));
            return `
                <article class="rv-render-preview-item">
                    <div class="rv-render-preview-head">
                        <div>
                            <h4>${this.escape(ratio)} 质量闸门</h4>
                            <p>${this.escape(ratioMissing.join(' · ') || '可以正式生成')}</p>
                        </div>
                        <span class="rv-pill ${ratioMissing.length ? 'warn' : 'ok'}">${ratioMissing.length ? '待确认' : '已确认'}</span>
                    </div>
                    <div class="rv-render-preview-grid">
                        ${this.renderPreviewCard(keyframes, '关键帧', ratio)}
                        ${this.renderPreviewCard(clip, '里程碑样片组', ratio)}
                    </div>
                </article>
            `;
        }).join('');
    },

    renderPreviewCard(preview, label, ratio) {
        if (!preview) {
            return `
                <div class="rv-render-preview-card">
                    <div class="rv-render-preview-empty">${this.escape(label)}未生成</div>
                    <p>${this.escape(ratio)} · 等待生成</p>
                </div>
            `;
        }
        const statusClass = preview.status === 'approved' ? 'ok' : (preview.status === 'failed' || preview.status === 'rejected' ? 'bad' : (preview.status === 'completed' ? 'warn' : ''));
        const fileCount = (preview.file_paths || []).length;
        const milestones = preview.meta?.milestones || [];
        const isClip = preview.preview_type === 'preview_clip';
        const media = preview.status === 'completed' || preview.status === 'approved'
            ? (isClip
                ? `<div class="rv-preview-clip-list">${(preview.file_paths || []).map((_, index) => {
                    const milestone = milestones[index] || {};
                    const caption = milestone.label || `${label} ${index + 1}`;
                    return `
                        <figure class="rv-preview-clip">
                            <video controls preload="metadata" src="${this.API_BASE}/report-video/previews/${preview.id}/files/${index}"></video>
                            <figcaption>${this.escape(caption)}</figcaption>
                        </figure>
                    `;
                }).join('')}</div>`
                : `<div class="rv-keyframe-strip">${(preview.file_paths || []).map((_, index) => {
                    const milestone = milestones[index] || {};
                    const caption = milestone.label || `${label} ${index + 1}`;
                    const href = `${this.API_BASE}/report-video/previews/${preview.id}/files/${index}`;
                    return `
                        <figure class="rv-keyframe">
                            <a class="rv-keyframe-link" href="${href}" target="_blank" rel="noopener" title="打开大图">
                                <img src="${href}" alt="${this.escape(caption)}">
                            </a>
                            <figcaption>${this.escape(caption)}</figcaption>
                            <button
                                class="rv-milestone-clip-btn"
                                type="button"
                                data-generate-milestone-clip="1"
                                data-preview-id="${preview.id}"
                                data-milestone-index="${index}"
                            >生成该样片</button>
                        </figure>
                    `;
                }).join('')}</div>`)
            : `<div class="rv-render-preview-empty">${this.escape(this.formatStatus(preview.status))}</div>`;
        const fileSummary = isClip
            ? `${fileCount || preview.meta?.clip_count || 0} 个样片`
            : `${fileCount || preview.meta?.frame_count || 0} 张关键帧`;
        return `
            <div class="rv-render-preview-card">
                <div class="rv-render-preview-card-head">
                    <strong>${this.escape(label)}</strong>
                    <span class="rv-pill ${statusClass}">${this.escape(this.formatStatus(preview.status))}</span>
                </div>
                ${media}
                <p>${this.escape(fileSummary)} · ${this.escape(preview.renderer_version || '-')}</p>
                ${preview.error_message ? `<p class="rv-error-text">${this.escape(preview.error_message)}</p>` : ''}
                ${preview.reject_reason ? `<p class="rv-error-text">${this.escape(preview.reject_reason)}</p>` : ''}
            </div>
        `;
    },

    async generateRenderPreview(type) {
        const task = this.getActiveTask();
        if (!task) {
            alert('请先选择任务');
            return;
        }
        const readyReports = (task.reports || []).filter(report => report.analysis && (report.segments || []).length);
        if (!readyReports.length) {
            alert('请先生成单篇 AI 文案，或在主题策划中生成并应用主题脚本');
            return;
        }
        const isClip = type === 'clip';
        const button = isClip ? this.els.generatePreviewClipBtn : this.els.generateKeyframesBtn;
        const endpoint = isClip ? 'clip' : 'keyframes';
        this.setButtonLoading(button, true, isClip ? '样片生成中...' : '关键帧生成中...');
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/previews/${endpoint}/generate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ratios: task.output_ratios || ['16:9'], duration_sec: 10 })
            });
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
        } catch (error) {
            alert(`${isClip ? '样片组' : '关键帧'}生成失败：${error.message}`);
        } finally {
            this.setButtonLoading(button, false, isClip ? '生成样片组' : '生成关键帧');
        }
    },

    async generateMilestoneClip(previewId, milestoneIndex, button) {
        const task = this.getActiveTask();
        if (!task) {
            alert('请先选择任务');
            return;
        }
        const preview = (task.render_previews || []).find(item => item.id === previewId);
        if (!preview) {
            alert('没有找到对应的关键帧预览');
            return;
        }
        const milestone = (preview.meta?.milestones || [])[milestoneIndex] || {};
        const payloadMilestone = {
            type: milestone.type || (milestone.segment_id ? 'segment' : 'theme_opening'),
            segment_id: milestone.segment_id || null,
            label: milestone.label || `关键帧 ${milestoneIndex + 1}`
        };
        this.setButtonLoading(button, true, '生成中...');
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/previews/clip/generate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ratios: [preview.ratio || this.storyboardRatio || '16:9'],
                    duration_sec: 10,
                    milestones: [payloadMilestone]
                })
            });
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
        } catch (error) {
            alert(`该里程碑样片生成失败：${error.message}`);
        } finally {
            this.setButtonLoading(button, false, '生成该样片');
        }
    },

    async approveRenderPreview(type) {
        const task = this.getActiveTask();
        if (!task) return;
        const isClip = type === 'clip';
        const button = isClip ? this.els.approvePreviewClipBtn : this.els.approveKeyframesBtn;
        this.setButtonLoading(button, true, '确认中...');
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/previews/${isClip ? 'clip' : 'keyframes'}/approve`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ratios: task.output_ratios || ['16:9'] })
            });
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
        } catch (error) {
            alert(`确认失败：${error.message}`);
        } finally {
            this.setButtonLoading(button, false, isClip ? '确认样片组' : '确认关键帧');
        }
    },

    async rejectRenderPreview(type) {
        const task = this.getActiveTask();
        if (!task) return;
        const reason = prompt('退回原因', '画面需要重新调整');
        if (reason === null) return;
        const isClip = type === 'clip';
        const button = isClip ? this.els.rejectPreviewClipBtn : this.els.rejectKeyframesBtn;
        this.setButtonLoading(button, true, '退回中...');
        try {
            const data = await this.api(`/report-video/tasks/${task.id}/previews/${isClip ? 'clip' : 'keyframes'}/reject`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ratios: task.output_ratios || ['16:9'], reason })
            });
            this.upsertTask(data.data.task);
            this.renderTasks();
            this.renderActiveTask();
        } catch (error) {
            alert(`退回失败：${error.message}`);
        } finally {
            this.setButtonLoading(button, false, isClip ? '退回样片组' : '退回关键帧');
        }
    },

    renderOutputs(outputs) {
        if (!this.els.videoOutputList) return;
        this.els.videoSummary.textContent = `${outputs.length} 个视频`;
        if (!outputs.length) {
            this.els.videoOutputList.innerHTML = '<div class="rv-empty">还没有生成视频。</div>';
            return;
        }
        this.els.videoOutputList.innerHTML = outputs.map(output => `
            <article class="rv-output-item">
                <div>
                    <h4>${this.escape(output.ratio)} · ${this.escape(output.video_type || '视频')}</h4>
                    <p>${this.formatStatus(output.status)} · ${this.formatDuration(output.duration_sec)} · ${this.escape(output.file_path || '-')}</p>
                    ${output.error_message ? `<p class="rv-error-text">${this.escape(output.error_message)}</p>` : ''}
                </div>
                ${output.status === 'completed' ? `<button type="button" data-download-output="${output.id}">下载</button>` : ''}
            </article>
        `).join('');
        this.els.videoOutputList.querySelectorAll('[data-download-output]').forEach(button => {
            button.addEventListener('click', () => {
                window.open(`${this.API_BASE}/report-video/outputs/${button.dataset.downloadOutput}/download`, '_blank');
            });
        });
    },

    getActiveTask() {
        return this.tasks.find(task => task.id === this.activeTaskId) || null;
    },

    getSelectedPrompt() {
        return this.prompts.find(prompt => prompt.id === this.selectedPromptId) || null;
    },

    upsertTask(nextTask) {
        const index = this.tasks.findIndex(task => task.id === nextTask.id);
        if (index >= 0) this.tasks[index] = nextTask;
        else this.tasks.unshift(nextTask);
    },

    setButtonLoading(button, loading, text) {
        if (!button) return;
        button.disabled = loading;
        button.textContent = text;
    },

    renderError(container, message) {
        if (!container) return;
        container.innerHTML = `<div class="rv-empty">加载失败：${this.escape(message)}</div>`;
    },

    renderToastHint(message) {
        let toast = document.getElementById('rv-toast-hint');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'rv-toast-hint';
            toast.className = 'rv-toast-hint';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.classList.add('show');
        window.clearTimeout(this.toastTimer);
        this.toastTimer = window.setTimeout(() => toast.classList.remove('show'), 3600);
    },

    formatStatus(status) {
        const map = {
            created: '已创建',
            uploaded: '已上传',
            parsing: '解析中',
            parsed: '解析完成',
            parse_failed: '解析失败',
            ai_pending: '等待 AI',
            ai_generating: 'AI 生成中',
            ai_failed: 'AI 失败',
            ai_blocked: 'AI 阻塞',
            script_ready: '文案就绪',
            topic_cards_generating: '摘要卡生成中',
            topic_cards_ready: '摘要卡就绪',
            topic_cards_failed: '摘要卡失败',
            topic_clustering: '主题聚类中',
            topic_ready: '主题榜就绪',
            topic_failed: '主题聚类失败',
            topic_script_generating: '主题脚本生成中',
            topic_script_ready: '主题脚本就绪',
            topic_script_failed: '主题脚本失败',
            waiting_audio: '待上传音频',
            audio_processing: '音频处理中',
            audio_failed: '音频处理失败',
            waiting_video: '待生成视频',
            keyframes_generating: '关键帧生成中',
            keyframes_ready: '关键帧就绪',
            preview_generating: '样片生成中',
            preview_ready: '样片就绪',
            preview_partial: '预览部分完成',
            preview_failed: '预览失败',
            video_generating: '视频生成中',
            remotion_generating: 'Remotion 生成中',
            video_partial: '部分生成',
            video_failed: '视频失败',
            completed: '已完成',
            approved: '已确认',
            rejected: '已退回',
            failed: '失败',
            queued: '排队中',
            processing: '处理中',
            pending: '待处理',
            asr_queued: 'ASR 排队中',
            asr_processing: 'ASR 生成字幕中',
            matched: '已匹配',
            timeline_matched: '整片时间轴已匹配',
            unmatched: '未匹配'
        };
        return map[status] || status || '-';
    },

    formatDoubaoTtsStatus(status) {
        const map = {
            empty: '未投递',
            queued: '排队中',
            running: '生成中',
            completed: '音频就绪',
            partial_failed: '部分失败',
            failed: '失败'
        };
        return map[status] || status || '-';
    },

    formatDoubaoJobStatus(status) {
        const map = {
            queued: '排队中',
            running: '生成中',
            succeeded: '待回填',
            audio_ready: '已回填',
            failed: '失败',
            blocked: '验证阻塞',
            canceled: '已取消'
        };
        return map[status] || status || '-';
    },

    doubaoTtsStatusClass(status) {
        if (status === 'completed') return 'ok';
        if (status === 'partial_failed') return 'warn';
        if (status === 'failed') return 'bad';
        return '';
    },

    doubaoJobStatusClass(status) {
        if (status === 'audio_ready') return 'ok';
        if (['failed', 'blocked', 'canceled'].includes(status)) return 'bad';
        if (status === 'succeeded') return 'warn';
        return '';
    },

    formatAutoScanStatus(status) {
        const map = {
            new: '待制作',
            completed: '已制作',
            queued_or_processing: '处理中',
        };
        return map[status] || status || '-';
    },

    formatDuration(value) {
        const seconds = Number(value || 0);
        if (!seconds) return '-';
        const mins = Math.floor(seconds / 60);
        const rest = Math.round(seconds % 60);
        return mins ? `${mins}分${String(rest).padStart(2, '0')}秒` : `${rest}秒`;
    },

    formatVideoType(value) {
        const map = {
            daily_digest: '每日快报',
            single_report: '单篇解读',
            topic_mix: '主题串烧'
        };
        return map[value] || value || '-';
    },

    formatExtractionMethod(value) {
        const map = {
            pdf_text: 'PDF 文本',
            ocr: 'OCR 识别',
            pdf_text_low_quality: '低质量文本'
        };
        return map[value] || value || '-';
    },

    formatChineseOrdinal(value) {
        const map = ['零', '第一', '第二', '第三', '第四', '第五', '第六', '第七', '第八', '第九', '第十'];
        return map[value] || `第${value}`;
    },

    formatTime(value) {
        if (!value) return '-';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return new Intl.DateTimeFormat('zh-CN', {
            timeZone: 'Asia/Shanghai',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        }).format(date);
    },

    escape(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
};

document.addEventListener('DOMContentLoaded', () => ReportVideoApp.init());
