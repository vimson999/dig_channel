class ReportPdfEvidencePanel {
    constructor() {
        this.jobId = new URLSearchParams(window.location.search).get('job_id') || '';
        this.detailRoot = document.getElementById('detail-root');
        this.root = null;
        this.data = null;
        this.objectUrls = [];
        this.mountObserver = null;
        if (!this.detailRoot || !this.jobId) return;
        this.mountObserver = new MutationObserver(() => this.ensureMounted());
        this.mountObserver.observe(this.detailRoot, { childList: true });
        window.addEventListener('beforeunload', () => {
            this.mountObserver?.disconnect();
            this.clearObjectUrls();
            this.closeImagePreview();
        });
        this.ensureMounted();
    }

    ensureMounted() {
        if (this.root?.isConnected && this.root.parentElement === this.detailRoot) return;
        const anchor = (
            this.detailRoot.querySelector('#content-results')
            || this.detailRoot.querySelector('#media-assets')
            || this.detailRoot.querySelector('#production-tools')
        );
        if (!anchor) return;
        this.clearObjectUrls();
        const root = document.createElement('div');
        root.id = 'pdf-evidence-panel';
        root.dataset.navSection = '';
        root.className = 'scroll-mt-24';
        anchor.insertAdjacentElement('afterend', root);
        this.root = root;
        this.root.addEventListener('click', (event) => this.onClick(event));
        this.root.addEventListener('change', (event) => this.onChange(event));
        if (this.data) {
            this.render();
            this.loadImages();
        } else {
            this.load();
        }
        window.reportAudioWorkflowDetailPage?.setupQuickNavigation?.();
    }

    apiRoot() {
        const configured = window.API_BASE_URL || window.CONFIG?.API_BASE_URL || '';
        if (configured) return configured.replace(/\/api\/v1\/?$/, '').replace(/\/$/, '');
        return `${window.location.protocol}//${window.location.hostname}:5001`;
    }

    headers(json = false) {
        const token = localStorage.getItem('access_token') || localStorage.getItem('token') || '';
        return {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
            ...(json ? { 'Content-Type': 'application/json' } : {}),
        };
    }

    async request(path, options = {}) {
        const response = await fetch(`${this.apiRoot()}${path}`, {
            ...options,
            headers: { ...this.headers(Boolean(options.body)), ...(options.headers || {}) },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.success === false) throw new Error(payload.message || `HTTP ${response.status}`);
        return payload.data;
    }

    async load() {
        this.root.innerHTML = this.loadingHtml();
        try {
            this.data = await this.request(`/api/report-pdf-evidence/tasks/${encodeURIComponent(this.jobId)}`);
            this.render();
            await this.loadImages();
        } catch (error) {
            this.root.innerHTML = this.shell(`<div class="rounded-lg bg-rose-50 p-3 text-sm text-rose-700">证据映射加载失败：${this.escape(error.message)}</div>`);
        }
    }

    render() {
        const data = this.data || {};
        if (data.status === 'disabled') {
            this.root.innerHTML = this.shell(`
                <div class="rounded-lg border border-slate-200 bg-slate-50 p-5 text-sm text-slate-600">
                    <div class="font-bold"><i class="fas fa-circle-pause mr-2 text-slate-400"></i>本任务未启用 PDF 证据映射</div>
                    <div class="mt-1 text-xs text-slate-500">新投递默认关闭；基础文案、语音、分镜和成片流程不受影响。</div>
                </div>
            `);
            return;
        }
        if (data.status === 'pending_generation') {
            this.root.innerHTML = this.shell(`
                <div class="rounded-lg border border-blue-200 bg-blue-50 p-5 text-sm text-blue-700">
                    <div class="font-bold"><i class="fas fa-spinner fa-spin mr-2"></i>等待基础文案完成后生成证据映射</div>
                    <div class="mt-1 text-xs text-blue-600">当前步骤 ${this.escape(data.source_task_step || 'processing')} · ${Number(data.source_task_progress || 0)}%</div>
                </div>
            `);
            return;
        }
        if (data.status === 'legacy_unavailable') {
            this.root.innerHTML = this.shell(`
                <div class="rounded-lg border border-dashed border-slate-200 bg-slate-50 p-5 text-sm text-slate-500">
                    这是旧任务，尚无 PDF 证据映射；不会自动重跑或改变原文案。
                </div>
            `);
            return;
        }
        if (data.status === 'persistence_failed' || data.status === 'generation_failed') {
            const generated = data.status === 'persistence_failed';
            const artifact = data.artifact_summary || {};
            this.root.innerHTML = this.shell(`
                <div class="rounded-lg border ${generated ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-rose-200 bg-rose-50 text-rose-700'} p-5 text-sm">
                    <div class="font-bold">${generated ? '证据已经生成，但数据库保存失败' : 'PDF 证据映射生成失败'}</div>
                    <div class="mt-1 text-xs">${this.escape(data.error || '请查看任务日志后重试。')}</div>
                    ${generated ? `<div class="mt-2 text-xs">已保留 ${Number(artifact.candidate_count || 0)} 条候选、${Number(artifact.claim_count || 0)} 条本地验证结果。</div>` : ''}
                </div>
            `);
            return;
        }
        const claims = Array.isArray(data.claims) ? data.claims : [];
        const statusClass = data.status === 'ready'
            ? 'bg-emerald-50 text-emerald-700'
            : (data.status === 'partial' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-600');
        const body = `
            <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
                <div class="flex flex-wrap items-center gap-2 text-xs">
                    <span class="rounded-full px-2.5 py-1 font-black ${statusClass}">${this.escape(data.status || 'unavailable')}</span>
                    <span class="text-slate-500">可用证据 ${Number(data.verified_claim_count || 0)}/${Number(data.claim_count || 0)} · 文案覆盖 ${(Number(data.coverage_ratio || 0) * 100).toFixed(1)}%</span>
                    <span class="rounded-full bg-slate-100 px-2.5 py-1 font-bold text-slate-600">截图源：${data.visual_pdf_source === 'content_pdf_clean' ? '清洗后 PDF' : '原 PDF（安全回退）'}</span>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                    <button data-evidence-action="select-all-visuals" class="rounded-lg bg-violet-700 px-3 py-2 text-xs font-bold text-white hover:bg-violet-800"><i class="fas fa-check-double mr-1"></i>全部加入证据候选池</button>
                    <button data-evidence-action="clear-all-visuals" class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50">全部取消</button>
                    <label class="flex items-center gap-2 rounded-lg border border-violet-200 bg-violet-50 px-3 py-2 text-xs font-bold text-violet-800">
                        <input data-evidence-visuals-enabled type="checkbox" ${data.visuals_enabled ? 'checked' : ''} class="h-4 w-4 rounded border-violet-300 text-violet-700">
                        新分镜启用已选证据画面
                    </label>
                </div>
            </div>
            <div class="mb-3 rounded-lg border border-violet-100 bg-violet-50 px-3 py-2 text-xs text-violet-700">
                候选证据会按分镜文案匹配使用；当前每个分镜最多采用一张，不代表每张候选图都会进入最终成片。
            </div>
            ${data.error ? `<div class="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">${this.escape(data.error)}</div>` : ''}
            <div class="space-y-3">
                ${claims.length ? claims.map((claim) => this.claimHtml(claim, data.page_count)).join('') : '<div class="rounded-lg border border-dashed border-slate-200 p-5 text-sm text-slate-500">未发现可验证证据候选；现有视觉生成不受影响。</div>'}
            </div>
        `;
        this.root.innerHTML = this.shell(body);
    }

    shell(body) {
        return `
            <section class="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <div class="mb-3 flex items-start justify-between gap-3">
                    <div>
                        <h2 class="text-base font-black text-slate-950"><i class="fas fa-file-shield mr-2 text-violet-600"></i>PDF 证据映射</h2>
                        <p class="mt-1 text-xs text-slate-500">核对主张对应的 PDF 页和截图区域；证据不进入 TTS 文案。</p>
                    </div>
                    <button data-evidence-action="refresh" class="shrink-0 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50"><i class="fas fa-rotate mr-1"></i>刷新</button>
                </div>
                ${body}
            </section>
        `;
    }

    loadingHtml() {
        return this.shell('<div class="py-8 text-center text-sm text-slate-400"><i class="fas fa-spinner fa-spin mr-2"></i>加载证据映射</div>');
    }

    claimHtml(claim, pageCount) {
        const crop = claim.crop || {};
        const verified = ['verified', 'manual_verified'].includes(claim.verification_status);
        const statusLabel = claim.verification_status === 'manual_verified'
            ? '人工确认'
            : claim.verification_status;
        return `
            <article data-evidence-claim="${this.escapeAttr(claim.id)}" class="grid gap-3 rounded-xl border border-slate-200 bg-white p-3 lg:grid-cols-[17rem_minmax(0,1fr)]">
                <div class="overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
                    <button type="button" data-evidence-action="preview-image" data-evidence-image-id="${this.escapeAttr(claim.id)}" title="点击放大证据截图" class="aspect-video grid w-full cursor-zoom-in place-items-center text-xs text-slate-400 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-violet-500">
                        <img data-evidence-image="${this.escapeAttr(claim.id)}" alt="PDF 第 ${Number(claim.pdf_page || 0)} 页证据截图" class="hidden h-full w-full object-contain">
                        <span data-evidence-image-placeholder><i class="fas fa-file-pdf mr-1"></i>PDF 第 ${Number(claim.pdf_page || 0)} 页</span>
                    </button>
                    <div class="flex items-center justify-end gap-2 border-t border-slate-200 bg-white px-2 py-1.5">
                        <button type="button" data-evidence-action="preview-image" data-evidence-image-id="${this.escapeAttr(claim.id)}" class="rounded px-2 py-1 text-[11px] font-bold text-violet-700 hover:bg-violet-50"><i class="fas fa-up-right-and-down-left-from-center mr-1"></i>放大</button>
                        <button type="button" data-evidence-action="download-image" data-evidence-image-id="${this.escapeAttr(claim.id)}" data-evidence-image-sequence="${Number(claim.sequence || 0)}" class="rounded px-2 py-1 text-[11px] font-bold text-slate-600 hover:bg-slate-100"><i class="fas fa-download mr-1"></i>下载</button>
                    </div>
                </div>
                <div class="min-w-0 space-y-2">
                    <div class="flex flex-wrap items-center gap-2">
                        <span class="rounded-full px-2 py-0.5 text-[11px] font-black ${verified ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}">${this.escape(statusLabel)}</span>
                        <span class="text-[11px] text-slate-400">置信度 ${(Number(claim.confidence || 0) * 100).toFixed(1)}%</span>
                        ${claim.user_edited ? '<span class="text-[11px] font-bold text-violet-600">人工已编辑</span>' : ''}
                    </div>
                    <label class="block text-xs font-bold text-slate-600">主张<input data-evidence-field="claim_text" value="${this.escapeAttr(claim.claim_text || '')}" class="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-xs font-normal"></label>
                    <label class="block text-xs font-bold text-slate-600">PDF 原文<input data-evidence-field="pdf_quote" value="${this.escapeAttr(claim.pdf_quote || '')}" class="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-xs font-normal"></label>
                    <div class="grid grid-cols-2 gap-2 sm:grid-cols-5">
                        <label class="text-[11px] font-bold text-slate-500">页码 / ${Number(pageCount || 0)}<input data-evidence-field="pdf_page" type="number" min="1" max="${Number(pageCount || 1)}" value="${Number(claim.pdf_page || 1)}" class="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5"></label>
                        ${['x', 'y', 'width', 'height'].map((key) => `<label class="text-[11px] font-bold text-slate-500">${key}<input data-evidence-field="crop_${key}" type="number" min="0" max="1" step="0.01" value="${Number(crop[key] ?? (key === 'width' || key === 'height' ? 1 : 0))}" class="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5"></label>`).join('')}
                    </div>
                    <div class="flex flex-wrap items-center justify-between gap-2">
                        <div class="flex flex-wrap gap-3 text-xs font-bold text-slate-600">
                            <label class="flex items-center gap-1.5"><input data-evidence-field="enabled" type="checkbox" ${claim.enabled ? 'checked' : ''}>保留证据</label>
                            <label class="flex items-center gap-1.5"><input data-evidence-field="use_as_visual" type="checkbox" ${claim.use_as_visual ? 'checked' : ''}>作为证据画面</label>
                        </div>
                        <button data-evidence-action="save-claim" class="rounded-md bg-violet-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-violet-800">保存并加入候选池</button>
                    </div>
                </div>
            </article>
        `;
    }

    async onClick(event) {
        const button = event.target.closest('[data-evidence-action]');
        if (!button) return;
        if (button.dataset.evidenceAction === 'refresh') return this.load();
        if (button.dataset.evidenceAction === 'preview-image') {
            return this.showImagePreview(button.dataset.evidenceImageId);
        }
        if (button.dataset.evidenceAction === 'download-image') {
            return this.downloadImage(
                button.dataset.evidenceImageId,
                Number(button.dataset.evidenceImageSequence || 0),
                button,
            );
        }
        if (button.dataset.evidenceAction === 'select-all-visuals') {
            return this.applyBulkVisualAction(button, 'select_all_visuals');
        }
        if (button.dataset.evidenceAction === 'clear-all-visuals') {
            return this.applyBulkVisualAction(button, 'clear_all_visuals');
        }
        if (button.dataset.evidenceAction !== 'save-claim') return;
        const card = button.closest('[data-evidence-claim]');
        if (!card) return;
        button.disabled = true;
        try {
            const body = {};
            card.querySelectorAll('[data-evidence-field]').forEach((input) => {
                const key = input.dataset.evidenceField;
                body[key] = input.type === 'checkbox' ? input.checked : (input.type === 'number' ? Number(input.value) : input.value);
            });
            body.enabled = true;
            body.use_as_visual = true;
            body.manual_confirmed = true;
            await this.request(`/api/report-pdf-evidence/claims/${encodeURIComponent(card.dataset.evidenceClaim)}`, {
                method: 'PATCH',
                body: JSON.stringify(body),
            });
            await this.load();
        } catch (error) {
            alert(`证据保存失败：${error.message}`);
        } finally {
            button.disabled = false;
        }
    }

    async onChange(event) {
        if (event.target.matches('[data-evidence-field="use_as_visual"]')) {
            return this.updateClaimVisualSelection(event.target);
        }
        if (!event.target.matches('[data-evidence-visuals-enabled]')) return;
        try {
            await this.request(`/api/report-pdf-evidence/tasks/${encodeURIComponent(this.jobId)}`, {
                method: 'PATCH',
                body: JSON.stringify({ visuals_enabled: event.target.checked }),
            });
            await this.load();
        } catch (error) {
            event.target.checked = !event.target.checked;
            alert(`证据画面设置失败：${error.message}`);
        }
    }

    async applyBulkVisualAction(button, bulkAction) {
        button.disabled = true;
        try {
            this.data = await this.request(`/api/report-pdf-evidence/tasks/${encodeURIComponent(this.jobId)}`, {
                method: 'PATCH',
                body: JSON.stringify({ bulk_action: bulkAction }),
            });
            this.render();
            await this.loadImages();
        } catch (error) {
            alert(`批量设置失败：${error.message}`);
        } finally {
            button.disabled = false;
        }
    }

    async updateClaimVisualSelection(input) {
        const card = input.closest('[data-evidence-claim]');
        if (!card) return;
        input.disabled = true;
        try {
            await this.request(`/api/report-pdf-evidence/claims/${encodeURIComponent(card.dataset.evidenceClaim)}`, {
                method: 'PATCH',
                body: JSON.stringify({
                    enabled: true,
                    use_as_visual: input.checked,
                    manual_confirmed: input.checked,
                }),
            });
            await this.load();
        } catch (error) {
            input.checked = !input.checked;
            alert(`证据画面设置失败：${error.message}`);
        } finally {
            input.disabled = false;
        }
    }

    async loadImages() {
        this.clearObjectUrls();
        const claims = Array.isArray(this.data?.claims) ? this.data.claims : [];
        await Promise.all(claims.filter((claim) => claim.pdf_page).map(async (claim) => {
            try {
                const response = await fetch(`${this.apiRoot()}/api/report-pdf-evidence/claims/${encodeURIComponent(claim.id)}/image?inline=1`, { headers: this.headers() });
                if (!response.ok) return;
                const url = URL.createObjectURL(await response.blob());
                this.objectUrls.push(url);
                const image = this.root.querySelector(`[data-evidence-image="${CSS.escape(claim.id)}"]`);
                if (!image) return;
                image.src = url;
                image.classList.remove('hidden');
                image.parentElement.querySelector('[data-evidence-image-placeholder]')?.classList.add('hidden');
            } catch (_) {}
        }));
    }

    showImagePreview(claimId) {
        const image = this.root.querySelector(`[data-evidence-image="${CSS.escape(claimId || '')}"]`);
        if (!image?.src || image.classList.contains('hidden')) return;
        this.closeImagePreview();
        const modal = document.createElement('div');
        modal.dataset.evidenceImageModal = '';
        modal.className = 'fixed inset-0 z-[1000] flex items-center justify-center bg-slate-950/85 p-4 backdrop-blur-sm';
        modal.innerHTML = `
            <div class="relative flex max-h-full max-w-[96vw] flex-col overflow-hidden rounded-xl bg-white shadow-2xl">
                <div class="flex items-center justify-between border-b border-slate-200 px-4 py-2">
                    <span class="text-sm font-bold text-slate-700">PDF 证据截图</span>
                    <button type="button" data-evidence-close-modal class="rounded-lg px-3 py-1.5 text-sm font-bold text-slate-600 hover:bg-slate-100"><i class="fas fa-xmark mr-1"></i>关闭</button>
                </div>
                <div class="min-h-0 overflow-auto bg-slate-100 p-3">
                    <img src="${this.escapeAttr(image.src)}" alt="${this.escapeAttr(image.alt || 'PDF 证据截图')}" class="max-w-none rounded bg-white shadow">
                </div>
            </div>
        `;
        modal.addEventListener('click', (event) => {
            if (event.target === modal || event.target.closest('[data-evidence-close-modal]')) {
                this.closeImagePreview();
            }
        });
        this.previewKeyHandler = (event) => {
            if (event.key === 'Escape') this.closeImagePreview();
        };
        document.addEventListener('keydown', this.previewKeyHandler);
        document.body.appendChild(modal);
        document.body.style.overflow = 'hidden';
    }

    closeImagePreview() {
        document.querySelector('[data-evidence-image-modal]')?.remove();
        if (this.previewKeyHandler) {
            document.removeEventListener('keydown', this.previewKeyHandler);
            this.previewKeyHandler = null;
        }
        document.body.style.overflow = '';
    }

    async downloadImage(claimId, sequence, button) {
        if (!claimId) return;
        button.disabled = true;
        try {
            const response = await fetch(
                `${this.apiRoot()}/api/report-pdf-evidence/claims/${encodeURIComponent(claimId)}/image`,
                { headers: this.headers() },
            );
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const url = URL.createObjectURL(await response.blob());
            const link = document.createElement('a');
            link.href = url;
            link.download = `pdf-evidence-${String(sequence || 0).padStart(3, '0')}.png`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) {
            alert(`证据截图下载失败：${error.message}`);
        } finally {
            button.disabled = false;
        }
    }

    clearObjectUrls() {
        this.objectUrls.forEach((url) => URL.revokeObjectURL(url));
        this.objectUrls = [];
    }

    escape(value) {
        return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
    }

    escapeAttr(value) {
        return this.escape(value).replace(/`/g, '&#96;');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.reportPdfEvidencePanel = new ReportPdfEvidencePanel();
});
