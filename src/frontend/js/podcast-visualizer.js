class PodcastVisualizerTool {
    constructor() {
        this.rendererVersion = '20260520-audio-controls-v9';
        this.audio = null;
        this.downloadUrl = null;
        this.currentTaskId = null;
        this.pollTimer = null;
        this.previewTimer = null;
        this.previewAbortController = null;
        this.previewObjectUrl = null;
        this.currentPreviewPayloadKey = '';
        this.audioPreview = new Audio();
        this.audioPreviewUrl = null;
        this.currentTaskPage = 1;
        this.tasksPerPage = 20;
        this.init();
    }

    init() {
        this.bindElements();
        this.bindEvents();
        this.syncPreview();
    }

    bindElements() {
        this.form = document.getElementById('podcast-form');
        this.dropZone = document.getElementById('drop-zone');
        this.audioInput = document.getElementById('audio-input');
        this.pickAudioBtn = document.getElementById('pick-audio-btn');
        this.clearAudioBtn = document.getElementById('clear-audio-btn');
        this.audioCard = document.getElementById('audio-card');
        this.audioName = document.getElementById('audio-name');
        this.audioSize = document.getElementById('audio-size');
        this.audioStartInput = document.getElementById('audio-start-input');
        this.audioEndInput = document.getElementById('audio-end-input');
        this.audioSpeedSelect = document.getElementById('audio-speed-select');
        this.audioPreviewBtn = document.getElementById('audio-preview-btn');
        this.audioPreviewStopBtn = document.getElementById('audio-preview-stop-btn');
        this.audioPreviewStatus = document.getElementById('audio-preview-status');
        this.titleInput = document.getElementById('title-input');
        this.headlineSubtitleInput = document.getElementById('headline-subtitle-input');
        this.subtitleInput = document.getElementById('subtitle-input');
        this.brandInput = document.getElementById('brand-input');
        this.srtInput = document.getElementById('srt-input');
        this.autoAsrInput = document.getElementById('auto-asr-input');
        this.asrModelSelect = document.getElementById('asr-model-select');
        this.captionInput = document.getElementById('caption-input');
        this.orientationSelect = document.getElementById('orientation-select');
        this.fpsSelect = document.getElementById('fps-select');
        this.crfSelect = document.getElementById('crf-select');
        this.message = document.getElementById('message');
        this.progressText = document.getElementById('progress-text');
        this.progressLabel = document.getElementById('progress-label');
        this.progressBar = document.getElementById('progress-bar');
        this.generateBtn = document.getElementById('generate-btn');
        this.serverPreviewImage = document.getElementById('server-preview-image');
        this.serverPreviewStatus = document.getElementById('server-preview-status');
        this.emptyState = document.getElementById('empty-state');
        this.previewWrap = document.getElementById('preview-wrap');
        this.previewVideo = document.getElementById('preview-video');
        this.downloadLink = document.getElementById('download-link');
        this.resultMeta = document.getElementById('result-meta');
        this.refreshTasksBtn = document.getElementById('refresh-tasks-btn');
        this.taskListBody = document.getElementById('task-list-body');
        this.taskPagination = document.getElementById('task-pagination');
        this.taskPrevPageBtn = document.getElementById('task-prev-page-btn');
        this.taskNextPageBtn = document.getElementById('task-next-page-btn');
        this.taskPageInfo = document.getElementById('task-page-info');
    }

    bindEvents() {
        this.pickAudioBtn.addEventListener('click', () => this.audioInput.click());
        this.audioInput.addEventListener('change', (event) => this.selectAudio(event.target.files[0]));
        this.clearAudioBtn.addEventListener('click', () => this.clearAudio());
        this.form.addEventListener('submit', (event) => {
            event.preventDefault();
            this.generate();
        });

        [this.titleInput, this.headlineSubtitleInput, this.subtitleInput, this.brandInput, this.captionInput].forEach((input) => {
            input.addEventListener('input', () => this.syncPreview());
        });
        this.orientationSelect.addEventListener('change', () => this.syncPreview());
        this.srtInput.addEventListener('change', () => this.handleSrtSelection());
        [this.audioStartInput, this.audioEndInput, this.audioSpeedSelect].forEach((input) => {
            input.addEventListener('input', () => this.updateAudioPreviewStatus());
            input.addEventListener('change', () => this.updateAudioPreviewStatus());
        });
        this.audioPreviewBtn?.addEventListener('click', () => this.playAudioPreview());
        this.audioPreviewStopBtn?.addEventListener('click', () => this.stopAudioPreview());
        this.audioPreview.addEventListener('timeupdate', () => this.enforceAudioPreviewEnd());
        this.audioPreview.addEventListener('ended', () => this.stopAudioPreview(false));
        this.audioPreview.addEventListener('loadedmetadata', () => this.updateAudioPreviewStatus());
        this.refreshTasksBtn.addEventListener('click', () => this.loadTasks(this.currentTaskPage));
        this.taskPrevPageBtn?.addEventListener('click', () => this.loadTasks(this.currentTaskPage - 1));
        this.taskNextPageBtn?.addEventListener('click', () => this.loadTasks(this.currentTaskPage + 1));
        this.taskListBody?.addEventListener('click', (event) => {
            const button = event.target.closest('[data-requeue-task-id]');
            if (!button) return;
            this.requeueTask(button.dataset.requeueTaskId);
        });
        this.loadTasks();

        ['dragenter', 'dragover'].forEach((eventName) => {
            this.dropZone.addEventListener(eventName, (event) => {
                event.preventDefault();
                this.dropZone.classList.add('dragover');
            });
        });

        ['dragleave', 'drop'].forEach((eventName) => {
            this.dropZone.addEventListener(eventName, (event) => {
                event.preventDefault();
                this.dropZone.classList.remove('dragover');
            });
        });

        this.dropZone.addEventListener('drop', (event) => {
            this.selectAudio(event.dataTransfer.files[0]);
        });
    }

    selectAudio(file) {
        this.hideMessage();
        if (!file) return;

        const allowed = ['.mp3', '.m4a', '.wav', '.aac', '.flac', '.ogg'];
        const lowerName = file.name.toLowerCase();
        if (!allowed.some((ext) => lowerName.endsWith(ext)) && !file.type.startsWith('audio/')) {
            this.showMessage('请选择音频文件', 'error');
            return;
        }

        const maxSize = 300 * 1024 * 1024;
        if (file.size > maxSize) {
            this.showMessage('音频超过 300MB，请选择更小的文件', 'error');
            return;
        }

        this.audio = file;
        this.stopAudioPreview(false);
        if (this.audioPreviewUrl) {
            URL.revokeObjectURL(this.audioPreviewUrl);
        }
        this.audioPreviewUrl = URL.createObjectURL(file);
        this.audioPreview.src = this.audioPreviewUrl;
        this.audioPreview.preload = 'metadata';
        this.audioName.textContent = file.name;
        this.audioSize.textContent = this.formatFileSize(file.size);
        this.audioCard.classList.remove('hidden');
        this.audioPreviewBtn.disabled = false;
        this.audioPreviewStopBtn.disabled = true;
        this.updateAudioPreviewStatus();
        this.setProgress(0, '准备生成');

        this.syncPreview();
    }

    clearAudio() {
        this.stopAudioPreview(false);
        this.audio = null;
        this.audioInput.value = '';
        this.audioCard.classList.add('hidden');
        if (this.audioPreviewUrl) {
            URL.revokeObjectURL(this.audioPreviewUrl);
            this.audioPreviewUrl = null;
        }
        this.audioPreview.removeAttribute('src');
        this.audioPreviewBtn.disabled = true;
        this.audioPreviewStopBtn.disabled = true;
        this.audioPreviewStatus.textContent = '选择音频后可试听裁切和倍速效果';
        this.setProgress(0, '等待音频');
        this.hideMessage();
    }

    parsePreviewTimecode(value) {
        const text = String(value || '').trim();
        if (!text) return null;
        if (/^\d+(\.\d+)?$/.test(text)) {
            return Number(text);
        }
        const parts = text.split(':').map((part) => Number(part));
        if ((parts.length !== 2 && parts.length !== 3) || parts.some((part) => Number.isNaN(part))) {
            throw new Error('时间格式请填写秒数、MM:SS 或 HH:MM:SS');
        }
        if (parts.length === 2) {
            return parts[0] * 60 + parts[1];
        }
        return parts[0] * 3600 + parts[1] * 60 + parts[2];
    }

    getAudioPreviewRange() {
        const duration = Number.isFinite(this.audioPreview.duration) ? this.audioPreview.duration : 0;
        const speed = Number(this.audioSpeedSelect.value || 1);
        const start = Math.max(0, this.parsePreviewTimecode(this.audioStartInput.value) ?? 0);
        const rawEnd = this.parsePreviewTimecode(this.audioEndInput.value);
        if (rawEnd === null && duration <= 0) {
            throw new Error('正在读取音频时长');
        }
        const end = rawEnd === null ? duration : Math.min(rawEnd, duration || rawEnd);
        if (speed <= 0) {
            throw new Error('倍速必须大于 0');
        }
        if (end <= start) {
            throw new Error('截止时间必须晚于开始时间');
        }
        return { start, end, speed, duration, playDuration: (end - start) / speed };
    }

    updateAudioPreviewStatus() {
        if (!this.audioPreviewStatus) return;
        if (!this.audio) {
            this.audioPreviewStatus.textContent = '选择音频后可试听裁切和倍速效果';
            return;
        }
        try {
            const range = this.getAudioPreviewRange();
            this.audioPreviewStatus.textContent = `试听 ${this.formatSeconds(range.start)} - ${this.formatSeconds(range.end)}，实际播放约 ${range.playDuration.toFixed(1)}s`;
        } catch (error) {
            this.audioPreviewStatus.textContent = error.message;
        }
    }

    async playAudioPreview() {
        if (!this.audio || !this.audioPreview.src) {
            this.showMessage('请先选择音频文件', 'error');
            return;
        }
        try {
            await this.ensureAudioPreviewMetadata();
            const range = this.getAudioPreviewRange();
            this.audioPreview.pause();
            this.audioPreview.playbackRate = range.speed;
            this.audioPreview.currentTime = range.start;
            await this.audioPreview.play();
            this.audioPreviewBtn.disabled = true;
            this.audioPreviewStopBtn.disabled = false;
            this.audioPreviewStatus.textContent = `正在试听 ${this.formatSeconds(range.start)} - ${this.formatSeconds(range.end)}，${range.speed}x`;
        } catch (error) {
            this.stopAudioPreview(false);
            this.showMessage(error.message || '音频试听失败', 'error');
            this.updateAudioPreviewStatus();
        }
    }

    ensureAudioPreviewMetadata() {
        if (Number.isFinite(this.audioPreview.duration) && this.audioPreview.duration > 0) {
            return Promise.resolve();
        }
        return new Promise((resolve, reject) => {
            const cleanup = () => {
                this.audioPreview.removeEventListener('loadedmetadata', handleLoaded);
                this.audioPreview.removeEventListener('error', handleError);
            };
            const handleLoaded = () => {
                cleanup();
                resolve();
            };
            const handleError = () => {
                cleanup();
                reject(new Error('音频元数据读取失败'));
            };
            this.audioPreview.addEventListener('loadedmetadata', handleLoaded);
            this.audioPreview.addEventListener('error', handleError);
            this.audioPreview.load();
        });
    }

    enforceAudioPreviewEnd() {
        if (!this.audio || this.audioPreview.paused) return;
        try {
            const range = this.getAudioPreviewRange();
            if (this.audioPreview.currentTime >= range.end) {
                this.stopAudioPreview();
            }
        } catch (error) {
            this.stopAudioPreview(false);
        }
    }

    stopAudioPreview(updateStatus = true) {
        this.audioPreview.pause();
        this.audioPreviewBtn.disabled = !this.audio;
        this.audioPreviewStopBtn.disabled = true;
        if (updateStatus) {
            this.updateAudioPreviewStatus();
        }
    }

    syncPreview() {
        this.scheduleServerPreview();
    }

    scheduleServerPreview() {
        if (this.previewTimer) {
            clearTimeout(this.previewTimer);
        }
        const payloadKey = JSON.stringify(this.getPreviewPayload());
        if (payloadKey === this.currentPreviewPayloadKey && this.previewObjectUrl) {
            return;
        }
        this.setServerPreviewStatus('正在渲染预览', 'loading');
        this.previewTimer = setTimeout(() => this.renderServerPreview(), 900);
    }

    getPreviewPayload() {
        return {
            title: this.titleInput.value.trim(),
            headline_subtitle: this.headlineSubtitleInput.value.trim(),
            subtitle: this.subtitleInput.value.trim(),
            brand_text: this.brandInput.value.trim(),
            caption_text: this.captionInput.value.trim(),
            orientation: this.orientationSelect.value,
            srt_selected: Boolean(this.srtInput.files[0]),
        };
    }

    async renderServerPreview() {
        const payload = this.getPreviewPayload();
        const payloadKey = JSON.stringify(payload);
        if (payloadKey === this.currentPreviewPayloadKey && this.previewObjectUrl) {
            this.setServerPreviewStatus('', 'ready');
            return;
        }
        if (this.previewAbortController) {
            this.previewAbortController.abort();
        }
        this.previewAbortController = new AbortController();

        try {
            const response = await fetch(`${API_BASE_URL}/documents/podcast-visualizer/preview-frame`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                signal: this.previewAbortController.signal,
            });
            if (!response.ok) {
                let message = `预览渲染失败，状态码 ${response.status}`;
                try {
                    const data = await response.json();
                    message = data.message || message;
                } catch (error) {
                    message = await response.text() || message;
                }
                throw new Error(message);
            }

            const blob = await response.blob();
            const nextUrl = URL.createObjectURL(blob);
            if (this.previewObjectUrl) {
                URL.revokeObjectURL(this.previewObjectUrl);
            }
            this.previewObjectUrl = nextUrl;
            this.currentPreviewPayloadKey = payloadKey;
            this.serverPreviewImage.src = nextUrl;
            this.setServerPreviewStatus('', 'ready');
        } catch (error) {
            if (error.name === 'AbortError') return;
            console.warn('render preview failed', error);
            this.setServerPreviewStatus(error.message || '预览渲染失败', 'error');
        }
    }

    setServerPreviewStatus(text, type) {
        if (!this.serverPreviewStatus) return;
        if (type === 'ready') {
            this.serverPreviewStatus.classList.add('hidden');
            return;
        }
        this.serverPreviewStatus.className = 'absolute inset-0 flex items-center justify-center bg-white/75 px-4 text-center text-sm font-bold backdrop-blur-sm';
        if (type === 'error') {
            this.serverPreviewStatus.classList.add('text-red-700');
            this.serverPreviewStatus.innerHTML = `<i class="fas fa-triangle-exclamation mr-2"></i>${this.escapeHtml(text)}`;
        } else {
            this.serverPreviewStatus.classList.add('text-slate-500');
            this.serverPreviewStatus.innerHTML = `<i class="fas fa-spinner fa-spin mr-2"></i>${this.escapeHtml(text)}`;
        }
    }

    handleSrtSelection() {
        const file = this.srtInput.files[0];
        if (!file) {
            this.syncPreview();
            return;
        }
        if (!file.name.toLowerCase().endsWith('.srt')) {
            this.showMessage('字幕文件只支持 .srt', 'error');
            this.srtInput.value = '';
            return;
        }
        this.hideMessage();
        this.syncPreview();
    }

    async generate() {
        if (!this.audio) {
            this.showMessage('请先选择音频文件', 'error');
            return;
        }
        if (!this.srtInput.files[0] && !this.autoAsrInput.checked && !this.captionInput.value.trim()) {
            this.showMessage('没有字幕来源：请上传 SRT、开启本地 ASR，或填写手动字幕兜底', 'error');
            return;
        }

        this.revokeDownloadUrl();
        this.hideMessage();
        this.setBusy(true);
        this.setProgress(10, '上传音频');
        this.emptyState.classList.add('hidden');
        this.previewWrap.classList.add('hidden');
        this.downloadLink.classList.add('hidden');

        const formData = new FormData();
        formData.append('audio', this.audio);
        formData.append('title', this.titleInput.value.trim() || '盘点鲍威尔八年功与过，他给美联储留下了什么？');
        formData.append('headline_subtitle', this.headlineSubtitleInput.value.trim());
        formData.append('subtitle', this.subtitleInput.value.trim() || '2026-05-19 / 精选片段');
        formData.append('brand_text', this.brandInput.value.trim() || '胶囊播客');
        formData.append('caption_text', this.captionInput.value.trim());
        formData.append('audio_start', this.audioStartInput.value.trim());
        formData.append('audio_end', this.audioEndInput.value.trim());
        formData.append('audio_speed', this.audioSpeedSelect.value);
        if (this.srtInput.files[0]) {
            formData.append('srt', this.srtInput.files[0]);
        }
        formData.append('auto_asr', this.autoAsrInput.checked ? 'true' : 'false');
        formData.append('asr_model', this.asrModelSelect.value);
        formData.append('orientation', this.orientationSelect.value);
        formData.append('fps', this.fpsSelect.value);
        formData.append('crf', this.crfSelect.value);

        const progressTimer = this.startSoftProgress();

        try {
            const response = await fetch(`${API_BASE_URL}/documents/podcast-visualizer/submit`, {
                method: 'POST',
                body: formData,
            });

            clearInterval(progressTimer);

            if (!response.ok) {
                let message = `生成失败，状态码 ${response.status}`;
                try {
                    const data = await response.json();
                    message = data.message || message;
                } catch (error) {
                    message = await response.text() || message;
                }
                throw new Error(message);
            }

            const data = await response.json();
            if (!data.success) {
                throw new Error(data.message || '提交任务失败');
            }
            this.currentTaskId = data.task_id;
            this.currentTaskPage = 1;
            this.setProgress(8, '任务已提交');
            this.showMessage('播客视频任务已提交，生成完成后会出现在列表里', 'success');
            await this.loadTasks(1);
            this.startPolling(data.task_id);
        } catch (error) {
            clearInterval(progressTimer);
            this.emptyState.classList.remove('hidden');
            this.setProgress(0, '生成失败');
            this.showMessage(error.message, 'error');
        } finally {
            this.setBusy(false);
        }
    }

    startPolling(taskId) {
        if (this.pollTimer) {
            clearInterval(this.pollTimer);
        }
        this.pollTimer = setInterval(() => this.pollTask(taskId), 2000);
        this.pollTask(taskId);
    }

    async pollTask(taskId) {
        try {
            const response = await fetch(`${API_BASE_URL}/documents/podcast-visualizer/task/${taskId}`);
            const data = await response.json();
            if (!data.success) return;
            const task = data.data;
            this.setProgress(task.progress || 0, this.getTaskStepText(task));
            await this.loadTasks(this.currentTaskPage);
            if (task.status === 'completed') {
                clearInterval(this.pollTimer);
                this.pollTimer = null;
                const downloadUrl = `${API_BASE_URL}/documents/podcast-visualizer/task/${task.id}/download`;
                this.resultMeta.textContent = this.buildTaskMeta(task);
                if (this.isCurrentRenderer(task)) {
                    this.downloadLink.href = downloadUrl;
                    this.downloadLink.download = '';
                    this.downloadLink.classList.remove('hidden');
                    this.previewVideo.src = `${downloadUrl}?v=${encodeURIComponent(task.result?.renderer_version || 'legacy')}&t=${Date.now()}`;
                    this.previewWrap.classList.remove('hidden');
                } else {
                    this.downloadLink.classList.add('hidden');
                    this.previewVideo.removeAttribute('src');
                    this.previewVideo.load();
                    this.previewWrap.classList.add('hidden');
                    this.emptyState.classList.remove('hidden');
                    this.showMessage('这个视频是旧渲染器生成的，样式不会和当前预览一致。请重启后端/Celery 后重新生成。', 'error');
                }
                this.setProgress(100, '生成完成');
            } else if (task.status === 'failed') {
                clearInterval(this.pollTimer);
                this.pollTimer = null;
                this.showMessage(task.error_message || '任务生成失败', 'error');
                this.setProgress(task.progress || 0, '生成失败');
            }
        } catch (error) {
            console.warn('poll task failed', error);
        }
    }

    async loadTasks(page = this.currentTaskPage) {
        try {
            const nextPage = Math.max(1, Number(page) || 1);
            const response = await fetch(`${API_BASE_URL}/documents/podcast-visualizer/tasks?page=${nextPage}&per_page=${this.tasksPerPage}`);
            const data = await response.json();
            if (!data.success) return;
            this.currentTaskPage = data.pagination?.page || nextPage;
            this.renderTasks(data.data || [], data.pagination || null);
        } catch (error) {
            console.warn('load tasks failed', error);
        }
    }

    async requeueTask(taskId) {
        if (!taskId) return;
        this.hideMessage();
        try {
            const response = await fetch(`${API_BASE_URL}/documents/podcast-visualizer/requeue/${taskId}`, {
                method: 'POST',
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.success) {
                throw new Error(data.message || `重新入队失败，状态码 ${response.status}`);
            }
            this.showMessage('播客任务已重新投递，请确认 podcast Celery 已启动', 'success');
            await this.loadTasks(this.currentTaskPage);
        } catch (error) {
            this.showMessage(error.message || '重新投递失败', 'error');
        }
    }

    renderTasks(tasks, pagination = null) {
        if (!tasks.length) {
            this.taskListBody.innerHTML = '<tr><td colspan="5" class="px-4 py-8 text-center text-slate-400">暂无任务</td></tr>';
            this.renderTaskPagination(pagination);
            return;
        }

        this.taskListBody.innerHTML = tasks.map((task) => {
            const podcast = task.params?.podcast_visualizer || {};
            const result = task.result || {};
            const downloadUrl = result.download_url
                ? this.resolveApiDownloadUrl(result.download_url)
                : '';
            const currentRenderer = this.isCurrentRenderer(task);
            const isCompleted = task.status === 'completed';
            const canRequeue = !isCompleted;
            const enqueueHint = !task.celery_task_id && canRequeue
                ? '<div class="mt-1 text-[11px] font-bold text-red-500">未入队</div>'
                : '';
            const statusClass = task.status === 'completed'
                ? 'text-emerald-600 bg-emerald-50'
                : task.status === 'failed'
                    ? 'text-red-600 bg-red-50'
                    : 'text-amber-700 bg-amber-50';
            const actionHtml = downloadUrl && isCompleted && currentRenderer
                ? `<a class="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-700" href="${downloadUrl}" download>下载</a>`
                : downloadUrl && isCompleted
                    ? '<span class="rounded-lg bg-amber-50 px-3 py-1.5 text-xs font-bold text-amber-700">旧版本</span>'
                    : `<button type="button" data-requeue-task-id="${this.escapeHtml(task.id)}" class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-700">重新投递</button>`;
            return `
                <tr>
                    <td class="px-4 py-3">
                        <div class="max-w-[260px] truncate font-bold text-slate-800">${this.escapeHtml(podcast.title || task.title || '未命名任务')}</div>
                        <div class="text-xs text-slate-400">${this.escapeHtml(task.id.slice(0, 8))}</div>
                    </td>
                    <td class="px-4 py-3">
                        <span class="rounded-full px-2 py-1 text-xs font-bold ${statusClass}">${this.escapeHtml(task.status)}</span>
                        ${enqueueHint}
                    </td>
                    <td class="px-4 py-3">
                        <div class="w-28 rounded-full bg-stone-100 h-2 overflow-hidden">
                            <div class="h-full bg-[#b9895e]" style="width:${Number(task.progress || 0)}%"></div>
                        </div>
                        <div class="mt-1 text-xs text-slate-400">${Number(task.progress || 0)}% / ${this.escapeHtml(task.current_step || '')}</div>
                    </td>
                    <td class="px-4 py-3 text-xs text-slate-500">${result.lyrics_count ?? '-'}</td>
                    <td class="px-4 py-3">
                        ${actionHtml}
                    </td>
                </tr>
            `;
        }).join('');
        this.renderTaskPagination(pagination);
    }

    renderTaskPagination(pagination) {
        if (!this.taskPagination || !pagination) {
            return;
        }

        const total = Number(pagination.total || 0);
        const page = Number(pagination.page || 1);
        const totalPages = Math.max(1, Number(pagination.total_pages || 1));
        this.taskPagination.classList.toggle('hidden', total <= this.tasksPerPage);
        this.taskPagination.classList.toggle('flex', total > this.tasksPerPage);
        this.taskPrevPageBtn.disabled = page <= 1;
        this.taskNextPageBtn.disabled = page >= totalPages;
        this.taskPageInfo.textContent = `第 ${page} / ${totalPages} 页，共 ${total} 个任务`;
    }

    getTaskStepText(task) {
        const stepMap = {
            prepare: '准备素材',
            lyrics: '处理字幕',
            render: '渲染模板',
            encode: '编码 MP4',
            done: '生成完成',
            failed: '生成失败',
        };
        return stepMap[task.current_step] || task.current_step || task.status || '处理中';
    }

    buildTaskMeta(task) {
        const result = task.result || {};
        const parts = [];
        if (result.duration) parts.push(`${Number(result.duration).toFixed(1)}s`);
        if (result.lyrics_count !== undefined) parts.push(`${result.lyrics_count} 条字幕`);
        if (!this.isCurrentRenderer(task)) parts.push('旧渲染器');
        return parts.length ? parts.join(' / ') : '视频生成完成';
    }

    isCurrentRenderer(task) {
        return task?.result?.renderer_version === this.rendererVersion
            || task?.params?.podcast_visualizer?.renderer_version === this.rendererVersion && task.status !== 'completed';
    }

    resolveApiDownloadUrl(path) {
        const value = String(path || '').trim();
        if (!value) return '';
        if (/^https?:\/\//i.test(value)) return value;
        if (value.startsWith('/api/')) {
            const apiRoot = String(API_BASE_URL || '').replace(/\/api\/v1\/?$/, '');
            return `${apiRoot}${value}`;
        }
        return value.startsWith('/') ? `${window.location.origin}${value}` : value;
    }

    startSoftProgress() {
        let value = 16;
        return setInterval(() => {
            value = Math.min(88, value + 4);
            const text = value < 38 ? '解析音频' : (value < 68 ? '渲染模板' : '编码 MP4');
            this.setProgress(value, text);
        }, 1000);
    }

    setBusy(isBusy) {
        this.generateBtn.disabled = isBusy;
        this.generateBtn.innerHTML = isBusy
            ? '<i class="fas fa-spinner fa-spin mr-2"></i>生成中'
            : '<i class="fas fa-video mr-2"></i>生成播客视频';
    }

    setProgress(percent, text) {
        this.progressBar.style.width = `${percent}%`;
        this.progressLabel.textContent = `${percent}%`;
        this.progressText.textContent = text;
    }

    showMessage(text, type) {
        this.message.textContent = text;
        this.message.className = 'rounded-lg border px-4 py-3 text-sm';
        if (type === 'success') {
            this.message.classList.add('bg-emerald-50', 'border-emerald-200', 'text-emerald-700');
        } else {
            this.message.classList.add('bg-red-50', 'border-red-200', 'text-red-700');
        }
        this.message.classList.remove('hidden');
    }

    hideMessage() {
        this.message.classList.add('hidden');
    }

    getFilenameFromResponse(response) {
        const disposition = response.headers.get('content-disposition') || '';
        const utf8Match = disposition.match(/filename\\*=UTF-8''([^;]+)/i);
        if (utf8Match) {
            return decodeURIComponent(utf8Match[1]);
        }
        const filenameMatch = disposition.match(/filename="?([^"]+)"?/i);
        if (filenameMatch) {
            return filenameMatch[1];
        }
        return `${this.audio.name.replace(/\.[^/.]+$/, '')}.podcast.mp4`;
    }

    buildMetaText(meta) {
        const parts = [];
        if (meta.width && meta.height) parts.push(`${meta.width} x ${meta.height}`);
        if (meta.duration) parts.push(`${Number(meta.duration).toFixed(1)}s`);
        if (meta.lyrics) parts.push(`${meta.lyrics} 条字幕`);
        if (meta.size) parts.push(this.formatFileSize(Number(meta.size)));
        return parts.length > 0 ? parts.join(' / ') : '视频生成完成';
    }

    formatFileSize(bytes) {
        if (!bytes) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
        return `${(bytes / Math.pow(1024, index)).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
    }

    formatSeconds(seconds) {
        const totalSeconds = Math.max(0, Number(seconds) || 0);
        const wholeSeconds = Math.floor(totalSeconds);
        const minutes = Math.floor(wholeSeconds / 60);
        const secs = wholeSeconds % 60;
        const tenths = Math.floor((totalSeconds - wholeSeconds) * 10);
        const suffix = tenths > 0 ? `.${tenths}` : '';
        return `${minutes}:${String(secs).padStart(2, '0')}${suffix}`;
    }

    escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    revokeDownloadUrl() {
        if (this.downloadUrl) {
            URL.revokeObjectURL(this.downloadUrl);
            this.downloadUrl = null;
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new PodcastVisualizerTool();
});
