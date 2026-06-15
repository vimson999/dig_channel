class GeminiBrowserTestPage {
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
            targetUrl: document.getElementById('target-url'),
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
            {
                prompt: '请解析上传的研报 PDF，提取核心观点、关键数据、风险提示和可用于口播的摘要。',
                followups: [this.editorialReviewPrompt()]
            },
            {
                prompt: '请把上传的研报 PDF 整理成结构化 JSON，包含标题、机构、日期、主题、核心结论和投资线索。',
                followups: [this.editorialReviewPrompt()]
            }
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
            {
                prompt: '请解析上传的研报 PDF，提取核心观点、关键数据、风险提示和可用于口播的摘要。',
                followups: [this.editorialReviewPrompt()]
            },
            {
                prompt: '请把上传的研报 PDF 整理成结构化 JSON，包含标题、机构、日期、主题、核心结论和投资线索。',
                followups: [this.editorialReviewPrompt()]
            }
        ]));
        this.els.seedFiveBtn.addEventListener('click', () => this.seedInputs([
            {
                prompt: '请解析上传的研报 PDF，提取核心观点、关键数据、风险提示和可用于口播的摘要。',
                followups: [this.editorialReviewPrompt()]
            },
            {
                prompt: '请把上传的研报 PDF 整理成结构化 JSON，包含标题、机构、日期、主题、核心结论和投资线索。',
                followups: [this.editorialReviewPrompt()]
            },
            {
                prompt: '请从上传的研报 PDF 中提取 5 个适合做短视频选题的主题。',
                followups: [this.editorialReviewPrompt()]
            },
            {
                prompt: '请根据上传的研报 PDF 写一版 90 秒财经口播脚本。',
                followups: [this.editorialReviewPrompt()]
            },
            {
                prompt: '请找出上传研报 PDF 里的公司、行业、技术路线和市场空间判断。',
                followups: [this.editorialReviewPrompt()]
            }
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
            if (error.name === 'AbortError') {
                throw new Error(`请求超时（${Math.round(timeoutMs / 1000)}s）`);
            }
            throw error;
        } finally {
            clearTimeout(timeoutId);
        }
    }

    seedInputs(prompts) {
        this.els.inputList.innerHTML = '';
        this.inputSeq = 0;
        prompts.forEach((item) => {
            if (item && typeof item === 'object') {
                this.addInput(item.prompt || '', item.followups || []);
            } else {
                this.addInput(item || '', []);
            }
        });
        this.renderInputNumbers();
    }

    editorialReviewPrompt() {
        return '担任首席责任主编，利用提供的PDF材料和可查网络信息，对上文内容逐条复核与勘误，确认事实准确、逻辑清晰、表达规范，列出每条修正点及依据，并给出最终优化后的信息版本，附上核对过程说明和参考来源。';
    }

    addInput(prompt = '', followups = []) {
        const id = `input-${++this.inputSeq}`;
        const wrapper = document.createElement('div');
        wrapper.className = 'rounded-lg border border-slate-200 bg-slate-50 p-3';
        wrapper.dataset.inputId = id;
        const followupText = Array.isArray(followups) ? followups.join('\n') : String(followups || '');
        wrapper.innerHTML = `
            <div class="mb-2 flex items-center justify-between gap-2">
                <span class="text-xs font-black text-slate-500" data-input-label>Prompt</span>
                <div class="flex items-center gap-1.5">
                    <button data-input-action="submit" class="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50">
                        <i class="fas fa-paper-plane mr-1"></i>投递
                    </button>
                    <button data-input-action="remove" class="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-bold text-slate-500 hover:bg-slate-50" title="删除">
                        <i class="fas fa-trash"></i>
                    </button>
                </div>
            </div>
            <textarea data-input-text rows="6" class="w-full resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 focus:border-slate-500 focus:outline-none" placeholder="输入要提交给研报 PDF Gem 的 prompt">${this.escape(prompt)}</textarea>
            <label class="mt-2 block">
                <span class="mb-1 block text-xs font-bold text-slate-500">追问</span>
                <textarea data-input-followups rows="4" class="w-full resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 focus:border-slate-500 focus:outline-none" placeholder="可选：每行一个追问，按同一个会话顺序发送">${this.escape(followupText)}</textarea>
            </label>
            <label class="mt-2 block rounded-md border border-dashed border-slate-300 bg-white px-3 py-2 text-xs text-slate-500">
                <span class="mb-1 block font-bold text-slate-600">研报 PDF</span>
                <input data-input-pdf type="file" accept="application/pdf,.pdf" class="block w-full text-xs text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-slate-900 file:px-3 file:py-1.5 file:text-xs file:font-bold file:text-white">
            </label>
        `;
        this.els.inputList.appendChild(wrapper);
        this.renderInputNumbers();
    }

    renderInputNumbers() {
        Array.from(this.els.inputList.querySelectorAll('[data-input-id]')).forEach((node, index) => {
            const label = node.querySelector('[data-input-label]');
            if (label) label.textContent = `Prompt ${index + 1}`;
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
        const targets = items.filter((item) => {
            const prompt = item.querySelector('[data-input-text]').value.trim();
            const pdf = item.querySelector('[data-input-pdf]')?.files?.[0] || null;
            const followups = this.readFollowups(item);
            return prompt || pdf || followups.length;
        });
        if (!targets.length) {
            this.setNotice('没有可投递的 PDF 或 prompt。', 'error');
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
        const followupTextarea = item.querySelector('[data-input-followups]');
        const pdfInput = item.querySelector('[data-input-pdf]');
        const prompt = textarea.value.trim();
        const followups = this.readFollowups(item);
        const pdfFile = pdfInput?.files?.[0] || null;
        if (!prompt && !pdfFile) {
            if (!options.silent) this.setNotice('这条任务没有 PDF 或 prompt。', 'error');
            return null;
        }

        const localId = `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        const localJob = {
            local_id: localId,
            prompt,
            followups,
            pdf: pdfFile ? { filename: pdfFile.name, bytes: pdfFile.size } : null,
            status: 'submitting',
            step: 'submit',
            progress: 0,
            logs: [],
            created_at: new Date().toISOString(),
            metadata: { source: 'report-pdf-gemini-test-page' }
        };
        this.jobs.unshift(localJob);
        this.render();

        try {
            const targetUrl = this.els.targetUrl.value.trim();
            const form = new FormData();
            form.append('prompt', prompt);
            form.append('followups', JSON.stringify(followups));
            if (targetUrl) form.append('url', targetUrl);
            if (pdfFile) form.append('pdf', pdfFile, pdfFile.name);
            form.append('metadata', JSON.stringify({
                source: 'report-pdf-gemini-test-page',
                local_id: localId
            }));
            const result = await this.request('/api/report-pdf-gemini/jobs', {
                method: 'POST',
                timeoutMs: 60000,
                body: form
            });
            Object.assign(localJob, {
                job_id: result.job_id || result.data?.job_id,
                celery_id: result.data?.celery_id,
                status: result.status || 'queued',
                step: 'queued',
                progress: 0
            });
            textarea.value = '';
            if (followupTextarea) followupTextarea.value = '';
            if (pdfInput) pdfInput.value = '';
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

    readFollowups(item) {
        const value = item.querySelector('[data-input-followups]')?.value || '';
        return value
            .split('\n')
            .map((line) => line.trim())
            .filter(Boolean);
    }

    async refreshStatus() {
        await Promise.all([
            this.refreshProviderStatus(),
            this.refreshWorkerStatus()
        ]);
    }

    async refreshProviderStatus() {
        try {
            const result = await this.request('/api/report-pdf-gemini/status', { timeoutMs: 8000 });
            const provider = result.data?.provider || {};
            if (provider.configured && provider.user_data_dir_exists) {
                this.setBadge(this.els.providerStatus, '配置正常', 'success');
            } else {
                this.setBadge(this.els.providerStatus, '配置不完整', 'warning');
            }
            if (!this.els.targetUrl.value && provider.gemini_url) {
                this.els.targetUrl.placeholder = provider.gemini_url;
            }
        } catch (error) {
            this.setBadge(this.els.providerStatus, '配置检查失败', 'error');
        }
    }

    async refreshWorkerStatus() {
        try {
            const result = await this.request('/api/v1/celery/status', { timeoutMs: 30000 });
            const services = result.data?.services || result.services || [];
            const service = services.find((item) => item.id === 'report_pdf_gemini');
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
            await this.request('/api/v1/celery/restart/report_pdf_gemini', {
                method: 'POST',
                timeoutMs: 20000
            });
            this.setNotice('report_pdf_gemini worker 已重启。', 'success');
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
            const result = await this.request('/api/report-pdf-gemini/jobs?per_page=20', { timeoutMs: 15000 });
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
            const result = await this.request(`/api/report-pdf-gemini/jobs/${encodeURIComponent(jobId)}`, { timeoutMs: 10000 });
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
        const failed = this.jobs.filter((job) => job.status === 'failed').length;
        this.els.summaryText.textContent = total
            ? `共 ${total} 条，运行中 ${running}，成功 ${succeeded}，失败 ${failed}。`
            : '等待投递。';

        if (!this.jobs.length) {
            this.els.jobList.innerHTML = `
                <div class="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-12 text-center text-sm text-slate-500">
                    暂无任务。输入 prompt 后点“投递全部”。
                </div>
            `;
            return;
        }

        this.els.jobList.innerHTML = this.jobs.map((job, index) => this.renderJob(job, index)).join('');
    }

    renderJob(job, index) {
        const status = this.statusMeta(job.status);
        const logs = Array.isArray(job.logs) ? job.logs.slice(-4) : [];
        const prompt = job.prompt || '';
        const followups = Array.isArray(job.followups) ? job.followups : (job.request?.followups || []);
        const turns = Array.isArray(job.turns) ? job.turns : [];
        const pdf = job.pdf || job.request?.pdf || null;
        const responseText = job.response_text || '';
        const progress = Number(job.progress || 0);
        const downloadUrl = job.download_url && job.job_id
            ? `${this.apiRoot()}${job.download_url}`
            : '';
        const pdfDownloadUrl = pdf?.download_url && job.job_id
            ? `${this.apiRoot()}${pdf.download_url}`
            : '';
        return `
            <article class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
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

                <div class="mt-3 rounded-md bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-700">
                    <div class="mb-1 text-xs font-bold text-slate-500">Prompt</div>
                    <div class="max-h-28 overflow-auto whitespace-pre-wrap">${this.escape(prompt || '无文本 prompt，仅提交 PDF')}</div>
                </div>

                ${followups.length ? `
                    <div class="mt-3 rounded-md bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-700">
                        <div class="mb-1 text-xs font-bold text-slate-500">追问</div>
                        <ol class="list-decimal space-y-1 pl-5">
                            ${followups.map((item) => `<li class="whitespace-pre-wrap">${this.escape(item)}</li>`).join('')}
                        </ol>
                    </div>
                ` : ''}

                ${pdf ? `
                    <div class="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
                        <span><i class="fas fa-file-pdf mr-1.5 text-red-500"></i>${this.escape(pdf.filename || 'PDF')} · ${this.formatBytes(pdf.bytes)}</span>
                        ${pdfDownloadUrl ? `<a href="${pdfDownloadUrl}" download class="font-bold text-slate-800 hover:text-slate-950">下载原 PDF</a>` : ''}
                    </div>
                ` : ''}

                ${turns.length ? `
                    <div class="mt-3 rounded-md border border-emerald-100 bg-emerald-50 p-3">
                        <div class="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs font-bold text-emerald-700">
                            <span>Gemini 多轮回复</span>
                            ${downloadUrl ? `<a href="${downloadUrl}" download class="rounded-md bg-emerald-700 px-3 py-1.5 text-white hover:bg-emerald-800"><i class="fas fa-download mr-1"></i>下载 JSON</a>` : ''}
                        </div>
                        <div class="space-y-3">
                            ${turns.map((turn) => `
                                <div class="rounded-md bg-white/80 p-3">
                                    <div class="mb-1 text-xs font-black text-emerald-700">第 ${this.escape(turn.index || '')} 轮 · ${this.escape(turn.kind === 'followup' ? '追问' : '首次')}</div>
                                    <div class="mb-2 max-h-24 overflow-auto whitespace-pre-wrap text-xs leading-5 text-slate-500">${this.escape(turn.prompt || '无文本 prompt')}</div>
                                    <div class="max-h-64 overflow-auto whitespace-pre-wrap text-sm leading-6 text-slate-800">${this.escape(turn.response_text || '')}</div>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                ` : responseText ? `
                    <div class="mt-3 rounded-md border border-emerald-100 bg-emerald-50 p-3">
                        <div class="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs font-bold text-emerald-700">
                            <span>Gemini 回复</span>
                            ${downloadUrl ? `<a href="${downloadUrl}" download class="rounded-md bg-emerald-700 px-3 py-1.5 text-white hover:bg-emerald-800"><i class="fas fa-download mr-1"></i>下载 JSON</a>` : ''}
                        </div>
                        <div class="max-h-72 overflow-auto whitespace-pre-wrap text-sm leading-6 text-slate-800">${this.escape(responseText)}</div>
                    </div>
                ` : ''}

                ${job.error ? `<div class="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">${this.escape(job.error)}</div>` : ''}
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
    window.geminiBrowserTestPage = new GeminiBrowserTestPage();
});
