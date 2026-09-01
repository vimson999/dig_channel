const SCRIPT_VIDEO_ACTIVE_POLL_MS = 10_000;

class ScriptVideoWorkflowPage {
    constructor() {
        this.workflow = null;
        this.jobId = new URLSearchParams(window.location.search).get('job_id') || '';
        const requestedStep = Number(new URLSearchParams(window.location.search).get('step') || 0);
        this.activeStep = Number.isInteger(requestedStep) && requestedStep >= 1 && requestedStep <= 5 ? requestedStep : 1;
        this.hasManualStep = Number.isInteger(requestedStep) && requestedStep >= 1 && requestedStep <= 5;
        this.visualSchemes = [];
        this.pollTimer = null;
        this.pollInFlight = false;
        this.waveformPeaks = null;
        this.waveformUrl = '';
        this.waveformLoading = false;
        this.previewTrimEnd = null;
        this.els = {
            providerStatus: document.getElementById('provider-status'),
            refreshStatus: document.getElementById('refresh-status-btn'),
            notice: document.getElementById('notice'),
            sourceText: document.getElementById('source-text'),
            charCount: document.getElementById('script-char-count'),
            visualScheme: document.getElementById('visual-scheme'),
            visualSchemeDescription: document.getElementById('visual-scheme-description'),
            createJob: document.getElementById('create-job-btn'),
            saveAudio: document.getElementById('save-audio-btn'),
            startAudio: document.getElementById('start-audio-btn'),
            uploadAudio: document.getElementById('upload-audio-btn'),
            audioUploadInput: document.getElementById('audio-upload-input'),
            audioSummary: document.getElementById('audio-summary'),
            audioChunks: document.getElementById('audio-chunks-editor'),
            audioEditor: document.getElementById('audio-editor-panel'),
            audioSourceMeta: document.getElementById('audio-source-meta'),
            audioSourcePlayer: document.getElementById('audio-source-player'),
            audioPlaySource: document.getElementById('audio-play-source-btn'),
            audioTimeline: document.getElementById('audio-timeline'),
            audioWaveform: document.getElementById('audio-waveform'),
            audioWaveformCanvas: document.getElementById('audio-waveform-canvas'),
            audioWaveformEmpty: document.getElementById('audio-waveform-empty'),
            audioExcludedStart: document.getElementById('audio-excluded-start'),
            audioSelectionOverlay: document.getElementById('audio-selection-overlay'),
            audioExcludedEnd: document.getElementById('audio-excluded-end'),
            audioPlayhead: document.getElementById('audio-playhead'),
            audioPlayheadTime: document.getElementById('audio-playhead-time'),
            audioSelectionTime: document.getElementById('audio-selection-time'),
            audioTimeStart: document.getElementById('audio-time-start'),
            audioTimeEnd: document.getElementById('audio-time-end'),
            audioSpeedPresets: document.getElementById('audio-speed-presets'),
            audioTrimStart: document.getElementById('audio-trim-start'),
            audioTrimStartRange: document.getElementById('audio-trim-start-range'),
            audioTrimEnd: document.getElementById('audio-trim-end'),
            audioTrimEndRange: document.getElementById('audio-trim-end-range'),
            audioSpeed: document.getElementById('audio-speed'),
            previewAudioEdit: document.getElementById('preview-audio-edit-btn'),
            resetAudioEdit: document.getElementById('reset-audio-edit-btn'),
            finalizeAudioEdit: document.getElementById('finalize-audio-edit-btn'),
            audioPanel: document.getElementById('audio-player-panel'),
            audioPlayer: document.getElementById('audio-player'),
            audioDownload: document.getElementById('audio-download-link'),
            startStoryboard: document.getElementById('start-storyboard-btn'),
            storyboardRunStatus: document.getElementById('storyboard-run-status'),
            saveScenes: document.getElementById('save-scenes-btn'),
            scenesSummary: document.getElementById('scenes-summary'),
            scenes: document.getElementById('scenes-editor'),
            startImages: document.getElementById('start-images-btn'),
            imagesSummary: document.getElementById('images-summary'),
            imagesGrid: document.getElementById('images-grid'),
            startRender: document.getElementById('start-render-btn'),
            renderSummary: document.getElementById('render-summary'),
            alignment: document.getElementById('alignment-summary'),
            videoPanel: document.getElementById('video-panel'),
            videoPlayer: document.getElementById('video-player'),
            videoDownload: document.getElementById('video-download-link'),
            refreshWorkflow: document.getElementById('refresh-workflow-btn'),
            currentJobId: document.getElementById('current-job-id'),
            workflowProgress: document.getElementById('workflow-progress'),
            workflowProgressLabel: document.getElementById('workflow-progress-label'),
            workflowStage: document.getElementById('workflow-stage'),
            workflowError: document.getElementById('workflow-error'),
            workflowStepNav: document.getElementById('workflow-step-nav'),
            workflowPrev: document.getElementById('workflow-prev-btn'),
            workflowNext: document.getElementById('workflow-next-btn'),
            workflowStepLabel: document.getElementById('workflow-step-label'),
            workflowStepHint: document.getElementById('workflow-step-hint'),
            loadJobs: document.getElementById('load-jobs-btn'),
            recentJobs: document.getElementById('recent-jobs'),
            logs: document.getElementById('workflow-logs'),
        };
        this.bind();
        this.updateCharacterCount();
        this.bootstrap();
    }

    async bootstrap() {
        await this.refreshStatus();
        await this.loadJobs();
        if (this.jobId) await this.loadWorkflow();
    }

    bind() {
        this.els.sourceText.addEventListener('input', () => this.updateCharacterCount());
        this.els.visualScheme.addEventListener('change', () => this.renderVisualSchemeDescription());
        this.els.createJob.addEventListener('click', () => this.submitDraft());
        this.els.saveAudio.addEventListener('click', () => this.saveAudioChunks());
        this.els.startAudio.addEventListener('click', () => this.startAudio());
        this.els.uploadAudio.addEventListener('click', () => this.els.audioUploadInput.click());
        this.els.audioUploadInput.addEventListener('change', () => this.uploadAudio());
        this.els.audioPlaySource.addEventListener('click', () => this.toggleSourcePlayback());
        this.els.previewAudioEdit.addEventListener('click', () => this.previewAudioEdit());
        this.els.resetAudioEdit.addEventListener('click', () => this.resetAudioEdit());
        this.els.finalizeAudioEdit.addEventListener('click', () => this.finalizeAudioEdit());
        this.els.audioTrimStart.addEventListener('input', () => this.syncAudioTrimControls('start'));
        this.els.audioTrimStartRange.addEventListener('input', () => this.syncAudioTrimControls('start', true));
        this.els.audioTrimEnd.addEventListener('input', () => this.syncAudioTrimControls('end'));
        this.els.audioTrimEndRange.addEventListener('input', () => this.syncAudioTrimControls('end', true));
        this.els.audioSpeed.addEventListener('change', () => this.setAudioSpeed(this.els.audioSpeed.value));
        this.els.audioSpeedPresets.addEventListener('click', (event) => {
            const button = event.target.closest('[data-audio-speed]');
            if (button) this.setAudioSpeed(button.dataset.audioSpeed);
        });
        this.els.audioSourcePlayer.addEventListener('timeupdate', () => {
            this.stopPreviewAtTrimEnd();
            this.renderAudioTimeline();
        });
        this.els.audioSourcePlayer.addEventListener('loadedmetadata', () => {
            this.renderAudioTimeline();
            this.drawAudioWaveform();
        });
        this.els.audioSourcePlayer.addEventListener('play', () => this.renderSourcePlaybackButton());
        this.els.audioSourcePlayer.addEventListener('pause', () => this.renderSourcePlaybackButton());
        this.els.audioSourcePlayer.addEventListener('ended', () => this.renderSourcePlaybackButton());
        window.addEventListener('resize', () => this.drawAudioWaveform());
        this.els.startStoryboard.addEventListener('click', () => this.startStoryboard());
        this.els.saveScenes.addEventListener('click', () => this.saveScenes());
        this.els.startImages.addEventListener('click', () => this.startImages());
        this.els.startRender.addEventListener('click', () => this.startRender());
        this.els.refreshStatus.addEventListener('click', () => this.refreshStatus());
        this.els.refreshWorkflow.addEventListener('click', () => this.loadWorkflow());
        this.els.loadJobs.addEventListener('click', () => this.loadJobs());
        document.querySelectorAll('[data-stage-indicator][data-workflow-step]').forEach((button) => {
            button.addEventListener('click', () => this.goToStep(Number(button.dataset.workflowStep)));
        });
        this.els.workflowPrev.addEventListener('click', () => this.goToStep(this.activeStep - 1));
        this.els.workflowNext.addEventListener('click', () => this.goToStep(this.activeStep + 1));
        this.els.imagesGrid.addEventListener('click', (event) => {
            const button = event.target.closest('[data-retry-image]');
            if (button) this.retryImage(Number(button.dataset.retryImage));
        });
        this.els.recentJobs.addEventListener('click', (event) => {
            const button = event.target.closest('[data-open-job]');
            if (!button) return;
            this.jobId = button.dataset.openJob || '';
            this.syncUrl();
            this.loadWorkflow();
        });
        document.addEventListener('visibilitychange', () => this.handleVisibilityChange());
        window.addEventListener('beforeunload', () => this.stopPolling());
    }

