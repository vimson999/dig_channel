class ReportScriptMaterialWorkflowPage {
    constructor() {
        this.assets = [];
        this.selected = [];
        this.jobs = [];
        this.assetPage = 1;
        this.assetPerPage = 10;
        this.assetScope = 'all';
        this.assetPagination = { page: 1, per_page: 10, has_more: false };
        this.jobPage = 1;
        this.jobPerPage = 10;
        this.jobStatus = '';
        this.jobSearch = '';
        this.jobPagination = { page: 1, pages: 0, total: 0 };
        this.selectedJobId = '';
        this.selectedJobDetail = null;
        this.assetSearchTimer = null;
        this.jobSearchTimer = null;
        this.pollTimer = null;
        this.geminiEnvUrl = '';
        this.geminiUrlPresets = this.defaultGeminiUrlPresets();
        this.els = {
            providerStatus: document.getElementById('provider-status'),
            workerStatus: document.getElementById('worker-status'),
            refreshStatusBtn: document.getElementById('refresh-status-btn'),
            restartWorkerBtn: document.getElementById('restart-worker-btn'),
            notice: document.getElementById('notice'),
            selectedCount: document.getElementById('selected-count'),
            assetTabs: document.getElementById('asset-tabs'),
            assetSearch: document.getElementById('asset-search'),
            assetRefreshBtn: document.getElementById('asset-refresh-btn'),
            assetSummary: document.getElementById('asset-summary'),
            assetList: document.getElementById('asset-list'),
            assetPagination: document.getElementById('asset-pagination'),
            selectedList: document.getElementById('selected-list'),
            clearSelectedBtn: document.getElementById('clear-selected-btn'),
            geminiUrlSelect: document.getElementById('gemini-url-select'),
            geminiUrl: document.getElementById('gemini-url'),
            geminiUrlCurrent: document.getElementById('gemini-url-current'),
            prompt: document.getElementById('prompt'),
            reviewPrompt: document.getElementById('review-prompt'),
            submitBtn: document.getElementById('submit-btn'),
            summaryText: document.getElementById('summary-text'),
            jobSearch: document.getElementById('job-search'),
            jobStatusFilter: document.getElementById('job-status-filter'),
            loadHistoryBtn: document.getElementById('load-history-btn'),
            jobList: document.getElementById('job-list'),
            jobPagination: document.getElementById('job-pagination'),
            jobDetail: document.getElementById('job-detail'),
        };
        this.renderGeminiUrlOptions();
        this.bind();
        this.refreshStatus();
        this.loadAssets({ silent: true });
        this.loadHistory({ silent: true });
        this.resetPollTimer();
        this.render();
    }

    defaultGeminiUrlPresets() {
        return [
            { value: '', label: '使用 .env 默认' },
            { value: 'https://gemini.google.com/gem/099a92ae7888', label: '文案编辑v4' },
            { value: 'https://gemini.google.com/gem/b71a9220571e', label: '文案编辑v5' },
            { value: 'https://gemini.google.com/gem/980ba0a0ff22', label: '文案v6' },
            { value: 'https://gemini.google.com/gem/3d1eca63c142', label: '今日研报严选总编辑 Gem' },
            { value: 'https://gemini.google.com/gem/f9e84d5e215c', label: 'MP3 视频解析 Gem' },
            { value: '__custom__', label: '自定义 URL' },
        ];
    }

    renderGeminiUrlOptions() {
        if (!this.els.geminiUrlSelect) return;
        const previousValue = this.els.geminiUrlSelect.value || '';
        const defaultValue = 'https://gemini.google.com/gem/980ba0a0ff22';
        this.els.geminiUrlSelect.innerHTML = this.geminiUrlPresets.map((item) => (
            `<option value="${this.escape(item.value)}">${this.escape(item.label)}</option>`
        )).join('');
        if (this.geminiUrlPresets.some((item) => item.value === previousValue)) {
            this.els.geminiUrlSelect.value = previousValue || defaultValue;
        }
        this.updateGeminiUrlUi();
    }

    updateGeminiUrlUi() {
        const selected = this.els.geminiUrlSelect?.value || '';
        const isCustom = selected === '__custom__';
        if (this.els.geminiUrl) {
            this.els.geminiUrl.classList.toggle('hidden', !isCustom);
            this.els.geminiUrl.disabled = !isCustom;
            if (!isCustom) this.els.geminiUrl.value = '';
        }
        if (!this.els.geminiUrlCurrent) return;
        const envText = this.geminiEnvUrl || 'REPORT_PDF_GEMINI_URL / GEMINI_BROWSER_URL';
        if (isCustom) {
            this.els.geminiUrlCurrent.textContent = '自定义 URL 会覆盖 worker 默认 Gem';
        } else if (selected) {
            this.els.geminiUrlCurrent.textContent = `覆盖：${selected}`;
        } else {
            this.els.geminiUrlCurrent.textContent = `默认：${envText}`;
        }
        this.els.geminiUrlCurrent.title = this.els.geminiUrlCurrent.textContent;
    }

    selectedGeminiUrl() {
        const select = document.getElementById('gemini-url-select') || this.els.geminiUrlSelect;
        const input = document.getElementById('gemini-url') || this.els.geminiUrl;
        const selected = select?.value || '';
        if (selected === '__custom__') return input?.value.trim() || '';
        return selected.trim();
    }

    apiRoot() {
        const configured = window.API_BASE_URL || window.CONFIG?.API_BASE_URL || '';
        if (configured) return configured.replace(/\/api\/v1\/?$/, '').replace(/\/$/, '');
        return `${window.location.protocol}//${window.location.hostname}:5001`;
    }

    token() {
        return localStorage.getItem('access_token') || localStorage.getItem('token') || '';
    }

    headers() {
        const token = this.token();
        return {
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
        };
    }

    async request(path, options = {}) {
        const { timeoutMs = 15000, ...fetchOptions } = options;
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(`${this.apiRoot()}${path}`, {
                ...fetchOptions,
                headers: { ...this.headers(), ...(fetchOptions.headers || {}) },
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
        this.els.restartWorkerBtn.addEventListener('click', () => this.restartWorker());
        this.els.assetRefreshBtn.addEventListener('click', () => this.loadAssets({ page: this.assetPage }));
        this.els.assetTabs.addEventListener('click', (event) => this.handleAssetScopeClick(event));
        this.els.assetSearch.addEventListener('input', () => this.scheduleAssetSearch());
        this.els.assetSearch.addEventListener('search', () => this.scheduleAssetSearch());
        this.els.assetSearch.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') this.loadAssets({ page: 1 });
        });
        this.els.assetList.addEventListener('click', (event) => this.handleAssetClick(event));
        this.els.assetPagination.addEventListener('click', (event) => this.handleAssetPageClick(event));
        this.els.assetPagination.addEventListener('submit', (event) => this.handleAssetPageJump(event));
        this.els.selectedList.addEventListener('click', (event) => this.handleSelectedClick(event));
        this.els.clearSelectedBtn.addEventListener('click', () => {
            this.selected = [];
            this.renderSelected();
            this.renderAssets();
        });
        this.els.geminiUrlSelect?.addEventListener('change', () => this.updateGeminiUrlUi());
        this.els.submitBtn.addEventListener('click', () => this.submit());
        this.els.loadHistoryBtn.addEventListener('click', () => this.loadHistory({ page: 1, force: true }));
        this.els.jobStatusFilter.addEventListener('change', () => {
            this.jobStatus = this.els.jobStatusFilter.value;
            this.loadHistory({ page: 1, force: true });
        });
        this.els.jobSearch.addEventListener('input', () => this.scheduleJobSearch());
        this.els.jobSearch.addEventListener('search', () => this.scheduleJobSearch());
        this.els.jobSearch.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') this.applyJobSearch();
        });
        this.els.jobList.addEventListener('click', (event) => this.handleJobClick(event));
        this.els.jobPagination.addEventListener('click', (event) => this.handleJobPageClick(event));
        this.els.jobPagination.addEventListener('submit', (event) => this.handleJobPageJump(event));
        this.els.jobDetail.addEventListener('click', (event) => this.handleDetailClick(event));
    }

    async refreshStatus() {
        await Promise.all([this.refreshProviderStatus(), this.refreshWorkerStatus()]);
    }

    async refreshProviderStatus() {
        try {
            const result = await this.request('/api/report-script-material-workflows/status', { timeoutMs: 10000 });
            const provider = result.data?.provider || {};
            const gemini = result.data?.report_pdf_gemini || {};
            this.geminiEnvUrl = (gemini.gemini_url || '').trim();
            this.updateGeminiUrlUi();
            const geminiOk = Boolean(gemini.configured);
            const normalizerOk = Boolean(provider.normalizer_available ?? provider.configured);
            const fallbackAllowed = Boolean(provider.local_fallback_allowed);
            const ok = geminiOk && normalizerOk;
            this.els.providerStatus.className = `rounded-lg border px-3 py-2 text-xs font-bold ${ok ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`;
            if (!geminiOk) {
                this.els.providerStatus.textContent = 'Gemini 未配置';
            } else if (!normalizerOk) {
                this.els.providerStatus.textContent = '结构化器不可用';
            } else if (fallbackAllowed) {
                this.els.providerStatus.textContent = '结构化器 Fallback 已启用';
            } else {
                this.els.providerStatus.textContent = 'Gemini / 结构化器已配置';
            }
        } catch (error) {
            this.els.providerStatus.className = 'rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-bold text-red-700';
            this.els.providerStatus.textContent = `配置失败：${error.message}`;
        }
    }

    async refreshWorkerStatus() {
        try {
            const result = await this.request('/api/v1/celery/status', { timeoutMs: 12000 });
            const service = (result.data?.services || []).find((item) => item.id === 'report_script_material_workflow');
            const running = Boolean(service?.running) || Number(service?.processes?.length || service?.pids?.length || 0) > 0;
            const queueLength = Number(service?.queue_length || 0);
            this.els.workerStatus.className = `rounded-lg border px-3 py-2 text-xs font-bold ${running ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`;
            this.els.workerStatus.textContent = running ? `物料队列在线 · 积压 ${queueLength}` : `物料队列未在线 · 积压 ${queueLength}`;
        } catch (error) {
            this.els.workerStatus.className = 'rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-bold text-red-700';
            this.els.workerStatus.textContent = `Monitor 失败：${error.message}`;
        }
    }

    async restartWorker() {
        if (!confirm('确认重启 report_script_material_workflow 队列？')) return;
        this.els.restartWorkerBtn.disabled = true;
        try {
            this.setNotice('正在重启基础文案物料队列...', 'muted');
            await this.request('/api/v1/celery/restart/report_script_material_workflow', { method: 'POST', timeoutMs: 25000 });
            this.setNotice('队列重启请求已发送。', 'success');
            await this.refreshWorkerStatus();
        } catch (error) {
            this.setNotice(`重启队列失败：${error.message}`, 'error');
        } finally {
            this.els.restartWorkerBtn.disabled = false;
        }
    }

    scheduleAssetSearch() {
        clearTimeout(this.assetSearchTimer);
        this.assetSearchTimer = setTimeout(() => this.loadAssets({ page: 1, silent: true }), 350);
    }

    scheduleJobSearch() {
        clearTimeout(this.jobSearchTimer);
        this.jobSearchTimer = setTimeout(() => this.applyJobSearch(), 350);
    }

    applyJobSearch() {
        this.jobSearch = this.els.jobSearch.value.trim();
        this.loadHistory({ page: 1, silent: true });
    }

    async loadAssets(options = {}) {
        const page = Math.max(1, Number(options.page || this.assetPage || 1));
        this.assetPage = page;
        this.renderAssetTabs();
        if (!options.silent) this.els.assetSummary.textContent = '加载已同步 PDF 中...';
        try {
            const query = new URLSearchParams({
                page: String(page),
                per_page: String(this.assetPerPage),
                scope: this.assetScope,
            });
            const q = this.els.assetSearch.value.trim();
            if (q) query.set('q', q);
            const result = await this.request(`/api/report-script-material-workflows/pdf-assets?${query.toString()}`, { timeoutMs: 30000 });
            this.assets = result.data?.items || result.items || [];
            this.assetPagination = result.data?.pagination || { page, per_page: this.assetPerPage, has_more: false };
            this.assetPage = Number(this.assetPagination.page || page);
            this.renderAssets();
        } catch (error) {
            this.els.assetSummary.textContent = `加载失败：${error.message}`;
            this.els.assetList.innerHTML = '';
            this.els.assetPagination.innerHTML = '';
        }
    }

    async loadHistory(options = {}) {
        const page = Math.max(1, Number(options.page || this.jobPage || 1));
        this.jobPage = page;
        try {
            const query = new URLSearchParams({
                page: String(page),
                per_page: String(this.jobPerPage),
            });
            if (this.jobStatus) query.set('status', this.jobStatus);
            if (this.jobSearch) query.set('q', this.jobSearch);
            const result = await this.request(`/api/report-script-material-workflows/jobs?${query.toString()}`, { timeoutMs: 15000 });
            this.jobs = result.data?.items || result.items || [];
            this.jobPagination = result.data?.pagination || { page, pages: 0, total: 0 };
            this.renderJobs();
            if (this.selectedJobId && this.jobs.some((job) => job.job_id === this.selectedJobId)) {
                this.loadJobDetail(this.selectedJobId, { silent: true });
            }
        } catch (error) {
            if (options.silent) return;
            this.els.summaryText.textContent = `加载历史失败：${error.message}`;
            this.els.jobList.innerHTML = '';
            this.els.jobPagination.innerHTML = '';
        }
    }

    async submit() {
        if (!this.selected.length) {
            this.setNotice('请先选择 PDF。', 'error');
            return;
        }
        this.els.submitBtn.disabled = true;
        this.els.submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>投递中';
        try {
            const payload = {
                pdf_asset_ids: this.selected.map((item) => item.id),
                gemini_url: this.selectedGeminiUrl(),
                prompt: this.els.prompt.value.trim(),
                review_prompt: this.els.reviewPrompt.value.trim(),
                metadata: { source: 'report-script-material-workflow-test-page' },
            };
            const result = await this.request('/api/report-script-material-workflows/jobs', {
                method: 'POST',
                body: JSON.stringify(payload),
                timeoutMs: 30000,
            });
            const submitted = result.data?.submitted_count || (result.jobs || []).length;
            const failed = result.data?.failed_count || (result.errors || []).length;
            this.setNotice(`已投递 ${submitted} 个任务${failed ? `，${failed} 个失败` : ''}。`, failed ? 'warning' : 'success');
            this.selected = [];
            this.renderSelected();
            this.renderAssets();
            await this.loadHistory({ page: 1, force: true });
            await this.refreshWorkerStatus();
        } catch (error) {
            this.setNotice(`投递失败：${error.message}`, 'error');
        } finally {
            this.els.submitBtn.disabled = false;
            this.els.submitBtn.innerHTML = '<i class="fas fa-paper-plane mr-1.5"></i>投递基础文案物料任务';
        }
    }

    handleAssetScopeClick(event) {
        const button = event.target.closest('[data-asset-scope]');
        if (!button) return;
        this.assetScope = button.dataset.assetScope || 'all';
        this.loadAssets({ page: 1 });
    }

    handleAssetClick(event) {
        const doubaoButton = event.target.closest('[data-asset-doubao-id]');
        if (doubaoButton) {
            event.preventDefault();
            event.stopPropagation();
            return this.submitAssetMaterialToDoubao(doubaoButton.dataset.assetDoubaoId, doubaoButton);
        }
        const materialButton = event.target.closest('[data-asset-material-id]');
        if (materialButton) {
            event.preventDefault();
            event.stopPropagation();
            return this.loadAssetMaterial(materialButton.dataset.assetMaterialId);
        }
        if (event.target.closest('[data-asset-preview-link]')) return;
        const button = event.target.closest('[data-asset-id]');
        if (!button) return;
        const id = button.dataset.assetId;
        const asset = this.assets.find((item) => item.id === id);
        if (!asset) return;
        if (this.selected.some((item) => item.id === id)) {
            this.selected = this.selected.filter((item) => item.id !== id);
        } else {
            this.selected.push(asset);
            if (asset.material_has_script) {
                this.setNotice('这份 PDF 已有基础文案，可点“查看文案”；继续投递会生成一条新的物料任务。', 'warning');
            }
        }
        this.renderSelected();
        this.renderAssets();
    }

    handleAssetPageClick(event) {
        const button = event.target.closest('[data-asset-page]');
        if (!button || button.disabled) return;
        if (button.dataset.assetPage === 'prev' && this.assetPage > 1) this.loadAssets({ page: this.assetPage - 1 });
        if (button.dataset.assetPage === 'next' && this.assetPagination?.has_more) this.loadAssets({ page: this.assetPage + 1 });
    }

    handleAssetPageJump(event) {
        event.preventDefault();
        const input = event.target.querySelector('input[name="asset_page"]');
        const page = Number(input?.value || 1);
        if (Number.isFinite(page) && page > 0) {
            this.loadAssets({ page: Math.floor(page) });
        }
    }

    handleSelectedClick(event) {
        const button = event.target.closest('[data-remove-id]');
        if (!button) return;
        this.selected = this.selected.filter((item) => item.id !== button.dataset.removeId);
        this.renderSelected();
        this.renderAssets();
    }

    handleJobPageClick(event) {
        const button = event.target.closest('[data-job-page]');
        if (!button || button.disabled) return;
        if (button.dataset.jobPage === 'prev' && this.jobPage > 1) this.loadHistory({ page: this.jobPage - 1 });
        if (button.dataset.jobPage === 'next' && this.jobPage < Number(this.jobPagination.pages || 0)) this.loadHistory({ page: this.jobPage + 1 });
    }

    handleJobPageJump(event) {
        event.preventDefault();
        const pages = Math.max(1, Number(this.jobPagination.pages || 1));
        const input = event.target.querySelector('input[name="job_page"]');
        const page = Math.max(1, Math.min(pages, Math.floor(Number(input?.value || 1))));
        if (Number.isFinite(page)) {
            this.loadHistory({ page, force: true });
        }
    }

    async handleJobClick(event) {
        const copyTitle = event.target.closest('[data-job-copy-title]');
        if (copyTitle) {
            event.preventDefault();
            event.stopPropagation();
            await this.copyText(copyTitle.dataset.jobCopyTitle || '', '标题已复制。');
            return;
        }
        const doubao = event.target.closest('[data-job-doubao]');
        if (doubao) {
            event.preventDefault();
            event.stopPropagation();
            await this.submitJobMaterialToDoubao(doubao.dataset.jobDoubao, doubao);
            return;
        }
        const refresh = event.target.closest('[data-job-refresh]');
        if (refresh) {
            event.preventDefault();
            event.stopPropagation();
            await this.loadJobDetail(refresh.dataset.jobRefresh);
            await this.loadHistory({ page: this.jobPage, silent: true });
            return;
        }
        const remove = event.target.closest('[data-job-remove]');
        if (remove) {
            event.preventDefault();
            event.stopPropagation();
            this.jobs = this.jobs.filter((job) => job.job_id !== remove.dataset.jobRemove);
            if (this.selectedJobId === remove.dataset.jobRemove) {
                this.selectedJobId = '';
                this.selectedJobDetail = null;
                this.renderDetail();
            }
            this.renderJobs();
            return;
        }
        if (event.target.closest('[data-job-preview-link]')) return;
        const retry = event.target.closest('[data-job-retry]');
        if (retry) return this.retryJob(retry.dataset.jobRetry);
        const cancel = event.target.closest('[data-job-cancel]');
        if (cancel) return this.cancelJob(cancel.dataset.jobCancel);
        const card = event.target.closest('[data-job-id]');
        if (!card) return;
        this.selectedJobId = card.dataset.jobId;
        await this.loadJobDetail(this.selectedJobId);
    }

    async handleDetailClick(event) {
        const doubao = event.target.closest('[data-doubao-script]');
        if (doubao) {
            await this.submitSelectedDetailToDoubao(doubao);
            return;
        }
        const copy = event.target.closest('[data-copy-script]');
        if (copy && this.selectedJobDetail?.final_script_for_tts) {
            await navigator.clipboard.writeText(this.selectedJobDetail.final_script_for_tts);
            this.setNotice('文案已复制。', 'success');
        }
    }

    async copyText(text, successMessage = '已复制。') {
        if (!text) return;
        try {
            if (navigator.clipboard?.writeText) {
                await navigator.clipboard.writeText(text);
            } else {
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand('copy');
                document.body.removeChild(textarea);
            }
            this.setNotice(successMessage, 'success');
        } catch (error) {
            this.setNotice(`复制失败：${error.message}`, 'error');
        }
    }

    async loadAssetMaterial(assetId) {
        if (!assetId) return;
        try {
            const result = await this.request(`/api/report-script-material-workflows/pdf-assets/${encodeURIComponent(assetId)}/material`, { timeoutMs: 15000 });
            this.selectedJobDetail = result.data || null;
            this.selectedJobId = this.selectedJobDetail?.job_id || '';
            this.renderJobs();
            this.renderDetail();
            if (this.selectedJobDetail?.final_script_for_tts) {
                this.setNotice('已加载这份 PDF 的基础文案。', 'success');
            } else {
                this.setNotice('这份 PDF 有物料任务记录，但还没有可展示的最终文案。', 'warning');
            }
        } catch (error) {
            this.setNotice(`加载基础文案失败：${error.message}`, 'error');
        }
    }

    async loadJobDetail(jobId, options = {}) {
        try {
            const result = await this.request(`/api/report-script-material-workflows/jobs/${encodeURIComponent(jobId)}`, { timeoutMs: 15000 });
            this.selectedJobDetail = result.data || null;
            this.renderJobs();
            this.renderDetail();
        } catch (error) {
            if (!options.silent) this.setNotice(`加载详情失败：${error.message}`, 'error');
        }
    }

    async submitAssetMaterialToDoubao(assetId, trigger = null) {
        if (!assetId) return;
        const restore = this.setButtonBusy(trigger, '<i class="fas fa-spinner fa-spin mr-1"></i>投递中');
        try {
            const result = await this.request(`/api/report-script-material-workflows/pdf-assets/${encodeURIComponent(assetId)}/material`, { timeoutMs: 15000 });
            const material = result.data || null;
            this.selectedJobDetail = material;
            this.selectedJobId = material?.job_id || '';
            this.renderJobs();
            this.renderDetail();
            await this.submitMaterialScriptToDoubao(material);
        } catch (error) {
            this.setNotice(`投递豆包朗读失败：${error.message}`, 'error');
        } finally {
            restore();
        }
    }

    async fetchJobDetail(jobId) {
        const result = await this.request(`/api/report-script-material-workflows/jobs/${encodeURIComponent(jobId)}`, { timeoutMs: 15000 });
        return result.data || null;
    }

    async submitJobMaterialToDoubao(jobId, trigger = null) {
        if (!jobId) return;
        const restore = this.setButtonBusy(trigger, '<i class="fas fa-spinner fa-spin"></i>');
        try {
            const detail = this.selectedJobDetail?.job_id === jobId && this.selectedJobDetail?.final_script_for_tts
                ? this.selectedJobDetail
                : await this.fetchJobDetail(jobId);
            this.selectedJobDetail = detail;
            this.selectedJobId = detail?.job_id || jobId;
            this.renderJobs();
            this.renderDetail();
            await this.submitMaterialScriptToDoubao(detail);
        } catch (error) {
            this.setNotice(`投递豆包朗读失败：${error.message}`, 'error');
        } finally {
            restore();
        }
    }

    async submitSelectedDetailToDoubao(trigger = null) {
        if (!this.selectedJobDetail?.job_id) {
            this.setNotice('请先选择一条已有基础文案的任务。', 'warning');
            return;
        }
        await this.submitJobMaterialToDoubao(this.selectedJobDetail.job_id, trigger);
    }

    async submitMaterialScriptToDoubao(material) {
        const script = String(material?.final_script_for_tts || '').trim();
        if (!script) {
            throw new Error('这份基础文案还没有可投递的最终文案');
        }
        const pdf = material?.pdf || {};
        const title = this.jobDisplayTitle(material || {});
        const metadata = {
            source: 'report-script-material-workflow-test-page',
            source_feature: 'report_script_material_workflow',
            source_task_id: material?.job_id || '',
            material_feature: material?.feature || 'report_script_material_workflow',
            material_status: material?.status || '',
            pdf_attachment_id: pdf.attachment_id || '',
            pdf_filename: pdf.filename || '',
            topic_title: pdf.topic_title || '',
            title,
            script_chars: script.length,
        };
        const result = await this.request('/api/doubao-tts/jobs', {
            method: 'POST',
            body: JSON.stringify({
                text: script,
                metadata,
                reuse_completed: true,
            }),
            timeoutMs: 30000,
        });
        const payload = result.data || result || {};
        const jobId = payload.job_id || result.job_id || '';
        const reused = Boolean(payload.reused || result.reused);
        const prefix = reused ? '已复用同文本豆包音频任务' : '已投递豆包网页朗读任务';
        this.setNotice(`${prefix}${jobId ? `：${jobId}` : ''}。可到“豆包网页朗读测试台”查看进度和下载音频。`, 'success');
    }

    async retryJob(jobId) {
        try {
            await this.request(`/api/report-script-material-workflows/jobs/${encodeURIComponent(jobId)}/retry`, { method: 'POST', timeoutMs: 25000 });
            this.setNotice('续跑请求已发送。', 'success');
            await this.loadHistory({ page: this.jobPage, force: true });
        } catch (error) {
            this.setNotice(`续跑失败：${error.message}`, 'error');
        }
    }

    async cancelJob(jobId) {
        if (!confirm('确认取消这个任务？')) return;
        try {
            await this.request(`/api/report-script-material-workflows/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST', timeoutMs: 15000 });
            this.setNotice('任务已取消。', 'success');
            await this.loadHistory({ page: this.jobPage, force: true });
        } catch (error) {
            this.setNotice(`取消失败：${error.message}`, 'error');
        }
    }

    resetPollTimer() {
        clearInterval(this.pollTimer);
        this.pollTimer = setInterval(() => {
            this.loadHistory({ page: this.jobPage, silent: true });
            this.refreshWorkerStatus();
        }, 12000);
    }

    render() {
        this.renderAssetTabs();
        this.renderSelected();
        this.renderAssets();
        this.renderJobs();
        this.renderDetail();
    }

    renderAssetTabs() {
        this.els.assetTabs.querySelectorAll('[data-asset-scope]').forEach((button) => {
            const active = button.dataset.assetScope === this.assetScope;
            button.className = `rounded-md border px-3 py-2 transition ${active ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'}`;
        });
    }

    renderAssets() {
        const selectedIds = new Set(this.selected.map((item) => item.id));
        this.els.assetSummary.textContent = this.assets.length
            ? `${this.assetScopeLabel()}· 第 ${this.assetPage} 页 · 本页 ${this.assets.length} 个 PDF`
            : (this.assetScope === 'hot' ? '没有找到下载次数达到 40 次的热门 PDF。' : '没有找到已下载的同步 PDF。');
        this.els.assetList.innerHTML = this.assets.map((asset) => {
            const selected = selectedIds.has(asset.id);
            const previewUrl = asset.preview_url ? `${this.apiRoot()}${asset.preview_url}` : '';
            const title = asset.filename || 'PDF';
            const subtitle = asset.topic_title || asset.tab_label || asset.topic_id || '-';
            const itemTitle = [asset.filename, asset.topic_title, asset.tab_label].filter(Boolean).join('\n');
            const downloadText = window.DownloadCountHighlight?.badgeText(asset.download_count)
                || (Number.isFinite(Number(asset.download_count)) ? `下载 ${Number(asset.download_count)} 次` : '下载 -');
            const downloadBadgeClass = window.DownloadCountHighlight?.badgeClass(asset.download_count)
                || 'border-slate-200 bg-slate-100 text-slate-500';
            const syncTime = this.formatDateTime(asset.topic_last_synced_at || asset.updated_at);
            const topicTime = this.formatDateTime(asset.topic_create_time) || asset.topic_create_time || '';
            const metaLine = [
                asset.tab_label || '',
                syncTime ? `同步 ${syncTime}` : '',
                topicTime ? `发布 ${topicTime}` : '',
            ].filter(Boolean).join(' · ');
            const materialMeta = this.materialTaskMeta(asset);
            return `
                <div role="button" tabindex="0" data-asset-id="${this.escape(asset.id)}" title="${this.escape(itemTitle)}" class="w-full cursor-pointer rounded-md border px-3 py-2 text-left text-xs transition ${selected ? 'border-indigo-300 bg-indigo-50 text-indigo-900' : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'}">
                    <div class="flex items-start justify-between gap-2">
                        <div class="min-w-0">
                            <div class="truncate font-bold">${this.escape(title)}</div>
                            <div class="mt-1 truncate text-slate-500">${this.escape(subtitle)}</div>
                        </div>
                        <div class="flex shrink-0 items-center gap-1.5">
                            <span class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-bold text-slate-500">${this.formatBytes(asset.bytes)}</span>
                            ${previewUrl ? `<a data-asset-preview-link href="${this.escape(previewUrl)}" target="_blank" rel="noopener noreferrer" class="rounded-md border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-bold text-indigo-700 hover:bg-indigo-50"><i class="fas fa-eye mr-1"></i>预览</a>` : ''}
                            <span class="inline-flex h-6 w-6 items-center justify-center rounded-full ${selected ? 'bg-indigo-600 text-white' : 'bg-slate-900 text-white'}">
                                <i class="fas ${selected ? 'fa-check' : 'fa-plus'} text-[10px]"></i>
                            </span>
                        </div>
                    </div>
                    <div class="mt-1 truncate text-[11px] text-slate-400">${this.escape(metaLine)}</div>
                    <div class="mt-2 flex flex-wrap items-center gap-1.5">
                        <span class="rounded-full border px-2 py-0.5 text-[11px] font-bold ${downloadBadgeClass}">
                            <i class="fas fa-download mr-1"></i>${this.escape(downloadText)}
                        </span>
                        <span class="rounded-full px-2 py-0.5 text-[11px] font-bold ${materialMeta.className}">
                            ${materialMeta.dot ? `<span class="mr-1 inline-block h-1.5 w-1.5 rounded-full ${materialMeta.dot}"></span>` : ''}
                            ${this.escape(materialMeta.text)}
                        </span>
                        ${materialMeta.detail ? `<span class="text-[11px] font-bold text-slate-400">${this.escape(materialMeta.detail)}</span>` : ''}
                        ${asset.material_task_id ? `
                            <button type="button" data-asset-material-id="${this.escape(asset.id)}" class="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-bold text-slate-600 hover:bg-indigo-50 hover:text-indigo-700">
                                <i class="fas fa-file-lines mr-1"></i>查看文案
                            </button>
                        ` : ''}
                        ${asset.material_has_script ? `
                            <button type="button" data-asset-doubao-id="${this.escape(asset.id)}" class="rounded-full border border-emerald-100 bg-emerald-50 px-2 py-0.5 text-[11px] font-bold text-emerald-700 hover:bg-emerald-100 disabled:cursor-wait disabled:opacity-60">
                                <i class="fas fa-volume-high mr-1"></i>豆包朗读
                            </button>
                        ` : ''}
                    </div>
                </div>
            `;
        }).join('') || '<div class="rounded-md border border-dashed border-slate-200 p-6 text-center text-sm text-slate-500">没有找到 PDF。</div>';
        this.els.assetPagination.innerHTML = this.renderPager('asset', this.assetPage, this.assetPagination?.has_more);
    }

    renderSelected() {
        this.els.selectedCount.textContent = `${this.selected.length} 份`;
        this.els.selectedList.innerHTML = this.selected.map((asset) => `
            <div class="flex items-center justify-between gap-3 rounded-md border border-slate-200 bg-white px-3 py-2">
                <div class="min-w-0">
                    <div class="truncate text-sm font-bold">${this.escape(asset.topic_title || asset.filename || asset.id)}</div>
                    <div class="truncate text-xs text-slate-500">${this.escape(asset.filename || '')}</div>
                </div>
                <button data-remove-id="${this.escape(asset.id)}" class="shrink-0 rounded border border-slate-200 px-2 py-1 text-xs font-bold text-slate-600 hover:bg-slate-50">
                    <i class="fas fa-xmark"></i>
                </button>
            </div>
        `).join('') || '<div class="rounded-md border border-dashed border-slate-200 p-4 text-center text-sm text-slate-500">尚未选择 PDF。</div>';
    }

    renderJobs() {
        const total = Number(this.jobPagination.total || 0);
        this.els.summaryText.textContent = `共 ${total} 个基础文案物料任务`;
        this.els.jobList.innerHTML = this.jobs.map((job, index) => this.renderJob(job, index)).join('')
            || '<div class="rounded-lg border border-dashed border-slate-200 p-8 text-center text-sm text-slate-500">暂无任务。</div>';
        this.els.jobPagination.innerHTML = this.renderJobPager();
    }

    renderJob(job, index) {
        const active = job.job_id === this.selectedJobId;
        const status = this.statusMeta(job.status);
        const displayTitle = this.jobDisplayTitle(job);
        const metaLine = this.jobMetaLine(job, index);
        const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
        return `
            <article data-job-id="${this.escape(job.job_id)}" class="cursor-pointer rounded-lg border bg-white p-3 shadow-sm transition ${active ? 'border-indigo-300 ring-1 ring-indigo-100' : 'border-slate-200 hover:border-slate-300'}">
                <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                        <div class="flex min-w-0 items-center gap-2">
                            <span class="h-2 w-2 shrink-0 rounded-full ${status.dot}"></span>
                            <h3 class="truncate text-sm font-black text-slate-900" title="${this.escape(displayTitle)}">${this.escape(displayTitle)}</h3>
                            <button type="button" data-job-copy-title="${this.escape(displayTitle)}" class="shrink-0 rounded-md border border-slate-200 bg-white px-1.5 py-1 text-[11px] text-slate-500 hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700" title="复制标题">
                                <i class="fas fa-copy"></i>
                            </button>
                        </div>
                        <p class="mt-1 truncate text-xs text-slate-500" title="${this.escape(metaLine)}">${this.escape(metaLine)}</p>
                    </div>
                    <div class="flex shrink-0 items-center gap-1.5">
                        <span class="rounded-full px-2.5 py-1 text-xs font-bold ${status.className}">${this.escape(status.label)}</span>
                        ${this.renderJobActionButtons(job)}
                    </div>
                </div>

                <div class="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
                    <div class="h-full rounded-full ${status.bar}" style="width:${progress}%"></div>
                </div>
                <div class="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                    <span>${this.escape(job.step || '-')} · ${progress}%</span>
                    <span>${this.escape(this.formatDateTime(job.updated_at || job.created_at) || this.formatTime(job.updated_at || job.created_at))}</span>
                </div>

                ${this.renderJobPdfRow(job)}

                ${job.error ? `<div class="mt-3 rounded-md border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-700">${this.escape(job.error)}</div>` : ''}
            </article>
        `;
    }

    renderJobActionButtons(job) {
        const id = this.escape(job.job_id || '');
        const buttons = [];
        if (['succeeded', 'completed'].includes(job.status)) {
            buttons.push(`<button type="button" data-job-doubao="${id}" class="rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1.5 text-xs text-emerald-700 hover:bg-emerald-100 disabled:cursor-wait disabled:opacity-60" title="投递豆包朗读"><i class="fas fa-volume-high"></i></button>`);
        }
        if (['failed', 'canceled'].includes(job.status)) {
            buttons.push(`<button type="button" data-job-retry="${id}" class="rounded-md border border-slate-300 px-2 py-1.5 text-xs text-slate-600 hover:bg-slate-50" title="续跑"><i class="fas fa-rotate"></i></button>`);
        }
        if (['queued', 'running'].includes(job.status)) {
            buttons.push(`<button type="button" data-job-cancel="${id}" class="rounded-md border border-slate-300 px-2 py-1.5 text-xs text-slate-600 hover:bg-slate-50" title="取消"><i class="fas fa-xmark"></i></button>`);
        }
        buttons.push(`<button type="button" data-job-refresh="${id}" class="rounded-md border border-slate-300 px-2 py-1.5 text-xs text-slate-600 hover:bg-slate-50" title="刷新"><i class="fas fa-rotate"></i></button>`);
        buttons.push(`<button type="button" data-job-remove="${id}" class="rounded-md border border-slate-300 px-2 py-1.5 text-xs text-slate-500 hover:bg-slate-50" title="从当前列表移除"><i class="fas fa-xmark"></i></button>`);
        return buttons.join('');
    }

    renderJobPdfRow(job) {
        const pdf = job.pdf || {};
        const filename = pdf.filename || this.jobDisplayTitle(job);
        const fallbackPath = job.job_id ? `/api/report-script-material-workflows/jobs/${encodeURIComponent(job.job_id)}/input-pdf` : '';
        const previewPath = pdf.preview_url || fallbackPath;
        const previewUrl = previewPath ? this.absoluteApiUrl(previewPath) : '';
        const rowContent = `
            <div class="flex min-w-0 items-center gap-2">
                <i class="fas fa-file-pdf shrink-0 text-red-500"></i>
                <span class="truncate">${this.escape(filename)}</span>
                <span class="shrink-0 text-slate-400">· ${this.formatBytes(pdf.bytes)}</span>
            </div>
            <span class="shrink-0 rounded-md border border-indigo-100 bg-white px-2 py-1 text-[11px] font-black text-indigo-700">
                <i class="fas fa-eye mr-1"></i>预览
            </span>
        `;
        if (!previewUrl) {
            return `<div class="mt-3 flex items-center justify-between gap-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">${rowContent}</div>`;
        }
        return `
            <a data-job-preview-link href="${this.escape(previewUrl)}" target="_blank" rel="noopener noreferrer" class="mt-3 flex items-center justify-between gap-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600 transition hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-800">
                ${rowContent}
            </a>
        `;
    }

    jobDisplayTitle(job) {
        return job.pdf?.filename || job.pdf?.topic_title || job.title || job.job_id || '研报基础文案物料';
    }

    jobMetaLine(job, index) {
        const sequence = `编排 ${this.jobSequenceNumber(index)}`;
        const createdAt = this.formatDateTime(job.created_at);
        return [
            sequence,
            createdAt ? `创建 ${createdAt}` : '',
            job.job_id || '',
        ].filter(Boolean).join(' · ');
    }

    jobSequenceNumber(index) {
        const total = Number(this.jobPagination?.total || 0);
        const page = Math.max(1, Number(this.jobPagination?.page || this.jobPage || 1));
        const perPage = Math.max(1, Number(this.jobPagination?.per_page || this.jobPerPage || this.jobs.length || 1));
        if (total > 0) return Math.max(1, total - ((page - 1) * perPage + index));
        return Math.max(1, this.jobs.length - index);
    }

    renderDetail() {
        const detail = this.selectedJobDetail;
        if (!detail) {
            this.els.jobDetail.innerHTML = '<div class="rounded-lg border border-dashed border-slate-200 bg-white p-8 text-center text-sm text-slate-500">选择一个任务查看文案。</div>';
            return;
        }
        const script = detail.final_script_for_tts || '';
        const hasScript = Boolean(String(script).trim());
        const structured = detail.structured_result || {};
        const children = detail.children || {};
        const detailTitle = this.jobDisplayTitle(detail);
        const materialTitle = structured.title || detailTitle;
        const coverTitle = structured.cover_title || '';
        const coverSubtitle = structured.cover_subtitle || '';
        const summary = structured.summary || '';
        const provider = structured.provider || detail.script_material?.source || '';
        const detailMeta = [
            this.formatDateTime(detail.created_at) ? `创建 ${this.formatDateTime(detail.created_at)}` : '',
            detail.job_id || '',
            this.statusLabel(detail.status),
        ].filter(Boolean).join(' · ');
        const childRows = Object.entries(children).map(([label, child]) => `
            <div class="rounded-md border border-slate-200 bg-white p-2">
                <div class="flex items-center justify-between gap-2 text-xs">
                    <span class="font-black text-slate-700">${this.escape(label)}</span>
                    <span class="${this.statusClass(child.status)} rounded-full px-2 py-0.5 font-black">${this.escape(this.statusLabel(child.status))}</span>
                </div>
                <div class="mt-1 truncate text-xs text-slate-500">${this.escape(child.step || '-')} · ${Number(child.progress || 0)}%</div>
            </div>
        `).join('');
        this.els.jobDetail.innerHTML = `
            <div class="rounded-lg border border-slate-200 bg-white p-4">
                <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                        <h3 class="truncate text-base font-black" title="${this.escape(detailTitle)}">${this.escape(detailTitle)}</h3>
                        <p class="mt-1 truncate text-xs text-slate-500" title="${this.escape(detailMeta)}">${this.escape(detailMeta)}</p>
                    </div>
                    <div class="flex shrink-0 flex-wrap items-center justify-end gap-2">
                        <button data-doubao-script ${hasScript ? '' : 'disabled'} class="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-700 hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-50">
                            <i class="fas fa-volume-high mr-1"></i>豆包朗读
                        </button>
                        <button data-copy-script ${hasScript ? '' : 'disabled'} class="rounded-md border border-slate-200 px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50">
                            <i class="fas fa-copy mr-1"></i>复制文案
                        </button>
                    </div>
                </div>
                <div class="mt-4 grid grid-cols-2 gap-2 text-xs">
                    <div class="rounded-md bg-slate-50 p-2"><span class="font-bold text-slate-500">字数</span><div class="mt-1 font-black">${script.length}</div></div>
                    <div class="rounded-md bg-slate-50 p-2"><span class="font-bold text-slate-500">片段</span><div class="mt-1 font-black">${this.escape(detail.tts_text?.chunks ?? '-')}</div></div>
                </div>
                ${childRows ? `<div class="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2">${childRows}</div>` : ''}
                <div class="mt-4 grid grid-cols-1 gap-2 text-xs md:grid-cols-2">
                    ${this.renderMaterialField('标题', materialTitle)}
                    ${this.renderMaterialField('封面标题', coverTitle, true)}
                    ${this.renderMaterialField('封面副标题', coverSubtitle)}
                    ${provider ? this.renderMaterialField('结构化来源', provider) : ''}
                    ${summary ? `<div class="rounded-md border border-slate-200 bg-slate-50 p-3 md:col-span-2">
                        <span class="font-bold text-slate-500">摘要</span>
                        <div class="mt-1 whitespace-pre-wrap text-slate-800">${this.escape(summary)}</div>
                    </div>` : ''}
                </div>
                <div class="mt-4 flex items-center justify-between gap-3">
                    <div class="text-xs font-black uppercase tracking-wider text-slate-400">最终口播正文</div>
                    ${hasScript ? `<div class="text-xs font-bold text-slate-400">${script.length} 字</div>` : ''}
                </div>
                <pre class="mt-4 max-h-[34rem] overflow-auto whitespace-pre-wrap rounded-md bg-slate-950 p-4 text-sm leading-7 text-slate-50">${this.escape(script || '等待生成。')}</pre>
            </div>
        `;
    }

    renderMaterialField(label, value, multiline = false) {
        const text = String(value || '').trim();
        if (!text) return '';
        return `
            <div class="rounded-md border border-slate-200 bg-slate-50 p-3">
                <span class="font-bold text-slate-500">${this.escape(label)}</span>
                <div class="mt-1 ${multiline ? 'whitespace-pre-wrap' : 'truncate'} font-black text-slate-900" title="${this.escape(text)}">${this.escape(text)}</div>
            </div>
        `;
    }

    renderPager(prefix, page, hasMore) {
        const hasPrevious = page > 1;
        return `
            <div class="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                <button type="button" data-${prefix}-page="prev" ${hasPrevious ? '' : 'disabled'} class="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 font-bold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40">
                    <i class="fas fa-chevron-left mr-1"></i>上一页
                </button>
                <div class="flex shrink-0 items-center gap-2">
                    <span class="font-bold text-slate-500">第 ${page} 页${hasMore ? ' · 还有更多' : ''}</span>
                    <form class="flex items-center gap-1" data-${prefix}-page-jump>
                        <input name="${prefix}_page" type="number" min="1" value="${page}" class="w-16 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-center text-xs font-bold text-slate-700 focus:border-slate-500 focus:outline-none">
                        <button type="submit" class="rounded-md bg-slate-900 px-2.5 py-1.5 font-bold text-white hover:bg-slate-700">跳页</button>
                    </form>
                </div>
                <button type="button" data-${prefix}-page="next" ${hasMore ? '' : 'disabled'} class="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 font-bold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40">
                    下一页<i class="fas fa-chevron-right ml-1"></i>
                </button>
            </div>
        `;
    }

    renderJobPager() {
        const pages = Number(this.jobPagination.pages || 0);
        const total = Number(this.jobPagination.total || 0);
        const currentPage = Math.max(1, Number(this.jobPagination.page || this.jobPage || 1));
        const safePages = Math.max(1, pages || 1);
        return `
            <div class="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                <button type="button" data-job-page="prev" ${currentPage > 1 ? '' : 'disabled'} class="rounded-md border border-slate-200 bg-white px-3 py-1.5 font-bold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40">
                    <i class="fas fa-chevron-left mr-1"></i>上一页
                </button>
                <div class="flex flex-wrap items-center justify-center gap-2">
                    <span class="font-bold text-slate-500">第 ${currentPage}/${safePages} 页 · 共 ${total} 条</span>
                    <form class="flex items-center gap-1.5" data-job-page-jump>
                        <input name="job_page" type="number" min="1" max="${safePages}" value="${currentPage}" class="h-8 w-20 rounded-md border border-slate-200 bg-white px-2 text-center text-xs font-bold text-slate-700 focus:border-slate-500 focus:outline-none">
                        <button type="submit" class="h-8 rounded-md border border-slate-200 bg-white px-2.5 text-xs font-bold text-slate-600 hover:bg-slate-50">跳转</button>
                    </form>
                </div>
                <button type="button" data-job-page="next" ${currentPage < safePages ? '' : 'disabled'} class="rounded-md border border-slate-200 bg-white px-3 py-1.5 font-bold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40">
                    下一页<i class="fas fa-chevron-right ml-1"></i>
                </button>
            </div>
        `;
    }

    assetScopeLabel() {
        return this.assetScope === 'hot' ? '热门 PDF ' : '已同步 PDF ';
    }

    materialTaskMeta(asset) {
        if (!asset?.material_task_id) {
            return {
                text: '未生成基础文案',
                detail: '',
                className: 'bg-slate-100 text-slate-500',
                dot: '',
            };
        }
        const normalized = { pending: 'queued', processing: 'running', completed: 'succeeded' }[asset.material_task_status] || asset.material_task_status;
        const statusMeta = this.statusMeta(normalized);
        const source = asset.material_task_feature_label || '物料';
        const chars = Number(asset.material_script_chars || 0);
        const text = asset.material_has_script
            ? `基础文案 ${statusMeta.label}`
            : `${source} ${statusMeta.label}`;
        const detail = [
            chars ? `${chars} 字` : '',
            asset.material_completed_at ? `完成 ${this.formatDateTime(asset.material_completed_at)}` : '',
        ].filter(Boolean).join(' · ');
        return {
            text,
            detail,
            className: statusMeta.className,
            dot: statusMeta.dot,
        };
    }

    statusMeta(status) {
        const map = {
            queued: { label: '排队中', className: 'bg-amber-50 text-amber-700', dot: 'bg-amber-500', bar: 'bg-amber-500' },
            running: { label: '运行中', className: 'bg-blue-50 text-blue-700', dot: 'bg-blue-500', bar: 'bg-blue-600' },
            succeeded: { label: '成功', className: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', bar: 'bg-slate-900' },
            completed: { label: '成功', className: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', bar: 'bg-slate-900' },
            failed: { label: '失败', className: 'bg-red-50 text-red-700', dot: 'bg-red-500', bar: 'bg-red-500' },
            canceled: { label: '已取消', className: 'bg-slate-100 text-slate-600', dot: 'bg-slate-400', bar: 'bg-slate-400' },
            deferred: { label: '延后', className: 'bg-violet-50 text-violet-700', dot: 'bg-violet-500', bar: 'bg-violet-500' },
        };
        return map[status] || { label: status || '等待', className: 'bg-slate-100 text-slate-600', dot: 'bg-slate-400', bar: 'bg-slate-500' };
    }

    statusLabel(status) {
        return {
            pending: '排队中',
            queued: '排队中',
            processing: '运行中',
            running: '运行中',
            completed: '成功',
            succeeded: '成功',
            failed: '失败',
            canceled: '已取消',
            deferred: '延后',
        }[status] || status || '未知';
    }

    statusClass(status) {
        const normalized = { pending: 'queued', processing: 'running', completed: 'succeeded' }[status] || status;
        return {
            queued: 'bg-amber-50 text-amber-700',
            running: 'bg-blue-50 text-blue-700',
            succeeded: 'bg-emerald-50 text-emerald-700',
            failed: 'bg-red-50 text-red-700',
            canceled: 'bg-slate-100 text-slate-600',
            deferred: 'bg-violet-50 text-violet-700',
        }[normalized] || 'bg-slate-100 text-slate-600';
    }

    setNotice(message, type = 'muted') {
        const classMap = {
            success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
            error: 'border-red-200 bg-red-50 text-red-800',
            warning: 'border-amber-200 bg-amber-50 text-amber-800',
            muted: 'border-slate-200 bg-white text-slate-700',
        };
        this.els.notice.className = `mb-4 rounded-lg border px-4 py-3 text-sm ${classMap[type] || classMap.muted}`;
        this.els.notice.textContent = message;
        this.els.notice.classList.remove('hidden');
    }

    setButtonBusy(button, html) {
        if (!button) return () => {};
        const originalHtml = button.innerHTML;
        const originalDisabled = button.disabled;
        button.disabled = true;
        button.innerHTML = html;
        return () => {
            if (!button.isConnected) return;
            button.disabled = originalDisabled;
            button.innerHTML = originalHtml;
        };
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

    absoluteApiUrl(path) {
        const raw = String(path || '').trim();
        if (!raw) return '';
        if (/^https?:\/\//i.test(raw)) return raw;
        if (raw.startsWith('/')) return `${this.apiRoot()}${raw}`;
        return `${this.apiRoot()}/${raw}`;
    }

    formatTime(value) {
        if (!value) return '-';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return date.toLocaleString('zh-CN', { hour12: false });
    }

    escape(value) {
        return String(value ?? '').replace(/[&<>"']/g, (char) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[char]));
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.reportScriptMaterialWorkflowPage = new ReportScriptMaterialWorkflowPage();
});
