class ReportAudioWorkflowDetailPage {
    constructor() {
        this.jobId = new URLSearchParams(window.location.search).get('job_id') || '';
        this.job = null;
        this.refreshTimer = null;
        this.autoRefreshMs = 6000;
        this.biliPanel = null;
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
        const activeUpload = (this.job?.bilibili_uploads || []).some((item) => (
            ['queued', 'pending', 'running', 'processing', 'submitting'].includes(String(item?.status || '').toLowerCase())
        ));
        if (!activeJob && !activeUpload) return;
        this.refreshTimer = setTimeout(() => {
            this.refreshTimer = null;
            this.load();
        }, this.autoRefreshMs);
    }

    renderMissingJob() {
        this.els.title.textContent = '缺少任务 ID';
        this.els.subtitle.textContent = '请从研报转语音编排台点击“详情新页”打开。';
        this.els.root.innerHTML = '<div class="rounded-lg border border-amber-200 bg-amber-50 p-6 text-sm font-bold text-amber-800">URL 中没有 job_id。</div>';
    }

    render() {
        const job = this.job || {};
        const title = this.displayTitle(job) || job.job_id || '研报转语音详情';
        this.els.title.textContent = title;
        this.els.subtitle.textContent = [
            job.pdf?.filename || job.request?.pdf?.filename || '',
            job.created_at ? `创建 ${this.formatDateTime(job.created_at)}` : '',
            job.completed_at ? `完成 ${this.formatDateTime(job.completed_at)}` : '',
        ].filter(Boolean).join(' · ');
        this.els.root.innerHTML = [
            this.renderSummary(job),
            this.renderDownloads(job),
            this.renderMedia(job),
            this.renderStructured(job),
            this.renderChildren(job),
            '<div id="report-bilibili-publish-panel"></div>',
            this.renderRaw(job),
        ].join('');
        this.mountBilibiliPanel(job);
    }

    mountBilibiliPanel(job) {
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
                onAfterSubmit: async () => this.load(),
                onAfterCancel: async () => this.load(),
            });
        }
        this.biliPanel.setJob(job);
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
        if (!audioUrl && !videoItems.length && !coverItems.length) return '';
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
            </section>
        `;
    }

    renderStructured(job) {
        const structured = job.structured_result || {};
        const ttsText = job.tts_text || structured.final_script_for_tts || structured.script || '';
        if (!Object.keys(structured).length && !ttsText) return '';
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
                ${ttsText ? `<div class="mt-3 max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-sm leading-7 text-slate-700">${this.escape(ttsText)}</div>` : ''}
            </section>
        `;
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

    defaultBiliCoverKey(job, videoKey) {
        const covers = this.biliCoverOptions(job, videoKey);
        return covers[0]?.key || '';
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