    apiRoot() {
        const configured = window.API_BASE_URL || window.CONFIG?.API_BASE_URL || '';
        if (configured) return configured.replace(/\/api\/v1\/?$/, '').replace(/\/$/, '');
        if (['localhost', '127.0.0.1'].includes(window.location.hostname) && window.location.port !== '5001') {
            return `${window.location.protocol}//${window.location.hostname}:5001`;
        }
        return window.location.origin;
    }

    token() {
        return localStorage.getItem('access_token') || localStorage.getItem('token') || '';
    }

    async request(path, options = {}) {
        const hasJsonBody = Boolean(options.body) && !(options.body instanceof FormData);
        const response = await fetch(`${this.apiRoot()}${path}`, {
            ...options,
            credentials: 'include',
            headers: {
                ...(hasJsonBody ? {'Content-Type': 'application/json'} : {}),
                ...(this.token() ? {Authorization: `Bearer ${this.token()}`} : {}),
                ...(options.headers || {}),
            },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.success === false) throw new Error(payload.message || `HTTP ${response.status}`);
        return payload;
    }

    async refreshStatus() {
        try {
            const result = await this.request('/api/script-video-workflows/status');
            const data = result.data || {};
            this.visualSchemes = Array.isArray(data.visual_schemes) ? data.visual_schemes : [];
            this.renderVisualSchemes();
            const doubao = data.providers?.doubao_browser_tts || {};
            const gemini = data.providers?.gemini_browser || {};
            const configured = Boolean(doubao.configured && gemini.configured);
            this.els.providerStatus.textContent = configured ? '豆包 / Gemini 浏览器已配置' : '浏览器配置待检查';
            this.els.providerStatus.className = `rounded-lg border px-3 py-2 text-xs font-bold ${configured ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`;
        } catch (error) {
            this.els.providerStatus.textContent = '无法读取浏览器配置';
            this.els.providerStatus.className = 'rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-bold text-red-700';
        }
    }

    renderVisualSchemes() {
        const previous = this.els.visualScheme.value || this.workflow?.visual_scheme?.key || 'stick_figure';
        const schemes = this.visualSchemes.length ? this.visualSchemes : [{key: 'stick_figure', name: '白板火柴人', description: '黑白极简白板火柴人、少量点缀色、中文标签、16:9', configured: true}];
        this.els.visualScheme.replaceChildren();
        schemes.forEach((scheme) => {
            const option = document.createElement('option');
            option.value = scheme.key;
            option.textContent = `${scheme.name}${scheme.configured === false ? '（待配置）' : ''}`;
            option.disabled = scheme.configured === false;
            this.els.visualScheme.appendChild(option);
        });
        this.els.visualScheme.value = schemes.some((item) => item.key === previous && item.configured !== false)
            ? previous
            : (schemes.find((item) => item.configured !== false)?.key || schemes[0].key);
        this.renderVisualSchemeDescription();
    }

    renderVisualSchemeDescription() {
        const selected = this.visualSchemes.find((item) => item.key === this.els.visualScheme.value);
        this.els.visualSchemeDescription.textContent = selected?.description || '视觉方案只影响 Gemini 图片提示词契约；不会改变语音与视频对齐。';
    }

    updateCharacterCount() {
        this.els.charCount.textContent = `${this.els.sourceText.value.trim().length} 字`;
    }

    async submitDraft() {
        const text = this.els.sourceText.value.trim();
        if (!text) return this.showNotice('请先输入整篇文案。', 'error');
        await this.withButton(this.els.createJob, '创建中', async () => {
            const result = await this.request('/api/script-video-workflows/jobs', {
                method: 'POST',
                body: JSON.stringify({text, visual_scheme_key: this.els.visualScheme.value}),
            });
            this.setWorkflow(result.data);
            this.showNotice('文案任务已创建。请确认语音分段，再填写分镜图片提示词。', 'success');
            await this.loadJobs();
        });
    }

    async saveAudioChunks() {
        if (!this.requireWorkflow()) return;
        const audioChunks = Array.from(this.els.audioChunks.querySelectorAll('[data-audio-chunk]')).map((input) => input.value);
        await this.withButton(this.els.saveAudio, '保存中', async () => {
            const result = await this.request(`/api/script-video-workflows/jobs/${encodeURIComponent(this.jobId)}/audio-chunks`, {
                method: 'PATCH', body: JSON.stringify({audio_chunks: audioChunks}),
            });
            this.setWorkflow(result.data);
            this.showNotice('语音分段已保存。', 'success');
        });
    }

    async startAudio() {
        if (!this.requireWorkflow()) return;
        await this.withButton(this.els.startAudio, '投递中', async () => {
            const result = await this.request(`/api/script-video-workflows/jobs/${encodeURIComponent(this.jobId)}/audio`, {method: 'POST', body: JSON.stringify({})});
            this.setWorkflow(result.data?.workflow || result.data);
            this.showNotice(result.message || '已投递豆包浏览器语音阶段。', 'success');
            this.startPolling();
        });
    }

    async uploadAudio() {
        if (!this.requireWorkflow()) return;
        const file = this.els.audioUploadInput.files?.[0];
        if (!file) return;
        const formData = new FormData();
        formData.append('audio', file);
        await this.withButton(this.els.uploadAudio, '上传中', async () => {
            const result = await this.request(`/api/script-video-workflows/jobs/${encodeURIComponent(this.jobId)}/audio/upload`, {
                method: 'POST', body: formData,
            });
            this.setWorkflow(result.data?.workflow || result.data);
            this.showNotice(result.message || '音频已上传，正在转换为可编辑 WAV。', 'success');
            this.startPolling();
        });
        this.els.audioUploadInput.value = '';
    }

    readAudioEdit() {
        const start = Number(this.els.audioTrimStart.value);
        const end = Number(this.els.audioTrimEnd.value);
        const speed = Number(this.els.audioSpeed.value);
        const duration = this.audioSourceSeconds();
        if (![start, end, speed].every(Number.isFinite)) throw new Error('请填写有效的裁剪时间和倍速。');
        if (start < 0 || end > duration + 0.05 || end - start < 0.1) throw new Error('请保留至少 0.1 秒语音，并确保结尾不超过原始时长。');
        return {trim_start_seconds: start, trim_end_seconds: Math.min(end, duration), speed};
    }

    async finalizeAudioEdit() {
        if (!this.requireWorkflow()) return;
        let edit;
        try {
            edit = this.readAudioEdit();
        } catch (error) {
            this.showNotice(error.message || '语音编辑参数不正确。', 'error');
            return;
        }
        await this.withButton(this.els.finalizeAudioEdit, '生成中', async () => {
            const result = await this.request(`/api/script-video-workflows/jobs/${encodeURIComponent(this.jobId)}/audio/edit`, {
                method: 'POST', body: JSON.stringify(edit),
            });
            this.setWorkflow(result.data?.workflow || result.data);
            this.showNotice(result.message || '正在生成最终 WAV。', 'success');
            this.startPolling();
        });
    }

    previewAudioEdit() {
        if (!this.audioSourceReady()) return;
        let edit;
        try {
            edit = this.readAudioEdit();
        } catch (error) {
            this.showNotice(error.message || '语音编辑参数不正确。', 'error');
            return;
        }
        const player = this.els.audioSourcePlayer;
        this.setAudioSpeed(String(edit.speed));
        player.currentTime = edit.trim_start_seconds;
        this.previewTrimEnd = edit.trim_end_seconds;
        const playing = player.play();
        if (playing?.catch) playing.catch(() => this.showNotice('浏览器无法自动播放，请点击播放器开始试听。', 'error'));
    }

    stopPreviewAtTrimEnd() {
        const end = Number(this.previewTrimEnd);
        const player = this.els.audioSourcePlayer;
        if (Number.isFinite(end) && end > 0 && player.currentTime >= end) {
            player.pause();
            player.currentTime = end;
            this.previewTrimEnd = null;
            this.renderSourcePlaybackButton();
        }
    }

    resetAudioEdit() {
        const duration = this.audioSourceSeconds();
        if (!duration) return;
        this.els.audioTrimStart.value = '0';
        this.els.audioTrimEnd.value = String(duration);
        this.els.audioTrimStartRange.value = '0';
        this.els.audioTrimEndRange.value = String(duration);
        this.setAudioSpeed('1');
        this.previewTrimEnd = null;
        this.renderAudioTimeline();
        this.showNotice('已恢复为原始语音；点击“生成最终语音”后才会生效。', 'success');
    }

    syncAudioTrimControls(edge, fromRange = false) {
        const source = edge === 'start'
            ? (fromRange ? this.els.audioTrimStartRange : this.els.audioTrimStart)
            : (fromRange ? this.els.audioTrimEndRange : this.els.audioTrimEnd);
        const target = edge === 'start'
            ? (fromRange ? this.els.audioTrimStart : this.els.audioTrimStartRange)
            : (fromRange ? this.els.audioTrimEnd : this.els.audioTrimEndRange);
        const duration = this.audioSourceSeconds();
        const value = Math.max(0, Math.min(duration, Number(source.value) || 0));
        source.value = String(value);
        target.value = String(value);
        if (edge === 'start' && value > Number(this.els.audioTrimEnd.value || duration)) {
            this.els.audioTrimEnd.value = String(value);
            this.els.audioTrimEndRange.value = String(value);
        }
        if (edge === 'end' && value < Number(this.els.audioTrimStart.value || 0)) {
            this.els.audioTrimStart.value = String(value);
            this.els.audioTrimStartRange.value = String(value);
        }
        this.renderAudioTimeline();
    }

    setAudioSpeed(value, {render = true} = {}) {
        const speed = String(value || '1');
        if (this.els.audioSpeed) this.els.audioSpeed.value = speed;
        if (this.els.audioSourcePlayer) this.els.audioSourcePlayer.playbackRate = Number(speed) || 1;
        if (this.els.audioSpeedPresets) {
            this.els.audioSpeedPresets.querySelectorAll('[data-audio-speed]').forEach((button) => {
                const active = String(button.dataset.audioSpeed) === speed;
                button.classList.toggle('border-indigo-500', active);
                button.classList.toggle('bg-indigo-100', active);
                button.classList.toggle('text-indigo-700', active);
                button.classList.toggle('border-slate-200', !active);
            });
        }
        if (render) this.renderAudioTimeline();
    }

    toggleSourcePlayback() {
        if (!this.audioSourceReady()) return;
        const player = this.els.audioSourcePlayer;
        if (player.paused) {
            const start = Number(this.els.audioTrimStart?.value || 0);
            const end = Number(this.els.audioTrimEnd?.value || this.audioSourceSeconds());
            player.currentTime = Math.max(0, Math.min(start, this.audioSourceSeconds()));
            this.previewTrimEnd = Number.isFinite(end) && end > start ? end : null;
            const playing = player.play();
            if (playing?.catch) playing.catch(() => this.showNotice('浏览器无法自动播放，请点击原生播放器开始试听。', 'error'));
        } else {
            player.pause();
            this.previewTrimEnd = null;
        }
        this.renderSourcePlaybackButton();
    }

    renderSourcePlaybackButton() {
        const button = this.els.audioPlaySource;
        const player = this.els.audioSourcePlayer;
        if (!button || !player) return;
        button.innerHTML = player.paused
            ? '<i class="fas fa-play mr-1"></i>播放原音'
            : '<i class="fas fa-pause mr-1"></i>暂停播放';
    }

    renderAudioTimeline() {
        if (!this.els.audioTimeline) return;
        const duration = this.audioSourceSeconds();
        const start = Math.max(0, Math.min(duration, Number(this.els.audioTrimStart?.value || 0)));
        const end = Math.max(start, Math.min(duration, Number(this.els.audioTrimEnd?.value || duration)));
        const current = Math.max(0, Math.min(duration || 0, Number(this.els.audioSourcePlayer?.currentTime || 0)));
        const startPercent = duration ? (start / duration) * 100 : 0;
        const endPercent = duration ? (end / duration) * 100 : 100;
        const currentPercent = duration ? (current / duration) * 100 : 0;
        if (this.els.audioExcludedStart) this.els.audioExcludedStart.style.width = `${startPercent}%`;
        if (this.els.audioSelectionOverlay) {
            this.els.audioSelectionOverlay.style.left = `${startPercent}%`;
            this.els.audioSelectionOverlay.style.width = `${Math.max(0, endPercent - startPercent)}%`;
        }
        if (this.els.audioExcludedEnd) this.els.audioExcludedEnd.style.width = `${Math.max(0, 100 - endPercent)}%`;
        if (this.els.audioPlayhead) this.els.audioPlayhead.style.left = `${currentPercent}%`;
        if (this.els.audioPlayheadTime) this.els.audioPlayheadTime.textContent = this.formatClock(current);
        if (this.els.audioSelectionTime) this.els.audioSelectionTime.textContent = duration
            ? `保留 ${this.formatClock(end - start)} · ${this.els.audioSpeed?.value || '1'}x`
            : '请选择开头和结尾';
        if (this.els.audioTimeStart) this.els.audioTimeStart.textContent = this.formatClock(0);
        if (this.els.audioTimeEnd) this.els.audioTimeEnd.textContent = this.formatClock(duration);
    }

    drawAudioWaveform() {
        const canvas = this.els.audioWaveformCanvas;
        const container = this.els.audioWaveform;
        if (!canvas || !container || typeof canvas.getContext !== 'function') return;
        const width = Math.max(320, Math.floor(container.clientWidth || 720));
        const height = Math.max(80, Math.floor(container.clientHeight || 112));
        const ratio = Math.max(1, Number(window.devicePixelRatio || 1));
        canvas.width = width * ratio;
        canvas.height = height * ratio;
        canvas.style.width = `${width}px`;
        canvas.style.height = `${height}px`;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.clearRect(0, 0, width, height);
        const peaks = Array.isArray(this.waveformPeaks) && this.waveformPeaks.length
            ? this.waveformPeaks
            : Array.from({length: 160}, (_, index) => 0.18 + (Math.sin(index * 0.67) ** 2) * 0.5 + (Math.sin(index * 0.17 + 1.5) ** 2) * 0.25);
        const barWidth = width / peaks.length;
        peaks.forEach((peak, index) => {
            const amplitude = Math.max(0.08, Math.min(1, Number(peak) || 0.08));
            const barHeight = Math.max(3, amplitude * (height - 18));
            const x = index * barWidth + 0.5;
            const y = (height - barHeight) / 2;
            ctx.fillStyle = index % 5 === 0 ? '#a5b4fc' : '#818cf8';
            ctx.fillRect(x, y, Math.max(1, barWidth - 1.2), barHeight);
        });
        if (this.els.audioWaveformEmpty) this.els.audioWaveformEmpty.classList.add('hidden');
    }

    async loadAudioWaveform(sourceUrl) {
        if (!sourceUrl || this.waveformLoading || this.waveformUrl === sourceUrl) return;
        this.waveformUrl = sourceUrl;
        this.waveformLoading = true;
        try {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            if (typeof window.fetch !== 'function' || !AudioContextClass) return;
            const response = await window.fetch(sourceUrl, {credentials: 'include'});
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const contentLength = Number(response.headers?.get?.('content-length') || 0);
            if (contentLength > 120 * 1024 * 1024) throw new Error('音频文件过大，使用预览波形');
            const bytes = await response.arrayBuffer();
            const context = new AudioContextClass();
            try {
                const buffer = await context.decodeAudioData(bytes);
                const channel = buffer.getChannelData(0);
                const samples = 160;
                const bucket = Math.max(1, Math.floor(channel.length / samples));
                this.waveformPeaks = Array.from({length: samples}, (_, index) => {
                    const offset = index * bucket;
                    const limit = Math.min(channel.length, offset + bucket);
                    let sum = 0;
                    for (let cursor = offset; cursor < limit; cursor += 1) sum += channel[cursor] ** 2;
                    return Math.sqrt(sum / Math.max(1, limit - offset));
                });
            } finally {
                if (typeof context.close === 'function') await context.close();
            }
        } catch (error) {
            this.waveformPeaks = null;
        } finally {
            this.waveformLoading = false;
            this.drawAudioWaveform();
        }
    }

    async startStoryboard() {
        if (!this.requireWorkflow()) return;
        await this.withButton(this.els.startStoryboard, '启动浏览器', async () => {
            const result = await this.request(`/api/script-video-workflows/jobs/${encodeURIComponent(this.jobId)}/storyboard`, {method: 'POST', body: JSON.stringify({})});
            this.setWorkflow(result.data?.workflow || this.workflow);
            this.showNotice(result.message || '已投递 Gemini 规划分镜，正在唤起浏览器。', 'success');
            this.startPolling();
        });
    }

    async saveScenes() {
        if (!this.requireWorkflow()) return;
        const scenes = Array.from(this.els.scenes.querySelectorAll('[data-scene-row]')).map((row) => ({
            sequence: Number(row.querySelector('[data-scene-sequence]').value),
            script_text: row.querySelector('[data-scene-text]').value,
            image_prompt: row.querySelector('[data-scene-prompt]').value,
        }));
        await this.withButton(this.els.saveScenes, '保存中', async () => {
            const result = await this.request(`/api/script-video-workflows/jobs/${encodeURIComponent(this.jobId)}/scenes`, {
                method: 'PATCH', body: JSON.stringify({scenes}),
            });
            this.setWorkflow(result.data);
            this.showNotice('分镜已保存；只有变化分镜的图片任务会失效。', 'success');
        });
    }

    async startImages() {
        if (!this.requireWorkflow()) return;
        try {
            await this.saveScenes();
        } catch (error) {
            return;
        }
        await this.withButton(this.els.startImages, '投递中', async () => {
            const result = await this.request(`/api/script-video-workflows/jobs/${encodeURIComponent(this.jobId)}/images`, {method: 'POST', body: JSON.stringify({})});
            this.setWorkflow(result.data?.workflow || this.workflow);
            this.showNotice(result.message || '已投递 Gemini 图片任务。', 'success');
            this.startPolling();
        });
    }

    async retryImage(sequence) {
        if (!this.requireWorkflow() || !Number.isInteger(sequence) || sequence < 1) return;
        try {
            const result = await this.request(`/api/script-video-workflows/jobs/${encodeURIComponent(this.jobId)}/scenes/${sequence}/image/retry`, {method: 'POST', body: JSON.stringify({})});
            this.setWorkflow(result.data?.workflow || this.workflow);
            this.showNotice(`已重新投递第 ${sequence} 段图片。`, 'success');
            this.startPolling();
        } catch (error) {
            this.showNotice(`图片重试失败：${error.message}`, 'error');
        }
    }

    async startRender() {
        if (!this.requireWorkflow()) return;
        await this.withButton(this.els.startRender, '投递中', async () => {
            const result = await this.request(`/api/script-video-workflows/jobs/${encodeURIComponent(this.jobId)}/render`, {method: 'POST', body: JSON.stringify({})});
            this.setWorkflow(result.data?.workflow || this.workflow);
            this.showNotice(result.message || '已投递音画对齐与 MP4 合成。', 'success');
            this.startPolling();
        });
    }

    async loadWorkflow({poll = false} = {}) {
        if (!this.jobId) return;
        if (this.pollInFlight) return;
        this.pollInFlight = true;
        let refreshFull = false;
        try {
            const suffix = poll ? '?poll=1' : '';
            const previousStage = String(this.workflow?.stage || this.workflow?.step || '');
            const result = await this.request(`/api/script-video-workflows/jobs/${encodeURIComponent(this.jobId)}${suffix}`);
            const payload = result.data || result;
            if (poll) {
                this.mergeWorkflowPoll(payload);
                const nextStage = String(payload?.stage || payload?.step || '');
                refreshFull = ['storyboard_queued', 'storyboard_processing'].includes(previousStage)
                    && !['storyboard_queued', 'storyboard_processing'].includes(nextStage);
            } else {
                this.setWorkflow(payload);
            }
        } catch (error) {
            this.showNotice(`读取任务失败：${error.message}`, 'error');
        } finally {
            this.pollInFlight = false;
        }
        if (refreshFull) await this.loadWorkflow();
    }

    async loadJobs() {
        try {
            const result = await this.request('/api/script-video-workflows/jobs?per_page=12&summary=1');
            this.renderRecentJobs(result.data?.items || result.items || []);
        } catch (error) {
            this.els.recentJobs.replaceChildren(this.textNode('读取历史任务失败。', 'text-xs text-red-600'));
        }
    }

    setWorkflow(workflow) {
        if (!workflow || !workflow.job_id) return;
        const isFirstWorkflow = !this.workflow;
        this.workflow = workflow;
        this.jobId = workflow.job_id;
        if (isFirstWorkflow && (!this.hasManualStep || !this.canVisitStep(this.activeStep))) {
            this.activeStep = this.suggestedStep();
            this.hasManualStep = false;
        } else if (!this.hasManualStep) {
            this.activeStep = this.suggestedStep();
        }
        this.syncUrl();
        if (!this.els.sourceText.value || this.els.sourceText.value.trim() === workflow.source_text?.trim()) {
            this.els.sourceText.value = workflow.source_text || '';
            this.updateCharacterCount();
        }
        if (workflow.visual_scheme?.key) this.els.visualScheme.value = workflow.visual_scheme.key;
        this.renderAll();
        if (this.isPollingNeeded()) this.startPolling(); else this.stopPolling();
    }

    mergeWorkflowPoll(payload) {
        if (!this.workflow || !payload?.partial) {
            this.setWorkflow(payload);
            return;
        }
        const {scene_updates: updates = [], partial: _partial, ...dynamic} = payload;
        const updatesByScene = new Map(updates.map((update) => [
            `${Number(update.sequence)}:${update.image_scene_hash || ''}`,
            update,
        ]));
        const scenes = (this.workflow.scenes || []).map((scene) => {
            const key = `${Number(scene.sequence)}:${scene.image_scene_hash || ''}`;
            const update = updatesByScene.get(key);
            return update ? {...scene, ...update} : scene;
        });
        this.setWorkflow({...this.workflow, ...dynamic, scenes});
    }

    renderAll() {
        const workflow = this.workflow;
        if (!workflow) return;
        this.els.currentJobId.textContent = workflow.job_id;
        const progress = Math.max(0, Math.min(100, Number(workflow.progress || 0)));
        this.els.workflowProgress.style.width = `${progress}%`;
        this.els.workflowProgressLabel.textContent = `${progress}%`;
        this.els.workflowStage.textContent = workflow.stage || workflow.step || '处理中';
        this.els.workflowError.textContent = workflow.error || '';
        this.els.workflowError.classList.toggle('hidden', !workflow.error);
        this.renderIndicators();
        this.renderStepVisibility();
        this.renderStepNav();
        this.renderAudio();
        this.renderScenes();
        this.renderImages();
        this.renderRenderStage();
        this.renderLogs();
    }

    suggestedStep() {
        if (!this.workflow) return 1;
        if (!this.audioReady()) return 2;
        if (!this.storyboardReady()) return 3;
        if (!this.imagesReady()) return 4;
        return 5;
    }

    storyboardReady() {
        const storyboard = this.workflow?.storyboard || {};
        const scenes = Array.isArray(this.workflow?.scenes) ? this.workflow.scenes : [];
        return storyboard.status === 'completed' || (scenes.length > 0 && scenes.some((scene) => String(scene.image_prompt || '').trim()));
    }

    canVisitStep(step) {
        const normalized = Number(step);
        if (!Number.isInteger(normalized) || normalized < 1 || normalized > 5) return false;
        if (normalized === 1) return true;
        if (!this.workflow) return false;
        if (normalized === 2) return true;
        if (normalized === 3) return this.audioReady();
        if (normalized === 4) return this.storyboardReady();
        return this.imagesReady();
    }

    goToStep(step) {
        const normalized = Math.max(1, Math.min(5, Number(step) || 1));
        if (!this.canVisitStep(normalized)) {
            this.showNotice(this.stepLockMessage(normalized), 'error');
            return;
        }
        this.activeStep = normalized;
        this.hasManualStep = true;
        this.syncUrl();
        this.renderStepVisibility();
        this.renderStepNav();
        this.renderIndicators();
        const stage = document.querySelector(`section[data-workflow-step="${normalized}"]`);
        if (stage) stage.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    stepLockMessage(step) {
        if (step <= 2) return '请先创建文案任务。';
        if (step === 3) return '请先上传或生成语音，并确认最终 WAV。';
        if (step === 4) return '请先让 Gemini 返回分镜与图片提示词。';
        return '请先完成全部图片生成。';
    }

    renderStepVisibility() {
        const active = this.activeStep || 1;
        document.querySelectorAll('section[data-workflow-step]').forEach((stage) => {
            stage.hidden = Number(stage.dataset.workflowStep) !== active;
        });
    }

    renderStepNav() {
        const meta = {
            1: ['文案', '先输入整篇文案并创建任务'],
            2: ['语音', '试听、裁剪开头／结尾并确认最终 WAV'],
            3: ['分镜', '投递 Gemini 返回覆盖文案的分镜表'],
            4: ['图片', '批量生成并检查每张图片'],
            5: ['对齐与成片', '用整篇语音对齐图片并合成 MP4'],
        }[this.activeStep] || ['文案', '先输入整篇文案并创建任务'];
        this.els.workflowStepLabel.textContent = `第 ${this.activeStep} 步 · ${meta[0]}`;
        this.els.workflowStepHint.textContent = meta[1];
        this.els.workflowPrev.disabled = this.activeStep <= 1;
        this.els.workflowNext.disabled = this.activeStep >= 5 || !this.canVisitStep(this.activeStep + 1);
        this.els.workflowNext.textContent = this.activeStep >= 5 ? '已是最后一步' : `下一步：${['', '文案', '语音', '分镜', '图片', '对齐与成片'][this.activeStep + 1]}`;
    }

    renderIndicators() {
        const step = String(this.workflow?.step || '');
        const map = {
            script: ['script_ready', 'draft'],
            audio: [
                'audio_queued', 'audio_processing', 'audio_upload_queued', 'audio_upload_processing',
                'audio_edit_ready', 'audio_edit_queued', 'audio_edit_processing', 'audio_ready',
            ],
            scenes: ['storyboard_queued', 'storyboard_processing', 'storyboard_ready', 'storyboard_failed', 'blocked_by_login', 'blocked_by_captcha'],
            images: ['images_queued', 'images_processing', 'images_ready', 'images_failed', 'blocked_by_login'],
            render: ['alignment_queued', 'alignment_ready', 'video_queued', 'render_video', 'completed'],
        };
        Object.entries(map).forEach(([key, values]) => {
            const indicator = document.querySelector(`[data-stage-indicator="${key}"]`);
            if (!indicator) return;
            const indicatorStep = Number(indicator.dataset.workflowStep || 0);
            const current = indicatorStep === this.activeStep;
            const unlocked = this.canVisitStep(indicatorStep);
            const complete = indicatorStep < this.activeStep && unlocked;
            indicator.disabled = !unlocked;
            indicator.setAttribute('aria-current', current ? 'step' : 'false');
            indicator.className = `rounded-lg px-3 py-2 text-center text-xs font-black transition disabled:cursor-not-allowed disabled:opacity-50 ${current ? 'bg-violet-700 text-white shadow-sm' : complete ? 'bg-emerald-50 text-emerald-700' : unlocked ? 'text-slate-600 hover:bg-slate-50' : 'text-slate-300'}`;
            if (values.includes(step) && !current) indicator.classList.add('ring-1', 'ring-violet-200');
        });
    }

    renderAudio() {
        const workflow = this.workflow;
        const audio = workflow.audio || {};
        const chunks = Array.isArray(workflow.audio_chunks) ? workflow.audio_chunks : [];
        const completed = Number(audio.chunks_completed || 0);
        const sourceReady = this.audioSourceReady();
        const finalReady = this.audioReady();
        const audioBusy = this.isBusy('audio');
        if (audioBusy) {
            this.els.audioSummary.textContent = audio.state === 'edit_processing' || workflow.step === 'audio_edit_processing'
                ? '正在根据裁剪和倍速生成最终 WAV。'
                : '正在准备可编辑的语音，请稍候。';
        } else if (finalReady) {
            this.els.audioSummary.textContent = `最终 WAV 已确认（${this.formatSeconds(audio.seconds)}）。现在可以进入 Gemini 分镜。`;
        } else if (sourceReady) {
            this.els.audioSummary.textContent = `原始语音已就绪（${this.formatSeconds(audio.source_seconds)}）。请试听、裁掉开头／结尾或调整倍速后生成最终 WAV。`;
        } else {
            this.els.audioSummary.textContent = chunks.length ? `共 ${chunks.length} 段；已完成 ${completed}/${audio.chunks_total || chunks.length} 段。` : '暂无语音分段。';
        }
        this.els.audioChunks.replaceChildren();
        chunks.forEach((chunk, index) => {
            const card = document.createElement('div');
            card.className = 'rounded-lg border border-slate-200 bg-slate-50 p-3';
            const label = document.createElement('label');
            label.className = 'block';
            const title = document.createElement('span');
            title.className = 'mb-1 block text-xs font-black text-slate-600';
            title.textContent = `第 ${index + 1} 段 · ${String(chunk || '').length} 字`;
            const input = document.createElement('textarea');
            input.rows = 3;
            input.dataset.audioChunk = String(index + 1);
            input.value = chunk || '';
            input.className = 'w-full resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 focus:border-indigo-500 focus:outline-none';
            input.disabled = this.audioLocked();
            label.append(title, input);
            card.appendChild(label);
            this.els.audioChunks.appendChild(card);
        });
        this.els.audioEditor.classList.toggle('hidden', !sourceReady);
        if (sourceReady) {
            const duration = this.audioSourceSeconds();
            const edit = audio.edit || {};
            const sourceUrl = `${this.apiRoot()}${this.endpoint('/audio/source')}`;
            if (this.els.audioSourcePlayer.src !== sourceUrl) {
                this.els.audioSourcePlayer.src = sourceUrl;
                this.waveformUrl = '';
                this.waveformPeaks = null;
            }
            this.els.audioSourceMeta.textContent = `${audio.original_filename || (audio.source_type === 'doubao_browser_tts' ? '豆包合并语音' : '上传音频')} · 原始时长 ${this.formatSeconds(duration)}。裁剪只作用于开头和结尾，原始音频不会被覆盖。`;
            const start = Number.isFinite(Number(edit.trim_start_seconds)) ? Number(edit.trim_start_seconds) : 0;
            const end = Number.isFinite(Number(edit.trim_end_seconds)) ? Number(edit.trim_end_seconds) : duration;
            const speed = Number.isFinite(Number(edit.speed)) ? String(edit.speed) : '1';
            for (const control of [this.els.audioTrimStart, this.els.audioTrimEnd, this.els.audioTrimStartRange, this.els.audioTrimEndRange]) {
                control.max = String(duration);
                control.disabled = audioBusy || this.isBusy('render');
            }
            this.els.audioTrimStart.value = String(Math.max(0, Math.min(start, duration)));
            this.els.audioTrimStartRange.value = this.els.audioTrimStart.value;
            this.els.audioTrimEnd.value = String(Math.max(0, Math.min(end, duration)));
            this.els.audioTrimEndRange.value = this.els.audioTrimEnd.value;
            this.setAudioSpeed(speed, {render: false});
            this.els.audioSpeed.disabled = audioBusy || this.isBusy('render');
            this.els.audioPlaySource.disabled = audioBusy || this.isBusy('render');
            this.els.previewAudioEdit.disabled = audioBusy || this.isBusy('render');
            this.els.resetAudioEdit.disabled = audioBusy || this.isBusy('render');
            this.els.finalizeAudioEdit.disabled = audioBusy || this.isBusy('render');
            this.renderAudioTimeline();
            this.drawAudioWaveform();
            if (this.waveformUrl !== sourceUrl) this.loadAudioWaveform(sourceUrl);
        } else {
            this.els.audioSourcePlayer.pause();
            this.els.audioSourcePlayer.removeAttribute('src');
            this.els.audioPlaySource.disabled = true;
            this.previewTrimEnd = null;
            this.waveformUrl = '';
            this.waveformPeaks = null;
            this.renderAudioTimeline();
        }
        this.els.audioPanel.classList.toggle('hidden', !finalReady);
        if (finalReady) {
            const url = `${this.apiRoot()}${this.endpoint('/audio/file')}`;
            this.els.audioPlayer.src = url;
            this.els.audioDownload.href = url;
        } else {
            this.els.audioPlayer.removeAttribute('src');
            this.els.audioDownload.removeAttribute('href');
        }
        const switchLocked = !workflow || audioBusy || this.isBusy('storyboard') || this.isBusy('images') || this.isBusy('render');
        this.els.saveAudio.disabled = !workflow || this.audioLocked();
        this.els.uploadAudio.disabled = switchLocked;
        this.els.audioUploadInput.disabled = switchLocked;
        this.els.startAudio.disabled = switchLocked || finalReady || sourceReady;
    }

    renderScenes() {
        const workflow = this.workflow;
        const scenes = Array.isArray(workflow?.scenes) ? workflow.scenes : [];
        const storyboard = workflow?.storyboard || {};
        const planning = this.isBusy('storyboard');
        this.els.scenes.replaceChildren();
        scenes.forEach((scene) => {
            const row = document.createElement('tr');
            row.dataset.sceneRow = String(scene.sequence);
            row.className = 'border-t border-slate-100 align-top';
            const sequenceCell = document.createElement('td');
            sequenceCell.className = 'px-3 py-3';
            const sequence = document.createElement('input');
            sequence.type = 'number'; sequence.min = '1'; sequence.value = String(scene.sequence || ''); sequence.dataset.sceneSequence = '';
            sequence.className = 'w-12 rounded border border-slate-300 px-1.5 py-1.5 text-center text-xs font-bold focus:border-violet-500 focus:outline-none';
            sequence.disabled = this.scenesLocked();
            sequenceCell.appendChild(sequence);
            const textCell = document.createElement('td'); textCell.className = 'px-3 py-3';
            const text = document.createElement('textarea'); text.rows = 4; text.value = scene.script_text || ''; text.dataset.sceneText = '';
            text.className = 'w-full resize-y rounded border border-slate-300 px-2 py-1.5 text-xs leading-5 focus:border-violet-500 focus:outline-none'; text.disabled = this.scenesLocked(); textCell.appendChild(text);
            const promptCell = document.createElement('td'); promptCell.className = 'px-3 py-3';
            const prompt = document.createElement('textarea'); prompt.rows = 4; prompt.value = scene.image_prompt || ''; prompt.dataset.scenePrompt = '';
            prompt.placeholder = '描述这张图的主体、动作、构图、数据或隐喻；视觉方案会自动附加。';
            prompt.className = 'w-full resize-y rounded border border-slate-300 px-2 py-1.5 text-xs leading-5 focus:border-violet-500 focus:outline-none'; prompt.disabled = this.scenesLocked(); promptCell.appendChild(prompt);
            row.append(sequenceCell, textCell, promptCell);
            this.els.scenes.appendChild(row);
        });
        if (planning) {
            this.els.scenesSummary.textContent = storyboard.last_message || 'Gemini 正在规划覆盖文案与图片提示词。';
            this.setStoryboardRunStatus('processing', storyboard.last_message || '已进入 Gemini 浏览器队列，正在唤起浏览器并等待返回。');
        } else if (this.storyboardFailed()) {
            const message = storyboard.error || workflow?.error || 'Gemini 规划没有完成。';
            this.els.scenesSummary.textContent = `Gemini 规划失败：${message}`;
            this.setStoryboardRunStatus('failed', `${message} 可以点击下方按钮重新投递，系统会再次打开浏览器执行。`);
        } else if (storyboard.status === 'legacy_local_draft') {
            this.els.scenesSummary.textContent = `当前 ${scenes.length} 行是旧版按字数切出的草稿；点击“投递 Gemini 规划分镜”会以 Gemini 返回结果替换。`;
            this.setStoryboardRunStatus('idle', '当前是旧版本地草稿；投递后将由 Gemini 返回正式分镜。');
        } else if (scenes.length) {
            this.els.scenesSummary.textContent = `Gemini 已返回 ${scenes.length} 个分镜。确认或修改后，再投递每张图片。`;
            this.setStoryboardRunStatus('success', 'Gemini 已返回分镜表，可以编辑图片提示词后进入下一步。');
        } else {
            this.els.scenesSummary.textContent = '暂无分镜。请先完成整篇语音，再投递 Gemini 规划分镜。';
            this.setStoryboardRunStatus('idle', '点击“投递 Gemini 规划分镜”后，会打开浏览器并等待 Gemini 返回。');
        }
        this.els.startStoryboard.disabled = !workflow || !this.audioReady() || planning || this.isBusy('images') || this.isBusy('render') || workflow.step === 'completed';
        this.els.startStoryboard.innerHTML = this.storyboardFailed()
            ? '<i class="fas fa-rotate-right mr-1"></i>重试 Gemini 规划'
            : '<i class="fas fa-wand-magic-sparkles mr-1"></i>投递 Gemini 规划分镜';
        this.els.saveScenes.disabled = !workflow || !scenes.length || this.scenesLocked();
    }

    storyboardFailed() {
        const storyboard = this.workflow?.storyboard || {};
        const step = String(this.workflow?.step || this.workflow?.stage || '');
        return ['storyboard_failed', 'failed'].includes(step) || ['failed', 'error'].includes(String(storyboard.status || ''));
    }

    setStoryboardRunStatus(kind, message) {
        const node = this.els.storyboardRunStatus;
        if (!node) return;
        const classes = {
            processing: 'border-indigo-200 bg-indigo-50 text-indigo-700',
            failed: 'border-red-200 bg-red-50 text-red-700',
            success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
            idle: 'border-slate-200 bg-slate-50 text-slate-500',
        };
        node.className = `mt-2 rounded-md border px-3 py-2 text-[11px] leading-5 ${classes[kind] || classes.idle}`;
        node.textContent = message || '';
        node.classList.remove('hidden');
    }

    renderImages() {
        const workflow = this.workflow;
        const images = workflow.images || {};
        const scenes = Array.isArray(workflow.scenes) ? workflow.scenes : [];
        const rendering = this.isBusy('render');
        this.els.imagesSummary.textContent = scenes.length ? `已完成 ${images.completed || 0}/${images.total || scenes.length} 张；失败或阻塞的图片可单独重试。` : '暂无图片任务。';
        this.els.imagesGrid.replaceChildren();
        scenes.forEach((scene) => {
            const card = document.createElement('article');
            card.className = 'overflow-hidden rounded-lg border border-slate-200 bg-slate-50';
            const preview = document.createElement('div'); preview.className = 'aspect-video bg-slate-200';
            if (scene.image_url && scene.image_status === 'completed') {
                const image = document.createElement('img'); image.src = `${this.apiRoot()}${scene.image_url}`; image.alt = `第 ${scene.sequence} 段图片`; image.loading = 'lazy'; image.className = 'h-full w-full object-cover'; preview.appendChild(image);
            } else {
                const placeholder = document.createElement('div'); placeholder.className = 'flex h-full items-center justify-center text-xs font-bold text-slate-500'; placeholder.textContent = scene.image_status === 'failed' ? '图片生成失败' : (scene.image_status === 'blocked_by_login' || scene.image_status === 'blocked_by_captcha' ? '等待浏览器登录 / 验证' : '等待图片'); preview.appendChild(placeholder);
            }
            const body = document.createElement('div'); body.className = 'p-3';
            const title = document.createElement('div'); title.className = 'flex items-center justify-between gap-2 text-xs';
            const left = document.createElement('span'); left.className = 'font-black text-slate-700'; left.textContent = `第 ${scene.sequence} 段`;
            const status = document.createElement('span'); status.className = `rounded-full px-2 py-0.5 font-bold ${this.statusClass(scene.image_status)}`; status.textContent = this.statusText(scene.image_status); title.append(left, status);
            const prompt = document.createElement('p'); prompt.className = 'mt-2 line-clamp-3 text-[11px] leading-5 text-slate-500'; prompt.textContent = scene.image_prompt || '尚未填写图片提示词';
            body.append(title, prompt);
            if (!rendering && ['failed', 'blocked_by_login', 'blocked_by_captcha', 'canceled'].includes(scene.image_status)) {
                const retry = document.createElement('button'); retry.type = 'button'; retry.dataset.retryImage = String(scene.sequence); retry.className = 'mt-3 rounded-md border border-red-200 bg-white px-2.5 py-1.5 text-xs font-bold text-red-700 hover:bg-red-50'; retry.textContent = '重试这张图'; body.appendChild(retry);
            }
            card.append(preview, body); this.els.imagesGrid.appendChild(card);
        });
        this.els.startImages.disabled = !workflow || !scenes.length || !this.audioReady() || this.isBusy('storyboard') || this.isBusy('images') || rendering;
    }

    renderRenderStage() {
        const workflow = this.workflow;
        const alignment = workflow.alignment || {};
        const video = workflow.video || {};
        const stats = [
            ['对齐镜头', alignment.scene_count || alignment.timeline?.length || 0],
            ['ASR 缓存', alignment.asr_metadata?.cache_hit ? '命中' : (alignment.asr_metadata ? '已生成' : '未开始')],
            ['视频时长', video.duration_seconds ? `${Number(video.duration_seconds).toFixed(1)} 秒` : '未生成'],
        ];
        this.els.alignment.replaceChildren();
        stats.forEach(([label, value]) => {
            const item = document.createElement('div'); item.className = 'rounded-md border border-slate-200 bg-slate-50 px-3 py-2';
            const labelNode = document.createElement('div'); labelNode.className = 'text-[11px] font-bold text-slate-400'; labelNode.textContent = label;
            const valueNode = document.createElement('div'); valueNode.className = 'mt-1 text-xs font-black text-slate-700'; valueNode.textContent = String(value);
            item.append(labelNode, valueNode); this.els.alignment.appendChild(item);
        });
        const hasVideo = Boolean(video.video_path);
        this.els.videoPanel.classList.toggle('hidden', !hasVideo);
        if (hasVideo) {
            const url = this.endpoint('/video/file'); this.els.videoPlayer.src = url; this.els.videoDownload.href = url;
        } else { this.els.videoPlayer.removeAttribute('src'); this.els.videoDownload.removeAttribute('href'); }
        this.els.renderSummary.textContent = hasVideo ? 'MP4 已完成，可在线播放或下载。' : '全部图片完成后，可投递整篇 WAV 的时间轴对齐与 MP4 合成。';
        this.els.startRender.disabled = !workflow || hasVideo || !this.imagesReady() || this.isBusy('render');
    }

    renderLogs() {
        this.els.logs.replaceChildren();
        const logs = Array.isArray(this.workflow?.logs) ? this.workflow.logs.slice().reverse() : [];
        if (!logs.length) this.els.logs.appendChild(this.textNode('暂无日志。', 'text-xs text-slate-400'));
        logs.forEach((entry) => {
            const item = document.createElement('li'); item.className = 'border-l-2 border-slate-200 pl-2';
            const message = document.createElement('div'); message.className = 'leading-5 text-slate-600'; message.textContent = entry.message || entry.step || '状态更新';
            const meta = document.createElement('div'); meta.className = 'mt-0.5 text-[10px] text-slate-400'; meta.textContent = `${entry.step || '-'} · ${this.formatTime(entry.updated_at)}`;
            item.append(message, meta); this.els.logs.appendChild(item);
        });
    }

    renderRecentJobs(jobs) {
        this.els.recentJobs.replaceChildren();
        if (!jobs.length) this.els.recentJobs.appendChild(this.textNode('暂无历史任务。', 'text-xs text-slate-400'));
        jobs.forEach((job) => {
            const button = document.createElement('button'); button.type = 'button'; button.dataset.openJob = job.job_id; button.className = 'block w-full rounded-lg border border-slate-200 bg-slate-50 p-2.5 text-left hover:border-violet-300 hover:bg-violet-50';
            const title = document.createElement('div'); title.className = 'truncate text-xs font-black text-slate-700'; title.textContent = job.title || '未命名文案';
            const meta = document.createElement('div'); meta.className = 'mt-1 flex items-center justify-between gap-2 text-[11px] text-slate-400';
            const stage = document.createElement('span'); stage.className = 'truncate'; stage.textContent = job.stage || job.step || job.status || '-';
            const progress = document.createElement('span'); progress.textContent = `${Number(job.progress || 0)}%`; meta.append(stage, progress); button.append(title, meta); this.els.recentJobs.appendChild(button);
        });
    }

    endpoint(suffix) {
        return this.jobId ? `/api/script-video-workflows/jobs/${encodeURIComponent(this.jobId)}${suffix}` : '#';
    }

    audioReady() {
        const audio = this.workflow?.audio || {};
        const state = String(audio.state || '');
        return Boolean(audio.wav_path) && (!state || state === 'finalized');
    }

    audioSourceReady() {
        return Boolean(this.workflow?.audio?.source_wav_path);
    }

    audioSourceSeconds() {
        const seconds = Number(this.workflow?.audio?.source_seconds || 0);
        if (Number.isFinite(seconds) && seconds > 0) return seconds;
        const metadataSeconds = Number(this.els.audioSourcePlayer?.duration || 0);
        return Number.isFinite(metadataSeconds) && metadataSeconds > 0 ? metadataSeconds : 0;
    }

    imagesReady() {
        const images = this.workflow?.images || {};
        return Number(images.total || 0) > 0 && Number(images.completed || 0) >= Number(images.total || 0);
    }

    audioLocked() {
        return this.audioReady() || this.audioSourceReady() || this.isBusy('audio');
    }

    scenesLocked() {
        return this.workflow?.step === 'completed' || this.isBusy('storyboard') || this.isBusy('render');
    }

    isBusy(stage) {
        const step = String(this.workflow?.stage || this.workflow?.step || '');
        if (stage === 'audio') return [
            'audio_queued', 'audio_processing', 'audio_upload_queued', 'audio_upload_processing',
            'audio_edit_queued', 'audio_edit_processing',
        ].includes(step);
        if (stage === 'storyboard') return ['storyboard_queued', 'storyboard_processing'].includes(step);
        if (stage === 'images') return ['images_queued', 'images_processing', 'blocked_by_login'].includes(step);
        if (stage === 'render') return ['alignment_queued', 'alignment_ready', 'video_queued', 'render_video'].includes(step);
        return false;
    }

    isPollingNeeded() {
        if (!this.workflow) return false;
        if (['completed', 'failed', 'blocked_by_login', 'blocked_by_captcha', 'images_failed'].includes(this.workflow.status)) {
            return false;
        }
        return this.isBusy('audio') || this.isBusy('storyboard') || this.isBusy('images') || this.isBusy('render');
    }

    startPolling() {
        this.stopPolling();
        if (!this.jobId || document.hidden || !this.isPollingNeeded()) return;
        this.pollTimer = window.setTimeout(async () => {
            await this.loadWorkflow({poll: true});
            this.startPolling();
        }, SCRIPT_VIDEO_ACTIVE_POLL_MS);
    }

    stopPolling() {
        if (this.pollTimer) window.clearTimeout(this.pollTimer);
        this.pollTimer = null;
    }

    handleVisibilityChange() {
        if (document.hidden) {
            this.stopPolling();
            return;
        }
        if (this.jobId) this.loadWorkflow();
        this.startPolling();
    }

    requireWorkflow() {
        if (this.workflow && this.jobId) return true;
        this.showNotice('请先创建或打开一个文案任务。', 'error');
        return false;
    }

    async withButton(button, busyText, action) {
        const original = button.textContent;
        button.disabled = true;
        button.textContent = busyText;
        try {
            await action();
        } catch (error) {
            this.showNotice(error.message || '操作失败。', 'error');
            throw error;
        } finally {
            button.disabled = false;
            button.textContent = original;
        }
    }

    showNotice(message, kind = 'success') {
        this.els.notice.textContent = message;
        this.els.notice.className = `mb-5 rounded-lg border px-4 py-3 text-sm ${kind === 'error' ? 'border-red-200 bg-red-50 text-red-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`;
        this.els.notice.classList.remove('hidden');
    }

    syncUrl() {
        const url = new URL(window.location.href);
        if (this.jobId) url.searchParams.set('job_id', this.jobId); else url.searchParams.delete('job_id');
        if (this.activeStep > 1) url.searchParams.set('step', String(this.activeStep)); else url.searchParams.delete('step');
        window.history.replaceState({}, '', url);
    }

    statusText(status) {
        return ({pending: '排队中', processing: '处理中', completed: '已完成', failed: '失败', blocked_by_login: '等待登录', blocked_by_captcha: '等待验证', canceled: '已取消', not_submitted: '未提交'})[status] || status || '未提交';
    }

    statusClass(status) {
        if (status === 'completed') return 'bg-emerald-100 text-emerald-700';
        if (['failed', 'canceled'].includes(status)) return 'bg-red-100 text-red-700';
        if (['blocked_by_login', 'blocked_by_captcha'].includes(status)) return 'bg-amber-100 text-amber-700';
        if (['pending', 'processing'].includes(status)) return 'bg-indigo-100 text-indigo-700';
        return 'bg-slate-200 text-slate-600';
    }

    textNode(text, className = '') {
        const node = document.createElement('div'); node.className = className; node.textContent = text; return node;
    }

    formatTime(value) {
        if (!value) return '-';
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', {hour12: false});
    }

    formatSeconds(value) {
        const seconds = Number(value || 0);
        if (!Number.isFinite(seconds) || seconds <= 0) return '—';
        const minutes = Math.floor(seconds / 60);
        const remainder = seconds - minutes * 60;
        return minutes ? `${minutes} 分 ${remainder.toFixed(1)} 秒` : `${remainder.toFixed(1)} 秒`;
    }

    formatClock(value) {
        const seconds = Math.max(0, Math.round(Number(value) || 0));
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const remainder = seconds % 60;
        if (hours) return `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
        return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    if (window.pageAccessGuard) await window.pageAccessGuard;
    window.scriptVideoWorkflowPage = new ScriptVideoWorkflowPage();
});
