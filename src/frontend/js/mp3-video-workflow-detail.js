class Mp3VideoWorkflowDetailPage {
    constructor() {
        this.jobId = new URLSearchParams(window.location.search).get('job_id') || '';
        this.job = null;
        this.biliCredentials = [];
        this.biliCredentialsError = '';
        this.biliChargingOptionsByCredential = {};
        this.biliChargingOptionsErrorByCredential = {};
        this.biliChargingOptionsPromises = {};
        this.biliChargingOptionsLoading = '';
        this.biliSeasonsByCredential = {};
        this.biliSeasonsErrorByCredential = {};
        this.biliSeasonsPromises = {};
        this.biliSeasonsLoading = '';
        this.biliPanel = null;
        this.biliPanelDraft = null;
        this.biliPanelDraftJobId = '';
        this.lastBiliUpload = null;
        this.douyinPanel = null;
        this.douyinPanelDraft = null;
        this.douyinPanelDraftJobId = '';
        this.wechatChannelsPanel = null;
        this.wechatChannelsPanelDraft = null;
        this.wechatChannelsPanelDraftJobId = '';
        this.activePublishPlatform = 'bilibili';
        this.publishTabs = null;
        this.refreshTimer = null;
        this.autoRefreshMs = 5000;
        this.quickNavObserver = null;
        this.els = {
            jobIdLabel: document.getElementById('job-id-label'),
            title: document.getElementById('detail-title'),
            subtitle: document.getElementById('detail-subtitle'),
            refreshBtn: document.getElementById('refresh-detail-btn'),
            openApiJson: document.getElementById('open-api-json'),
            notice: document.getElementById('notice'),
            root: document.getElementById('detail-root'),
            quickNav: document.getElementById('quick-nav'),
            regenerateDialog: document.getElementById('regenerate-video-dialog'),
            regenerateForm: document.getElementById('regenerate-video-form'),
            regenerateStart: document.getElementById('regenerate-video-start'),
            regenerateEnd: document.getElementById('regenerate-video-end'),
            regenerateSpeed: document.getElementById('regenerate-video-speed'),
            regenerateError: document.getElementById('regenerate-video-error'),
            regenerateCancel: document.getElementById('regenerate-video-cancel'),
            regenerateSubmit: document.getElementById('regenerate-video-submit'),
        };
        this.bind();
        this.load();
    }

    bind() {
        this.els.refreshBtn?.addEventListener('click', () => this.load({ manual: true }));
        this.els.regenerateCancel?.addEventListener('click', () => this.els.regenerateDialog?.close());
        this.els.regenerateForm?.addEventListener('submit', (event) => {
            event.preventDefault();
            this.submitRegenerateVideos();
        });
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
            const continueFullVideoButton = event.target.closest('[data-continue-full-video]');
            if (continueFullVideoButton) {
                event.preventDefault();
                this.continueFullVideo(continueFullVideoButton);
                return;
            }
            const regenerateVideosButton = event.target.closest('[data-regenerate-videos]');
            if (regenerateVideosButton) {
                event.preventDefault();
                this.regenerateVideos(regenerateVideosButton);
                return;
            }
            const cancelBiliUploadButton = event.target.closest('[data-bili-upload-cancel]');
            if (cancelBiliUploadButton) {
                event.preventDefault();
                this.cancelBilibiliUpload(cancelBiliUploadButton.dataset.biliUploadCancel || '', cancelBiliUploadButton);
                return;
            }
            const button = event.target.closest('[data-copy-text]');
            if (!button) return;
            this.copyText(button.dataset.copyText || '');
        });
        this.els.root?.addEventListener('change', (event) => {
            if (event.target?.id === 'bili-video-key') {
                this.applyBiliVideoDefaults(event.target.value);
            } else if (event.target?.id === 'bili-credential-id') {
                this.onBiliCredentialChange(event.target.value);
            } else if (event.target?.id === 'bili-charging-enabled') {
                this.toggleBiliChargingFields(event.target.checked);
            } else if (event.target?.id === 'bili-schedule-enabled') {
                this.refreshBiliScheduleInput();
            }
            if (event.target?.id === 'bili-schedule-time') this.refreshBiliScheduleInput();
        });
        this.els.root?.addEventListener('click', (event) => {
            const clearSeasonButton = event.target.closest('[data-bili-clear-season]');
            if (!clearSeasonButton) return;
            event.preventDefault();
            const select = document.getElementById('bili-season-id');
            if (select) select.value = '';
        });
        this.els.root?.addEventListener('submit', (event) => {
            const artifactForm = event.target.closest('[data-artifact-regenerate-form]');
            if (artifactForm) {
                event.preventDefault();
                this.regenerateArtifact(artifactForm);
                return;
            }
            const form = event.target.closest('#bili-upload-form');
            if (!form) return;
            event.preventDefault();
            this.submitBilibiliUpload(form);
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
        this.els.openApiJson.href = this.absoluteUrl(`/api/mp3-video-workflows/jobs/${encodeURIComponent(this.jobId)}`);
        if (!this.job) {
            this.els.root.innerHTML = '<div class="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">加载详情中...</div>';
        }
        try {
            const [result] = await Promise.all([
                this.request(`/api/mp3-video-workflows/jobs/${encodeURIComponent(this.jobId)}`, { timeoutMs: 30000 }),
                this.loadBiliCredentials(),
            ]);
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
        if (!this.hasActiveBiliUpload(this.job)) return;
        this.refreshTimer = setTimeout(() => {
            this.refreshTimer = null;
            this.refreshBilibiliUploads();
        }, this.autoRefreshMs);
    }

    hasActiveBiliUpload(job) {
        const items = Array.isArray(job?.bilibili_uploads) ? job.bilibili_uploads : [];
        const merged = [...items];
        const localUpload = this.biliPanel?.lastUpload || this.lastBiliUpload;
        if (localUpload && !merged.some((item) => item.task_id === localUpload.task_id)) {
            merged.push(localUpload);
        }
        return merged.some((item) => ['queued', 'pending', 'running', 'processing', 'submitting'].includes(String(item?.status || '').toLowerCase()));
    }

    async refreshBilibiliUploads() {
        if (!this.jobId) return;
        try {
            const result = await this.request(`/api/mp3-video-workflows/jobs/${encodeURIComponent(this.jobId)}/bilibili-uploads`, { timeoutMs: 15000 });
            this.job = {
                ...(this.job || {}),
                bilibili_uploads: result.data?.items || result.items || [],
            };
            this.biliPanel?.updateJobData?.(this.job);
            this.scheduleAutoRefresh();
        } catch (error) {
            this.setNotice(`刷新 B站上传记录失败：${error.message}`, 'warning');
            this.scheduleAutoRefresh();
        }
    }

    async loadBiliCredentials() {
        try {
            const result = await this.request('/api/v1/platform-credentials?platform=bilibili&purpose=bilibili.upload&status=active&per_page=100', { timeoutMs: 15000 });
            this.biliCredentials = result.data?.items || result.items || [];
            this.biliCredentialsError = '';
        } catch (error) {
            this.biliCredentials = [];
            this.biliCredentialsError = error.message || String(error);
        }
    }

    async loadBiliChargingOptions(credentialId) {
        const id = String(credentialId || '').trim();
        if (!id || this.biliChargingOptionsByCredential[id]) return;
        if (this.biliChargingOptionsPromises[id]) return this.biliChargingOptionsPromises[id];
        this.biliChargingOptionsLoading = id;
        this.applyBiliChargingOptions(id);
        this.biliChargingOptionsPromises[id] = (async () => {
            try {
                const result = await this.request(`/api/mp3-video-workflows/bilibili-charging-options?credential_id=${encodeURIComponent(id)}`, { timeoutMs: 20000 });
                this.biliChargingOptionsByCredential[id] = result.data || result;
                delete this.biliChargingOptionsErrorByCredential[id];
            } catch (error) {
                this.biliChargingOptionsErrorByCredential[id] = error.message || String(error);
            } finally {
                delete this.biliChargingOptionsPromises[id];
                if (this.biliChargingOptionsLoading === id) this.biliChargingOptionsLoading = '';
                this.applyBiliChargingOptions(id);
            }
        })();
        return this.biliChargingOptionsPromises[id];
    }

    async loadBiliSeasons(credentialId) {
        const id = String(credentialId || '').trim();
        if (!id) return;
        if (
            Object.prototype.hasOwnProperty.call(this.biliSeasonsByCredential, id) &&
            !this.biliSeasonsErrorByCredential[id]
        ) return;
        if (this.biliSeasonsPromises[id]) return this.biliSeasonsPromises[id];
        this.biliSeasonsLoading = id;
        this.applyBiliSeasonOptions(id);
        this.biliSeasonsPromises[id] = (async () => {
            try {
                const result = await this.request(`/api/mp3-video-workflows/bilibili-seasons?credential_id=${encodeURIComponent(id)}`, { timeoutMs: 20000 });
                this.biliSeasonsByCredential[id] = Array.isArray(result.data?.items)
                    ? result.data.items
                    : (Array.isArray(result.data) ? result.data : (result.items || []));
                delete this.biliSeasonsErrorByCredential[id];
            } catch (error) {
                delete this.biliSeasonsByCredential[id];
                this.biliSeasonsErrorByCredential[id] = error.message || String(error);
            } finally {
                delete this.biliSeasonsPromises[id];
                if (this.biliSeasonsLoading === id) this.biliSeasonsLoading = '';
                this.applyBiliSeasonOptions(id);
            }
        })();
        return this.biliSeasonsPromises[id];
    }

    renderMissingJob() {
        this.els.title.textContent = '缺少任务 ID';
        this.els.subtitle.textContent = '请从编排台点击“详情新页”打开。';
        this.els.root.innerHTML = '<div class="rounded-lg border border-amber-200 bg-amber-50 p-6 text-sm font-bold text-amber-800">URL 中没有 job_id。</div>';
    }

    render() {
        const job = this.job || {};
        const title = job.plan?.title || job.request?.audio?.topic_title || job.request?.audio?.filename || job.job_id || 'MP3 视频编排详情';
        const biliPanelDraft = this.captureBilibiliPanelDraft(job);
        const douyinPanelDraft = this.captureDouyinPanelDraft(job);
        const wechatChannelsPanelDraft = this.captureWechatChannelsPanelDraft(job);
        this.publishTabs?.destroy?.();
        this.els.title.textContent = title;
        this.els.subtitle.textContent = [
            job.request?.audio?.filename || '',
            job.created_at ? `创建 ${this.formatDateTime(job.created_at)}` : '',
            job.completed_at ? `完成 ${this.formatDateTime(job.completed_at)}` : '',
        ].filter(Boolean).join(' · ');
        this.els.root.innerHTML = [
            this.navSection('task-overview', this.renderSummary(job)),
            this.navSection('content-extract', this.renderDsExtract(job)),
            this.navSection('downloads', this.renderDownloads(job)),
            this.navSection('visual-assets', this.renderArtifacts(job)),
            this.navSection('video-files', this.renderVideos(job)),
            this.navSection('publish-center', this.renderPublishCenter()),
            this.navSection('run-logs', this.renderLogs(job)),
            this.renderRaw(job),
        ].join('');
        this.mountBilibiliPanel(job, biliPanelDraft);
        this.mountDouyinPanel(job, douyinPanelDraft);
        this.mountWechatChannelsPanel(job, wechatChannelsPanelDraft);
        this.mountPublishTabs();
        this.refreshBiliScheduleInput();
        const credentialId = document.getElementById('bili-credential-id')?.value || '';
        if (credentialId) {
            this.applyBiliSeasonOptions(credentialId);
            this.loadBiliSeasons(credentialId);
        }
        this.setupQuickNavigation();
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
            return `${this.renderBilibiliPublisher(this.job)}<div id="mp3-douyin-publish-panel"></div><div id="mp3-wechat-channels-publish-panel"></div>`;
        }
        return window.renderPlatformPublishTabs({
            prefix: 'mp3',
            activeKey: this.activePublishPlatform,
            panelIds: {
                bilibili: 'mp3-bilibili-publish-panel',
                douyin: 'mp3-douyin-publish-panel',
                wechat_channels: 'mp3-wechat-channels-publish-panel',
            },
        });
    }

    mountPublishTabs() {
        const container = this.els.root?.querySelector('[data-platform-publish-tabs="mp3"]');
        if (!container || !window.PlatformPublishTabs) return;
        this.publishTabs = new window.PlatformPublishTabs({
            container,
            activeKey: this.activePublishPlatform,
            onChange: (key) => { this.activePublishPlatform = key; },
        });
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
        const container = document.getElementById('mp3-bilibili-publish-panel');
        if (!container || !window.BilibiliPublishPanel) return;
        if (!this.biliPanel || this.biliPanel.container !== container) {
            this.biliPanel = new window.BilibiliPublishPanel({
                container,
                apiRoot: () => this.apiRoot(),
                request: (path, options) => this.request(path, options),
                onNotice: (message, type) => this.setNotice(message, type),
                uploadPath: (jobId) => `/api/mp3-video-workflows/jobs/${encodeURIComponent(jobId)}/bilibili-upload`,
                cancelPath: (jobId, uploadTaskId) => `/api/mp3-video-workflows/jobs/${encodeURIComponent(jobId)}/bilibili-upload/${encodeURIComponent(uploadTaskId)}/cancel`,
                uploads: (payload) => payload.bilibili_uploads || [],
                videoOptions: (payload) => this.biliVideoOptions(payload),
                coverOptions: (payload, videoKey) => this.biliCoverOptions(payload, videoKey),
                defaultCoverKey: (payload, videoKey) => this.defaultBiliCoverKey(payload, videoKey),
                defaultTitle: (payload, videoKey) => this.defaultBiliTitle(payload, videoKey),
                defaultTags: '财经,音频,内资闭门会',
                emptyDescription: '当前任务还没有完整视频文件。请先用“完整横竖屏视频”模式生成横屏或竖屏视频，关键帧预览不能直接投递 B站。',
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
        const container = document.getElementById('mp3-douyin-publish-panel');
        if (!container || !window.DouyinPublishPanel) return;
        if (!this.douyinPanel || this.douyinPanel.container !== container) {
            this.douyinPanel?.destroy?.();
            this.douyinPanel = new window.DouyinPublishPanel({
                container,
                apiRoot: () => this.apiRoot(),
                request: (path, options) => this.request(path, options),
                onNotice: (message, type) => this.setNotice(message, type),
                listPath: (jobId) => `/api/mp3-video-workflows/jobs/${encodeURIComponent(jobId)}/douyin-uploads`,
                uploadPath: (jobId) => `/api/mp3-video-workflows/jobs/${encodeURIComponent(jobId)}/douyin-upload`,
                cancelPath: (jobId, taskId) => `/api/mp3-video-workflows/jobs/${encodeURIComponent(jobId)}/douyin-upload/${encodeURIComponent(taskId)}/cancel`,
                videoOptions: (payload) => this.biliVideoOptions(payload),
                defaultTitle: (payload, videoKey) => this.defaultBiliTitle(payload, videoKey),
                defaultTags: '财经,音频,内资闭门会',
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
        const container = document.getElementById('mp3-wechat-channels-publish-panel');
        if (!container || !window.WechatChannelsPublishPanel) return;
        if (!this.wechatChannelsPanel || this.wechatChannelsPanel.container !== container) {
            this.wechatChannelsPanel?.destroy?.();
            this.wechatChannelsPanel = new window.WechatChannelsPublishPanel({
                container,
                apiRoot: () => this.apiRoot(),
                request: (path, options) => this.request(path, options),
                onNotice: (message, type) => this.setNotice(message, type),
                listPath: (jobId) => `/api/mp3-video-workflows/jobs/${encodeURIComponent(jobId)}/wechat-channels-uploads`,
                uploadPath: (jobId) => `/api/mp3-video-workflows/jobs/${encodeURIComponent(jobId)}/wechat-channels-upload`,
                cancelPath: (jobId, taskId) => `/api/mp3-video-workflows/jobs/${encodeURIComponent(jobId)}/wechat-channels-upload/${encodeURIComponent(taskId)}/cancel`,
                videoOptions: (payload) => this.biliVideoOptions(payload),
                defaultTitle: (payload, videoKey) => this.defaultBiliTitle(payload, videoKey),
                defaultTags: '财经,音频,内资闭门会',
                emptyDescription: '当前任务还没有完整视频文件。请先生成横屏或竖屏视频。',
            });
        }
        this.wechatChannelsPanel.setJob(job, { formDraft });
    }

    sourceLabel(job) {
        const source = job.input_source || job.request?.input_source || job.raw_result?.input_source || '';
        const labels = {
            ds_response: '手动 DS 返回值',
            ima_transcript: 'IMA 转录全文 + DS',
            gemini_audio: 'Gemini 上传 MP3',
        };
        return labels[source] || '未知来源';
    }

    renderSummary(job) {
        const status = this.statusMeta(job.status);
        const audio = job.request?.audio || job.audio || {};
        const planAudio = job.plan?.audio || {};
        const generateSubtitles = job.generate_subtitles ?? job.request?.generate_subtitles ?? true;
        const canContinueFullVideo = this.canContinueFullVideo(job);
        const canRegenerateVideos = this.canRegenerateVideos(job);
        const continueStartText = planAudio.start_seconds !== undefined && planAudio.start_seconds !== null
            ? this.formatDuration(planAudio.start_seconds)
            : '00:00';
        const continueEndText = planAudio.end_seconds !== undefined && planAudio.end_seconds !== null
            ? this.formatDuration(planAudio.end_seconds)
            : '';
        const range = [
            planAudio.start_seconds !== undefined ? `起点 ${this.formatDuration(planAudio.start_seconds)}` : '',
            planAudio.end_seconds !== undefined && planAudio.end_seconds !== null ? `终点 ${this.formatDuration(planAudio.end_seconds)}` : '终点自动',
            `倍速 ${planAudio.speed || 1.2}`,
        ].filter(Boolean).join(' · ');
        const regeneration = job.regeneration || {};
        const regenerationAudio = regeneration.audio_range || {};
        const regenerationRange = regeneration.active
            ? `${this.formatDuration(regenerationAudio.start_seconds ?? planAudio.start_seconds ?? 0)}–${this.formatDuration(regenerationAudio.end_seconds ?? planAudio.end_seconds ?? 0)}`
            : '';
        return `
            <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                    <div class="min-w-0">
                        <div class="flex flex-wrap items-center gap-2">
                            <span class="status-dot ${status.dot}"></span>
                            <span class="rounded-full px-2.5 py-1 text-xs font-bold ${status.className}">${this.escape(status.label)}</span>
                            <span class="text-xs font-bold text-slate-400">${this.escape(job.step || '')} · ${Number(job.progress || 0)}%</span>
                        </div>
                        <div class="mt-3 text-sm font-bold text-slate-900">${this.escape(audio.filename || audio.topic_title || '未命名音频')}</div>
                        <div class="mt-1 text-xs leading-5 text-slate-500">${this.escape([
                            this.sourceLabel(job),
                            job.render_mode === 'keyframes' ? '关键帧预览' : '完整视频',
                            generateSubtitles ? '生成字幕' : '无字幕',
                            range,
                        ].filter(Boolean).join(' · '))}</div>
                    </div>
                    <div class="flex flex-col items-start gap-2 lg:items-end">
                        ${canContinueFullVideo ? `
                            <div class="w-full rounded-lg border border-emerald-100 bg-emerald-50 p-2.5 lg:w-72">
                                <div class="mb-2 text-[11px] font-black text-emerald-800">完整视频截取范围</div>
                                <div class="grid grid-cols-2 gap-2">
                                    <label class="text-[11px] font-bold text-slate-500">
                                        起点
                                        <input type="text" data-continue-start-seconds value="${this.escapeAttr(continueStartText)}" placeholder="00:37" inputmode="numeric" class="mt-1 w-full rounded-md border border-emerald-200 bg-white px-2 py-1.5 text-xs font-black text-slate-900 focus:border-emerald-500 focus:outline-none">
                                    </label>
                                    <label class="text-[11px] font-bold text-slate-500">
                                        终点
                                        <input type="text" data-continue-end-seconds value="${this.escapeAttr(continueEndText)}" placeholder="14:39" inputmode="numeric" class="mt-1 w-full rounded-md border border-emerald-200 bg-white px-2 py-1.5 text-xs font-black text-slate-900 focus:border-emerald-500 focus:outline-none">
                                    </label>
                                </div>
                                <button type="button" data-continue-full-video="1" class="mt-2 w-full rounded-lg bg-emerald-600 px-3 py-2 text-xs font-black text-white hover:bg-emerald-700">
                                    <i class="fas fa-film mr-1.5"></i>继续生成完整视频
                                </button>
                            </div>
                        ` : ''}
                        ${canRegenerateVideos ? `
                            <button type="button" data-regenerate-videos="1" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-black text-amber-800 hover:bg-amber-100">
                                <i class="fas fa-arrows-rotate mr-1.5"></i>重新生成完整视频
                            </button>
                        ` : ''}
                        ${job.error ? `<div class="rounded-md border border-rose-100 bg-rose-50 px-3 py-2 text-xs font-bold text-rose-700">${this.escape(job.error)}</div>` : ''}
                    </div>
                </div>
                ${regeneration.active ? `
                    <div class="mt-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs">
                        <div class="font-bold text-amber-900">
                            <i class="fas fa-spinner fa-spin mr-1.5"></i>正在按新范围 ${this.escape(regenerationRange)}、${this.escape(planAudio.speed || 1.2)}× 重新生成
                        </div>
                        <div class="text-amber-700">新视频完成前，页面中的旧视频只代表上一版本。</div>
                    </div>
                ` : ''}
                <div class="mt-4 h-2 overflow-hidden rounded-full bg-slate-100">
                    <div class="h-full rounded-full ${status.bar}" style="width:${Math.max(0, Math.min(100, Number(job.progress || 0)))}%"></div>
                </div>
            </section>
        `;
    }

    canContinueFullVideo(job) {
        const status = String(job?.status || '').toLowerCase();
        const mode = String(job?.render_mode || job?.request?.render_mode || '').toLowerCase();
        const hasKeyframes = Object.keys(job?.keyframes || {}).length > 0
            || Object.keys(job?.artifacts || {}).some((key) => key.startsWith('keyframe_'));
        const hasLandscape = Boolean(job?.download_landscape_video_url);
        const hasVertical = Boolean(job?.download_vertical_video_url);
        return ['succeeded', 'completed'].includes(status)
            && mode === 'keyframes'
            && hasKeyframes
            && !(hasLandscape && hasVertical);
    }

    canRegenerateVideos(job) {
        const status = String(job?.status || '').toLowerCase();
        const mode = String(job?.render_mode || job?.request?.render_mode || '').toLowerCase();
        const hasLandscape = Boolean(job?.download_landscape_video_url || job?.landscape_video?.video_path);
        const hasVertical = Boolean(job?.download_vertical_video_url || job?.vertical_video?.video_path);
        return ['succeeded', 'completed'].includes(status)
            && mode === 'full_video'
            && (hasLandscape || hasVertical);
    }

    renderDsExtract(job) {
        const ds = this.normalizedDsExtract(job);
        if (!ds.has_content) {
            return `
                <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                    <div class="text-sm font-black text-slate-900">DS 清洗结果</div>
                    <div class="mt-2 rounded-md border border-dashed border-slate-200 p-4 text-sm text-slate-500">暂无可清洗的 DS 1、2、时间戳、6、7。</div>
                </section>
            `;
        }
        const clipText = [
            ds.clip?.start_text ? `开始 ${ds.clip.start_text}` : '',
            ds.clip?.end_text ? `结束 ${ds.clip.end_text}` : '',
        ].filter(Boolean).join(' · ') || '未识别到时间戳';
        const cards = [
            ['1', '高密度标题版本一', ds.title_1],
            ['2', '高密度标题版本二', ds.title_2],
            ['4', '精华片段时间戳', clipText],
            ['6', '小君君横屏标题（斜杠）', ds.title_6],
            ['7', '此刻此声竖屏标题（竖线）', ds.title_7],
        ];
        return `
            <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div>
                        <div class="text-sm font-black text-slate-900">DS 清洗结果</div>
                        <div class="mt-1 text-xs text-slate-500">展示 DS 返回值中的 1、2、时间戳、6、7，去掉 Markdown 和说明性前缀。</div>
                    </div>
                    ${ds.raw_text ? `<button type="button" data-copy-text="${this.escapeAttr(ds.raw_text)}" class="rounded-md border border-slate-300 px-2.5 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50">复制 DS 原文</button>` : ''}
                </div>
                <div class="grid grid-cols-1 gap-3 lg:grid-cols-2">
                    ${cards.map(([number, label, value]) => `
                        <div class="rounded-md border border-slate-100 bg-slate-50 p-3">
                            <div class="flex items-center justify-between gap-2">
                                <div class="text-[11px] font-black uppercase text-slate-400">${this.escape(number)} · ${this.escape(label)}</div>
                                ${value ? `<button type="button" data-copy-text="${this.escapeAttr(value)}" class="text-[11px] font-bold text-indigo-700 hover:text-indigo-900">复制</button>` : ''}
                            </div>
                            <div class="mt-2 whitespace-pre-wrap text-sm font-bold leading-6 text-slate-900">${this.escape(value || '未识别')}</div>
                        </div>
                    `).join('')}
                </div>
                ${ds.clip?.summary ? `<details class="mt-3 rounded-md border border-slate-100 bg-white p-3 text-xs leading-5 text-slate-600"><summary class="cursor-pointer font-black text-slate-500">查看时间戳原段</summary><div class="mt-2 whitespace-pre-wrap">${this.escape(ds.clip.summary)}</div></details>` : ''}
            </section>
        `;
    }

    renderDownloads(job) {
        const items = this.downloadItems(job);
        if (!items.length) {
            return `
                <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                    <div class="text-sm font-black text-slate-900">下载入口</div>
                    <div class="mt-2 rounded-md border border-dashed border-slate-200 p-4 text-sm text-slate-500">暂无可下载产物。</div>
                </section>
            `;
        }
        const groupOrder = [
            ['audio', '音频'],
            ['json', 'JSON'],
            ['image', '封皮 / 背景图'],
            ['keyframe', '关键帧'],
            ['video', '视频'],
        ];
        const grouped = groupOrder.map(([kind, label]) => [
            kind,
            label,
            items.filter((item) => item.kind === kind),
        ]).filter(([, , values]) => values.length);
        return `
            <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div>
                        <div class="text-sm font-black text-slate-900">下载入口</div>
                        <div class="mt-1 text-xs text-slate-500">原始音频、DS/Gemini 源结果、封皮背景、关键帧、横竖屏视频和结果 JSON。</div>
                    </div>
                    <div class="text-xs font-bold text-slate-500">${items.length} 个入口</div>
                </div>
                <div class="space-y-3">
                    ${grouped.map(([, label, values]) => `
                        <div>
                            <div class="mb-1 text-[11px] font-black uppercase text-slate-400">${this.escape(label)}</div>
                            <div class="flex flex-wrap gap-2">
                                ${values.map((item) => `
                                    <a href="${this.absoluteUrl(item.url)}" target="_blank" rel="noopener noreferrer" class="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50">
                                        <i class="fas fa-download mr-1"></i>${this.escape(item.label)}
                                    </a>
                                `).join('')}
                            </div>
                        </div>
                    `).join('')}
                </div>
            </section>
        `;
    }

    renderArtifacts(job) {
        const artifacts = job.artifacts || {};
        const urls = job.artifact_download_urls || {};
        const images = Object.entries(urls)
            .filter(([key]) => {
                const artifact = artifacts[key] || {};
                const label = String(artifact.label || key);
                return !key.startsWith('keyframe_') && !/json/i.test(label);
            })
            .map(([key, url]) => ({ key, url, label: artifacts[key]?.label || key }));
        if (!images.length) return '';
        return `
            <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="mb-3 text-sm font-black text-slate-900">封皮 / 背景图预览</div>
                <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    ${images.map((item) => `
                        <div class="overflow-hidden rounded-md border border-slate-200 bg-slate-50">
                            <a href="${this.absoluteUrl(item.url)}" target="_blank" rel="noopener noreferrer" class="block">
                                <img src="${this.absoluteUrl(item.url)}" alt="${this.escapeAttr(item.label)}" class="aspect-video w-full object-cover">
                            </a>
                            <div class="space-y-2 px-2 py-2">
                                <div class="text-xs font-bold text-slate-600">${this.escape(item.label)}</div>
                                ${this.renderArtifactRegenerateForm(job, item)}
                            </div>
                        </div>
                    `).join('')}
                </div>
            </section>
        `;
    }

    renderArtifactRegenerateForm(job, item) {
        if (!this.canRegenerateArtifact(item.key)) return '';
        const canSubmit = this.canEditArtifactNow(job);
        const defaultText = this.defaultArtifactTitleText(job, item.key);
        return `
            <details class="rounded-md border border-indigo-100 bg-white">
                <summary class="cursor-pointer select-none px-2.5 py-2 text-[11px] font-black text-indigo-700 hover:bg-indigo-50">
                    <i class="fas fa-wand-magic-sparkles mr-1"></i>重做封皮
                </summary>
                <form data-artifact-regenerate-form data-artifact-key="${this.escapeAttr(item.key)}" class="space-y-2 border-t border-indigo-50 p-2.5">
                    <label class="block text-[11px] font-bold text-slate-500">
                        标题文案（最多 3 行）
                        <textarea name="title_text" rows="3" maxlength="240" class="mt-1 w-full resize-y rounded-md border border-slate-300 bg-white px-2 py-1.5 text-xs font-bold leading-5 text-slate-900 focus:border-indigo-500 focus:outline-none" placeholder="${this.escapeAttr(this.artifactTitlePlaceholder(item.key))}">${this.escape(defaultText)}</textarea>
                    </label>
                    <div class="text-[10px] leading-4 text-slate-400">${this.escape(this.artifactRegenerateHint(item.key))}</div>
                    ${canSubmit ? '' : '<div class="rounded-md border border-amber-100 bg-amber-50 px-2 py-1.5 text-[10px] font-bold text-amber-700">当前任务运行中，结束后再替换封皮。</div>'}
                    <button type="submit" ${canSubmit ? '' : 'disabled'} class="w-full rounded-md bg-indigo-600 px-2.5 py-1.5 text-[11px] font-black text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300">
                        <i class="fas fa-rotate mr-1"></i>生成并替换
                    </button>
                </form>
            </details>
        `;
    }

    canRegenerateArtifact(key) {
        return ['finance_cover', 'audio_background', 'xiaojunjun_cover', 'xiaojunjun_background'].includes(String(key || ''));
    }

    canEditArtifactNow(job) {
        return ['succeeded', 'completed', 'failed', 'canceled'].includes(String(job?.status || '').toLowerCase());
    }

    defaultArtifactTitleText(job, key) {
        const plan = job?.plan || {};
        if (key === 'finance_cover') {
            return plan.finance_cover_subtitle || plan.title || '';
        }
        if (key === 'audio_background') {
            return [plan.cover_title, plan.cover_subtitle].filter(Boolean).join('\n') || plan.title || '';
        }
        if (key === 'xiaojunjun_cover' || key === 'xiaojunjun_background') {
            return plan.xiaojunjun_subtitle || plan.xiaojunjun_video_title || plan.title || '';
        }
        return '';
    }

    artifactTitlePlaceholder(key) {
        if (key === 'audio_background') return '第一行主标题\n第二行主标题\n第三行副标题';
        return '第一行标题\n第二行标题\n第三行标题';
    }

    artifactRegenerateHint(key) {
        if (key === 'finance_cover') return '替换黄色大标题；栏目标题和日期沿用当前任务。';
        if (key === 'audio_background') return '前两行作为竖屏主标题，第三行作为副标题。';
        if (key === 'xiaojunjun_cover') return '替换小君君横屏封皮的黄色高亮条，日期保留。';
        if (key === 'xiaojunjun_background') return '替换小君君横屏背景的黄色高亮条，不带日期。';
        return '只重做当前这张图。';
    }

    renderVideos(job) {
        const links = [
            ['小君君横屏视频', job.download_landscape_video_url],
            ['此刻此声竖屏视频', job.download_vertical_video_url],
        ].filter(([, url]) => url);
        if (!links.length) return '';
        return `
            <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="mb-3 text-sm font-black text-slate-900">视频文件</div>
                <div class="grid grid-cols-1 gap-3 lg:grid-cols-2">
                    ${links.map(([label, url]) => `
                        <div class="rounded-md border border-slate-200 bg-slate-50 p-3">
                            <div class="mb-2 text-xs font-black text-slate-500">${this.escape(label)}</div>
                            <video class="w-full rounded-md bg-black" controls preload="metadata" src="${this.absoluteUrl(url)}"></video>
                        </div>
                    `).join('')}
                </div>
            </section>
        `;
    }

    renderBilibiliPublisher(job) {
        return '<div id="mp3-bilibili-publish-panel"></div>';
        const videos = this.biliVideoOptions(job);
        const hasVideos = videos.length > 0;
        const selectedVideo = videos[0] || { key: 'landscape', label: '暂无可投递视频', defaultTitle: job.plan?.title || job.request?.audio?.topic_title || '内资闭门会' };
        const coverOptions = this.biliCoverOptions(job, selectedVideo.key);
        const selectedCover = this.defaultBiliCoverKey(job, selectedVideo.key);
        const defaultTitle = this.defaultBiliTitle(job, selectedVideo.key);
        const uploadItems = Array.isArray(job.bilibili_uploads) ? job.bilibili_uploads : [];
        const disabled = !hasVideos || !this.biliCredentials.length;
        const selectedCredentialId = this.biliCredentials[0]?.id || '';
        const unavailableMessages = [
            hasVideos ? '' : '当前任务还没有完整视频文件。请先用“完整横竖屏视频”模式生成横屏或竖屏视频，关键帧预览不能直接投递 B站。',
            !this.biliCredentials.length ? '暂无可用 B站上传账号。请先在账号管理里导入或新增用途为「B站视频上传」的账号。' : '',
        ].filter(Boolean);
        return `
            <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="mb-3 flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
                    <div>
                        <div class="text-sm font-black text-slate-900">B站发布</div>
                        <div class="mt-1 text-xs text-slate-500">选择已生成的视频、封面和账号，直接投递 B站上传，不重新合成视频。</div>
                    </div>
                    <a href="platform-credentials.html" target="_blank" rel="noopener noreferrer" class="self-start rounded-md border border-slate-300 px-2.5 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50">
                        <i class="fas fa-user-gear mr-1"></i>账号管理
                    </a>
                </div>
                ${this.biliCredentialsError ? `<div class="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-800">B站账号读取失败：${this.escape(this.biliCredentialsError)}</div>` : ''}
                ${unavailableMessages.map((message) => `<div class="mb-3 rounded-md border border-dashed border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-500">${this.escape(message)}</div>`).join('')}
                <form id="bili-upload-form" class="space-y-3">
                    <div class="grid grid-cols-1 gap-3 lg:grid-cols-3">
                        <label class="block">
                            <span class="text-[11px] font-black uppercase text-slate-400">账号</span>
                            <select id="bili-credential-id" name="credential_id" class="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-bold text-slate-800" ${disabled ? 'disabled' : ''}>
                                ${this.biliCredentials.length ? this.biliCredentials.map((item) => `
                                    <option value="${this.escapeAttr(item.id)}">${this.escape(item.owner_label || item.name || item.masked_summary || item.id)}</option>
                                `).join('') : '<option value="">暂无可用账号</option>'}
                            </select>
                        </label>
                        <label class="block">
                            <span class="text-[11px] font-black uppercase text-slate-400">视频</span>
                            <select id="bili-video-key" name="video_key" class="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-bold text-slate-800" ${disabled ? 'disabled' : ''}>
                                ${videos.length ? videos.map((item) => `
                                    <option value="${this.escapeAttr(item.key)}" data-default-title="${this.escapeAttr(item.defaultTitle)}" data-default-cover="${this.escapeAttr(this.defaultBiliCoverKey(job, item.key))}">${this.escape(item.label)}</option>
                                `).join('') : '<option value="">暂无可投递视频</option>'}
                            </select>
                        </label>
                        <label class="block">
                            <span class="text-[11px] font-black uppercase text-slate-400">封面</span>
                            <select id="bili-cover-key" name="cover_key" class="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-bold text-slate-800" ${disabled ? 'disabled' : ''}>
                                <option value="">自动抽帧</option>
                                ${coverOptions.map((item) => `
                                    <option value="${this.escapeAttr(item.key)}" ${item.key === selectedCover ? 'selected' : ''}>${this.escape(item.label)}</option>
                                `).join('')}
                            </select>
                        </label>
                    </div>
                    <label class="block">
                        <span class="text-[11px] font-black uppercase text-slate-400">标题</span>
                        <input id="bili-title" name="title" value="${this.escapeAttr(defaultTitle)}" maxlength="120" class="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-bold text-slate-900" ${disabled ? 'disabled' : ''}>
                    </label>
                    <div class="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_220px_140px]">
                        <label class="block">
                            <span class="text-[11px] font-black uppercase text-slate-400">简介</span>
                            <textarea name="desc" rows="3" class="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm leading-6 text-slate-800" ${disabled ? 'disabled' : ''}></textarea>
                        </label>
                        <label class="block">
                            <span class="text-[11px] font-black uppercase text-slate-400">标签</span>
                            <input name="tags" value="财经,音频,内资闭门会" class="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-bold text-slate-800" ${disabled ? 'disabled' : ''}>
                        </label>
                        <label class="block">
                            <span class="text-[11px] font-black uppercase text-slate-400">分区</span>
                            <input name="tid" type="number" value="207" min="1" class="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-bold text-slate-800" ${disabled ? 'disabled' : ''}>
                        </label>
                    </div>
                    <div class="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_1.2fr]">
                        <div class="rounded-md border border-slate-200 bg-white p-3">
                            <div class="grid grid-cols-[86px_1fr_auto] items-center gap-3">
                                <div class="text-xs text-slate-600">加入合集</div>
                                <select id="bili-season-id" name="season_id" class="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-bold text-slate-800 disabled:bg-slate-100 disabled:text-slate-400" ${disabled ? 'disabled' : ''}>
                                    ${this.renderBiliSeasonOptions(selectedCredentialId)}
                                </select>
                                <button type="button" data-bili-clear-season="1" class="rounded-md px-2 py-1 text-xs font-bold text-slate-500 hover:bg-slate-100" ${disabled ? 'disabled' : ''}>清除</button>
                            </div>
                            <div id="bili-season-options-note" class="ml-[98px] mt-1 text-xs text-slate-400">${this.escape(this.biliSeasonOptionNote(selectedCredentialId))}</div>
                        </div>
                        <div class="rounded-md border border-slate-200 bg-white p-3">
                            <div class="grid grid-cols-[86px_1fr] items-center gap-3">
                                <div class="text-xs text-slate-600">定时发布</div>
                                <div class="flex flex-wrap items-center gap-3">
                                    <label class="inline-flex cursor-pointer items-center gap-3 font-bold text-sm text-slate-700">
                                        <input id="bili-schedule-enabled" name="schedule_enabled" type="checkbox" value="1" class="peer sr-only" ${disabled ? 'disabled' : ''}>
                                        <span class="relative h-5 w-10 rounded-full bg-slate-300 transition peer-checked:bg-sky-500 peer-disabled:opacity-50 after:absolute after:left-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:shadow-sm after:transition peer-checked:after:translate-x-5"></span>
                                        <span>启用定时</span>
                                    </label>
                                    <input id="bili-schedule-time" name="schedule_time" type="datetime-local" class="min-w-[210px] rounded-md border border-slate-300 px-3 py-2 text-sm font-bold text-slate-800 disabled:bg-slate-100 disabled:text-slate-400" disabled>
                                </div>
                            </div>
                            <div class="ml-[98px] mt-1 text-xs text-slate-400">可选择距当前最早 5 分钟、最晚 15 天的时间。</div>
                        </div>
                    </div>
                    <div class="rounded-md border border-slate-200 bg-white p-3">
                        <div class="grid gap-3 text-sm text-slate-700">
                            <div class="grid grid-cols-[86px_1fr] items-center gap-3">
                                <div class="text-xs text-slate-600">充电设置</div>
                                <label class="inline-flex cursor-pointer items-center gap-3 font-bold">
                                    <input id="bili-charging-enabled" name="charging_enabled" type="checkbox" value="1" class="peer sr-only" ${disabled ? 'disabled' : ''}>
                                    <span class="relative h-5 w-10 rounded-full bg-slate-300 transition peer-checked:bg-sky-500 peer-disabled:opacity-50 after:absolute after:left-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:shadow-sm after:transition peer-checked:after:translate-x-5"></span>
                                    <span>设为充电专属视频</span>
                                </label>
                            </div>
                            <div class="grid grid-cols-[86px_1fr] items-center gap-3">
                                <div class="text-xs text-slate-600">试看：</div>
                                <div class="flex flex-wrap items-center gap-3">
                                    <label class="inline-flex items-center gap-2">
                                        <input name="charging_preview_enabled" data-bili-charging-field type="checkbox" value="1" class="rounded border-slate-300" checked disabled>
                                        设置试看
                                    </label>
                                    <span class="text-slate-500">试看至</span>
                                    <select name="charging_preview_end_time" data-bili-charging-field class="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-bold text-slate-800 disabled:bg-slate-100 disabled:text-slate-400" disabled>
                                        <option value="00:01:00">00:01:00</option>
                                        <option value="00:03:00" selected>00:03:00</option>
                                        <option value="00:05:00">00:05:00</option>
                                        <option value="00:10:00">00:10:00</option>
                                    </select>
                                    <span class="text-slate-500">结束</span>
                                </div>
                            </div>
                            <div class="grid grid-cols-[86px_1fr] items-center gap-3">
                                <div class="text-xs text-slate-600">付费模式：</div>
                                <div class="flex flex-wrap items-center gap-3">
                                    <span class="font-bold text-sky-600">✓</span>
                                    <span class="font-bold">包月付费</span>
                                    <span class="text-slate-500">仅有加入</span>
                                    <select id="bili-upower-level-id" name="upower_level_id" data-bili-charging-field class="min-w-[180px] rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-bold text-slate-800 disabled:bg-slate-100 disabled:text-slate-400" disabled>
                                        ${this.renderBiliChargingLevelOptions(selectedCredentialId)}
                                    </select>
                                    <span class="text-slate-500">及以上成员可观看</span>
                                </div>
                            </div>
                            <div class="grid grid-cols-[86px_1fr] items-center gap-3">
                                <div class="text-xs text-slate-600">观看模式：</div>
                                <div class="flex flex-wrap items-center gap-5 text-slate-400">
                                    <label class="inline-flex items-center gap-2"><input type="checkbox" disabled> 抢先看</label>
                                    <label class="inline-flex items-center gap-2"><input type="checkbox" disabled> 限时免费</label>
                                </div>
                            </div>
                            <div id="bili-charging-options-note" class="ml-[98px] text-xs text-slate-400">${this.escape(this.biliChargingOptionNote(selectedCredentialId))}</div>
                        </div>
                    </div>
                    <div class="flex flex-wrap items-center justify-between gap-3">
                        <label class="inline-flex items-center gap-2 text-xs font-bold text-slate-600">
                            <input name="is_only_self" type="checkbox" value="1" class="rounded border-slate-300" ${disabled ? 'disabled' : ''}>
                            仅自己可见
                        </label>
                        <button id="bili-submit-btn" type="submit" class="rounded-lg bg-slate-950 px-4 py-2 text-sm font-black text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300" ${disabled ? 'disabled' : ''}>
                            <i class="fab fa-bilibili mr-1.5"></i>${hasVideos ? '投递 B站上传' : '等待视频生成'}
                        </button>
                    </div>
                </form>
                ${this.renderBiliUploadHistory(uploadItems)}
            </section>
        `;
    }

    renderBiliUploadHistory(items) {
        const merged = [...items];
        if (this.lastBiliUpload && !merged.some((item) => item.task_id === this.lastBiliUpload.task_id)) {
            merged.unshift(this.lastBiliUpload);
        }
        if (!merged.length) return '';
        return `
            <div class="mt-4 border-t border-slate-100 pt-3">
                <div class="mb-2 text-[11px] font-black uppercase text-slate-400">上传记录</div>
                <div class="space-y-2">
                    ${merged.slice(0, 8).map((item) => {
                        const status = this.statusMeta(item.status);
                        const canCancel = this.canCancelBiliUpload(item);
                        return `
                            <div class="rounded-md border border-slate-100 bg-slate-50 px-3 py-2 text-xs">
                                <div class="flex flex-wrap items-center justify-between gap-2">
                                    <div class="min-w-0 font-bold text-slate-800">${this.escape(item.title || item.task_id || '')}</div>
                                    <div class="flex items-center gap-2">
                                        ${canCancel ? `
                                            <button type="button" data-bili-upload-cancel="${this.escapeAttr(item.task_id || '')}" class="rounded-md border border-rose-200 bg-white px-2 py-1 font-bold text-rose-600 hover:bg-rose-50">
                                                取消
                                            </button>
                                        ` : ''}
                                        <span class="rounded-full px-2 py-1 font-bold ${status.className}">${this.escape(status.label)}</span>
                                    </div>
                                </div>
                                <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-slate-500">
                                    <span>${this.escape(item.video_key === 'vertical' ? '此刻此声竖屏' : '小君君横屏')}</span>
                                    ${item.credential_name ? `<span>账号 ${this.escape(item.credential_name)}</span>` : ''}
                                    ${item.delay_time ? `<span>定时 ${this.escape(this.formatBiliDelayTime(item.delay_time))}</span>` : ''}
                                    ${item.season_id ? `<span>合集 ${this.escape(item.season_id)}</span>` : ''}
                                    ${item.charging_pay?.enabled ? `<span>充电 ${this.escape(item.charging_pay.upower_level_id || '默认档')}</span>` : ''}
                                    <a href="${this.absoluteUrl(`/api/v1/publisher/task/${encodeURIComponent(item.task_id)}`)}" target="_blank" rel="noopener noreferrer" class="font-bold text-indigo-700 hover:text-indigo-900">任务 JSON</a>
                                </div>
                            </div>
                        `;
                    }).join('')}
                </div>
            </div>
        `;
    }

    canCancelBiliUpload(item) {
        return ['queued', 'pending'].includes(String(item?.status || '').toLowerCase());
    }

    renderLogs(job) {
        const logs = Array.isArray(job.logs) ? job.logs.slice(-20).reverse() : [];
        if (!logs.length) return '';
        return `
            <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="mb-3 text-sm font-black text-slate-900">最近日志</div>
                <div class="space-y-1">
                    ${logs.map((log) => `
                        <div class="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600">
                            <span class="font-mono text-slate-400">${this.escape(log.progress ?? '')}%</span>
                            <span class="ml-2 font-bold">${this.escape(log.step || '')}</span>
                            <span class="ml-2">${this.escape(log.message || '')}</span>
                        </div>
                    `).join('')}
                </div>
            </section>
        `;
    }

    renderRaw(job) {
        const raw = job.raw_result || null;
        if (!raw) return '';
        const text = JSON.stringify(raw, null, 2);
        return `
            <section class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <details>
                    <summary class="cursor-pointer text-sm font-black text-slate-900">原始 result JSON</summary>
                    <pre class="mt-3 max-h-[34rem] overflow-auto rounded-md bg-slate-950 p-3 text-xs leading-5 text-slate-100">${this.escape(text)}</pre>
                </details>
            </section>
        `;
    }

    downloadItems(job) {
        if (Array.isArray(job.download_items) && job.download_items.length) {
            return job.download_items.filter((item) => item?.url);
        }
        const items = [];
        const add = (key, label, url, kind) => {
            if (url) items.push({ key, label, url, kind });
        };
        add('input_audio', '原始 MP3', job.download_input_audio_url, 'audio');
        add('source_result', 'DS/Gemini 源结果 JSON', job.download_source_result_url, 'json');
        add('infographic', job.infographic?.label || '信息图', job.download_infographic_url, 'image');
        Object.entries(job.artifact_download_urls || {}).forEach(([key, url]) => {
            add(key, job.artifacts?.[key]?.label || key, url, key.startsWith('keyframe_') ? 'keyframe' : 'image');
        });
        add('landscape_video', '小君君横屏视频', job.download_landscape_video_url, 'video');
        add('vertical_video', '此刻此声竖屏视频', job.download_vertical_video_url, 'video');
        add('result_json', '编排结果 JSON', job.download_result_url, 'json');
        return items;
    }

    biliVideoOptions(job) {
        const ds = this.normalizedDsExtract(job);
        return [
            {
                key: 'landscape',
                label: '小君君横屏视频',
                url: job.download_landscape_video_url,
                defaultTitle: this.firstCleanBiliTitle(ds.title_6, job.plan?.xiaojunjun_video_title, job.plan?.title),
            },
            {
                key: 'vertical',
                label: '此刻此声竖屏视频',
                url: job.download_vertical_video_url,
                defaultTitle: this.firstCleanBiliTitle(ds.title_7, job.plan?.ciketc_video_title, job.plan?.title),
            },
        ].filter((item) => item.url);
    }

    biliCoverOptions(job, videoKey = '') {
        const artifacts = job.artifacts || {};
        const urls = job.artifact_download_urls || {};
        const preferred = videoKey === 'vertical'
            ? ['finance_cover', 'podcast_cover', 'vertical_cover', 'audio_background', 'xiaojunjun_cover', 'xiaojunjun_background']
            : ['xiaojunjun_cover', 'finance_cover', 'xiaojunjun_background', 'audio_background', 'podcast_cover', 'vertical_cover'];
        const entries = Object.entries(urls)
            .filter(([key]) => !key.startsWith('keyframe_'))
            .map(([key, url]) => ({
                key,
                url,
                label: artifacts[key]?.label || key,
                rank: preferred.includes(key) ? preferred.indexOf(key) : 99,
            }))
            .sort((a, b) => a.rank - b.rank || a.label.localeCompare(b.label, 'zh-Hans-CN'));
        return entries;
    }

    defaultBiliCoverKey(job, videoKey) {
        const options = this.biliCoverOptions(job, videoKey);
        return options[0]?.key || '';
    }

    defaultBiliTitle(job, videoKey) {
        const option = this.biliVideoOptions(job).find((item) => item.key === videoKey);
        return this.firstCleanBiliTitle(option?.defaultTitle, job.plan?.title, job.request?.audio?.topic_title, '内资闭门会').slice(0, 120);
    }

    defaultBiliDesc() {
        return '';
    }

    applyBiliVideoDefaults(videoKey) {
        if (!this.job) return;
        const titleInput = document.getElementById('bili-title');
        const coverSelect = document.getElementById('bili-cover-key');
        const form = document.getElementById('bili-upload-form');
        if (!titleInput || !coverSelect || !form) return;
        const title = this.defaultBiliTitle(this.job, videoKey);
        titleInput.value = title;
        const covers = this.biliCoverOptions(this.job, videoKey);
        const defaultCover = this.defaultBiliCoverKey(this.job, videoKey);
        coverSelect.innerHTML = [
            '<option value="">自动抽帧</option>',
            ...covers.map((item) => `<option value="${this.escapeAttr(item.key)}" ${item.key === defaultCover ? 'selected' : ''}>${this.escape(item.label)}</option>`),
        ].join('');
        const desc = form.querySelector('textarea[name="desc"]');
        if (desc) desc.value = this.defaultBiliDesc();
    }

    toggleBiliChargingFields(enabled) {
        const form = document.getElementById('bili-upload-form');
        if (!form) return;
        form.querySelectorAll('[data-bili-charging-field]').forEach((field) => {
            field.disabled = !enabled;
        });
        if (enabled) {
            const credentialId = form.querySelector('#bili-credential-id')?.value || '';
            this.loadBiliChargingOptions(credentialId);
        }
    }

    onBiliCredentialChange(credentialId) {
        const form = document.getElementById('bili-upload-form');
        const enabled = Boolean(form?.querySelector('#bili-charging-enabled')?.checked);
        this.applyBiliSeasonOptions(credentialId);
        this.loadBiliSeasons(credentialId);
        if (enabled) {
            this.loadBiliChargingOptions(credentialId);
        } else {
            this.applyBiliChargingOptions(credentialId);
        }
    }

    renderBiliSeasonOptions(credentialId) {
        const id = String(credentialId || '').trim();
        const seasons = Array.isArray(this.biliSeasonsByCredential[id]) ? this.biliSeasonsByCredential[id] : [];
        const loading = this.biliSeasonsLoading === id;
        const error = this.biliSeasonsErrorByCredential[id];
        const placeholder = loading
            ? '<option value="">读取合集中...</option>'
            : (error ? '<option value="">合集读取失败</option>' : '<option value="">不加入合集</option>');
        return [
            placeholder,
            ...seasons.map((item) => `<option value="${this.escapeAttr(item.id || '')}">${this.escape(item.title || item.name || item.id || '未命名合集')}</option>`),
        ].join('');
    }

    biliSeasonOptionNote(credentialId) {
        const id = String(credentialId || '').trim();
        if (!id) return '';
        if (this.biliSeasonsLoading === id) return '正在读取该账号的合集...';
        const error = this.biliSeasonsErrorByCredential[id];
        if (error) return `合集读取失败：${error}`;
        const seasons = this.biliSeasonsByCredential[id] || [];
        return seasons.length ? `已读取 ${seasons.length} 个合集` : '该账号暂无可选合集，或 B站未返回合集列表';
    }

    applyBiliSeasonOptions(credentialId) {
        const form = document.getElementById('bili-upload-form');
        if (!form) return;
        const select = form.querySelector('#bili-season-id');
        const previous = select?.value || '';
        if (select) {
            select.innerHTML = this.renderBiliSeasonOptions(credentialId);
            if (previous && Array.from(select.options).some((option) => option.value === previous)) {
                select.value = previous;
            }
        }
        const note = form.querySelector('#bili-season-options-note');
        if (note) note.textContent = this.biliSeasonOptionNote(credentialId);
    }

    formatDateTimeLocal(date) {
        const pad = (value) => String(value).padStart(2, '0');
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }

    refreshBiliScheduleInput() {
        const checkbox = document.getElementById('bili-schedule-enabled');
        const input = document.getElementById('bili-schedule-time');
        if (!checkbox || !input) return;
        const minDate = new Date(Date.now() + 6 * 60 * 1000);
        const maxDate = new Date(Date.now() + 15 * 24 * 60 * 60 * 1000);
        minDate.setSeconds(0, 0);
        maxDate.setSeconds(0, 0);
        input.min = this.formatDateTimeLocal(minDate);
        input.max = this.formatDateTimeLocal(maxDate);
        input.disabled = !checkbox.checked;
        if (checkbox.checked && !input.value) {
            input.value = input.min;
        }
        if (!checkbox.checked) {
            input.value = '';
        }
    }

    getBiliScheduleTimestamp(formData) {
        const enabled = document.getElementById('bili-schedule-enabled')?.checked ?? false;
        if (!enabled) return null;
        const value = formData.get('schedule_time');
        if (!value) throw new Error('请选择 B站定时发布时间');
        const timestamp = Math.floor(new Date(value).getTime() / 1000);
        const minTimestamp = Math.floor((Date.now() + 5 * 60 * 1000) / 1000);
        const maxTimestamp = Math.floor((Date.now() + 15 * 24 * 60 * 60 * 1000) / 1000);
        if (!Number.isFinite(timestamp) || timestamp < minTimestamp || timestamp > maxTimestamp) {
            throw new Error('B站定时发布时间需在 5 分钟后、15 天内');
        }
        return timestamp;
    }

    formatBiliDelayTime(value) {
        const seconds = Number(value);
        if (!Number.isFinite(seconds)) return String(value || '');
        return this.formatDateTime(new Date(seconds * 1000).toISOString());
    }

    renderBiliChargingLevelOptions(credentialId) {
        const id = String(credentialId || '').trim();
        const options = this.biliChargingOptionsByCredential[id] || {};
        const levels = Array.isArray(options.levels) ? options.levels : [];
        const defaultId = options.default_level_id || levels[0]?.id || '';
        const fallback = `<option value="">自动默认档</option>`;
        if (!levels.length) return fallback;
        return levels.map((level) => `
            <option value="${this.escapeAttr(level.id || '')}" data-price="${this.escapeAttr(level.upower_unit_price || 0)}" ${(level.id || '') === defaultId ? 'selected' : ''}>${this.escape(level.label || level.id || '充电档位')}</option>
        `).join('');
    }

    biliChargingOptionNote(credentialId) {
        const id = String(credentialId || '').trim();
        if (!id) return '';
        if (this.biliChargingOptionsLoading === id) return '正在读取该账号的充电档位...';
        const error = this.biliChargingOptionsErrorByCredential[id];
        if (error) return `档位读取失败，将使用账号默认档：${error}`;
        const levels = this.biliChargingOptionsByCredential[id]?.levels || [];
        return levels.length ? `已读取 ${levels.length} 个充电档位` : '勾选充电后读取该账号档位';
    }

    applyBiliChargingOptions(credentialId) {
        const form = document.getElementById('bili-upload-form');
        if (!form) return;
        const select = form.querySelector('#bili-upower-level-id');
        if (select) select.innerHTML = this.renderBiliChargingLevelOptions(credentialId);
        const note = form.querySelector('#bili-charging-options-note');
        if (note) note.textContent = this.biliChargingOptionNote(credentialId);
    }

    async submitBilibiliUpload(form) {
        const button = form.querySelector('#bili-submit-btn');
        const originalHtml = button?.innerHTML || '';
        const data = new FormData(form);
        const chargingEnabled = Boolean(data.get('charging_enabled'));
        let delayTime = null;
        try {
            delayTime = this.getBiliScheduleTimestamp(data);
        } catch (error) {
            this.setNotice(error.message || 'B站定时发布时间无效', 'warning');
            return;
        }
        const payload = {
            credential_id: data.get('credential_id') || '',
            video_key: data.get('video_key') || 'landscape',
            cover_key: data.get('cover_key') || '',
            title: data.get('title') || '',
            desc: data.get('desc') || '',
            tags: data.get('tags') || '',
            tid: data.get('tid') || 207,
            is_only_self: data.get('is_only_self') ? 1 : 0,
            season_id: data.get('season_id') || '',
        };
        if (delayTime) {
            payload.schedule_enabled = 1;
            payload.delay_time = delayTime;
        }
        if (chargingEnabled) {
            if (!payload.credential_id) {
                this.setNotice('启用充电专属前，请先选择一个 B站账号。', 'warning');
                return;
            }
            await this.loadBiliChargingOptions(payload.credential_id);
            if (this.biliChargingOptionsErrorByCredential[payload.credential_id]) {
                this.setNotice(`充电档位读取失败：${this.biliChargingOptionsErrorByCredential[payload.credential_id]}`, 'error');
                return;
            }
            const levels = this.biliChargingOptionsByCredential[payload.credential_id]?.levels || [];
            const levelSelect = form.querySelector('#bili-upower-level-id');
            const selectedLevel = levelSelect?.selectedOptions?.[0];
            const selectedLevelId = levelSelect?.value || '';
            const previewEndTime = form.querySelector('[name="charging_preview_end_time"]')?.value || '00:03:00';
            const previewEnabled = Boolean(form.querySelector('[name="charging_preview_enabled"]')?.checked);
            if (!levels.length || !selectedLevelId) {
                this.setNotice('启用充电专属前，需要先读取并选择一个可用的充电档位。', 'warning');
                return;
            }
            payload.charging_pay = {
                enabled: true,
                preview: {
                    need_preview: previewEnabled ? 1 : 0,
                    start_time: 0,
                    end_time: previewEndTime,
                },
                preview_end_time: previewEndTime,
                upower_level_id: selectedLevelId,
                upower_unit_price: selectedLevel?.dataset?.price || 0,
                upower_mode: 0,
            };
        }
        try {
            if (button) {
                button.disabled = true;
                button.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>投递中...';
            }
            const result = await this.request(`/api/mp3-video-workflows/jobs/${encodeURIComponent(this.jobId)}/bilibili-upload`, {
                method: 'POST',
                timeoutMs: 30000,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            this.lastBiliUpload = result.data?.upload_task || result.upload_task || null;
            await this.load();
            this.setNotice(`已投递 B站上传任务：${this.lastBiliUpload?.task_id || ''}`, 'success');
        } catch (error) {
            this.setNotice(`B站上传投递失败：${error.message}`, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                button.innerHTML = originalHtml;
            }
        }
    }

    async continueFullVideo(button) {
        let payload;
        try {
            payload = this.continueAudioRangePayload();
        } catch (error) {
            this.setNotice(error.message, 'error');
            return;
        }
        const rangeLabel = `${this.formatDuration(payload.start_seconds)} - ${payload.end_seconds === null ? '自动' : this.formatDuration(payload.end_seconds)}`;
        if (!window.confirm(`确认关键帧效果可以，继续生成完整横屏和竖屏视频？\n本次音频截取：${rangeLabel}\n已有 DS、封皮、背景和关键帧会复用。`)) return;
        const originalHtml = button?.innerHTML || '';
        try {
            if (button) {
                button.disabled = true;
                button.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>投递中...';
            }
            const result = await this.request(`/api/mp3-video-workflows/jobs/${encodeURIComponent(this.jobId)}/continue-full-video`, {
                method: 'POST',
                timeoutMs: 30000,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            this.job = result.data || result;
            this.render();
            this.scheduleAutoRefresh();
            this.setNotice(result.message || '已开始继续生成完整视频。', 'success');
        } catch (error) {
            this.setNotice(`继续生成完整视频失败：${error.message}`, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                button.innerHTML = originalHtml;
            }
        }
    }

    continueAudioRangePayload() {
        const planAudio = this.job?.plan?.audio || {};
        const startInput = document.querySelector('[data-continue-start-seconds]');
        const endInput = document.querySelector('[data-continue-end-seconds]');
        const fallbackStart = Number(planAudio.start_seconds ?? 0);
        const fallbackEnd = planAudio.end_seconds === undefined || planAudio.end_seconds === null
            ? null
            : Number(planAudio.end_seconds);
        const parsedStart = this.parseDurationInput(startInput?.value, '起点');
        const parsedEnd = this.parseDurationInput(endInput?.value, '终点');
        const start = parsedStart ?? (Number.isFinite(fallbackStart) ? fallbackStart : 0);
        const end = parsedEnd ?? (Number.isFinite(fallbackEnd) ? fallbackEnd : null);
        if (!Number.isFinite(start) || start < 0) throw new Error('起点时间无效');
        if (end !== null && (!Number.isFinite(end) || end <= start)) {
            throw new Error('终点时间必须大于起点时间');
        }
        return {
            start_seconds: Math.round(start * 1000) / 1000,
            end_seconds: end === null ? null : Math.round(end * 1000) / 1000,
        };
    }

    parseDurationInput(value, label = '时间') {
        const text = String(value ?? '').trim();
        if (!text) return null;
        if (text.includes(':')) {
            const parts = text.split(':').map((part) => part.trim());
            if (![2, 3].includes(parts.length) || parts.some((part) => !part)) {
                throw new Error(`${label}格式无效，请使用 MM:SS 或 HH:MM:SS`);
            }
            const numbers = parts.map((part) => Number(part));
            if (numbers.some((item) => !Number.isFinite(item) || item < 0)) {
                throw new Error(`${label}格式无效，请使用 MM:SS 或 HH:MM:SS`);
            }
            const secondsPart = numbers[numbers.length - 1];
            const minuteOverflow = numbers.length === 3 && numbers[1] >= 60;
            if (secondsPart >= 60 || minuteOverflow) {
                throw new Error(`${label}格式无效，分秒不能超过 59`);
            }
            if (numbers.length === 2) return numbers[0] * 60 + numbers[1];
            return numbers[0] * 3600 + numbers[1] * 60 + numbers[2];
        }
        const seconds = Number(text);
        if (!Number.isFinite(seconds) || seconds < 0) {
            throw new Error(`${label}格式无效，请使用秒数、MM:SS 或 HH:MM:SS`);
        }
        return seconds;
    }

    async regenerateVideos(button) {
        const planAudio = this.job?.plan?.audio || {};
        const start = Number(planAudio.start_seconds ?? 0);
        const end = planAudio.end_seconds === undefined || planAudio.end_seconds === null
            ? null
            : Number(planAudio.end_seconds);
        this.regenerateVideosButton = button || null;
        if (this.els.regenerateStart) {
            this.els.regenerateStart.value = this.formatDuration(Number.isFinite(start) ? start : 0);
        }
        if (this.els.regenerateEnd) {
            this.els.regenerateEnd.value = Number.isFinite(end) ? this.formatDuration(end) : '';
        }
        if (this.els.regenerateSpeed) {
            this.els.regenerateSpeed.textContent = `${planAudio.speed || 1.2}×`;
        }
        if (this.els.regenerateError) {
            this.els.regenerateError.textContent = '';
            this.els.regenerateError.classList.add('hidden');
        }
        this.els.regenerateDialog?.showModal();
        window.setTimeout(() => {
            this.els.regenerateStart?.focus();
            this.els.regenerateStart?.select();
        }, 0);
    }

    async submitRegenerateVideos() {
        let payload;
        try {
            const start = this.parseDurationInput(this.els.regenerateStart?.value, '起点');
            const end = this.parseDurationInput(this.els.regenerateEnd?.value, '终点');
            if (start === null) throw new Error('请输入起点时间');
            if (end === null) throw new Error('请输入终点时间');
            if (end <= start) throw new Error('终点时间必须大于起点时间');
            payload = {
                start_seconds: Math.round(start * 1000) / 1000,
                end_seconds: Math.round(end * 1000) / 1000,
            };
        } catch (error) {
            if (this.els.regenerateError) {
                this.els.regenerateError.textContent = error.message;
                this.els.regenerateError.classList.remove('hidden');
            }
            return;
        }
        const button = this.regenerateVideosButton;
        const originalHtml = button?.innerHTML || '';
        const submitHtml = this.els.regenerateSubmit?.innerHTML || '';
        try {
            if (button) {
                button.disabled = true;
                button.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>投递中...';
            }
            if (this.els.regenerateSubmit) {
                this.els.regenerateSubmit.disabled = true;
                this.els.regenerateSubmit.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>投递中...';
            }
            const result = await this.request(`/api/mp3-video-workflows/jobs/${encodeURIComponent(this.jobId)}/regenerate-videos`, {
                method: 'POST',
                timeoutMs: 30000,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            this.els.regenerateDialog?.close();
            this.job = result.data || result;
            this.render();
            this.scheduleAutoRefresh();
            this.setNotice(result.message || '已重新投递完整视频生成任务。', 'success');
        } catch (error) {
            if (this.els.regenerateError) {
                this.els.regenerateError.textContent = error.message;
                this.els.regenerateError.classList.remove('hidden');
            }
        } finally {
            if (button) {
                button.disabled = false;
                button.innerHTML = originalHtml;
            }
            if (this.els.regenerateSubmit) {
                this.els.regenerateSubmit.disabled = false;
                this.els.regenerateSubmit.innerHTML = submitHtml;
            }
        }
    }

    async regenerateArtifact(form) {
        const key = String(form?.dataset?.artifactKey || '').trim();
        const textarea = form?.querySelector('textarea[name="title_text"]');
        const titleText = String(textarea?.value || '').trim();
        const lines = titleText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
        if (!key) {
            this.setNotice('缺少要重做的封皮 key。', 'error');
            return;
        }
        if (!titleText || !lines.length) {
            this.setNotice('请先输入标题文案。', 'warning');
            textarea?.focus();
            return;
        }
        if (lines.length > 3) {
            this.setNotice('标题最多支持 3 行。', 'warning');
            textarea?.focus();
            return;
        }
        const button = form.querySelector('button[type="submit"]');
        const originalHtml = button?.innerHTML || '';
        try {
            if (button) {
                button.disabled = true;
                button.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>生成中...';
            }
            const result = await this.request(`/api/mp3-video-workflows/jobs/${encodeURIComponent(this.jobId)}/artifacts/${encodeURIComponent(key)}/regenerate`, {
                method: 'POST',
                timeoutMs: 60000,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title_text: lines.join('\n') }),
            });
            this.job = result.data || result;
            this.render();
            this.setNotice(result.message || '封皮已重做并替换。后续重新生成视频会使用新版图片。', 'success');
        } catch (error) {
            this.setNotice(`重做封皮失败：${error.message}`, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                button.innerHTML = originalHtml;
            }
        }
    }

    async cancelBilibiliUpload(uploadTaskId, button) {
        const id = String(uploadTaskId || '').trim();
        if (!id) return;
        if (!window.confirm('取消这个排队中的 B站上传任务？')) return;
        const originalHtml = button?.innerHTML || '';
        try {
            if (button) {
                button.disabled = true;
                button.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>取消中';
            }
            const result = await this.request(`/api/mp3-video-workflows/jobs/${encodeURIComponent(this.jobId)}/bilibili-upload/${encodeURIComponent(id)}/cancel`, {
                method: 'POST',
                timeoutMs: 15000,
            });
            const uploadTask = result.data?.upload_task || result.upload_task || null;
            if (uploadTask) this.lastBiliUpload = uploadTask;
            await this.load();
            this.setNotice(`已取消 B站上传任务：${id}`, 'success');
        } catch (error) {
            this.setNotice(`取消 B站上传失败：${error.message}`, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                button.innerHTML = originalHtml;
            }
        }
    }

    normalizedDsExtract(job) {
        if (job.ds_extract?.has_content) return job.ds_extract;
        const rawText = this.sourceResponseText(job);
        const sections = this.numberedSections(rawText);
        const clip = this.extractClip(sections['4'] || rawText);
        const payload = {
            title_1: this.cleanDsTitle(sections['1'] || ''),
            title_2: this.cleanDsTitle(sections['2'] || ''),
            clip,
            title_6: this.cleanDsTitle(sections['6'] || '', { preferClosedDoor: true }),
            title_7: this.cleanDsTitle(sections['7'] || '', { preferClosedDoor: true }),
            raw_text: rawText,
            raw_sections: sections,
        };
        payload.has_content = Boolean(payload.title_1 || payload.title_2 || payload.title_6 || payload.title_7 || clip.start_text || clip.end_text);
        return payload;
    }

    sourceResponseText(job) {
        const raw = job.raw_result || {};
        const source = job.source || raw.source || {};
        const gemini = job.gemini || raw.gemini || {};
        return [
            source.response_text,
            source.initial_response_text,
            source.final_response_text,
            job.initial_response_text,
            raw.initial_response_text,
            gemini.response_text,
            gemini.initial_response_text,
            gemini.final_response_text,
        ].map((item) => String(item || '').trim()).find(Boolean) || '';
    }

    numberedSections(text) {
        const source = String(text || '');
        if (!source.trim()) return {};
        const markerRe = /(?:(?<=\n)|^)\s*(?:[-*]\s*)?(?:\*\*)?\s*([1-7])\s*[.．、:：]\s*|(?:\*\*)\s*([1-7])\s*[.．、:：]\s*/g;
        const matches = Array.from(source.matchAll(markerRe));
        const sections = {};
        matches.forEach((match, index) => {
            const number = match[1] || match[2];
            const start = match.index + match[0].length;
            const end = index + 1 < matches.length ? matches[index + 1].index : source.length;
            const content = source.slice(start, end).trim();
            if (number && content) sections[number] = content;
        });
        return sections;
    }

    cleanDsText(text) {
        return String(text || '')
            .replace(/\*\*/g, '')
            .replace(/__/g, '')
            .replace(/^[\s\-*•]+/, '')
            .replace(/\s+/g, ' ')
            .replace(/^[\s：:，,。;；]+|[\s：:，,。;；]+$/g, '');
    }

    cleanDsTitle(text, options = {}) {
        let value = this.dsTitleContentLines(text).join(' ') || this.cleanDsText(text);
        if (!value) return '';
        if (options.preferClosedDoor) {
            value = this.stripClosedDoorTemplateNoise(value);
            const patterns = [
                /内资闭门会\s*[\/|｜].*?(?:\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{6,8})/g,
                /内资闭门会\s*[\/|｜][^。；\n]+/g,
            ];
            for (const pattern of patterns) {
                const matches = [...value.matchAll(pattern)]
                    .map((match) => this.trimTitle(match[0]))
                    .filter((item) => item && !this.isPlaceholderClosedDoorTitle(item));
                if (matches.length) return matches[matches.length - 1];
            }
        }
        const quoted = value.match(/^[“"]([^”"]{6,})[”"]$/);
        if (quoted) return this.trimTitle(quoted[1]);
        value = value.replace(/^(?:高信息密度)?(?:投资人|投资者)?(?:视角)?(?:标题|长标题|标题版本[一二三四五六七八九十A-Za-z\d]*|版本[一二]|标题优化|闭门会格式标题)(?:（[^）]*）|\([^)]*\))?\s*[:：]?\s*/, '');
        value = value.replace(/^(?:斜杠版|竖线版|另一个版本|版本[一二])\s*[:：]?\s*/, '');
        return this.trimTitle(value);
    }

    dsTitleContentLines(text) {
        const lines = [];
        String(text || '').replace(/\r/g, '\n').split('\n').forEach((raw) => {
            let clean = this.cleanDsText(raw);
            if (!clean || this.isDsTitleHeading(clean)) return;
            clean = clean
                .replace(/^(?:高信息密度)?(?:投资人|投资者)?(?:视角)?(?:标题|长标题|标题版本[一二三四五六七八九十A-Za-z\d]*|版本[一二]|短标题版本|标题优化|优化标题格式|优化标题|闭门会格式标题)(?:（[^）]*）|\([^)]*\))?\s*[:：-]?\s*/, '')
                .replace(/^[\s：:，,。;；]+|[\s：:，,。;；]+$/g, '');
            if (clean && !this.isDsTitleHeading(clean)) lines.push(clean);
        });
        return lines;
    }

    isDsTitleHeading(value) {
        return /^(?:高信息密度)?(?:投资人|投资者)?(?:视角)?(?:标题|长标题|标题版本[一二三四五六七八九十A-Za-z\d]*|版本[一二]|短标题版本|精简短标题|标题优化|优化标题格式|优化标题|闭门会格式标题)(?:（[^）]*）|\([^)]*\))?$/i.test(this.cleanDsText(value));
    }

    firstCleanBiliTitle(...values) {
        for (const value of values) {
            const cleaned = this.cleanDsTitle(value, { preferClosedDoor: true });
            if (cleaned && !this.isPlaceholderClosedDoorTitle(cleaned)) return cleaned;
        }
        return '';
    }

    stripClosedDoorTemplateNoise(value) {
        return String(value || '')
            .replace(/(?:使用|格式\s*[:：]?)?\s*内资闭门会(?:议)?\s*[\/|｜]\s*xxxx\s*[\/|｜]\s*xxxx\s*[\/|｜]\s*xxxx\s*[\/|｜]\s*当日日期[)）]?/gi, ' ')
            .replace(/这样的格式来写/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();
    }

    isPlaceholderClosedDoorTitle(value) {
        return /xxxx|当日日期/i.test(String(value || ''));
    }

    trimTitle(value) {
        return this.cleanDsText(value).replace(/^[「」『』“”"']+|[「」『』“”"']+$/g, '').slice(0, 500);
    }

    extractClip(text) {
        const value = this.cleanDsText(text);
        const timeToken = '(?:\\d{1,2}:)?\\d{1,2}:\\d{2}|\\d+(?:\\.\\d+)?\\s*(?:秒|s)?';
        const pair = value.match(new RegExp(`(${timeToken})\\s*(?:-|—|–|~|～|至|到)\\s*(${timeToken})`));
        let start = pair?.[1] || '';
        let end = pair?.[2] || '';
        if (!start || !end) {
            const startMatch = value.match(new RegExp(`(?:开始|起点|起始)(?:时间戳|时间)?\\s*[:：]?\\s*[\\[【(（]?\\s*(${timeToken})`));
            const endMatch = value.match(new RegExp(`(?:结束|终点|截止|终止)(?:时间戳|时间)?\\s*[:：]?\\s*[\\[【(（]?\\s*(${timeToken})`));
            start = start || startMatch?.[1] || '';
            end = end || endMatch?.[1] || '';
        }
        const startSeconds = this.timestampToSeconds(start);
        const endSeconds = this.timestampToSeconds(end);
        return {
            start_text: Number.isFinite(startSeconds) ? this.formatDuration(startSeconds) : '',
            end_text: Number.isFinite(endSeconds) ? this.formatDuration(endSeconds) : '',
            start_seconds: Number.isFinite(startSeconds) ? startSeconds : null,
            end_seconds: Number.isFinite(endSeconds) ? endSeconds : null,
            summary: value.slice(0, 1000),
        };
    }

    timestampToSeconds(value) {
        const text = String(value || '').trim().toLowerCase().replace('秒', '').replace('s', '');
        if (!text) return NaN;
        if (text.includes(':')) {
            const parts = text.split(':').filter(Boolean).map((item) => Number(item));
            if (parts.some((item) => !Number.isFinite(item))) return NaN;
            if (parts.length === 2) return Math.round(parts[0] * 60 + parts[1]);
            if (parts.length === 3) return Math.round(parts[0] * 3600 + parts[1] * 60 + parts[2]);
            return NaN;
        }
        return Number(text);
    }

    async copyText(text) {
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
            this.setNotice('已复制。', 'success');
        } catch (error) {
            this.setNotice(`复制失败：${error.message || error}`, 'error');
        }
    }

    setNotice(message, type = 'info', hidden = false) {
        if (hidden) {
            this.els.notice.classList.add('hidden');
            this.els.notice.textContent = '';
            return;
        }
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

    statusMeta(status) {
        const map = {
            queued: ['排队中', 'bg-amber-400', 'bg-amber-50 text-amber-700', 'bg-amber-500'],
            running: ['运行中', 'bg-blue-500', 'bg-blue-50 text-blue-700', 'bg-blue-600'],
            pending: ['排队中', 'bg-amber-400', 'bg-amber-50 text-amber-700', 'bg-amber-500'],
            processing: ['运行中', 'bg-blue-500', 'bg-blue-50 text-blue-700', 'bg-blue-600'],
            succeeded: ['成功', 'bg-emerald-500', 'bg-emerald-50 text-emerald-700', 'bg-emerald-600'],
            completed: ['成功', 'bg-emerald-500', 'bg-emerald-50 text-emerald-700', 'bg-emerald-600'],
            failed: ['失败', 'bg-rose-500', 'bg-rose-50 text-rose-700', 'bg-rose-600'],
            canceled: ['已取消', 'bg-slate-400', 'bg-slate-100 text-slate-600', 'bg-slate-400'],
        };
        const [label, dot, className, bar] = map[status] || [status || '未知', 'bg-slate-400', 'bg-slate-100 text-slate-600', 'bg-slate-500'];
        return { label, dot, className, bar };
    }

    absoluteUrl(path) {
        if (!path) return '#';
        if (/^https?:\/\//.test(path)) return path;
        return `${this.apiRoot()}${path}`;
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

    formatDuration(value) {
        const seconds = Number(value);
        if (!Number.isFinite(seconds)) return String(value || '');
        const total = Math.max(0, Math.round(seconds));
        const hours = Math.floor(total / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        const remainder = total % 60;
        const pad = (item) => String(item).padStart(2, '0');
        if (hours > 0) return `${pad(hours)}:${pad(minutes)}:${pad(remainder)}`;
        return `${pad(minutes)}:${pad(remainder)}`;
    }

    escape(value) {
        return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;',
        }[ch]));
    }

    escapeAttr(value) {
        return this.escape(value).replace(/`/g, '&#096;');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.mp3VideoWorkflowDetailPage = new Mp3VideoWorkflowDetailPage();
});
