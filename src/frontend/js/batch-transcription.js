const BatchTranscription = {
    API_BASE: window.API_BASE_URL || 'http://localhost:5001/api/v1',
    pollTimer: null,
    taskId: null,

    elements: {
        form: document.getElementById('batch-form'),
        inputDir: document.getElementById('input-dir'),
        outputDir: document.getElementById('output-dir'),
        pickInputDir: document.getElementById('pick-input-dir'),
        pickOutputDir: document.getElementById('pick-output-dir'),
        modelSize: document.getElementById('model-size'),
        scanBtn: document.getElementById('scan-btn'),
        startBtn: document.getElementById('start-btn'),
        table: document.getElementById('file-table'),
        summary: document.getElementById('summary-text'),
        taskState: document.getElementById('task-state'),
        progressLabel: document.getElementById('progress-label'),
        progressBar: document.getElementById('progress-bar'),
        alert: document.getElementById('alert'),
    },

    init() {
        this.elements.pickInputDir.addEventListener('click', () => this.pickDirectory('input'));
        this.elements.pickOutputDir.addEventListener('click', () => this.pickDirectory('output'));
        this.elements.scanBtn.addEventListener('click', () => this.scan());
        this.elements.form.addEventListener('submit', (event) => {
            event.preventDefault();
            this.start();
        });
    },

    payload() {
        return {
            input_dir: this.elements.inputDir.value.trim(),
            output_dir: this.elements.outputDir.value.trim(),
            model_size: this.elements.modelSize.value,
        };
    },

    async request(endpoint, data = null) {
        const options = data ? {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        } : {};
        const response = await fetch(`${this.API_BASE}${endpoint}`, options);
        const result = await response.json();
        if (!response.ok || !result.success) {
            throw new Error(result.error || '请求失败');
        }
        return result.data;
    },

    async pickDirectory(purpose) {
        const targetInput = purpose === 'input' ? this.elements.inputDir : this.elements.outputDir;
        this.hideAlert();
        try {
            const data = await this.request('/batch-transcription/select-directory', {
                purpose,
                initial_dir: targetInput.value.trim(),
            });
            targetInput.value = data.path;
        } catch (error) {
            if (error.message !== '未选择目录') {
                this.showAlert(error.message, 'error');
            }
        }
    },

    async scan() {
        this.setBusy(true, '正在扫描...');
        this.hideAlert();
        try {
            const data = await this.request('/batch-transcription/scan', {
                input_dir: this.elements.inputDir.value.trim(),
            });
            this.renderFiles(data.files, {
                total: data.total,
                completed: 0,
                succeeded: 0,
                failed: 0,
                status: 'scanned',
            });
            this.elements.summary.textContent = `找到 ${data.total} 个音频文件，支持 ${data.extensions.join('、')}`;
            this.updateProgress({ total: data.total, completed: 0, status: '已扫描' });
        } catch (error) {
            this.showAlert(error.message, 'error');
        } finally {
            this.setBusy(false);
        }
    },

    async start() {
        this.setBusy(true, '正在启动...');
        this.hideAlert();
        if (this.pollTimer) {
            clearInterval(this.pollTimer);
            this.pollTimer = null;
        }

        try {
            const data = await this.request('/batch-transcription/start', this.payload());
            this.taskId = data.id;
            this.renderTask(data);
            this.pollTimer = setInterval(() => this.poll(), 2000);
        } catch (error) {
            this.showAlert(error.message, 'error');
            this.setBusy(false);
        }
    },

    async poll() {
        if (!this.taskId) return;

        try {
            const data = await this.request(`/batch-transcription/task/${this.taskId}`);
            this.renderTask(data);
            if (['completed', 'completed_with_errors', 'failed'].includes(data.status)) {
                clearInterval(this.pollTimer);
                this.pollTimer = null;
                this.setBusy(false);
                if (data.status === 'failed') {
                    this.showAlert(data.error || '任务失败', 'error');
                }
            }
        } catch (error) {
            clearInterval(this.pollTimer);
            this.pollTimer = null;
            this.setBusy(false);
            this.showAlert(error.message, 'error');
        }
    },

    renderTask(task) {
        this.renderFiles(task.files, task);
        const current = task.current_file ? `，当前：${task.current_file}` : '';
        this.elements.summary.textContent = `共 ${task.total} 个，成功 ${task.succeeded} 个，失败 ${task.failed} 个${current}`;
        this.updateProgress(task);
    },

    renderFiles(files, task) {
        if (!files || files.length === 0) {
            this.elements.table.innerHTML = `
                <tr>
                    <td colspan="4" class="px-6 py-16 text-center text-slate-400">
                        <i class="fas fa-folder-open text-3xl mb-3 block"></i>
                        没有找到音频文件
                    </td>
                </tr>`;
            return;
        }

        const outputDir = this.elements.outputDir.value.trim();
        const modelSize = this.elements.modelSize.value;
        this.elements.table.innerHTML = files.map((file, index) => {
            const outputPath = file.output_path || (outputDir ? `${outputDir}/${this.transcriptRelativePath(file.relative_path, modelSize, file.elapsed_seconds)}` : '-');
            const title = file.error ? ` title="${this.escape(file.error)}"` : '';
            return `
                <tr class="${file.status === 'processing' ? 'bg-blue-50/40' : ''}">
                    <td class="px-6 py-4 text-slate-400 font-mono">${index + 1}</td>
                    <td class="px-6 py-4">
                        <div class="font-semibold text-slate-800 break-all">${this.escape(file.relative_path)}</div>
                        <div class="text-xs text-slate-400 break-all mt-1">${this.escape(file.source_path || '')}</div>
                    </td>
                    <td class="px-6 py-4 text-xs font-mono text-slate-600">${this.formatDuration(file.duration_seconds)}</td>
                    <td class="px-6 py-4 text-xs font-mono text-slate-600">${this.formatElapsed(file.elapsed_seconds)}</td>
                    <td class="px-6 py-4 text-xs text-slate-500 break-all">${this.escape(outputPath)}</td>
                    <td class="px-6 py-4"${title}>${this.statusPill(file.status)}</td>
                </tr>`;
        }).join('');
    },

    statusPill(status) {
        const map = {
            waiting: ['waiting', 'fa-clock', '等待'],
            processing: ['processing', 'fa-spinner fa-spin', '处理中'],
            success: ['success', 'fa-check', '成功'],
            failed: ['failed', 'fa-triangle-exclamation', '失败'],
        };
        const [className, icon, label] = map[status] || map.waiting;
        return `<span class="status-pill status-${className}"><i class="fas ${icon}"></i>${label}</span>`;
    },

    updateProgress(task) {
        const total = Number(task.total || 0);
        const completed = Number(task.completed || 0);
        const percent = total ? Math.round((completed / total) * 100) : 0;
        const label = typeof task.status === 'string' ? this.statusLabel(task.status) : task.status;
        this.elements.taskState.textContent = label || '未开始';
        this.elements.progressLabel.textContent = `${completed} / ${total}`;
        this.elements.progressBar.style.width = `${percent}%`;
    },

    statusLabel(status) {
        return {
            scanned: '已扫描',
            queued: '排队中',
            running: '处理中',
            completed: '已完成',
            completed_with_errors: '完成但有失败',
            failed: '失败',
        }[status] || status;
    },

    formatDuration(seconds) {
        if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return '-';
        const value = Math.max(0, Math.round(Number(seconds)));
        const hours = Math.floor(value / 3600);
        const minutes = Math.floor((value % 3600) / 60);
        const secs = value % 60;
        if (hours > 0) {
            return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
        }
        return `${minutes}:${String(secs).padStart(2, '0')}`;
    },

    formatElapsed(seconds) {
        if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return '-';
        const value = Number(seconds);
        if (value < 60) return `${value.toFixed(1)}s`;
        const minutes = Math.floor(value / 60);
        const secs = value % 60;
        return `${minutes}m ${secs.toFixed(0)}s`;
    },

    transcriptRelativePath(relativePath, modelSize, elapsedSeconds = null) {
        const parts = String(relativePath || '').split('/');
        const filename = parts.pop() || '';
        const dotIndex = filename.lastIndexOf('.');
        const stem = dotIndex > 0 ? filename.slice(0, dotIndex) : filename;
        const elapsedLabel = elapsedSeconds === null || elapsedSeconds === undefined ? '耗时' : this.formatElapsedForFilename(elapsedSeconds);
        parts.push(`${stem}.${modelSize}.${elapsedLabel}.txt`);
        return parts.join('/');
    },

    formatElapsedForFilename(seconds) {
        const value = Number(seconds);
        if (Number.isNaN(value)) return '耗时';
        if (value < 60) return `${value.toFixed(1)}s`.replace('.0s', 's');
        const minutes = Math.floor(value / 60);
        const secs = Math.round(value % 60);
        return `${minutes}m${String(secs).padStart(2, '0')}s`;
    },

    setBusy(isBusy, label = '') {
        this.elements.scanBtn.disabled = isBusy;
        this.elements.startBtn.disabled = isBusy;
        if (label) {
            this.elements.startBtn.innerHTML = `<i class="fas fa-spinner fa-spin mr-2"></i>${label}`;
        } else {
            this.elements.startBtn.innerHTML = '<i class="fas fa-play mr-2"></i>开始转写';
        }
    },

    showAlert(message, type) {
        this.elements.alert.className = `mx-6 mt-6 rounded-lg border px-4 py-3 text-sm ${
            type === 'error'
                ? 'border-red-200 bg-red-50 text-red-700'
                : 'border-blue-200 bg-blue-50 text-blue-700'
        }`;
        this.elements.alert.textContent = message;
        this.elements.alert.classList.remove('hidden');
    },

    hideAlert() {
        this.elements.alert.classList.add('hidden');
    },

    escape(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    },
};

document.addEventListener('DOMContentLoaded', () => BatchTranscription.init());
