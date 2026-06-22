class Mp3VideoWorkflowPage {
    constructor() {
        this.audioAssets = [];
        this.audioAssetPage = 1;
        this.audioAssetPerPage = 10;
        this.audioAssetPagination = { page: 1, per_page: 10, has_more: false };
        this.audioAssetScope = 'all';
        this.audioAssetSearchTimer = null;
        this.audioAssetRequestSeq = 0;
        this.audioAssetWorkflowSeq = 0;
        this.selectedAudioAsset = null;
        this.jobs = [];
        this.jobPage = this.initialJobPage();
        this.jobStatus = this.initialJobStatus();
        this.jobSearch = this.initialJobSearch();
        this.jobSearchTimer = null;
        this.jobPerPage = 10;
        this.jobPagination = { page: 1, per_page: 10, total: 0, pages: 0 };
        this.historyRequestSeq = 0;
        this.pollTimer = null;
        this.els = {
            providerStatus: document.getElementById('provider-status'),
            workerStatus: document.getElementById('worker-status'),
            refreshStatusBtn: document.getElementById('refresh-status-btn'),
            restartWorkerBtn: document.getElementById('restart-worker-btn'),
            notice: document.getElementById('notice'),
            selectedBadge: document.getElementById('selected-badge'),
            audioAssetTabs: document.getElementById('audio-asset-tabs'),
            audioAssetSearch: document.getElementById('audio-asset-search'),
            audioAssetRefreshBtn: document.getElementById('audio-asset-refresh-btn'),
            audioAssetSummary: document.getElementById('audio-asset-summary'),
            audioAssetList: document.getElementById('audio-asset-list'),
            audioAssetPagination: document.getElementById('audio-asset-pagination'),
            selectedAudioPanel: document.getElementById('selected-audio-panel'),
            selectedAudioTitle: document.getElementById('selected-audio-title'),
            selectedAudioMeta: document.getElementById('selected-audio-meta'),
            selectedAudioPlayer: document.getElementById('selected-audio-player'),
            geminiUrlInput: document.getElementById('gemini-url-input'),
            imagePromptInput: document.getElementById('image-prompt-input'),
            submitBtn: document.getElementById('submit-btn'),
            loadHistoryBtn: document.getElementById('load-history-btn'),
            refreshJobsBtn: document.getElementById('refresh-jobs-btn'),
            pollDelay: document.getElementById('poll-delay'),
            summaryText: document.getElementById('summary-text'),
            jobNameSearch: document.getElementById('job-name-search'),
            jobStatusFilter: document.getElementById('job-status-filter'),
            jobList: document.getElementById('job-list'),
            jobPagination: document.getElementById('job-pagination'),
        };
        this.defaults();
        this.bind();
        this.syncJobStatusFilter();
        this.syncJobSearchInput();
        this.refreshStatus();
        this.loadAudioAssets({ silent: true });
        this.loadHistory({ silent: true });
        this.resetPollTimer();
        this.render();
    }

    defaults() {
        this.els.imagePromptInput.value = [
            '根据以上内容',
            '请创造一张横向排版的信息图，以简短、干净、极简为排版原则；视觉上采用手绘笔记风格，并严格参照《华尔街日报》经典插图的黑白钢笔画、点刻/Hedcut、细密排线表现形式来呈现主题图案；所有说明文字请使用简体中文；排版中不得出现任何机构名称或字符串“FCCNN88”；保持整体手绘质感，图文协调，适合直接用于展示。'
        ].join('\n');
    }

    initialJobPage() {
        const page = Number(new URLSearchParams(window.location.search).get('job_page') || 1);
        return Number.isFinite(page) && page > 0 ? Math.floor(page) : 1;
    }

    initialJobStatus() {
        const params = new URLSearchParams(window.location.search);
        return this.normalizeJobStatus(params.get('status') || params.get('job_status') || '');
    }

    initialJobSearch() {
        const params = new URLSearchParams(window.location.search);
        return this.normalizeJobSearch(params.get('q') || params.get('search') || params.get('job_search') || '');
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

    normalizeJobStatus(status) {
        const value = String(status || '').trim().toLowerCase();
        const map = { pending: 'queued', processing: 'running', completed: 'succeeded', cancelled: 'canceled' };
        const normalized = map[value] || value;
        const allowed = new Set(this.jobStatusOptions().map((item) => item.value));
        return allowed.has(normalized) ? normalized : '';
    }

    jobStatusFilterLabel(status = this.jobStatus) {
        return this.jobStatusOptions().find((item) => item.value === status)?.label || '全部';
    }

    normalizeJobSearch(value) {
        return String(value || '').trim().replace(/\s+/g, ' ').slice(0, 80);
    }

    syncJobStatusFilter() {
        if (!this.els.jobStatusFilter) return;
        const status = this.normalizeJobStatus(this.jobStatus);
        if (this.els.jobStatusFilter.value !== status) {
            this.els.jobStatusFilter.value = status;
        }
    }

    syncJobSearchInput() {
        if (!this.els.jobNameSearch) return;
        const search = this.normalizeJobSearch(this.jobSearch);
        if (this.els.jobNameSearch.value !== search) {
            this.els.jobNameSearch.value = search;
        }
    }

    bind() {
        this.els.refreshStatusBtn.addEventListener('click', () => this.refreshStatus());
        this.els.restartWorkerBtn.addEventListener('click', () => this.restartWorker());
        this.els.audioAssetRefreshBtn.addEventListener('click', () => this.loadAudioAssets({ page: this.audioAssetPage }));
        this.els.audioAssetTabs?.addEventListener('click', (event) => this.handleAudioAssetScopeClick(event));
        this.els.audioAssetSearch.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') this.loadAudioAssets({ page: 1 });
        });
        this.els.audioAssetSearch.addEventListener('input', () => this.scheduleAudioAssetSearch());
        this.els.audioAssetSearch.addEventListener('search', () => this.scheduleAudioAssetSearch());
        this.els.audioAssetList.addEventListener('click', (event) => this.handleAudioAssetClick(event));
        this.els.audioAssetList.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') this.handleAudioAssetClick(event);
        });
        this.els.audioAssetPagination.addEventListener('click', (event) => this.handleAudioAssetPageClick(event));
        this.els.audioAssetPagination.addEventListener('submit', (event) => this.handleAudioAssetPageJump(event));
        this.els.submitBtn.addEventListener('click', () => this.submit());
        this.els.loadHistoryBtn.addEventListener('click', () => this.loadHistory({ page: 1, force: true }));
        this.els.refreshJobsBtn.addEventListener('click', () => this.refreshJobs());
        this.els.pollDelay.addEventListener('change', () => this.resetPollTimer());
        this.els.jobStatusFilter?.addEventListener('change', () => this.handleJobStatusFilterChange());
        this.els.jobNameSearch?.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                this.applyJobSearch({ force: true });
            }
        });
        this.els.jobNameSearch?.addEventListener('input', () => this.scheduleJobSearch());
        this.els.jobNameSearch?.addEventListener('search', () => this.applyJobSearch({ force: true }));
        this.els.jobNameSearch?.addEventListener('change', () => this.applyJobSearch({ force: true }));
        this.els.jobList.addEventListener('click', (event) => this.handleJobAction(event));
        this.els.jobPagination.addEventListener('click', (event) => this.handleJobPageClick(event));
        this.els.jobPagination.addEventListener('submit', (event) => this.handleJobPageJump(event));
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
            const response = await fetch(`${this.apiRoot()}${path}`, {
                ...fetchOptions,
                headers: {
                    ...this.headers(true),
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
            const result = await this.request('/api/mp3-video-workflows/status', { timeoutMs: 10000 });
            const provider = result.data?.provider || {};
            const ds = provider.deepseek_configured ? `DS: ${provider.planner_model || 'deepseek-v4-pro'}` : 'DS 未配，使用兜底';
            if (provider.configured) {
                this.setBadge(this.els.providerStatus, `配置正常 · ${ds}`, 'success');
            } else {
                this.setBadge(this.els.providerStatus, 'Gemini 未配置', 'warning');
            }
        } catch (error) {
            this.setBadge(this.els.providerStatus, '配置检查失败', 'error');
        }
    }

    async refreshWorkerStatus() {
        try {
            const result = await this.request('/api/v1/celery/status', { timeoutMs: 30000 });
            const services = result.data?.services || result.services || [];
            const workflow = services.find((item) => item.id === 'mp3_video_workflow');
            const render = services.find((item) => item.id === 'render');
            if (workflow?.running && render?.running) {
                this.setBadge(this.els.workerStatus, `Worker 正常 · MP3 ${workflow.queue_length || 0} / 渲染 ${render.queue_length || 0}`, 'success');
            } else {
                const missing = [
                    workflow?.running ? null : 'MP3 编排',
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
            await this.request('/api/v1/celery/restart/mp3_video_workflow', { method: 'POST', timeoutMs: 20000 });
            await this.request('/api/v1/celery/restart/render', { method: 'POST', timeoutMs: 20000 });
            this.setNotice('MP3 编排和渲染 worker 已重启。', 'success');
            await this.refreshWorkerStatus();
        } catch (error) {
            this.setNotice(`重启失败：${error.message}`, 'error');
        } finally {
            this.els.restartWorkerBtn.disabled = false;
            this.els.restartWorkerBtn.innerHTML = '<i class="fas fa-rotate-right mr-1.5"></i>重启编排/渲染 Worker';
        }
    }

    scheduleAudioAssetSearch() {
        clearTimeout(this.audioAssetSearchTimer);
        this.audioAssetSearchTimer = setTimeout(() => this.loadAudioAssets({ page: 1, silent: true }), 350);
    }

    async loadAudioAssets({ page = this.audioAssetPage, silent = false } = {}) {
        const seq = ++this.audioAssetRequestSeq;
        const q = this.els.audioAssetSearch.value.trim();
        this.renderAudioAssetTabs();
        if (!silent) this.els.audioAssetSummary.textContent = `加载${this.audioAssetScopeLabel()}中...`;
        try {
            const params = new URLSearchParams({
                page: String(page),
                per_page: String(this.audioAssetPerPage),
                scope: this.audioAssetScope,
            });
            if (q) params.set('q', q);
            const result = await this.request(`/api/mp3-video-workflows/audio-assets?${params.toString()}`, { timeoutMs: 20000 });
            if (seq !== this.audioAssetRequestSeq) return;
            this.audioAssets = (result.data?.items || result.items || []).map((item) => ({
                ...item,
                workflow_loading: true,
            }));
            this.audioAssetPagination = result.data?.pagination || { page, per_page: this.audioAssetPerPage, has_more: false, scope: this.audioAssetScope };
            this.audioAssetPage = this.audioAssetPagination.page || page;
            this.renderAudioAssets();
            this.loadAudioAssetWorkflowStats(this.audioAssets);
        } catch (error) {
            if (seq !== this.audioAssetRequestSeq) return;
            this.els.audioAssetSummary.textContent = `加载失败：${error.message}`;
            this.els.audioAssetList.innerHTML = '';
            this.els.audioAssetPagination.innerHTML = '';
        }
    }

    handleAudioAssetScopeClick(event) {
        const button = event.target.closest('[data-audio-asset-scope]');
        if (!button) return;
        const nextScope = button.dataset.audioAssetScope === 'hot' ? 'hot' : 'all';
        if (nextScope === this.audioAssetScope) return;
        this.audioAssetScope = nextScope;
        this.audioAssetPage = 1;
        this.selectedAudioAsset = null;
        this.updateSelectedAudioPanel();
        this.loadAudioAssets({ page: 1 });
    }

    audioAssetScopeLabel() {
        return this.audioAssetScope === 'hot' ? '热门 MP3 ' : '已同步 MP3 ';
    }

    renderAudioAssetTabs() {
        if (!this.els.audioAssetTabs) return;
        this.els.audioAssetTabs.querySelectorAll('[data-audio-asset-scope]').forEach((button) => {
            const active = button.dataset.audioAssetScope === this.audioAssetScope;
            button.className = active
                ? 'rounded-md border border-slate-900 bg-slate-900 px-3 py-2 text-white shadow-sm transition'
                : 'rounded-md border border-slate-200 bg-white px-3 py-2 text-slate-600 transition hover:bg-slate-50';
        });
    }

    async loadAudioAssetWorkflowStats(items) {
        const ids = (items || []).map((item) => item.attachment_id).filter(Boolean);
        if (!ids.length) return;
        const seq = ++this.audioAssetWorkflowSeq;
        const params = new URLSearchParams({ ids: ids.join(',') });
        try {
            const result = await this.request(`/api/mp3-video-workflows/audio-assets/workflow-stats?${params.toString()}`, { timeoutMs: 30000 });
            if (seq !== this.audioAssetWorkflowSeq) return;
            const stats = result.data?.stats || result.stats || {};
            this.audioAssets = this.audioAssets.map((item) => ({
                ...item,
                workflow_loading: false,
                workflow_load_error: false,
                workflow: stats[item.attachment_id] || item.workflow || null,
            }));
            this.syncSelectedAudioAssetFromList();
            this.renderAudioAssets();
        } catch (error) {
            if (seq !== this.audioAssetWorkflowSeq) return;
            this.audioAssets = this.audioAssets.map((item) => ({
                ...item,
                workflow_loading: false,
                workflow_load_error: true,
            }));
            this.syncSelectedAudioAssetFromList();
            this.renderAudioAssets();
        }
    }

    syncSelectedAudioAssetFromList() {
        if (!this.selectedAudioAsset?.attachment_id) return;
        const current = this.audioAssets.find((item) => item.attachment_id === this.selectedAudioAsset.attachment_id);
        if (current) {
            this.selectedAudioAsset = current;
            this.updateSelectedAudioPanel();
        }
    }

    handleAudioAssetClick(event) {
        const target = event.target.closest('[data-audio-id]');
        if (!target) return;
        const asset = this.audioAssets.find((item) => item.attachment_id === target.dataset.audioId);
        if (!asset) return;
        this.selectedAudioAsset = asset;
        this.updateSelectedAudioPanel();
        this.renderAudioAssets();
    }

    handleAudioAssetPageClick(event) {
        const button = event.target.closest('[data-audio-page]');
        if (!button || button.disabled) return;
        const page = Number(button.dataset.audioPage);
        if (Number.isFinite(page) && page > 0) this.loadAudioAssets({ page });
    }

    handleAudioAssetPageJump(event) {
        event.preventDefault();
        const input = event.target.querySelector('input[name="audio_page"]');
        const page = Number(input?.value || 1);
        if (Number.isFinite(page) && page > 0) this.loadAudioAssets({ page: Math.floor(page) });
    }

    updateSelectedAudioPanel() {
        const asset = this.selectedAudioAsset;
        this.els.selectedAudioPanel.classList.toggle('hidden', !asset);
        if (!asset) {
            this.els.selectedBadge.textContent = '未选择';
            this.els.selectedAudioPlayer.removeAttribute('src');
            return;
        }
        this.els.selectedBadge.textContent = '已选择';
        this.els.selectedAudioTitle.textContent = asset.topic_title || asset.filename || '未命名音频';
        this.els.selectedAudioMeta.textContent = [
            asset.filename || '',
            this.formatBytes(asset.bytes),
            this.audioAssetDownloadText(asset),
            asset.topic_create_time || asset.updated_at || '',
        ].filter(Boolean).join(' · ');
        this.els.selectedAudioPlayer.src = this.absoluteUrl(`/api/mp3-video-workflows/audio-assets/${encodeURIComponent(asset.attachment_id)}/preview`);
    }

    async submit() {
        if (!this.selectedAudioAsset) {
            this.setNotice('请先选择一个已同步 MP3。', 'warning');
            return;
        }
        this.els.submitBtn.disabled = true;
        this.els.submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>投递中';
        try {
            const payload = {
                audio_asset_id: this.selectedAudioAsset.attachment_id,
                gemini_url: this.els.geminiUrlInput.value.trim(),
                image_prompt: this.els.imagePromptInput.value.trim(),
                metadata: {
                    title: this.selectedAudioAsset.topic_title || this.selectedAudioAsset.filename,
                    source: 'mp3-video-workflow.html'
                }
            };
            const result = await this.request('/api/mp3-video-workflows/jobs', {
                method: 'POST',
                body: JSON.stringify(payload),
                timeoutMs: 20000,
            });
            this.setNotice(`任务已投递：${result.job_id || result.data?.job_id}`, 'success');
            this.jobPage = 1;
            await this.loadAudioAssetWorkflowStats(this.audioAssets);
            await this.loadHistory({ page: 1, force: true });
        } catch (error) {
            this.setNotice(`投递失败：${error.message}`, 'error');
        } finally {
            this.els.submitBtn.disabled = false;
            this.els.submitBtn.innerHTML = '<i class="fas fa-paper-plane mr-1.5"></i>投递编排';
        }
    }

    async loadHistory({ page = this.jobPage, silent = false } = {}) {
        const seq = ++this.historyRequestSeq;
        this.jobStatus = this.normalizeJobStatus(this.jobStatus);
        this.jobSearch = this.normalizeJobSearch(this.jobSearch);
        this.syncJobStatusFilter();
        this.syncJobSearchInput();
        if (!silent) this.els.summaryText.textContent = '加载任务中...';
        try {
            const params = new URLSearchParams({ page: String(page), per_page: String(this.jobPerPage) });
            if (this.jobStatus) params.set('status', this.jobStatus);
            if (this.jobSearch) params.set('q', this.jobSearch);
            const result = await this.request(`/api/mp3-video-workflows/jobs?${params.toString()}`, { timeoutMs: 20000 });
            if (seq !== this.historyRequestSeq) return;
            this.jobs = result.data?.items || result.items || [];
            this.jobPagination = result.data?.pagination || { page, per_page: this.jobPerPage, total: this.jobs.length, pages: 1 };
            this.jobPage = this.jobPagination.page || page;
            this.renderJobs();
        } catch (error) {
            if (seq !== this.historyRequestSeq) return;
            this.els.summaryText.textContent = `加载失败：${error.message}`;
            this.els.jobList.innerHTML = '';
            this.els.jobPagination.innerHTML = '';
        }
    }

    handleJobStatusFilterChange() {
        const nextStatus = this.normalizeJobStatus(this.els.jobStatusFilter?.value || '');
        if (nextStatus === this.jobStatus) return;
        this.jobStatus = nextStatus;
        this.jobPage = 1;
        this.updateJobPageUrl(1);
        this.loadHistory({ page: 1, force: true });
    }

    scheduleJobSearch() {
        if (this.jobSearchTimer) clearTimeout(this.jobSearchTimer);
        this.jobSearchTimer = setTimeout(() => this.applyJobSearch(), 350);
    }

    applyJobSearch(options = {}) {
        if (this.jobSearchTimer) {
            clearTimeout(this.jobSearchTimer);
            this.jobSearchTimer = null;
        }
        const nextSearch = this.normalizeJobSearch(this.els.jobNameSearch?.value || '');
        if (!options.force && nextSearch === this.jobSearch) return;
        this.jobSearch = nextSearch;
        this.jobPage = 1;
        this.updateJobPageUrl(1);
        this.loadHistory({ page: 1, force: true });
    }

    async refreshJobs() {
        await this.loadHistory({ page: this.jobPage, force: true });
    }

    handleJobPageClick(event) {
        const button = event.target.closest('[data-job-page]');
        if (!button || button.disabled) return;
        const page = Number(button.dataset.jobPage);
        if (Number.isFinite(page) && page > 0) {
            this.updateJobPageUrl(page);
            this.loadHistory({ page });
        }
    }

    handleJobPageJump(event) {
        event.preventDefault();
        const input = event.target.querySelector('input[name="job_page"]');
        const page = Number(input?.value || 1);
        if (Number.isFinite(page) && page > 0) {
            const targetPage = Math.floor(page);
            this.updateJobPageUrl(targetPage);
            this.loadHistory({ page: targetPage });
        }
    }

    async handleJobAction(event) {
        const button = event.target.closest('[data-action]');
        if (!button || button.disabled) return;
        const jobId = button.dataset.jobId;
        const action = button.dataset.action;
        if (action === 'copy-title') {
            event.preventDefault();
            event.stopPropagation();
            this.copyText(button.dataset.copyText || '', '标题已复制。');
            return;
        }
        try {
            button.disabled = true;
            if (action === 'cancel') {
                await this.request(`/api/mp3-video-workflows/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST', timeoutMs: 12000 });
                this.setNotice('任务已取消。', 'success');
            } else if (action === 'retry') {
                if (!window.confirm('从失败处续跑这个编排？已完成的 Gemini、四张图和视频子任务会尽量复用。')) return;
                const result = await this.request(`/api/mp3-video-workflows/jobs/${encodeURIComponent(jobId)}/retry`, { method: 'POST', timeoutMs: 12000 });
                this.setNotice(result.message || '任务已从失败处续跑。', 'success');
            } else if (action === 'refresh') {
                const result = await this.request(`/api/mp3-video-workflows/jobs/${encodeURIComponent(jobId)}`, { timeoutMs: 12000 });
                const index = this.jobs.findIndex((job) => job.job_id === jobId);
                if (index >= 0) this.jobs[index] = result.data || result;
                this.renderJobs();
                return;
            }
            await this.loadHistory({ page: this.jobPage, force: true });
        } catch (error) {
            this.setNotice(`操作失败：${error.message}`, 'error');
        } finally {
            button.disabled = false;
        }
    }

    async copyText(text, successMessage = '已复制。') {
        const value = String(text || '').trim();
        if (!value) {
            this.setNotice('没有可复制的内容。', 'warning');
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

    resetPollTimer() {
        clearInterval(this.pollTimer);
        const delay = Math.max(1000, Number(this.els.pollDelay.value || 3000));
        this.pollTimer = setInterval(() => {
            const hasActive = this.jobs.some((job) => ['queued', 'running', 'pending', 'processing'].includes(job.status));
            if (hasActive) this.loadHistory({ page: this.jobPage, silent: true });
        }, delay);
    }

    render() {
        this.renderAudioAssets();
        this.renderJobs();
    }

    renderAudioAssets() {
        this.renderAudioAssetTabs();
        const page = this.audioAssetPagination.page || this.audioAssetPage;
        const scanned = this.audioAssetPagination.scanned || 0;
        const skipped = this.audioAssetPagination.skipped_missing || 0;
        this.els.audioAssetSummary.textContent = `${this.audioAssetScopeLabel()}· 第 ${page} 页 · ${this.audioAssets.length} 个可用 MP3 · 扫描 ${scanned} 条 · 跳过缺失 ${skipped} 条`;
        if (!this.audioAssets.length) {
            const emptyText = this.audioAssetScope === 'hot'
                ? '暂无热门 MP3。当前筛选条件是下载次数 ≥ 20，且附件已下载到本地。'
                : '没有找到已下载的 MP3。';
            this.els.audioAssetList.innerHTML = `<div class="rounded-md border border-dashed border-slate-300 p-4 text-sm text-slate-500">${this.escape(emptyText)}</div>`;
        } else {
            this.els.audioAssetList.innerHTML = this.audioAssets.map((asset) => this.renderAudioAssetCard(asset)).join('');
        }
        this.renderAudioAssetPagination();
    }

    renderAudioAssetCard(asset) {
        const selected = this.selectedAudioAsset?.attachment_id === asset.attachment_id;
        const title = this.escape(asset.topic_title || asset.filename || '未命名音频');
        const file = this.escape(asset.filename || '');
        const excerpt = this.escape(asset.topic_content_excerpt || '');
        const downloadText = this.audioAssetDownloadText(asset);
        const workflowMeta = this.audioAssetWorkflowMeta(asset);
        const selectedClass = selected ? 'border-emerald-400 bg-emerald-50 ring-1 ring-emerald-200' : 'border-slate-200 bg-white hover:border-slate-300';
        return `
            <button type="button" data-audio-id="${this.escapeAttr(asset.attachment_id)}" class="block w-full rounded-md border ${selectedClass} p-3 text-left transition" tabindex="0">
                <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                        <div class="line-clamp-2 text-sm font-black text-slate-900">${title}</div>
                        <div class="mt-1 truncate text-xs text-slate-500">${file}</div>
                    </div>
                    <span class="shrink-0 rounded bg-slate-900 px-2 py-1 text-[10px] font-black uppercase text-white">${this.escape(asset.file_ext || 'mp3')}</span>
                </div>
                ${excerpt ? `<div class="mt-2 line-clamp-2 text-xs leading-5 text-slate-500">${excerpt}</div>` : ''}
                <div class="mt-2 flex flex-wrap gap-2 text-[11px] font-bold text-slate-400">
                    <span>${this.formatBytes(asset.bytes)}</span>
                    <span>${this.escape(asset.topic_create_time || asset.updated_at || '')}</span>
                    <span>${this.escape(asset.group_id || '')}</span>
                </div>
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
            </button>
        `;
    }

    audioAssetDownloadText(asset) {
        const count = Number(asset?.download_count);
        return Number.isFinite(count) ? `下载 ${count} 次` : '下载 -';
    }

    audioAssetWorkflowMeta(asset) {
        if (asset.workflow_loading) {
            return {
                latestMeta: null,
                text: '编排状态加载中',
                detail: '',
                className: 'bg-indigo-50 text-indigo-600',
            };
        }
        if (asset.workflow_load_error) {
            return {
                latestMeta: null,
                text: '编排状态待刷新',
                detail: '',
                className: 'bg-amber-50 text-amber-700',
            };
        }

        const workflow = asset.workflow || {};
        const total = Number(workflow.total || 0);
        const succeeded = Number(workflow.succeeded || 0);
        const failed = Number(workflow.failed || 0);
        const running = Number(workflow.running || 0);
        const latest = workflow.latest || null;
        const latestMeta = this.workflowStatusMeta(latest?.status || '');

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

    workflowStatusMeta(status) {
        const map = {
            queued: {
                label: '排队中',
                dot: 'bg-amber-400',
                className: 'bg-amber-50 text-amber-700',
            },
            running: {
                label: '运行中',
                dot: 'bg-blue-500',
                className: 'bg-blue-50 text-blue-700',
            },
            submitting: {
                label: '提交中',
                dot: 'bg-blue-500',
                className: 'bg-blue-50 text-blue-700',
            },
            succeeded: {
                label: '已完成',
                dot: 'bg-emerald-500',
                className: 'bg-emerald-50 text-emerald-700',
            },
            failed: {
                label: '失败',
                dot: 'bg-rose-500',
                className: 'bg-rose-50 text-rose-700',
            },
            canceled: {
                label: '已取消',
                dot: 'bg-slate-400',
                className: 'bg-slate-100 text-slate-600',
            },
        };
        return map[status] || {
            label: status || '未知',
            dot: 'bg-slate-400',
            className: 'bg-slate-100 text-slate-600',
        };
    }

    renderAudioAssetPagination() {
        const page = this.audioAssetPagination.page || 1;
        const hasMore = Boolean(this.audioAssetPagination.has_more);
        this.els.audioAssetPagination.innerHTML = `
            <div class="flex flex-wrap items-center justify-between gap-2 text-xs">
                <div class="font-bold text-slate-500">第 ${page} 页${hasMore ? ' · 还有更多' : ''}</div>
                <div class="flex items-center gap-2">
                    <button data-audio-page="${page - 1}" ${page <= 1 ? 'disabled' : ''} class="rounded-md border border-slate-300 px-3 py-1.5 font-bold text-slate-700 disabled:opacity-40">上一页</button>
                    <button data-audio-page="${page + 1}" ${!hasMore ? 'disabled' : ''} class="rounded-md border border-slate-300 px-3 py-1.5 font-bold text-slate-700 disabled:opacity-40">下一页</button>
                    <form class="flex items-center gap-1">
                        <input name="audio_page" type="number" min="1" value="${page}" class="w-16 rounded-md border border-slate-300 px-2 py-1.5">
                        <button class="rounded-md bg-slate-900 px-2.5 py-1.5 font-bold text-white">跳页</button>
                    </form>
                </div>
            </div>
        `;
    }

    renderJobs() {
        const total = this.jobPagination.total || this.jobs.length;
        const statusLabel = this.jobStatusFilterLabel(this.jobStatus);
        const searchText = this.normalizeJobSearch(this.jobSearch);
        const scopeLabel = [
            searchText ? `搜索“${searchText}”` : '',
            this.jobStatus ? statusLabel : '',
        ].filter(Boolean).join(' · ');
        const running = this.jobs.filter((job) => ['queued', 'running', 'pending', 'processing'].includes(job.status)).length;
        const succeeded = this.jobs.filter((job) => ['succeeded', 'completed'].includes(job.status)).length;
        const failed = this.jobs.filter((job) => job.status === 'failed').length;
        this.syncJobStatusFilter();
        this.syncJobSearchInput();
        this.els.summaryText.textContent = total
            ? `${scopeLabel || '全部'} ${total} 个 MP3 视频编排任务 · 本页 ${this.jobs.length} 个，运行中 ${running}，成功 ${succeeded}，失败 ${failed}。`
            : (scopeLabel ? `暂无${scopeLabel}的 MP3 视频编排任务。` : '还没有任务。');
        if (!this.jobs.length) {
            const emptyText = scopeLabel ? `暂无${scopeLabel}的 MP3 视频编排任务。` : '暂无任务。';
            this.els.jobList.innerHTML = `<div class="rounded-md border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">${this.escape(emptyText)}</div>`;
        } else {
            this.els.jobList.innerHTML = this.jobs.map((job) => this.renderJobCard(job)).join('');
        }
        this.renderJobPagination();
    }

    renderJobCard(job) {
        const title = job.plan?.title || job.request?.audio?.topic_title || job.request?.audio?.filename || job.job_id;
        const audio = job.request?.audio || job.audio || {};
        const status = this.jobStatusMeta(job.status);
        const canCancel = ['queued', 'running'].includes(job.status);
        const canRetry = ['failed', 'canceled'].includes(job.status);
        return `
            <article class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="flex flex-wrap items-start justify-between gap-3">
                    <div class="min-w-0">
                        <div class="flex flex-wrap items-center gap-2">
                            <span class="status-dot ${status.dot}"></span>
                            <h3 class="line-clamp-2 text-base font-black text-slate-900" title="${this.escape(title)}">${this.escape(title)}</h3>
                            <button type="button" data-action="copy-title" data-copy-text="${this.escapeAttr(title)}" class="shrink-0 rounded-md border border-slate-200 bg-white px-1.5 py-1 text-[11px] text-slate-500 hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700" title="复制标题">
                                <i class="fas fa-copy"></i>
                            </button>
                        </div>
                        <div class="mt-1 text-xs text-slate-500">${this.escape(audio.filename || '')} · ${this.escape(job.created_at || '')}</div>
                    </div>
                    <div class="flex flex-wrap items-center gap-2">
                        <span class="rounded-full px-2.5 py-1 text-xs font-bold ${status.className}">${status.label}</span>
                        <button data-action="refresh" data-job-id="${this.escapeAttr(job.job_id)}" class="rounded-md border border-slate-300 px-2.5 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50">刷新</button>
                        ${canCancel ? `<button data-action="cancel" data-job-id="${this.escapeAttr(job.job_id)}" class="rounded-md border border-rose-200 px-2.5 py-1.5 text-xs font-bold text-rose-600 hover:bg-rose-50">取消</button>` : ''}
                        ${canRetry ? `<button data-action="retry" data-job-id="${this.escapeAttr(job.job_id)}" class="rounded-md border border-emerald-200 px-2.5 py-1.5 text-xs font-bold text-emerald-700 hover:bg-emerald-50" title="从失败处续跑">失败续跑</button>` : ''}
                    </div>
                </div>
                <div class="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
                    <div class="h-full rounded-full ${status.bar} transition-all" style="width:${Math.max(0, Math.min(100, job.progress || 0))}%"></div>
                </div>
                <div class="mt-2 flex flex-wrap items-center gap-2 text-xs font-bold text-slate-500">
                    <span class="${status.textClass}">${status.label}</span>
                    <span>${this.escape(job.step || '')}</span>
                    <span>${job.progress || 0}%</span>
                    ${job.error ? `<span class="text-rose-600">${this.escape(job.error)}</span>` : ''}
                </div>
                ${this.renderGeminiRetry(job)}
                ${this.renderPlan(job)}
                ${this.renderDownloads(job)}
                ${this.renderLogs(job.logs || [])}
            </article>
        `;
    }

    renderGeminiRetry(job) {
        const attempts = Array.isArray(job.retry_attempts?.gemini) ? job.retry_attempts.gemini : [];
        const state = job.retry_state?.gemini || {};
        const lastAttempt = attempts[attempts.length - 1] || {};
        const maxAttempts = Number(state.max_attempts || lastAttempt.max_attempts || 0);
        const currentAttempt = Number(state.attempt || lastAttempt.attempt || attempts.length || 0);
        if (!maxAttempts && !attempts.length) return '';
        const failedAttempts = attempts.filter((attempt) => String(attempt?.status || '').toLowerCase() === 'failed').length;
        const statusText = String(state.status || lastAttempt.status || '');
        const summary = [
            `Gemini 自动重试 ${currentAttempt || 1}/${maxAttempts || '?'}`,
            failedAttempts ? `失败尝试 ${failedAttempts}` : '',
            statusText === 'completed' && failedAttempts ? '已恢复' : '',
            statusText === 'failed' ? '待续跑' : '',
        ].filter(Boolean).join(' · ');
        return `
            <div class="mt-3 rounded-md border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                <div class="flex flex-wrap items-center gap-2 font-black">
                    <i class="fas fa-rotate"></i>
                    <span>${this.escape(summary)}</span>
                </div>
                ${attempts.length > 1 ? `
                    <div class="mt-2 grid gap-1 md:grid-cols-2">
                        ${attempts.map((attempt, index) => {
                            const attemptNo = attempt?.attempt || index + 1;
                            const maxNo = attempt?.max_attempts || maxAttempts || '';
                            const error = attempt?.error ? this.compactText(attempt.error, 120) : '';
                            return `
                                <div class="rounded border border-amber-100 bg-white/70 px-2 py-1">
                                    <div class="flex flex-wrap items-center justify-between gap-2">
                                        <span class="font-black">第 ${this.escape(attemptNo)}${maxNo ? `/${this.escape(maxNo)}` : ''} 次</span>
                                        <span>${this.escape(attempt?.status || '-')}</span>
                                    </div>
                                    <div class="mt-0.5 truncate font-mono text-[11px] text-amber-600">${this.escape(attempt?.job_id || '-')}</div>
                                    ${error ? `<div class="mt-0.5 text-rose-700" title="${this.escape(attempt.error)}">${this.escape(error)}</div>` : ''}
                                </div>
                            `;
                        }).join('')}
                    </div>
                ` : ''}
            </div>
        `;
    }

    renderPlan(job) {
        const plan = job.plan || {};
        if (!Object.keys(plan).length && !job.initial_response_text) return '';
        const audio = plan.audio || {};
        const timeText = [
            audio.start_seconds !== undefined ? `起点 ${audio.start_seconds}s` : null,
            audio.end_seconds !== null && audio.end_seconds !== undefined ? `终点 ${audio.end_seconds}s` : '终点自动',
            `倍速 ${audio.speed || 1.2}`
        ].filter(Boolean).join(' · ');
        return `
            <div class="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
                <div class="rounded-md bg-slate-50 p-3">
                    <div class="text-[11px] font-black uppercase text-slate-400">DS 规划</div>
                    <div class="mt-1 text-sm font-bold text-slate-900">${this.escape(plan.title || '等待规划')}</div>
                    <div class="mt-1 text-xs leading-5 text-slate-500">${this.escape(plan.subtitle || '')}</div>
                    <div class="mt-2 text-xs font-bold text-slate-500">${this.escape(timeText)}</div>
                </div>
                <div class="rounded-md bg-slate-50 p-3">
                    <div class="text-[11px] font-black uppercase text-slate-400">Gemini 第一轮</div>
                    <div class="mt-1 max-h-24 overflow-auto text-xs leading-5 text-slate-600">${this.escape(job.initial_response_text || job.gemini?.initial_response_text || '等待返回')}</div>
                </div>
            </div>
        `;
    }

    renderDownloads(job) {
        const links = [];
        if (job.download_input_audio_url) links.push(['原始 MP3', job.download_input_audio_url]);
        if (job.download_infographic_url) links.push(['Gemini 信息图', job.download_infographic_url]);
        Object.entries(job.artifact_download_urls || {}).forEach(([key, url]) => {
            const label = job.artifacts?.[key]?.label || key;
            links.push([label, url]);
        });
        if (job.download_landscape_video_url) links.push(['横屏视频', job.download_landscape_video_url]);
        if (job.download_vertical_video_url) links.push(['竖屏视频', job.download_vertical_video_url]);
        if (job.download_result_url) links.push(['结果 JSON', job.download_result_url]);
        if (!links.length) return '';
        return `
            <div class="mt-3 flex flex-wrap gap-2">
                ${links.map(([label, url]) => `<a href="${this.absoluteUrl(url)}" target="_blank" class="rounded-md border border-slate-300 px-2.5 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50">${this.escape(label)}</a>`).join('')}
            </div>
        `;
    }

    renderLogs(logs) {
        const recent = logs.slice(-5).reverse();
        if (!recent.length) return '';
        return `
            <div class="mt-3 rounded-md border border-slate-100 bg-slate-50 p-3">
                <div class="mb-1 text-[11px] font-black uppercase text-slate-400">最近日志</div>
                <div class="space-y-1">
                    ${recent.map((log) => `<div class="text-xs text-slate-500"><span class="font-mono text-slate-400">${this.escape(log.progress ?? '')}%</span> ${this.escape(log.message || log.step || '')}</div>`).join('')}
                </div>
            </div>
        `;
    }

    renderJobPagination() {
        const page = this.jobPagination.page || 1;
        const pages = this.jobPagination.pages || 0;
        const total = this.jobPagination.total || 0;
        this.els.jobPagination.innerHTML = `
            <div class="flex flex-wrap items-center justify-between gap-2 text-xs">
                <div class="font-bold text-slate-500">第 ${page} 页 / ${pages || 1} 页 · ${total} 条</div>
                <div class="flex items-center gap-2">
                    <button data-job-page="${page - 1}" ${page <= 1 ? 'disabled' : ''} class="rounded-md border border-slate-300 px-3 py-1.5 font-bold text-slate-700 disabled:opacity-40">上一页</button>
                    <button data-job-page="${page + 1}" ${pages && page >= pages ? 'disabled' : ''} class="rounded-md border border-slate-300 px-3 py-1.5 font-bold text-slate-700 disabled:opacity-40">下一页</button>
                    <form class="flex items-center gap-1">
                        <input name="job_page" type="number" min="1" value="${page}" class="w-16 rounded-md border border-slate-300 px-2 py-1.5">
                        <button class="rounded-md bg-slate-900 px-2.5 py-1.5 font-bold text-white">跳页</button>
                    </form>
                </div>
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
        const status = this.normalizeJobStatus(this.jobStatus);
        if (status) {
            url.searchParams.set('status', status);
        } else {
            url.searchParams.delete('status');
        }
        url.searchParams.delete('job_status');
        const search = this.normalizeJobSearch(this.jobSearch);
        if (search) {
            url.searchParams.set('q', search);
        } else {
            url.searchParams.delete('q');
        }
        url.searchParams.delete('search');
        url.searchParams.delete('job_search');
    }

    setBadge(el, text, type) {
        const classes = {
            success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
            warning: 'border-amber-200 bg-amber-50 text-amber-700',
            error: 'border-rose-200 bg-rose-50 text-rose-700',
        };
        el.className = `rounded-lg border px-3 py-2 text-xs font-bold ${classes[type] || 'border-slate-200 bg-white text-slate-500'}`;
        el.textContent = text;
    }

    setNotice(message, type = 'info') {
        const classes = {
            success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
            warning: 'border-amber-200 bg-amber-50 text-amber-800',
            error: 'border-rose-200 bg-rose-50 text-rose-800',
            info: 'border-slate-200 bg-white text-slate-700',
        };
        this.els.notice.className = `mb-4 rounded-lg border px-4 py-3 text-sm ${classes[type] || classes.info}`;
        this.els.notice.textContent = message;
        this.els.notice.classList.remove('hidden');
    }

    statusDotClass(status) {
        if (status === 'succeeded' || status === 'completed') return 'bg-emerald-500';
        if (status === 'failed') return 'bg-rose-500';
        if (status === 'canceled') return 'bg-slate-400';
        if (status === 'queued') return 'bg-amber-400';
        return 'bg-blue-500';
    }

    jobStatusMeta(status) {
        const map = {
            queued: {
                label: '排队中',
                dot: 'bg-amber-400',
                className: 'bg-amber-50 text-amber-700',
                textClass: 'text-amber-700',
                bar: 'bg-amber-500',
            },
            running: {
                label: '运行中',
                dot: 'bg-blue-500',
                className: 'bg-blue-50 text-blue-700',
                textClass: 'text-blue-700',
                bar: 'bg-blue-600',
            },
            pending: {
                label: '排队中',
                dot: 'bg-amber-400',
                className: 'bg-amber-50 text-amber-700',
                textClass: 'text-amber-700',
                bar: 'bg-amber-500',
            },
            processing: {
                label: '运行中',
                dot: 'bg-blue-500',
                className: 'bg-blue-50 text-blue-700',
                textClass: 'text-blue-700',
                bar: 'bg-blue-600',
            },
            succeeded: {
                label: '成功',
                dot: 'bg-emerald-500',
                className: 'bg-emerald-50 text-emerald-700',
                textClass: 'text-emerald-700',
                bar: 'bg-emerald-600',
            },
            completed: {
                label: '成功',
                dot: 'bg-emerald-500',
                className: 'bg-emerald-50 text-emerald-700',
                textClass: 'text-emerald-700',
                bar: 'bg-emerald-600',
            },
            failed: {
                label: '失败',
                dot: 'bg-rose-500',
                className: 'bg-rose-50 text-rose-700',
                textClass: 'text-rose-700',
                bar: 'bg-rose-600',
            },
            canceled: {
                label: '已取消',
                dot: 'bg-slate-400',
                className: 'bg-slate-100 text-slate-600',
                textClass: 'text-slate-600',
                bar: 'bg-slate-400',
            },
        };
        return map[status] || {
            label: status || '未知',
            dot: 'bg-slate-400',
            className: 'bg-slate-100 text-slate-600',
            textClass: 'text-slate-600',
            bar: 'bg-slate-500',
        };
    }

    statusLabel(status) {
        return {
            queued: '排队中',
            running: '运行中',
            succeeded: '成功',
            completed: '成功',
            failed: '失败',
            canceled: '已取消',
        }[status] || status || '未知';
    }

    absoluteUrl(path) {
        if (!path) return '#';
        if (/^https?:\/\//.test(path)) return path;
        return `${this.apiRoot()}${path}`;
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

    compactText(value, maxLength = 120) {
        const text = String(value ?? '').replace(/\s+/g, ' ').trim();
        if (text.length <= maxLength) return text;
        return `${text.slice(0, Math.max(0, maxLength - 1))}…`;
    }

    escape(value) {
        return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        }[ch]));
    }

    escapeAttr(value) {
        return this.escape(value).replace(/`/g, '&#096;');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.mp3VideoWorkflowPage = new Mp3VideoWorkflowPage();
});
