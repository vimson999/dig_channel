class PdfMergeWorkflowWorkbench {
    constructor() {
        this.items = [];
        this.assets = [];
        this.jobs = [];
        this.jobsLoadPromise = null;
        this.busy = false;
        this.els = {
            notice: document.getElementById('pdf-merge-notice'),
            filesInput: document.getElementById('pdf-merge-files-input'),
            assetSearch: document.getElementById('pdf-merge-asset-search'),
            assetRefresh: document.getElementById('pdf-merge-asset-refresh'),
            assetSummary: document.getElementById('pdf-merge-asset-summary'),
            assetList: document.getElementById('pdf-merge-asset-list'),
            queue: document.getElementById('pdf-merge-queue'),
            queueCount: document.getElementById('pdf-merge-queue-count'),
            title: document.getElementById('pdf-merge-title'),
            submit: document.getElementById('pdf-merge-submit'),
            jobsRefresh: document.getElementById('pdf-merge-jobs-refresh'),
            jobList: document.getElementById('pdf-merge-job-list'),
        };
        this.polling = window.VisibilityPolling.create({
            documentRef: document,
            windowRef: window,
            intervalMs: 12000,
            poll: () => this.loadJobs({ silent: true }),
            setIntervalFn: window.setInterval.bind(window),
            clearIntervalFn: window.clearInterval.bind(window),
        });
        this.bind();
        this.renderQueue();
        if (!document.hidden) void this.loadJobs();
    }

    bind() {
        this.els.filesInput?.addEventListener('change', (event) => {
            this.addLocalFiles(Array.from(event.target.files || []));
            event.target.value = '';
        });
        this.els.assetRefresh?.addEventListener('click', () => this.loadAssets());
        this.els.assetSearch?.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                this.loadAssets();
            }
        });
        this.els.submit?.addEventListener('click', () => this.submit());
        this.els.jobsRefresh?.addEventListener('click', () => this.loadJobs());
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                this.stopPolling();
                return;
            }
            void this.loadJobs({ silent: true });
        });
        window.addEventListener('beforeunload', () => this.stopPolling());
    }

    startPolling() {
        this.polling.start();
    }

    stopPolling() {
        this.polling.stop();
    }

    apiRoot() {
        const configured = window.API_BASE_URL || window.CONFIG?.API_BASE_URL || '';
        if (configured) {
            return configured.replace(/\/api\/v1\/?$/, '').replace(/\/$/, '');
        }
        if (['localhost', '127.0.0.1'].includes(window.location.hostname)) {
            return `${window.location.protocol}//${window.location.hostname}:5001`;
        }
        return '';
    }

    async request(path, options = {}) {
        const token = window.localStorage?.getItem('access_token') || '';
        const headers = new Headers(options.headers || {});
        headers.set('Accept', 'application/json');
        if (token) headers.set('Authorization', `Bearer ${token}`);
        const response = await fetch(`${this.apiRoot()}${path}`, {
            credentials: 'include',
            ...options,
            headers,
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.success === false) {
            throw new Error(payload.message || `请求失败（${response.status}）`);
        }
        return payload;
    }

    async loadAssets({ silent = false } = {}) {
        if (!silent) this.setAssetSummary('正在加载已清洗 PDF 资产…');
        const query = new URLSearchParams({
            page: '1',
            per_page: '40',
        });
        const search = String(this.els.assetSearch?.value || '').trim();
        if (search) query.set('q', search);
        try {
            const payload = await this.request(`/api/report-audio-workflows/pdf-assets?${query.toString()}`);
            this.assets = Array.isArray(payload.data?.items) ? payload.data.items : (payload.items || []);
            this.renderAssets();
            this.setAssetSummary(`可加入 ${this.assets.length} 份已清洗 PDF；系统资产与本地上传可以混排。`);
        } catch (error) {
            this.assets = [];
            this.renderAssets();
            this.setAssetSummary(`资产加载失败：${error.message}`);
            if (!silent) this.notice(error.message, 'error');
        }
    }

    setAssetSummary(message) {
        if (this.els.assetSummary) this.els.assetSummary.textContent = message;
    }

    renderAssets() {
        const container = this.els.assetList;
        if (!container) return;
        container.replaceChildren();
        const selectable = this.assets.filter((asset) => String(asset.pipeline_asset_id || '').trim());
        if (!selectable.length) {
            container.append(this.emptyState('没有找到可复用的已清洗 PDF。'));
            return;
        }
        selectable.forEach((asset) => {
            const card = this.element('div', 'flex min-w-0 items-center gap-3 rounded-lg border border-slate-200 bg-white p-3');
            const text = this.element('div', 'min-w-0 flex-1');
            const name = this.element('div', 'truncate text-xs font-black text-slate-800', asset.filename || '未命名 PDF');
            name.title = name.textContent;
            const meta = this.element('div', 'mt-1 truncate text-[11px] text-slate-500', this.assetMeta(asset));
            text.append(name, meta);
            const add = this.element('button', 'shrink-0 rounded-md border border-blue-200 bg-blue-50 px-2.5 py-1.5 text-xs font-bold text-blue-700 hover:bg-blue-100', '加入');
            add.type = 'button';
            add.addEventListener('click', () => this.addAsset(asset));
            card.append(text, add);
            container.append(card);
        });
    }

    assetMeta(asset) {
        const parts = [asset.source_label || '已清洗资产', this.formatBytes(asset.bytes)];
        if (asset.topic_title) parts.push(asset.topic_title);
        return parts.filter(Boolean).join(' · ');
    }

    addLocalFiles(files) {
        const invalid = [];
        files.forEach((file) => {
            if (!String(file.name || '').toLowerCase().endsWith('.pdf')) {
                invalid.push(file.name || '未命名文件');
                return;
            }
            this.items.push({
                kind: 'upload',
                file,
                filename: file.name || '本地 PDF.pdf',
                bytes: Number(file.size || 0),
                sourceLabel: '本地上传',
            });
        });
        this.renderQueue();
        if (invalid.length) this.notice(`已跳过非 PDF 文件：${invalid.join('、')}`, 'error');
    }

    addAsset(asset) {
        const assetId = String(asset.pipeline_asset_id || '').trim();
        if (!assetId) {
            this.notice('该资产尚不可用，请刷新后再试。', 'error');
            return;
        }
        this.items.push({
            kind: 'pipeline_asset',
            assetRef: `pipeline:${assetId}`,
            filename: asset.filename || '系统 PDF.pdf',
            bytes: Number(asset.bytes || 0),
            sourceLabel: asset.source_label || '已清洗资产',
        });
        this.renderQueue();
    }

    removeItem(index) {
        this.items.splice(index, 1);
        this.renderQueue();
    }

    renderQueue() {
        const queue = this.els.queue;
        if (!queue) return;
        queue.replaceChildren();
        if (this.els.queueCount) this.els.queueCount.textContent = `${this.items.length} 份 PDF`;
        if (!this.items.length) {
            queue.append(this.emptyState('尚未加入 PDF。先添加本地文件或选择系统资产。'));
            this.updateSubmitState();
            return;
        }
        this.items.forEach((item, index) => {
            const row = this.element('li', 'flex items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-3');
            const sequence = this.element('span', 'flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-900 text-xs font-black text-white', String(index + 1));
            const details = this.element('div', 'min-w-0 flex-1');
            const filename = this.element('div', 'truncate text-sm font-bold text-slate-800', item.filename);
            filename.title = filename.textContent;
            const meta = this.element('div', 'mt-0.5 text-xs text-slate-500', `${item.sourceLabel} · ${this.formatBytes(item.bytes)}`);
            details.append(filename, meta);
            const remove = this.element('button', 'shrink-0 rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-bold text-slate-600 hover:bg-slate-100', '移除');
            remove.type = 'button';
            remove.addEventListener('click', () => this.removeItem(index));
            row.append(sequence, details, remove);
            queue.append(row);
        });
        this.updateSubmitState();
    }

    updateSubmitState() {
        if (!this.els.submit) return;
        this.els.submit.disabled = this.busy || this.items.length < 2;
        this.els.submit.textContent = this.busy ? '正在提交…' : '提交合并';
    }

    async submit() {
        if (this.busy) return;
        if (this.items.length < 2) {
            this.notice('请至少按顺序加入 2 份 PDF。', 'error');
            return;
        }
        const form = new FormData();
        form.append('title', String(this.els.title?.value || '').trim());
        let uploadIndex = 0;
        const items = this.items.map((item) => {
            if (item.kind === 'upload') {
                const currentUploadIndex = uploadIndex;
                uploadIndex += 1;
                form.append('files', item.file, item.filename);
                return { kind: 'upload', upload_index: currentUploadIndex };
            }
            return { kind: 'pipeline_asset', asset_ref: item.assetRef };
        });
        form.append('items', JSON.stringify(items));
        this.busy = true;
        this.updateSubmitState();
        try {
            const payload = await this.request('/api/pdf-merge-workflows/jobs', {
                method: 'POST',
                body: form,
            });
            this.items = [];
            this.renderQueue();
            this.notice(`已提交合并任务：${payload.data?.title || 'PDF 合集'}`, 'success');
            await this.loadJobs({ silent: true });
        } catch (error) {
            this.notice(error.message, 'error');
        } finally {
            this.busy = false;
            this.updateSubmitState();
        }
    }

    loadJobs({ silent = false } = {}) {
        if (this.jobsLoadPromise) return this.jobsLoadPromise;
        const request = this._loadJobs({ silent });
        const settled = request.finally(() => {
            if (this.jobsLoadPromise === settled) {
                this.jobsLoadPromise = null;
            }
        });
        this.jobsLoadPromise = settled;
        return settled;
    }

    async _loadJobs({ silent = false } = {}) {
        try {
            const payload = await this.request('/api/pdf-merge-workflows/jobs?page=1&per_page=30');
            this.jobs = Array.isArray(payload.data?.items) ? payload.data.items : (payload.items || []);
            this.renderJobs();
            this.syncPollingState();
            return this.jobs;
        } catch (error) {
            if (!silent) this.notice(`任务历史加载失败：${error.message}`, 'error');
            if (!this.jobs.length) this.renderJobs();
            return this.jobs;
        }
    }

    syncPollingState() {
        const hasActiveJob = this.jobs.some((job) => ['pending', 'processing'].includes(job.status));
        if (hasActiveJob && !document.hidden) this.startPolling();
        else this.stopPolling();
    }

    renderJobs() {
        const container = this.els.jobList;
        if (!container) return;
        container.replaceChildren();
        if (!this.jobs.length) {
            container.append(this.emptyState('还没有 PDF 合并任务。', 'p-5'));
            return;
        }
        this.jobs.forEach((job) => {
            const card = this.element('article', 'p-4');
            const head = this.element('div', 'flex items-start justify-between gap-3');
            const text = this.element('div', 'min-w-0');
            const title = this.element('div', 'truncate text-sm font-black text-slate-800', job.title || 'PDF 合集');
            title.title = title.textContent;
            const detail = this.element('div', 'mt-1 text-xs text-slate-500', `${job.source_count || 0} 份 PDF · ${this.formatDate(job.created_at)}`);
            text.append(title, detail);
            const badge = this.element('span', `shrink-0 rounded-full px-2 py-1 text-[11px] font-black ${this.statusClass(job.status)}`, this.statusLabel(job.status));
            head.append(text, badge);
            card.append(head);

            const progress = this.element('div', 'mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100');
            const progressInner = this.element('div', 'h-full rounded-full bg-blue-600');
            progressInner.style.width = `${Math.max(0, Math.min(100, Number(job.progress || 0)))}%`;
            progress.append(progressInner);
            card.append(progress);

            const step = this.element('div', 'mt-2 text-xs text-slate-500', this.jobSummary(job));
            card.append(step);
            const actions = this.jobActions(job);
            if (actions) card.append(actions);
            container.append(card);
        });
    }

    jobActions(job) {
        const actions = this.element('div', 'mt-3 flex flex-wrap gap-2');
        let hasActions = false;
        if (job.status === 'pending') {
            hasActions = true;
            actions.append(this.actionButton('取消', 'border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100', () => this.cancelJob(job.job_id)));
        }
        if (job.status === 'failed' || job.status === 'canceled') {
            hasActions = true;
            actions.append(this.actionButton('重试', 'border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100', () => this.retryJob(job.job_id)));
        }
        if (job.status === 'completed' && job.download_url) {
            hasActions = true;
            actions.append(this.actionButton('下载合并 PDF', 'border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100', () => this.downloadJob(job.job_id)));
        }
        return hasActions ? actions : null;
    }

    actionButton(label, className, handler) {
        const button = this.element('button', `rounded-md border px-2.5 py-1.5 text-xs font-bold ${className}`, label);
        button.type = 'button';
        button.addEventListener('click', handler);
        return button;
    }

    async cancelJob(jobId) {
        try {
            await this.request(`/api/pdf-merge-workflows/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' });
            this.notice('任务已取消。', 'success');
            await this.loadJobs({ silent: true });
        } catch (error) {
            this.notice(error.message, 'error');
        }
    }

    async retryJob(jobId) {
        try {
            await this.request(`/api/pdf-merge-workflows/jobs/${encodeURIComponent(jobId)}/retry`, { method: 'POST' });
            this.notice('已重新投递冻结的 PDF 输入。', 'success');
            await this.loadJobs({ silent: true });
        } catch (error) {
            this.notice(error.message, 'error');
        }
    }

    downloadJob(jobId) {
        window.location.assign(`${this.apiRoot()}/api/pdf-merge-workflows/jobs/${encodeURIComponent(jobId)}/download`);
    }

    jobSummary(job) {
        if (job.status === 'failed') return job.error_message || '任务失败，可检查输入后重试。';
        if (job.status === 'completed') {
            const output = job.output || {};
            return `已完成${output.page_count ? ` · ${output.page_count} 页` : ''}${output.reused_asset ? ' · 复用已有相同产物' : ''}`;
        }
        return job.current_step ? `当前阶段：${job.current_step}` : '正在等待处理。';
    }

    notice(message, tone = 'success') {
        const element = this.els.notice;
        if (!element) return;
        element.className = tone === 'error'
            ? 'mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700'
            : 'mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700';
        element.textContent = message;
        element.classList.remove('hidden');
    }

    emptyState(message, extraClass = '') {
        return this.element('div', `rounded-lg border border-dashed border-slate-200 bg-slate-50 px-3 py-4 text-center text-xs text-slate-500 ${extraClass}`.trim(), message);
    }

    element(tag, className = '', text = null) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== null && text !== undefined) element.textContent = String(text);
        return element;
    }

    statusLabel(status) {
        return {
            pending: '排队中',
            processing: '处理中',
            completed: '已完成',
            failed: '失败',
            canceled: '已取消',
        }[status] || '未知';
    }

    statusClass(status) {
        return {
            pending: 'bg-amber-100 text-amber-800',
            processing: 'bg-blue-100 text-blue-700',
            completed: 'bg-emerald-100 text-emerald-700',
            failed: 'bg-red-100 text-red-700',
            canceled: 'bg-slate-100 text-slate-600',
        }[status] || 'bg-slate-100 text-slate-600';
    }

    formatBytes(value) {
        const bytes = Number(value || 0);
        if (!Number.isFinite(bytes) || bytes <= 0) return '大小未知';
        if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(bytes >= 100 * 1024 * 1024 ? 0 : 1)} MB`;
    }

    formatDate(value) {
        if (!value) return '刚刚';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return new Intl.DateTimeFormat('zh-CN', {
            month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
        }).format(date);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.pdfMergeWorkflowWorkbench = new PdfMergeWorkflowWorkbench();
});
