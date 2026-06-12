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
    prompts: [],

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
        videoOutputList: document.getElementById('video-output-list'),
        videoSummary: document.getElementById('video-summary'),
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
        closeTextModal: document.getElementById('close-text-modal')
    },

    init() {
        this.bindEvents();
        this.updateAudioModeUi();
        this.loadTasks();
        this.loadAutoScan();
        this.loadPrompts();
        this.refreshTimer = window.setInterval(() => this.refreshIfBusy(), 5000);
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
        this.els.copyAllTtsBtn?.addEventListener('click', () => this.copyAllTtsScripts());
        this.els.copyTtsManifestBtn?.addEventListener('click', () => this.copyTtsManifest());
        this.els.audioInput?.addEventListener('change', () => this.updateAudioFileSummary());
        this.els.timelineInput?.addEventListener('change', () => this.updateTimelineFileSummary());
        document.querySelectorAll('input[name="audio-mode"]').forEach(input => {
            input.addEventListener('change', () => this.updateAudioModeUi());
        });
        this.els.uploadAudioBtn?.addEventListener('click', () => this.uploadAudios());
        this.els.generateVideoBtn?.addEventListener('click', () => this.generateVideos());
        this.els.saveAutoScanBtn?.addEventListener('click', () => this.saveAutoScanConfig());
        this.els.previewAutoScanBtn?.addEventListener('click', () => this.previewAutoScan());
        this.els.runAutoScanBtn?.addEventListener('click', () => this.runAutoScan());
        this.els.refreshPromptsBtn?.addEventListener('click', () => this.loadPrompts());
        this.els.savePromptBtn?.addEventListener('click', () => this.saveSelectedPrompt());
        this.els.resetPromptBtn?.addEventListener('click', () => this.resetSelectedPrompt());
        this.els.closeTextModal?.addEventListener('click', () => this.closeTextModal());
        this.els.textModal?.addEventListener('click', (event) => {
            if (event.target === this.els.textModal) this.closeTextModal();
        });
        document.querySelectorAll('.rv-tabs button').forEach((button) => {
            button.addEventListener('click', () => this.switchTab(button.dataset.tab));
        });
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
            this.tasks = data.data || [];
            if (!this.activeTaskId && this.tasks.length) {
                this.activeTaskId = this.tasks[0].id;
            }
            this.renderTasks();
            this.renderActiveTask();
        } catch (error) {
            this.renderError(this.els.taskList, error.message);
        }
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

    refreshIfBusy() {
        const busyStatuses = new Set([
            'parsing',
            'ai_generating',
            'video_generating',
            'topic_cards_generating',
            'topic_clustering',
            'topic_script_generating',
            'audio_processing',
        ]);
        if (this.tasks.some(task => busyStatuses.has(task.status))) {
            this.loadTasks();
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
            return;
        }
        this.els.taskList.innerHTML = this.tasks.map(task => `
            <button class="rv-task-item ${task.id === this.activeTaskId ? 'active' : ''}" type="button" data-task-id="${task.id}">
                <h3>${this.escape(task.task_name)}</h3>
                <p>${this.formatStatus(task.status)} · ${task.report_count || 0} 篇研报 · ${this.formatTime(task.created_at)}</p>
            </button>
        `).join('');
        this.els.taskList.querySelectorAll('[data-task-id]').forEach(button => {
            button.addEventListener('click', () => {
                this.activeTaskId = button.dataset.taskId;
                this.selectedTopicId = null;
                this.renderTasks();
                this.renderActiveTask();
            });
        });
    },

    renderActiveTask() {
        const task = this.getActiveTask();
        this.els.emptyState.classList.toggle('hidden', !!task);
        this.els.workbench.classList.toggle('hidden', !task);
        if (!task) return;

        const reports = task.reports || [];
        this.els.activeTaskName.textContent = task.task_name;
        this.els.activeTaskMeta.textContent = `${this.formatVideoType(task.video_type)} · 默认 ${task.default_segment_count} 段 · ${task.output_ratios.join(' + ')}`;
        this.els.activeTaskStatus.textContent = this.formatStatus(task.status);
        this.els.reportSummary.textContent = `${reports.length} 篇`;
        this.renderReports(reports);
        this.renderTopicPlanning(task);
        this.renderScripts(reports);
        this.renderMindmaps(reports);
        this.renderTtsScripts(reports);
        this.renderAudios(task);
        this.renderOutputs(task.outputs || []);
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
            video_generating: '视频生成中',
            video_partial: '部分生成',
            video_failed: '视频失败',
            completed: '已完成',
            failed: '失败',
            pending: '待处理',
            asr_queued: 'ASR 排队中',
            asr_processing: 'ASR 生成字幕中',
            matched: '已匹配',
            timeline_matched: '整片时间轴已匹配',
            unmatched: '未匹配'
        };
        return map[status] || status || '-';
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
