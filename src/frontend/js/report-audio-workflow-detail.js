class ReportAudioWorkflowDetailPage {
    constructor() {
        this.jobId = new URLSearchParams(window.location.search).get('job_id') || '';
        this.job = null;
        this.refreshTimer = null;
        this.autoRefreshMs = 6000;
        this.biliPanel = null;
        this.biliPanelDraft = null;
        this.biliPanelDraftJobId = '';
        this.verticalVideoRequestIds = new Set();
        this.podcastCoverRequestIds = new Set();
        this.coverRegeneratingIds = new Set();
        this.els = {
            jobIdLabel: document.getElementById('job-id-label'),
            title: document.getElementById('detail-title'),
            subtitle: document.getElementById('detail-subtitle'),
            refreshBtn: document.getElementById('refresh-detail-btn'),
            openApiJson: document.getElementById('open-api-json'),
            notice: document.getElementById('notice'),
            root: document.getElementById('detail-root'),
        };
        this.bind();
        this.load();
    }

    bind() {
        this.els.refreshBtn?.addEventListener('click', () => this.load({ manual: true }));
        window.addEventListener('beforeunload', () => this.clearAutoRefresh());
        this.els.root?.addEventListener('click', (event) => {
            const actionButton = event.target.closest('[data-detail-action]');
            if (actionButton) {
                event.preventDefault();
                event.stopPropagation();
                this.handleDetailAction(actionButton);
                return;
            }
            const button = event.target.closest('[data-copy-text]');
            if (!button) return;
            this.copyText(button.dataset.copyText || '');
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

    headers() {
        const token = this.token();
        return token ? { Authorization: `Bearer ${token}` } : {};
    }

    async request(path, options = {}) {
        const { timeoutMs = 20000, ...fetchOptions } = options;
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(`${this.apiRoot()}${path}`, {
                ...fetchOptions,
                headers: {
                    ...this.headers(),
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

    async load(options = {}) {
        if (!this.jobId) {
            this.renderMissingJob();
            return;
        }
        if (options.manual) this.clearAutoRefresh();
        this.els.refreshBtn.disabled = true;
        this.els.refreshBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>刷新中';
        this.els.jobIdLabel.textContent = `任务 ${this.jobId}`;
        this.els.openApiJson.href = this.absoluteUrl(`/api/report-audio-workflows/jobs/${encodeURIComponent(this.jobId)}`);
        if (!this.job) {
            this.els.root.innerHTML = '<div class="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">加载详情中...</div>';
        }
        try {
            const result = await this.request(`/api/report-audio-workflows/jobs/${encodeURIComponent(this.jobId)}`, { timeoutMs: 30000 });
            this.job = result.data || result;
            this.render();
            this.scheduleAutoRefresh();
            this.setNotice('', 'info', true);
        } catch (error) {
            this.setNotice(`加载失败：${error.message}`, 'error');
        } finally {
            this.els.refreshBtn.disabled = false;
            this.els.refreshBtn.innerHTML = '<i class="fas fa-rotate mr-1.5"></i>刷新详情';
        }
    }

    clearAutoRefresh() {
        if (this.refreshTimer) {
            clearTimeout(this.refreshTimer);
            this.refreshTimer = null;
        }
    }

    scheduleAutoRefresh() {
        this.clearAutoRefresh();
        const activeJob = ['queued', 'running', 'submitting'].includes(String(this.job?.status || '').toLowerCase());
        const uploadItems = Array.isArray(this.job?.bilibili_uploads) ? [...this.job.bilibili_uploads] : [];
        const localUpload = this.biliPanel?.lastUpload;
        if (localUpload && !uploadItems.some((item) => item.task_id === localUpload.task_id)) {
            uploadItems.push(localUpload);
        }
        const activeUpload = uploadItems.some((item) => (
            ['queued', 'pending', 'running', 'processing', 'submitting'].includes(String(item?.status || '').toLowerCase())
        ));
        if (!activeJob && !activeUpload) return;
        this.refreshTimer = setTimeout(() => {
            this.refreshTimer = null;
            if (activeJob) {
                this.load();
            } else {
                this.refreshBilibiliUploads();
            }
        }, this.autoRefreshMs);
    }

    async refreshBilibiliUploads() {
        if (!this.jobId) return;
        try {
            const result = await this.request(`/api/report-audio-workflows/jobs/${encodeURIComponent(this.jobId)}`, { timeoutMs: 30000 });
            this.job = result.data || result;
            this.biliPanel?.updateJobData?.(this.job);
            this.scheduleAutoRefresh();
        } catch (error) {
            this.setNotice(`刷新 B站上传记录失败：${error.message}`, 'warning');
            this.scheduleAutoRefresh();
        }
    }

    renderMissingJob() {
        this.els.title.textContent = '缺少任务 ID';
        this.els.subtitle.textContent = '请从研报转语音编排台点击“详情新页”打开。';
        this.els.root.innerHTML = '<div class="rounded-lg border border-amber-200 bg-amber-50 p-6 text-sm font-bold text-amber-800">URL 中没有 job_id。</div>';
    }

    render() {
        const job = this.job || {};
        const title = this.displayTitle(job) || job.job_id || '研报转语音详情';
        const biliPanelDraft = this.captureBilibiliPanelDraft(job);
        this.els.title.textContent = title;
        this.els.subtitle.textContent = [
            job.pdf?.filename || job.request?.pdf?.filename || '',
            job.created_at ? `创建 ${this.formatDateTime(job.created_at)}` : '',
            job.completed_at ? `完成 ${this.formatDateTime(job.completed_at)}` : '',
        ].filter(Boolean).join(' · ');
        this.els.root.innerHTML = [
            this.renderSummary(job),
            this.renderActions(job),
            this.renderDownloads(job),
            this.renderMedia(job),
            this.renderAudioScript(job),
            this.renderStructured(job),
            this.renderChildren(job),
            '<div id="report-bilibili-publish-panel"></div>',
            this.renderRaw(job),
        ].join('');
        this.mountBilibiliPanel(job, biliPanelDraft);
    }

    handleDetailAction(button) {
        const jobId = button.dataset.jobId || this.jobId;
        const action = button.dataset.detailAction;
        if (action === 'generate-vertical-preview') {
            this.generateVerticalVideo(jobId, {
                previewSeconds: Number(button.dataset.previewSeconds || 10),
                force: true,
            });
        }
        if (action === 'generate-vertical-full') {
            this.generateVerticalVideo(jobId, {
                previewSeconds: 0,
                force: true,
            });
        }
        if (action === 'generate-podcast-cover') {
            this.generatePodcastCover(jobId, { force: false });
        }
        if (action === 'regenerate-cover') {
            this.regenerateCover(jobId);
        }
    }

    captureBilibiliPanelDraft(job) {
        const nextJobId = String(job?.job_id || job?.id || this.jobId || '');
        const currentJobId = String(this.biliPanel?.job?.job_id || this.biliPanel?.job?.id || this.biliPanelDraftJobId || '');
        const sameJob = !currentJobId || !nextJobId || currentJobId === nextJobId;
        if (sameJob && this.biliPanel?.readFormDraft) {
            this.biliPanelDraft = this.biliPanel.readFormDraft() || this.biliPanelDraft;
            this.biliPanelDraftJobId = nextJobId;
        }
        if (this.biliPanelDraftJobId && nextJobId && this.biliPanelDraftJobId !== nextJobId) {
            this.biliPanelDraft = null;
            this.biliPanelDraftJobId = nextJobId;
        }
        return this.biliPanelDraft ? { ...this.biliPanelDraft } : null;
    }

    mountBilibiliPanel(job, formDraft = null) {
        const container = document.getElementById('report-bilibili-publish-panel');
        if (!container || !window.BilibiliPublishPanel) return;
        if (!this.biliPanel || this.biliPanel.container !== container) {
            this.biliPanel = new window.BilibiliPublishPanel({
                container,
                apiRoot: () => this.apiRoot(),
                request: (path, options) => this.request(path, options),
                onNotice: (message, type) => this.setNotice(message, type),
                uploadPath: (jobId) => `/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}/bilibili-upload`,
                cancelPath: (jobId, uploadTaskId) => `/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}/bilibili-upload/${encodeURIComponent(uploadTaskId)}/cancel`,
                uploads: (payload) => payload.bilibili_uploads || [],
                videoOptions: (payload) => this.biliVideoOptions(payload),
                coverOptions: (payload, videoKey) => this.biliCoverOptions(payload, videoKey),
                defaultCoverKey: (payload, videoKey) => this.defaultBiliCoverKey(payload, videoKey),
                defaultTitle: (payload) => this.defaultBiliTitle(payload),
                defaultTags: '财经,音频,研报解读',
                emptyDescription: '当前任务还没有完整视频文件。请先生成横屏或竖屏视频后再投递 B站。',
                onAfterSubmit: async () => this.refreshBilibiliUploads(),
                onAfterCancel: async () => this.refreshBilibiliUploads(),
            });
        }
        this.biliPanel.setJob(job, { formDraft });
        this.biliPanelDraft = this.biliPanel.readFormDraft?.() || formDraft || null;
        this.biliPanelDraftJobId = String(job?.job_id || job?.id || this.jobId || '');
    }

    renderSummary(job) {
        const status = this.statusMeta(job.status);
        const pdfName = job.pdf?.filename || job.request?.pdf?.filename || 'PDF';
        return `
            <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                    <div class="min-w-0">
                        <div class="flex flex-wrap items-center gap-2">
                            <span class="status-dot ${status.dot}"></span>
                            <span class="rounded-full px-2.5 py-1 text-xs font-bold ${status.className}">${this.escape(status.label)}</span>
                            <span class="text-xs font-bold text-slate-400">${this.escape(job.step || '')} · ${Number(job.progress || 0)}%</span>
                        </div>
                        <div class="mt-3 text-sm font-bold text-slate-900">${this.escape(pdfName)}</div>
                        <div class="mt-1 text-xs leading-5 text-slate-500">${this.escape([
                            job.job_id || '',
                            job.audio?.seconds ? `音频 ${this.formatDuration(job.audio.seconds)}` : '',
                            job.video?.vertical_video_scheme ? `竖屏 ${job.video.vertical_video_scheme}` : '',
                        ].filter(Boolean).join(' · '))}</div>
                    </div>
                    <div class="flex flex-wrap gap-2">
                        ${job.pdf?.preview_url ? `<a href="${this.absoluteUrl(job.pdf.preview_url)}" target="_blank" rel="noopener noreferrer" class="rounded-md border border-slate-300 bg-white px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50"><i class="fas fa-file-pdf mr-1"></i>PDF 预览</a>` : ''}
                        <button type="button" data-copy-text="${this.escapeAttr(this.displayTitle(job))}" class="rounded-md border border-slate-300 bg-white px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50"><i class="fas fa-copy mr-1"></i>复制标题</button>
                    </div>
                </div>
                <div class="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
                    <div class="h-full rounded-full ${status.bar}" style="width: ${Math.min(100, Math.max(0, Number(job.progress || 0)))}%"></div>
                </div>
                ${job.error ? `<div class="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">${this.escape(job.error)}</div>` : ''}
            </section>
        `;
    }

    renderActions(job) {
        const jobId = job.job_id || this.jobId || '';
        if (!jobId) return '';
        const video = job.video || {};
        const audioUrl = job.download_audio_url || job.audio?.download_url || '';
        const videoUrl = job.download_video_url || video?.video?.download_url || '';
        const coverUrl = job.download_cover_url || video?.cover?.download_url || '';
        const verticalVideoUrl = job.download_vertical_video_url || video?.vertical_video?.download_url || '';
        const verticalPreviewVideoUrl = video?.vertical_preview_video?.download_url || '';
        const verticalCoverUrl = job.download_vertical_cover_url || video?.vertical_cover?.download_url || '';
        const podcastCoverUrl = job.download_podcast_cover_url || video?.podcast_cover?.download_url || '';
        const bilibiliCoverUrl = this.bilibiliCoverUrl(job);
        const verticalChild = job.publisher_vertical_video || job.children?.publisher_vertical_video;
        const previewChild = job.publisher_vertical_preview_video || job.children?.publisher_vertical_preview_video;
        const verticalActive = this.isActiveStatus(verticalChild?.status) || this.isActiveStatus(previewChild?.status) || this.verticalVideoRequestIds.has(jobId);
        const podcastGenerating = this.podcastCoverRequestIds.has(jobId);
        const coverRegenerating = this.coverRegeneratingIds.has(jobId);
        const scheme = this.normalizeVerticalScheme(
            job.request?.video?.vertical_video_scheme
            || job.request?.video?.vertical_scheme
            || video.vertical_video_scheme
            || video.vertical_preview_scheme
            || 'scheme1'
        );
        const canRequestVertical = Boolean(audioUrl || videoUrl);
        return `
            <section class="rounded-lg border border-indigo-100 bg-indigo-50/60 p-4 shadow-sm">
                <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div>
                        <div class="text-sm font-black text-indigo-950">视频操作区</div>
                        <div class="mt-1 text-xs text-indigo-700">复用列表页同一套接口，不重新跑 Gemini 和豆包朗读。</div>
                    </div>
                    ${!canRequestVertical ? `<span class="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-bold text-amber-700">需要先有音频或视频文件</span>` : ''}
                </div>
                <div class="flex flex-wrap items-center gap-2">
                    ${coverUrl ? `<a href="${this.absoluteUrl(coverUrl)}" download class="rounded-md border border-indigo-200 bg-white px-3 py-1.5 text-xs font-bold text-indigo-700 hover:bg-indigo-50"><i class="fas fa-image mr-1"></i>封面</a>` : ''}
                    <button type="button" data-detail-action="regenerate-cover" data-job-id="${this.escapeAttr(jobId)}" ${coverRegenerating ? 'disabled' : ''} class="rounded-md border border-indigo-200 bg-white px-3 py-1.5 text-xs font-bold text-indigo-700 hover:bg-indigo-50 disabled:cursor-wait disabled:opacity-60">
                        <i class="fas ${coverRegenerating ? 'fa-spinner fa-spin' : 'fa-wand-magic-sparkles'} mr-1"></i>${coverRegenerating ? '生成中' : '重做封面'}
                    </button>
                    ${videoUrl ? `<a href="${this.absoluteUrl(videoUrl)}" download class="rounded-md bg-indigo-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-indigo-800"><i class="fas fa-film mr-1"></i>横屏 MP4</a>` : ''}
                    ${verticalVideoUrl ? `<a href="${this.absoluteUrl(verticalVideoUrl)}" download class="rounded-md bg-fuchsia-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-fuchsia-800"><i class="fas fa-mobile-screen mr-1"></i>竖屏 MP4</a>` : ''}
                    ${verticalPreviewVideoUrl ? `<a href="${this.absoluteUrl(verticalPreviewVideoUrl)}" download class="rounded-md border border-fuchsia-200 bg-white px-3 py-1.5 text-xs font-bold text-fuchsia-700 hover:bg-fuchsia-50"><i class="fas fa-clock mr-1"></i>10秒预览</a>` : ''}
                    ${verticalCoverUrl ? `<a href="${this.absoluteUrl(verticalCoverUrl)}" download class="rounded-md border border-emerald-200 bg-white px-3 py-1.5 text-xs font-bold text-emerald-700 hover:bg-emerald-50"><i class="fas fa-image-portrait mr-1"></i>竖版封皮</a>` : ''}
                    ${podcastCoverUrl ? `<a href="${this.absoluteUrl(podcastCoverUrl)}" download class="rounded-md border border-amber-200 bg-white px-3 py-1.5 text-xs font-bold text-amber-700 hover:bg-amber-50"><i class="fas fa-record-vinyl mr-1"></i>播客封皮</a>` : ''}
                    ${bilibiliCoverUrl ? `<a href="${this.absoluteUrl(bilibiliCoverUrl)}" download class="rounded-md border border-pink-200 bg-white px-3 py-1.5 text-xs font-bold text-pink-700 hover:bg-pink-50"><i class="fas fa-tv mr-1"></i>B站封皮</a>` : ''}
                    ${!podcastCoverUrl ? `<button type="button" data-detail-action="generate-podcast-cover" data-job-id="${this.escapeAttr(jobId)}" ${podcastGenerating ? 'disabled' : ''} class="rounded-md border border-amber-200 bg-white px-3 py-1.5 text-xs font-bold text-amber-700 hover:bg-amber-50 disabled:cursor-wait disabled:opacity-60"><i class="fas ${podcastGenerating ? 'fa-spinner fa-spin' : 'fa-record-vinyl'} mr-1"></i>${podcastGenerating ? '封皮生成中' : '补生成播客封皮'}</button>` : ''}
                    <select data-detail-vertical-scheme class="rounded-md border border-fuchsia-200 bg-white px-2 py-1.5 text-xs font-bold text-fuchsia-800 focus:border-fuchsia-400 focus:outline-none">
                        <option value="scheme1" ${scheme === 'scheme1' ? 'selected' : ''}>方案一</option>
                        <option value="scheme2" ${scheme === 'scheme2' ? 'selected' : ''}>方案二</option>
                    </select>
                    <button type="button" data-detail-action="generate-vertical-preview" data-preview-seconds="10" data-job-id="${this.escapeAttr(jobId)}" ${!canRequestVertical || verticalActive ? 'disabled' : ''} class="rounded-md border border-fuchsia-200 bg-white px-3 py-1.5 text-xs font-bold text-fuchsia-700 hover:bg-fuchsia-50 disabled:cursor-wait disabled:opacity-60">
                        <i class="fas ${verticalActive ? 'fa-spinner fa-spin' : 'fa-bolt'} mr-1"></i>${verticalActive ? '竖屏生成中' : '10秒预览'}
                    </button>
                    <button type="button" data-detail-action="generate-vertical-full" data-job-id="${this.escapeAttr(jobId)}" ${!canRequestVertical || verticalActive ? 'disabled' : ''} class="rounded-md bg-fuchsia-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-fuchsia-800 disabled:cursor-wait disabled:opacity-60">
                        <i class="fas fa-mobile-screen-button mr-1"></i>${verticalVideoUrl ? '完整重合成' : '生成完整竖屏'}
                    </button>
                </div>
                ${verticalChild?.error ? `<div class="mt-2 rounded border border-red-100 bg-white/80 px-2 py-1 text-xs text-red-700">完整竖屏失败：${this.escape(verticalChild.error)}</div>` : ''}
                ${previewChild?.error ? `<div class="mt-2 rounded border border-red-100 bg-white/80 px-2 py-1 text-xs text-red-700">10秒预览失败：${this.escape(previewChild.error)}</div>` : ''}
            </section>
        `;
    }

    currentDetailVerticalScheme() {
        const select = this.els.root?.querySelector('[data-detail-vertical-scheme]');
        return this.normalizeVerticalScheme(select?.value || this.job?.request?.video?.vertical_video_scheme || this.job?.video?.vertical_video_scheme);
    }

    async generatePodcastCover(jobId, options = {}) {
        if (!jobId || this.podcastCoverRequestIds.has(jobId)) return;
        const scheme = this.normalizeVerticalScheme(options.scheme || this.currentDetailVerticalScheme());
        this.podcastCoverRequestIds.add(jobId);
        this.render();
        try {
            const result = await this.request(`/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}/generate-podcast-cover`, {
                method: 'POST',
                timeoutMs: 30000,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    scheme,
                    force: options.force === true,
                }),
            });
            this.mergeJobPayload(result.data || result);
            this.render();
            await this.load();
            this.setNotice(result.message || '播客封皮已生成。', 'success');
        } catch (error) {
            this.setNotice(`播客封皮生成失败：${error.message}`, 'error');
        } finally {
            this.podcastCoverRequestIds.delete(jobId);
            this.render();
        }
    }

    async generateVerticalVideo(jobId, options = {}) {
        if (!jobId || this.verticalVideoRequestIds.has(jobId)) return;
        const scheme = this.normalizeVerticalScheme(options.scheme || this.currentDetailVerticalScheme());
        const previewSeconds = Math.max(0, Number(options.previewSeconds || 0));
        const isPreview = previewSeconds > 0;
        this.verticalVideoRequestIds.add(jobId);
        if (this.job) {
            const key = isPreview ? 'publisher_vertical_preview_video' : 'publisher_vertical_video';
            const pendingChild = {
                status: 'queued',
                step: 'queued',
                progress: 0,
                vertical_video_scheme: scheme,
                preview_seconds: previewSeconds,
            };
            this.job[key] = pendingChild;
            this.job.children = { ...(this.job.children || {}), [key]: pendingChild };
            this.render();
        }
        try {
            const result = await this.request(`/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}/generate-vertical-video`, {
                method: 'POST',
                timeoutMs: 20000,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    scheme,
                    preview_seconds: previewSeconds,
                    force: options.force !== false,
                }),
            });
            this.mergeJobPayload(result.data || result);
            this.render();
            await this.load();
            this.setNotice(result.message || `竖屏 MP4 已提交生成（${this.verticalSchemeLabel(scheme)}${isPreview ? ` · ${previewSeconds}s预览` : ''}）。`, 'success');
        } catch (error) {
            const key = isPreview ? 'publisher_vertical_preview_video' : 'publisher_vertical_video';
            if (this.job) {
                this.job[key] = {
                    ...(this.job[key] || {}),
                    status: 'failed',
                    error: error.message,
                };
                this.job.children = { ...(this.job.children || {}), [key]: this.job[key] };
            }
            this.setNotice(`竖屏 MP4 提交失败：${error.message}`, 'error');
            this.render();
        } finally {
            this.verticalVideoRequestIds.delete(jobId);
            this.render();
        }
    }

    async regenerateCover(jobId) {
        if (!jobId || this.coverRegeneratingIds.has(jobId)) return;
        const currentTitle = this.coverTitleForEdit(this.job);
        const title = this.compactMultilineCoverText(window.prompt('封面主标题', currentTitle) || '');
        if (!title) return;
        const subtitle = this.compactMultilineCoverText(window.prompt('封面副标题（可留空）', this.coverSubtitleForEdit(this.job)) || '');
        this.coverRegeneratingIds.add(jobId);
        this.render();
        try {
            const result = await this.request(`/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}/regenerate-cover`, {
                method: 'POST',
                timeoutMs: 30000,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, subtitle }),
            });
            this.mergeJobPayload(result.data || result);
            this.render();
            await this.load();
            this.setNotice(result.message || '封皮已重新生成。', 'success');
        } catch (error) {
            this.setNotice(`封皮重生成失败：${error.message}`, 'error');
        } finally {
            this.coverRegeneratingIds.delete(jobId);
            this.render();
        }
    }

    mergeJobPayload(payload) {
        if (!payload || typeof payload !== 'object') return;
        this.job = { ...(this.job || {}), ...payload };
    }

    renderDownloads(job) {
        const items = this.downloadItems(job);
        if (!items.length) return '';
        return `
            <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="mb-3 text-sm font-black text-slate-900">下载入口</div>
                <div class="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    ${items.map((item) => `
                        <a href="${this.absoluteUrl(item.url)}" target="_blank" rel="noopener noreferrer" class="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-bold text-slate-700 hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-800">
                            <i class="${this.escapeAttr(item.icon)} mr-1.5"></i>${this.escape(item.label)}
                            ${item.bytes ? `<span class="ml-1 font-normal text-slate-400">${this.formatBytes(item.bytes)}</span>` : ''}
                        </a>
                    `).join('')}
                </div>
            </section>
        `;
    }

    renderMedia(job) {
        const audioUrl = job.download_audio_url || job.audio?.download_url || '';
        const videoItems = this.biliVideoOptions(job);
        const coverItems = this.biliCoverOptions(job, 'landscape');
        const keyframeItems = this.keyframeOptions(job);
        if (!audioUrl && !videoItems.length && !coverItems.length && !keyframeItems.length) return '';
        return `
            <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="mb-3 text-sm font-black text-slate-900">封皮 / 音视频预览</div>
                ${audioUrl ? `
                    <div class="mb-4 rounded-md border border-blue-100 bg-blue-50 p-3">
                        <div class="mb-2 text-xs font-bold text-blue-800">音频</div>
                        <audio controls src="${this.absoluteUrl(audioUrl)}" class="w-full"></audio>
                    </div>
                ` : ''}
                ${videoItems.length ? `
                    <div class="grid grid-cols-1 gap-3 lg:grid-cols-2">
                        ${videoItems.map((item) => `
                            <div class="rounded-md border border-slate-200 bg-slate-50 p-3">
                                <div class="mb-2 text-xs font-bold text-slate-600">${this.escape(item.label)}</div>
                                <video controls src="${this.absoluteUrl(item.url)}" class="max-h-[360px] w-full rounded bg-black"></video>
                            </div>
                        `).join('')}
                    </div>
                ` : ''}
                ${coverItems.length ? `
                    <div class="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                        ${coverItems.map((item) => `
                            <a href="${this.absoluteUrl(item.url)}" target="_blank" rel="noopener noreferrer" class="block rounded-md border border-slate-200 bg-slate-50 p-2 hover:border-indigo-200">
                                <img src="${this.absoluteUrl(item.url)}" alt="${this.escapeAttr(item.label)}" class="h-48 w-full rounded object-contain">
                                <div class="mt-2 text-xs font-bold text-slate-600">${this.escape(item.label)}</div>
                            </a>
                        `).join('')}
                    </div>
                ` : ''}
                ${keyframeItems.length ? `
                    <div class="mt-4">
                        <div class="mb-2 text-xs font-black text-slate-500">关键帧预览</div>
                        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                            ${keyframeItems.map((item) => `
                                <a href="${this.absoluteUrl(item.url)}" target="_blank" rel="noopener noreferrer" class="block rounded-md border border-slate-200 bg-slate-50 p-2 hover:border-emerald-200">
                                    <img src="${this.absoluteUrl(item.url)}" alt="${this.escapeAttr(item.label)}" class="h-48 w-full rounded object-contain">
                                    <div class="mt-2 text-xs font-bold text-slate-600">${this.escape(item.label)}</div>
                                </a>
                            `).join('')}
                        </div>
                    </div>
                ` : ''}
            </section>
        `;
    }

    renderStructured(job) {
        const structured = job.structured_result || {};
        if (!Object.keys(structured).length) return '';
        return `
            <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div class="text-sm font-black text-slate-900">结构化结果</div>
                    ${structured.provider ? `<span class="text-xs font-bold text-slate-400">${this.escape(structured.provider)}</span>` : ''}
                </div>
                <div class="grid gap-3 md:grid-cols-2">
                    ${this.renderTextBlock('标题', structured.title || job.title)}
                    ${this.renderTextBlock('封面主标题', structured.cover_title || job.cover_title)}
                    ${this.renderTextBlock('封面副标题', structured.cover_subtitle || job.cover_subtitle)}
                    ${this.renderTextBlock('摘要', structured.summary)}
                </div>
            </section>
        `;
    }

    renderAudioScript(job) {
        const script = this.audioScriptPayload(job);
        if (!script.text) return '';
        const qualityIssue = script.qualityIssue || this.audioScriptQualityIssue(script.text);
        return `
            <section class="rounded-lg border border-blue-100 bg-white p-4 shadow-sm">
                <div class="mb-3 flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <div class="text-sm font-black text-slate-900">音频文案</div>
                        <div class="mt-1 text-xs text-slate-500">
                            已用于豆包朗读的最终正文
                            ${script.chars ? ` · ${this.formatNumber(script.chars)} 字` : ''}
                            ${script.chunks ? ` · ${this.formatNumber(script.chunks)} 段` : ''}
                            ${script.provider ? ` · ${this.escape(script.provider)}` : ''}
                        </div>
                    </div>
                    <button type="button" data-copy-text="${this.escapeAttr(script.text)}" class="rounded-md border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-bold text-blue-700 hover:bg-blue-100">
                        <i class="fas fa-copy mr-1"></i>复制文案
                    </button>
                </div>
                ${qualityIssue ? `
                    <div class="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold leading-5 text-amber-800">
                        <i class="fas fa-triangle-exclamation mr-1.5"></i>${this.escape(qualityIssue)}。这条历史音频可能已经读入了结构标签，建议重跑结构化/朗读后再使用。
                    </div>
                ` : ''}
                <div class="max-h-[520px] overflow-auto whitespace-pre-wrap rounded-md border border-slate-200 bg-slate-50 p-4 text-sm leading-7 text-slate-800">${this.escape(script.text)}</div>
            </section>
        `;
    }

    audioScriptPayload(job) {
        const structured = job?.structured_result || {};
        const payload = job?.audio_script || {};
        const text = String(payload.text || structured.final_script_for_tts || structured.script || '').trim();
        const ttsMeta = job?.tts_text && typeof job.tts_text === 'object' ? job.tts_text : {};
        return {
            text,
            chars: Number(payload.chars || ttsMeta.chars || text.length || 0),
            chunks: Number(payload.chunks || ttsMeta.chunks || 0),
            provider: payload.provider || structured.provider || '',
            qualityIssue: payload.quality_issue || '',
        };
    }

    audioScriptQualityIssue(text) {
        if (/(^|[。！？!?；;]\s*)(?:第\s*)?[一二三四五六七八九十\d]+\s*部分\s*[:：]/.test(String(text || ''))) {
            return '音频文案包含分段结构标签';
        }
        if (/作为(?:首席)?内容主编|核对过程与勘误说明|原稿表述|修正说明/.test(String(text || ''))) {
            return '音频文案包含复核说明残留';
        }
        if (/研报\s*pdf\s*解析视频文案|自定义\s*Gem|PDF\s*\+\s*\d+/i.test(String(text || ''))) {
            return '音频文案包含页面或上传控件残留';
        }
        return '';
    }

    renderChildren(job) {
        const rows = Object.entries(job.children || {}).filter(([, value]) => value);
        if (!rows.length && !job.publisher_video && !job.publisher_vertical_video) return '';
        const seen = new Set();
        const items = [];
        const add = (key, value) => {
            if (!value || seen.has(key)) return;
            seen.add(key);
            items.push([key, value]);
        };
        add('publisher_video', job.publisher_video);
        add('publisher_vertical_video', job.publisher_vertical_video);
        add('publisher_vertical_preview_video', job.publisher_vertical_preview_video);
        rows.forEach(([key, value]) => add(key, value));
        return `
            <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="mb-3 text-sm font-black text-slate-900">子任务</div>
                <div class="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3">
                    ${items.map(([key, child]) => {
                        const status = this.statusMeta(child?.status || '');
                        return `
                            <div class="rounded-md border border-slate-200 bg-slate-50 p-3 text-xs">
                                <div class="flex items-center justify-between gap-2">
                                    <span class="font-black text-slate-800">${this.escape(this.childLabel(key))}</span>
                                    <span class="rounded-full px-2 py-1 font-bold ${status.className}">${this.escape(status.label)}</span>
                                </div>
                                <div class="mt-2 space-y-1 text-slate-500">
                                    <div>步骤 ${this.escape(child?.step || '-')}</div>
                                    <div>进度 ${Number(child?.progress || 0)}%</div>
                                    ${child?.job_id ? `<div class="truncate">任务 ${this.escape(child.job_id)}</div>` : ''}
                                </div>
                                ${child?.error ? `<div class="mt-2 text-rose-600">${this.escape(child.error)}</div>` : ''}
                            </div>
                        `;
                    }).join('')}
                </div>
            </section>
        `;
    }

    renderRaw(job) {
        return `
            <details class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <summary class="cursor-pointer text-sm font-black text-slate-900">原始 JSON</summary>
                <pre class="mt-3 max-h-[520px] overflow-auto rounded-md bg-slate-950 p-4 text-xs leading-5 text-slate-100">${this.escape(JSON.stringify(job, null, 2))}</pre>
            </details>
        `;
    }

    renderTextBlock(label, value) {
        if (!value) return '';
        return `
            <div class="rounded-md bg-slate-50 p-3">
                <div class="mb-1 text-[11px] font-black uppercase text-slate-400">${this.escape(label)}</div>
                <div class="text-sm font-bold leading-6 text-slate-800">${this.escape(value)}</div>
            </div>
        `;
    }

    downloadItems(job) {
        const items = [];
        const add = (key, label, url, icon, bytes = null) => {
            if (url) items.push({ key, label, url, icon, bytes });
        };
        add('pdf', '原始 PDF', job.pdf?.download_url || job.request?.pdf?.download_url, 'fas fa-file-pdf', job.pdf?.bytes);
        add('audio', '音频 WAV', job.download_audio_url || job.audio?.download_url, 'fas fa-volume-high', job.audio?.bytes);
        add('result', '结果 JSON', job.download_result_url, 'fas fa-file-code');
        add('cover', '横屏封面', job.download_cover_url || job.video?.cover?.download_url, 'fas fa-image');
        add('vertical_cover', '竖版封皮', job.download_vertical_cover_url || job.video?.vertical_cover?.download_url, 'fas fa-image-portrait');
        add('podcast_cover', '播客封皮', job.download_podcast_cover_url || job.video?.podcast_cover?.download_url, 'fas fa-record-vinyl');
        add('bilibili_cover', 'B站封皮', this.bilibiliCoverUrl(job), 'fas fa-tv');
        add('video', '横屏 MP4', job.download_video_url || job.video?.video?.download_url, 'fas fa-film', job.video?.video?.bytes);
        add('vertical_video', '竖屏 MP4', job.download_vertical_video_url || job.video?.vertical_video?.download_url, 'fas fa-mobile-screen', job.video?.vertical_video?.bytes);
        return items;
    }

    biliVideoOptions(job) {
        const items = [];
        const add = (key, label, url, defaultTitle) => {
            if (url) items.push({ key, label, url, defaultTitle });
        };
        add('landscape', '横屏视频', job.download_video_url || job.video?.video?.download_url, this.defaultBiliTitle(job));
        add('vertical', '竖屏视频', job.download_vertical_video_url || job.video?.vertical_video?.download_url, this.defaultBiliTitle(job));
        return items;
    }

    biliCoverOptions(job, videoKey = '') {
        const entries = [];
        const add = (key, label, url) => {
            if (url) entries.push({ key, label, url });
        };
        add('bilibili_cover', 'B站封皮', this.bilibiliCoverUrl(job));
        if (videoKey === 'vertical') {
            add('vertical_cover', '竖版封皮', job.download_vertical_cover_url || job.video?.vertical_cover?.download_url);
            add('podcast_cover', '播客封皮', job.download_podcast_cover_url || job.video?.podcast_cover?.download_url);
            add('cover', '横屏封面', job.download_cover_url || job.video?.cover?.download_url);
        } else {
            add('cover', '横屏封面', job.download_cover_url || job.video?.cover?.download_url);
            add('vertical_cover', '竖版封皮', job.download_vertical_cover_url || job.video?.vertical_cover?.download_url);
            add('podcast_cover', '播客封皮', job.download_podcast_cover_url || job.video?.podcast_cover?.download_url);
        }
        return entries;
    }

    bilibiliCoverUrl(job) {
        return job?.download_bilibili_cover_url
            || job?.video?.bilibili_cover?.download_url
            || job?.video?.keyframes?.bilibili_cover?.download_url
            || '';
    }

    defaultBiliCoverKey(job, videoKey) {
        const covers = this.biliCoverOptions(job, videoKey);
        return covers[0]?.key || '';
    }

    keyframeOptions(job) {
        const raw = job?.video?.keyframes || {};
        return Object.entries(raw)
            .filter(([key, item]) => key !== 'bilibili_cover' && item?.download_url)
            .map(([key, item]) => ({
                key,
                label: item.label || key,
                url: item.download_url,
                ratio: item.ratio || '',
            }));
    }

    defaultBiliTitle(job) {
        return this.compactTitle(this.displayTitle(job) || job.cover_title || job.pdf?.topic_title || job.pdf?.filename || '研报解读').slice(0, 120);
    }

    displayTitle(job) {
        const structured = job?.structured_result || {};
        const candidates = [
            job?.title,
            structured.cover_title,
            structured.title,
            job?.cover_title,
            job?.request?.metadata?.title,
            job?.pdf?.topic_title,
            job?.request?.pdf?.topic_title,
            job?.pdf?.filename,
            job?.request?.pdf?.filename,
        ];
        for (const candidate of candidates) {
            const title = this.compactTitle(candidate);
            if (title && !this.isGenericTitle(title)) return title;
        }
        return candidates.map((item) => this.compactTitle(item)).find(Boolean) || '';
    }

    compactTitle(value) {
        return String(value || '').replace(/\s+/g, ' ').trim();
    }

    isGenericTitle(value) {
        return ['外资研报', '研报', 'PDF', 'pdf'].includes(String(value || '').trim());
    }

    normalizeVerticalScheme(value) {
        const text = String(value || '').trim().toLowerCase();
        if (['scheme2', '2', '方案二', 'workbench', 'thumbnail_workbench', 'thumbnail-workbench'].includes(text)) {
            return 'scheme2';
        }
        return 'scheme1';
    }

    verticalSchemeLabel(value) {
        return this.normalizeVerticalScheme(value) === 'scheme2' ? '方案二' : '方案一';
    }

    isActiveStatus(status) {
        return ['queued', 'pending', 'running', 'processing', 'submitting'].includes(String(status || '').toLowerCase());
    }

    coverTitleForEdit(job) {
        const structured = job?.structured_result || {};
        const candidates = [
            job?.video?.cover_title,
            structured.cover_title,
            job?.cover_title,
            this.displayTitle(job),
        ];
        for (const value of candidates) {
            const text = this.compactMultilineCoverText(value);
            if (text) return text;
        }
        return '';
    }

    coverSubtitleForEdit(job) {
        const structured = job?.structured_result || {};
        const candidates = [
            job?.video?.cover_subtitle,
            structured.cover_subtitle,
            job?.cover_subtitle,
            structured.summary,
        ];
        for (const value of candidates) {
            const text = this.compactMultilineCoverText(value);
            if (text) return text;
        }
        return '';
    }

    compactMultilineCoverText(value) {
        return String(value || '')
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter(Boolean)
            .join('\n')
            .trim();
    }

    childLabel(key) {
        return {
            gemini: 'Gemini',
            script_material: '基础文案',
            publisher_video: '视频合成',
            publisher_vertical_video: '完整竖屏',
            publisher_vertical_preview_video: '竖屏预览',
        }[key] || key;
    }

    statusMeta(status) {
        const normalized = String(status || '').toLowerCase();
        if (['succeeded', 'completed', 'success'].includes(normalized)) return { label: '成功', className: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', bar: 'bg-emerald-500' };
        if (['failed', 'failure'].includes(normalized)) return { label: '失败', className: 'bg-rose-50 text-rose-700', dot: 'bg-rose-500', bar: 'bg-rose-500' };
        if (['canceled', 'cancelled'].includes(normalized)) return { label: '已取消', className: 'bg-slate-100 text-slate-500', dot: 'bg-slate-400', bar: 'bg-slate-400' };
        if (['running', 'processing', 'submitting'].includes(normalized)) return { label: '运行中', className: 'bg-blue-50 text-blue-700', dot: 'bg-blue-500', bar: 'bg-blue-600' };
        return { label: '排队中', className: 'bg-amber-50 text-amber-700', dot: 'bg-amber-500', bar: 'bg-amber-500' };
    }

    async copyText(text) {
        try {
            await navigator.clipboard.writeText(String(text || ''));
            this.setNotice('已复制。', 'success');
        } catch (error) {
            this.setNotice(`复制失败：${error.message}`, 'error');
        }
    }

    setNotice(message, type = 'info', hide = false) {
        if (!this.els.notice) return;
        if (hide || !message) {
            this.els.notice.classList.add('hidden');
            this.els.notice.textContent = '';
            return;
        }
        const classes = {
            success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
            error: 'border-red-200 bg-red-50 text-red-700',
            warning: 'border-amber-200 bg-amber-50 text-amber-800',
            info: 'border-slate-200 bg-white text-slate-700',
        };
        this.els.notice.className = `mb-4 rounded-lg border px-4 py-3 text-sm ${classes[type] || classes.info}`;
        this.els.notice.textContent = message;
    }

    absoluteUrl(path) {
        const raw = String(path || '').trim();
        if (!raw) return '#';
        if (/^https?:\/\//i.test(raw)) return raw;
        return `${this.apiRoot()}${raw}`;
    }

    formatDateTime(value) {
        if (!value) return '';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return date.toLocaleString('zh-CN', { hour12: false });
    }

    formatDuration(value) {
        const seconds = Number(value);
        if (!Number.isFinite(seconds)) return '';
        const total = Math.max(0, Math.round(seconds));
        const h = Math.floor(total / 3600);
        const m = Math.floor((total % 3600) / 60);
        const s = total % 60;
        return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
    }

    formatBytes(value) {
        const bytes = Number(value || 0);
        if (!Number.isFinite(bytes) || bytes <= 0) return '';
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
        return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
    }

    formatNumber(value) {
        const number = Number(value || 0);
        if (!Number.isFinite(number)) return '';
        return number.toLocaleString('zh-CN');
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

    escapeAttr(value) {
        return this.escape(value).replace(/`/g, '&#96;');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.reportAudioWorkflowDetailPage = new ReportAudioWorkflowDetailPage();
});
