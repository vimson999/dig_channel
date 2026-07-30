class ReportAudioWorkflowDetailPage {
    constructor() {
        this.jobId = new URLSearchParams(window.location.search).get('job_id') || '';
        this.job = null;
        this.refreshTimer = null;
        this.autoRefreshMs = 6000;
        this.biliPanel = null;
        this.biliPanelDraft = null;
        this.biliPanelDraftJobId = '';
        this.douyinPanel = null;
        this.douyinPanelDraft = null;
        this.douyinPanelDraftJobId = '';
        this.wechatChannelsPanel = null;
        this.wechatChannelsPanelDraft = null;
        this.wechatChannelsPanelDraftJobId = '';
        this.activePublishPlatform = 'bilibili';
        this.publishTabs = null;
        this.verticalVideoRequestIds = new Set();
        this.finalVideoRequestIds = new Set();
        this.selectedVerticalScheme = null;
        this.podcastCoverRequestIds = new Set();
        this.coverRegeneratingIds = new Set();
        this.stickWorkflowRequestIds = new Set();
        this.visualSchemes = [{ key: 'stick_figure', name: '白板火柴人', configured: true }];
        this.selectedVisualSchemeKey = 'stick_figure';
        this.verticalVideoSchemes = [
            {
                key: 'scheme1',
                label: '方案一',
                name: '当前竖屏背景',
                description: '复用当前竖屏模板、标题、字幕和既有背景素材。',
            },
            {
                key: 'scheme2',
                label: '方案二',
                name: '封面工具台背景',
                description: '使用竖版封面工具台生成的视觉背景承载口播。',
            },
            {
                key: 'scheme3',
                label: '方案三',
                name: '视觉嵌框',
                description: '把已完成的火柴人、哆啦A梦等视觉分镜嵌入竖屏模板。',
            },
        ];
        this.lastRefreshedAt = null;
        this.quickNavObserver = null;
        this.sectionRenderCache = new Map();
        this.els = {
            jobIdLabel: document.getElementById('job-id-label'),
            title: document.getElementById('detail-title'),
            subtitle: document.getElementById('detail-subtitle'),
            refreshBtn: document.getElementById('refresh-detail-btn'),
            openApiJson: document.getElementById('open-api-json'),
            notice: document.getElementById('notice'),
            root: document.getElementById('detail-root'),
            quickNav: document.getElementById('quick-nav'),
            sidebarStatus: document.getElementById('sidebar-task-status'),
            sidebarRefreshBtn: document.getElementById('sidebar-refresh-btn'),
        };
        this.bind();
        this.loadVisualSchemes();
        this.load();
    }

    bind() {
        this.els.refreshBtn?.addEventListener('click', () => this.load({ manual: true }));
        this.els.sidebarRefreshBtn?.addEventListener('click', () => this.load({ manual: true }));
        window.addEventListener('beforeunload', () => { this.clearAutoRefresh(); this.quickNavObserver?.disconnect(); this.douyinPanel?.destroy?.(); this.wechatChannelsPanel?.destroy?.(); });
        this.els.quickNav?.addEventListener('click', (event) => {
            const item = event.target.closest('[data-quick-nav]');
            if (item && !item.disabled) {
                const target = document.getElementById(item.dataset.quickNav || '');
                if (target) {
                    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    this.setActiveQuickNav(target.id);
                }
                return;
            }
            if (event.target.closest('[data-scroll-top]')) window.scrollTo({ top: 0, behavior: 'smooth' });
        });
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
        this.els.root?.addEventListener('change', (event) => {
            if (event.target.matches('[data-detail-visual-scheme]')) {
                this.selectedVisualSchemeKey = String(event.target.value || 'stick_figure');
                this.render();
                return;
            }
            if (!event.target.matches('[data-detail-vertical-scheme]')) return;
            this.selectedVerticalScheme = this.normalizeVerticalScheme(event.target.value);
            this.render();
        });
    }

    async loadVisualSchemes() {
        try {
            const result = await this.request('/api/report-stick-figure-video-workflows/status', { timeoutMs: 10000 });
            const schemes = result.data?.visual_schemes;
            if (Array.isArray(schemes) && schemes.length) this.visualSchemes = schemes;
            if (!this.visualSchemes.some((item) => item.key === this.selectedVisualSchemeKey && item.configured)) {
                this.selectedVisualSchemeKey = this.visualSchemes.find((item) => item.configured)?.key || 'stick_figure';
            }
            if (this.job) this.renderChangedSections(['production-tools']);
        } catch (_) {
            // Keep the backward-compatible stick-figure default when status is unavailable.
        }
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
        const background = Boolean(this.job) && !options.manual;
        if (!background) {
            this.setRefreshBusy(true);
        }
        this.els.jobIdLabel.textContent = `任务 ${this.jobId}`;
        this.els.openApiJson.href = this.absoluteUrl(`/api/report-audio-workflows/jobs/${encodeURIComponent(this.jobId)}`);
        if (!this.job) {
            this.els.root.innerHTML = '<div class="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">加载详情中...</div>';
        }
        try {
            const useLightweightStatus = Boolean(this.job) && !options.manual;
            const hadActiveVideo = this.hasActiveVideoChild(this.job);
            const path = useLightweightStatus
                ? `/api/report-audio-workflows/jobs/${encodeURIComponent(this.jobId)}/status`
                : `/api/report-audio-workflows/jobs/${encodeURIComponent(this.jobId)}`;
            const result = await this.request(path, { timeoutMs: useLightweightStatus ? 10000 : 30000 });
            const payload = result.data || result;
            this.job = useLightweightStatus
                ? this.mergeJobStatus(this.job, payload)
                : payload;
            if (useLightweightStatus && hadActiveVideo && !this.hasActiveVideoChild(this.job)) {
                const detailResult = await this.request(`/api/report-audio-workflows/jobs/${encodeURIComponent(this.jobId)}`, { timeoutMs: 30000 });
                this.job = detailResult.data || detailResult;
            }
            this.lastRefreshedAt = new Date();
            if (background) this.renderChangedSections();
            else this.render();
            this.scheduleAutoRefresh();
            this.setNotice('', 'info', true);
        } catch (error) {
            this.setNotice(`加载失败：${error.message}`, 'error');
        } finally {
            if (!background) this.setRefreshBusy(false);
        }
    }

    setRefreshBusy(busy) {
        if (this.els.refreshBtn) {
            this.els.refreshBtn.disabled = busy;
            this.els.refreshBtn.innerHTML = busy
                ? '<i class="fas fa-spinner fa-spin mr-1.5"></i>刷新中'
                : '<i class="fas fa-rotate mr-1.5"></i>刷新详情';
        }
        if (this.els.sidebarRefreshBtn) {
            this.els.sidebarRefreshBtn.disabled = busy;
            this.els.sidebarRefreshBtn.innerHTML = busy
                ? '<i class="fas fa-spinner fa-spin"></i><span>刷新中</span>'
                : '<i class="fas fa-rotate"></i><span>刷新详情</span>';
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
        const activeVideo = this.hasActiveVideoChild(this.job);
        if (!activeJob && !activeUpload && !activeVideo) return;
        this.refreshTimer = setTimeout(() => {
            this.refreshTimer = null;
            if (activeJob || activeVideo) {
                this.load({ background: true });
            } else {
                this.refreshBilibiliUploads();
            }
        }, this.autoRefreshMs);
    }

    async refreshBilibiliUploads() {
        if (!this.jobId) return;
        try {
            const result = await this.request(`/api/report-audio-workflows/jobs/${encodeURIComponent(this.jobId)}/status`, { timeoutMs: 10000 });
            this.job = this.mergeJobStatus(this.job || {}, result.data || result);
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

    mergeJobStatus(job, status) {
        const next = { ...(job || {}) };
        const payload = status || {};
        Object.assign(next, payload);
        next.children = {
            ...((job && job.children) || {}),
            ...(payload.children || {}),
        };
        for (const key of ['publisher_video', 'publisher_vertical_video', 'publisher_vertical_preview_video']) {
            if (next.children[key]) next[key] = next.children[key];
        }
        if (payload.doubao_browser_tts) next.doubao_browser_tts = payload.doubao_browser_tts;
        if (payload.bilibili_uploads) next.bilibili_uploads = payload.bilibili_uploads;
        if (payload.visual_videos) next.visual_videos = payload.visual_videos;
        return next;
    }

    hasActiveVideoChild(job) {
        const children = job?.children || {};
        return [
            children.publisher_video,
            children.publisher_vertical_video,
            children.publisher_vertical_preview_video,
            job?.publisher_video,
            job?.publisher_vertical_video,
            job?.publisher_vertical_preview_video,
            ...(Array.isArray(job?.visual_videos) ? job.visual_videos : []),
        ].some((item) => this.isActiveStatus(item?.status));
    }

    render() {
        const job = this.job || {};
        const title = this.displayTitle(job) || job.job_id || '研报转语音详情';
        const biliPanelDraft = this.captureBilibiliPanelDraft(job);
        const douyinPanelDraft = this.captureDouyinPanelDraft(job);
        const wechatChannelsPanelDraft = this.captureWechatChannelsPanelDraft(job);
        this.publishTabs?.destroy?.();
        this.els.title.textContent = title;
        this.els.subtitle.textContent = [
            job.pdf?.filename || job.request?.pdf?.filename || '',
            job.created_at ? `创建 ${this.formatDateTime(job.created_at)}` : '',
            job.completed_at ? `完成 ${this.formatDateTime(job.completed_at)}` : '',
        ].filter(Boolean).join(' · ');
        const sections = this.sectionDefinitions(job);
        this.sectionRenderCache = new Map(sections.map(({ id, markup }) => [id, markup]));
        this.els.root.innerHTML = `${sections.map(({ markup }) => markup).join('')}${this.renderRaw(job)}`;
        this.mountBilibiliPanel(job, biliPanelDraft);
        this.mountDouyinPanel(job, douyinPanelDraft);
        this.mountWechatChannelsPanel(job, wechatChannelsPanelDraft);
        this.mountPublishTabs();
        this.renderSidebarStatus(job);
        this.setupQuickNavigation();
    }

    sectionDefinitions(job) {
        return [
            ['task-overview', this.renderSummary(job)],
            ['production-tools', this.renderActions(job)],
            ['visual-videos', this.renderVisualVideos(job)],
            ['media-assets', [this.renderDownloads(job), this.renderMedia(job)].join('')],
            ['content-results', [this.renderAudioScript(job), this.renderStructured(job)].join('')],
            ['child-tasks', this.renderChildren(job)],
            ['publish-center', this.renderPublishCenter()],
        ].map(([id, html]) => ({ id, markup: this.navSection(id, html) }));
    }

    renderChangedSections(onlyIds = null) {
        const job = this.job || {};
        const allowed = onlyIds ? new Set(onlyIds) : null;
        const sections = this.sectionDefinitions(job);
        let structureChanged = false;
        for (let index = 0; index < sections.length; index += 1) {
            const { id, markup } = sections[index];
            if (id === 'publish-center' || (allowed && !allowed.has(id))) continue;
            if (this.sectionRenderCache.get(id) === markup) continue;
            const current = document.getElementById(id);
            if (!markup) {
                current?.remove();
            } else if (current) {
                current.outerHTML = markup;
            } else {
                const previous = [...sections.slice(0, index)].reverse()
                    .map((item) => document.getElementById(item.id))
                    .find(Boolean);
                if (previous) previous.insertAdjacentHTML('afterend', markup);
                else this.els.root.insertAdjacentHTML('afterbegin', markup);
            }
            this.sectionRenderCache.set(id, markup);
            structureChanged = true;
        }
        this.els.title.textContent = this.displayTitle(job) || job.job_id || '研报转语音详情';
        this.els.subtitle.textContent = [
            job.pdf?.filename || job.request?.pdf?.filename || '',
            job.created_at ? `创建 ${this.formatDateTime(job.created_at)}` : '',
            job.completed_at ? `完成 ${this.formatDateTime(job.completed_at)}` : '',
        ].filter(Boolean).join(' · ');
        this.biliPanel?.updateJobData?.(job);
        this.renderSidebarStatus(job);
        if (structureChanged) this.setupQuickNavigation();
    }

    renderSidebarStatus(job) {
        if (!this.els.sidebarStatus) return;
        const status = this.statusMeta(job?.status);
        const stages = this.reportWorkflowStages(job);
        const current = stages.find((stage) => ['active', 'failed', 'canceled'].includes(stage.state))
            || [...stages].reverse().find((stage) => stage.state === 'completed')
            || stages[0];
        const stateMeta = {
            completed: { icon: 'fa-circle-check', color: 'text-emerald-600', dot: 'bg-emerald-500' },
            active: { icon: 'fa-spinner fa-spin', color: 'text-blue-600', dot: 'bg-blue-500' },
            failed: { icon: 'fa-circle-exclamation', color: 'text-red-600', dot: 'bg-red-500' },
            canceled: { icon: 'fa-circle-stop', color: 'text-amber-600', dot: 'bg-amber-500' },
            pending: { icon: 'fa-circle', color: 'text-slate-300', dot: 'bg-slate-300' },
        };
        const activelyRefreshing = Boolean(this.refreshTimer)
            || ['queued', 'running', 'submitting'].includes(String(job?.status || '').toLowerCase())
            || this.hasActiveVideoChild(job);
        const refreshedAt = this.lastRefreshedAt
            ? this.lastRefreshedAt.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
            : '--:--:--';
        this.els.sidebarStatus.innerHTML = `
            <div class="flex items-center justify-between gap-2">
                <span class="text-[11px] font-black uppercase tracking-wider text-slate-400">任务状态</span>
                <span class="rounded-full px-2 py-0.5 text-[10px] font-black ${status.className}">${this.escape(status.label)}</span>
            </div>
            <div class="mt-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2">
                <div class="truncate text-xs font-black text-slate-800">${this.escape(current?.label || '等待任务')}</div>
                <div class="mt-1 flex items-center justify-between text-[10px] font-bold text-slate-500">
                    <span>${this.escape(current?.state === 'active' ? '进行中' : (status.label || '等待'))}</span>
                    <span>${Math.max(0, Math.min(100, Number(job?.progress || 0)))}%</span>
                </div>
            </div>
            <ol class="mt-2 grid grid-cols-2 gap-1" aria-label="左侧任务流程状态">
                ${stages.map((stage) => {
                    const meta = stateMeta[stage.state] || stateMeta.pending;
                    return `
                        <li class="flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-1 text-[10px] ${stage.state === 'active' ? 'bg-blue-50 font-black text-blue-800' : 'font-bold text-slate-500'}" title="${this.escapeAttr(stage.label)}">
                            <i class="fas ${meta.icon} ${meta.color} w-3 shrink-0 text-center" aria-hidden="true"></i>
                            <span class="truncate">${this.escape(stage.label)}</span>
                        </li>
                    `;
                }).join('')}
            </ol>
            <div class="mt-2 border-t border-slate-100 pt-2 text-[10px] leading-4 text-slate-400">
                <div>${activelyRefreshing ? `运行中每 ${Math.round(this.autoRefreshMs / 1000)} 秒自动刷新` : '状态稳定，可手动刷新'}</div>
                <div>最近刷新 ${this.escape(refreshedAt)}</div>
            </div>
        `;
    }

    navSection(id, html) {
        const content = String(html || '').trim();
        if (!content) return '';
        return `<div id="${id}" data-nav-section class="scroll-mt-24 space-y-4">${content}</div>`;
    }

    setupQuickNavigation() {
        if (!this.els.quickNav) return;
        this.quickNavObserver?.disconnect();
        const buttons = Array.from(this.els.quickNav.querySelectorAll('[data-quick-nav]'));
        const sections = [];
        buttons.forEach((button) => {
            const section = document.getElementById(button.dataset.quickNav || '');
            button.disabled = !section;
            button.classList.toggle('opacity-40', !section);
            button.classList.toggle('cursor-not-allowed', !section);
            if (section) sections.push(section);
        });
        if (!sections.length) return;
        this.setActiveQuickNav(sections[0].id);
        this.quickNavObserver = new IntersectionObserver(() => {
            const passed = sections.filter((section) => section.getBoundingClientRect().top <= 112);
            this.setActiveQuickNav((passed[passed.length - 1] || sections[0]).id);
        }, { rootMargin: '-96px 0px -62% 0px', threshold: [0, 0.05, 0.2] });
        sections.forEach((section) => this.quickNavObserver.observe(section));
    }

    setActiveQuickNav(id) {
        this.els.quickNav?.querySelectorAll('[data-quick-nav]').forEach((button) => {
            const active = button.dataset.quickNav === id;
            button.classList.toggle('bg-indigo-50', active);
            button.classList.toggle('text-indigo-700', active);
            button.classList.toggle('text-slate-600', !active);
            if (active) button.setAttribute('aria-current', 'location');
            else button.removeAttribute('aria-current');
        });
    }

    renderPublishCenter() {
        if (!window.renderPlatformPublishTabs) {
            return '<div id="report-bilibili-publish-panel"></div><div id="report-douyin-publish-panel"></div><div id="report-wechat-channels-publish-panel"></div>';
        }
        return window.renderPlatformPublishTabs({
            prefix: 'report',
            activeKey: this.activePublishPlatform,
            panelIds: {
                bilibili: 'report-bilibili-publish-panel',
                douyin: 'report-douyin-publish-panel',
                wechat_channels: 'report-wechat-channels-publish-panel',
            },
        });
    }

    mountPublishTabs() {
        const container = this.els.root?.querySelector('[data-platform-publish-tabs="report"]');
        if (!container || !window.PlatformPublishTabs) return;
        this.publishTabs = new window.PlatformPublishTabs({
            container,
            activeKey: this.activePublishPlatform,
            onChange: (key) => { this.activePublishPlatform = key; },
        });
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
        if (action === 'confirm-keyframes-generate-final') {
            this.generateFinalVideos(jobId);
        }
        if (action === 'generate-podcast-cover') {
            this.generatePodcastCover(jobId, { force: false });
        }
        if (action === 'regenerate-cover') {
            this.regenerateCover(jobId);
        }
        if (action === 'regenerate-cover-asset') {
            this.regenerateCoverAsset(jobId, button.dataset.coverKey || '', button);
        }
        if (action === 'open-stick-figure-workflow') {
            this.openStickFigureWorkflow(jobId, button.dataset.visualSchemeKey || '');
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
                videoOptions: (payload) => this.biliUploadVideoOptions(payload),
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

    captureDouyinPanelDraft(job) {
        const nextJobId = String(job?.job_id || job?.id || this.jobId || '');
        const currentJobId = String(this.douyinPanel?.job?.job_id || this.douyinPanel?.job?.id || this.douyinPanelDraftJobId || '');
        if ((!currentJobId || !nextJobId || currentJobId === nextJobId) && this.douyinPanel?.readFormDraft) {
            this.douyinPanelDraft = this.douyinPanel.readFormDraft() || this.douyinPanelDraft;
        }
        if (this.douyinPanelDraftJobId && nextJobId && this.douyinPanelDraftJobId !== nextJobId) this.douyinPanelDraft = null;
        this.douyinPanelDraftJobId = nextJobId;
        return this.douyinPanelDraft ? { ...this.douyinPanelDraft } : null;
    }

    mountDouyinPanel(job, formDraft = null) {
        const container = document.getElementById('report-douyin-publish-panel');
        if (!container || !window.DouyinPublishPanel) return;
        if (!this.douyinPanel || this.douyinPanel.container !== container) {
            this.douyinPanel?.destroy?.();
            this.douyinPanel = new window.DouyinPublishPanel({
                container,
                apiRoot: () => this.apiRoot(),
                request: (path, options) => this.request(path, options),
                onNotice: (message, type) => this.setNotice(message, type),
                listPath: (jobId) => `/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}/douyin-uploads`,
                uploadPath: (jobId) => `/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}/douyin-upload`,
                cancelPath: (jobId, taskId) => `/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}/douyin-upload/${encodeURIComponent(taskId)}/cancel`,
                videoOptions: (payload) => this.biliVideoOptions(payload),
                defaultTitle: (payload, videoKey) => this.variantBiliTitle(payload, videoKey),
                defaultTags: '财经,音频,研报解读',
                emptyDescription: '当前任务还没有完整视频文件。请先生成横屏或竖屏视频。',
            });
        }
        this.douyinPanel.setJob(job, { formDraft });
    }

    captureWechatChannelsPanelDraft(job) {
        const nextJobId = String(job?.job_id || job?.id || this.jobId || '');
        const currentJobId = String(this.wechatChannelsPanel?.job?.job_id || this.wechatChannelsPanel?.job?.id || this.wechatChannelsPanelDraftJobId || '');
        if ((!currentJobId || !nextJobId || currentJobId === nextJobId) && this.wechatChannelsPanel?.readFormDraft) {
            this.wechatChannelsPanelDraft = this.wechatChannelsPanel.readFormDraft() || this.wechatChannelsPanelDraft;
        }
        if (this.wechatChannelsPanelDraftJobId && nextJobId && this.wechatChannelsPanelDraftJobId !== nextJobId) this.wechatChannelsPanelDraft = null;
        this.wechatChannelsPanelDraftJobId = nextJobId;
        return this.wechatChannelsPanelDraft ? { ...this.wechatChannelsPanelDraft } : null;
    }

    mountWechatChannelsPanel(job, formDraft = null) {
        const container = document.getElementById('report-wechat-channels-publish-panel');
        if (!container || !window.WechatChannelsPublishPanel) return;
        if (!this.wechatChannelsPanel || this.wechatChannelsPanel.container !== container) {
            this.wechatChannelsPanel?.destroy?.();
            this.wechatChannelsPanel = new window.WechatChannelsPublishPanel({
                container,
                apiRoot: () => this.apiRoot(),
                request: (path, options) => this.request(path, options),
                onNotice: (message, type) => this.setNotice(message, type),
                listPath: (jobId) => `/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}/wechat-channels-uploads`,
                uploadPath: (jobId) => `/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}/wechat-channels-upload`,
                cancelPath: (jobId, taskId) => `/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}/wechat-channels-upload/${encodeURIComponent(taskId)}/cancel`,
                videoOptions: (payload) => this.biliVideoOptions(payload),
                defaultTitle: (payload, videoKey) => this.variantBiliTitle(payload, videoKey),
                defaultTags: '财经,音频,研报解读',
                emptyDescription: '当前任务还没有完整视频文件。请先生成横屏或竖屏视频。',
            });
        }
        this.wechatChannelsPanel.setJob(job, { formDraft });
    }

    renderSummary(job) {
        const status = this.statusMeta(job.status);
        const pdfName = job.pdf?.filename || job.request?.pdf?.filename || 'PDF';
        const workflowStages = this.reportWorkflowStages(job);
        const outputSummary = this.reportWorkflowOutputSummary(job);
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
                            job.step ? `当前 ${job.step}` : '',
                            job.audio?.seconds ? `音频 ${this.formatDuration(job.audio.seconds)}` : '',
                            ...outputSummary,
                        ].filter(Boolean).join(' · '))}</div>
                    </div>
                    <div class="flex flex-wrap gap-2">
                        ${job.pdf?.preview_url ? `<a href="${this.absoluteUrl(job.pdf.preview_url)}" target="_blank" rel="noopener noreferrer" class="rounded-md border border-slate-300 bg-white px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50"><i class="fas fa-file-pdf mr-1"></i>PDF 预览</a>` : ''}
                        <button type="button" data-copy-text="${this.escapeAttr(this.displayTitle(job))}" class="rounded-md border border-slate-300 bg-white px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50"><i class="fas fa-copy mr-1"></i>复制标题</button>
                    </div>
                </div>
                <ol class="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6" aria-label="任务流程进度">
                    ${workflowStages.map((stage) => this.renderWorkflowStage(stage)).join('')}
                </ol>
                ${job.error ? `<div class="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">${this.escape(job.error)}</div>` : ''}
            </section>
        `;
    }

    reportWorkflowRenderMode(job) {
        const explicitMode = String(
            job?.video?.render_mode
            || job?.request?.video?.render_mode
            || job?.request?.video_render_mode
            || ''
        ).trim().toLowerCase();
        if (explicitMode === 'keyframes' || explicitMode === 'full_video') return explicitMode;
        return String(job?.video?.status || '').trim().toLowerCase() === 'keyframes_ready'
            ? 'keyframes'
            : 'full_video';
    }

    reportWorkflowOutputSummary(job) {
        const video = job?.video || {};
        if (this.reportWorkflowRenderMode(job) === 'keyframes') {
            const keyframes = Object.entries(video.keyframes || {});
            const summary = [`关键帧/封皮${keyframes.length ? ` ${keyframes.length} 项` : ''}`];
            const verticalKeyframe = keyframes.find(([key, item]) => (
                /vertical_(background|keyframe)/.test(String(key || ''))
                || /竖.*关键帧/.test(String(item?.label || ''))
            ));
            if (verticalKeyframe) {
                const scheme = verticalKeyframe[1]?.scheme || video.vertical_video_scheme;
                if (scheme) summary.push(`${this.verticalSchemeLabel(scheme)}竖版关键帧`);
            }
            return summary;
        }

        const summary = ['完整视频'];
        const verticalRequested = Boolean(
            video.vertical_enabled
            || job?.request?.video?.vertical_enabled
            || video.vertical_video
            || Object.keys(video.vertical_videos || {}).length
        );
        if (verticalRequested && video.vertical_video_scheme) {
            summary.push(`${this.verticalSchemeLabel(video.vertical_video_scheme)}竖屏`);
        }
        return summary;
    }

    reportWorkflowStages(job) {
        const keyframeMode = this.reportWorkflowRenderMode(job) === 'keyframes';
        const stages = [
            { key: 'pdf', label: 'PDF 准备', icon: 'fa-file-pdf' },
            { key: 'gemini', label: 'Gemini 解析', icon: 'fa-wand-magic-sparkles' },
            { key: 'script', label: '文案整理', icon: 'fa-file-lines' },
            { key: 'tts', label: '语音生成', icon: 'fa-wave-square' },
            { key: 'video', label: keyframeMode ? '关键帧/封皮' : '视频生成', icon: keyframeMode ? 'fa-images' : 'fa-film' },
            { key: 'done', label: '任务完成', icon: 'fa-circle-check' },
        ];
        const taskStatus = String(job?.status || '').trim().toLowerCase();
        const step = String(job?.step || '').trim().toLowerCase();
        const progress = Math.max(0, Math.min(100, Number(job?.progress || 0)));
        let currentIndex = this.reportWorkflowStageIndex(step, progress);
        if (['completed', 'success', 'succeeded', 'done'].includes(taskStatus) || step === 'done') {
            currentIndex = stages.length - 1;
        }
        const failed = ['failed', 'error'].includes(taskStatus);
        const canceled = ['canceled', 'cancelled'].includes(taskStatus);
        return stages.map((stage, index) => ({
            ...stage,
            state: (
                index < currentIndex || (index === currentIndex && currentIndex === stages.length - 1 && !failed && !canceled)
                    ? 'completed'
                    : (index === currentIndex ? (failed ? 'failed' : (canceled ? 'canceled' : 'active')) : 'pending')
            ),
        }));
    }

    reportWorkflowStageIndex(step, progress) {
        if (step === 'done') return 5;
        if (
            step.includes('publisher')
            || step.includes('video')
            || step.includes('keyframe')
            || step.includes('pdf_preview')
            || step.includes('render_pdf')
        ) return 4;
        if (
            step.includes('doubao')
            || step.includes('tts')
            || step.includes('audio')
            || step.includes('reading')
            || step.includes('combine')
        ) return 3;
        if (
            step.includes('normalize')
            || step.includes('review_cover')
            || step.includes('structured')
            || step.includes('script_material')
            || step.includes('manual_gemini')
        ) return 2;
        if (step.includes('gemini')) return 1;
        if (step && !['queued', 'pending', 'start'].includes(step)) {
            if (progress >= 94) return 4;
            if (progress >= 61) return 3;
            if (progress >= 53) return 2;
            if (progress >= 8) return 1;
        }
        return 0;
    }

    renderWorkflowStage(stage) {
        const meta = {
            completed: {
                card: 'border-emerald-200 bg-emerald-50',
                icon: 'fa-circle-check text-emerald-600',
                label: 'text-emerald-950',
                status: '已完成',
                statusClass: 'text-emerald-700',
            },
            active: {
                card: 'border-blue-300 bg-blue-50 ring-1 ring-blue-100',
                icon: 'fa-spinner fa-spin text-blue-600',
                label: 'text-blue-950',
                status: '进行中',
                statusClass: 'text-blue-700',
            },
            failed: {
                card: 'border-red-300 bg-red-50',
                icon: 'fa-circle-exclamation text-red-600',
                label: 'text-red-950',
                status: '失败',
                statusClass: 'text-red-700',
            },
            canceled: {
                card: 'border-amber-300 bg-amber-50',
                icon: 'fa-circle-stop text-amber-600',
                label: 'text-amber-950',
                status: '已取消',
                statusClass: 'text-amber-700',
            },
            pending: {
                card: 'border-slate-200 bg-slate-50',
                icon: `${stage.icon} text-slate-400`,
                label: 'text-slate-600',
                status: '待开始',
                statusClass: 'text-slate-400',
            },
        }[stage.state];
        return `
            <li class="flex min-w-0 items-center gap-2.5 rounded-lg border px-3 py-2.5 ${meta.card}">
                <i class="fas ${meta.icon} w-4 shrink-0 text-center" aria-hidden="true"></i>
                <div class="min-w-0">
                    <div class="truncate text-xs font-black ${meta.label}">${this.escape(stage.label)}</div>
                    <div class="mt-0.5 text-[11px] font-bold ${meta.statusClass}">${meta.status}</div>
                </div>
            </li>
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
        const verticalVideoItems = Object.values(video?.vertical_videos || {});
        const verticalPreviewVideoUrl = video?.vertical_preview_video?.download_url || '';
        const verticalPreviewItems = Object.values(video?.vertical_preview_videos || {});
        const verticalCoverUrl = job.download_vertical_cover_url || video?.vertical_cover?.download_url || '';
        const podcastCoverUrl = job.download_podcast_cover_url || video?.podcast_cover?.download_url || '';
        const bilibiliCoverUrl = this.bilibiliCoverUrl(job);
        const verticalChild = job.publisher_vertical_video || job.children?.publisher_vertical_video;
        const previewChild = job.publisher_vertical_preview_video || job.children?.publisher_vertical_preview_video;
        const verticalActive = this.isActiveStatus(verticalChild?.status) || this.isActiveStatus(previewChild?.status) || this.verticalVideoRequestIds.has(jobId);
        const finalVideoActive = this.finalVideoRequestIds.has(jobId)
            || (this.isActiveStatus(verticalChild?.status) && Boolean(verticalChild?.metadata?.include_landscape));
        const podcastGenerating = this.podcastCoverRequestIds.has(jobId);
        const coverRegenerating = this.coverRegeneratingIds.has(jobId);
        const scheme = this.normalizeVerticalScheme(
            this.selectedVerticalScheme
            || job.request?.video?.vertical_video_scheme
            || job.request?.video?.vertical_scheme
            || video.vertical_video_scheme
            || video.vertical_preview_scheme
            || 'scheme1'
        );
        const requestedSchemes = Array.isArray(job.request?.video?.vertical_video_schemes)
            ? job.request.video.vertical_video_schemes.map((item) => this.normalizeVerticalScheme(item))
            : [scheme];
        const landscapeSelected = job.request?.video?.enabled !== false;
        const canRequestVertical = Boolean(audioUrl || videoUrl);
        const stickWorkflowActive = this.stickWorkflowRequestIds.has(jobId);
        const keyframeMode = String(video.render_mode || job.request?.video?.render_mode || '').toLowerCase() === 'keyframes';
        const selectedVisualScheme = this.visualSchemes.find(
            (item) => item.key === this.selectedVisualSchemeKey
        ) || this.visualSchemes[0];
        const visualWorkflowEntries = this.visualSchemes
            .map((item, index) => {
                const existing = (Array.isArray(job.visual_videos) ? job.visual_videos : []).find(
                    (workflow) => String(workflow?.visual_scheme?.key || '') === item.key
                );
                const selected = item.key === this.selectedVisualSchemeKey;
                const action = existing?.detail_url
                    ? `<a href="${this.absoluteUrl(existing.detail_url)}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center rounded-md border border-violet-200 bg-white px-3 py-1.5 text-xs font-bold text-violet-700 hover:bg-violet-50"><i class="fas fa-arrow-up-right-from-square mr-1"></i>${this.escape(item.name)}详情</a>`
                    : `<button type="button" data-detail-action="open-stick-figure-workflow" data-visual-scheme-key="${this.escapeAttr(item.key)}" data-job-id="${this.escapeAttr(jobId)}" ${!item.configured || !audioUrl || stickWorkflowActive ? 'disabled' : ''} class="rounded-md border border-violet-200 bg-white px-3 py-1.5 text-xs font-bold text-violet-700 hover:bg-violet-50 disabled:cursor-wait disabled:opacity-60"><i class="fas ${stickWorkflowActive ? 'fa-spinner fa-spin' : 'fa-folder-open'} mr-1"></i>创建${this.escape(item.name)}详情</button>`;
                return `
                    <article class="rounded-lg border p-3 ${selected ? 'border-violet-300 bg-violet-50 ring-1 ring-violet-100' : 'border-slate-200 bg-white'} ${item.configured ? '' : 'opacity-60'}">
                        <label class="flex cursor-pointer items-start gap-2">
                            <input type="radio" name="detail_visual_scheme" value="${this.escapeAttr(item.key)}" data-detail-visual-scheme ${selected ? 'checked' : ''} ${item.configured ? '' : 'disabled'} class="mt-0.5 h-4 w-4 border-slate-300 text-violet-700 focus:ring-violet-500">
                            <span class="min-w-0">
                                <span class="block text-xs font-black text-slate-900">方案 ${index + 2} · ${this.escape(item.name)}</span>
                                <span class="mt-1 block text-[11px] leading-4 text-slate-500">${this.escape(item.description || '由视觉方案注册表提供的横屏分镜视频。')}</span>
                            </span>
                        </label>
                        <div class="mt-3 flex flex-wrap items-center gap-2">
                            ${action}
                            ${existing ? `<span class="rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-bold text-emerald-700">${this.isActiveStatus(existing.status) ? '生成中' : '已有任务'}</span>` : ''}
                            ${!item.configured ? '<span class="text-[11px] font-bold text-amber-700">待配置 Gem</span>' : ''}
                        </div>
                    </article>
                `;
            })
            .join('');
        const finalVerticalOptions = this.verticalVideoSchemes.map((item) => `
            <label class="flex cursor-pointer items-start gap-2 rounded-md border border-fuchsia-100 bg-fuchsia-50/60 px-3 py-2 text-xs text-fuchsia-950">
                <input type="checkbox" value="${this.escapeAttr(item.key)}" data-detail-final-video-option ${requestedSchemes.includes(item.key) ? 'checked' : ''} class="mt-0.5 h-4 w-4 rounded border-slate-300 text-fuchsia-700 focus:ring-fuchsia-500">
                <span><span class="block font-black">${this.escape(item.label)} · ${this.escape(item.name)}</span><span class="mt-0.5 block text-[11px] font-normal text-fuchsia-700">${this.escape(item.description)}</span></span>
            </label>
        `).join('');
        const verticalSchemeCards = this.verticalVideoSchemes.map((item) => {
            const selected = item.key === scheme;
            return `
                <label class="flex cursor-pointer items-start gap-2 rounded-lg border p-3 ${selected ? 'border-fuchsia-300 bg-fuchsia-50 ring-1 ring-fuchsia-100' : 'border-slate-200 bg-white'}">
                    <input type="radio" name="detail_vertical_scheme" value="${this.escapeAttr(item.key)}" data-detail-vertical-scheme ${selected ? 'checked' : ''} class="mt-0.5 h-4 w-4 border-slate-300 text-fuchsia-700 focus:ring-fuchsia-500">
                    <span class="min-w-0">
                        <span class="block text-xs font-black text-slate-900">${this.escape(item.label)} · ${this.escape(item.name)}</span>
                        <span class="mt-1 block text-[11px] leading-4 text-slate-500">${this.escape(item.description)}</span>
                    </span>
                </label>
            `;
        }).join('');
        return `
            <section class="rounded-xl border border-indigo-100 bg-indigo-50/40 p-4 shadow-sm">
                <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div>
                        <div class="text-sm font-black text-indigo-950">成片方案中心</div>
                        <div class="mt-1 text-xs text-indigo-700">先按横屏、竖屏选择方案；所有方案复用现有音频、文案和素材，不重新跑 Gemini 与豆包朗读。</div>
                    </div>
                    ${!canRequestVertical ? `<span class="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-bold text-amber-700">需要先有音频或视频文件</span>` : ''}
                </div>
                ${keyframeMode && !videoUrl ? `
                    <div class="mb-4 rounded-xl border border-indigo-200 bg-white p-4">
                        <div class="text-xs font-black text-indigo-950">关键帧确认与批量成片</div>
                        <div class="mt-1 text-xs leading-5 text-slate-600">横屏和竖屏分别勾选，可一次提交多个成片版本。</div>
                        <div class="mt-3 grid gap-4 xl:grid-cols-2" data-detail-final-video-options>
                            <fieldset>
                                <legend class="mb-2 text-xs font-black text-indigo-900"><i class="fas fa-display mr-1"></i>横屏 · 16:9</legend>
                                <label class="flex cursor-pointer items-start gap-2 rounded-md border border-indigo-100 bg-indigo-50/60 px-3 py-2 text-xs text-indigo-950">
                                    <input type="checkbox" value="landscape" data-detail-final-video-option ${landscapeSelected ? 'checked' : ''} class="mt-0.5 h-4 w-4 rounded border-slate-300 text-indigo-700 focus:ring-indigo-500">
                                    <span><span class="block font-black">方案 1 · 研报截图成片</span><span class="mt-0.5 block text-[11px] font-normal text-indigo-700">使用已确认的研报截图关键帧生成横屏 MP4。</span></span>
                                </label>
                            </fieldset>
                            <fieldset>
                                <legend class="mb-2 text-xs font-black text-fuchsia-900"><i class="fas fa-mobile-screen mr-1"></i>竖屏 · 9:16</legend>
                                <div class="grid gap-2">${finalVerticalOptions}</div>
                            </fieldset>
                        </div>
                        <button type="button" data-detail-action="confirm-keyframes-generate-final" data-job-id="${this.escapeAttr(jobId)}" ${!canRequestVertical || verticalActive || finalVideoActive ? 'disabled' : ''} class="mt-2 rounded-md bg-indigo-700 px-3 py-2 text-xs font-bold text-white hover:bg-indigo-800 disabled:cursor-wait disabled:opacity-60">
                            <i class="fas ${finalVideoActive ? 'fa-spinner fa-spin' : 'fa-circle-check'} mr-1"></i>${finalVideoActive ? '所选成片生成中' : '确认关键帧，生成所选成片'}
                        </button>
                    </div>
                ` : ''}
                <div class="grid gap-4 xl:grid-cols-2">
                    <section class="rounded-xl border border-indigo-200 bg-white p-4">
                        <div class="flex items-start justify-between gap-3">
                            <div>
                                <h3 class="text-sm font-black text-indigo-950"><i class="fas fa-display mr-2 text-indigo-600"></i>横屏方案 <span class="text-xs font-bold text-slate-400">16:9</span></h3>
                                <p class="mt-1 text-xs text-slate-500">截图成片与视觉方案横屏视频统一在这里管理。</p>
                            </div>
                            ${videoUrl ? '<span class="rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-bold text-emerald-700">已有横屏成片</span>' : ''}
                        </div>
                        <div class="mt-3 space-y-3">
                            <article class="rounded-lg border border-indigo-200 bg-indigo-50/60 p-3">
                                <div class="text-xs font-black text-slate-900">方案 1 · 研报截图成片</div>
                                <div class="mt-1 text-[11px] leading-4 text-slate-500">使用研报页面、标题、字幕和整篇音频生成标准横屏视频。</div>
                                <div class="mt-3 flex flex-wrap gap-2">
                                    ${coverUrl ? `<a href="${this.absoluteUrl(coverUrl)}" download class="rounded-md border border-indigo-200 bg-white px-3 py-1.5 text-xs font-bold text-indigo-700 hover:bg-indigo-50"><i class="fas fa-image mr-1"></i>横版封面</a>` : ''}
                                    <button type="button" data-detail-action="regenerate-cover" data-job-id="${this.escapeAttr(jobId)}" ${coverRegenerating ? 'disabled' : ''} class="rounded-md border border-indigo-200 bg-white px-3 py-1.5 text-xs font-bold text-indigo-700 hover:bg-indigo-50 disabled:cursor-wait disabled:opacity-60">
                                        <i class="fas ${coverRegenerating ? 'fa-spinner fa-spin' : 'fa-wand-magic-sparkles'} mr-1"></i>${coverRegenerating ? '生成中' : '重做横版封面'}
                                    </button>
                                    ${videoUrl ? `<a href="${this.absoluteUrl(videoUrl)}" download class="rounded-md bg-indigo-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-indigo-800"><i class="fas fa-film mr-1"></i>下载横屏 MP4</a>` : ''}
                                    ${bilibiliCoverUrl ? `<a href="${this.absoluteUrl(bilibiliCoverUrl)}" download class="rounded-md border border-pink-200 bg-white px-3 py-1.5 text-xs font-bold text-pink-700 hover:bg-pink-50"><i class="fas fa-tv mr-1"></i>B站封皮</a>` : ''}
                                </div>
                            </article>
                            <div>
                                <div class="mb-2 text-[11px] font-black uppercase tracking-wide text-violet-700">视觉方案注册表</div>
                                <div class="grid gap-2">${visualWorkflowEntries}</div>
                            </div>
                        </div>
                    </section>
                    <section class="rounded-xl border border-fuchsia-200 bg-white p-4">
                        <div class="flex items-start justify-between gap-3">
                            <div>
                                <h3 class="text-sm font-black text-fuchsia-950"><i class="fas fa-mobile-screen mr-2 text-fuchsia-600"></i>竖屏方案 <span class="text-xs font-bold text-slate-400">9:16</span></h3>
                                <p class="mt-1 text-xs text-slate-500">先选择竖屏模板，再生成 10 秒预览或完整成片。</p>
                            </div>
                            ${verticalVideoItems.length || verticalVideoUrl ? '<span class="rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-bold text-emerald-700">已有竖屏成片</span>' : ''}
                        </div>
                        <div class="mt-3 grid gap-2">${verticalSchemeCards}</div>
                        ${scheme === 'scheme3' ? `
                            <label class="mt-3 block rounded-lg border border-violet-200 bg-violet-50 p-3 text-xs font-bold text-violet-900">
                                方案三视觉素材源
                                <select data-detail-visual-scheme class="mt-2 w-full rounded-md border border-violet-200 bg-white px-2.5 py-2 text-xs font-bold text-violet-800" aria-label="方案三视觉素材源">
                                    ${this.visualSchemes.map((item) => `<option value="${this.escapeAttr(item.key)}" ${item.key === this.selectedVisualSchemeKey ? 'selected' : ''} ${item.configured ? '' : 'disabled'}>${this.escape(item.name)}${item.configured ? '' : '（待配置 Gem）'}</option>`).join('')}
                                </select>
                                <span class="mt-1 block text-[11px] font-normal text-violet-700">当前使用：${this.escape(selectedVisualScheme?.name || '白板火柴人')}。需先在左侧创建并完成对应视觉任务。</span>
                            </label>
                        ` : ''}
                        <div class="mt-3 flex flex-wrap gap-2">
                            <button type="button" data-detail-action="generate-vertical-preview" data-preview-seconds="10" data-job-id="${this.escapeAttr(jobId)}" ${!canRequestVertical || verticalActive ? 'disabled' : ''} class="rounded-md border border-fuchsia-200 bg-white px-3 py-1.5 text-xs font-bold text-fuchsia-700 hover:bg-fuchsia-50 disabled:cursor-wait disabled:opacity-60">
                                <i class="fas ${verticalActive ? 'fa-spinner fa-spin' : 'fa-bolt'} mr-1"></i>${verticalActive ? '竖屏生成中' : '生成 10 秒预览'}
                            </button>
                            <button type="button" data-detail-action="generate-vertical-full" data-job-id="${this.escapeAttr(jobId)}" ${!canRequestVertical || verticalActive ? 'disabled' : ''} class="rounded-md bg-fuchsia-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-fuchsia-800 disabled:cursor-wait disabled:opacity-60">
                                <i class="fas fa-mobile-screen-button mr-1"></i>${verticalVideoUrl ? '重新生成完整竖屏' : '生成完整竖屏'}
                            </button>
                            ${verticalCoverUrl ? `<a href="${this.absoluteUrl(verticalCoverUrl)}" download class="rounded-md border border-emerald-200 bg-white px-3 py-1.5 text-xs font-bold text-emerald-700 hover:bg-emerald-50"><i class="fas fa-image-portrait mr-1"></i>竖版封皮</a>` : ''}
                            ${podcastCoverUrl ? `<a href="${this.absoluteUrl(podcastCoverUrl)}" download class="rounded-md border border-amber-200 bg-white px-3 py-1.5 text-xs font-bold text-amber-700 hover:bg-amber-50"><i class="fas fa-record-vinyl mr-1"></i>播客封皮</a>` : ''}
                            ${!podcastCoverUrl ? `<button type="button" data-detail-action="generate-podcast-cover" data-job-id="${this.escapeAttr(jobId)}" ${podcastGenerating ? 'disabled' : ''} class="rounded-md border border-amber-200 bg-white px-3 py-1.5 text-xs font-bold text-amber-700 hover:bg-amber-50 disabled:cursor-wait disabled:opacity-60"><i class="fas ${podcastGenerating ? 'fa-spinner fa-spin' : 'fa-record-vinyl'} mr-1"></i>${podcastGenerating ? '封皮生成中' : '补生成播客封皮'}</button>` : ''}
                        </div>
                        ${(verticalVideoItems.length || verticalVideoUrl || verticalPreviewItems.length || verticalPreviewVideoUrl) ? `
                            <div class="mt-3 border-t border-fuchsia-100 pt-3">
                                <div class="mb-2 text-[11px] font-black text-slate-500">已生成文件</div>
                                <div class="flex flex-wrap gap-2">
                                    ${verticalVideoItems.length ? verticalVideoItems.map((item) => `<a href="${this.absoluteUrl(item.download_url)}" download class="rounded-md bg-fuchsia-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-fuchsia-800"><i class="fas fa-mobile-screen mr-1"></i>${this.escape(item.label || this.verticalSchemeLabel(item.scheme))} MP4</a>`).join('') : (verticalVideoUrl ? `<a href="${this.absoluteUrl(verticalVideoUrl)}" download class="rounded-md bg-fuchsia-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-fuchsia-800"><i class="fas fa-mobile-screen mr-1"></i>竖屏 MP4</a>` : '')}
                                    ${verticalPreviewItems.length ? verticalPreviewItems.map((item) => `<a href="${this.absoluteUrl(item.download_url)}" download class="rounded-md border border-fuchsia-200 bg-white px-3 py-1.5 text-xs font-bold text-fuchsia-700 hover:bg-fuchsia-50"><i class="fas fa-clock mr-1"></i>${this.escape(item.label || this.verticalSchemeLabel(item.scheme))} 10秒预览</a>`).join('') : (verticalPreviewVideoUrl ? `<a href="${this.absoluteUrl(verticalPreviewVideoUrl)}" download class="rounded-md border border-fuchsia-200 bg-white px-3 py-1.5 text-xs font-bold text-fuchsia-700 hover:bg-fuchsia-50"><i class="fas fa-clock mr-1"></i>10秒预览</a>` : '')}
                                </div>
                            </div>
                        ` : ''}
                    </section>
                </div>
                ${verticalChild?.error ? `<div class="mt-2 rounded border border-red-100 bg-white/80 px-2 py-1 text-xs text-red-700">完整竖屏失败：${this.escape(verticalChild.error)}</div>` : ''}
                ${previewChild?.error ? `<div class="mt-2 rounded border border-red-100 bg-white/80 px-2 py-1 text-xs text-red-700">10秒预览失败：${this.escape(previewChild.error)}</div>` : ''}
            </section>
        `;
    }

    currentDetailVerticalScheme() {
        const select = (
            this.els.root?.querySelector('[data-detail-vertical-scheme]:checked')
            || this.els.root?.querySelector('[data-detail-vertical-scheme]')
        );
        return this.normalizeVerticalScheme(select?.value || this.job?.request?.video?.vertical_video_scheme || this.job?.video?.vertical_video_scheme);
    }

    currentFinalVideoSelection() {
        const selected = Array.from(this.els.root?.querySelectorAll('[data-detail-final-video-option]:checked') || [])
            .map((input) => String(input.value || '').trim());
        return {
            includeLandscape: selected.includes('landscape'),
            schemes: ['scheme1', 'scheme2', 'scheme3'].filter((scheme) => selected.includes(scheme)),
        };
    }

    async openStickFigureWorkflow(jobId, visualSchemeKey = '') {
        if (!jobId || this.stickWorkflowRequestIds.has(jobId)) return;
        const requestedSchemeKey = String(visualSchemeKey || this.selectedVisualSchemeKey || 'stick_figure');
        this.selectedVisualSchemeKey = requestedSchemeKey;
        const detailWindow = window.open('about:blank', '_blank');
        if (detailWindow) detailWindow.opener = null;
        this.stickWorkflowRequestIds.add(jobId);
        this.render();
        try {
            const result = await this.request(`/api/report-stick-figure-video-workflows/jobs/from-report-audio/${encodeURIComponent(jobId)}`, {
                method: 'POST',
                timeoutMs: 30000,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ force_new: false, visual_scheme_key: requestedSchemeKey }),
            });
            const workflowJobId = result.workflow_job_id || result.job_id || result.data?.workflow_job_id || result.data?.job_id;
            if (!workflowJobId) throw new Error('后端未返回视觉编排任务 ID');
            const target = new URL('/report-stick-figure-video-workflow-detail.html', window.location.href);
            target.searchParams.set('workflow_job_id', workflowJobId);
            if (detailWindow && !detailWindow.closed) {
                detailWindow.location.replace(target.href);
            } else {
                window.open(target.href, '_blank', 'noopener');
            }
            this.setNotice(result.message || '已复用整篇文案和 WAV，视觉方案已打开。', 'success');
            await this.load({ manual: true });
        } catch (error) {
            if (detailWindow && !detailWindow.closed) detailWindow.close();
            this.setNotice(`视觉方案创建失败：${error.message}`, 'error');
        } finally {
            this.stickWorkflowRequestIds.delete(jobId);
            this.render();
        }
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
        const hasExplicitSchemes = Array.isArray(options.schemes);
        const schemes = hasExplicitSchemes
            ? [...new Set(options.schemes.map((item) => this.normalizeVerticalScheme(item)))]
            : [this.normalizeVerticalScheme(options.scheme || this.currentDetailVerticalScheme())];
        if (schemes.length > 2) {
            this.setNotice('竖屏方案一次最多选择两个，请取消一个后重试。', 'warning');
            return;
        }
        const scheme = schemes.includes('scheme3')
            ? 'scheme3'
            : (schemes.includes('scheme2') ? 'scheme2' : (schemes[0] || this.currentDetailVerticalScheme()));
        const visualVideo = this.selectedCompletedVisualVideo();
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
                    vertical_video_schemes: schemes,
                    preview_seconds: previewSeconds,
                    force: options.force !== false,
                    include_landscape: options.includeLandscape === true,
                    selection_source: options.selectionSource || 'vertical-video-panel',
                    ...(schemes.includes('scheme3')
                        ? {
                            visual_scheme_key: this.selectedVisualSchemeKey || 'stick_figure',
                            ...(visualVideo?.workflow_job_id
                                ? { visual_workflow_id: visualVideo.workflow_job_id }
                                : {}),
                        }
                        : {}),
                }),
            });
            this.mergeJobPayload(result.data || result);
            this.render();
            await this.load();
            this.setNotice(result.message || `竖屏 MP4 已提交生成（${this.verticalSchemeLabel(scheme)}${isPreview ? ` · ${previewSeconds}s预览` : ''}）。`, 'success');
        } catch (error) {
            const key = isPreview ? 'publisher_vertical_preview_video' : 'publisher_vertical_video';
            // The enqueue endpoint is durable before it builds the response. If
            // that final response is interrupted, reload once and trust the
            // persisted child instead of showing a false submission failure.
            try {
                await this.load({ manual: true });
                const persistedChild = this.job?.children?.[key] || this.job?.[key];
                if (persistedChild && !['failed', 'cancelled', 'canceled'].includes(String(persistedChild.status || '').toLowerCase())) {
                    this.setNotice(`竖屏 MP4 已提交生成（${this.verticalSchemeLabel(scheme)}${isPreview ? ` · ${previewSeconds}s预览` : ''}），提交响应曾中断，已从任务记录确认。`, 'success');
                    return;
                }
            } catch (_) {
                // Fall through to the original error when status verification
                // is unavailable as well.
            }
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

    async generateFinalVideos(jobId) {
        if (!jobId || this.finalVideoRequestIds.has(jobId)) return;
        const selection = this.currentFinalVideoSelection();
        if (!selection.includeLandscape && selection.schemes.length === 0) {
            this.setNotice('请至少选择一个成片版本。', 'error');
            return;
        }
        this.finalVideoRequestIds.add(jobId);
        this.render();
        try {
            await this.generateVerticalVideo(jobId, {
                schemes: selection.schemes,
                previewSeconds: 0,
                force: true,
                includeLandscape: selection.includeLandscape,
                selectionSource: 'keyframe-confirmation',
            });
        } finally {
            this.finalVideoRequestIds.delete(jobId);
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

    async regenerateCoverAsset(jobId, coverKey, button) {
        const typeMap = {
            cover: 'landscape',
            vertical_cover: 'vertical',
            podcast_cover: 'podcast',
            bilibili_cover: 'bilibili',
        };
        const verticalBackground = String(coverKey || '').startsWith('vertical_background');
        const coverType = verticalBackground ? 'vertical_background' : typeMap[coverKey];
        const scheme = button?.dataset.coverScheme || '';
        const requestKey = `${jobId}:${coverType || coverKey}`;
        if (!jobId || !coverType || this.coverRegeneratingIds.has(requestKey)) return;
        const editor = button?.closest('[data-cover-editor]');
        const title = this.compactMultilineCoverText(editor?.querySelector('[data-cover-title]')?.value || '');
        const subtitle = this.compactMultilineCoverText(editor?.querySelector('[data-cover-subtitle]')?.value || '');
        if (!title) {
            this.setNotice('请先填写封皮主标题。', 'warning');
            editor?.querySelector('[data-cover-title]')?.focus();
            return;
        }

        this.coverRegeneratingIds.add(requestKey);
        if (button) {
            button.disabled = true;
            button.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>生成中';
        }
        try {
            const result = await this.request(`/api/report-audio-workflows/jobs/${encodeURIComponent(jobId)}/regenerate-cover-asset`, {
                method: 'POST',
                timeoutMs: 45000,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ cover_type: coverType, title, subtitle, scheme }),
            });
            this.mergeJobPayload(result.data || result);
            this.render();
            await this.load({ manual: true });
            this.setNotice(`${this.coverAssetLabel(coverKey)}已重新生成。`, 'success');
        } catch (error) {
            this.setNotice(`${this.coverAssetLabel(coverKey)}生成失败：${error.message}`, 'error');
        } finally {
            this.coverRegeneratingIds.delete(requestKey);
            if (button?.isConnected) {
                button.disabled = false;
                button.innerHTML = '<i class="fas fa-wand-magic-sparkles mr-1"></i>重新生成';
            }
        }
    }

    renderVisualVideos(job) {
        const items = Array.isArray(job?.visual_videos) ? job.visual_videos : [];
        if (!items.length) return '';
        return `
            <section class="rounded-lg border border-violet-200 bg-violet-50/40 p-4 shadow-sm" data-visual-video-variants>
                <div class="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <div class="text-sm font-black text-slate-900">同一 PDF 的视觉方案视频</div>
                        <div class="mt-1 text-xs leading-5 text-slate-500">自动归集从视觉编排台或当前研报语音任务生成的火柴人、哆啦A梦等方案。</div>
                    </div>
                    <span class="rounded-full bg-violet-100 px-2.5 py-1 text-xs font-black text-violet-700">${items.length} 个方案</span>
                </div>
                <div class="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
                    ${items.map((item) => {
                        const scheme = item?.visual_scheme || {};
                        const status = this.statusMeta(item?.status);
                        const video = item?.video || null;
                        const progress = Math.max(0, Math.min(100, Number(item?.progress || 0)));
                        const detailUrl = this.absoluteUrl(item?.detail_url || '');
                        const sourceLabel = item?.source === 'report_audio'
                            ? '由本研报语音任务创建'
                            : '同 PDF 自动归集';
                        return `
                            <article class="overflow-hidden rounded-lg border border-violet-100 bg-white shadow-sm" data-visual-video-workflow-id="${this.escapeAttr(item?.workflow_job_id || '')}">
                                <div class="flex flex-wrap items-start justify-between gap-2 border-b border-slate-100 px-4 py-3">
                                    <div class="min-w-0">
                                        <div class="truncate text-sm font-black text-slate-900">${this.escape(scheme.name || scheme.key || '视觉方案')}</div>
                                        <div class="mt-1 text-[11px] text-slate-500">${this.escape(sourceLabel)}${item?.created_at ? ` · ${this.escape(this.formatDateTime(item.created_at))}` : ''}</div>
                                    </div>
                                    <span class="rounded-full px-2 py-1 text-[11px] font-black ${status.className}">${this.escape(item?.status === 'ready' ? '待合成' : status.label)}</span>
                                </div>
                                <div class="p-4">
                                    ${video?.url ? `
                                        <video controls preload="metadata" class="aspect-video w-full rounded-md bg-black object-contain" src="${this.absoluteUrl(video.url)}"></video>
                                    ` : `
                                        <div class="rounded-md border border-dashed border-violet-200 bg-violet-50/60 p-3">
                                            <div class="flex items-center justify-between gap-3 text-xs font-bold text-violet-800">
                                                <span>${this.escape(item?.step || status.label)}</span>
                                                <span>${progress}%</span>
                                            </div>
                                            <div class="mt-2 h-1.5 overflow-hidden rounded-full bg-violet-100">
                                                <div class="h-full rounded-full ${status.bar}" style="width:${progress}%"></div>
                                            </div>
                                        </div>
                                    `}
                                    ${item?.error ? `<div class="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-bold leading-5 text-rose-700">${this.escape(item.error)}</div>` : ''}
                                    <div class="mt-3 flex flex-wrap gap-2">
                                        ${detailUrl ? `<a href="${detailUrl}" target="_blank" rel="noopener noreferrer" class="rounded-md border border-violet-200 bg-white px-3 py-2 text-xs font-bold text-violet-700 hover:bg-violet-50"><i class="fas fa-arrow-up-right-from-square mr-1"></i>视觉任务详情</a>` : ''}
                                        ${video?.url ? `<a href="${this.absoluteUrl(video.url)}" download class="rounded-md bg-violet-700 px-3 py-2 text-xs font-bold text-white hover:bg-violet-800"><i class="fas fa-download mr-1"></i>下载 ${this.escape(scheme.name || '视觉方案')} MP4${video.bytes ? ` · ${this.escape(this.formatBytes(video.bytes))}` : ''}</a>` : ''}
                                    </div>
                                </div>
                            </article>
                        `;
                    }).join('')}
                </div>
            </section>
        `;
    }

    mergeJobPayload(payload) {
        if (!payload || typeof payload !== 'object') return;
        const current = this.job || {};
        this.job = {
            ...current,
            ...payload,
            video: {
                ...(current.video || {}),
                ...(payload.video || {}),
            },
            structured_result: {
                ...(current.structured_result || {}),
                ...(payload.structured_result || {}),
            },
        };
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
                        <audio controls src="${this.previewUrl(audioUrl)}" class="w-full"></audio>
                    </div>
                ` : ''}
                ${videoItems.length ? `
                    <div class="grid grid-cols-1 gap-3 lg:grid-cols-2">
                        ${videoItems.map((item) => `
                            <div class="rounded-md border border-slate-200 bg-slate-50 p-3">
                                <div class="mb-2 text-xs font-bold text-slate-600">${this.escape(item.label)}</div>
                                <video controls preload="metadata" src="${this.previewUrl(item.url)}" class="max-h-[360px] w-full rounded bg-black"></video>
                            </div>
                        `).join('')}
                    </div>
                ` : ''}
                ${coverItems.length ? `
                    <div class="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
                        ${coverItems.map((item) => {
                            const draft = this.coverAssetDraft(job, item.key);
                            return `
                            <div data-cover-editor="${this.escapeAttr(item.key)}" class="rounded-md border border-slate-200 bg-slate-50 p-3">
                                <a href="${this.absoluteUrl(item.url)}" target="_blank" rel="noopener noreferrer" class="block hover:opacity-90">
                                    <img src="${this.previewUrl(item.url, item.version)}" alt="${this.escapeAttr(item.label)}" class="h-56 w-full rounded bg-white object-contain">
                                </a>
                                <div class="mt-2 text-xs font-black text-slate-700">${this.escape(item.label)}</div>
                                <div class="mt-3 grid gap-2">
                                    <label class="text-[11px] font-bold text-slate-500">
                                        主标题
                                        <textarea data-cover-title rows="2" class="mt-1 w-full resize-y rounded border border-slate-300 bg-white px-2.5 py-2 text-sm font-semibold leading-5 text-slate-800 outline-none focus:border-indigo-400">${this.escape(draft.title)}</textarea>
                                    </label>
                                    <label class="text-[11px] font-bold text-slate-500">
                                        副标题
                                        <input data-cover-subtitle type="text" value="${this.escapeAttr(draft.subtitle)}" class="mt-1 w-full rounded border border-slate-300 bg-white px-2.5 py-2 text-sm text-slate-800 outline-none focus:border-indigo-400">
                                    </label>
                                    <button type="button" data-detail-action="regenerate-cover-asset" data-cover-key="${this.escapeAttr(item.key)}" data-job-id="${this.escapeAttr(job.job_id || job.id || this.jobId)}" class="justify-self-end rounded-md bg-indigo-700 px-3 py-2 text-xs font-bold text-white hover:bg-indigo-800 disabled:cursor-wait disabled:opacity-60">
                                        <i class="fas fa-wand-magic-sparkles mr-1"></i>重新生成当前封皮
                                    </button>
                                </div>
                            </div>
                        `;
                        }).join('')}
                    </div>
                ` : ''}
                ${keyframeItems.length ? `
                    <div class="mt-4">
                        <div class="mb-2 text-xs font-black text-slate-500">关键帧预览</div>
                        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                            ${keyframeItems.map((item) => {
                                if (!item.editable) return `
                                    <a href="${this.absoluteUrl(item.url)}" target="_blank" rel="noopener noreferrer" class="block rounded-md border border-slate-200 bg-slate-50 p-2 hover:border-emerald-200">
                                        <img src="${this.previewUrl(item.url, item.version)}" alt="${this.escapeAttr(item.label)}" class="h-48 w-full rounded object-contain">
                                        <div class="mt-2 text-xs font-bold text-slate-600">${this.escape(item.label)}</div>
                                    </a>
                                `;
                                const draft = this.coverAssetDraft(job, item.key);
                                return `
                                    <div data-cover-editor="${this.escapeAttr(item.key)}" class="rounded-md border border-indigo-200 bg-indigo-50/40 p-2">
                                        <a href="${this.absoluteUrl(item.url)}" target="_blank" rel="noopener noreferrer" class="block hover:opacity-90">
                                            <img src="${this.previewUrl(item.url, item.version)}" alt="${this.escapeAttr(item.label)}" class="h-48 w-full rounded bg-white object-contain">
                                        </a>
                                        <div class="mt-2 text-xs font-black text-slate-700">${this.escape(item.label)}</div>
                                        <label class="mt-2 block text-[11px] font-bold text-slate-500">
                                            主标题
                                            <textarea data-cover-title rows="2" class="mt-1 w-full resize-y rounded border border-slate-300 bg-white px-2 py-1.5 text-xs font-semibold leading-5 text-slate-800 outline-none focus:border-indigo-400">${this.escape(draft.title)}</textarea>
                                        </label>
                                        <label class="mt-2 block text-[11px] font-bold text-slate-500">
                                            副标题
                                            <input data-cover-subtitle type="text" value="${this.escapeAttr(draft.subtitle)}" class="mt-1 w-full rounded border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-800 outline-none focus:border-indigo-400">
                                        </label>
                                        <button type="button" data-detail-action="regenerate-cover-asset" data-cover-key="${this.escapeAttr(item.key)}" data-cover-scheme="${this.escapeAttr(item.scheme)}" data-job-id="${this.escapeAttr(job.job_id || job.id || this.jobId)}" class="mt-2 w-full rounded-md bg-indigo-700 px-2 py-2 text-xs font-bold text-white hover:bg-indigo-800 disabled:cursor-wait disabled:opacity-60">
                                            <i class="fas fa-wand-magic-sparkles mr-1"></i>重新生成竖屏背景
                                        </button>
                                    </div>
                                `;
                            }).join('')}
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
                ${this.renderTitleReview(structured.title_review)}
            </section>
        `;
    }

    renderTitleReview(review) {
        if (!review || typeof review !== 'object') return '';
        const selected = review.selected && typeof review.selected === 'object' ? review.selected : {};
        const status = String(review.status || '');
        const statusMeta = status === 'completed'
            ? { label: '评审完成', classes: 'border-emerald-200 bg-emerald-50 text-emerald-700' }
            : status === 'completed_with_generation_fallback'
                ? { label: '基础候选评审完成', classes: 'border-amber-200 bg-amber-50 text-amber-700' }
                : { label: '沿用原有标题', classes: 'border-slate-200 bg-slate-100 text-slate-600' };
        const selectedCards = [
            ['横屏采用', selected.landscape],
            ['竖屏 / B站采用', selected.vertical],
            ['备用方案', selected.backup],
        ].filter(([, item]) => item && item.title);
        const candidates = Array.isArray(review.candidates)
            ? review.candidates
                .slice()
                .sort((a, b) => Math.max(Number(b.landscape_score || 0), Number(b.vertical_score || 0))
                    - Math.max(Number(a.landscape_score || 0), Number(a.vertical_score || 0)))
                .slice(0, 8)
            : [];
        return `
            <div class="mt-4 rounded-lg border border-indigo-100 bg-indigo-50/40 p-3">
                <div class="flex flex-wrap items-center justify-between gap-2">
                    <div>
                        <div class="text-xs font-black text-indigo-950">标题独立评审</div>
                        <div class="mt-1 text-[11px] text-slate-500">
                            ${this.escape(review.review_model || review.generation_model || '')}
                            ${review.original_pdf_title ? ' · PDF 原题作为基准候选' : ''}
                        </div>
                    </div>
                    <span class="rounded-full border px-2 py-1 text-[11px] font-bold ${statusMeta.classes}">${statusMeta.label}</span>
                </div>
                ${review.original_pdf_title ? `
                    <div class="mt-3 rounded-md border border-slate-200 bg-white px-3 py-2">
                        <div class="text-[10px] font-black uppercase tracking-wide text-slate-400">PDF 原始标题</div>
                        <div class="mt-1 text-xs font-semibold leading-5 text-slate-700">${this.escape(review.original_pdf_title)}</div>
                    </div>
                ` : ''}
                ${selectedCards.length ? `
                    <div class="mt-3 grid gap-2 lg:grid-cols-3">
                        ${selectedCards.map(([label, item]) => `
                            <div class="rounded-md border border-indigo-100 bg-white p-3">
                                <div class="flex items-center justify-between gap-2">
                                    <div class="text-[10px] font-black uppercase tracking-wide text-indigo-500">${this.escape(label)}</div>
                                    <button type="button" data-copy-text="${this.escapeAttr([item.title, item.subtitle].filter(Boolean).join('\n'))}" class="text-[11px] font-bold text-indigo-600 hover:text-indigo-800">复制</button>
                                </div>
                                <div class="mt-1 whitespace-pre-line text-sm font-black leading-5 text-slate-900">${this.escape(item.title)}</div>
                                ${item.subtitle ? `<div class="mt-1 text-xs leading-5 text-slate-500">${this.escape(item.subtitle)}</div>` : ''}
                                <div class="mt-2 text-[10px] text-slate-400">
                                    横屏 ${Number(item.landscape_score || 0)} · 竖屏 ${Number(item.vertical_score || 0)}
                                    ${item.source ? ` · ${this.escape(item.source)}` : ''}
                                </div>
                                ${item.reason ? `<div class="mt-1 text-[11px] leading-4 text-slate-500">${this.escape(item.reason)}</div>` : ''}
                            </div>
                        `).join('')}
                    </div>
                ` : ''}
                ${review.decision_summary ? `<div class="mt-3 text-xs leading-5 text-slate-600"><span class="font-bold">选择理由：</span>${this.escape(review.decision_summary)}</div>` : ''}
                ${review.review_error ? `<div class="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">评审未生效：${this.escape(review.review_error)}</div>` : ''}
                ${candidates.length ? `
                    <details class="mt-3">
                        <summary class="cursor-pointer text-xs font-bold text-indigo-700">查看候选评分（${this.formatNumber(review.candidates.length)} 条）</summary>
                        <div class="mt-2 overflow-x-auto rounded-md border border-slate-200 bg-white">
                            <table class="min-w-full text-left text-[11px]">
                                <thead class="bg-slate-50 text-slate-500">
                                    <tr><th class="px-3 py-2">候选</th><th class="px-3 py-2">横屏</th><th class="px-3 py-2">竖屏</th><th class="px-3 py-2">评语 / 淘汰原因</th></tr>
                                </thead>
                                <tbody class="divide-y divide-slate-100">
                                    ${candidates.map((item) => `
                                        <tr>
                                            <td class="max-w-sm whitespace-pre-line px-3 py-2 font-bold text-slate-800">${this.escape(item.title || '')}</td>
                                            <td class="px-3 py-2">${Number(item.landscape_score || 0)}</td>
                                            <td class="px-3 py-2">${Number(item.vertical_score || 0)}</td>
                                            <td class="max-w-md px-3 py-2 leading-4 text-slate-500">${this.escape(item.rejection_reason || item.reason || '-')}</td>
                                        </tr>
                                    `).join('')}
                                </tbody>
                            </table>
                        </div>
                    </details>
                ` : ''}
            </div>
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
        add('landscape', '横屏视频', job.download_video_url || job.video?.video?.download_url, this.variantBiliTitle(job, 'landscape'));
        add('vertical', '竖屏视频', job.download_vertical_video_url || job.video?.vertical_video?.download_url, this.variantBiliTitle(job, 'vertical'));
        return items;
    }

    biliUploadVideoOptions(job) {
        const items = [...this.biliVideoOptions(job)];
        const seen = new Set(items.map((item) => item.key));
        for (const item of (Array.isArray(job?.visual_videos) ? job.visual_videos : [])) {
            const workflowId = String(item?.workflow_job_id || '').trim();
            const videoUrl = item?.video?.url || '';
            if (!workflowId || !videoUrl) continue;
            const key = `visual:${workflowId.replace(/-/g,'').slice(0,16)}`;
            if (seen.has(key)) continue;
            seen.add(key);
            const schemeName = item?.visual_scheme?.name || item?.visual_scheme?.key || '视觉方案';
            items.push({
                key,
                label: `${schemeName}视频`,
                url: videoUrl,
                visualWorkflowId: workflowId,
                defaultTitle: this.variantBiliTitle(job, 'landscape'),
            });
        }
        return items;
    }

    biliCoverOptions(job, videoKey = '') {
        const entries = [];
        const video = job?.video || {};
        const versions = {
            cover: video.cover_regenerated_at || '',
            vertical_cover: video.vertical_cover_regenerated_at || '',
            podcast_cover: video.podcast_cover_regenerated_at || '',
            bilibili_cover: video.bilibili_cover_regenerated_at || '',
        };
        const add = (key, label, url) => {
            if (url) entries.push({ key, label, url, version: versions[key] || '' });
        };
        add('bilibili_cover', 'B站双比例安全封皮', this.bilibiliCoverUrl(job));
        add('cover', '横屏封面', job.download_cover_url || job.video?.cover?.download_url);
        add('vertical_cover', '竖版封皮', job.download_vertical_cover_url || job.video?.vertical_cover?.download_url);
        add('podcast_cover', '播客封皮', job.download_podcast_cover_url || job.video?.podcast_cover?.download_url);
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
                title: item.title || '',
                subtitle: item.subtitle || '',
                version: item.updated_at || '',
                editable: key.startsWith('vertical_background'),
                scheme: item.scheme || (key.endsWith('_scheme1') ? 'scheme1' : (key.endsWith('_scheme2') ? 'scheme2' : (key.endsWith('_scheme3') ? 'scheme3' : this.currentDetailVerticalScheme()))),
            }));
    }

    defaultBiliTitle(job) {
        return this.compactTitle(this.displayTitle(job) || job.cover_title || job.pdf?.topic_title || job.pdf?.filename || '研报解读').slice(0, 120);
    }

    variantBiliTitle(job, videoKey) {
        const structured = job?.structured_result || {};
        const backendTitle = videoKey === 'vertical'
            ? job?.bilibili_titles?.vertical
            : job?.bilibili_titles?.landscape;
        if (this.compactTitle(backendTitle)) return this.compactTitle(backendTitle).slice(0, 120);
        const candidates = videoKey === 'vertical'
            ? [structured.vertical_title, job?.video?.vertical_title, structured.cover_title]
            : [structured.landscape_title, job?.video?.landscape_title, structured.cover_title];
        const selected = candidates.map((value) => this.compactTitle(value)).find(Boolean);
        const subtitleCandidates = videoKey === 'vertical'
            ? [structured.vertical_subtitle, job?.video?.vertical_subtitle, structured.cover_subtitle]
            : [structured.landscape_subtitle, job?.video?.landscape_subtitle, structured.cover_subtitle];
        const subtitle = subtitleCandidates.map((value) => this.compactTitle(value)).find(Boolean) || '';
        const separator = videoKey === 'vertical' ? ' | ' : ' / ';
        const institution = this.reportInstitution(job, selected);
        const mainParts = this.stripInstitution(selected || this.defaultBiliTitle(job), institution)
            .split(/\s*\/\s*/)
            .map((part) => part.trim())
            .filter(Boolean);
        return [institution, ...mainParts, subtitle, this.reportDate(job)]
            .filter(Boolean)
            .join(separator)
            .slice(0, 120);
    }

    reportInstitution(job, title = '') {
        const structured = job?.structured_result || {};
        const source = [title, structured.cover_title, structured.title, job?.pdf?.filename, job?.request?.pdf?.filename]
            .map((value) => String(value || ''))
            .join(' ');
        const aliases = [
            ['摩根士丹利', '大摩'], ['大摩', '大摩'], ['摩根大通', '摩根大通'], ['小摩', '摩根大通'],
            ['高盛', '高盛'], ['瑞银', '瑞银'], ['野村', '野村'], ['美国银行', '美银'], ['美银', '美银'], ['汇丰', '汇丰'],
            ['Jefferies', '杰富瑞'], ['杰富瑞', '杰富瑞'],
            ['Bernstein', '伯恩斯坦'], ['伯恩斯坦', '伯恩斯坦'], ['盛博', '伯恩斯坦'],
        ];
        const match = aliases.find(([alias]) => source.includes(alias));
        return match?.[1] || '机构研报';
    }

    stripInstitution(title, institution) {
        let text = this.compactTitle(title);
        const aliases = [institution, '摩根士丹利', '大摩', '摩根大通', '小摩', '高盛', '瑞银', '野村', '美国银行', '美银', '汇丰', 'Jefferies', '杰富瑞', 'Bernstein', '伯恩斯坦', '盛博'];
        for (const alias of aliases.filter(Boolean).sort((a, b) => b.length - a.length)) {
            const escaped = alias.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            text = text.replace(new RegExp(`^\\s*${escaped}(?:证券)?\\s*[:：]\\s*`, 'i'), '');
        }
        return text.replace(/^[|｜/：:\s]+|[|｜/：:\s]+$/g, '');
    }

    coverTitleWithInstitution(job, title) {
        const institution = this.reportInstitution(job, title);
        let body = this.compactMultilineCoverText(title);
        const genericPrefix = /^(?:机构|机构研报|深度研报)\s*[:：]/.test(body);
        const fullInstitutionAliases = [
            ['摩根士丹利', '大摩'],
            ['J.P. Morgan', '摩根大通'],
            ['JP Morgan', '摩根大通'],
            ['Goldman Sachs', '高盛'],
            ['Bank of America', '美银'],
            ['Jefferies', '杰富瑞'],
            ['Bernstein', '伯恩斯坦'],
        ];
        const explicitFullInstitution = fullInstitutionAliases.some(([alias, label]) => {
            const escaped = alias.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            return label === institution && new RegExp(`^\\s*${escaped}\\s*[:：]`, 'i').test(body);
        });
        if (explicitFullInstitution && !genericPrefix) return body;

        const aliases = [institution, '机构', '机构研报', '摩根士丹利', '大摩', '摩根大通', '小摩', '高盛', '瑞银', '野村', '美国银行', '美银', '汇丰', 'Jefferies', '杰富瑞', 'Bernstein', '伯恩斯坦', '盛博'];
        let previousBody = '';
        while (body !== previousBody) {
            previousBody = body;
            for (const alias of aliases.filter(Boolean).sort((a, b) => b.length - a.length)) {
                const escaped = alias.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                body = body.replace(new RegExp(`^\\s*${escaped}(?:证券)?\\s*[:：]\\s*`, 'i'), '');
            }
        }
        body = body.replace(/^[|｜/：:\s]+|[|｜/：:\s]+$/g, '');
        return `${institution || '机构'}：${body || '研报核心观点解读'}`;
    }

    reportDate(job) {
        const source = [job?.pdf?.filename, job?.request?.pdf?.filename, job?.pdf?.topic_title, job?.request?.pdf?.topic_title]
            .map((value) => String(value || ''))
            .join(' ');
        const full = source.match(/\b(20\d{2})[./-]?(\d{2})[./-]?(\d{2})\b/);
        if (full) return `${full[1]}-${full[2]}-${full[3]}`;
        const short = source.match(/(?:^|\D)(\d{2})(\d{2})(\d{2})(?:\D|$)/);
        if (short) return `20${short[1]}-${short[2]}-${short[3]}`;
        const created = new Date(job?.created_at || Date.now());
        if (Number.isNaN(created.getTime())) return '';
        const pad = (value) => String(value).padStart(2, '0');
        return `${created.getFullYear()}-${pad(created.getMonth() + 1)}-${pad(created.getDate())}`;
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
        if (['scheme3', '3', '方案三', 'visual_frame', 'visual-frame', 'video_bg', 'video-bg'].includes(text)) {
            return 'scheme3';
        }
        if (['scheme2', '2', '方案二', 'workbench', 'thumbnail_workbench', 'thumbnail-workbench'].includes(text)) {
            return 'scheme2';
        }
        return 'scheme1';
    }

    verticalSchemeLabel(value) {
        const key = this.normalizeVerticalScheme(value);
        return this.verticalVideoSchemes.find((item) => item.key === key)?.label || key;
    }

    selectedCompletedVisualVideo() {
        const items = Array.isArray(this.job?.visual_videos) ? this.job.visual_videos : [];
        const matching = items.filter((item) => (
            item?.visual_scheme?.key === this.selectedVisualSchemeKey
        ));
        return matching.find((item) => (
            item?.visual_scheme?.key === this.selectedVisualSchemeKey
            && (
                item?.scheme3_ready === true
                || item?.scene_assets?.direct_render_ready === true
                || (
                    item?.video?.url
                    && String(item?.status || '').toLowerCase() === 'succeeded'
                )
            )
        )) || matching[0] || null;
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

    coverAssetLabel(key) {
        if (String(key || '').startsWith('vertical_background')) return '竖屏视频背景';
        return {
            cover: '横屏封面',
            vertical_cover: '竖版封皮',
            podcast_cover: '播客封皮',
            bilibili_cover: 'B站封皮',
        }[key] || '封皮';
    }

    coverAssetDraft(job, key) {
        const video = job?.video || {};
        const structured = job?.structured_result || {};
        if (String(key || '').startsWith('vertical_background')) {
            const keyframe = video?.keyframes?.[key] || {};
            const scheme = keyframe.scheme || (String(key).endsWith('_scheme1') ? 'scheme1' : (String(key).endsWith('_scheme2') ? 'scheme2' : (String(key).endsWith('_scheme3') ? 'scheme3' : this.currentDetailVerticalScheme())));
            const titleMap = video.vertical_background_titles || {};
            const subtitleMap = video.vertical_background_subtitles || {};
            return {
                title: this.compactMultilineCoverText(keyframe.title || titleMap[scheme] || video.vertical_background_title || video.vertical_title || structured.vertical_title || structured.cover_title || this.coverTitleForEdit(job)),
                subtitle: this.compactMultilineCoverText(keyframe.subtitle || subtitleMap[scheme] || video.vertical_background_subtitle || video.vertical_subtitle || structured.vertical_subtitle || structured.cover_subtitle || this.coverSubtitleForEdit(job)),
            };
        }
        const titleCandidates = {
            cover: [video.cover_title, structured.landscape_title, structured.cover_title],
            vertical_cover: [video.vertical_cover_title, structured.vertical_title, structured.cover_title],
            podcast_cover: [video.podcast_cover_title, structured.vertical_title, structured.cover_title],
            bilibili_cover: [video.bilibili_cover_title, structured.vertical_title, structured.cover_title],
        }[key] || [];
        const subtitleCandidates = {
            cover: [video.cover_subtitle, structured.landscape_subtitle, structured.cover_subtitle],
            vertical_cover: [video.vertical_cover_subtitle, structured.vertical_subtitle, structured.cover_subtitle],
            podcast_cover: [video.podcast_cover_subtitle, structured.vertical_subtitle, structured.cover_subtitle],
            bilibili_cover: [video.bilibili_cover_subtitle, structured.vertical_subtitle, structured.cover_subtitle],
        }[key] || [];
        const selectedTitle = titleCandidates.map((value) => this.compactMultilineCoverText(value)).find(Boolean) || this.coverTitleForEdit(job);
        return {
            title: this.coverTitleWithInstitution(job, selectedTitle),
            subtitle: subtitleCandidates.map((value) => this.compactMultilineCoverText(value)).find(Boolean) || this.coverSubtitleForEdit(job),
        };
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
        if (normalized === 'ready') return { label: '待合成', className: 'bg-violet-50 text-violet-700', dot: 'bg-violet-500', bar: 'bg-violet-500' };
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

    previewUrl(path, version = '') {
        const url = this.absoluteUrl(path);
        if (url === '#') return url;
        const params = ['inline=1'];
        if (version) params.push(`v=${encodeURIComponent(String(version))}`);
        return `${url}${url.includes('?') ? '&' : '?'}${params.join('&')}`;
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
