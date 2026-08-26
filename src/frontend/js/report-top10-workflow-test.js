class ReportTop10WorkflowPage {
    constructor() {
        this.assets = [];
        this.selected = [];
        this.jobs = [];
        this.assetPage = 1;
        this.assetPerPage = 10;
        this.assetPagination = { page: 1, per_page: 10, has_more: false };
        this.assetScope = 'all';
        this.assetRequestSeq = 0;
        this.jobPage = this.initialJobPage();
        this.jobPerPage = 10;
        this.jobStatus = this.initialJobStatus();
        this.jobSearch = this.initialJobSearch();
        this.jobPagination = { page: 1, pages: 0, total: 0 };
        this.defaultReportCount = 10;
        this.minReportCount = 1;
        this.maxReportCount = 20;
        this.reportCountDirty = false;
        this.selectedJobId = '';
        this.selectedJobDetail = null;
        this.selectedJobLoading = false;
        this.selectedJobError = '';
        this.selectedSourceReportIndex = 0;
        this.assetSearchTimer = null;
        this.jobSearchTimer = null;
        this.pollTimer = null;
        this.els = {
            providerStatus: document.getElementById('provider-status'),
            refreshStatusBtn: document.getElementById('refresh-status-btn'),
            notice: document.getElementById('notice'),
            selectedCount: document.getElementById('selected-count'),
            selectedCountInput: document.getElementById('selected-count-input'),
            assetTabs: document.getElementById('asset-tabs'),
            assetSearch: document.getElementById('asset-search'),
            assetRefreshBtn: document.getElementById('asset-refresh-btn'),
            assetSummary: document.getElementById('asset-summary'),
            assetList: document.getElementById('asset-list'),
            assetPagination: document.getElementById('asset-pagination'),
            selectedList: document.getElementById('selected-list'),
            programName: document.getElementById('program-name'),
            dateLabel: document.getElementById('date-label'),
            editorGeminiUrl: document.getElementById('editor-gemini-url'),
            reuseCache: document.getElementById('reuse-cache'),
            submitBtn: document.getElementById('submit-btn'),
            loadHistoryBtn: document.getElementById('load-history-btn'),
            refreshJobsBtn: document.getElementById('refresh-jobs-btn'),
            jobStatusFilter: document.getElementById('job-status-filter'),
            jobNameSearch: document.getElementById('job-name-search'),
            summaryText: document.getElementById('summary-text'),
            jobList: document.getElementById('job-list'),
            jobPagination: document.getElementById('job-pagination'),
            jobDetail: document.getElementById('job-detail'),
        };
        this.els.dateLabel.value = new Date().toISOString().slice(0, 10);
        if (this.els.selectedCountInput) {
            this.els.selectedCountInput.value = String(this.initialReportCount());
        }
        this.bind();
        this.refreshStatus();
        this.loadAssets({ silent: true });
        this.loadHistory({ silent: true });
        this.resetPollTimer();
        this.render();
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
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
        };
    }

    initialJobPage() {
        const value = Number(new URLSearchParams(window.location.search).get('job_page') || 1);
        return Number.isFinite(value) && value > 0 ? Math.floor(value) : 1;
    }

    initialJobStatus() {
        const params = new URLSearchParams(window.location.search);
        return this.normalizeJobFilterStatus(params.get('status') || params.get('job_status') || '');
    }

    initialJobSearch() {
        const params = new URLSearchParams(window.location.search);
        return this.normalizeJobSearch(params.get('q') || params.get('job_search') || '');
    }

    initialReportCount() {
        const params = new URLSearchParams(window.location.search);
        return this.normalizeReportCount(params.get('selected_count') || params.get('report_count') || params.get('n') || this.defaultReportCount);
    }

    normalizeReportCount(value) {
        const parsed = Number(value);
        const fallback = Number(this.defaultReportCount || 10);
        const raw = Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
        const min = Number.isFinite(Number(this.minReportCount)) ? Number(this.minReportCount) : 1;
        const max = Number.isFinite(Number(this.maxReportCount)) ? Number(this.maxReportCount) : 20;
        return Math.min(max, Math.max(min, Math.floor(raw)));
    }

    targetReportCount() {
        return this.normalizeReportCount(this.els.selectedCountInput?.value || this.defaultReportCount);
    }

    jobTargetReportCount(job) {
        return this.normalizeReportCount(job?.target_report_count || job?.pdfs?.length || this.defaultReportCount);
    }

    handleReportCountChange() {
        this.reportCountDirty = true;
        const target = this.targetReportCount();
        if (this.els.selectedCountInput) this.els.selectedCountInput.value = String(target);
        if (this.selected.length > target) {
            this.selected = this.selected.slice(0, target);
            this.setNotice(`本期最多篇数已调整为 ${target}，超出的 PDF 已从已选列表移除。`, 'info');
        }
        this.renderSelected();
        this.renderAssets();
    }

    async request(path, options = {}) {
        const { timeoutMs = 15000, ...fetchOptions } = options;
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(`${this.apiRoot()}${path}`, {
                ...fetchOptions,
                headers: {
                    ...this.headers(!(fetchOptions.body instanceof FormData)),
                    ...(fetchOptions.headers || {}),
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

    bind() {
        this.els.refreshStatusBtn.addEventListener('click', () => this.refreshStatus());
        this.els.assetRefreshBtn.addEventListener('click', () => this.loadAssets({ page: this.assetPage }));
        this.els.assetTabs?.addEventListener('click', (event) => this.handleAssetScopeClick(event));
        this.els.assetSearch.addEventListener('input', () => this.scheduleAssetSearch());
        this.els.assetSearch.addEventListener('search', () => this.scheduleAssetSearch());
        this.els.assetSearch.addEventListener('change', () => this.scheduleAssetSearch());
        this.els.assetSearch.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') this.loadAssets({ page: 1 });
        });
        this.els.assetList.addEventListener('click', (event) => this.handleAssetClick(event));
        this.els.assetList.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            const card = event.target.closest('[data-asset-id]');
            if (!card) return;
            event.preventDefault();
            this.handleAssetClick(event);
        });
        this.els.assetPagination.addEventListener('click', (event) => this.handleAssetPageClick(event));
        this.els.assetPagination.addEventListener('submit', (event) => this.handleAssetPageJump(event));
        this.els.selectedList.addEventListener('click', (event) => this.handleSelectedClick(event));
        this.els.selectedCountInput?.addEventListener('change', () => this.handleReportCountChange());
        this.els.selectedCountInput?.addEventListener('input', () => this.handleReportCountChange());
        this.els.submitBtn.addEventListener('click', () => this.submit());
        this.els.loadHistoryBtn.addEventListener('click', () => this.loadHistory({ page: 1, force: true }));
        this.els.refreshJobsBtn?.addEventListener('click', () => this.loadHistory({ page: this.jobPage, force: true }));
        this.els.jobStatusFilter.addEventListener('change', () => this.handleJobStatusFilterChange());
        this.els.jobNameSearch?.addEventListener('input', () => this.scheduleJobSearch());
        this.els.jobNameSearch?.addEventListener('search', () => this.scheduleJobSearch());
        this.els.jobNameSearch?.addEventListener('change', () => this.scheduleJobSearch());
        this.els.jobNameSearch?.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') this.applyJobSearch({ force: true });
        });
        this.els.jobList.addEventListener('click', (event) => this.handleJobClick(event));
        this.els.jobPagination.addEventListener('click', (event) => this.handleJobPageClick(event));
        this.els.jobPagination.addEventListener('submit', (event) => this.handleJobPageJump(event));
        this.els.jobDetail?.addEventListener('click', (event) => this.handleDetailClick(event));
    }

    async refreshStatus() {
        try {
            const result = await this.request('/api/report-top10-workflows/status', { timeoutMs: 10000 });
            const provider = result.data?.provider || {};
            const gemini = result.data?.report_pdf_gemini || {};
            this.defaultReportCount = this.normalizeReportCount(provider.default_report_count || this.defaultReportCount);
            this.minReportCount = Number(provider.min_report_count || this.minReportCount || 1);
            this.maxReportCount = Number(provider.max_report_count || this.maxReportCount || 20);
            if (this.els.selectedCountInput) {
                this.els.selectedCountInput.min = String(this.minReportCount);
                this.els.selectedCountInput.max = String(this.maxReportCount);
                if (!this.reportCountDirty) {
                    this.els.selectedCountInput.value = String(this.defaultReportCount);
                    this.renderSelected();
                    this.renderAssets();
                }
            }
            const ok = provider.configured && gemini.configured;
            this.els.providerStatus.className = `rounded-lg border px-3 py-2 text-xs font-bold ${ok ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`;
            this.els.providerStatus.textContent = ok ? '严选 / Gemini 已配置' : 'Gemini 浏览器未配置';
        } catch (error) {
            this.els.providerStatus.className = 'rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-bold text-red-700';
            this.els.providerStatus.textContent = `状态失败：${error.message}`;
        }
    }

    scheduleAssetSearch() {
        clearTimeout(this.assetSearchTimer);
        this.assetSearchTimer = setTimeout(() => this.loadAssets({ page: 1, silent: true }), 350);
    }

    async loadAssets(options = {}) {
        const page = Math.max(1, Number(options.page || this.assetPage || 1));
        const requestSeq = ++this.assetRequestSeq;
        this.assetPage = page;
        this.renderAssetTabs();
        if (!options.silent) {
            this.els.assetSummary.textContent = `加载${this.assetScopeLabel()}中...`;
            this.els.assetRefreshBtn.disabled = true;
            this.els.assetRefreshBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>加载';
        }
        try {
            const query = new URLSearchParams({
                page: String(page),
                per_page: String(this.assetPerPage),
                scope: this.assetScope,
            });
            const q = this.els.assetSearch.value.trim();
            if (q) query.set('q', q);
            const result = await this.request(`/api/report-top10-workflows/pdf-assets?${query.toString()}`, { timeoutMs: 30000 });
            if (requestSeq !== this.assetRequestSeq) return;
            this.assets = result.data?.items || result.items || [];
            this.assetPagination = result.data?.pagination || { page, per_page: this.assetPerPage, has_more: false, scope: this.assetScope };
            this.assetPage = Number(this.assetPagination.page || page);
            if (!this.assets.length && this.assetPage > 1) {
                this.assetPage -= 1;
                return this.loadAssets({ ...options, page: this.assetPage });
            }
            this.renderAssets();
        } catch (error) {
            if (requestSeq !== this.assetRequestSeq) return;
            this.els.assetSummary.textContent = `加载已清洗 PDF 失败：${error.message}`;
            this.els.assetList.innerHTML = '';
            this.els.assetPagination.innerHTML = '';
            if (!options.silent) this.setNotice(`加载 PDF 失败：${error.message}`, 'error');
        } finally {
            if (requestSeq !== this.assetRequestSeq) return;
            this.els.assetRefreshBtn.disabled = false;
            this.els.assetRefreshBtn.innerHTML = '<i class="fas fa-rotate mr-1"></i>刷新';
        }
    }

    handleAssetClick(event) {
        if (event.target.closest('[data-asset-preview-link]')) return;
        const button = event.target.closest('[data-asset-id]');
        if (!button) return;
        const id = button.dataset.assetId;
        const asset = this.assets.find((item) => item.id === id);
        if (!asset) return;
        if (this.selected.some((item) => item.id === id)) {
            this.setNotice('这份 PDF 已经选过。', 'error');
            return;
        }
        const target = this.targetReportCount();
        if (this.selected.length >= target) {
            this.setNotice(`本期最多选择 ${target} 份 PDF。`, 'error');
            return;
        }
        this.selected.push(asset);
        this.renderSelected();
        this.renderAssets();
    }

    handleSelectedClick(event) {
        const button = event.target.closest('[data-remove-id]');
        if (!button) return;
        this.selected = this.selected.filter((item) => item.id !== button.dataset.removeId);
        this.renderSelected();
        this.renderAssets();
    }

    handleAssetPageClick(event) {
        const button = event.target.closest('[data-asset-page]');
        if (!button || button.disabled) return;
        event.preventDefault();
        const direction = button.dataset.assetPage;
        if (direction === 'prev' && this.assetPage > 1) {
            this.loadAssets({ page: this.assetPage - 1 });
        }
        if (direction === 'next' && this.assetPagination?.has_more) {
            this.loadAssets({ page: this.assetPage + 1 });
        }
    }

    handleAssetPageJump(event) {
        const form = event.target.closest('[data-asset-page-jump]');
        if (!form) return;
        event.preventDefault();
        const input = form.querySelector('[name="asset_page_jump"]');
        const parsed = Number(input?.value || 1);
        if (Number.isFinite(parsed) && parsed > 0) this.loadAssets({ page: Math.floor(parsed) });
    }

    handleAssetScopeClick(event) {
        const button = event.target.closest('[data-asset-scope]');
        if (!button) return;
        const nextScope = button.dataset.assetScope === 'hot' ? 'hot' : 'all';
        if (nextScope === this.assetScope) return;
        this.assetScope = nextScope;
        this.assetPage = 1;
        this.loadAssets({ page: 1 });
    }

    assetScopeLabel() {
        return this.assetScope === 'hot' ? '热门已清洗 PDF ' : '已清洗 PDF ';
    }

    renderAssetTabs() {
        if (!this.els.assetTabs) return;
        this.els.assetTabs.querySelectorAll('[data-asset-scope]').forEach((button) => {
            const active = button.dataset.assetScope === this.assetScope;
            button.className = active
                ? 'rounded-md border border-slate-900 bg-slate-900 px-3 py-2 text-white shadow-sm transition'
                : 'rounded-md border border-slate-200 bg-white px-3 py-2 text-slate-600 transition hover:bg-slate-50';
        });
    }

    async submit() {
        const selectedCount = this.selected.length;
        const maxCount = this.targetReportCount();
        if (!selectedCount) {
            this.setNotice('请至少选择 1 份 PDF。', 'error');
            return;
        }
        if (selectedCount > maxCount) {
            this.setNotice(`本期最多 ${maxCount} 份 PDF，当前 ${selectedCount} 份。`, 'error');
            return;
        }
        this.els.submitBtn.disabled = true;
        this.els.submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>投递中';
        try {
            const result = await this.request('/api/report-top10-workflows/jobs', {
                method: 'POST',
                timeoutMs: 60000,
                body: JSON.stringify({
                    pdf_asset_ids: this.selected.map((item) => item.id),
                    selected_count: selectedCount,
                    program_name: this.els.programName.value.trim() || '今日研报严选',
                    date_label: this.els.dateLabel.value.trim(),
                    editor_gemini_url: this.els.editorGeminiUrl.value.trim(),
                    reuse_existing_single_reports: this.els.reuseCache.checked,
                    metadata: { source: 'report-top10-workflow-test-page' },
                }),
            });
            this.setNotice('严选节目包编排任务已投递。', 'success');
            this.selected = [];
            this.renderSelected();
            await this.loadHistory({ page: 1, force: true });
            const jobId = result.job_id || result.data?.job_id;
            if (jobId) this.openJob(jobId);
        } catch (error) {
            this.setNotice(`投递失败：${error.message}`, 'error');
        } finally {
            this.els.submitBtn.disabled = false;
            this.els.submitBtn.innerHTML = '<i class="fas fa-paper-plane mr-1.5"></i>开始编排';
        }
    }

    async loadHistory(options = {}) {
        const page = Math.max(1, Number(options.page || this.jobPage || 1));
        this.jobPage = page;
        this.jobStatus = this.normalizeJobFilterStatus(this.jobStatus);
        this.jobSearch = this.normalizeJobSearch(this.jobSearch);
        this.syncJobStatusFilter();
        this.syncJobSearchInput();
        if (!options.silent) {
            this.els.loadHistoryBtn.disabled = true;
            this.els.loadHistoryBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>加载中';
        }
        try {
            const query = new URLSearchParams({ page: String(page), per_page: String(this.jobPerPage) });
            if (this.jobStatus) query.set('status', this.jobStatus);
            if (this.jobSearch) query.set('q', this.jobSearch);
            const result = await this.request(`/api/report-top10-workflows/jobs?${query.toString()}`, { timeoutMs: 15000 });
            this.jobs = result.data?.items || result.items || [];
            this.jobPagination = result.data?.pagination || { page, pages: 0, total: this.jobs.length };
            this.jobPage = Number(this.jobPagination.page || page);
            if (!this.jobs.length && this.jobPage > 1) {
                this.jobPage -= 1;
                return this.loadHistory({ ...options, page: this.jobPage, force: true });
            }
            this.renderJobs();
            this.updateSummary();
        } catch (error) {
            if (!options.silent) this.setNotice(`加载历史失败：${error.message}`, 'error');
        } finally {
            this.els.loadHistoryBtn.disabled = false;
            this.els.loadHistoryBtn.innerHTML = '<i class="fas fa-clock-rotate-left mr-1.5"></i>加载历史';
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

    scheduleJobSearch() {
        if (this.jobSearchTimer) window.clearTimeout(this.jobSearchTimer);
        this.jobSearchTimer = window.setTimeout(() => this.applyJobSearch(), 350);
    }

    applyJobSearch(options = {}) {
        if (this.jobSearchTimer) {
            window.clearTimeout(this.jobSearchTimer);
            this.jobSearchTimer = null;
        }
        const nextSearch = this.normalizeJobSearch(this.els.jobNameSearch?.value || '');
        if (!options.force && nextSearch === this.jobSearch) return;
        this.jobSearch = nextSearch;
        this.jobPage = 1;
        this.updateJobPageUrl(1);
        this.loadHistory({ page: 1, force: true });
    }

    handleJobClick(event) {
        const copyButton = event.target.closest('[data-copy-target]');
        if (copyButton) {
            const target = document.getElementById(copyButton.dataset.copyTarget);
            this.copyText(target?.textContent || '', '内容已复制。');
            return;
        }
        const actionButton = event.target.closest('[data-job-action]');
        if (actionButton) {
            const id = actionButton.dataset.jobId;
            if (actionButton.dataset.jobAction === 'retry') this.retryJob(id);
            if (actionButton.dataset.jobAction === 'cancel') this.cancelJob(id);
            if (actionButton.dataset.jobAction === 'create-video-task') this.createVideoTask(id, actionButton);
            if (actionButton.dataset.jobAction === 'open-video-task') this.openVideoFactory(actionButton.dataset.videoTaskId);
            return;
        }
        const sourceReportButton = event.target.closest('[data-source-report-index]');
        if (sourceReportButton) {
            event.preventDefault();
            event.stopPropagation();
            this.selectedSourceReportIndex = Math.max(0, Number(sourceReportButton.dataset.sourceReportIndex || 0));
            this.renderJobs();
            return;
        }
        const card = event.target.closest('[data-job-id]');
        if (card) this.openJob(card.dataset.jobId);
    }

    handleJobPageClick(event) {
        const button = event.target.closest('[data-job-page]');
        if (!button || button.disabled) return;
        event.preventDefault();
        const direction = button.dataset.jobPage;
        let targetPage = this.jobPage;
        if (direction === 'prev' && this.jobPage > 1) targetPage = this.jobPage - 1;
        if (direction === 'next' && this.jobPage < Number(this.jobPagination?.pages || 0)) targetPage = this.jobPage + 1;
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

    async openJob(jobId, options = {}) {
        const silent = Boolean(options.silent);
        const sameJob = this.selectedJobId === jobId;
        const hasCurrentDetail = this.selectedJobDetail?.job_id === jobId;
        this.selectedJobId = jobId;
        if (!sameJob) this.selectedSourceReportIndex = 0;
        if (!silent || !hasCurrentDetail) {
            this.selectedJobDetail = hasCurrentDetail ? this.selectedJobDetail : null;
            this.selectedJobError = '';
            this.selectedJobLoading = true;
            this.renderJobs();
        }
        try {
            const result = await this.request(`/api/report-top10-workflows/jobs/${encodeURIComponent(jobId)}`, { timeoutMs: 45000 });
            const job = result.data || result;
            const index = this.jobs.findIndex((item) => item.job_id === jobId);
            if (index >= 0) this.jobs[index] = { ...this.jobs[index], ...job };
            this.selectedJobDetail = job;
            this.selectedJobLoading = false;
            const sourceReports = Array.isArray(job.source_reports) ? job.source_reports : [];
            this.selectedSourceReportIndex = Math.max(0, Math.min(this.selectedSourceReportIndex, Math.max(0, sourceReports.length - 1)));
            this.renderJobs();
        } catch (error) {
            this.selectedJobLoading = false;
            this.selectedJobError = error.message;
            this.renderJobs();
        }
    }

    async retryJob(jobId) {
        if (!window.confirm('确认从失败处续跑？已完成的单篇文案和总编辑结果会优先复用。')) return;
        const job = this.jobs.find((item) => item.job_id === jobId);
        if (job) {
            job.status = 'running';
            job.step = 'queued';
            job.error = null;
            this.renderJobs();
        }
        try {
            await this.request(`/api/report-top10-workflows/jobs/${encodeURIComponent(jobId)}/retry`, { method: 'POST', timeoutMs: 20000 });
            this.setNotice('任务已从失败处续跑。', 'success');
            this.loadHistory({ page: this.jobPage, force: true });
            this.openJob(jobId);
        } catch (error) {
            if (this.isTimeoutError(error)) {
                const confirmed = await this.confirmRetryAfterTimeout(jobId);
                if (confirmed) return;
            }
            if (job) {
                job.status = 'failed';
                job.error = error.message;
                this.renderJobs();
            }
            this.setNotice(`续跑失败：${error.message}`, 'error');
        }
    }

    async confirmRetryAfterTimeout(jobId) {
        try {
            const result = await this.request(`/api/report-top10-workflows/jobs/${encodeURIComponent(jobId)}`, { timeoutMs: 12000 });
            const job = result.data || result;
            const index = this.jobs.findIndex((item) => item.job_id === jobId);
            if (index >= 0) this.jobs[index] = { ...this.jobs[index], ...job };
            if (this.selectedJobId === jobId) {
                this.selectedJobDetail = job;
                this.selectedJobLoading = false;
                this.selectedJobError = '';
            }
            this.renderJobs();
            if (['queued', 'running'].includes(job.status)) {
                this.setNotice('续跑请求已提交，接口确认稍慢；任务已进入运行队列。', 'success');
                this.loadHistory({ page: this.jobPage, force: true });
                return true;
            }
            return false;
        } catch (_confirmError) {
            return false;
        }
    }

    isTimeoutError(error) {
        return /请求超时|timeout|timed out/i.test(String(error?.message || error || ''));
    }

    async cancelJob(jobId) {
        if (!window.confirm('确认取消这个严选编排任务？')) return;
        try {
            await this.request(`/api/report-top10-workflows/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST', timeoutMs: 20000 });
            this.setNotice('任务已取消。', 'success');
            this.loadHistory({ page: this.jobPage, force: true });
        } catch (error) {
            this.setNotice(`取消失败：${error.message}`, 'error');
        }
    }

    async createVideoTask(jobId, button) {
        const originalHtml = button?.innerHTML || '';
        if (button) {
            button.disabled = true;
            button.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>创建中';
        }
        try {
            const result = await this.request(`/api/report-top10-workflows/jobs/${encodeURIComponent(jobId)}/create-video-task`, {
                method: 'POST',
                timeoutMs: 60000,
                body: JSON.stringify({ output_ratios: ['16:9', '9:16'] }),
            });
            const taskId = result.data?.report_video_task_id;
            if (!taskId) throw new Error('接口没有返回视频工厂任务 ID');
            this.setNotice('节目包已送入视频工厂，正在打开制作台。', 'success');
            this.openVideoFactory(taskId);
        } catch (error) {
            if (button) {
                button.disabled = false;
                button.innerHTML = originalHtml;
            }
            this.setNotice(`创建视频工厂任务失败：${error.message}`, 'error');
        }
    }

    openVideoFactory(taskId) {
        if (!taskId) return;
        window.location.href = `report-video-factory.html?task_id=${encodeURIComponent(taskId)}`;
    }

    handleDetailClick(event) {
        const button = event.target.closest('[data-copy-target]');
        if (!button) return;
        const target = document.getElementById(button.dataset.copyTarget);
        this.copyText(target?.textContent || '', '内容已复制。');
    }

    async copyText(text, message) {
        if (!text.trim()) return;
        try {
            await navigator.clipboard.writeText(text);
        } catch (_error) {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            textarea.remove();
        }
        this.setNotice(message, 'success');
    }

    resetPollTimer() {
        clearInterval(this.pollTimer);
        this.pollTimer = setInterval(() => {
            if (this.jobs.some((job) => ['queued', 'running'].includes(job.status))) {
                this.loadHistory({ page: this.jobPage, silent: true });
                if (this.selectedJobId) this.openJob(this.selectedJobId, { silent: true });
            }
        }, 8000);
    }

    render() {
        this.syncJobStatusFilter();
        this.syncJobSearchInput();
        this.renderAssets();
        this.renderSelected();
        this.renderJobs();
        this.updateSummary();
    }

    renderAssets() {
        this.renderAssetTabs();
        const selectedIds = new Set(this.selected.map((item) => item.id));
        const page = this.assetPagination.page || this.assetPage;
        const scanned = this.assetPagination.scanned || 0;
        const skipped = this.assetPagination.skipped_missing || 0;
        const target = this.targetReportCount();
        this.els.assetSummary.textContent = this.assets.length
            ? `${this.assetScopeLabel()}· 第 ${page} 页 · 本页 ${this.assets.length} 个 PDF · 本期 N=${this.selected.length} / 上限 ${target} · 扫描 ${scanned} 条 · 跳过缺失 ${skipped} 条`
            : (this.assetScope === 'hot' ? '没有找到下载次数达到 30 次的热门已清洗 PDF。' : '没有找到已清洗 PDF。');
        if (!this.assets.length) {
            const emptyText = this.assetScope === 'hot'
                ? '暂无热门已清洗 PDF。当前筛选条件是下载次数 ≥ 30，且已有可用清洗产物。'
                : '暂无可选 PDF。请先完成清洗后再选择。';
            this.els.assetList.innerHTML = `<div class="rounded-md border border-dashed border-slate-300 bg-white px-3 py-6 text-center text-xs text-slate-500">${this.escape(emptyText)}</div>`;
        } else {
            this.els.assetList.innerHTML = this.assets.map((item) => {
                const selected = selectedIds.has(item.id);
                const selectedIndex = this.selected.findIndex((selectedItem) => selectedItem.id === item.id);
                const previewPath = item.preview_url || `/api/report-top10-workflows/pdf-assets/${encodeURIComponent(item.id)}/preview`;
                const previewUrl = `${this.apiRoot()}${previewPath}`;
                const itemTitle = [item.filename, item.topic_title, item.tab_label].filter(Boolean).join('\n');
                const downloadText = window.DownloadCountHighlight?.badgeText(item.download_count)
                    || (Number.isFinite(Number(item.download_count)) ? `下载 ${Number(item.download_count)} 次` : '下载 -');
                const downloadBadgeClass = window.DownloadCountHighlight?.badgeClass(item.download_count)
                    || 'border-slate-200 bg-slate-100 text-slate-500';
                const syncTime = this.formatDateTime(item.topic_last_synced_at || item.updated_at);
                const topicTime = this.formatDateTime(item.topic_create_time) || item.topic_create_time || '';
                const metaLine = [
                    item.tab_label || '',
                    syncTime ? `同步 ${syncTime}` : '',
                    topicTime ? `发布 ${topicTime}` : '',
                ].filter(Boolean).join(' · ');
                return `
                    <div role="button" tabindex="0" data-asset-id="${this.escapeAttr(item.id)}" title="${this.escapeAttr(itemTitle)}" class="w-full cursor-pointer rounded-md border px-3 py-2 text-left text-xs ${selected ? 'border-indigo-300 bg-indigo-50 text-indigo-900' : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'}">
                        <div class="flex items-start justify-between gap-2">
                            <div class="min-w-0">
                                <div class="truncate font-bold">${this.escape(item.filename || 'PDF')}</div>
                                <div class="mt-1 truncate text-slate-500">${this.escape(item.topic_title || item.tab_label || item.topic_id || '-')}</div>
                            </div>
                            <div class="flex shrink-0 items-center gap-1.5">
                                <span class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-bold text-slate-500">${this.formatBytes(item.bytes)}</span>
                                ${previewUrl ? `<a data-asset-preview-link href="${this.escapeAttr(previewUrl)}" target="_blank" rel="noopener noreferrer" class="rounded-md border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-bold text-indigo-700 hover:bg-indigo-50"><i class="fas fa-eye mr-1"></i>预览</a>` : ''}
                            </div>
                        </div>
                        <div class="mt-1 truncate text-[11px] text-slate-400">${this.escape(metaLine)}</div>
                        <div class="mt-2 flex flex-wrap items-center gap-1.5">
                            <span class="rounded-full bg-violet-50 px-2 py-0.5 text-[11px] font-bold text-violet-700">${this.escape(item.source_label || item.tab_label || '清洗产物')} · 已清洗</span>
                            <span class="rounded-full border px-2 py-0.5 text-[11px] font-bold ${downloadBadgeClass}">
                                <i class="fas fa-download mr-1"></i>${this.escape(downloadText)}
                            </span>
                            <span class="rounded-full px-2 py-0.5 text-[11px] font-bold ${item.single_script_cached ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}">
                                ${item.single_script_cached ? '已有文案缓存' : '需解析'}
                            </span>
                            ${selected ? `<span class="rounded-full bg-indigo-100 px-2 py-0.5 text-[11px] font-bold text-indigo-700">已选 R${String(selectedIndex + 1).padStart(2, '0')}</span>` : ''}
                        </div>
                    </div>
                `;
            }).join('');
        }
        this.renderAssetPagination();
    }

    renderAssetPagination() {
        const page = Math.max(1, Number(this.assetPagination?.page || this.assetPage || 1));
        const hasPrevious = page > 1;
        const hasMore = Boolean(this.assetPagination?.has_more);
        this.els.assetPagination.innerHTML = `
            <div class="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                <button type="button" data-asset-page="prev" class="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 font-bold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40" ${hasPrevious ? '' : 'disabled'}>
                    <i class="fas fa-chevron-left mr-1"></i>上一页
                </button>
                <div class="flex items-center gap-1.5">
                    <span class="font-bold text-slate-500">第 ${page} 页${hasMore ? ' · 还有更多' : ''}</span>
                    <form class="flex items-center gap-1" data-asset-page-jump>
                        <input name="asset_page_jump" type="number" min="1" value="${page}" class="h-8 w-16 rounded-md border border-slate-200 px-2 text-center text-xs font-bold">
                        <button type="submit" class="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs font-bold text-slate-600 hover:bg-slate-50">跳</button>
                    </form>
                </div>
                <button type="button" data-asset-page="next" class="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 font-bold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40" ${hasMore ? '' : 'disabled'}>
                    下一页<i class="fas fa-chevron-right ml-1"></i>
                </button>
            </div>
        `;
    }

    renderSelected() {
        const target = this.targetReportCount();
        this.els.selectedCount.textContent = `N=${this.selected.length} / 上限${target}`;
        this.els.selectedCount.className = `rounded-md px-2.5 py-1 text-xs font-black ${this.selected.length > 0 ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-600'}`;
        if (!this.selected.length) {
            this.els.selectedList.innerHTML = '<div class="rounded-md border border-dashed border-slate-200 bg-slate-50 px-3 py-6 text-center text-xs text-slate-500">还没有选择 PDF。</div>';
            return;
        }
        this.els.selectedList.innerHTML = this.selected.map((item, index) => `
            <div class="flex items-start gap-2 rounded-md border border-slate-200 bg-white p-2">
                <span class="mt-0.5 w-7 shrink-0 rounded bg-slate-900 px-1.5 py-0.5 text-center text-[11px] font-black text-white">R${String(index + 1).padStart(2, '0')}</span>
                <div class="min-w-0 flex-1">
                    <div class="truncate text-xs font-black text-slate-800" title="${this.escape(item.filename || '')}">${this.escape(item.filename || '未命名 PDF')}</div>
                    <div class="mt-0.5 truncate text-[11px] text-slate-500">${this.escape(item.topic_title || '')}</div>
                </div>
                <button data-remove-id="${this.escape(item.id)}" class="rounded border border-slate-200 px-1.5 py-1 text-xs text-slate-500 hover:bg-slate-50" title="移除">
                    <i class="fas fa-xmark"></i>
                </button>
            </div>
        `).join('');
    }

    renderJobs() {
        if (!this.jobs.length) {
            const scopeLabel = this.jobScopeLabel();
            const emptyText = scopeLabel ? `暂无${scopeLabel}的严选编排任务。` : '暂无严选编排任务。';
            this.els.jobList.innerHTML = `<div class="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-12 text-center text-sm text-slate-500">${this.escape(emptyText)}</div>`;
        } else {
            this.els.jobList.innerHTML = this.jobs.map((job) => this.renderJobCard(job)).join('');
        }
        this.renderJobPagination();
    }

    renderJobCard(job) {
        const status = this.statusMeta(job.status);
        const active = job.job_id === this.selectedJobId;
        const detailHtml = active ? this.renderInlineJobDetail(job) : '';
        const target = this.jobTargetReportCount(job);
        return `
            <div data-job-id="${this.escapeAttr(job.job_id)}" class="cursor-pointer rounded-lg border ${active ? 'border-indigo-300 bg-indigo-50/70' : 'border-slate-200 bg-white hover:bg-slate-50'} p-4 shadow-sm">
                <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                        <div class="flex flex-wrap items-center gap-2">
                            <span class="status-dot ${status.dot}"></span>
                            <h3 class="line-clamp-2 text-base font-black text-slate-900">${this.escape(job.title || '今日研报严选')}</h3>
                        </div>
                        <div class="mt-1 text-xs text-slate-500">编排 ${this.escape(job.source_report_count || 0)} / ${target} · 创建 ${this.formatDateTime(job.created_at) || '-'} · ${this.escape(job.job_id || '')}</div>
                    </div>
                    <div class="flex shrink-0 items-center gap-2">
                        <span class="rounded-full px-2.5 py-1 text-xs font-bold ${status.className}">${status.label}</span>
                        ${job.status === 'failed' ? `<button data-job-action="retry" data-job-id="${this.escapeAttr(job.job_id)}" class="rounded-md border border-emerald-300 bg-emerald-50 px-2 py-1.5 text-xs text-emerald-700 hover:bg-emerald-100" title="失败续跑"><i class="fas fa-play"></i></button>` : ''}
                        ${['queued', 'running'].includes(job.status) ? `<button data-job-action="cancel" data-job-id="${this.escapeAttr(job.job_id)}" class="rounded-md border border-slate-200 px-2 py-1.5 text-xs text-slate-500 hover:bg-slate-50" title="取消"><i class="fas fa-ban"></i></button>` : ''}
                    </div>
                </div>
                <div class="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
                    <div class="h-full rounded-full ${status.bar}" style="width: ${Math.max(0, Math.min(100, Number(job.progress || 0)))}%"></div>
                </div>
                <div class="mt-1 flex items-center justify-between text-xs text-slate-500">
                    <span>${this.escape(job.step || '-')}</span>
                    <span>${Math.max(0, Math.min(100, Number(job.progress || 0)))}%</span>
                </div>
                ${job.error ? `<div class="mt-2 rounded bg-red-50 px-2 py-1 text-xs text-red-700">${this.escape(job.error)}</div>` : ''}
                ${detailHtml}
            </div>
        `;
    }

    renderInlineJobDetail(job) {
        if (this.selectedJobLoading) {
            return '<div class="mt-4 rounded-md border border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">正在加载任务详情...</div>';
        }
        if (this.selectedJobError) {
            return `<div class="mt-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">${this.escape(this.selectedJobError)}</div>`;
        }
        const detail = this.selectedJobDetail?.job_id === job.job_id ? this.selectedJobDetail : job;
        return `<div class="mt-4 border-t border-slate-100 pt-4">${this.renderJobDetail(detail)}</div>`;
    }

    renderJobDetail(job) {
        const sourceReports = Array.isArray(job.source_reports) ? job.source_reports : [];
        const finalScript = job.final_script_for_tts || job.editor_result?.final_script_for_tts || '';
        const editorInput = job.editor_input || '';
        const detailKey = this.safeDomId(job.job_id || 'current');
        const target = this.jobTargetReportCount(job);
        const videoTaskId = job.video_factory?.task_id || '';
        const selectedReportIndex = sourceReports.length
            ? Math.max(0, Math.min(this.selectedSourceReportIndex, sourceReports.length - 1))
            : 0;
        const selectedReport = sourceReports[selectedReportIndex] || null;
        return `
            <div class="space-y-4">
                <div class="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h3 class="text-lg font-black text-slate-900">${this.escape(job.title || '今日研报严选')}</h3>
                        <p class="mt-1 text-xs text-slate-500">${this.escape(job.job_id || '')}</p>
                    </div>
                    <div class="flex flex-wrap gap-2">
                        ${videoTaskId ? `<button type="button" data-job-action="open-video-task" data-video-task-id="${this.escapeAttr(videoTaskId)}" data-job-id="${this.escapeAttr(job.job_id)}" class="rounded-md border border-indigo-300 bg-indigo-50 px-3 py-2 text-xs font-bold text-indigo-700 hover:bg-indigo-100"><i class="fas fa-film mr-1"></i>打开视频工厂</button>` : ''}
                        ${!videoTaskId && job.status === 'succeeded' ? `<button type="button" data-job-action="create-video-task" data-job-id="${this.escapeAttr(job.job_id)}" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-700 hover:bg-emerald-100"><i class="fas fa-film mr-1"></i>制作成视频</button>` : ''}
                        ${job.download_result_url ? `<a href="${this.apiRoot()}${job.download_result_url}" class="rounded-md border border-slate-300 bg-white px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50"><i class="fas fa-download mr-1"></i>下载结果</a>` : ''}
                    </div>
                </div>

                <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
                    <div class="rounded-md border border-slate-200 bg-slate-50 p-3">
                        <div class="text-xs font-bold text-slate-500">单篇文案</div>
                        <div class="mt-1 text-xl font-black text-slate-900">${this.escape(sourceReports.length)} / ${target}</div>
                    </div>
                    <div class="rounded-md border border-slate-200 bg-slate-50 p-3">
                        <div class="text-xs font-bold text-slate-500">总编辑输入</div>
                        <div class="mt-1 text-xl font-black text-slate-900">${this.escape(job.editor_input_chars || editorInput.length || 0)}</div>
                    </div>
                    <div class="rounded-md border border-slate-200 bg-slate-50 p-3">
                        <div class="text-xs font-bold text-slate-500">视频段落</div>
                        <div class="mt-1 text-xl font-black text-slate-900">${this.escape(Array.isArray(job.video_segments) ? job.video_segments.length : 0)}</div>
                    </div>
                </div>

                <div class="grid grid-cols-1 gap-3 2xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
                    <div class="rounded-md border border-slate-200">
                        <div class="flex items-center justify-between border-b border-slate-100 px-3 py-2">
                            <h4 class="text-sm font-black text-slate-800">单篇素材来源</h4>
                            <span class="text-xs font-bold text-slate-400">共 ${this.escape(sourceReports.length)} 条 · 点击查看返回值</span>
                        </div>
                        <div class="grid grid-cols-1 gap-2 p-2 lg:grid-cols-2 2xl:grid-cols-1">
                            ${sourceReports.length ? sourceReports.map((report, index) => {
                                const active = index === selectedReportIndex;
                                return `
                                    <button type="button" data-source-report-index="${index}" class="block w-full rounded-md border px-3 py-2 text-left text-xs transition ${active ? 'border-indigo-200 bg-indigo-50 text-indigo-950' : 'border-slate-100 bg-white hover:bg-slate-50'}">
                                        <div class="font-black text-slate-800">R${String(index + 1).padStart(2, '0')} · ${this.escape(report.cover_title || report.title || report.pdf?.filename || '')}</div>
                                        <div class="mt-1 text-slate-500">${this.escape(report.source || '')} · ${this.escape(report.script_chars || 0)} 字 · ${this.escape(report.pdf?.filename || '')}</div>
                                    </button>
                                `;
                            }).join('') : '<div class="px-3 py-6 text-center text-sm text-slate-500">等待单篇文案。</div>'}
                        </div>
                    </div>
                    ${this.renderSourceReportDetail(selectedReport, selectedReportIndex, detailKey)}
                </div>

                <div class="rounded-md border border-slate-200">
                    <div class="flex items-center justify-between border-b border-slate-100 px-3 py-2">
                        <h4 class="text-sm font-black text-slate-800">最终口播稿</h4>
                        <button data-copy-target="final-script-text-${detailKey}" class="rounded border border-slate-200 px-2 py-1 text-xs font-bold text-slate-600 hover:bg-slate-50"><i class="fas fa-copy mr-1"></i>复制</button>
                    </div>
                    <pre id="final-script-text-${detailKey}" class="max-h-96 overflow-auto whitespace-pre-wrap p-3 text-sm leading-6 text-slate-800">${this.escape(finalScript || '等待总编辑 Gem 返回。')}</pre>
                </div>

                <div class="rounded-md border border-slate-200">
                    <div class="flex items-center justify-between border-b border-slate-100 px-3 py-2">
                        <h4 class="text-sm font-black text-slate-800">投递给总编辑 Gem 的输入素材</h4>
                        <button data-copy-target="editor-input-text-${detailKey}" class="rounded border border-slate-200 px-2 py-1 text-xs font-bold text-slate-600 hover:bg-slate-50"><i class="fas fa-copy mr-1"></i>复制</button>
                    </div>
                    <pre id="editor-input-text-${detailKey}" class="max-h-80 overflow-auto whitespace-pre-wrap p-3 text-xs leading-5 text-slate-700">${this.escape(editorInput || '等待组装输入素材。')}</pre>
                </div>
            </div>
        `;
    }

    renderSourceReportDetail(report, index, detailKey) {
        if (!report) {
            return `
                <div class="rounded-md border border-dashed border-slate-200 bg-slate-50 px-4 py-12 text-center text-sm text-slate-500">
                    等待单篇 Gemini 返回。
                </div>
            `;
        }
        const structured = report.structured_result && typeof report.structured_result === 'object' ? report.structured_result : {};
        const script = report.single_report_script || report.final_script_for_tts || structured.final_script_for_tts || '';
        const riskNotes = this.normalizeNotes(report.risk_notes || structured.risk_notes);
        const jsonText = this.safeJson(structured);
        const suffix = `${detailKey}-${index}`;
        const title = report.cover_title || report.title || report.pdf?.filename || `R${String(index + 1).padStart(2, '0')}`;
        return `
            <div class="rounded-md border border-slate-200 bg-white">
                <div class="flex flex-wrap items-start justify-between gap-2 border-b border-slate-100 px-3 py-2">
                    <div class="min-w-0">
                        <h4 class="text-sm font-black text-slate-800">R${String(index + 1).padStart(2, '0')} 返回值详情</h4>
                        <p class="mt-0.5 truncate text-xs text-slate-500">${this.escape(title)}</p>
                    </div>
                    <button data-copy-target="source-report-script-${suffix}" class="rounded border border-slate-200 px-2 py-1 text-xs font-bold text-slate-600 hover:bg-slate-50">
                        <i class="fas fa-copy mr-1"></i>复制口播稿
                    </button>
                </div>
                <div class="space-y-3 p-3 text-xs">
                    <div class="grid grid-cols-1 gap-2 md:grid-cols-2">
                        <div class="rounded-md bg-slate-50 p-2">
                            <div class="font-bold text-slate-500">标题</div>
                            <div class="mt-1 font-black text-slate-800">${this.escape(report.title || structured.title || '-')}</div>
                        </div>
                        <div class="rounded-md bg-slate-50 p-2">
                            <div class="font-bold text-slate-500">封面标题</div>
                            <div class="mt-1 font-black text-slate-800">${this.escape(report.cover_title || structured.cover_title || '-')}</div>
                        </div>
                        <div class="rounded-md bg-slate-50 p-2 md:col-span-2">
                            <div class="font-bold text-slate-500">封面副标题</div>
                            <div class="mt-1 text-slate-800">${this.escape(report.cover_subtitle || structured.cover_subtitle || '-')}</div>
                        </div>
                    </div>
                    <div class="rounded-md bg-slate-50 p-2">
                        <div class="font-bold text-slate-500">摘要</div>
                        <div class="mt-1 whitespace-pre-wrap leading-5 text-slate-800">${this.escape(report.summary || structured.summary || '-')}</div>
                    </div>
                    ${riskNotes.length ? `
                        <div class="rounded-md bg-amber-50 p-2">
                            <div class="font-bold text-amber-700">风险备注</div>
                            <ul class="mt-1 list-disc space-y-1 pl-4 text-amber-900">
                                ${riskNotes.map((item) => `<li>${this.escape(item)}</li>`).join('')}
                            </ul>
                        </div>
                    ` : ''}
                    <div class="rounded-md bg-slate-50 p-2">
                        <div class="font-bold text-slate-500">PDF</div>
                        <div class="mt-1 break-all text-slate-800">${this.escape(report.pdf?.filename || '-')}</div>
                        <div class="mt-1 break-all text-slate-500">${this.escape(report.pdf?.topic_title || '')}</div>
                    </div>
                    <div class="rounded-md border border-slate-200">
                        <div class="flex items-center justify-between border-b border-slate-100 px-2 py-1.5">
                            <div class="font-bold text-slate-600">单篇口播稿</div>
                            <span class="text-[11px] font-bold text-slate-400">${this.escape(script.length)} 字</span>
                        </div>
                        <pre id="source-report-script-${suffix}" class="max-h-72 overflow-auto whitespace-pre-wrap p-2 text-xs leading-5 text-slate-800">${this.escape(script || '等待返回。')}</pre>
                    </div>
                    <details class="rounded-md border border-slate-200">
                        <summary class="cursor-pointer px-2 py-1.5 font-bold text-slate-600">结构化 JSON</summary>
                        <pre class="max-h-72 overflow-auto whitespace-pre-wrap border-t border-slate-100 p-2 text-[11px] leading-5 text-slate-700">${this.escape(jsonText || '{}')}</pre>
                    </details>
                </div>
            </div>
        `;
    }

    updateSummary() {
        const running = this.jobs.filter((job) => ['queued', 'running'].includes(job.status)).length;
        const succeeded = this.jobs.filter((job) => job.status === 'succeeded').length;
        const failed = this.jobs.filter((job) => job.status === 'failed').length;
        const historyTotal = Number(this.jobPagination?.total || 0);
        const pageCount = Number(this.jobPagination?.pages || 0);
        const scopeLabel = this.jobScopeLabel();
        this.els.summaryText.textContent = this.jobs.length
            ? `${scopeLabel || '全部'} ${historyTotal || this.jobs.length} 条 · 第 ${this.jobPage}/${Math.max(1, pageCount || 1)} 页，本页 ${this.jobs.length} 条，运行中 ${running}，成功 ${succeeded}，失败 ${failed}。`
            : (scopeLabel ? `暂无${scopeLabel}的严选编排任务。` : '等待投递。');
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
                    <a href="${this.escapeAttr(this.jobPageUrl(page - 1))}" data-job-page="prev" class="rounded-md border border-slate-200 bg-white px-3 py-1.5 font-bold text-slate-600 hover:bg-slate-50">
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
                    <a href="${this.escapeAttr(this.jobPageUrl(page + 1))}" data-job-page="next" class="rounded-md border border-slate-200 bg-white px-3 py-1.5 font-bold text-slate-600 hover:bg-slate-50">
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

    jobScopeLabel() {
        const parts = [
            this.jobSearch ? `搜索“${this.jobSearch}”` : '',
            this.jobStatus ? this.jobStatusLabel(this.jobStatus) : '',
        ].filter(Boolean);
        return parts.join(' · ');
    }

    jobPageUrl(page) {
        const url = new URL(window.location.href);
        url.searchParams.set('job_page', String(Math.max(1, page)));
        this.applyJobStatusToUrl(url);
        this.applyJobSearchToUrl(url);
        return `${url.pathname}${url.search}${url.hash}`;
    }

    updateJobPageUrl(page) {
        if (!window.history?.replaceState) return;
        const url = new URL(window.location.href);
        url.searchParams.set('job_page', String(Math.max(1, page)));
        this.applyJobStatusToUrl(url);
        this.applyJobSearchToUrl(url);
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

    applyJobSearchToUrl(url) {
        const search = this.normalizeJobSearch(this.jobSearch);
        if (search) {
            url.searchParams.set('q', search);
        } else {
            url.searchParams.delete('q');
        }
        url.searchParams.delete('job_search');
    }

    syncJobStatusFilter() {
        if (!this.els.jobStatusFilter) return;
        const status = this.normalizeJobFilterStatus(this.jobStatus);
        if (this.els.jobStatusFilter.value !== status) this.els.jobStatusFilter.value = status;
    }

    syncJobSearchInput() {
        if (!this.els.jobNameSearch) return;
        const search = this.normalizeJobSearch(this.jobSearch);
        if (this.els.jobNameSearch.value !== search) this.els.jobNameSearch.value = search;
    }

    normalizeStatus(status) {
        const value = String(status || '').trim().toLowerCase();
        const map = {
            pending: 'queued',
            processing: 'running',
            completed: 'succeeded',
            cancelled: 'canceled',
        };
        return map[value] || value;
    }

    normalizeJobFilterStatus(status) {
        const value = this.normalizeStatus(status);
        return this.jobStatusOptions().some((item) => item.value === value) ? value : '';
    }

    normalizeJobSearch(value) {
        return String(value || '').replace(/\s+/g, ' ').trim().slice(0, 80);
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

    jobStatusLabel(status = this.jobStatus) {
        return this.jobStatusOptions().find((item) => item.value === status)?.label || '全部';
    }

    statusMeta(status) {
        status = this.normalizeStatus(status);
        const map = {
            queued: { label: '排队中', className: 'bg-slate-100 text-slate-600', dot: 'bg-slate-400', bar: 'bg-slate-500' },
            running: { label: '运行中', className: 'bg-blue-50 text-blue-700', dot: 'bg-blue-500', bar: 'bg-blue-500' },
            succeeded: { label: '成功', className: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', bar: 'bg-emerald-500' },
            failed: { label: '失败', className: 'bg-red-50 text-red-700', dot: 'bg-red-500', bar: 'bg-red-500' },
            canceled: { label: '已取消', className: 'bg-slate-100 text-slate-500', dot: 'bg-slate-400', bar: 'bg-slate-400' },
        };
        return map[status] || map.queued;
    }

    setNotice(message, type = 'info') {
        const classes = {
            success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
            error: 'border-red-200 bg-red-50 text-red-700',
            info: 'border-blue-200 bg-blue-50 text-blue-700',
        };
        this.els.notice.className = `mb-4 rounded-lg border px-4 py-3 text-sm ${classes[type] || classes.info}`;
        this.els.notice.textContent = message;
        this.els.notice.classList.remove('hidden');
    }

    formatTime(value) {
        if (!value) return '';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return date.toLocaleString('zh-CN', { hour12: false });
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

    formatBytes(value) {
        const bytes = Number(value || 0);
        if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        let size = bytes;
        let index = 0;
        while (size >= 1024 && index < units.length - 1) {
            size /= 1024;
            index += 1;
        }
        return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
    }

    normalizeNotes(value) {
        if (Array.isArray(value)) return value.map((item) => String(item || '').trim()).filter(Boolean);
        const text = String(value || '').trim();
        return text ? [text] : [];
    }

    safeJson(value) {
        try {
            return JSON.stringify(value || {}, null, 2);
        } catch (_error) {
            return '{}';
        }
    }

    escape(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    escapeAttr(value) {
        return this.escape(value).replace(/`/g, '&#096;');
    }

    safeDomId(value) {
        return String(value || '').replace(/[^a-zA-Z0-9_-]/g, '_') || 'item';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.reportTop10WorkflowPage = new ReportTop10WorkflowPage();
});
