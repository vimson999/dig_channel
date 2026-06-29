class ReportVisualPlanTestPage {
    constructor() {
        this.assets = [];
        this.assetPage = 1;
        this.assetPerPage = 10;
        this.assetScope = 'all';
        this.assetPagination = { page: 1, per_page: 10, has_more: false };
        this.assetSearchTimer = null;
        this.currentPreview = null;
        this.els = {
            providerStatus: document.getElementById('provider-status'),
            refreshBtn: document.getElementById('refresh-btn'),
            notice: document.getElementById('notice'),
            forceRefresh: document.getElementById('force-refresh'),
            assetTabs: document.getElementById('asset-tabs'),
            assetSearch: document.getElementById('asset-search'),
            assetRefreshBtn: document.getElementById('asset-refresh-btn'),
            assetSummary: document.getElementById('asset-summary'),
            assetList: document.getElementById('asset-list'),
            assetPagination: document.getElementById('asset-pagination'),
            previewSummary: document.getElementById('preview-summary'),
            previewBadge: document.getElementById('preview-badge'),
            previewPanel: document.getElementById('preview-panel'),
        };
        this.bind();
        this.refreshStatus();
        this.loadAssets({ silent: true });
        this.renderAssetTabs();
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
        const { timeoutMs = 20000, ...fetchOptions } = options;
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
        this.els.refreshBtn.addEventListener('click', () => {
            this.refreshStatus();
            this.loadAssets({ page: this.assetPage });
        });
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
    }

    async refreshStatus() {
        try {
            const result = await this.request('/api/report-visual-plans/status', { timeoutMs: 10000 });
            const provider = result.data?.provider || {};
            const ok = Boolean(provider.configured);
            this.els.providerStatus.className = `rounded-lg border px-3 py-2 text-xs font-bold ${ok ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`;
            this.els.providerStatus.textContent = ok ? `DS 已配置 · ${provider.model || 'deepseek-v4-pro'}` : 'DS 未配置 · 本地兜底';
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

    handleAssetScopeClick(event) {
        const button = event.target.closest('[data-asset-scope]');
        if (!button) return;
        this.assetScope = button.dataset.assetScope || 'all';
        this.assetPage = 1;
        this.renderAssetTabs();
        this.loadAssets({ page: 1 });
    }

    handleAssetClick(event) {
        const button = event.target.closest('[data-generate-asset-id]');
        if (!button) return;
        this.generatePreview(button.dataset.generateAssetId, button);
    }

    handleAssetPageClick(event) {
        const button = event.target.closest('[data-page]');
        if (!button) return;
        const page = Math.max(1, Number(button.dataset.page || 1));
        this.loadAssets({ page });
    }

    handleAssetPageJump(event) {
        event.preventDefault();
        const input = event.target.querySelector('input[name="page"]');
        const page = Math.max(1, Number(input?.value || 1));
        this.loadAssets({ page });
    }

    async generatePreview(assetId, button) {
        const asset = this.assets.find((item) => String(item.id) === String(assetId));
        if (!asset?.material_has_script) {
            this.setNotice('这份 PDF 还没有基础文案，先去基础文案物料台生成。', 'warning');
            return;
        }
        button.disabled = true;
        const oldHtml = button.innerHTML;
        button.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>生成中';
        this.setPreviewLoading(asset);
        try {
            const result = await this.request('/api/report-visual-plans/preview', {
                method: 'POST',
                body: JSON.stringify({
                    asset_id: assetId,
                    force: Boolean(this.els.forceRefresh.checked),
                }),
                timeoutMs: 140000,
            });
            this.currentPreview = result.data || {};
            this.renderPreview(result.data || {}, { cached: Boolean(result.cached), asset });
            this.setNotice(result.cached ? '已复用已有 visual_plan 预览。' : 'visual_plan 已生成。', 'success');
        } catch (error) {
            this.setNotice(`生成 visual_plan 失败：${error.message}`, 'error');
            this.renderPreviewError(error);
        } finally {
            button.disabled = false;
            button.innerHTML = oldHtml;
        }
    }

    setPreviewLoading(asset) {
        this.els.previewBadge.className = 'rounded-full bg-blue-50 px-3 py-1 text-xs font-black text-blue-700';
        this.els.previewBadge.textContent = '生成中';
        this.els.previewSummary.textContent = this.compactTitle(asset?.topic_title || asset?.filename || '');
        this.els.previewPanel.innerHTML = `
            <div class="rounded-lg border border-blue-100 bg-blue-50 p-8 text-center text-sm font-bold text-blue-700">
                <i class="fas fa-spinner fa-spin mr-2"></i>正在生成 visual_plan 与预览图...
            </div>
        `;
    }

    renderAssets() {
        const totalHint = this.assetPagination.has_more ? '更多' : String(this.assets.length);
        this.els.assetSummary.textContent = `${this.assetScope === 'hot' ? '热门 PDF' : '全部 PDF'} · 第 ${this.assetPage} 页 · 本页 ${this.assets.length} 个`;
        if (!this.assets.length) {
            this.els.assetList.innerHTML = '<div class="rounded-md border border-dashed border-slate-300 bg-slate-50 p-5 text-center text-sm text-slate-400">没有找到 PDF。</div>';
            this.renderAssetPagination();
            return;
        }
        this.els.assetList.innerHTML = this.assets.map((asset) => this.renderAssetItem(asset)).join('');
        this.renderAssetPagination(totalHint);
    }

    renderAssetItem(asset) {
        const title = this.compactTitle(asset.topic_title || asset.filename || 'PDF');
        const filename = this.compactTitle(asset.filename || '', 62);
        const hasMaterial = Boolean(asset.material_has_script);
        const badge = hasMaterial
            ? `<span class="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-black text-emerald-700"><i class="fas fa-circle-check mr-1"></i>基础文案 ${asset.material_script_chars || 0} 字</span>`
            : `<span class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-bold text-slate-500">暂无基础文案</span>`;
        const previewUrl = asset.preview_url ? `${this.apiRoot()}${asset.preview_url}` : '';
        return `
            <article class="rounded-lg border ${hasMaterial ? 'border-slate-200 hover:border-blue-300' : 'border-slate-200 opacity-75'} bg-white p-3 transition">
                <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                        <div class="truncate text-sm font-black text-slate-900" title="${this.escape(asset.topic_title || asset.filename || '')}">${this.escape(title)}</div>
                        <div class="mt-1 truncate text-xs text-slate-500" title="${this.escape(asset.filename || '')}">${this.escape(filename)}</div>
                        <div class="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                            <span><i class="fas fa-download mr-1"></i>下载 ${Number(asset.download_count || 0)} 次</span>
                            ${badge}
                        </div>
                    </div>
                    <div class="flex shrink-0 items-center gap-1">
                        ${previewUrl ? `<a href="${this.escape(previewUrl)}" target="_blank" rel="noopener noreferrer" class="rounded-md border border-indigo-100 bg-indigo-50 px-2 py-1 text-[11px] font-bold text-indigo-700 hover:bg-indigo-100"><i class="fas fa-eye mr-1"></i>PDF</a>` : ''}
                        <button data-generate-asset-id="${this.escape(asset.id)}" ${hasMaterial ? '' : 'disabled'} class="rounded-md bg-slate-900 px-2.5 py-1.5 text-[11px] font-black text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300">
                            <i class="fas fa-wand-magic-sparkles mr-1"></i>预览
                        </button>
                    </div>
                </div>
            </article>
        `;
    }

    renderAssetTabs() {
        this.els.assetTabs.querySelectorAll('[data-asset-scope]').forEach((button) => {
            const active = button.dataset.assetScope === this.assetScope;
            button.className = `rounded-md border px-3 py-2 transition ${active ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'}`;
        });
    }

    renderAssetPagination() {
        const page = Number(this.assetPagination.page || this.assetPage || 1);
        const hasPrev = page > 1;
        const hasNext = Boolean(this.assetPagination.has_more);
        this.els.assetPagination.innerHTML = `
            <div class="flex items-center justify-between gap-3">
                <button data-page="${page - 1}" ${hasPrev ? '' : 'disabled'} class="rounded-md border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40">上一页</button>
                <form class="flex items-center gap-2 text-xs text-slate-500">
                    <span>第</span>
                    <input name="page" type="number" min="1" value="${page}" class="w-16 rounded-md border border-slate-200 px-2 py-1.5 text-center text-xs font-bold text-slate-700">
                    <span>页</span>
                    <button class="rounded-md border border-slate-200 px-2 py-1.5 font-bold text-slate-600 hover:bg-slate-50">跳转</button>
                </form>
                <button data-page="${page + 1}" ${hasNext ? '' : 'disabled'} class="rounded-md border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40">下一页</button>
            </div>
        `;
    }

    renderPreview(job, context = {}) {
        const assets = job.preview_assets || [];
        const opening = assets.find((item) => item.kind === 'opening_cover');
        const logic = assets.find((item) => item.kind === 'logic_card');
        const plan = job.visual_plan || {};
        this.els.previewBadge.className = `rounded-full px-3 py-1 text-xs font-black ${context.cached ? 'bg-amber-50 text-amber-700' : 'bg-emerald-50 text-emerald-700'}`;
        this.els.previewBadge.textContent = context.cached ? '缓存命中' : '已生成';
        this.els.previewSummary.textContent = `${job.title || context.asset?.topic_title || ''} · ${job.provider || plan.provider || ''}`;
        this.els.previewPanel.innerHTML = `
            <div class="space-y-4">
                ${job.provider_error ? `<div class="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">DS 调用未完成，已使用本地兜底：${this.escape(job.provider_error)}</div>` : ''}
                <div class="grid grid-cols-1 gap-4 2xl:grid-cols-2">
                    ${this.renderPreviewImage('开场封面', opening)}
                    ${this.renderPreviewImage('逻辑图解卡', logic)}
                </div>
                <details class="rounded-lg border border-slate-200 bg-slate-50">
                    <summary class="cursor-pointer px-4 py-3 text-sm font-black text-slate-700">visual_plan JSON</summary>
                    <pre class="max-h-[34rem] overflow-auto border-t border-slate-200 p-4 text-xs leading-5 text-slate-700">${this.escape(JSON.stringify(plan, null, 2))}</pre>
                </details>
            </div>
        `;
    }

    renderPreviewImage(title, asset) {
        if (!asset?.url) {
            return `<div class="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-8 text-center text-sm text-slate-400">${this.escape(title)}未生成</div>`;
        }
        const url = `${this.apiRoot()}${asset.url}`;
        return `
            <figure class="rounded-lg border border-slate-200 bg-slate-50 p-3">
                <div class="mb-2 flex items-center justify-between gap-2">
                    <figcaption class="text-sm font-black text-slate-700">${this.escape(title)}</figcaption>
                    <a href="${this.escape(url)}" target="_blank" rel="noopener noreferrer" class="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs font-bold text-slate-600 hover:bg-slate-50">
                        <i class="fas fa-up-right-from-square mr-1"></i>打开
                    </a>
                </div>
                <img src="${this.escape(url)}" alt="${this.escape(title)}" class="mx-auto max-h-[760px] rounded-md object-contain shadow-sm">
            </figure>
        `;
    }

    renderPreviewError(error) {
        this.els.previewBadge.className = 'rounded-full bg-red-50 px-3 py-1 text-xs font-black text-red-700';
        this.els.previewBadge.textContent = '失败';
        this.els.previewPanel.innerHTML = `
            <div class="rounded-lg border border-red-200 bg-red-50 p-6 text-sm font-bold text-red-700">
                ${this.escape(error.message)}
            </div>
        `;
    }

    setNotice(message, type = 'muted') {
        const classes = {
            success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
            error: 'border-red-200 bg-red-50 text-red-800',
            warning: 'border-amber-200 bg-amber-50 text-amber-800',
            muted: 'border-slate-200 bg-white text-slate-600',
        };
        this.els.notice.className = `mb-4 rounded-lg border px-4 py-3 text-sm ${classes[type] || classes.muted}`;
        this.els.notice.textContent = message;
        this.els.notice.classList.remove('hidden');
    }

    compactTitle(value, max = 42) {
        const text = String(value || '').replace(/\s+/g, ' ').trim();
        return text.length > max ? `${text.slice(0, max - 1)}…` : text;
    }

    escape(value) {
        return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[ch]));
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.reportVisualPlanTestPage = new ReportVisualPlanTestPage();
});
