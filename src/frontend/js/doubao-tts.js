class DoubaoTTSPage {
    constructor() {
        this.apiBase = this.resolveApiBase();
        this.pollTimer = null;
        this.bind();
        this.checkStatus();
        this.loadHistory();
        this.pollTimer = window.setInterval(() => this.loadHistory(false), 5000);
    }

    resolveApiBase() {
        if (window.API_BASE_URL) {
            return window.API_BASE_URL.replace(/\/api\/v1\/?$/, '/api/v2/tts');
        }
        return `${window.location.protocol}//${window.location.hostname}:5001/api/v2/tts`;
    }

    bind() {
        const speed = document.getElementById('speed');
        const loudness = document.getElementById('loudness');
        const text = document.getElementById('text-input');
        const voicePreset = document.getElementById('voice-preset');
        const voiceType = document.getElementById('voice-type');
        speed.addEventListener('input', () => document.getElementById('speed-label').textContent = `${speed.value}x`);
        loudness.addEventListener('input', () => document.getElementById('loudness-label').textContent = `${loudness.value}x`);
        voicePreset.addEventListener('change', () => {
            if (voicePreset.value !== 'custom') {
                voiceType.value = voicePreset.value;
            }
        });
        voiceType.addEventListener('input', () => {
            const matchedOption = Array.from(voicePreset.options).find((option) => option.value === voiceType.value.trim());
            voicePreset.value = matchedOption ? matchedOption.value : 'custom';
        });
        text.addEventListener('input', () => this.updateCharCount());
        document.getElementById('split-btn').addEventListener('click', () => this.previewSegments());
        document.getElementById('submit-btn').addEventListener('click', () => this.submit());
        document.getElementById('refresh-btn').addEventListener('click', () => this.loadHistory());
        document.querySelectorAll('[data-close-detail]').forEach((node) => {
            node.addEventListener('click', () => this.closeDetail());
        });
    }

    token() {
        return localStorage.getItem('access_token') || localStorage.getItem('token') || '';
    }

    headers() {
        return {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${this.token()}`
        };
    }

    assetUrl(path) {
        if (!path) return '';
        if (/^https?:\/\//i.test(path)) return path;
        return `${this.apiBase.replace(/\/$/, '')}${path.startsWith('/') ? path.slice('/api/v2/tts'.length) : `/${path}`}`;
    }

    async request(path, options = {}) {
        const response = await fetch(`${this.apiBase}${path}`, {
            ...options,
            headers: {
                ...this.headers(),
                ...(options.headers || {})
            }
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || result.success === false) {
            throw new Error(result.message || `HTTP ${response.status}`);
        }
        return result;
    }

    async checkStatus() {
        const badge = document.getElementById('provider-status');
        try {
            const result = await this.request('/status');
            const provider = (result.data.providers || []).find((item) => item.id === 'doubao_ws_v3');
            if (provider && provider.configured) {
                badge.textContent = '豆包 V3 已配置';
                badge.className = 'px-3 py-1.5 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700';
            } else {
                badge.textContent = '缺少 API Key';
                badge.className = 'px-3 py-1.5 rounded-full text-xs font-semibold bg-amber-50 text-amber-700';
            }
        } catch (error) {
            badge.textContent = '配置检查失败';
            badge.className = 'px-3 py-1.5 rounded-full text-xs font-semibold bg-red-50 text-red-700';
        }
    }

    updateCharCount() {
        const value = document.getElementById('text-input').value;
        document.getElementById('char-count').textContent = `${value.length} 字 / ${this.getSegments().length || 0} 段`;
    }

    getSegments() {
        const value = document.getElementById('text-input').value.trim();
        if (!value) return [];
        return value
            .split(/\n\s*\n/g)
            .map((item) => item.trim())
            .filter(Boolean);
    }

    previewSegments() {
        const segments = this.getSegments();
        this.showMessage(`当前会拆成 ${segments.length} 段。`, 'info');
        this.updateCharCount();
    }

    getFormData() {
        const title = document.getElementById('task-title').value.trim();
        const text = document.getElementById('text-input').value.trim();
        const segments = this.getSegments();
        return {
            title: title || text.slice(0, 28) || '豆包语音合成',
            segments,
            config: {
                provider: 'doubao_ws_v3',
                resource_id: document.getElementById('resource-id').value,
                model: document.getElementById('model').value,
                voice_type: document.getElementById('voice-type').value.trim(),
                format: document.getElementById('audio-format').value,
                sample_rate: Number(document.getElementById('sample-rate').value),
                bit_rate: Number(document.getElementById('bit-rate').value),
                speed: Number(document.getElementById('speed').value),
                loudness: Number(document.getElementById('loudness').value),
                emotion: document.getElementById('emotion').value.trim(),
                emotion_scale: Number(document.getElementById('emotion-scale').value),
                enable_subtitle: document.getElementById('enable-subtitle').checked
            }
        };
    }

    async submit() {
        const button = document.getElementById('submit-btn');
        const payload = this.getFormData();
        if (!payload.segments.length) {
            this.showMessage('请输入需要合成的文本。', 'error');
            return;
        }
        if (!payload.config.voice_type) {
            this.showMessage('请填写音色 ID。', 'error');
            return;
        }

        button.disabled = true;
        button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 提交中';
        try {
            await this.request('/submit', {
                method: 'POST',
                body: JSON.stringify(payload)
            });
            this.showMessage('任务已提交，后台会串行生成每段音频。', 'success');
            document.getElementById('text-input').value = '';
            document.getElementById('task-title').value = '';
            this.updateCharCount();
            await this.loadHistory();
        } catch (error) {
            this.showMessage(`提交失败：${error.message}`, 'error');
        } finally {
            button.disabled = false;
            button.innerHTML = '<i class="fas fa-paper-plane"></i> 提交合成任务';
        }
    }

    async loadHistory(showLoading = true) {
        const list = document.getElementById('task-list');
        if (showLoading) {
            list.innerHTML = '<div class="py-12 text-center text-slate-400"><i class="fas fa-spinner fa-spin mr-2"></i>加载任务中</div>';
        }
        try {
            const result = await this.request('/history?per_page=20');
            const items = result.data.items || [];
            if (!items.length) {
                list.innerHTML = '<div class="py-12 text-center text-slate-400 border border-dashed border-slate-200 rounded-xl">暂无任务</div>';
                return;
            }
            list.innerHTML = items.map((item) => this.renderTaskItem(item)).join('');
            list.querySelectorAll('[data-show-detail]').forEach((node) => {
                node.addEventListener('click', () => this.showDetail(node.dataset.taskId));
            });
        } catch (error) {
            if (showLoading) list.innerHTML = `<div class="py-12 text-center text-red-500">加载失败：${this.escape(error.message)}</div>`;
        }
    }

    renderTaskItem(item) {
        const config = item.config || {};
        const status = this.statusText(item.status);
        const firstAudio = item.first_audio_segment || null;
        const audioUrl = firstAudio && firstAudio.audio_url ? this.assetUrl(firstAudio.audio_url) : '';
        const subtitleUrl = firstAudio && firstAudio.subtitle_url ? this.assetUrl(firstAudio.subtitle_url) : '';
        const quickAudio = audioUrl ? `
            <div class="mt-3 flex flex-col gap-2 rounded-lg bg-slate-50 border border-slate-100 p-3">
                <div class="flex items-center justify-between gap-3">
                    <span class="text-xs font-semibold text-slate-500">段落 ${firstAudio.sequence} 试听</span>
                    <div class="flex items-center gap-2">
                        <a href="${audioUrl}" download class="px-3 py-1.5 rounded-lg bg-slate-900 text-white text-xs font-semibold hover:bg-slate-700">
                            <i class="fas fa-download mr-1"></i>音频
                        </a>
                        ${subtitleUrl ? `<a href="${subtitleUrl}" download class="px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-semibold text-slate-700 hover:bg-white">SRT</a>` : ''}
                    </div>
                </div>
                <audio controls class="h-9 w-full" src="${audioUrl}"></audio>
            </div>
        ` : '';
        return `
            <article class="w-full bg-white border border-slate-200 rounded-xl p-4 hover:border-blue-200 hover:shadow-sm transition-all">
                <div class="flex items-start justify-between gap-4">
                    <div class="min-w-0">
                        <div class="font-semibold text-slate-900 truncate">${this.escape(item.title)}</div>
                        <div class="mt-1 text-xs text-slate-500">${this.formatTime(item.created_at)} · ${item.segment_count || 0} 段 · 成功 ${item.success_count || 0} 段 · ${this.escape(config.voice_type || '-')}</div>
                    </div>
                    <div class="shrink-0 flex items-center gap-2">
                        <span class="px-2.5 py-1 rounded-full text-xs font-semibold ${status.className}">${status.label}</span>
                        <button data-show-detail data-task-id="${item.id}" class="px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-semibold text-slate-600 hover:bg-slate-50">
                            详情
                        </button>
                    </div>
                </div>
                ${quickAudio}
            </article>
        `;
    }

    async showDetail(id) {
        const modal = document.getElementById('detail-modal');
        const body = document.getElementById('detail-body');
        modal.classList.remove('hidden');
        body.innerHTML = '<div class="py-12 text-center text-slate-400"><i class="fas fa-spinner fa-spin mr-2"></i>加载详情中</div>';
        try {
            const result = await this.request(`/article/${id}`);
            const article = result.data;
            document.getElementById('detail-title').textContent = article.title;
            document.getElementById('detail-meta').textContent = `${this.statusText(article.status).label} · ${this.formatTime(article.created_at)}`;
            body.innerHTML = (article.segments || []).map((segment) => this.renderSegment(segment)).join('');
        } catch (error) {
            body.innerHTML = `<div class="text-red-500">加载失败：${this.escape(error.message)}</div>`;
        }
    }

    renderSegment(segment) {
        const status = this.statusText(segment.status);
        const audioUrl = this.assetUrl(segment.audio_url || (segment.audio_path ? `/${segment.audio_path}` : ''));
        const subtitleUrl = this.assetUrl(segment.subtitle_url || (segment.subtitle_path ? `/${segment.subtitle_path}` : ''));
        const audio = audioUrl ? `
            <div class="mt-3 flex items-center gap-2">
                <audio controls class="h-9 flex-1" src="${audioUrl}"></audio>
                <a href="${audioUrl}" download class="px-3 py-2 rounded-lg bg-slate-900 text-white text-xs font-semibold">音频</a>
                ${subtitleUrl ? `<a href="${subtitleUrl}" download class="px-3 py-2 rounded-lg border border-slate-200 text-xs font-semibold text-slate-700">SRT</a>` : ''}
            </div>
        ` : '';
        const error = segment.error_log ? `<div class="mt-3 p-3 rounded-lg bg-red-50 text-xs text-red-700 font-mono">${this.escape(segment.error_log)}</div>` : '';
        return `
            <article class="border border-slate-200 rounded-xl p-4">
                <div class="flex items-center justify-between gap-3">
                    <div class="text-sm font-semibold text-slate-600">段落 ${segment.sequence}</div>
                    <span class="px-2.5 py-1 rounded-full text-xs font-semibold ${status.className}">${status.label}</span>
                </div>
                <p class="mt-3 text-sm leading-7 text-slate-700 whitespace-pre-wrap">${this.escape(segment.text)}</p>
                ${audio}
                ${error}
            </article>
        `;
    }

    closeDetail() {
        document.getElementById('detail-modal').classList.add('hidden');
    }

    showMessage(message, type) {
        const node = document.getElementById('form-message');
        const classes = {
            success: 'bg-emerald-50 text-emerald-700',
            error: 'bg-red-50 text-red-700',
            info: 'bg-blue-50 text-blue-700'
        };
        node.className = `text-sm rounded-lg px-3 py-2 ${classes[type] || classes.info}`;
        node.textContent = message;
        node.classList.remove('hidden');
    }

    statusText(status) {
        const map = {
            pending: ['排队中', 'bg-amber-50 text-amber-700'],
            processing: ['处理中', 'bg-blue-50 text-blue-700'],
            completed: ['完成', 'bg-emerald-50 text-emerald-700'],
            failed: ['失败', 'bg-red-50 text-red-700'],
            partial_failed: ['部分失败', 'bg-orange-50 text-orange-700'],
            waiting: ['等待', 'bg-slate-100 text-slate-500'],
            success: ['成功', 'bg-emerald-50 text-emerald-700']
        };
        const value = map[status] || [status || '-', 'bg-slate-100 text-slate-500'];
        return { label: value[0], className: value[1] };
    }

    formatTime(value) {
        if (!value) return '-';
        return new Date(value).toLocaleString('zh-CN', { hour12: false });
    }

    escape(value) {
        return String(value ?? '').replace(/[&<>"']/g, (char) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        }[char]));
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.doubaoTTSPage = new DoubaoTTSPage();
});
