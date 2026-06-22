class DoubaoBrowserTTSTestPage {
    constructor() {
        this.jobs = [];
        this.inputSeq = 0;
        this.pollTimer = null;
        this.els = {
            providerStatus: document.getElementById('provider-status'),
            workerStatus: document.getElementById('worker-status'),
            refreshStatusBtn: document.getElementById('refresh-status-btn'),
            restartWorkerBtn: document.getElementById('restart-worker-btn'),
            notice: document.getElementById('notice'),
            inputList: document.getElementById('input-list'),
            addInputBtn: document.getElementById('add-input-btn'),
            submitAllBtn: document.getElementById('submit-all-btn'),
            clearFinishedBtn: document.getElementById('clear-finished-btn'),
            seedTwoBtn: document.getElementById('seed-two-btn'),
            seedFiveBtn: document.getElementById('seed-five-btn'),
            submitDelay: document.getElementById('submit-delay'),
            pollDelay: document.getElementById('poll-delay'),
            loadHistoryBtn: document.getElementById('load-history-btn'),
            refreshJobsBtn: document.getElementById('refresh-jobs-btn'),
            jobList: document.getElementById('job-list'),
            summaryText: document.getElementById('summary-text'),
        };
        this.bind();
        this.seedInputs([
            '请用一句话解释液冷服务器的价值。',
            '用一句话说明 AI 服务器为什么需要更高效的散热系统。'
        ]);
        this.refreshStatus();
        this.loadHistory({ silent: true });
        this.render();
        this.resetPollTimer();
    }

    bind() {
        this.els.addInputBtn.addEventListener('click', () => this.addInput(''));
        this.els.submitAllBtn.addEventListener('click', () => this.submitAll());
        this.els.clearFinishedBtn.addEventListener('click', () => this.clearFinished());
        this.els.seedTwoBtn.addEventListener('click', () => this.seedInputs([
            '请用一句话解释液冷服务器的价值。',
            '用一句话说明 AI 服务器为什么需要更高效的散热系统。'
        ]));
        this.els.seedFiveBtn.addEventListener('click', () => this.seedInputs([
            '请用一句话解释液冷服务器的价值。',
            '用一句话说明 AI 服务器为什么需要更高效的散热系统。',
            '用一句话概括数据中心节能改造的商业价值。',
            '用一句话解释 GPU 集群对低延迟网络的需求。',
            '用一句话介绍企业采购推理服务器时最应该关注的指标。'
        ]));
        this.els.refreshStatusBtn.addEventListener('click', () => this.refreshStatus());
        this.els.restartWorkerBtn.addEventListener('click', () => this.restartWorker());
        this.els.loadHistoryBtn.addEventListener('click', () => this.loadHistory());
        this.els.refreshJobsBtn.addEventListener('click', () => this.refreshJobs());
        this.els.pollDelay.addEventListener('change', () => this.resetPollTimer());
        this.els.inputList.addEventListener('click', (event) => this.handleInputAction(event));
        this.els.jobList.addEventListener('click', (event) => this.handleJobAction(event));
    }

    apiRoot() {
        const configured = window.API_BASE_URL || window.CONFIG?.API_BASE_URL || '';
        if (configured) {
            return configured.replace(/\/api\/v1\/?$/, '').replace(/\/$/, '');
        }
        return `${window.location.protocol}//${window.location.hostname}:5001`;
    }

    token() {
        return localStorage.getItem('access_token') || localStorage.getItem('token') || '';
    }

    headers() {
        const token = this.token();
        return {
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Bearer ${token}` } : {})
        };
    }

    async request(path, options = {}) {
        const { timeoutMs = 15000, ...fetchOptions } = options;
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(`${this.apiRoot()}${path}`, {
                ...fetchOptions,
                headers: {
                    ...this.headers(),
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
            if (error.name === 'AbortError') {
                throw new Error(`请求超时（${Math.round(timeoutMs / 1000)}s）`);
            }
            throw error;
        } finally {
            clearTimeout(timeoutId);
        }
    }

    seedInputs(texts) {
        this.els.inputList.innerHTML = '';
        this.inputSeq = 0;
        texts.forEach((text) => this.addInput(text));
        this.renderInputNumbers();
    }

    addInput(text = '') {
        const id = `input-${++this.inputSeq}`;
        const wrapper = document.createElement('div');
        wrapper.className = 'rounded-lg border border-slate-200 bg-slate-50 p-3';
        wrapper.dataset.inputId = id;
        wrapper.innerHTML = `
            <div class="mb-2 flex items-center justify-between gap-2">
                <span class="text-xs font-black text-slate-500" data-input-label>文本</span>
                <div class="flex items-center gap-1.5">
                    <button data-input-action="submit" class="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50">
                        <i class="fas fa-paper-plane mr-1"></i>投递
                    </button>
                    <button data-input-action="remove" class="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-bold text-slate-500 hover:bg-slate-50" title="删除">
                        <i class="fas fa-trash"></i>
                    </button>
                </div>
            </div>
            <textarea data-input-text rows="5" class="w-full resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 focus:border-slate-500 focus:outline-none" placeholder="输入一段需要豆包朗读的文本">${this.escape(text)}</textarea>
        `;
        this.els.inputList.appendChild(wrapper);
        this.renderInputNumbers();
    }

    renderInputNumbers() {
        Array.from(this.els.inputList.querySelectorAll('[data-input-id]')).forEach((node, index) => {
            const label = node.querySelector('[data-input-label]');
            if (label) label.textContent = `文本 ${index + 1}`;
        });
    }

    handleInputAction(event) {
        const button = event.target.closest('[data-input-action]');
        if (!button) return;
        const item = button.closest('[data-input-id]');
        const action = button.dataset.inputAction;
        if (action === 'remove') {
            item.remove();
            if (!this.els.inputList.children.length) this.addInput('');
            this.renderInputNumbers();
            return;
        }
        if (action === 'submit') {
            this.submitOne(item);
        }
    }

    handleJobAction(event) {
        const button = event.target.closest('[data-job-action]');
        if (!button) return;
        const jobId = button.dataset.jobId;
        const action = button.dataset.jobAction;
        if (action === 'refresh') {
            this.refreshJob(jobId);
        }
        if (action === 'remove') {
            this.jobs = this.jobs.filter((job) => (job.job_id || job.local_id) !== jobId);
            this.render();
        }
    }

    async submitAll() {
        const items = Array.from(this.els.inputList.querySelectorAll('[data-input-id]'));
        const targets = items.filter((item) => item.querySelector('[data-input-text]').value.trim());
        if (!targets.length) {
            this.setNotice('没有可投递的文本。', 'error');
            return;
        }

        this.els.submitAllBtn.disabled = true;
        this.els.submitAllBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>投递中';
        try {
            const delay = Math.max(0, Number(this.els.submitDelay.value) || 0);
            for (const item of targets) {
                await this.submitOne(item, { silent: true });
                if (delay) await this.sleep(delay);
            }
            this.setNotice(`已投递 ${targets.length} 条任务。`, 'success');
        } catch (error) {
            this.setNotice(`批量投递中断：${error.message}`, 'error');
        } finally {
            this.els.submitAllBtn.disabled = false;
            this.els.submitAllBtn.innerHTML = '<i class="fas fa-paper-plane mr-1.5"></i>投递全部';
            this.render();
        }
    }

    async submitOne(item, options = {}) {
        const textarea = item.querySelector('[data-input-text]');
        const text = textarea.value.trim();
        if (!text) {
            if (!options.silent) this.setNotice('这条文本为空。', 'error');
            return null;
        }

        const localId = `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        const localJob = {
            local_id: localId,
            text,
            status: 'submitting',
            step: 'submit',
            progress: 0,
            logs: [],
            created_at: new Date().toISOString(),
            metadata: { source: 'browser-test-page' }
        };
        this.jobs.unshift(localJob);
        this.render();

        try {
            const result = await this.request('/api/doubao-tts/jobs', {
                method: 'POST',
                timeoutMs: 20000,
                body: JSON.stringify({
                    text,
                    metadata: {
                        source: 'browser-test-page',
                        local_id: localId
                    }
                })
            });
            Object.assign(localJob, {
                job_id: result.job_id || result.data?.job_id,
                celery_id: result.data?.celery_id,
                status: result.status || 'queued',
                step: 'queued',
                progress: 0
            });
            textarea.value = '';
            if (!options.silent) this.setNotice('任务已投递。', 'success');
            this.render();
            this.refreshJob(localJob.job_id);
            return localJob;
        } catch (error) {
            Object.assign(localJob, {
                status: 'failed',
                step: 'submit_failed',
                error: error.message
            });
            this.render();
            throw error;
        }
    }

    async refreshStatus() {
        await Promise.all([
            this.refreshProviderStatus(),
            this.refreshWorkerStatus()
        ]);
    }

    async refreshProviderStatus() {
        try {
            const result = await this.request('/api/doubao-tts/status', { timeoutMs: 8000 });
            const provider = result.data?.provider || {};
            if (provider.configured && provider.user_data_dir_exists && provider.extension_path_exists) {
                this.setBadge(this.els.providerStatus, '配置正常', 'success');
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
            const service = services.find((item) => item.id === 'doubao_browser_tts');
            if (service?.running) {
                this.setBadge(this.els.workerStatus, `Worker 运行中 · 队列 ${service.queue_length || 0}`, 'success');
            } else {
                this.setBadge(this.els.workerStatus, 'Worker 未运行', 'warning');
            }
        } catch (error) {
            this.setBadge(this.els.workerStatus, 'Worker 检查失败', 'error');
        }
    }

    async restartWorker() {
        this.els.restartWorkerBtn.disabled = true;
        this.els.restartWorkerBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>重启中';
        try {
            await this.request('/api/v1/celery/restart/doubao_browser_tts', {
                method: 'POST',
                timeoutMs: 20000
            });
            this.setNotice('doubao_browser_tts worker 已重启。', 'success');
            await this.refreshWorkerStatus();
        } catch (error) {
            this.setNotice(`重启失败：${error.message}`, 'error');
        } finally {
            this.els.restartWorkerBtn.disabled = false;
            this.els.restartWorkerBtn.innerHTML = '<i class="fas fa-rotate-right mr-1.5"></i>重启 Worker';
        }
    }

    async refreshJobs() {
        const activeJobs = this.jobs.filter((job) => job.job_id && !this.isTerminal(job.status));
        await Promise.all(activeJobs.map((job) => this.refreshJob(job.job_id).catch(() => null)));
        this.render();
    }

    async loadHistory(options = {}) {
        this.els.loadHistoryBtn.disabled = true;
        this.els.loadHistoryBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>加载中';
        try {
            const result = await this.request('/api/doubao-tts/jobs?per_page=20', { timeoutMs: 15000 });
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
            this.els.loadHistoryBtn.innerHTML = '<i class="fas fa-clock-rotate-left mr-1.5"></i>加载最近历史';
        }
    }

    async refreshJob(jobId) {
        if (!jobId) return;
        const job = this.jobs.find((item) => item.job_id === jobId);
        if (!job) return;
        try {
            const result = await this.request(`/api/doubao-tts/jobs/${encodeURIComponent(jobId)}`, { timeoutMs: 10000 });
            Object.assign(job, result.data || result);
        } catch (error) {
            job.poll_error = error.message;
        }
        this.render();
    }

    resetPollTimer() {
        if (this.pollTimer) window.clearInterval(this.pollTimer);
        const delay = Math.max(1000, Number(this.els.pollDelay.value) || 2500);
        this.pollTimer = window.setInterval(() => this.refreshJobs(), delay);
    }

    clearFinished() {
        this.jobs = this.jobs.filter((job) => !this.isTerminal(job.status));
        this.render();
    }

    render() {
        const total = this.jobs.length;
        const running = this.jobs.filter((job) => ['queued', 'running', 'submitting'].includes(job.status)).length;
        const succeeded = this.jobs.filter((job) => job.status === 'succeeded').length;
        const blocked = this.jobs.filter((job) => job.status === 'blocked').length;
        const failed = this.jobs.filter((job) => job.status === 'failed').length;
        this.els.summaryText.textContent = total
            ? `共 ${total} 条，运行中 ${running}，成功 ${succeeded}，阻塞 ${blocked}，失败 ${failed}。`
            : '等待投递。';

        if (!this.jobs.length) {
            this.els.jobList.innerHTML = `
                <div class="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-12 text-center text-sm text-slate-500">
                    暂无任务。输入两段文本后点“投递全部”。
                </div>
            `;
            return;
        }

        this.els.jobList.innerHTML = this.jobs.map((job, index) => this.renderJob(job, index)).join('');
    }

    renderJob(job, index) {
        const status = this.statusMeta(job.status);
        const audio = job.audio || null;
        const downloadUrl = audio?.download_url && job.job_id
            ? `${this.apiRoot()}${audio.download_url}`
            : '';
        const logs = Array.isArray(job.logs) ? job.logs.slice(-4) : [];
        const text = job.text || job.metadata?.text || '';
        const progress = Number(job.progress || 0);
        const blocked = job.status === 'blocked';
        const captcha = job.captcha_blocked || null;
        return `
            <article class="job-card rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="status-dot ${status.dot}"></span>
                            <h3 class="truncate text-sm font-black">任务 ${this.jobs.length - index}</h3>
                        </div>
                        <p class="mt-1 truncate text-xs text-slate-500">${this.escape(job.job_id || job.local_id || '-')}</p>
                    </div>
                    <div class="flex items-center gap-1.5">
                        <span class="rounded-full px-2.5 py-1 text-xs font-bold ${status.className}">${status.label}</span>
                        ${job.job_id ? `
                            <button data-job-action="refresh" data-job-id="${this.escape(job.job_id)}" class="rounded-md border border-slate-300 px-2 py-1.5 text-xs text-slate-600 hover:bg-slate-50" title="刷新">
                                <i class="fas fa-rotate"></i>
                            </button>
                        ` : ''}
                        <button data-job-action="remove" data-job-id="${this.escape(job.job_id || job.local_id || '')}" class="rounded-md border border-slate-300 px-2 py-1.5 text-xs text-slate-500 hover:bg-slate-50" title="移除">
                            <i class="fas fa-xmark"></i>
                        </button>
                    </div>
                </div>

                <div class="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
                    <div class="h-full rounded-full ${status.bar}" style="width: ${Math.min(100, Math.max(0, progress))}%"></div>
                </div>
                <div class="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                    <span>${this.escape(job.step || '-')}</span>
                    <span>${progress}%</span>
                </div>

                <p class="mt-3 line-clamp-3 rounded-md bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-700">${this.escape(text)}</p>

                ${audio ? `
                    <div class="mt-3 rounded-md border border-emerald-100 bg-emerald-50 p-3">
                        <div class="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-emerald-700">
                            <span>${audio.seconds || 0}s · ${audio.sample_rate || '-'}Hz · ${audio.chunks || 0} chunks</span>
                            ${downloadUrl ? `<a href="${downloadUrl}" download class="rounded-md bg-emerald-700 px-3 py-1.5 font-bold text-white hover:bg-emerald-800"><i class="fas fa-download mr-1"></i>下载 WAV</a>` : ''}
                        </div>
                        ${downloadUrl ? `<audio controls src="${downloadUrl}" class="h-9 w-full"></audio>` : ''}
                    </div>
                ` : ''}

                ${job.error ? `<div class="mt-3 rounded-md border ${blocked ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-red-200 bg-red-50 text-red-700'} px-3 py-2 text-xs">${this.escape(job.error)}</div>` : ''}
                ${captcha ? `
                    <div class="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                        <div class="font-bold">需要人工完成豆包人机验证后重试。</div>
                        ${captcha.screenshot_path ? `<div class="mt-1 break-all">截图：${this.escape(captcha.screenshot_path)}</div>` : ''}
                        ${captcha.page_url ? `<div class="mt-1 break-all">页面：${this.escape(captcha.page_url)}</div>` : ''}
                    </div>
                ` : ''}
                ${job.poll_error ? `<div class="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">轮询失败：${this.escape(job.poll_error)}</div>` : ''}

                ${logs.length ? `
                    <div class="mt-3 space-y-1 border-t border-slate-100 pt-3">
                        ${logs.map((log) => `
                            <div class="flex items-start gap-2 text-xs text-slate-500">
                                <span class="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300"></span>
                                <span class="min-w-0">${this.escape(log.step || '-')}: ${this.escape(log.message || '')}</span>
                            </div>
                        `).join('')}
                    </div>
                ` : ''}
            </article>
        `;
    }

    isTerminal(status) {
        return ['succeeded', 'failed', 'blocked', 'canceled'].includes(status);
    }

    statusMeta(status) {
        const map = {
            submitting: { label: '提交中', className: 'bg-slate-100 text-slate-600', dot: 'bg-slate-400', bar: 'bg-slate-500' },
            queued: { label: '排队中', className: 'bg-amber-50 text-amber-700', dot: 'bg-amber-500', bar: 'bg-amber-500' },
            running: { label: '运行中', className: 'bg-blue-50 text-blue-700', dot: 'bg-blue-500', bar: 'bg-blue-600' },
            succeeded: { label: '成功', className: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', bar: 'bg-emerald-600' },
            blocked: { label: '人机验证阻塞', className: 'bg-amber-50 text-amber-800', dot: 'bg-amber-500', bar: 'bg-amber-500' },
            failed: { label: '失败', className: 'bg-red-50 text-red-700', dot: 'bg-red-500', bar: 'bg-red-600' },
            canceled: { label: '已取消', className: 'bg-slate-100 text-slate-600', dot: 'bg-slate-400', bar: 'bg-slate-500' },
        };
        return map[status] || { label: status || '未知', className: 'bg-slate-100 text-slate-600', dot: 'bg-slate-400', bar: 'bg-slate-500' };
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

    sleep(ms) {
        return new Promise((resolve) => window.setTimeout(resolve, ms));
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
    window.doubaoBrowserTTSTestPage = new DoubaoBrowserTTSTestPage();
});
