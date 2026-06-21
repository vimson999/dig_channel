class ReportAudioWorkflowPage {
    constructor() {
        this.jobs = [];
        this.pdfAssets = [];
        this.pdfAssetPage = 1;
        this.pdfAssetPerPage = 10;
        this.pdfAssetPagination = { page: 1, per_page: 10, has_more: false };
        this.pdfAssetScope = 'all';
        this.pdfAssetSearchTimer = null;
        this.pdfAssetRequestSeq = 0;
        this.pdfAssetWorkflowSeq = 0;
        this.selectedPdfAsset = null;
        this.jobPage = this.initialJobPage();
        this.jobStatus = this.initialJobStatus();
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
            pdfAssetTabs: document.getElementById('pdf-asset-tabs'),
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
            jobStatusFilter: document.getElementById('job-status-filter'),
            jobList: document.getElementById('job-list'),
            jobPagination: document.getElementById('job-pagination'),
        };
        this.ensureDynamicElements();
        this.defaults();
        this.bind();
        this.syncJobStatusFilter();
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
        if (!document.getElementById('child-detail-modal')) {
            document.body.insertAdjacentHTML('beforeend', `
                <div id="child-detail-modal" class="fixed inset-0 z-50 hidden bg-slate-950/45 p-4">
                    <div class="mx-auto flex h-full max-w-5xl items-center justify-center">
                        <div class="max-h-[92vh] w-full overflow-hidden rounded-lg bg-white shadow-2xl">
                            <div id="child-detail-modal-content"></div>
                        </div>
                    </div>
                </div>
            `);
        }
        this.els.childDetailModal = document.getElementById('child-detail-modal');
        this.els.childDetailModalContent = document.getElementById('child-detail-modal-content');
    }

    defaults() {
        this.els.promptInput.value = '请解析上传的研报 PDF，提取核心观点、关键数据、风险提示和可用于口播的摘要。';
        this.els.reviewPromptInput.value = '担任首席责任主编，利用提供的PDF材料和可查网络信息，对上文内容逐条复核与勘误，确认事实准确、逻辑清晰、表达规范，列出每条修正点及依据，并给出最终优化后的信息版本，附上核对过程说明和参考来源。';
    }

    initialJobPage() {
        const page = Number(new URLSearchParams(window.location.search).get('job_page') || 1);
        return Number.isFinite(page) && page > 0 ? Math.floor(page) : 1;
    }

    initialJobStatus() {
        const params = new URLSearchParams(window.location.search);
        return this.normalizeJobFilterStatus(params.get('status') || params.get('job_status') || '');
    }

    jobStatusOptions() {
        return [
            { value: '', label: '全部' },
            { value: 'queued', label: '排队中' },
            { value: 'running', label: '运行中' },
            { value: 'succeeded', label: '成功' },
            { value: 'failed', label: '失败' },
            { value: 'canceled', label: '已取消' },
        ];
    }

    normalizeJobFilterStatus(status) {
        const value = this.normalizeStatus(String(status || '').trim().toLowerCase());
        const allowed = new Set(this.jobStatusOptions().map((item) => item.value));
        return allowed.has(value) ? value : '';
    }

    jobStatusLabel(status = this.jobStatus) {
        return this.jobStatusOptions().find((item) => item.value === status)?.label || '全部';
    }

    syncJobStatusFilter() {
        if (!this.els.jobStatusFilter) return;
        const nextValue = this.normalizeJobFilterStatus(this.jobStatus);
        if (this.els.jobStatusFilter.value !== nextValue) {
            this.els.jobStatusFilter.value = nextValue;
        }
    }

    bind() {
        this.els.refreshStatusBtn.addEventListener('click', () => this.refreshStatus());
        this.els.restartWorkerBtn.addEventListener('click', () => this.restartWorker());
        this.els.pdfSourceUpload.addEventListener('change', () => this.updatePdfSourceUi());
        this.els.pdfSourceAsset.addEventListener('change', () => this.updatePdfSourceUi());
        this.els.pdfAssetRefreshBtn.addEventListener('click', () => this.loadPdfAssets({ page: this.pdfAssetPage }));
        this.els.pdfAssetTabs?.addEventListener('click', (event) => this.handlePdfAssetScopeClick(event));
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
        this.els.pdfAssetPagination.addEventListener('submit', (event) => this.handlePdfAssetPageJump(event));
        this.els.submitBtn.addEventListener('click', () => this.submit());
        this.els.loadHistoryBtn.addEventListener('click', () => this.loadHistory({ page: 1, force: true }));
        this.els.refreshJobsBtn.addEventListener('click', () => this.refreshJobs());
        this.els.pollDelay.addEventListener('change', () => this.resetPollTimer());
        this.els.jobStatusFilter?.addEventListener('change', () => this.handleJobStatusFilterChange());
        this.els.jobList.addEventListener('click', (event) => this.handleJobAction(event));
        this.els.jobList.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            if (!event.target.closest('[data-child-detail]')) return;
            event.preventDefault();
            this.handleJobAction(event);
        });
        this.els.jobPagination.addEventListener('click', (event) => this.handleJobPageClick(event));
        this.els.jobPagination.addEventListener('submit', (event) => this.handleJobPageJump(event));
        this.els.childDetailModal.addEventListener('click', (event) => {
            if (event.target === this.els.childDetailModal || event.target.closest('[data-child-detail-close]')) {
                this.closeChildDetailModal();
            }
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') this.closeChildDetailModal();
        });
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
        this.renderPdfAssetTabs();
        if (showLoading) {
            this.els.pdfAssetRefreshBtn.disabled = true;
            this.els.pdfAssetRefreshBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>加载';
        }
        const q = this.els.pdfAssetSearch.value.trim();
        try {
            const query = new URLSearchParams({
                page: String(page),
                per_page: String(this.pdfAssetPerPage),
                scope: this.pdfAssetScope,
            });
            if (q) query.set('q', q);
            const result = await this.request(`/api/report-audio-workflows/pdf-assets?${query.toString()}`, { timeoutMs: 45000 });
            if (requestSeq !== this.pdfAssetRequestSeq) return;
            this.pdfAssets = (result.data?.items || result.items || []).map((item) => ({
                ...item,
                workflow_loading: true,
            }));
            this.pdfAssetPagination = result.data?.pagination || {
                page,
                per_page: this.pdfAssetPerPage,
                has_more: false,
                scope: this.pdfAssetScope,
            };
            this.pdfAssetPage = Number(this.pdfAssetPagination.page || page);
            if (!this.pdfAssets.length && this.pdfAssetPage > 1) {
                this.pdfAssetPage -= 1;
                return this.loadPdfAssets({ ...options, page: this.pdfAssetPage });
            }
            this.renderPdfAssets();
            this.renderPdfAssetPagination();
            this.loadPdfAssetWorkflowStats(this.pdfAssets);
            if (!options.silent) this.setNotice(`已加载${this.pdfAssetScopeLabel()}第 ${this.pdfAssetPage} 页，共 ${this.pdfAssets.length} 个 PDF。`, 'success');
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

    handlePdfAssetScopeClick(event) {
        const button = event.target.closest('[data-pdf-asset-scope]');
        if (!button) return;
        const nextScope = button.dataset.pdfAssetScope === 'hot' ? 'hot' : 'all';
        if (nextScope === this.pdfAssetScope) return;
        this.pdfAssetScope = nextScope;
        this.pdfAssetPage = 1;
        this.selectedPdfAsset = null;
        this.loadPdfAssets({ page: 1 });
    }

    pdfAssetScopeLabel() {
        return this.pdfAssetScope === 'hot' ? '热门 PDF ' : '已同步 PDF ';
    }

    renderPdfAssetTabs() {
        if (!this.els.pdfAssetTabs) return;
        this.els.pdfAssetTabs.querySelectorAll('[data-pdf-asset-scope]').forEach((button) => {
            const active = button.dataset.pdfAssetScope === this.pdfAssetScope;
            button.className = active
                ? 'rounded-md border border-slate-900 bg-slate-900 px-3 py-2 text-white shadow-sm transition'
                : 'rounded-md border border-slate-200 bg-white px-3 py-2 text-slate-600 transition hover:bg-slate-50';
        });
    }

    async loadPdfAssetWorkflowStats(items) {
        const ids = (items || []).map((item) => item.id).filter(Boolean);
        if (!ids.length) return;
        const requestSeq = ++this.pdfAssetWorkflowSeq;
        const query = new URLSearchParams({ ids: ids.join(',') });
        try {
            const result = await this.request(`/api/report-audio-workflows/pdf-assets/workflow-stats?${query.toString()}`, { timeoutMs: 30000 });
            if (requestSeq !== this.pdfAssetWorkflowSeq) return;
            const stats = result.data?.stats || result.stats || {};
            this.pdfAssets = this.pdfAssets.map((item) => ({
                ...item,
                workflow_loading: false,
                workflow_load_error: false,
                workflow: stats[item.id] || item.workflow || null,
            }));
            this.renderPdfAssets();
        } catch (error) {
            if (requestSeq !== this.pdfAssetWorkflowSeq) return;
            this.pdfAssets = this.pdfAssets.map((item) => ({
                ...item,
                workflow_loading: false,
                workflow_load_error: true,
            }));
            this.renderPdfAssets();
        }
    }

    schedulePdfAssetSearch() {
        if (this.pdfAssetSearchTimer) window.clearTimeout(this.pdfAssetSearchTimer);
        this.pdfAssetSearchTimer = window.setTimeout(() => {
            this.loadPdfAssets({ page: 1, silent: true });
        }, 350);
    }

    renderPdfAssets() {
        this.renderPdfAssetTabs();
        const selectedId = this.selectedPdfAsset?.id || '';
        this.els.pdfAssetSummary.textContent = this.pdfAssets.length
            ? `${this.pdfAssetScopeLabel()}· 第 ${this.pdfAssetPage} 页 · 本页 ${this.pdfAssets.length} 个 PDF${this.selectedPdfAsset ? ` · 已选 ${this.selectedPdfAsset.filename}` : ''}`
            : (this.pdfAssetScope === 'hot' ? '没有找到下载次数达到 30 次的热门 PDF。' : '没有找到已下载的同步 PDF。');
        if (!this.pdfAssets.length) {
            const emptyText = this.pdfAssetScope === 'hot'
                ? '暂无热门 PDF。当前筛选条件是下载次数 ≥ 30，且附件已下载到本地。'
                : '暂无可选 PDF。请先完成知识星球同步并下载附件。';
            this.els.pdfAssetList.innerHTML = `<div class="rounded-md border border-dashed border-slate-300 bg-white px-3 py-6 text-center text-xs text-slate-500">${this.escape(emptyText)}</div>`;
            this.renderPdfAssetPagination();
            return;
        }
        this.els.pdfAssetList.innerHTML = this.pdfAssets.map((item) => {
            const active = item.id === selectedId;
            const previewPath = item.preview_url || `/api/report-audio-workflows/pdf-assets/${encodeURIComponent(item.id)}/preview`;
            const previewUrl = `${this.apiRoot()}${previewPath}`;
            const itemTitle = [item.filename, item.topic_title, item.tab_label].filter(Boolean).join('\n');
            const downloadText = Number.isFinite(Number(item.download_count))
                ? `下载 ${Number(item.download_count)} 次`
                : '下载 -';
            const workflowMeta = this.pdfAssetWorkflowMeta(item);
            const syncTime = this.formatDateTime(item.topic_last_synced_at || item.updated_at);
            const topicTime = this.formatDateTime(item.topic_create_time) || item.topic_create_time || '';
            const metaLine = [
                item.tab_label || '',
                syncTime ? `同步 ${syncTime}` : '',
                topicTime ? `发布 ${topicTime}` : '',
            ].filter(Boolean).join(' · ');
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
                    <div class="mt-1 truncate text-[11px] text-slate-400">${this.escape(metaLine)}</div>
                    <div class="mt-2 flex flex-wrap items-center gap-1.5">
                        <span class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-bold text-slate-500">
                            <i class="fas fa-download mr-1"></i>${this.escape(downloadText)}
                        </span>
                        <span class="rounded-full px-2 py-0.5 text-[11px] font-bold ${workflowMeta.className}">
                            ${workflowMeta.latestMeta ? `<span class="mr-1 inline-block h-1.5 w-1.5 rounded-full ${workflowMeta.latestMeta.dot}"></span>` : ''}
                            ${this.escape(workflowMeta.text)}
                        </span>
                        ${workflowMeta.latestMeta ? `<span class="rounded-full px-2 py-0.5 text-[11px] font-bold ${workflowMeta.latestMeta.className}">最近 ${this.escape(workflowMeta.latestMeta.label)}</span>` : ''}
                        ${workflowMeta.detail ? `<span class="text-[11px] font-bold text-slate-400">${this.escape(workflowMeta.detail)}</span>` : ''}
                    </div>
                </div>
            `;
        }).join('');
    }

    renderPdfAssetPagination() {
        const page = Math.max(1, Number(this.pdfAssetPagination?.page || this.pdfAssetPage || 1));
        const hasMore = Boolean(this.pdfAssetPagination?.has_more);
        const hasPrevious = page > 1;
        this.els.pdfAssetPagination.innerHTML = `
            <div class="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                <button type="button" data-pdf-asset-page="prev" class="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 font-bold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40" ${hasPrevious ? '' : 'disabled'}>
                    <i class="fas fa-chevron-left mr-1"></i>上一页
                </button>
                <div class="flex shrink-0 items-center gap-2">
                    <span class="font-bold text-slate-500">第 ${page} 页${hasMore ? ' · 还有更多' : ''}</span>
                    <form class="flex items-center gap-1" data-pdf-asset-page-jump>
                        <input name="pdf_asset_page" type="number" min="1" value="${page}" class="w-16 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-xs font-bold text-slate-700 focus:border-slate-500 focus:outline-none">
                        <button type="submit" class="rounded-md bg-slate-900 px-2.5 py-1.5 font-bold text-white hover:bg-slate-700">跳页</button>
                    </form>
                </div>
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

    handlePdfAssetPageJump(event) {
        event.preventDefault();
        const input = event.target.querySelector('input[name="pdf_asset_page"]');
        const page = Number(input?.value || 1);
        if (Number.isFinite(page) && page > 0) {
            this.loadPdfAssets({ page: Math.floor(page) });
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
        this.jobStatus = this.normalizeJobFilterStatus(this.jobStatus);
        this.syncJobStatusFilter();
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
            if (this.jobStatus) query.set('status', this.jobStatus);
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
            this.jobs = items.map((item) => this.mergeJobSummary(item));
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

    handleJobStatusFilterChange() {
        const nextStatus = this.normalizeJobFilterStatus(this.els.jobStatusFilter?.value || '');
        if (nextStatus === this.jobStatus) return;
        this.jobStatus = nextStatus;
        this.jobPage = 1;
        this.updateJobPageUrl(1);
        this.loadHistory({ page: 1, force: true });
    }

    mergeJobSummary(incoming) {
        const key = this.jobIdentity(incoming);
        const existing = key ? this.jobs.find((item) => this.jobIdentity(item) === key) : null;
        if (!existing) return incoming;

        const merged = { ...existing, ...incoming };
        [
            'logs',
            'children',
            'gemini',
            'doubao_browser_tts',
            'publisher_video',
            'structured_result',
            'tts_text',
            'request',
            'pdf',
            'audio',
            'video',
            'metadata',
            'notifications',
        ].forEach((field) => {
            merged[field] = this.mergeDetailValue(existing[field], incoming[field]);
        });
        return merged;
    }

    jobIdentity(job) {
        return String(job?.job_id || job?.local_id || '').trim();
    }

    mergeDetailValue(existing, incoming) {
        if (this.isEmptyDetailValue(incoming)) return existing;
        if (this.isEmptyDetailValue(existing)) return incoming;
        if (Array.isArray(existing) && Array.isArray(incoming)) {
            return this.mergeDetailArray(existing, incoming);
        }
        if (this.isPlainObject(existing) && this.isPlainObject(incoming)) {
            return this.mergeDetailObject(existing, incoming);
        }
        return incoming;
    }

    mergeDetailObject(existing, incoming) {
        const merged = { ...existing };
        Object.entries(incoming || {}).forEach(([key, value]) => {
            if (this.isEmptyDetailValue(value)) {
                if (!(key in merged)) merged[key] = value;
                return;
            }
            merged[key] = this.mergeDetailValue(merged[key], value);
        });
        return merged;
    }

    mergeDetailArray(existing, incoming) {
        if (!incoming.length) return existing;
        if (!existing.length) return incoming;

        const existingByKey = new Map();
        existing.forEach((item) => {
            const key = this.detailArrayItemKey(item);
            if (key) existingByKey.set(key, item);
        });

        const usedKeys = new Set();
        const merged = incoming.map((item) => {
            const key = this.detailArrayItemKey(item);
            if (!key || !existingByKey.has(key)) return item;
            usedKeys.add(key);
            return this.mergeDetailValue(existingByKey.get(key), item);
        });

        existing.forEach((item) => {
            const key = this.detailArrayItemKey(item);
            if (key && usedKeys.has(key)) return;
            merged.push(item);
        });
        return merged;
    }

    detailArrayItemKey(item) {
        if (!this.isPlainObject(item)) return '';
        return String(item.job_id || item.id || item.celery_id || '').trim();
    }

    isEmptyDetailValue(value) {
        if (value === null || value === undefined || value === '') return true;
        if (Array.isArray(value)) return value.length === 0;
        if (this.isPlainObject(value)) return Object.keys(value).length === 0;
        return false;
    }

    isPlainObject(value) {
        return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
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
        const childCard = event.target.closest('[data-child-detail]');
        if (childCard) {
            event.preventDefault();
            event.stopPropagation();
            this.openChildDetail({
                jobId: childCard.dataset.jobId,
                kind: childCard.dataset.childKind,
                childId: childCard.dataset.childId,
                chunkIndex: childCard.dataset.chunkIndex,
            });
            return;
        }

        const button = event.target.closest('[data-job-action]');
        if (!button) return;
        const jobId = button.dataset.jobId;
        const action = button.dataset.jobAction;
        if (action === 'copy-title') {
            event.preventDefault();
            event.stopPropagation();
            this.copyText(button.dataset.copyText || '', '标题已复制。');
            return;
        }
        if (action === 'refresh') this.refreshJob(jobId);
        if (action === 'retry') this.retryJob(jobId);
        if (action === 'remove') {
            this.jobs = this.jobs.filter((job) => (job.job_id || job.local_id) !== jobId);
            this.render();
        }
    }

    async copyText(text, successMessage = '已复制。') {
        const value = String(text || '').trim();
        if (!value) {
            this.setNotice('没有可复制的内容。', 'error');
            return;
        }
        try {
            if (navigator.clipboard?.writeText) {
                await navigator.clipboard.writeText(value);
            } else {
                const textarea = document.createElement('textarea');
                textarea.value = value;
                textarea.setAttribute('readonly', '');
                textarea.style.position = 'fixed';
                textarea.style.left = '-9999px';
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand('copy');
                textarea.remove();
            }
            this.setNotice(successMessage, 'success');
        } catch (error) {
            this.setNotice(`复制失败：${error.message || error}`, 'error');
        }
    }

    async retryJob(jobId) {
        if (!jobId) return;
        if (!window.confirm('从失败处续跑这个编排？已完成的 Gemini 和豆包浏览器朗读分段会尽量复用。')) return;
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

    handleJobPageJump(event) {
        const form = event.target.closest('[data-job-page-jump-form]');
        if (!form) return;
        event.preventDefault();

        const input = form.querySelector('[name="job_page_jump"]');
        const pages = Math.max(1, Number(this.jobPagination?.pages || 1));
        const parsed = Number(input?.value || 0);
        if (!Number.isFinite(parsed) || parsed < 1) {
            this.setNotice('请输入有效页码。', 'error');
            input?.focus();
            return;
        }

        const targetPage = Math.min(pages, Math.max(1, Math.floor(parsed)));
        if (input) input.value = String(targetPage);
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
        this.syncJobStatusFilter();
        const total = this.jobs.length;
        const running = this.jobs.filter((job) => ['queued', 'running', 'submitting'].includes(job.status)).length;
        const succeeded = this.jobs.filter((job) => job.status === 'succeeded').length;
        const failed = this.jobs.filter((job) => job.status === 'failed').length;
        const historyTotal = Number(this.jobPagination?.total || 0);
        const pageCount = Number(this.jobPagination?.pages || 0);
        const statusLabel = this.jobStatusLabel(this.jobStatus);
        this.els.summaryText.textContent = total
            ? `${statusLabel} ${historyTotal || total} 条 · 第 ${this.jobPage}/${Math.max(1, pageCount || 1)} 页，本页 ${total} 条，运行中 ${running}，成功 ${succeeded}，失败 ${failed}。`
            : (this.jobStatus ? `暂无${statusLabel}状态的编排任务。` : '等待投递。');

        if (!this.jobs.length) {
            const emptyText = this.jobStatus ? `暂无${statusLabel}状态的编排任务。` : '暂无编排任务。';
            this.els.jobList.innerHTML = `<div class="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-12 text-center text-sm text-slate-500">${this.escape(emptyText)}</div>`;
            this.renderJobPagination();
            return;
        }
        this.els.jobList.innerHTML = this.jobs.map((job, index) => this.renderJob(job, index)).join('');
        this.renderJobPagination();
    }

    pdfAssetWorkflowMeta(item) {
        if (item.workflow_loading) {
            return {
                latestMeta: null,
                text: '编排状态加载中',
                detail: '',
                className: 'bg-indigo-50 text-indigo-600',
            };
        }
        if (item.workflow_load_error) {
            return {
                latestMeta: null,
                text: '编排状态待刷新',
                detail: '',
                className: 'bg-amber-50 text-amber-700',
            };
        }
        const workflow = item.workflow || {};
        const total = Number(workflow.total || 0);
        const succeeded = Number(workflow.succeeded || 0);
        const failed = Number(workflow.failed || 0);
        const running = Number(workflow.running || 0);
        const latest = workflow.latest || null;
        const latestMeta = this.statusMeta(latest?.status || '');

        if (!total) {
            return {
                latestMeta: null,
                text: '未编排',
                detail: '',
                className: 'bg-slate-100 text-slate-500',
            };
        }

        const detail = [
            succeeded ? `成功 ${succeeded}` : '',
            failed ? `失败 ${failed}` : '',
            running ? `运行 ${running}` : '',
        ].filter(Boolean).join(' · ');
        return {
            latestMeta,
            text: `编排 ${total} 次`,
            detail,
            className: latestMeta.className,
        };
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
                <div class="flex flex-wrap items-center justify-center gap-2">
                    <span class="font-bold text-slate-500">第 ${page}/${pages} 页 · 共 ${total} 条</span>
                    <form data-job-page-jump-form class="flex items-center gap-1.5">
                        <label class="sr-only" for="job-page-jump">跳转页码</label>
                        <input id="job-page-jump" name="job_page_jump" type="number" min="1" max="${pages}" value="${page}" class="h-8 w-20 rounded-md border border-slate-200 bg-white px-2 text-center text-xs font-bold text-slate-700 focus:border-slate-500 focus:outline-none">
                        <button type="submit" class="h-8 rounded-md border border-slate-200 bg-white px-2.5 text-xs font-bold text-slate-600 hover:bg-slate-50">
                            跳转
                        </button>
                    </form>
                </div>
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
        this.applyJobStatusToUrl(url);
        return `${url.pathname}${url.search}${url.hash}`;
    }

    updateJobPageUrl(page) {
        if (!window.history?.replaceState) return;
        const url = new URL(window.location.href);
        url.searchParams.set('job_page', String(Math.max(1, page)));
        this.applyJobStatusToUrl(url);
        window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    }

    applyJobStatusToUrl(url) {
        const status = this.normalizeJobFilterStatus(this.jobStatus);
        if (status) {
            url.searchParams.set('status', status);
        } else {
            url.searchParams.delete('status');
        }
        url.searchParams.delete('job_status');
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
        const video = job.video || null;
        const videoUrl = job.download_video_url
            ? `${this.apiRoot()}${job.download_video_url}`
            : (video?.video?.download_url ? `${this.apiRoot()}${video.video.download_url}` : '');
        const verticalVideoUrl = job.download_vertical_video_url
            ? `${this.apiRoot()}${job.download_vertical_video_url}`
            : (video?.vertical_video?.download_url ? `${this.apiRoot()}${video.vertical_video.download_url}` : '');
        const showLandscapeVideoUrl = Boolean(videoUrl && video?.landscape_enabled !== false);
        const coverUrl = job.download_cover_url ? `${this.apiRoot()}${job.download_cover_url}` : '';
        const pdf = job.pdf || job.request?.pdf || null;
        const parentStatus = job.status || '';
        const geminiChild = job.gemini || job.children?.gemini;
        const doubaoItems = job.doubao_browser_tts || [];
        const doubaoDisplayItems = this.doubaoDisplayItems(doubaoItems);
        const publisherChild = job.publisher_video || job.children?.publisher_video;
        const retryState = job.retry_state || {};
        const childAttempts = job.child_attempts || {};
        return `
            <article class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="status-dot ${status.dot}"></span>
                            <h3 class="truncate text-sm font-black" title="${this.escape(displayTitle)}">${this.escape(displayTitle)}</h3>
                            <button type="button" data-job-action="copy-title" data-copy-text="${this.escape(displayTitle)}" class="shrink-0 rounded-md border border-slate-200 bg-white px-1.5 py-1 text-[11px] text-slate-500 hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700" title="复制标题">
                                <i class="fas fa-copy"></i>
                            </button>
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
                    ${this.renderChild('Gemini', geminiChild, parentStatus, this.stageDisplayStatus('gemini', job, geminiChild), this.childDetailRef(job, 'gemini', geminiChild), this.retryInfo(retryState.gemini, childAttempts.gemini))}
                    ${doubaoDisplayItems.length ? doubaoDisplayItems.map((item, idx) => this.renderChild(this.doubaoChildLabel(item, idx, doubaoDisplayItems), item, parentStatus, this.stageDisplayStatus('doubao', job, item), this.childDetailRef(job, 'doubao', item, idx))).join('') : this.renderChild('豆包浏览器朗读', null, parentStatus, this.stageDisplayStatus('doubao', job, null))}
                    ${this.renderChild('视频合成', publisherChild, parentStatus, this.stageDisplayStatus('publisher', job, publisherChild), this.childDetailRef(job, 'publisher', publisherChild))}
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
                                ${showLandscapeVideoUrl ? `<a href="${videoUrl}" download class="rounded-md bg-indigo-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-indigo-800"><i class="fas fa-film mr-1"></i>横屏 MP4</a>` : ''}
                                ${verticalVideoUrl ? `<a href="${verticalVideoUrl}" download class="rounded-md bg-fuchsia-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-fuchsia-800"><i class="fas fa-mobile-screen mr-1"></i>竖屏 MP4</a>` : ''}
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

    async openChildDetail(ref = {}) {
        if (!ref.jobId) return;
        this.showChildDetailLoading();
        await this.refreshJob(ref.jobId);

        const job = this.jobs.find((item) => item.job_id === ref.jobId);
        const resolved = this.resolveChildDetail(job, ref);
        if (!resolved?.child) {
            this.closeChildDetailModal();
            this.setNotice('没有找到这个子任务详情。', 'error');
            return;
        }

        let child = resolved.child;
        let detailError = '';
        if (ref.childId) {
            try {
                const result = await this.request(`/api/report-audio-workflows/jobs/${encodeURIComponent(ref.jobId)}/children/${encodeURIComponent(ref.childId)}`, { timeoutMs: 15000 });
                child = this.mergeDetailValue(child, result.data || result);
            } catch (error) {
                detailError = error.message;
            }
        }
        this.renderChildDetailModal({ ...resolved, child, detailError });
    }

    resolveChildDetail(job, ref = {}) {
        if (!job) return null;
        const kind = ref.kind || '';
        if (kind === 'gemini') {
            return {
                job,
                kind,
                label: 'Gemini',
                child: this.withRetryAttempts(job.gemini || job.children?.gemini, job.child_attempts?.gemini),
            };
        }
        if (kind === 'publisher') {
            return { job, kind, label: '视频合成', child: job.publisher_video || job.children?.publisher_video };
        }
        if (kind === 'doubao') {
            const items = this.doubaoDisplayItems(job.doubao_browser_tts || []);
            const targetChunk = Number(ref.chunkIndex || 0);
            const index = items.findIndex((item) => {
                const metadata = item?.metadata || {};
                const chunkIndex = Number(metadata.chunk_index || 0);
                if (targetChunk && chunkIndex === targetChunk) return true;
                if (item?.job_id && item.job_id === ref.childId) return true;
                return Array.isArray(item?.attempts) && item.attempts.some((attempt) => attempt?.job_id === ref.childId);
            });
            const item = index >= 0 ? items[index] : null;
            return {
                job,
                kind,
                label: item ? this.doubaoChildLabel(item, index, items) : '豆包浏览器朗读',
                child: item,
            };
        }
        return null;
    }

    withRetryAttempts(child, attempts = []) {
        if (!Array.isArray(attempts) || !attempts.length) return child;
        const base = child ? { ...child } : { job_id: attempts[attempts.length - 1]?.job_id || '' };
        base.attempts = attempts;
        return base;
    }

    showChildDetailLoading() {
        this.els.childDetailModalContent.innerHTML = `
            <div class="p-6 text-sm text-slate-500">
                <i class="fas fa-spinner fa-spin mr-2"></i>正在读取子任务详情...
            </div>
        `;
        this.els.childDetailModal.classList.remove('hidden');
        document.body.classList.add('overflow-hidden');
    }

    closeChildDetailModal() {
        if (!this.els.childDetailModal) return;
        this.els.childDetailModal.classList.add('hidden');
        document.body.classList.remove('overflow-hidden');
    }

    renderChildDetailModal({ job, label, child, detailError }) {
        const status = this.statusMeta(this.normalizeStatus(child?.status || ''));
        const metadata = child?.metadata || {};
        const attempts = Array.isArray(child?.attempts) ? child.attempts : [];
        const rawParams = child?.raw_params || null;
        const rawResult = child?.raw_result || null;
        const audio = child?.audio || {};
        const audioUrl = audio.download_url ? `${this.apiRoot()}${audio.download_url}` : '';
        const attemptLabel = metadata.chunk_attempt
            ? `第 ${metadata.chunk_attempt} 次`
            : (child?.attempt ? `第 ${child.attempt}${child.max_attempts ? `/${child.max_attempts}` : ''} 次` : (attempts.length ? `${attempts.length} 次记录` : '-'));
        this.els.childDetailModalContent.innerHTML = `
            <div class="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
                <div class="min-w-0">
                    <div class="flex flex-wrap items-center gap-2">
                        <h3 class="text-base font-black text-slate-900">${this.escape(label || '子任务详情')}</h3>
                        <span class="rounded-full px-2.5 py-1 text-xs font-bold ${status.className}">${status.label}</span>
                    </div>
                    <p class="mt-1 truncate text-xs text-slate-500">${this.escape(job?.title || job?.job_id || '-')}</p>
                </div>
                <button type="button" data-child-detail-close class="rounded-md border border-slate-200 px-2.5 py-1.5 text-xs font-bold text-slate-500 hover:bg-slate-50">
                    <i class="fas fa-xmark mr-1"></i>关闭
                </button>
            </div>
            <div class="max-h-[76vh] overflow-auto p-5">
                ${detailError ? `<div class="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-700">读取完整子任务失败：${this.escape(detailError)}</div>` : ''}
                <div class="grid gap-2 text-xs md:grid-cols-4">
                    ${this.renderDetailStat('任务 ID', child?.job_id || '-')}
                    ${this.renderDetailStat('Celery ID', child?.celery_id || '-')}
                    ${this.renderDetailStat('步骤', child?.step || '-')}
                    ${this.renderDetailStat('进度', `${Number(child?.progress || 0)}%`)}
                    ${this.renderDetailStat('创建', this.formatDateTime(child?.created_at) || '-')}
                    ${this.renderDetailStat('完成', this.formatDateTime(child?.completed_at) || '-')}
                    ${this.renderDetailStat('分段', metadata.chunk_index ? `${metadata.chunk_index}/${metadata.chunk_total || '-'}` : '-')}
                    ${this.renderDetailStat('尝试', attemptLabel)}
                </div>

                ${child?.error ? `<div class="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700">${this.escape(child.error)}</div>` : ''}

                ${this.renderAttemptRows(attempts)}

                ${audio && (audio.seconds || audio.bytes || audioUrl) ? `
                    <div class="mt-4 rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-sm text-blue-800">
                        <div class="flex flex-wrap items-center justify-between gap-2">
                            <span>音频 ${audio.seconds ? `${Math.round(Number(audio.seconds))}s` : '-'} · ${this.formatBytes(audio.bytes)}</span>
                            ${audioUrl ? `<a href="${this.escape(audioUrl)}" download class="rounded-md bg-blue-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-800"><i class="fas fa-download mr-1"></i>下载</a>` : ''}
                        </div>
                    </div>
                ` : ''}

                ${child?.text_preview ? `
                    <div class="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3">
                        <div class="mb-2 text-xs font-black text-slate-600">文本预览</div>
                        <div class="whitespace-pre-wrap text-sm leading-6 text-slate-700">${this.escape(child.text_preview)}</div>
                    </div>
                ` : ''}

                ${this.renderJsonDetails('Metadata', metadata)}
                ${this.renderJsonDetails('Raw Params', rawParams)}
                ${this.renderJsonDetails('Raw Result', rawResult)}
            </div>
        `;
        this.els.childDetailModal.classList.remove('hidden');
        document.body.classList.add('overflow-hidden');
    }

    renderDetailStat(label, value) {
        return `
            <div class="rounded-md bg-slate-50 px-3 py-2">
                <div class="text-slate-400">${this.escape(label)}</div>
                <div class="mt-1 truncate font-black text-slate-700" title="${this.escape(value)}">${this.escape(value)}</div>
            </div>
        `;
    }

    renderAttemptRows(attempts = []) {
        if (!Array.isArray(attempts) || attempts.length <= 1) return '';
        return `
            <div class="mt-4 rounded-md border border-slate-200">
                <div class="border-b border-slate-100 px-3 py-2 text-xs font-black text-slate-600">重试记录</div>
                <div class="divide-y divide-slate-100">
                    ${attempts.map((attempt, index) => {
                        const metadata = attempt?.metadata || {};
                        const status = this.statusMeta(this.normalizeStatus(attempt?.status || ''));
                        const attemptNo = metadata.chunk_attempt || attempt?.attempt || index + 1;
                        const maxAttempts = attempt?.max_attempts || '';
                        const retryIndex = Number(metadata.retry_index || Math.max(0, Number(attempt?.attempt || 1) - 1));
                        const detail = this.childDetail(attempt);
                        return `
                            <div class="px-3 py-2 text-xs">
                                <div class="flex flex-wrap items-center justify-between gap-2">
                                    <div class="font-black text-slate-700">第 ${this.escape(attemptNo)}${maxAttempts ? `/${this.escape(maxAttempts)}` : ''} 次${retryIndex > 0 ? ` · 重试 ${this.escape(retryIndex)}` : ''}</div>
                                    <span class="rounded-full px-2 py-0.5 font-bold ${status.className}">${status.label}</span>
                                </div>
                                <div class="mt-1 truncate font-mono text-[11px] text-slate-400">${this.escape(attempt?.job_id || '-')}</div>
                                ${detail ? `<div class="mt-1 text-slate-500">${this.escape(detail)}</div>` : ''}
                                ${attempt?.error ? `<div class="mt-1 rounded border border-red-100 bg-red-50 px-2 py-1 leading-5 text-red-700">${this.escape(attempt.error)}</div>` : ''}
                            </div>
                        `;
                    }).join('')}
                </div>
            </div>
        `;
    }

    renderJsonDetails(label, value) {
        if (this.isEmptyDetailValue(value)) return '';
        return `
            <details class="mt-4 rounded-md border border-slate-200 bg-white">
                <summary class="cursor-pointer px-3 py-2 text-xs font-black text-slate-600">${this.escape(label)}</summary>
                <pre class="max-h-72 overflow-auto border-t border-slate-100 bg-slate-950 p-3 text-xs leading-5 text-slate-100">${this.escape(this.stringifyJson(value))}</pre>
            </details>
        `;
    }

    stringifyJson(value) {
        try {
            return JSON.stringify(value, null, 2);
        } catch (error) {
            return String(value ?? '');
        }
    }

    compactTitle(value) {
        return String(value ?? '')
            .replace(/\s*\n\s*/g, ' / ')
            .replace(/\s+/g, ' ')
            .trim();
    }

    childDetailRef(job, kind, child, index = 0) {
        if (!job?.job_id || !child?.job_id) return null;
        const metadata = child.metadata || {};
        return {
            jobId: job.job_id,
            kind,
            childId: child.job_id,
            chunkIndex: metadata.chunk_index || index + 1,
        };
    }

    renderChild(label, child, parentStatus = '', statusOverride = '', detailRef = null, retryInfo = null) {
        const childStatus = child?.status ? this.normalizeStatus(child.status) : '';
        const meta = this.statusMeta(statusOverride || this.childDisplayStatus(childStatus, parentStatus, Boolean(child)));
        const detail = this.childDetail(child);
        const preview = this.compactTitle(child?.text_preview || '');
        const retry = this.renderRetryInfo(retryInfo);
        const clickable = Boolean(detailRef?.jobId && detailRef?.childId);
        const detailAttrs = clickable
            ? `role="button" tabindex="0" data-child-detail="1" data-job-id="${this.escape(detailRef.jobId)}" data-child-kind="${this.escape(detailRef.kind)}" data-child-id="${this.escape(detailRef.childId)}" data-chunk-index="${this.escape(detailRef.chunkIndex || '')}" title="查看子任务详情"`
            : '';
        return `
            <div ${detailAttrs} class="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs ${clickable ? 'cursor-pointer transition hover:border-indigo-200 hover:bg-white focus:border-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-100' : ''}">
                <div class="flex items-center justify-between gap-2">
                    <span class="font-bold text-slate-600">${this.escape(label)}</span>
                    <span class="${meta.className} rounded-full px-2 py-0.5 font-bold">${meta.label}</span>
                </div>
                <div class="mt-1 truncate text-slate-400">${this.escape(child?.job_id || '-')}</div>
                ${detail ? `<div class="mt-1 truncate text-slate-500">${this.escape(detail)}</div>` : ''}
                ${retry}
                ${preview ? `<div class="mt-1 text-slate-500" title="${this.escape(preview)}" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;">${this.escape(preview)}</div>` : ''}
            </div>
        `;
    }

    retryInfo(state = null, attempts = []) {
        const list = Array.isArray(attempts) ? attempts : [];
        const latest = state && typeof state === 'object' ? state : {};
        const lastAttempt = list[list.length - 1] || {};
        const maxAttempts = Number(latest.max_attempts || lastAttempt.max_attempts || 0);
        const currentAttempt = Number(latest.attempt || lastAttempt.attempt || list.length || 0);
        if (!maxAttempts && !list.length) return null;
        const failedAttempts = list.filter((attempt) => String(attempt?.status || '').toLowerCase() === 'failed').length;
        const status = String(latest.status || lastAttempt.status || '').toLowerCase();
        const error = latest.error || lastAttempt.error || '';
        return {
            currentAttempt,
            maxAttempts,
            failedAttempts,
            status,
            error,
        };
    }

    renderRetryInfo(info) {
        if (!info) return '';
        const current = info.currentAttempt || 0;
        const max = info.maxAttempts || '?';
        const failed = Number(info.failedAttempts || 0);
        const status = String(info.status || '');
        const tone = status === 'completed'
            ? 'text-emerald-700'
            : (failed > 0 || status === 'failed' ? 'text-amber-700' : 'text-slate-500');
        const parts = [`自动重试 ${current || 1}/${max}`];
        if (failed > 0) parts.push(`失败尝试 ${failed}`);
        if (status === 'completed' && failed > 0) parts.push('已恢复');
        if (status === 'failed') parts.push('待续跑');
        const text = parts.join(' · ');
        const title = info.error ? `${text}：${this.compactTitle(info.error)}` : text;
        return `<div class="mt-1 truncate text-[11px] font-bold ${tone}" title="${this.escape(title)}"><i class="fas fa-rotate mr-1"></i>${this.escape(text)}</div>`;
    }

    childDetail(child) {
        if (!child) return '';
        const parts = [];
        const step = String(child.step || '').trim();
        if (step && step !== 'done' && step !== 'queued') parts.push(step);
        const progress = Number(child.progress || 0);
        if (progress > 0 && progress < 100) parts.push(`${progress}%`);
        const audio = child.audio || {};
        const seconds = Number(audio.seconds || 0);
        const bytes = Number(audio.bytes || 0);
        if (seconds || bytes) {
            const audioParts = [];
            if (seconds) audioParts.push(`${Math.round(seconds)}s`);
            if (bytes) audioParts.push(this.formatBytes(bytes));
            parts.push(`音频 ${audioParts.join(' / ')}`);
        }
        const turnCount = Number(child.turn_count || 0);
        if (turnCount > 1) parts.push(`${turnCount} 轮`);
        const retryCount = Number(child.retry_count || 0);
        const failedAttempts = Number(child.failed_attempts || 0);
        if (retryCount > 0) parts.push(`重试 ${retryCount} 次`);
        if (failedAttempts > 0) parts.push(`失败尝试 ${failedAttempts}`);
        return parts.join(' · ');
    }

    doubaoDisplayItems(items = []) {
        if (!Array.isArray(items) || !items.length) return [];
        const groups = new Map();
        items.forEach((item, index) => {
            const metadata = item?.metadata || {};
            const rawChunk = Number(metadata.chunk_index);
            const chunkIndex = Number.isFinite(rawChunk) && rawChunk > 0 ? rawChunk : 0;
            const key = chunkIndex ? `chunk:${chunkIndex}` : `item:${index}`;
            if (!groups.has(key)) {
                groups.set(key, {
                    key,
                    chunkIndex: chunkIndex || index + 1,
                    firstIndex: index,
                    attempts: [],
                    chunkTotal: 0,
                });
            }
            const group = groups.get(key);
            group.attempts.push({ ...item, _attempt_order: index });
            const rawTotal = Number(metadata.chunk_total);
            if (Number.isFinite(rawTotal) && rawTotal > 0) group.chunkTotal = rawTotal;
        });

        return Array.from(groups.values())
            .sort((a, b) => (a.chunkIndex - b.chunkIndex) || (a.firstIndex - b.firstIndex))
            .map((group) => {
                const attempts = group.attempts.slice().sort((a, b) => {
                    const attemptA = Number(a?.metadata?.chunk_attempt || a?.metadata?.retry_index || 0);
                    const attemptB = Number(b?.metadata?.chunk_attempt || b?.metadata?.retry_index || 0);
                    return (attemptA - attemptB) || ((a._attempt_order || 0) - (b._attempt_order || 0));
                });
                const display = this.bestDoubaoAttempt(attempts);
                const failedAttempts = attempts.filter((attempt) => this.normalizeStatus(attempt?.status || '') === 'failed').length;
                const retryCount = Math.max(
                    0,
                    attempts.length - 1,
                    Number(display?.metadata?.retry_index || 0),
                    Number(display?.metadata?.chunk_attempt || 1) - 1,
                );
                return {
                    ...display,
                    metadata: {
                        ...(display?.metadata || {}),
                        chunk_index: group.chunkIndex,
                        chunk_total: group.chunkTotal || display?.metadata?.chunk_total,
                    },
                    attempts,
                    failed_attempts: failedAttempts,
                    retry_count: retryCount,
                };
            });
    }

    bestDoubaoAttempt(attempts = []) {
        const reversed = attempts.slice().reverse();
        return (
            reversed.find((item) => this.normalizeStatus(item?.status || '') === 'succeeded')
            || reversed.find((item) => ['running', 'queued'].includes(this.normalizeStatus(item?.status || '')))
            || reversed[0]
            || null
        );
    }

    doubaoChildLabel(item, index, items = []) {
        const metadata = item?.metadata || {};
        const rawChunk = Number(metadata.chunk_index);
        const chunkIndex = Number.isFinite(rawChunk) && rawChunk > 0 ? rawChunk : index + 1;
        const rawTotal = Number(metadata.chunk_total);
        const chunkTotal = Number.isFinite(rawTotal) && rawTotal > 0 ? rawTotal : 0;
        const base = `豆包朗读 ${chunkIndex}${chunkTotal ? `/${chunkTotal}` : ''}`;
        const retryCount = Number(item?.retry_count || metadata.retry_index || 0);
        const status = this.normalizeStatus(item?.status || '');
        if (retryCount > 0 && status === 'succeeded') return `${base} · 重试成功`;
        if (retryCount > 0) return `${base} · 重试 ${retryCount} 次`;
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
            if (job.audio || job.download_audio_url) return 'succeeded';
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
        const value = String(status || '').trim().toLowerCase();
        const map = { pending: 'queued', processing: 'running', completed: 'succeeded' };
        const normalized = map[value] || value;
        if (normalized === 'cancelled') return 'canceled';
        return normalized;
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
        if (!video) return '视频等待生成';
        if (video.enabled === false) return video.message || '视频已跳过';

        const parts = [];
        const onlyVertical = video.vertical_enabled && video.landscape_enabled === false;
        if (video.video?.bytes && !onlyVertical) {
            parts.push(`横屏 ${this.formatBytes(video.video.bytes)}`);
        }
        if (video.vertical_video?.bytes) {
            parts.push(`竖屏 ${this.formatBytes(video.vertical_video.bytes)}`);
        }
        if (video.video?.bytes && onlyVertical && !video.vertical_video?.bytes) {
            parts.push(`竖屏 ${this.formatBytes(video.video.bytes)}`);
        }
        if (parts.length) return `视频已生成 · ${parts.join(' / ')}`;

        if (video.publisher_task_id) return `视频任务 ${video.publisher_task_id}`;
        if (video.landscape_enabled || video.vertical_enabled) return '视频处理中';
        return '视频等待生成';
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
