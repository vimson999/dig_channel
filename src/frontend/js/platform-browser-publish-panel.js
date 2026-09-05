class PlatformBrowserPublishPanel {
    constructor(options = {}) {
        this.options = options;
        this.container = options.container;
        this.job = null; this.credentials = []; this.uploads = [];
        this.uploadStatuses = new Map(); this.uploadStatusInitialized = false;
        this.credentialsLoading = false; this.uploadsLoading = false;
        this.localNotice = null; this.formDraft = null; this.pollTimer = null;
        this.bind();
    }
    bind() {
        this.container?.addEventListener('submit', (event) => {
            const form = event.target.closest('[data-platform-publish-form]');
            if (!form) return; event.preventDefault(); this.submit(form);
        });
        this.container?.addEventListener('click', (event) => {
            const button = event.target.closest('[data-platform-publish-cancel]');
            if (!button) return; event.preventDefault();
            this.cancel(button.dataset.platformPublishCancel, button);
        });
        this.container?.addEventListener('change', (event) => {
            if (event.target.matches('[data-platform-publish-field="video"]')) this.applyVideoTitle(event.target.value);
            if (event.target.matches('[data-platform-publish-field="submit"]')) this.updateSubmitButton(event.target.checked);
        });
    }
    destroy() { if (this.pollTimer) window.clearTimeout(this.pollTimer); this.pollTimer = null; }
    setJob(job, { formDraft = null } = {}) {
        const previousJobId = this.jobId();
        const nextJobId = String(job?.job_id || job?.id || '');
        if (previousJobId !== nextJobId) {
            this.uploads = [];
            this.uploadStatuses = new Map();
            this.uploadStatusInitialized = false;
        }
        this.job = job || {}; this.formDraft = formDraft || this.formDraft; this.render();
        if (!this.credentials.length && !this.credentialsLoading) this.loadCredentials();
        this.loadUploads();
    }
    jobId() { return String(this.job?.job_id || this.job?.id || ''); }
    apiRoot() { return typeof this.options.apiRoot === 'function' ? this.options.apiRoot() : ''; }
    platformName() { return this.options.platformName || '平台'; }
    async request(path, options = {}) {
        if (this.options.request) return this.options.request(path, options);
        const response = await fetch(`${this.apiRoot()}${path}`, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.success === false) throw new Error(payload.message || `HTTP ${response.status}`);
        return payload;
    }
    async loadCredentials() {
        this.credentialsLoading = true; this.render();
        try {
            const query = new URLSearchParams({
                platform: this.options.credentialPlatform,
                purpose: this.options.credentialPurpose,
                status: 'active', per_page: '100',
            });
            const result = await this.request(`/api/v1/platform-credentials?${query}`, { timeoutMs: 15000 });
            this.credentials = result.data?.items || result.items || [];
        } catch (error) { this.notify(`${this.platformName()}账号读取失败：${error.message}`, 'warning'); }
        finally { this.credentialsLoading = false; this.render(); }
    }
    async loadUploads({ quiet = true } = {}) {
        const jobId = this.jobId();
        if (!jobId || !this.options.listPath || this.uploadsLoading) return;
        this.uploadsLoading = true;
        try {
            const result = await this.request(this.options.listPath(jobId), { timeoutMs: 15000 });
            this.uploads = result.data?.items || result.items || [];
            this.observeUploadStatuses({ initial: !this.uploadStatusInitialized });
            this.render();
        } catch (error) { if (!quiet) this.notify(`${this.platformName()}任务读取失败：${error.message}`, 'warning'); }
        finally { this.uploadsLoading = false; this.schedulePoll(); }
    }
    schedulePoll() {
        if (this.pollTimer) window.clearTimeout(this.pollTimer); this.pollTimer = null;
        if (!this.uploads.some((item) => ['queued','running','pending','processing'].includes(String(item.status || '').toLowerCase()))) return;
        this.pollTimer = window.setTimeout(() => this.loadUploads(), 3000);
    }
    isSuccessfulUpload(item) {
        const status = String(item?.status || '').trim().toLowerCase();
        const publishStatus = String(item?.publish_status || '').trim().toLowerCase();
        return ['succeeded', 'completed', 'success', 'prepared'].includes(status)
            || ['submitted', 'submitted_pending_verification', 'visible'].includes(publishStatus);
    }
    observeUploadStatuses({ initial = false } = {}) {
        const nextStatuses = new Map();
        this.uploads.forEach((item) => {
            const taskId = String(item?.task_id || item?.job_id || '').trim();
            if (!taskId) return;
            const successful = this.isSuccessfulUpload(item);
            const previous = this.uploadStatuses.get(taskId);
            if (!initial && successful && !previous?.successful) {
                window.UploadSuccessSound?.play({ platform: this.platformName(), taskId });
            }
            nextStatuses.set(taskId, {
                successful,
                status: String(item?.status || '').trim().toLowerCase(),
                publishStatus: String(item?.publish_status || '').trim().toLowerCase(),
            });
        });
        this.uploadStatuses = nextStatuses;
        this.uploadStatusInitialized = true;
    }
    videoOptions() {
        const raw = this.options.videoOptions ? this.options.videoOptions(this.job) : [];
        return (Array.isArray(raw) ? raw : []).filter((item) => item?.key).sort((a,b) => a.key === 'vertical' ? -1 : (b.key === 'vertical' ? 1 : 0));
    }
    defaultTitle(videoKey) {
        return String(this.options.defaultTitle ? this.options.defaultTitle(this.job, videoKey) : '').trim().slice(0, 100);
    }
    readFormDraft() {
        const form = this.container?.querySelector('[data-platform-publish-form]');
        if (!form) return this.formDraft;
        const data = Object.fromEntries(new FormData(form).entries());
        data.submit = form.querySelector('[name="submit"]')?.checked ? '1' : '0';
        return data;
    }
    render() {
        if (!this.container) return;
        const currentDraft = this.readFormDraft(); if (currentDraft) this.formDraft = currentDraft;
        const videos = this.videoOptions();
        const selectedVideoKey = this.formDraft?.video_key && videos.some((item) => item.key === this.formDraft.video_key) ? this.formDraft.video_key : (videos[0]?.key || '');
        const selectedCredential = this.formDraft?.credential_id || this.credentials[0]?.id || '';
        const title = this.formDraft?.title ?? this.defaultTitle(selectedVideoKey);
        const disabled = !videos.length || !this.credentials.length;
        const submitChecked = this.formDraft?.submit === '1';
        const visibility = this.formDraft?.visibility || this.options.defaultVisibility || 'private';
        const declarations = this.options.declarationOptions || [];
        this.container.innerHTML = `<section class="rounded-lg border ${this.options.borderClass || 'border-slate-200'} bg-white p-4 shadow-sm">
            <div class="mb-3 flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between"><div><div class="text-sm font-black text-slate-900"><i class="${this.options.iconClass || 'fas fa-cloud-arrow-up'} mr-1.5 ${this.options.iconColorClass || ''}"></i>${this.escape(this.options.panelTitle || `${this.platformName()}发布`)}</div><div class="mt-1 text-xs text-slate-500">${this.escape(this.options.panelDescription || '复用当前视频成品；默认仅自己可见，不勾选时不会点击发布。')}</div></div><a href="platform-credentials.html" target="_blank" rel="noopener noreferrer" class="self-start rounded-md border border-slate-300 px-2.5 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50"><i class="fas fa-user-gear mr-1"></i>账号管理</a></div>
            <div class="${this.localNotice ? '' : 'hidden'} mb-3 rounded-md border px-3 py-2 text-xs font-bold ${this.noticeClass(this.localNotice?.type)}">${this.escape(this.localNotice?.message || '')}</div>
            ${this.credentialsLoading ? `<div class="mb-3 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-500"><i class="fas fa-spinner fa-spin mr-1"></i>正在读取${this.escape(this.platformName())}账号...</div>` : ''}
            ${!videos.length ? `<div class="mb-3 rounded-md border border-dashed border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-500">${this.escape(this.options.emptyDescription || '当前任务还没有可投递的视频。')}</div>` : ''}
            ${!this.credentials.length && !this.credentialsLoading ? `<div class="mb-3 rounded-md border border-dashed border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-800">暂无用途为 ${this.escape(this.options.credentialPurpose)} 的可用账号。</div>` : ''}
            <form data-platform-publish-form class="space-y-3"><div class="grid grid-cols-1 gap-3 lg:grid-cols-3">
                ${this.selectField('账号', 'credential_id', this.credentials.map((item) => ({value:item.id,label:item.owner_label||item.name||item.id})), selectedCredential, disabled)}
                ${this.selectField('视频', 'video_key', videos.map((item) => ({value:item.key,label:item.label||item.key})), selectedVideoKey, disabled, 'data-platform-publish-field="video"')}
                ${this.selectField('谁可以看', 'visibility', this.options.visibilityOptions || [{value:'private',label:'仅自己可见'},{value:'public',label:'公开'}], visibility, disabled)}
            </div>
            <label class="block"><span class="text-[11px] font-black uppercase text-slate-400">标题</span><input name="title" minlength="${Number(this.options.titleMinChars || 1)}" maxlength="100" value="${this.escapeAttr(title)}" class="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-bold" ${disabled ? 'disabled' : ''}></label>
            <div class="grid grid-cols-1 gap-3 ${declarations.length ? 'lg:grid-cols-[1fr_240px]' : 'lg:grid-cols-[1fr_240px]'}"><label><span class="text-[11px] font-black uppercase text-slate-400">简介</span><textarea name="description" maxlength="${Number(this.options.descriptionMaxLength || 3000)}" rows="3" class="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm" ${disabled ? 'disabled' : ''}>${this.escape(this.formDraft?.description || '')}</textarea></label><div class="space-y-3"><label class="block"><span class="text-[11px] font-black uppercase text-slate-400">话题</span><input name="tags" value="${this.escapeAttr(this.formDraft?.tags || this.options.defaultTags || '')}" class="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-bold" ${disabled ? 'disabled' : ''}></label>${declarations.length ? this.selectField('自主声明','declaration',declarations,this.formDraft?.declaration||this.options.defaultDeclaration||declarations[0].value,disabled) : ''}</div></div>
            <label class="flex items-start gap-3 rounded-md border border-amber-200 bg-amber-50 p-3"><input data-platform-publish-field="submit" name="submit" type="checkbox" value="1" class="mt-0.5 h-4 w-4" ${submitChecked ? 'checked' : ''} ${disabled ? 'disabled' : ''}><span><strong class="block text-sm text-amber-900">${this.escape(this.options.confirmLabel || '确认点击发布')}</strong><span class="mt-0.5 block text-xs text-amber-700">${this.escape(this.options.safeModeDescription || '不勾选时只验证上传、文案和权限，不点击发布。')}</span></span></label>
            <button data-platform-publish-submit type="submit" class="w-full rounded-md ${this.options.buttonClass || 'bg-slate-950'} px-4 py-2.5 text-sm font-black text-white disabled:opacity-50" ${disabled ? 'disabled' : ''}>${this.submitButtonText(submitChecked)}</button></form>${this.renderUploads()}</section>`;
    }
    selectField(label, name, items, selected, disabled, attrs='') {
        const options = items.length ? items.map((item) => `<option value="${this.escapeAttr(item.value)}" ${String(item.value)===String(selected)?'selected':''}>${this.escape(item.label)}</option>`).join('') : '<option>暂无可用选项</option>';
        return `<label><span class="text-[11px] font-black uppercase text-slate-400">${this.escape(label)}</span><select ${attrs} name="${this.escapeAttr(name)}" class="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-bold" ${disabled?'disabled':''}>${options}</select></label>`;
    }
    renderUploads() {
        if (!this.uploads.length) return '';
        return `<div class="mt-4 border-t border-slate-200 pt-3"><div class="mb-2 text-xs font-black text-slate-500">最近${this.escape(this.platformName())}任务</div><div class="space-y-2">${this.uploads.map((item) => `<div class="rounded-md border border-slate-200 bg-slate-50 px-3 py-2"><div class="flex items-center justify-between gap-3"><div class="min-w-0"><div class="truncate text-xs font-bold text-slate-800">${this.escape(item.title||item.task_id)}</div><div class="mt-1 text-[11px] text-slate-500">${this.escape(this.statusLabel(item))} · ${this.escape(this.visibilityLabel(item.visibility))} · ${Number(item.progress||0)}%</div></div>${item.status==='queued'?`<button data-platform-publish-cancel="${this.escapeAttr(item.task_id)}" class="rounded border border-rose-200 px-2 py-1 text-[11px] font-bold text-rose-600">取消</button>`:''}</div>${item.error||item.message?`<div class="mt-1 text-[11px] ${item.error?'text-rose-600':'text-slate-500'}">${this.escape(item.error||item.message)}</div>`:''}</div>`).join('')}</div></div>`;
    }
    async submit(form) {
        const draft = Object.fromEntries(new FormData(form).entries());
        const realSubmit = form.querySelector('[name="submit"]')?.checked === true;
        if (realSubmit && !window.confirm(`确认将“${draft.title || '当前视频'}”提交到${this.platformName()}？可见范围：${this.visibilityLabel(draft.visibility)}`)) return;
        draft.submit = realSubmit; if (realSubmit) draft.publish_confirmation = 'PUBLISH';
        const button = form.querySelector('[data-platform-publish-submit]');
        if (button) { button.disabled = true; button.textContent = '正在投递...'; }
        try {
            const result = await this.request(this.options.uploadPath(this.jobId()), {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(draft),timeoutMs:30000});
            this.notify(result.message || `${this.platformName()}任务已投递`, 'success'); await this.loadUploads({quiet:false});
        } catch (error) { this.notify(error.message, 'error'); this.render(); }
    }
    async cancel(taskId, button) {
        if (button) button.disabled = true;
        try { const result=await this.request(this.options.cancelPath(this.jobId(),taskId),{method:'POST',timeoutMs:15000});this.notify(result.message||'任务已取消','success');await this.loadUploads({quiet:false}); }
        catch (error) { this.notify(error.message,'error'); }
    }
    applyVideoTitle(videoKey) { const input=this.container?.querySelector('[name="title"]');if(input)input.value=this.defaultTitle(videoKey); }
    updateSubmitButton(checked) { const button=this.container?.querySelector('[data-platform-publish-submit]');if(button)button.textContent=this.submitButtonText(checked); }
    submitButtonText(real) { return real ? (this.options.realSubmitLabel || `投递${this.platformName()}真实发布`) : (this.options.safeSubmitLabel || `开始${this.platformName()}安全测试`); }
    notify(message,type='info'){this.localNotice={message:String(message||''),type};this.options.onNotice?.(message,type);this.render();}
    noticeClass(type){return type==='error'?'border-rose-200 bg-rose-50 text-rose-700':type==='warning'?'border-amber-200 bg-amber-50 text-amber-800':type==='success'?'border-emerald-200 bg-emerald-50 text-emerald-700':'border-sky-200 bg-sky-50 text-sky-700';}
    visibilityLabel(value){const item=(this.options.visibilityOptions||[]).find((option)=>option.value===value);return item?.label||'仅自己可见';}
    statusLabel(item){if(item.publish_status==='visible')return'已发布·已回查';if(item.publish_status==='submitted_pending_verification')return'已提交·需人工确认';if(item.publish_status==='submitted')return'已提交·待确认';if(item.publish_status==='prepared')return`测试通过·未${this.options.publishVerb||'发布'}`;return({queued:'排队中',running:'运行中',succeeded:'已完成',failed:'失败',canceled:'已取消'})[item.status]||item.status||'未知';}
    escape(value){return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
    escapeAttr(value){return this.escape(value);}
}

const DOUYIN_PANEL_DEFAULTS = {
    platformName:'抖音', credentialPlatform:'douyin', credentialPurpose:'douyin.publish',
    panelTitle:'抖音发布', iconClass:'fab fa-tiktok',
    visibilityOptions:[{value:'private',label:'仅自己可见'},{value:'friends',label:'好友可见'},{value:'public',label:'公开'}],
    declarationOptions:[{value:'personal_opinion',label:'内容为个人观点或见解'},{value:'ai_generated',label:'内容由 AI 生成'},{value:'reposted',label:'内容为转载信息'},{value:'marketing',label:'内容含营销推广信息'},{value:'fictional',label:'虚构演绎，仅供娱乐'},{value:'none',label:'无需添加自主声明'}],
    defaultDeclaration:'none', publishVerb:'发布',
};
const WECHAT_CHANNELS_PANEL_DEFAULTS = {
    platformName:'视频号', credentialPlatform:'wechat_channels', credentialPurpose:'wechat_channels.publish',
    panelTitle:'视频号投递', iconClass:'fab fa-weixin', iconColorClass:'text-emerald-600',
    borderClass:'border-emerald-200', buttonClass:'bg-emerald-700', titleMinChars:6,
    descriptionMaxLength:1000, confirmLabel:'确认点击发表', publishVerb:'发表',
    realSubmitLabel:'投递视频号真实发表', safeSubmitLabel:'开始视频号安全测试',
    visibilityOptions:[{value:'private',label:'仅自己可见'},{value:'public',label:'公开'}],
};

window.PlatformBrowserPublishPanel = PlatformBrowserPublishPanel;
window.DouyinPublishPanel = class extends PlatformBrowserPublishPanel { constructor(options={}) { super({...DOUYIN_PANEL_DEFAULTS,...options}); } };
window.WechatChannelsPublishPanel = class extends PlatformBrowserPublishPanel { constructor(options={}) { super({...WECHAT_CHANNELS_PANEL_DEFAULTS,...options}); } };

const PLATFORM_PUBLISH_TAB_ITEMS = [
    { key: 'bilibili', label: 'B站', icon: 'fab fa-bilibili' },
    { key: 'douyin', label: '抖音', icon: 'fab fa-tiktok' },
    { key: 'wechat_channels', label: '视频号', icon: 'fab fa-weixin' },
];

window.renderPlatformPublishTabs = function renderPlatformPublishTabs(options = {}) {
    const prefix = String(options.prefix || 'publish');
    const activeKey = PLATFORM_PUBLISH_TAB_ITEMS.some((item) => item.key === options.activeKey)
        ? options.activeKey
        : 'bilibili';
    const panelIds = options.panelIds || {};
    const tabs = PLATFORM_PUBLISH_TAB_ITEMS.map((item) => {
        const selected = item.key === activeKey;
        return `<button type="button" role="tab" id="${prefix}-publish-tab-${item.key}" data-publish-platform-tab="${item.key}" aria-controls="${prefix}-publish-panel-${item.key}" aria-selected="${selected ? 'true' : 'false'}" tabindex="${selected ? '0' : '-1'}" class="flex min-h-11 items-center justify-center gap-2 rounded-lg px-3 py-2.5 text-sm font-black transition focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-1 ${selected ? 'bg-white text-indigo-700 shadow-sm ring-1 ring-slate-200' : 'text-slate-500 hover:bg-white/70 hover:text-slate-800'}"><i class="${item.icon}" aria-hidden="true"></i><span>${item.label}</span></button>`;
    }).join('');
    const panels = PLATFORM_PUBLISH_TAB_ITEMS.map((item) => {
        const selected = item.key === activeKey;
        const panelId = String(panelIds[item.key] || `${prefix}-${item.key}-publish-panel`);
        return `<div role="tabpanel" id="${prefix}-publish-panel-${item.key}" data-publish-platform-panel="${item.key}" aria-labelledby="${prefix}-publish-tab-${item.key}" ${selected ? '' : 'hidden'}><div id="${panelId}"></div></div>`;
    }).join('');
    return `<section data-platform-publish-tabs="${prefix}" class="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm"><div class="border-b border-slate-200 px-4 py-3"><div class="text-sm font-black text-slate-900">发布中心</div><div class="mt-1 text-xs text-slate-500">在同一区域切换发布平台，已填写的内容会保留。</div></div><div class="border-b border-slate-200 bg-slate-50 p-2"><div role="tablist" aria-label="发布平台" class="grid grid-cols-3 gap-2">${tabs}</div></div><div class="platform-publish-tab-body p-4">${panels}</div></section>`;
};

window.PlatformPublishTabs = class PlatformPublishTabs {
    constructor(options = {}) {
        this.container = options.container;
        this.activeKey = options.activeKey || 'bilibili';
        this.onChange = options.onChange;
        this.handleClick = (event) => {
            const button = event.target.closest('[data-publish-platform-tab]');
            if (button && this.container?.contains(button)) this.activate(button.dataset.publishPlatformTab, { focus: false });
        };
        this.handleKeydown = (event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            const buttons = this.buttons();
            const currentIndex = buttons.indexOf(event.target.closest('[data-publish-platform-tab]'));
            if (currentIndex < 0) return;
            event.preventDefault();
            let nextIndex = event.key === 'Home' ? 0 : (event.key === 'End' ? buttons.length - 1 : currentIndex + (event.key === 'ArrowRight' ? 1 : -1));
            nextIndex = (nextIndex + buttons.length) % buttons.length;
            this.activate(buttons[nextIndex].dataset.publishPlatformTab, { focus: true });
        };
        this.container?.addEventListener('click', this.handleClick);
        this.container?.addEventListener('keydown', this.handleKeydown);
        this.activate(this.activeKey, { focus: false, notify: false });
    }

    buttons() {
        return Array.from(this.container?.querySelectorAll('[data-publish-platform-tab]') || []);
    }

    activate(key, options = {}) {
        const nextKey = PLATFORM_PUBLISH_TAB_ITEMS.some((item) => item.key === key) ? key : 'bilibili';
        this.activeKey = nextKey;
        this.buttons().forEach((button) => {
            const selected = button.dataset.publishPlatformTab === nextKey;
            button.setAttribute('aria-selected', selected ? 'true' : 'false');
            button.tabIndex = selected ? 0 : -1;
            button.classList.toggle('bg-white', selected);
            button.classList.toggle('text-indigo-700', selected);
            button.classList.toggle('shadow-sm', selected);
            button.classList.toggle('ring-1', selected);
            button.classList.toggle('ring-slate-200', selected);
            button.classList.toggle('text-slate-500', !selected);
            button.classList.toggle('hover:bg-white/70', !selected);
            button.classList.toggle('hover:text-slate-800', !selected);
            if (selected && options.focus) button.focus();
        });
        this.container?.querySelectorAll('[data-publish-platform-panel]').forEach((panel) => {
            panel.hidden = panel.dataset.publishPlatformPanel !== nextKey;
        });
        if (options.notify !== false) this.onChange?.(nextKey);
    }

    destroy() {
        this.container?.removeEventListener('click', this.handleClick);
        this.container?.removeEventListener('keydown', this.handleKeydown);
    }
};
