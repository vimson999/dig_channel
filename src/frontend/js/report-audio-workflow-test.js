class ReportAudioWorkflowPage {
    constructor() {
        this.jobs = [];
        this.pollTimer = null;
        this.els = {
            providerStatus: document.getElementById('provider-status'),
            workerStatus: document.getElementById('worker-status'),
            refreshStatusBtn: document.getElementById('refresh-status-btn'),
            restartWorkerBtn: document.getElementById('restart-worker-btn'),
            notice: document.getElementById('notice'),
            pdfInput: document.getElementById('pdf-input'),
            promptInput: document.getElementById('prompt-input'),
            reviewPromptInput: document.getElementById('review-prompt-input'),
            geminiUrlInput: document.getElementById('gemini-url-input'),
            submitBtn: document.getElementById('submit-btn'),
            loadHistoryBtn: document.getElementById('load-history-btn'),
            refreshJobsBtn: document.getElementById('refresh-jobs-btn'),
            pollDelay: document.getElementById('poll-delay'),
            summaryText: document.getElementById('summary-text'),
            jobList: document.getElementById('job-list'),
        };
        this.defaults();
        this.bind();
        this.refreshStatus();
        this.loadHistory({ silent: true });
        this.resetPollTimer();
        this.render();
    }

    defaults() {
        this.els.promptInput.value = '请解析上传的研报 PDF，提取核心观点、关键数据、风险提示和可用于口播的摘要。';
        this.els.reviewPromptInput.value = '担任首席责任主编，利用提供的PDF材料和可查网络信息，对上文内容逐条复核与勘误，确认事实准确、逻辑清晰、表达规范，列出每条修正点及依据，并给出最终优化后的信息版本，附上核对过程说明和参考来源。';
    }

    bind() {
        this.els.refreshStatusBtn.addEventListener('click', () => this.refreshStatus());
        this.els.restartWorkerBtn.addEventListener('click', () => this.restartWorker());
        this.els.submitBtn.addEventListener('click', () => this.submit());
        this.els.loadHistoryBtn.addEventListener('click', () => this.loadHistory());
        this.els.refreshJobsBtn.addEventListener('click', () => this.refreshJobs());
        this.els.pollDelay.addEventListener('change', () => this.resetPollTimer());
        this.els.jobList.addEventListener('click', (event) => this.handleJobAction(event));
    }

    apiRoot() {
        const configured = window.API_BASE_URL || window.CONFIG?.API_BASE_URL || '';
        if (configured) return configured.replace(/\/api\/v1\/?$/, '').replace(/\/$/, '');
        return `${window.location.protocol}//${window.location.hostname}:5001`;
    }

    token() {
        return localStorage.getItem('access_token') || localStorage.getItem('token') || '';
    }

    headers(includeContentType = true) {
        const token = this.token();
        return {
            ...(includeContentType ? { 'Content-Type': 'application/json' } : {}),
            ...(token ? { Authorization: `Bearer ${token}` } : {})
        };
    }

    async request(path, options = {}) {
        const { timeoutMs = 15000, ...fetchOptions } = options;
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const isFormData = typeof FormData !== 'undefined' && fetchOptions.body instanceof FormData;
            const response = await fetch(`${this.apiRoot()}${path}`, {
                ...fetchOptions,
                headers: {
                    ...this.headers(!isFormData),
                    ...(fetchOptions.headers || {})
                },
                signal: controller.signal,
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.success === false) {
                throw new Error(data.message || data.error?.message || `HTTP ${response.status}`);
            }
            return data;
        } catch (error) {
            if (error.name === 'AbortError') throw new Error(`请求超时（${Math.round(timeoutMs / 1000)}s）`);
            throw error;
        } finally {
            clearTimeout(timeoutId);
        }
    }

    async refreshStatus() {
        await Promise.all([this.refreshProviderStatus(), this.refreshWorkerStatus()]);
    }

    async refreshProviderStatus() {
        try {
            const result = await this.request('/api/report-audio-workflows/status', { timeoutMs: 10000 });
            const provider = result.data?.provider || {};
            const gemini = result.data?.report_pdf_gemini || result.data?.gemini_browser || {};
            const doubao = result.data?.doubao_browser_tts || {};
            if (provider.configured && gemini.configured && doubao.configured) {
                const normalizer = provider.deepseek_configured ? 'DS' : '本地兜底';
                this.setBadge(this.els.providerStatus, `配置正常 · ${normalizer}`, 'success');
            } else {
                this.setBadge(this.els.providerStatus, '配置不完整', 'warning');
            }
        } catch (error) {
            this.setBadge(this.els.providerStatus, '配置检查失败', 'error');
        }
    }

    async refreshWorkerStatus() {
        try {
            const result = await this.request('/api/v1/celery/status', { timeoutMs: 30000 });
            const services = result.data?.services || result.services || [];
            const service = services.find((item) => item.id === 'report_audio_workflow');
            if (service?.running) {
                this.setBadge(this.els.workerStatus, `编排运行中 · 队列 ${service.queue_length || 0}`, 'success');
            } else {
                this.setBadge(this.els.workerStatus, '编排未运行', 'warning');
            }
        } catch (error) {
            this.setBadge(this.els.workerStatus, 'Worker 检查失败', 'error');
        }
    }

    async restartWorker() {
        this.els.restartWorkerBtn.disabled = true;
        this.els.restartWorkerBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>重启中';
        try {
            await this.request('/api/v1/celery/restart/report_audio_workflow', {
                method: 'POST',
                timeoutMs: 20000
            });
            this.setNotice('report_audio_workflow worker 已重启。', 'success');
            await this.refreshWorkerStatus();
        } catch (error) {
            this.setNotice(`重启失败：${error.message}`, 'error');
        } finally {
            this.els.restartWorkerBtn.disabled = false;
            this.els.restartWorkerBtn.innerHTML = '<i class="fas fa-rotate-right mr-1.5"></i>重启编排 Worker';
        }
    }

    async submit() {
        const pdf = this.els.pdfInput.files?.[0] || null;
        if (!pdf) {
            this.setNotice('请先选择 PDF。', 'error');
            return;
        }
        const localId = `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        const localJob = {
            local_id: localId,
            status: 'submitting',
            step: 'submit',
            progress: 0,
            request: {
                pdf: { filename: pdf.name, bytes: pdf.size },
                prompt: this.els.promptInput.value.trim(),
                review_prompt: this.els.reviewPromptInput.value.trim(),
            },
            logs: [],
            created_at: new Date().toISOString(),
        };
        this.jobs.unshift(localJob);
        this.render();

        this.els.submitBtn.disabled = true;
        this.els.submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>投递中';
        try {
            const form = new FormData();
            form.append('pdf', pdf, pdf.name);
            form.append('prompt', this.els.promptInput.value.trim());
            form.append('review_prompt', this.els.reviewPromptInput.value.trim());
            const geminiUrl = this.els.geminiUrlInput.value.trim();
            if (geminiUrl) form.append('gemini_url', geminiUrl);
            form.append('metadata', JSON.stringify({ source: 'report-audio-workflow-test-page', local_id: localId }));
            const result = await this.request('/api/report-audio-workflows/jobs', {
                method: 'POST',
                timeoutMs: 60000,
                body: form,
            });
            Object.assign(localJob, {
                job_id: result.job_id || result.data?.job_id,
                celery_id: result.data?.celery_id,
                status: result.status || 'queued',
                step: 'queued',
                progress: 0,
            });
            this.els.pdfInput.value = '';
            this.setNotice('编排任务已投递。', 'success');
            this.render();
            this.refreshJob(localJob.job_id);
        } catch (error) {
            Object.assign(localJob, { status: 'failed', step: 'submit_failed', error: error.message });
            this.setNotice(`投递失败：${error.message}`, 'error');
            this.render();
        } finally {
            this.els.submitBtn.disabled = false;
            this.els.submitBtn.innerHTML = '<i class="fas fa-paper-plane mr-1.5"></i>投递编排';
        }
    }

    async loadHistory(options = {}) {
        this.els.loadHistoryBtn.disabled = true;
        this.els.loadHistoryBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>加载中';
        try {
            const result = await this.request('/api/report-audio-workflows/jobs?per_page=20', { timeoutMs: 15000 });
            const items = result.data?.items || result.items || [];
            const byId = new Map();
            [...items, ...this.jobs].forEach((job) => {
                const key = job.job_id || job.local_id;
                if (key && !byId.has(key)) byId.set(key, job);
            });
            this.jobs = Array.from(byId.values());
            if (!options.silent) this.setNotice(`已加载最近 ${items.length} 条历史任务。`, 'success');
            this.render();
        } catch (error) {
            if (!options.silent) this.setNotice(`加载历史失败：${error.message}`, 'error');
        } finally {
            this.els.loadHistoryBtn.disabled = false;
            this.els.loadHistoryBtn.innerHTML = '<i class="fas fa-clock-rotate-left mr-1.5"></i>加载历史';
        }
    }

    async refreshJobs() {
        const active = this.jobs.filter((job) => job.job_id && !this.isTerminal(job.status));
        await Promise.all(active.map((job) => this.refreshJob(job.job_id).catch(() => null)));
        this.render();
    }

    async refreshJob(jobId) {
        if (!jobId) return;
        const job = this.jobs.find((item) => item.job_id === jobId);
        if (!job) return;
        try {
            const result = await this.request(`/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}`, { timeoutMs: 10000 });
            Object.assign(job, result.data || result);
            job.poll_error = null;
        } catch (error) {
            job.poll_error = error.message;
        }
        this.render();
    }

    handleJobAction(event) {
        const button = event.target.closest('[data-job-action]');
        if (!button) return;
        const jobId = button.dataset.jobId;
        const action = button.dataset.jobAction;
        if (action === 'refresh') this.refreshJob(jobId);
        if (action === 'remove') {
            this.jobs = this.jobs.filter((job) => (job.job_id || job.local_id) !== jobId);
            this.render();
        }
    }

    resetPollTimer() {
        if (this.pollTimer) window.clearInterval(this.pollTimer);
        const delay = Math.max(1000, Number(this.els.pollDelay.value) || 3000);
        this.pollTimer = window.setInterval(() => this.refreshJobs(), delay);
    }

    render() {
        const total = this.jobs.length;
        const running = this.jobs.filter((job) => ['queued', 'running', 'submitting'].includes(job.status)).length;
        const succeeded = this.jobs.filter((job) => job.status === 'succeeded').length;
        const failed = this.jobs.filter((job) => job.status === 'failed').length;
        this.els.summaryText.textContent = total
            ? `共 ${total} 条，运行中 ${running}，成功 ${succeeded}，失败 ${failed}。`
            : '等待投递。';

        if (!this.jobs.length) {
            this.els.jobList.innerHTML = '<div class="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-12 text-center text-sm text-slate-500">暂无编排任务。</div>';
            return;
        }
        this.els.jobList.innerHTML = this.jobs.map((job, index) => this.renderJob(job, index)).join('');
    }

    renderJob(job, index) {
        const status = this.statusMeta(job.status);
        const logs = Array.isArray(job.logs) ? job.logs.slice(-6) : [];
        const structured = job.structured_result || null;
        const audioUrl = job.download_audio_url ? `${this.apiRoot()}${job.download_audio_url}` : '';
        const resultUrl = job.download_result_url ? `${this.apiRoot()}${job.download_result_url}` : '';
        const pdf = job.pdf || job.request?.pdf || null;
        return `
            <article class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="status-dot ${status.dot}"></span>
                            <h3 class="truncate text-sm font-black">编排 ${this.jobs.length - index}</h3>
                        </div>
                        <p class="mt-1 truncate text-xs text-slate-500">${this.escape(job.job_id || job.local_id || '-')}</p>
                    </div>
                    <div class="flex items-center gap-1.5">
                        <span class="rounded-full px-2.5 py-1 text-xs font-bold ${status.className}">${status.label}</span>
                        ${job.job_id ? `<button data-job-action="refresh" data-job-id="${this.escape(job.job_id)}" class="rounded-md border border-slate-300 px-2 py-1.5 text-xs text-slate-600 hover:bg-slate-50" title="刷新"><i class="fas fa-rotate"></i></button>` : ''}
                        <button data-job-action="remove" data-job-id="${this.escape(job.job_id || job.local_id || '')}" class="rounded-md border border-slate-300 px-2 py-1.5 text-xs text-slate-500 hover:bg-slate-50" title="移除"><i class="fas fa-xmark"></i></button>
                    </div>
                </div>

                <div class="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
                    <div class="h-full rounded-full ${status.bar}" style="width: ${Math.min(100, Math.max(0, Number(job.progress || 0)))}%"></div>
                </div>
                <div class="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                    <span>${this.escape(job.step || '-')}</span>
                    <span>${Number(job.progress || 0)}%</span>
                </div>

                ${pdf ? `<div class="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600"><i class="fas fa-file-pdf mr-1.5 text-red-500"></i>${this.escape(pdf.filename || 'PDF')} · ${this.formatBytes(pdf.bytes)}</div>` : ''}

                <div class="mt-3 grid grid-cols-1 gap-2 md:grid-cols-3">
                    ${this.renderChild('Gemini', job.gemini || job.children?.gemini)}
                    ${(job.doubao_browser_tts || []).length ? (job.doubao_browser_tts || []).map((item, idx) => this.renderChild(`豆包 ${idx + 1}`, item)).join('') : this.renderChild('豆包', null)}
                </div>

                ${structured ? `
                    <div class="mt-3 rounded-md border border-emerald-100 bg-emerald-50 p-3">
                        <div class="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs font-bold text-emerald-700">
                            <span>${this.escape(structured.title || '结构化结果')}</span>
                            <span>${this.escape(structured.provider || '-')}</span>
                        </div>
                        <div class="max-h-48 overflow-auto whitespace-pre-wrap text-sm leading-6 text-slate-800">${this.escape(structured.final_script_for_tts || '')}</div>
                    </div>
                ` : ''}

                ${audioUrl ? `
                    <div class="mt-3 rounded-md border border-blue-100 bg-blue-50 p-3 text-sm text-blue-800">
                        <div class="flex flex-wrap items-center justify-between gap-2">
                            <span>${this.escape(job.audio?.seconds ? `${Math.round(job.audio.seconds)}s` : '音频已生成')} · ${this.formatBytes(job.audio?.bytes)}</span>
                            <div class="flex items-center gap-2">
                                ${resultUrl ? `<a href="${resultUrl}" download class="rounded-md border border-blue-200 bg-white px-3 py-1.5 text-xs font-bold text-blue-700 hover:bg-blue-50"><i class="fas fa-file-code mr-1"></i>JSON</a>` : ''}
                                <a href="${audioUrl}" download class="rounded-md bg-blue-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-800"><i class="fas fa-download mr-1"></i>WAV</a>
                            </div>
                        </div>
                    </div>
                ` : ''}

                ${job.error ? `<div class="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">${this.escape(job.error)}</div>` : ''}
                ${job.poll_error ? `<div class="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">轮询失败：${this.escape(job.poll_error)}</div>` : ''}

                ${logs.length ? `
                    <div class="mt-3 space-y-1 border-t border-slate-100 pt-3">
                        ${logs.map((log) => `<div class="flex items-start gap-2 text-xs text-slate-500"><span class="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300"></span><span>${this.escape(log.step || '-')}: ${this.escape(log.message || '')}</span></div>`).join('')}
                    </div>
                ` : ''}
            </article>
        `;
    }

    renderChild(label, child) {
        const meta = this.statusMeta(child?.status ? this.normalizeStatus(child.status) : '');
        return `
            <div class="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs">
                <div class="flex items-center justify-between gap-2">
                    <span class="font-bold text-slate-600">${this.escape(label)}</span>
                    <span class="${meta.className} rounded-full px-2 py-0.5 font-bold">${child ? meta.label : '等待'}</span>
                </div>
                <div class="mt-1 truncate text-slate-400">${this.escape(child?.job_id || '-')}</div>
            </div>
        `;
    }

    normalizeStatus(status) {
        const map = { pending: 'queued', processing: 'running', completed: 'succeeded' };
        return map[status] || status;
    }

    isTerminal(status) {
        return ['succeeded', 'failed', 'canceled'].includes(status);
    }

    statusMeta(status) {
        const map = {
            submitting: { label: '提交中', className: 'bg-slate-100 text-slate-600', dot: 'bg-slate-400', bar: 'bg-slate-500' },
            queued: { label: '排队中', className: 'bg-amber-50 text-amber-700', dot: 'bg-amber-500', bar: 'bg-amber-500' },
            running: { label: '运行中', className: 'bg-blue-50 text-blue-700', dot: 'bg-blue-500', bar: 'bg-blue-600' },
            succeeded: { label: '成功', className: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', bar: 'bg-emerald-600' },
            failed: { label: '失败', className: 'bg-red-50 text-red-700', dot: 'bg-red-500', bar: 'bg-red-600' },
            canceled: { label: '已取消', className: 'bg-slate-100 text-slate-600', dot: 'bg-slate-400', bar: 'bg-slate-500' },
        };
        return map[status] || { label: status || '等待', className: 'bg-slate-100 text-slate-600', dot: 'bg-slate-400', bar: 'bg-slate-500' };
    }

    setBadge(el, text, type) {
        const classes = {
            success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
            warning: 'border-amber-200 bg-amber-50 text-amber-700',
            error: 'border-red-200 bg-red-50 text-red-700',
            muted: 'border-slate-200 bg-white text-slate-500'
        };
        el.className = `rounded-lg border px-3 py-2 text-xs font-bold ${classes[type] || classes.muted}`;
        el.textContent = text;
    }

    setNotice(message, type = 'muted') {
        const classes = {
            success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
            error: 'border-red-200 bg-red-50 text-red-800',
            warning: 'border-amber-200 bg-amber-50 text-amber-800',
            muted: 'border-slate-200 bg-white text-slate-600'
        };
        this.els.notice.className = `mb-4 rounded-lg border px-4 py-3 text-sm ${classes[type] || classes.muted}`;
        this.els.notice.textContent = message;
        this.els.notice.classList.remove('hidden');
    }

    formatBytes(value) {
        const bytes = Number(value || 0);
        if (!bytes) return '-';
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    }

    escape(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.reportAudioWorkflowPage = new ReportAudioWorkflowPage();
});
