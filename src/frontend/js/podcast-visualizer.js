class PodcastVisualizerTool {
    constructor() {
        this.rendererVersion = '20260520-header-scale-v8';
        this.audio = null;
        this.downloadUrl = null;
        this.currentTaskId = null;
        this.pollTimer = null;
        this.previewTimer = null;
        this.previewAbortController = null;
        this.previewObjectUrl = null;
        this.currentPreviewPayloadKey = '';
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
        this.refreshTasksBtn.addEventListener('click', () => this.loadTasks());
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
        this.audioName.textContent = file.name;
        this.audioSize.textContent = this.formatFileSize(file.size);
        this.audioCard.classList.remove('hidden');
        this.setProgress(0, '准备生成');

        this.syncPreview();
    }

    clearAudio() {
        this.audio = null;
        this.audioInput.value = '';
        this.audioCard.classList.add('hidden');
        this.setProgress(0, '等待音频');
        this.hideMessage();
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
            this.setProgress(8, '任务已提交');
            this.showMessage('播客视频任务已提交，生成完成后会出现在列表里', 'success');
            await this.loadTasks();
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
            await this.loadTasks();
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

    async loadTasks() {
        try {
            const response = await fetch(`${API_BASE_URL}/documents/podcast-visualizer/tasks`);
            const data = await response.json();
            if (!data.success) return;
            this.renderTasks(data.data || []);
        } catch (error) {
            console.warn('load tasks failed', error);
        }
    }

    renderTasks(tasks) {
        if (!tasks.length) {
            this.taskListBody.innerHTML = '<tr><td colspan="5" class="px-4 py-8 text-center text-slate-400">暂无任务</td></tr>';
            return;
        }

        this.taskListBody.innerHTML = tasks.map((task) => {
            const podcast = task.params?.podcast_visualizer || {};
            const result = task.result || {};
            const downloadUrl = result.download_url ? `${window.location.origin}${result.download_url}` : '';
            const currentRenderer = this.isCurrentRenderer(task);
            const statusClass = task.status === 'completed'
                ? 'text-emerald-600 bg-emerald-50'
                : task.status === 'failed'
                    ? 'text-red-600 bg-red-50'
                    : 'text-amber-700 bg-amber-50';
            return `
                <tr>
                    <td class="px-4 py-3">
                        <div class="max-w-[260px] truncate font-bold text-slate-800">${this.escapeHtml(podcast.title || task.title || '未命名任务')}</div>
                        <div class="text-xs text-slate-400">${this.escapeHtml(task.id.slice(0, 8))}</div>
                    </td>
                    <td class="px-4 py-3">
                        <span class="rounded-full px-2 py-1 text-xs font-bold ${statusClass}">${this.escapeHtml(task.status)}</span>
                    </td>
                    <td class="px-4 py-3">
                        <div class="w-28 rounded-full bg-stone-100 h-2 overflow-hidden">
                            <div class="h-full bg-[#b9895e]" style="width:${Number(task.progress || 0)}%"></div>
                        </div>
                        <div class="mt-1 text-xs text-slate-400">${Number(task.progress || 0)}% / ${this.escapeHtml(task.current_step || '')}</div>
                    </td>
                    <td class="px-4 py-3 text-xs text-slate-500">${result.lyrics_count ?? '-'}</td>
                    <td class="px-4 py-3">
                        ${downloadUrl && task.status === 'completed' && currentRenderer
                            ? `<a class="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-700" href="${downloadUrl}" download>下载</a>`
                            : downloadUrl && task.status === 'completed'
                                ? '<span class="rounded-lg bg-amber-50 px-3 py-1.5 text-xs font-bold text-amber-700">旧版本</span>'
                            : '<span class="text-xs text-slate-400">等待</span>'}
                    </td>
                </tr>
            `;
        }).join('');
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
