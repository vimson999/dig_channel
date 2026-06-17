class ReportAudioWorkflowPage {
    constructor() {
        this.jobs = [];
        this.pdfAssets = [];
        this.pdfAssetPage = 1;
        this.pdfAssetPerPage = 10;
        this.pdfAssetPagination = { page: 1, per_page: 10, has_more: false };
        this.pdfAssetSearchTimer = null;
        this.pdfAssetRequestSeq = 0;
        this.selectedPdfAsset = null;
        this.jobPage = this.initialJobPage();
        this.jobPerPage = 10;
        this.jobPagination = { page: 1, per_page: 10, total: 0, pages: 0 };
        this.historyRequestSeq = 0;
        this.historyLoading = false;
        this.pollTimer = null;
        this.els = {
            providerStatus: document.getElementById('provider-status'),
            workerStatus: document.getElementById('worker-status'),
            refreshStatusBtn: document.getElementById('refresh-status-btn'),
            restartWorkerBtn: document.getElementById('restart-worker-btn'),
            notice: document.getElementById('notice'),
            pdfSourceUpload: document.getElementById('pdf-source-upload'),
            pdfSourceAsset: document.getElementById('pdf-source-asset'),
            pdfUploadPanel: document.getElementById('pdf-upload-panel'),
            pdfAssetPanel: document.getElementById('pdf-asset-panel'),
            pdfInput: document.getElementById('pdf-input'),
            pdfAssetSearch: document.getElementById('pdf-asset-search'),
            pdfAssetRefreshBtn: document.getElementById('pdf-asset-refresh-btn'),
            pdfAssetSummary: document.getElementById('pdf-asset-summary'),
            pdfAssetList: document.getElementById('pdf-asset-list'),
            pdfAssetPagination: document.getElementById('pdf-asset-pagination'),
            promptInput: document.getElementById('prompt-input'),
            reviewPromptInput: document.getElementById('review-prompt-input'),
            geminiUrlInput: document.getElementById('gemini-url-input'),
            landscapeVideoEnabled: document.getElementById('landscape-video-enabled'),
            verticalVideoEnabled: document.getElementById('vertical-video-enabled'),
            bilibiliEnabled: document.getElementById('bilibili-enabled'),
            videoTitleInput: document.getElementById('video-title-input'),
            videoSubtitleInput: document.getElementById('video-subtitle-input'),
            submitBtn: document.getElementById('submit-btn'),
            loadHistoryBtn: document.getElementById('load-history-btn'),
            refreshJobsBtn: document.getElementById('refresh-jobs-btn'),
            pollDelay: document.getElementById('poll-delay'),
            summaryText: document.getElementById('summary-text'),
            jobList: document.getElementById('job-list'),
            jobPagination: document.getElementById('job-pagination'),
        };
        this.ensureDynamicElements();
        this.defaults();
        this.bind();
        this.updatePdfSourceUi();
        this.refreshStatus();
        this.loadPdfAssets({ silent: true });
        this.loadHistory({ silent: true });
        this.resetPollTimer();
        this.render();
    }

    ensureDynamicElements() {
        if (!this.els.pdfAssetPagination && this.els.pdfAssetList) {
            const pagination = document.createElement('div');
            pagination.id = 'pdf-asset-pagination';
            pagination.className = 'mt-2';
            this.els.pdfAssetList.insertAdjacentElement('afterend', pagination);
            this.els.pdfAssetPagination = pagination;
        }
        if (!this.els.jobPagination && this.els.jobList) {
            const pagination = document.createElement('div');
            pagination.id = 'job-pagination';
            pagination.className = 'border-t border-slate-100 px-4 py-3';
            this.els.jobList.insertAdjacentElement('afterend', pagination);
            this.els.jobPagination = pagination;
        }
    }

    defaults() {
        this.els.promptInput.value = '请解析上传的研报 PDF，提取核心观点、关键数据、风险提示和可用于口播的摘要。';
        this.els.reviewPromptInput.value = '担任首席责任主编，利用提供的PDF材料和可查网络信息，对上文内容逐条复核与勘误，确认事实准确、逻辑清晰、表达规范，列出每条修正点及依据，并给出最终优化后的信息版本，附上核对过程说明和参考来源。';
    }

    initialJobPage() {
        const page = Number(new URLSearchParams(window.location.search).get('job_page') || 1);
        return Number.isFinite(page) && page > 0 ? Math.floor(page) : 1;
    }

    bind() {
        this.els.refreshStatusBtn.addEventListener('click', () => this.refreshStatus());
        this.els.restartWorkerBtn.addEventListener('click', () => this.restartWorker());
        this.els.pdfSourceUpload.addEventListener('change', () => this.updatePdfSourceUi());
        this.els.pdfSourceAsset.addEventListener('change', () => this.updatePdfSourceUi());
        this.els.pdfAssetRefreshBtn.addEventListener('click', () => this.loadPdfAssets({ page: this.pdfAssetPage }));
        this.els.pdfAssetSearch.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') this.loadPdfAssets({ page: 1 });
        });
        this.els.pdfAssetSearch.addEventListener('input', () => this.schedulePdfAssetSearch());
        this.els.pdfAssetSearch.addEventListener('search', () => this.schedulePdfAssetSearch());
        this.els.pdfAssetSearch.addEventListener('change', () => this.schedulePdfAssetSearch());
        this.els.pdfAssetList.addEventListener('click', (event) => this.handlePdfAssetClick(event));
        this.els.pdfAssetList.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') this.handlePdfAssetClick(event);
        });
        this.els.pdfAssetPagination.addEventListener('click', (event) => this.handlePdfAssetPageClick(event));
        this.els.submitBtn.addEventListener('click', () => this.submit());
        this.els.loadHistoryBtn.addEventListener('click', () => this.loadHistory({ page: 1, force: true }));
        this.els.refreshJobsBtn.addEventListener('click', () => this.refreshJobs());
        this.els.pollDelay.addEventListener('change', () => this.resetPollTimer());
        this.els.jobList.addEventListener('click', (event) => this.handleJobAction(event));
        this.els.jobPagination.addEventListener('click', (event) => this.handleJobPageClick(event));
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
                const normalizer = provider.deepseek_configured
                    ? `DS: ${provider.normalizer_model || 'deepseek-v4-pro'}`
                    : '本地兜底';
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
            const workflow = services.find((item) => item.id === 'report_audio_workflow');
            const render = services.find((item) => item.id === 'render');
            if (workflow?.running && render?.running) {
                this.setBadge(this.els.workerStatus, `Worker 正常 · 编排 ${workflow.queue_length || 0} / 渲染 ${render.queue_length || 0}`, 'success');
            } else {
                const missing = [
                    workflow?.running ? null : '编排',
                    render?.running ? null : '渲染',
                ].filter(Boolean).join('、');
                this.setBadge(this.els.workerStatus, `${missing || 'Worker'} 未运行`, 'warning');
            }
        } catch (error) {
            this.setBadge(this.els.workerStatus, 'Worker 检查失败', 'error');
        }
    }

    async restartWorker() {
        this.els.restartWorkerBtn.disabled = true;
        this.els.restartWorkerBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>重启中';
        try {
            await this.request('/api/v1/celery/restart/report_audio_workflow', { method: 'POST', timeoutMs: 20000 });
            await this.request('/api/v1/celery/restart/render', { method: 'POST', timeoutMs: 20000 });
            this.setNotice('编排和渲染 worker 已重启。', 'success');
            await this.refreshWorkerStatus();
        } catch (error) {
            this.setNotice(`重启失败：${error.message}`, 'error');
        } finally {
            this.els.restartWorkerBtn.disabled = false;
            this.els.restartWorkerBtn.innerHTML = '<i class="fas fa-rotate-right mr-1.5"></i>重启编排/渲染 Worker';
        }
    }

    updatePdfSourceUi() {
        const useAsset = this.els.pdfSourceAsset.checked;
        this.els.pdfUploadPanel.classList.toggle('hidden', useAsset);
        this.els.pdfAssetPanel.classList.toggle('hidden', !useAsset);
        this.els.pdfInput.disabled = useAsset;
        if (useAsset && !this.pdfAssets.length) {
            this.loadPdfAssets({ silent: true });
        }
    }

    async loadPdfAssets(options = {}) {
        const page = Math.max(1, Number(options.page || this.pdfAssetPage || 1));
        const requestSeq = ++this.pdfAssetRequestSeq;
        const showLoading = !options.silent;
        if (showLoading) {
            this.els.pdfAssetRefreshBtn.disabled = true;
            this.els.pdfAssetRefreshBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>加载';
        }
        const q = this.els.pdfAssetSearch.value.trim();
        try {
            const query = new URLSearchParams({
                page: String(page),
                per_page: String(this.pdfAssetPerPage),
            });
            if (q) query.set('q', q);
            const result = await this.request(`/api/report-audio-workflows/pdf-assets?${query.toString()}`, { timeoutMs: 15000 });
            if (requestSeq !== this.pdfAssetRequestSeq) return;
            this.pdfAssets = result.data?.items || result.items || [];
            this.pdfAssetPagination = result.data?.pagination || {
                page,
                per_page: this.pdfAssetPerPage,
                has_more: false,
            };
            this.pdfAssetPage = Number(this.pdfAssetPagination.page || page);
            if (!this.pdfAssets.length && this.pdfAssetPage > 1) {
                this.pdfAssetPage -= 1;
                return this.loadPdfAssets({ ...options, page: this.pdfAssetPage });
            }
            this.renderPdfAssets();
            this.renderPdfAssetPagination();
            if (!options.silent) this.setNotice(`已加载第 ${this.pdfAssetPage} 页，共 ${this.pdfAssets.length} 个已同步 PDF。`, 'success');
        } catch (error) {
            if (requestSeq !== this.pdfAssetRequestSeq) return;
            this.els.pdfAssetSummary.textContent = `加载已同步 PDF 失败：${error.message}`;
            this.els.pdfAssetList.innerHTML = '';
            this.els.pdfAssetPagination.innerHTML = '';
            if (!options.silent) this.setNotice(`加载已同步 PDF 失败：${error.message}`, 'error');
        } finally {
            if (requestSeq !== this.pdfAssetRequestSeq) return;
            this.els.pdfAssetRefreshBtn.disabled = false;
            this.els.pdfAssetRefreshBtn.innerHTML = '<i class="fas fa-rotate mr-1"></i>刷新';
        }
    }

    schedulePdfAssetSearch() {
        if (this.pdfAssetSearchTimer) window.clearTimeout(this.pdfAssetSearchTimer);
        this.pdfAssetSearchTimer = window.setTimeout(() => {
            this.loadPdfAssets({ page: 1, silent: true });
        }, 350);
    }

    renderPdfAssets() {
        const selectedId = this.selectedPdfAsset?.id || '';
        this.els.pdfAssetSummary.textContent = this.pdfAssets.length
            ? `第 ${this.pdfAssetPage} 页 · 本页 ${this.pdfAssets.length} 个 PDF${this.selectedPdfAsset ? ` · 已选 ${this.selectedPdfAsset.filename}` : ''}`
            : '没有找到已下载的同步 PDF。';
        if (!this.pdfAssets.length) {
            this.els.pdfAssetList.innerHTML = '<div class="rounded-md border border-dashed border-slate-300 bg-white px-3 py-6 text-center text-xs text-slate-500">暂无可选 PDF。请先完成知识星球同步并下载附件。</div>';
            this.renderPdfAssetPagination();
            return;
        }
        this.els.pdfAssetList.innerHTML = this.pdfAssets.map((item) => {
            const active = item.id === selectedId;
            const previewPath = item.preview_url || `/api/report-audio-workflows/pdf-assets/${encodeURIComponent(item.id)}/preview`;
            const previewUrl = `${this.apiRoot()}${previewPath}`;
            const itemTitle = [item.filename, item.topic_title, item.tab_label].filter(Boolean).join('\n');
            return `
                <div role="button" tabindex="0" data-pdf-asset-id="${this.escape(item.id)}" title="${this.escape(itemTitle)}" class="w-full cursor-pointer rounded-md border px-3 py-2 text-left text-xs ${active ? 'border-indigo-300 bg-indigo-50 text-indigo-900' : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'}">
                    <div class="flex items-start justify-between gap-2">
                        <div class="min-w-0">
                            <div class="truncate font-bold">${this.escape(item.filename || 'PDF')}</div>
                            <div class="mt-1 truncate text-slate-500">${this.escape(item.topic_title || item.tab_label || item.topic_id || '-')}</div>
                        </div>
                        <div class="flex shrink-0 items-center gap-1.5">
                            <span class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-bold text-slate-500">${this.formatBytes(item.bytes)}</span>
                            ${previewUrl ? `<a data-pdf-preview-link href="${this.escape(previewUrl)}" target="_blank" rel="noopener noreferrer" class="rounded-md border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-bold text-indigo-700 hover:bg-indigo-50"><i class="fas fa-eye mr-1"></i>预览</a>` : ''}
                        </div>
                    </div>
                    <div class="mt-1 truncate text-[11px] text-slate-400">${this.escape(item.tab_label || '')}${item.topic_create_time ? ` · ${this.escape(item.topic_create_time)}` : ''}</div>
                </div>
            `;
        }).join('');
    }

    renderPdfAssetPagination() {
        const page = Math.max(1, Number(this.pdfAssetPagination?.page || this.pdfAssetPage || 1));
        const hasMore = Boolean(this.pdfAssetPagination?.has_more);
        const hasPrevious = page > 1;
        this.els.pdfAssetPagination.innerHTML = `
            <div class="flex items-center justify-between gap-2 text-xs text-slate-500">
                <button type="button" data-pdf-asset-page="prev" class="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 font-bold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40" ${hasPrevious ? '' : 'disabled'}>
                    <i class="fas fa-chevron-left mr-1"></i>上一页
                </button>
                <span class="shrink-0 font-bold text-slate-500">第 ${page} 页</span>
                <button type="button" data-pdf-asset-page="next" class="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 font-bold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40" ${hasMore ? '' : 'disabled'}>
                    下一页<i class="fas fa-chevron-right ml-1"></i>
                </button>
            </div>
        `;
    }

    handlePdfAssetClick(event) {
        if (event.target.closest('[data-pdf-preview-link]')) return;
        const button = event.target.closest('[data-pdf-asset-id]');
        if (!button) return;
        const asset = this.pdfAssets.find((item) => item.id === button.dataset.pdfAssetId);
        if (!asset) return;
        this.selectedPdfAsset = asset;
        this.renderPdfAssets();
    }

    handlePdfAssetPageClick(event) {
        const button = event.target.closest('[data-pdf-asset-page]');
        if (!button || button.disabled) return;
        const direction = button.dataset.pdfAssetPage;
        if (direction === 'prev' && this.pdfAssetPage > 1) {
            this.loadPdfAssets({ page: this.pdfAssetPage - 1 });
        }
        if (direction === 'next' && this.pdfAssetPagination?.has_more) {
            this.loadPdfAssets({ page: this.pdfAssetPage + 1 });
        }
    }

    async submit() {
        const useAsset = this.els.pdfSourceAsset.checked;
        const pdf = useAsset ? null : (this.els.pdfInput.files?.[0] || null);
        const selectedAsset = useAsset ? this.selectedPdfAsset : null;
        if (!pdf && !selectedAsset) {
            this.setNotice(useAsset ? '请先选择一个已同步 PDF。' : '请先上传 PDF。', 'error');
            return;
        }
        const localId = `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        const pdfSummary = selectedAsset
            ? { filename: selectedAsset.filename, bytes: selectedAsset.bytes, source: 'zsxq_attachment', attachment_id: selectedAsset.id, topic_title: selectedAsset.topic_title, tab_label: selectedAsset.tab_label }
            : { filename: pdf.name, bytes: pdf.size, source: 'upload' };
        const localJob = {
            local_id: localId,
            status: 'submitting',
            step: 'submit',
            progress: 0,
            request: {
                pdf: pdfSummary,
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
            if (selectedAsset) {
                form.append('pdf_asset_id', selectedAsset.id);
            } else {
                form.append('pdf', pdf, pdf.name);
            }
            form.append('prompt', this.els.promptInput.value.trim());
            form.append('review_prompt', this.els.reviewPromptInput.value.trim());
            const geminiUrl = this.els.geminiUrlInput.value.trim();
            if (geminiUrl) form.append('gemini_url', geminiUrl);
            form.append('landscape_video_enabled', this.els.landscapeVideoEnabled.checked ? '1' : '0');
            form.append('vertical_video_enabled', this.els.verticalVideoEnabled.checked ? '1' : '0');
            form.append('bilibili_enabled', this.els.bilibiliEnabled.checked ? '1' : '0');
            form.append('video_title', this.els.videoTitleInput.value.trim());
            form.append('video_subtitle', this.els.videoSubtitleInput.value.trim());
            form.append('metadata', JSON.stringify({
                source: 'report-audio-workflow-test-page',
                local_id: localId,
                pdf_source: selectedAsset ? 'zsxq_attachment' : 'upload',
                pdf_asset_id: selectedAsset?.id || null,
            }));
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
            if (!selectedAsset) this.els.pdfInput.value = '';
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
        if (this.historyLoading && !options.force) return;
        const page = Math.max(1, Number(options.page || this.jobPage || 1));
        const requestSeq = ++this.historyRequestSeq;
        const showLoading = !options.silent;
        this.jobPage = page;
        this.historyLoading = true;
        if (showLoading) {
            this.els.loadHistoryBtn.disabled = true;
            this.els.loadHistoryBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>加载中';
        }
        try {
            const query = new URLSearchParams({
                page: String(page),
                per_page: String(this.jobPerPage),
            });
            const result = await this.request(`/api/report-audio-workflows/jobs?${query.toString()}`, { timeoutMs: 15000 });
            if (requestSeq !== this.historyRequestSeq) return;
            const items = result.data?.items || result.items || [];
            this.jobPagination = result.data?.pagination || {
                page,
                per_page: this.jobPerPage,
                total: items.length,
                pages: items.length ? 1 : 0,
            };
            this.jobPage = Number(this.jobPagination.page || page);
            if (!items.length && this.jobPage > 1) {
                this.jobPage -= 1;
                return this.loadHistory({ ...options, page: this.jobPage, force: true });
            }
            this.jobs = items;
            if (!options.silent) this.setNotice(`已加载编排历史第 ${this.jobPage} 页。`, 'success');
            this.render();
        } catch (error) {
            if (requestSeq !== this.historyRequestSeq) return;
            if (!options.silent) this.setNotice(`加载历史失败：${error.message}`, 'error');
        } finally {
            if (requestSeq !== this.historyRequestSeq) return;
            this.historyLoading = false;
            if (showLoading) {
                this.els.loadHistoryBtn.disabled = false;
                this.els.loadHistoryBtn.innerHTML = '<i class="fas fa-clock-rotate-left mr-1.5"></i>加载历史';
            }
        }
    }

    async refreshJobs() {
        if (!this.jobs.some((job) => job.job_id && !this.isTerminal(job.status))) {
            if (this.historyLoading) return;
            await this.loadHistory({ page: this.jobPage, silent: true });
            return;
        }
        const active = this.jobs.filter((job) => job.job_id && !this.isTerminal(job.status));
        await Promise.all(active.map((job) => this.refreshJob(job.job_id).catch(() => null)));
        if (this.historyLoading) return;
        await this.loadHistory({ page: this.jobPage, silent: true });
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
        if (action === 'retry') this.retryJob(jobId);
        if (action === 'remove') {
            this.jobs = this.jobs.filter((job) => (job.job_id || job.local_id) !== jobId);
            this.render();
        }
    }

    async retryJob(jobId) {
        if (!jobId) return;
        if (!window.confirm('从失败处续跑这个编排？已完成的 Gemini 和豆包分段会尽量复用。')) return;
        const job = this.jobs.find((item) => item.job_id === jobId);
        if (job) {
            job.status = 'running';
            job.step = 'queued';
            job.error = null;
            job.poll_error = null;
            this.render();
        }
        try {
            const result = await this.request(`/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}/retry`, {
                method: 'POST',
                timeoutMs: 20000,
            });
            const payload = result.data || result;
            const index = this.jobs.findIndex((item) => item.job_id === jobId);
            if (index >= 0) this.jobs[index] = { ...this.jobs[index], ...payload };
            this.setNotice(result.message || '编排已从失败处续跑。', 'success');
            this.render();
            this.refreshJob(jobId);
        } catch (error) {
            if (job) {
                job.status = 'failed';
                job.error = error.message;
            }
            this.setNotice(`续跑失败：${error.message}`, 'error');
            this.render();
        }
    }

    handleJobPageClick(event) {
        const button = event.target.closest('[data-job-page]');
        if (!button || button.disabled) return;
        event.preventDefault();
        const direction = button.dataset.jobPage;
        let targetPage = this.jobPage;
        if (direction === 'prev' && this.jobPage > 1) {
            targetPage = this.jobPage - 1;
        }
        if (direction === 'next' && this.jobPage < Number(this.jobPagination?.pages || 0)) {
            targetPage = this.jobPage + 1;
        }
        if (targetPage === this.jobPage) return;
        this.updateJobPageUrl(targetPage);
        this.loadHistory({ page: targetPage, force: true });
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
        const historyTotal = Number(this.jobPagination?.total || 0);
        const pageCount = Number(this.jobPagination?.pages || 0);
        this.els.summaryText.textContent = total
            ? `全部 ${historyTotal || total} 条 · 第 ${this.jobPage}/${Math.max(1, pageCount || 1)} 页，本页 ${total} 条，运行中 ${running}，成功 ${succeeded}，失败 ${failed}。`
            : '等待投递。';

        if (!this.jobs.length) {
            this.els.jobList.innerHTML = '<div class="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-12 text-center text-sm text-slate-500">暂无编排任务。</div>';
            this.renderJobPagination();
            return;
        }
        this.els.jobList.innerHTML = this.jobs.map((job, index) => this.renderJob(job, index)).join('');
        this.renderJobPagination();
    }

    renderJobPagination() {
        const page = Math.max(1, Number(this.jobPagination?.page || this.jobPage || 1));
        const pages = Math.max(1, Number(this.jobPagination?.pages || 1));
        const total = Number(this.jobPagination?.total || this.jobs.length || 0);
        const hasPrevious = page > 1;
        const hasNext = page < pages;
        this.els.jobPagination.innerHTML = `
            <div class="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                ${hasPrevious ? `
                    <a href="${this.escape(this.jobPageUrl(page - 1))}" data-job-page="prev" class="rounded-md border border-slate-200 bg-white px-3 py-1.5 font-bold text-slate-600 hover:bg-slate-50">
                        <i class="fas fa-chevron-left mr-1"></i>上一页
                    </a>
                ` : `
                    <span class="cursor-not-allowed rounded-md border border-slate-200 bg-white px-3 py-1.5 font-bold text-slate-400 opacity-50">
                        <i class="fas fa-chevron-left mr-1"></i>上一页
                    </span>
                `}
                <span class="font-bold text-slate-500">第 ${page}/${pages} 页 · 共 ${total} 条</span>
                ${hasNext ? `
                    <a href="${this.escape(this.jobPageUrl(page + 1))}" data-job-page="next" class="rounded-md border border-slate-200 bg-white px-3 py-1.5 font-bold text-slate-600 hover:bg-slate-50">
                        下一页<i class="fas fa-chevron-right ml-1"></i>
                    </a>
                ` : `
                    <span class="cursor-not-allowed rounded-md border border-slate-200 bg-white px-3 py-1.5 font-bold text-slate-400 opacity-50">
                        下一页<i class="fas fa-chevron-right ml-1"></i>
                    </span>
                `}
            </div>
        `;
    }

    jobPageUrl(page) {
        const url = new URL(window.location.href);
        url.searchParams.set('job_page', String(Math.max(1, page)));
        return `${url.pathname}${url.search}${url.hash}`;
    }

    updateJobPageUrl(page) {
        if (!window.history?.replaceState) return;
        const url = new URL(window.location.href);
        url.searchParams.set('job_page', String(Math.max(1, page)));
        window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    }

    jobSequenceNumber(index) {
        const total = Number(this.jobPagination?.total || 0);
        const page = Math.max(1, Number(this.jobPagination?.page || this.jobPage || 1));
        const perPage = Math.max(1, Number(this.jobPagination?.per_page || this.jobPerPage || this.jobs.length || 1));
        if (total > 0) return Math.max(1, total - ((page - 1) * perPage + index));
        return Math.max(1, this.jobs.length - index);
    }
    renderJob(job, index) {
        const status = this.statusMeta(job.status);
        const logs = Array.isArray(job.logs) ? job.logs.slice(-6) : [];
        const structured = job.structured_result || null;
        const sequenceLabel = `编排 ${this.jobSequenceNumber(index)}`;
        const displayTitle = this.jobDisplayTitle(job, sequenceLabel);
        const hasDisplayTitle = displayTitle !== sequenceLabel;
        const metaLine = this.jobMetaLine(job, sequenceLabel, hasDisplayTitle);
        const audioUrl = job.download_audio_url ? `${this.apiRoot()}${job.download_audio_url}` : '';
        const resultUrl = job.download_result_url ? `${this.apiRoot()}${job.download_result_url}` : '';
        const videoUrl = job.download_video_url ? `${this.apiRoot()}${job.download_video_url}` : '';
        const coverUrl = job.download_cover_url ? `${this.apiRoot()}${job.download_cover_url}` : '';
        const pdf = job.pdf || job.request?.pdf || null;
        const parentStatus = job.status || '';
        const geminiChild = job.gemini || job.children?.gemini;
        const doubaoItems = job.doubao_browser_tts || [];
        const publisherChild = job.publisher_video || job.children?.publisher_video;
        const video = job.video || null;
        return `
            <article class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="status-dot ${status.dot}"></span>
                            <h3 class="truncate text-sm font-black" title="${this.escape(displayTitle)}">${this.escape(displayTitle)}</h3>
                        </div>
                        <p class="mt-1 truncate text-xs text-slate-500" title="${this.escape(metaLine)}">${this.escape(metaLine)}</p>
                    </div>
                    <div class="flex items-center gap-1.5">
                        <span class="rounded-full px-2.5 py-1 text-xs font-bold ${status.className}">${status.label}</span>
                        ${job.job_id && job.status === 'failed' ? `<button data-job-action="retry" data-job-id="${this.escape(job.job_id)}" class="rounded-md border border-emerald-300 bg-emerald-50 px-2 py-1.5 text-xs text-emerald-700 hover:bg-emerald-100" title="从失败处续跑"><i class="fas fa-play"></i></button>` : ''}
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
                    ${this.renderChild('Gemini', geminiChild, parentStatus, this.stageDisplayStatus('gemini', job, geminiChild))}
                    ${doubaoItems.length ? doubaoItems.map((item, idx) => this.renderChild(this.doubaoChildLabel(item, idx, doubaoItems), item, parentStatus, this.stageDisplayStatus('doubao', job, item))).join('') : this.renderChild('豆包', null, parentStatus, this.stageDisplayStatus('doubao', job, null))}
                    ${this.renderChild('横屏视频', publisherChild, parentStatus, this.stageDisplayStatus('publisher', job, publisherChild))}
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

                ${video ? `
                    <div class="mt-3 rounded-md border border-indigo-100 bg-indigo-50 p-3 text-sm text-indigo-800">
                        <div class="flex flex-wrap items-center justify-between gap-2">
                            <span>${this.videoSummary(video)}</span>
                            <div class="flex items-center gap-2">
                                ${coverUrl ? `<a href="${coverUrl}" download class="rounded-md border border-indigo-200 bg-white px-3 py-1.5 text-xs font-bold text-indigo-700 hover:bg-indigo-50"><i class="fas fa-image mr-1"></i>封面</a>` : ''}
                                ${videoUrl ? `<a href="${videoUrl}" download class="rounded-md bg-indigo-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-indigo-800"><i class="fas fa-film mr-1"></i>MP4</a>` : ''}
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

    jobDisplayTitle(job, fallback) {
        const structured = job?.structured_result || {};
        const candidates = [
            job?.title,
            structured.cover_title,
            structured.title,
            Array.isArray(structured.titles?.short) ? structured.titles.short[0] : '',
            Array.isArray(structured.titles?.standard) ? structured.titles.standard[0] : '',
            job?.video?.title,
            job?.request?.metadata?.title,
            job?.pdf?.topic_title,
            job?.request?.pdf?.topic_title,
        ];
        for (const candidate of candidates) {
            const title = this.compactTitle(candidate);
            if (title) return title;
        }
        return fallback;
    }

    jobMetaLine(job, sequenceLabel, hasDisplayTitle) {
        const createdAt = this.formatDateTime(job?.created_at);
        const identity = job?.job_id || job?.local_id || '-';
        const parts = [];
        if (hasDisplayTitle) parts.push(sequenceLabel);
        if (createdAt) parts.push(`创建 ${createdAt}`);
        parts.push(identity);
        return parts.join(' · ');
    }

    compactTitle(value) {
        return String(value ?? '')
            .replace(/\s*\n\s*/g, ' / ')
            .replace(/\s+/g, ' ')
            .trim();
    }

    renderChild(label, child, parentStatus = '', statusOverride = '') {
        const childStatus = child?.status ? this.normalizeStatus(child.status) : '';
        const meta = this.statusMeta(statusOverride || this.childDisplayStatus(childStatus, parentStatus, Boolean(child)));
        return `
            <div class="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs">
                <div class="flex items-center justify-between gap-2">
                    <span class="font-bold text-slate-600">${this.escape(label)}</span>
                    <span class="${meta.className} rounded-full px-2 py-0.5 font-bold">${meta.label}</span>
                </div>
                <div class="mt-1 truncate text-slate-400">${this.escape(child?.job_id || '-')}</div>
            </div>
        `;
    }

    doubaoChildLabel(item, index, items = []) {
        const metadata = item?.metadata || {};
        const rawChunk = Number(metadata.chunk_index);
        const chunkIndex = Number.isFinite(rawChunk) && rawChunk > 0 ? rawChunk : index + 1;
        const rawTotal = Number(metadata.chunk_total);
        const chunkTotal = Number.isFinite(rawTotal) && rawTotal > 0 ? rawTotal : 0;
        const priorAttempts = items
            .slice(0, index)
            .filter((candidate) => {
                const candidateChunk = Number(candidate?.metadata?.chunk_index);
                return Number.isFinite(candidateChunk) && candidateChunk === chunkIndex;
            })
            .length;
        const rawRetryIndex = Number(metadata.retry_index);
        const retryIndex = Number.isFinite(rawRetryIndex) ? rawRetryIndex : priorAttempts;
        const hasMultipleAttempts = retryIndex > 0 || items.some((candidate, candidateIndex) => {
            if (candidateIndex === index) return false;
            const candidateChunk = Number(candidate?.metadata?.chunk_index);
            return Number.isFinite(candidateChunk) && candidateChunk === chunkIndex;
        });
        const base = `豆包 ${chunkIndex}${chunkTotal ? `/${chunkTotal}` : ''}`;
        if (retryIndex > 0) return `${base} · 重试 ${retryIndex}`;
        if (hasMultipleAttempts) return `${base} · 首次`;
        return base;
    }

    stageDisplayStatus(stage, job, child) {
        const parentStatus = job.status || '';
        const childStatus = child?.status ? this.normalizeStatus(child.status) : '';
        const progress = Number(job.progress || 0);
        const step = String(job.step || '');

        if (stage === 'gemini') {
            if (job.gemini || job.structured_result || progress >= 58) return 'succeeded';
            if (this.isTerminal(parentStatus)) {
                return this.childDisplayStatus(childStatus, parentStatus, Boolean(child));
            }
            if (parentStatus === 'running' && (child || progress > 0) && (progress < 58 || step.startsWith('gemini') || step.startsWith('gem_'))) {
                return 'running';
            }
            return childStatus;
        }

        if (stage === 'doubao') {
            if (child) {
                if (this.isTerminal(parentStatus) && !this.isTerminal(childStatus)) {
                    return this.childDisplayStatus(childStatus, parentStatus, true);
                }
                return childStatus;
            }
            if (this.isTerminal(parentStatus)) {
                return this.childDisplayStatus('', parentStatus, false);
            }
            if (parentStatus === 'running' && (step.startsWith('doubao') || progress >= 60)) {
                return 'running';
            }
        }

        if (stage === 'publisher') {
            if (job.video?.enabled === false) return 'skipped';
            if (job.video?.video) return 'succeeded';
            if (child) return childStatus;
            if (this.isTerminal(parentStatus)) {
                return this.childDisplayStatus('', parentStatus, false);
            }
            if (parentStatus === 'running' && (step.startsWith('publisher') || step.startsWith('pub_') || progress >= 94)) {
                return 'running';
            }
        }

        return childStatus;
    }

    childDisplayStatus(childStatus, parentStatus, hasChild) {
        if (!hasChild) {
            if (['succeeded', 'failed'].includes(parentStatus)) return 'skipped';
            if (parentStatus === 'canceled') return 'canceled';
            return '';
        }
        if (this.isTerminal(parentStatus) && !this.isTerminal(childStatus)) {
            return parentStatus === 'failed' ? 'interrupted' : 'canceled';
        }
        return childStatus;
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
            skipped: { label: '未执行', className: 'bg-slate-100 text-slate-500', dot: 'bg-slate-300', bar: 'bg-slate-400' },
            interrupted: { label: '已中止', className: 'bg-slate-100 text-slate-600', dot: 'bg-slate-400', bar: 'bg-slate-500' },
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

    formatDateTime(value) {
        if (!value) return '';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '';
        const parts = new Intl.DateTimeFormat('zh-CN', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
        }).formatToParts(date);
        const pick = (type) => parts.find((part) => part.type === type)?.value || '';
        return `${pick('month')}-${pick('day')} ${pick('hour')}:${pick('minute')}`;
    }

    videoSummary(video) {
        if (!video) return '横屏视频等待生成';
        if (video.enabled === false) return video.message || '横屏视频已跳过';
        if (video.video?.bytes) return `横屏视频已生成 · ${this.formatBytes(video.video.bytes)}`;
        if (video.publisher_task_id) return `横屏视频任务 ${video.publisher_task_id}`;
        return '横屏视频处理中';
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
