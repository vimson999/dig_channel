class BilibiliPublishPanel {
    constructor(options = {}) {
        this.options = {
            defaultTags: '财经,音频,内资闭门会',
            defaultTid: 207,
            emptyDescription: '当前任务还没有可投递的视频。',
            ...options,
        };
        this.container = options.container;
        this.job = null;
        this.credentials = [];
        this.credentialsError = '';
        this.credentialsLoaded = false;
        this.credentialsLoading = false;
        this.chargingOptionsByCredential = {};
        this.chargingOptionsErrorByCredential = {};
        this.chargingOptionsPromises = {};
        this.chargingOptionsLoading = '';
        this.seasonsByCredential = {};
        this.seasonsErrorByCredential = {};
        this.seasonsPromises = {};
        this.seasonsLoading = '';
        this.lastUpload = null;
        this.formDraft = null;
        this.instanceId = `bili-${Math.random().toString(36).slice(2, 9)}`;
        this.bind();
    }

    bind() {
        if (!this.container) return;
        this.container.addEventListener('change', (event) => {
            if (event.target?.dataset?.biliField === 'video') {
                this.applyVideoDefaults(event.target.value);
                return;
            }
            if (event.target?.dataset?.biliField === 'credential') {
                this.onCredentialChange(event.target.value);
                return;
            }
            if (event.target?.dataset?.biliField === 'charging-enabled') {
                this.toggleChargingFields(event.target.checked);
                return;
            }
            if (event.target?.dataset?.biliField === 'schedule-enabled' || event.target?.dataset?.biliField === 'schedule-time') {
                this.refreshScheduleInput();
            }
        });
        this.container.addEventListener('click', (event) => {
            const cancelButton = event.target.closest('[data-bili-shared-cancel]');
            if (cancelButton) {
                event.preventDefault();
                this.cancelUpload(cancelButton.dataset.biliSharedCancel || '', cancelButton);
                return;
            }
            const clearSeasonButton = event.target.closest('[data-bili-shared-clear-season]');
            if (clearSeasonButton) {
                event.preventDefault();
                const select = this.field('season');
                if (select) select.value = '';
            }
        });
        this.container.addEventListener('submit', (event) => {
            const form = event.target.closest('[data-bili-shared-form]');
            if (!form) return;
            event.preventDefault();
            this.submit(form);
        });
    }

    setJob(job, options = {}) {
        const previousJobId = this.job?.job_id || this.job?.id || '';
        const nextJobId = job?.job_id || job?.id || '';
        const incomingDraft = options.formDraft ? { ...options.formDraft } : null;
        if (previousJobId !== nextJobId) {
            this.formDraft = incomingDraft || null;
            this.lastUpload = null;
        } else if (incomingDraft) {
            this.formDraft = incomingDraft;
        }
        this.job = job || {};
        this.render();
        this.loadCredentials();
    }

    updateJobData(job) {
        this.job = job || this.job || {};
        this.refreshHistory();
    }

    apiRoot() {
        if (this.options.apiRoot) return this.options.apiRoot();
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
        if (this.options.request) return this.options.request(path, options);
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

    notify(message, type = 'info') {
        if (this.options.onNotice) this.options.onNotice(message, type);
    }

    async loadCredentials() {
        if (this.credentialsLoaded || this.credentialsLoading) return;
        this.credentialsLoading = true;
        this.render();
        try {
            const result = await this.request('/api/v1/platform-credentials?platform=bilibili&purpose=bilibili.upload&status=active&per_page=100', { timeoutMs: 15000 });
            this.credentials = result.data?.items || result.items || [];
            this.credentialsError = '';
        } catch (error) {
            this.credentials = [];
            this.credentialsError = error.message || String(error);
        } finally {
            this.credentialsLoaded = true;
            this.credentialsLoading = false;
            this.render();
            const credentialId = this.field('credential')?.value || this.credentials[0]?.id || '';
            if (credentialId) this.loadSeasons(credentialId);
        }
    }

    async loadSeasons(credentialId) {
        const id = String(credentialId || '').trim();
        if (!id) return;
        if (Object.prototype.hasOwnProperty.call(this.seasonsByCredential, id) && !this.seasonsErrorByCredential[id]) return;
        if (this.seasonsPromises[id]) return this.seasonsPromises[id];
        this.seasonsLoading = id;
        this.applySeasonOptions(id);
        this.seasonsPromises[id] = (async () => {
            try {
                const result = await this.request(`/api/bilibili-publish/seasons?credential_id=${encodeURIComponent(id)}`, { timeoutMs: 20000 });
                this.seasonsByCredential[id] = Array.isArray(result.data?.items)
                    ? result.data.items
                    : (Array.isArray(result.data) ? result.data : (result.items || []));
                delete this.seasonsErrorByCredential[id];
            } catch (error) {
                delete this.seasonsByCredential[id];
                this.seasonsErrorByCredential[id] = error.message || String(error);
            } finally {
                delete this.seasonsPromises[id];
                if (this.seasonsLoading === id) this.seasonsLoading = '';
                this.applySeasonOptions(id);
            }
        })();
        return this.seasonsPromises[id];
    }

    async loadChargingOptions(credentialId) {
        const id = String(credentialId || '').trim();
        if (!id || this.chargingOptionsByCredential[id]) return;
        if (this.chargingOptionsPromises[id]) return this.chargingOptionsPromises[id];
        this.chargingOptionsLoading = id;
        this.applyChargingOptions(id);
        this.chargingOptionsPromises[id] = (async () => {
            try {
                const result = await this.request(`/api/bilibili-publish/charging-options?credential_id=${encodeURIComponent(id)}`, { timeoutMs: 20000 });
                this.chargingOptionsByCredential[id] = result.data || result;
                delete this.chargingOptionsErrorByCredential[id];
            } catch (error) {
                this.chargingOptionsErrorByCredential[id] = error.message || String(error);
            } finally {
                delete this.chargingOptionsPromises[id];
                if (this.chargingOptionsLoading === id) this.chargingOptionsLoading = '';
                this.applyChargingOptions(id);
            }
        })();
        return this.chargingOptionsPromises[id];
    }

    videoOptions() {
        const options = this.options.videoOptions ? this.options.videoOptions(this.job) : [];
        return (Array.isArray(options) ? options : []).filter((item) => item?.key);
    }

    coverOptions(videoKey) {
        const options = this.options.coverOptions ? this.options.coverOptions(this.job, videoKey) : [];
        return Array.isArray(options) ? options : [];
    }

    uploads() {
        const items = this.options.uploads ? this.options.uploads(this.job) : (this.job?.bilibili_uploads || []);
        const merged = Array.isArray(items) ? [...items] : [];
        if (this.lastUpload && !merged.some((item) => item.task_id === this.lastUpload.task_id)) {
            merged.unshift(this.lastUpload);
        }
        return merged;
    }

    render() {
        if (!this.container) return;
        this.formDraft = this.readFormDraft();
        const videos = this.videoOptions();
        const hasVideos = videos.length > 0;
        const selectedVideoKey = this.pickVideoKey(videos);
        const selectedVideo = videos.find((item) => item.key === selectedVideoKey) || videos[0] || null;
        const covers = this.coverOptions(selectedVideo?.key || '');
        const selectedCover = this.pickCoverKey(selectedVideo?.key || '', covers);
        const selectedCredentialId = this.pickCredentialId();
        const disabled = !hasVideos || !this.credentials.length;
        const unavailableMessages = [
            hasVideos ? '' : this.options.emptyDescription,
            !this.credentials.length && !this.credentialsLoading ? '暂无可用 B站上传账号。请先在账号管理里导入或新增用途为「B站视频上传」的账号。' : '',
        ].filter(Boolean);
        const defaultTitle = this.firstNonBlank(this.formDraft?.title, this.defaultTitle(selectedVideo));
        const defaultDesc = this.formDraft?.desc ?? '';
        const defaultTags = this.firstNonBlank(this.formDraft?.tags, this.options.defaultTags);
        const defaultTid = this.firstNonBlank(this.formDraft?.tid, this.options.defaultTid);
        const onlySelfChecked = this.formDraft?.is_only_self === '1';

        this.container.innerHTML = `
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
                ${this.credentialsError ? `<div class="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-800">B站账号读取失败：${this.escape(this.credentialsError)}</div>` : ''}
                ${this.credentialsLoading ? '<div class="mb-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-500"><i class="fas fa-spinner fa-spin mr-1.5"></i>正在读取 B站账号...</div>' : ''}
                ${unavailableMessages.map((message) => `<div class="mb-3 rounded-md border border-dashed border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-500">${this.escape(message)}</div>`).join('')}
                <form data-bili-shared-form class="space-y-3">
                    <div class="grid grid-cols-1 gap-3 lg:grid-cols-3">
                        <label class="block">
                            <span class="text-[11px] font-black uppercase text-slate-400">账号</span>
                            <select data-bili-field="credential" name="credential_id" class="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-bold text-slate-800" ${disabled ? 'disabled' : ''}>
                                ${this.credentials.length ? this.credentials.map((item) => `
                                    <option value="${this.escapeAttr(item.id)}" ${String(item.id || '') === String(selectedCredentialId || '') ? 'selected' : ''}>${this.escape(item.owner_label || item.name || item.masked_summary || item.id)}</option>
                                `).join('') : '<option value="">暂无可用账号</option>'}
                            </select>
                        </label>
                        <label class="block">
                            <span class="text-[11px] font-black uppercase text-slate-400">视频</span>
                            <select data-bili-field="video" name="video_key" class="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-bold text-slate-800" ${disabled ? 'disabled' : ''}>
                                ${videos.length ? videos.map((item) => `
                                    <option value="${this.escapeAttr(item.key)}" ${item.key === selectedVideoKey ? 'selected' : ''}>${this.escape(item.label || item.key)}</option>
                                `).join('') : '<option value="">暂无可投递视频</option>'}
                            </select>
                        </label>
                        <label class="block">
                            <span class="text-[11px] font-black uppercase text-slate-400">封面</span>
                            <select data-bili-field="cover" name="cover_key" class="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-bold text-slate-800" ${disabled ? 'disabled' : ''}>
                                <option value="">自动抽帧</option>
                                ${covers.map((item) => `
                                    <option value="${this.escapeAttr(item.key)}" ${item.key === selectedCover ? 'selected' : ''}>${this.escape(item.label || item.key)}</option>
                                `).join('')}
                            </select>
                        </label>
                    </div>
                    <label class="block">
                        <span class="text-[11px] font-black uppercase text-slate-400">标题</span>
                        <input data-bili-field="title" name="title" value="${this.escapeAttr(defaultTitle)}" maxlength="120" class="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-bold text-slate-900" ${disabled ? 'disabled' : ''}>
                    </label>
                    <div class="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_220px_140px]">
                        <label class="block">
                            <span class="text-[11px] font-black uppercase text-slate-400">简介</span>
                            <textarea name="desc" rows="3" class="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm leading-6 text-slate-800" ${disabled ? 'disabled' : ''}>${this.escape(defaultDesc)}</textarea>
                        </label>
                        <label class="block">
                            <span class="text-[11px] font-black uppercase text-slate-400">标签</span>
                            <input name="tags" value="${this.escapeAttr(defaultTags)}" class="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-bold text-slate-800" ${disabled ? 'disabled' : ''}>
                        </label>
                        <label class="block">
                            <span class="text-[11px] font-black uppercase text-slate-400">分区</span>
                            <input name="tid" type="number" value="${this.escapeAttr(defaultTid)}" min="1" class="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-bold text-slate-800" ${disabled ? 'disabled' : ''}>
                        </label>
                    </div>
                    <div class="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_1.2fr]">
                        <div class="rounded-md border border-slate-200 bg-white p-3">
                            <div class="grid grid-cols-[86px_1fr_auto] items-center gap-3">
                                <div class="text-xs text-slate-600">加入合集</div>
                                <select data-bili-field="season" name="season_id" class="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-bold text-slate-800 disabled:bg-slate-100 disabled:text-slate-400" ${disabled ? 'disabled' : ''}>
                                    ${this.renderSeasonOptions(selectedCredentialId)}
                                </select>
                                <button type="button" data-bili-shared-clear-season="1" class="rounded-md px-2 py-1 text-xs font-bold text-slate-500 hover:bg-slate-100" ${disabled ? 'disabled' : ''}>清除</button>
                            </div>
                            <div data-bili-note="season" class="ml-[98px] mt-1 text-xs text-slate-400">${this.escape(this.seasonNote(selectedCredentialId))}</div>
                        </div>
                        <div class="rounded-md border border-slate-200 bg-white p-3">
                            <div class="grid grid-cols-[86px_1fr] items-center gap-3">
                                <div class="text-xs text-slate-600">定时发布</div>
                                <div class="flex flex-wrap items-center gap-3">
                                    <label class="inline-flex cursor-pointer items-center gap-3 font-bold text-sm text-slate-700">
                                        <input data-bili-field="schedule-enabled" name="schedule_enabled" type="checkbox" value="1" class="peer sr-only" ${this.formDraft?.schedule_enabled === '1' ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
                                        <span class="relative h-5 w-10 rounded-full bg-slate-300 transition peer-checked:bg-sky-500 peer-disabled:opacity-50 after:absolute after:left-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:shadow-sm after:transition peer-checked:after:translate-x-5"></span>
                                        <span>启用定时</span>
                                    </label>
                                    <input data-bili-field="schedule-time" name="schedule_time" type="datetime-local" value="${this.escapeAttr(this.formDraft?.schedule_time || '')}" class="min-w-[210px] rounded-md border border-slate-300 px-3 py-2 text-sm font-bold text-slate-800 disabled:bg-slate-100 disabled:text-slate-400" disabled>
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
                                    <input data-bili-field="charging-enabled" name="charging_enabled" type="checkbox" value="1" class="peer sr-only" ${this.formDraft?.charging_enabled === '1' ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
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
                                        ${['00:01:00', '00:03:00', '00:05:00', '00:10:00'].map((value) => `<option value="${value}" ${value === (this.formDraft?.charging_preview_end_time || '00:03:00') ? 'selected' : ''}>${value}</option>`).join('')}
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
                                    <select data-bili-field="upower-level" name="upower_level_id" data-bili-charging-field class="min-w-[180px] rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-bold text-slate-800 disabled:bg-slate-100 disabled:text-slate-400" disabled>
                                        ${this.renderChargingLevelOptions(selectedCredentialId)}
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
                            <div data-bili-note="charging" class="ml-[98px] text-xs text-slate-400">${this.escape(this.chargingNote(selectedCredentialId))}</div>
                        </div>
                    </div>
                    <div class="flex flex-wrap items-center justify-between gap-3">
                        <label class="inline-flex items-center gap-2 text-xs font-bold text-slate-600">
                            <input name="is_only_self" type="checkbox" value="1" class="rounded border-slate-300" ${onlySelfChecked ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
                            仅自己可见
                        </label>
                        <button data-bili-field="submit" type="submit" class="rounded-lg bg-slate-950 px-4 py-2 text-sm font-black text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300" ${disabled ? 'disabled' : ''}>
                            <i class="fab fa-bilibili mr-1.5"></i>${hasVideos ? '投递 B站上传' : '等待视频生成'}
                        </button>
                    </div>
                </form>
                ${this.renderHistory(this.uploads())}
            </section>
        `;
        this.refreshScheduleInput();
        this.toggleChargingFields(this.field('charging-enabled')?.checked || false);
        if (selectedCredentialId) this.applySeasonOptions(selectedCredentialId);
    }

    readFormDraft() {
        const form = this.container?.querySelector('[data-bili-shared-form]');
        if (!form) return this.formDraft;
        const data = new FormData(form);
        const previous = this.formDraft || {};
        const credentialId = data.get('credential_id') || '';
        const credentialSelect = form.elements.credential_id;
        const seasonSelect = form.elements.season_id;
        const chargingLevelSelect = form.elements.upower_level_id;
        const formHasCredentialChoices = this.hasNonEmptyOption(credentialSelect);
        const formHasSeasonChoices = this.hasNonEmptyOption(seasonSelect);
        const formHasChargingChoices = this.hasNonEmptyOption(chargingLevelSelect);
        return {
            credential_id: formHasCredentialChoices || credentialId ? credentialId : (previous.credential_id || ''),
            video_key: data.get('video_key') || '',
            cover_key: data.get('cover_key') || '',
            title: data.get('title') || '',
            desc: data.get('desc') || '',
            tags: data.get('tags') || '',
            tid: data.get('tid') || '',
            season_id: formHasSeasonChoices || data.get('season_id') ? (data.get('season_id') || '') : (previous.season_id || ''),
            is_only_self: data.get('is_only_self') ? '1' : '0',
            charging_enabled: data.get('charging_enabled') ? '1' : '0',
            charging_preview_end_time: data.get('charging_preview_end_time') || '00:03:00',
            upower_level_id: formHasChargingChoices || data.get('upower_level_id') ? (data.get('upower_level_id') || '') : (previous.upower_level_id || ''),
            schedule_enabled: data.get('schedule_enabled') ? '1' : '0',
            schedule_time: data.get('schedule_time') || '',
        };
    }

    hasNonEmptyOption(select) {
        if (!select?.options) return false;
        return Array.from(select.options).some((option) => String(option.value || '').trim());
    }

    field(name) {
        return this.container?.querySelector(`[data-bili-field="${name}"]`) || null;
    }

    pickVideoKey(videos) {
        const current = this.formDraft?.video_key;
        if (current && videos.some((item) => item.key === current)) return current;
        return videos[0]?.key || '';
    }

    pickCoverKey(videoKey, covers) {
        const current = this.formDraft?.cover_key;
        if (current && covers.some((item) => item.key === current)) return current;
        if (this.options.defaultCoverKey) return this.options.defaultCoverKey(this.job, videoKey) || '';
        return covers[0]?.key || '';
    }

    pickCredentialId() {
        const current = this.formDraft?.credential_id;
        if (current && this.credentials.some((item) => String(item.id || '') === String(current))) return current;
        return this.credentials[0]?.id || '';
    }

    defaultTitle(video) {
        const title = video?.defaultTitle || (this.options.defaultTitle ? this.options.defaultTitle(this.job, video?.key || '') : '') || '';
        return String(title || '').replace(/\s+/g, ' ').trim().slice(0, 120);
    }

    firstNonBlank(...values) {
        for (const value of values) {
            const text = String(value ?? '').trim();
            if (text) return text;
        }
        return '';
    }

    applyVideoDefaults(videoKey) {
        if (!this.job) return;
        const video = this.videoOptions().find((item) => item.key === videoKey);
        const titleInput = this.field('title');
        const coverSelect = this.field('cover');
        if (titleInput) titleInput.value = this.defaultTitle(video);
        if (!coverSelect) return;
        const covers = this.coverOptions(videoKey);
        const defaultCover = this.options.defaultCoverKey ? this.options.defaultCoverKey(this.job, videoKey) : (covers[0]?.key || '');
        coverSelect.innerHTML = [
            '<option value="">自动抽帧</option>',
            ...covers.map((item) => `<option value="${this.escapeAttr(item.key)}" ${item.key === defaultCover ? 'selected' : ''}>${this.escape(item.label || item.key)}</option>`),
        ].join('');
    }

    onCredentialChange(credentialId) {
        this.applySeasonOptions(credentialId);
        this.loadSeasons(credentialId);
        if (this.field('charging-enabled')?.checked) {
            this.loadChargingOptions(credentialId);
        } else {
            this.applyChargingOptions(credentialId);
        }
    }

    renderSeasonOptions(credentialId) {
        const id = String(credentialId || '').trim();
        const seasons = Array.isArray(this.seasonsByCredential[id]) ? this.seasonsByCredential[id] : [];
        const loading = this.seasonsLoading === id;
        const error = this.seasonsErrorByCredential[id];
        const placeholder = loading
            ? '<option value="">读取合集中...</option>'
            : (error ? '<option value="">合集读取失败</option>' : '<option value="">不加入合集</option>');
        const selected = this.formDraft?.season_id || '';
        return [
            placeholder,
            ...seasons.map((item) => `<option value="${this.escapeAttr(item.id || '')}" ${String(item.id || '') === String(selected || '') ? 'selected' : ''}>${this.escape(item.title || item.name || item.id || '未命名合集')}</option>`),
        ].join('');
    }

    seasonNote(credentialId) {
        const id = String(credentialId || '').trim();
        if (!id) return '';
        if (this.seasonsLoading === id) return '正在读取该账号的合集...';
        const error = this.seasonsErrorByCredential[id];
        if (error) return `合集读取失败：${error}`;
        const seasons = this.seasonsByCredential[id] || [];
        return seasons.length ? `已读取 ${seasons.length} 个合集` : '该账号暂无可选合集，或 B站未返回合集列表';
    }

    applySeasonOptions(credentialId) {
        const select = this.field('season');
        const previous = select?.value || this.formDraft?.season_id || '';
        if (select) {
            select.innerHTML = this.renderSeasonOptions(credentialId);
            if (previous && Array.from(select.options).some((option) => option.value === previous)) {
                select.value = previous;
            }
        }
        const note = this.container?.querySelector('[data-bili-note="season"]');
        if (note) note.textContent = this.seasonNote(credentialId);
    }

    toggleChargingFields(enabled) {
        const form = this.container?.querySelector('[data-bili-shared-form]');
        if (!form) return;
        form.querySelectorAll('[data-bili-charging-field]').forEach((field) => {
            field.disabled = !enabled;
        });
        if (enabled) {
            const credentialId = this.field('credential')?.value || '';
            this.loadChargingOptions(credentialId);
        }
    }

    renderChargingLevelOptions(credentialId) {
        const id = String(credentialId || '').trim();
        const options = this.chargingOptionsByCredential[id] || {};
        const levels = Array.isArray(options.levels) ? options.levels : [];
        const selected = this.formDraft?.upower_level_id || options.default_level_id || levels[0]?.id || '';
        if (!levels.length) return '<option value="">自动默认档</option>';
        return levels.map((level) => `
            <option value="${this.escapeAttr(level.id || '')}" data-price="${this.escapeAttr(level.upower_unit_price || 0)}" ${String(level.id || '') === String(selected || '') ? 'selected' : ''}>${this.escape(level.label || level.id || '充电档位')}</option>
        `).join('');
    }

    chargingNote(credentialId) {
        const id = String(credentialId || '').trim();
        if (!id) return '';
        if (this.chargingOptionsLoading === id) return '正在读取该账号的充电档位...';
        const error = this.chargingOptionsErrorByCredential[id];
        if (error) return `档位读取失败，将使用账号默认档：${error}`;
        const levels = this.chargingOptionsByCredential[id]?.levels || [];
        return levels.length ? `已读取 ${levels.length} 个充电档位` : '勾选充电后读取该账号档位';
    }

    applyChargingOptions(credentialId) {
        const select = this.field('upower-level');
        if (select) select.innerHTML = this.renderChargingLevelOptions(credentialId);
        const note = this.container?.querySelector('[data-bili-note="charging"]');
        if (note) note.textContent = this.chargingNote(credentialId);
    }

    refreshScheduleInput() {
        const checkbox = this.field('schedule-enabled');
        const input = this.field('schedule-time');
        if (!checkbox || !input) return;
        const minDate = new Date(Date.now() + 6 * 60 * 1000);
        const maxDate = new Date(Date.now() + 15 * 24 * 60 * 60 * 1000);
        minDate.setSeconds(0, 0);
        maxDate.setSeconds(0, 0);
        input.min = this.formatDateTimeLocal(minDate);
        input.max = this.formatDateTimeLocal(maxDate);
        input.disabled = !checkbox.checked;
        if (checkbox.checked && !input.value) input.value = input.min;
        if (!checkbox.checked) input.value = '';
    }

    getScheduleTimestamp(formData) {
        const enabled = this.field('schedule-enabled')?.checked ?? false;
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

    async submit(form) {
        const button = this.field('submit');
        const originalHtml = button?.innerHTML || '';
        const data = new FormData(form);
        const chargingEnabled = Boolean(data.get('charging_enabled'));
        const videoKey = data.get('video_key') || 'landscape';
        const selectedVideo = this.videoOptions().find((item) => item.key === videoKey);
        let delayTime = null;
        try {
            delayTime = this.getScheduleTimestamp(data);
        } catch (error) {
            this.notify(error.message || 'B站定时发布时间无效', 'warning');
            return;
        }
        const payload = {
            credential_id: data.get('credential_id') || '',
            video_key: videoKey,
            cover_key: data.get('cover_key') || '',
            title: this.firstNonBlank(data.get('title'), this.defaultTitle(selectedVideo)),
            desc: data.get('desc') || '',
            tags: this.firstNonBlank(data.get('tags'), this.options.defaultTags),
            tid: this.firstNonBlank(data.get('tid'), this.options.defaultTid),
            is_only_self: data.get('is_only_self') ? 1 : 0,
            season_id: data.get('season_id') || '',
        };
        if (delayTime) {
            payload.schedule_enabled = 1;
            payload.delay_time = delayTime;
        }
        if (chargingEnabled) {
            if (!payload.credential_id) {
                this.notify('启用充电专属前，请先选择一个 B站账号。', 'warning');
                return;
            }
            await this.loadChargingOptions(payload.credential_id);
            if (this.chargingOptionsErrorByCredential[payload.credential_id]) {
                this.notify(`充电档位读取失败：${this.chargingOptionsErrorByCredential[payload.credential_id]}`, 'error');
                return;
            }
            const levels = this.chargingOptionsByCredential[payload.credential_id]?.levels || [];
            const levelSelect = this.field('upower-level');
            const selectedLevel = levelSelect?.selectedOptions?.[0];
            const selectedLevelId = levelSelect?.value || '';
            const previewEndTime = form.querySelector('[name="charging_preview_end_time"]')?.value || '00:03:00';
            const previewEnabled = Boolean(form.querySelector('[name="charging_preview_enabled"]')?.checked);
            if (!levels.length || !selectedLevelId) {
                this.notify('启用充电专属前，需要先读取并选择一个可用的充电档位。', 'warning');
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
            const uploadPath = this.options.uploadPath?.(this.job?.job_id || this.job?.id || '');
            if (!uploadPath) throw new Error('缺少 B站上传接口');
            const result = await this.request(uploadPath, {
                method: 'POST',
                timeoutMs: 30000,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            this.lastUpload = result.data?.upload_task || result.upload_task || null;
            this.formDraft = this.readFormDraft();
            this.refreshHistory();
            if (this.options.onAfterSubmit) await this.options.onAfterSubmit(result);
            this.notify(`已投递 B站上传任务：${this.lastUpload?.task_id || ''}`, 'success');
        } catch (error) {
            this.notify(`B站上传投递失败：${error.message}`, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                button.innerHTML = originalHtml;
            }
        }
    }

    async cancelUpload(uploadTaskId, button) {
        const id = String(uploadTaskId || '').trim();
        if (!id) return;
        if (!window.confirm('取消这个排队中的 B站上传任务？')) return;
        const originalHtml = button?.innerHTML || '';
        try {
            if (button) {
                button.disabled = true;
                button.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>取消中';
            }
            const cancelPath = this.options.cancelPath?.(this.job?.job_id || this.job?.id || '', id);
            if (!cancelPath) throw new Error('缺少 B站上传取消接口');
            const result = await this.request(cancelPath, { method: 'POST', timeoutMs: 15000 });
            this.lastUpload = result.data?.upload_task || result.upload_task || this.lastUpload;
            this.refreshHistory();
            if (this.options.onAfterCancel) await this.options.onAfterCancel(result);
            this.notify(`已取消 B站上传任务：${id}`, 'success');
        } catch (error) {
            this.notify(`取消 B站上传失败：${error.message}`, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                button.innerHTML = originalHtml;
            }
        }
    }

    refreshHistory() {
        const target = this.container?.querySelector('[data-bili-history]');
        if (target) target.outerHTML = this.renderHistory(this.uploads());
    }

    renderHistory(items) {
        if (!items.length) return '<div data-bili-history></div>';
        return `
            <div data-bili-history class="mt-4 border-t border-slate-100 pt-3">
                <div class="mb-2 text-[11px] font-black uppercase text-slate-400">上传记录</div>
                <div class="space-y-2">
                    ${items.slice(0, 8).map((item) => {
                        const status = this.statusMeta(item.status);
                        const canCancel = ['queued', 'pending'].includes(String(item?.status || '').toLowerCase());
                        return `
                            <div class="rounded-md border border-slate-100 bg-slate-50 px-3 py-2 text-xs">
                                <div class="flex flex-wrap items-center justify-between gap-2">
                                    <div class="min-w-0 font-bold text-slate-800">${this.escape(item.title || item.task_id || '')}</div>
                                    <div class="flex items-center gap-2">
                                        ${canCancel ? `
                                            <button type="button" data-bili-shared-cancel="${this.escapeAttr(item.task_id || '')}" class="rounded-md border border-rose-200 bg-white px-2 py-1 font-bold text-rose-600 hover:bg-rose-50">
                                                取消
                                            </button>
                                        ` : ''}
                                        <span class="rounded-full px-2 py-1 font-bold ${status.className}">${this.escape(status.label)}</span>
                                    </div>
                                </div>
                                <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-slate-500">
                                    <span>${this.escape(this.videoLabel(item.video_key))}</span>
                                    ${item.credential_name ? `<span>账号 ${this.escape(item.credential_name)}</span>` : ''}
                                    ${item.delay_time ? `<span>定时 ${this.escape(this.formatDelayTime(item.delay_time))}</span>` : ''}
                                    ${item.season_id ? `<span>合集 ${this.escape(item.season_id)}</span>` : ''}
                                    ${item.charging_pay?.enabled ? `<span>充电 ${this.escape(item.charging_pay.upower_level_id || '默认档')}</span>` : ''}
                                    <a href="${this.absoluteUrl(`/api/v1/publisher/task/${encodeURIComponent(item.task_id)}`)}" target="_blank" rel="noopener noreferrer" class="font-bold text-indigo-700 hover:text-indigo-900">任务 JSON</a>
                                </div>
                                ${item.error ? `<div class="mt-1 text-rose-600">${this.escape(this.friendlyUploadError(item))}</div>` : ''}
                            </div>
                        `;
                    }).join('')}
                </div>
            </div>
        `;
    }

    friendlyUploadError(item) {
        const error = String(item?.error || '').trim();
        if (!error) return '';
        if (/状态码：?403|HTTP\s*403/i.test(error)) {
            const context = [
                item.credential_name ? `账号：${item.credential_name}` : '',
                item.season_id ? `合集：${item.season_id}` : '',
                item.charging_pay?.enabled ? `充电档：${item.charging_pay.upower_level_id || '默认档'}` : '',
            ].filter(Boolean).join('，');
            const suffix = context ? `（${context}）` : '';
            return `${error} B站拒绝访问：常见原因是账号登录态失效、B站风控，或该账号无权使用当前合集/充电配置${suffix}。请重新登录账号后重试，或先去掉合集/充电配置重试。`;
        }
        return error;
    }

    videoLabel(key) {
        const item = this.videoOptions().find((option) => option.key === key);
        return item?.label || (key === 'vertical' ? '竖屏视频' : '横屏视频');
    }

    statusMeta(status) {
        const normalized = String(status || '').toLowerCase();
        if (['succeeded', 'completed', 'success'].includes(normalized)) return { label: '成功', className: 'bg-emerald-50 text-emerald-700' };
        if (['failed', 'failure'].includes(normalized)) return { label: '失败', className: 'bg-rose-50 text-rose-700' };
        if (['canceled', 'cancelled'].includes(normalized)) return { label: '已取消', className: 'bg-slate-100 text-slate-500' };
        if (['running', 'processing', 'submitting'].includes(normalized)) return { label: '运行中', className: 'bg-blue-50 text-blue-700' };
        return { label: '排队中', className: 'bg-amber-50 text-amber-700' };
    }

    absoluteUrl(path) {
        const raw = String(path || '').trim();
        if (!raw) return '#';
        if (/^https?:\/\//i.test(raw)) return raw;
        return `${this.apiRoot()}${raw}`;
    }

    formatDateTimeLocal(date) {
        const pad = (value) => String(value).padStart(2, '0');
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }

    formatDelayTime(value) {
        const seconds = Number(value);
        if (!Number.isFinite(seconds)) return String(value || '');
        return this.formatDateTime(new Date(seconds * 1000).toISOString());
    }

    formatDateTime(value) {
        if (!value) return '';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
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

    escapeAttr(value) {
        return this.escape(value).replace(/`/g, '&#96;');
    }
}

window.BilibiliPublishPanel = BilibiliPublishPanel;
