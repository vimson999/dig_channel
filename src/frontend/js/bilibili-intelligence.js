(() => {
    const state = {
        competitors: [],
        videos: [],
        overview: null,
        pagination: { page: 1, pages: 0, total: 0, per_page: 30 },
        selected: new Set(),
        loading: false,
    };
    const elements = {};

    document.addEventListener('DOMContentLoaded', async () => {
        cacheElements();
        bindEvents();
        if (window.pageAccessGuard) await window.pageAccessGuard;
        await loadAll();
    });

    function cacheElements() {
        [
            'metric-grid', 'latest-sync', 'competitor-list', 'video-feed', 'empty-state',
            'feed-summary', 'selected-count', 'match-selected-btn', 'sync-all-btn',
            'match-pending-btn', 'add-competitor-btn', 'refresh-btn', 'query-input',
            'competitor-filter', 'content-type-filter', 'match-status-filter', 'range-filter', 'prev-page',
            'next-page', 'page-label', 'competitor-dialog', 'competitor-form',
            'detail-dialog', 'detail-content', 'loading-layer', 'loading-title',
            'loading-message', 'toast-stack',
        ].forEach((id) => { elements[id] = document.getElementById(id); });
    }

    function bindEvents() {
        elements['sync-all-btn'].addEventListener('click', syncAll);
        elements['match-pending-btn'].addEventListener('click', () => runMatch([]));
        elements['match-selected-btn'].addEventListener('click', () => runMatch([...state.selected]));
        elements['add-competitor-btn'].addEventListener('click', () => elements['competitor-dialog'].showModal());
        elements['refresh-btn'].addEventListener('click', loadAll);
        elements['competitor-form'].addEventListener('submit', createCompetitor);
        elements['query-input'].addEventListener('keydown', (event) => {
            if (event.key === 'Enter') { state.pagination.page = 1; loadVideos(); }
        });
        ['competitor-filter', 'content-type-filter', 'match-status-filter', 'range-filter'].forEach((id) => {
            elements[id].addEventListener('change', () => { state.pagination.page = 1; loadAll(); });
        });
        elements['prev-page'].addEventListener('click', () => changePage(-1));
        elements['next-page'].addEventListener('click', () => changePage(1));
        elements['video-feed'].addEventListener('change', onVideoCheck);
        elements['video-feed'].addEventListener('click', onVideoAction);
        elements['competitor-list'].addEventListener('click', onCompetitorAction);
        document.querySelectorAll('[data-close-dialog]').forEach((button) => button.addEventListener('click', () => elements['competitor-dialog'].close()));
        document.querySelectorAll('[data-close-detail]').forEach((button) => button.addEventListener('click', () => elements['detail-dialog'].close()));
    }

    async function loadAll() {
        try {
            const [competitors, overview] = await Promise.all([
                api('/bilibili-intelligence/competitors'),
                api(`/bilibili-intelligence/overview?range_days=${encodeURIComponent(elements['range-filter'].value)}`),
            ]);
            state.competitors = competitors.data?.items || [];
            state.overview = overview.data || {};
            renderCompetitors();
            renderCompetitorFilter();
            renderOverview();
            await loadVideos();
        } catch (error) {
            showToast(error.message, true);
        }
    }

    async function loadVideos() {
        const params = new URLSearchParams({
            page: String(state.pagination.page),
            per_page: String(state.pagination.per_page),
            range_days: elements['range-filter'].value,
        });
        const contentType = elements['content-type-filter'].value;
        const matchStatus = elements['match-status-filter'].value;
        const competitorId = elements['competitor-filter'].value;
        const query = elements['query-input'].value.trim();
        if (competitorId) params.set('competitor_id', competitorId);
        if (contentType) params.set('content_type', contentType);
        if (matchStatus) params.set('match_status', matchStatus);
        if (query) params.set('q', query);
        try {
            const payload = await api(`/bilibili-intelligence/videos?${params.toString()}`);
            state.videos = payload.data?.items || [];
            state.pagination = payload.data?.pagination || state.pagination;
            state.selected.clear();
            renderVideos();
            renderSelection();
        } catch (error) {
            showToast(error.message, true);
        }
    }

    function renderOverview() {
        const data = state.overview || {};
        const match = data.by_match_status || {};
        const decisions = data.by_decision_status || {};
        const cards = [
            ['fa-satellite-dish', '监测投稿', data.total || 0, `近 ${data.range_days || 30} 天`],
            ['fa-file-pdf', '研报情报', data.by_content_type?.report || 0, 'PDF 对应线索'],
            ['fa-file-audio', '音频情报', data.by_content_type?.audio || 0, 'MP3 对应线索'],
            ['fa-link', '找到对应素材', match.matched || 0, `疑似 ${match.possible || 0} 条`],
            ['fa-bullseye', '值得跟进', decisions.worth_following || 0, `待决策 ${decisions.pending || 0} 条`],
        ];
        elements['metric-grid'].innerHTML = cards.map(([icon, label, value, note]) => `
            <article class="metric-card"><span><i class="fa-solid ${icon}"></i>${label}</span><strong>${formatNumber(value)}</strong><em>${escapeHtml(note)}</em></article>
        `).join('');
        elements['latest-sync'].textContent = data.latest_sync_at ? `更新 ${formatRelative(data.latest_sync_at)}` : '尚未同步';
    }

    function renderCompetitors() {
        if (!state.competitors.length) {
            elements['competitor-list'].innerHTML = '<p class="muted">还没有监控源</p>';
            return;
        }
        elements['competitor-list'].innerHTML = state.competitors.map((item) => `
            <article class="competitor-card">
                <div class="competitor-top"><strong>${escapeHtml(item.name)}</strong><span class="type-tag">${item.content_type === 'report' ? '研报' : '音频'}</span></div>
                <p>UID ${escapeHtml(item.uid)} · ${formatNumber(item.video_count || 0)} 个投稿</p>
                <div class="competitor-meta"><span class="muted">${item.last_error ? '同步异常' : (item.last_synced_at ? formatRelative(item.last_synced_at) : '待同步')}</span><button type="button" data-sync-competitor="${item.id}"><i class="fa-solid fa-rotate"></i> 同步</button></div>
            </article>
        `).join('');
    }

    function renderCompetitorFilter() {
        const select = elements['competitor-filter'];
        const previous = select.value;
        select.innerHTML = '<option value="">全部频道</option>' + state.competitors.map((item) => (
            `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${item.content_type === 'report' ? '研报' : '音频'}</option>`
        )).join('');
        if (state.competitors.some((item) => item.id === previous)) select.value = previous;
    }

    function renderVideos() {
        const total = state.pagination.total || 0;
        elements['feed-summary'].textContent = `共 ${formatNumber(total)} 条情报，本页 ${state.videos.length} 条`;
        elements['empty-state'].classList.toggle('hidden', state.videos.length > 0);
        elements['video-feed'].classList.toggle('hidden', state.videos.length === 0);
        elements['video-feed'].innerHTML = state.videos.map(videoTemplate).join('');
        elements['page-label'].textContent = `第 ${state.pagination.page || 1} / ${state.pagination.pages || 1} 页`;
        elements['prev-page'].disabled = (state.pagination.page || 1) <= 1;
        elements['next-page'].disabled = !state.pagination.pages || state.pagination.page >= state.pagination.pages;
    }

    function videoTemplate(item) {
        const matchLabel = matchStatusLabel(item.match_status);
        const orchestration = orchestrationLabel(item.orchestration_status);
        const decisionClass = item.decision_status === 'worth_following' ? 'decision-worth' : (item.decision_status === 'ignored' ? 'decision-ignored' : '');
        return `
            <article class="video-row" data-video-id="${item.id}">
                <input class="video-check" type="checkbox" value="${item.id}" aria-label="选择 ${escapeHtml(item.title)}">
                <a class="cover-wrap" href="${escapeHtml(item.video_url)}" target="_blank" rel="noopener">
                    ${item.cover_url ? `<img src="${escapeHtml(normalizeUrl(item.cover_url))}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}
                    <span class="duration">${formatDuration(item.duration_seconds)}</span>
                </a>
                <div class="video-main">
                    <h3><a href="${escapeHtml(item.video_url)}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a></h3>
                    <div class="video-meta"><span><i class="fa-regular fa-user"></i> ${escapeHtml(item.competitor_name || '')}</span><span>${item.content_type === 'report' ? '研报' : '音频'}</span><span>${formatRelative(item.published_at)}</span><span><i class="fa-regular fa-circle-play"></i> ${formatNumber(item.play_count || 0)}</span></div>
                </div>
                <div class="match-cell">
                    <span class="status-pill status-${escapeHtml(item.match_status || 'pending')}">${matchLabel}${item.match_confidence != null ? ` · ${Math.round(item.match_confidence * 100)}%` : ''}</span>
                    <p class="local-title">${escapeHtml(item.matched_local_title || '尚未确认本地对应素材')}</p>
                    <div class="match-reason" title="${escapeHtml(item.match_reason || item.match_error || '')}">${escapeHtml(item.match_reason || item.match_error || '等待 AI 语义匹配')}</div>
                </div>
                <div class="workflow-cell">
                    <span class="workflow-status ${decisionClass}">${escapeHtml(orchestration)}${item.decision_status === 'worth_following' ? ' · 已标记值得做' : (item.decision_status === 'ignored' ? ' · 已忽略' : '')}</span>
                    <div class="row-actions">
                        <button class="action-ai" type="button" data-action="match"><i class="fa-solid fa-wand-magic-sparkles"></i> 匹配</button>
                        <button type="button" data-action="detail"><i class="fa-regular fa-rectangle-list"></i> 详情</button>
                        <button class="action-follow" type="button" data-action="worth"><i class="fa-regular fa-star"></i> 值得做</button>
                        <button type="button" data-action="ignore"><i class="fa-regular fa-eye-slash"></i> 忽略</button>
                        ${workflowLink(item)}
                    </div>
                </div>
            </article>
        `;
    }

    function workflowLink(item) {
        if (!item.orchestration_task_id) return '';
        let href = item.content_type === 'audio'
            ? `mp3-video-workflow-detail.html?job_id=${encodeURIComponent(item.orchestration_task_id)}`
            : `report-audio-workflow-detail.html?job_id=${encodeURIComponent(item.orchestration_task_id)}`;
        if (item.matched_local_type === 'report_video_report') href = `report-video-factory.html?task_id=${encodeURIComponent(item.orchestration_task_id)}`;
        return `<a href="${href}" target="_blank"><i class="fa-solid fa-arrow-up-right-from-square"></i> 编排</a>`;
    }

    async function syncAll() {
        setLoading(true, '正在同步 B站投稿', '使用项目现有 B站凭证依次读取监控账号');
        try {
            const result = await api('/bilibili-intelligence/sync', { method: 'POST', body: JSON.stringify({ page_size: 30 }) });
            const data = result.data || {};
            showToast(`同步完成：${data.synced || 0} 个成功，${data.failed || 0} 个失败`);
            await loadAll();
        } catch (error) {
            showToast(error.message, true);
        } finally {
            setLoading(false);
        }
    }

    async function syncOne(id) {
        setLoading(true, '正在同步监控源', '读取该 UP 主的最新投稿');
        try {
            const result = await api(`/bilibili-intelligence/competitors/${encodeURIComponent(id)}/sync`, { method: 'POST', body: JSON.stringify({ page_size: 30 }) });
            showToast(`同步完成：新增 ${result.data?.created || 0} 条，更新 ${result.data?.updated || 0} 条`);
            await loadAll();
        } catch (error) {
            showToast(error.message, true);
        } finally {
            setLoading(false);
        }
    }

    async function runMatch(videoIds) {
        if (videoIds.length > 10) {
            showToast('每批最多匹配 10 条，请分批处理', true);
            return;
        }
        setLoading(true, 'DS v4 flash 正在语义匹配', '代码已完成候选召回，AI 正在判断是否为同一素材');
        try {
            const body = videoIds.length ? { video_ids: videoIds } : { limit: 5, content_type: elements['content-type-filter'].value || undefined };
            const result = await api('/bilibili-intelligence/match', { method: 'POST', body: JSON.stringify(body) });
            showToast(`AI 匹配完成：${result.data?.matched || 0} 条`);
            await loadAll();
        } catch (error) {
            showToast(error.message, true);
        } finally {
            setLoading(false);
        }
    }

    async function createCompetitor(event) {
        event.preventDefault();
        const form = new FormData(elements['competitor-form']);
        const payload = Object.fromEntries(form.entries());
        try {
            await api('/bilibili-intelligence/competitors', { method: 'POST', body: JSON.stringify(payload) });
            elements['competitor-dialog'].close();
            elements['competitor-form'].reset();
            showToast('监控源已添加');
            await loadAll();
        } catch (error) {
            showToast(error.message, true);
        }
    }

    async function decide(videoId, status) {
        try {
            await api(`/bilibili-intelligence/videos/${encodeURIComponent(videoId)}/decision`, { method: 'PATCH', body: JSON.stringify({ status }) });
            showToast(status === 'worth_following' ? '已标记为值得做' : '已忽略该线索');
            await loadAll();
        } catch (error) {
            showToast(error.message, true);
        }
    }

    async function openDetail(videoId) {
        try {
            const payload = await api(`/bilibili-intelligence/videos/${encodeURIComponent(videoId)}`);
            const item = payload.data || {};
            const audit = item.match_payload || {};
            elements['detail-content'].innerHTML = `
                <section class="detail-section"><h3>B站投稿</h3><p>${escapeHtml(item.title || '')}</p><p class="muted">${escapeHtml(item.description || '暂无简介')}</p></section>
                <section class="detail-section"><h3>AI 结论</h3><p><span class="status-pill status-${escapeHtml(item.match_status || 'pending')}">${matchStatusLabel(item.match_status)}</span></p><p>${escapeHtml(item.match_reason || item.match_error || '尚未匹配')}</p></section>
                <section class="detail-section"><h3>支持证据</h3>${renderList(audit.evidence, '暂无支持证据')}</section>
                <section class="detail-section"><h3>冲突与不确定性</h3>${renderList(audit.conflicts, '未记录明显冲突')}</section>
                <section class="detail-section"><h3>代码召回候选</h3><div class="candidate-audit">${(audit.candidates || []).map((candidate) => `<div><span>${escapeHtml(candidate.title || candidate.filename || candidate.key || '')}</span><strong>${Math.round(Number(candidate.rule_score || 0) * 100)}%</strong></div>`).join('') || '<p>暂无候选</p>'}</div></section>
            `;
            elements['detail-dialog'].showModal();
        } catch (error) {
            showToast(error.message, true);
        }
    }

    function renderList(items, emptyText) {
        if (!Array.isArray(items) || !items.length) return `<p>${escapeHtml(emptyText)}</p>`;
        return `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
    }

    function onVideoCheck(event) {
        const checkbox = event.target.closest('.video-check');
        if (!checkbox) return;
        if (checkbox.checked) state.selected.add(checkbox.value);
        else state.selected.delete(checkbox.value);
        renderSelection();
    }

    function onVideoAction(event) {
        const button = event.target.closest('[data-action]');
        if (!button) return;
        const row = button.closest('[data-video-id]');
        if (!row) return;
        const id = row.dataset.videoId;
        if (button.dataset.action === 'match') runMatch([id]);
        if (button.dataset.action === 'detail') openDetail(id);
        if (button.dataset.action === 'worth') decide(id, 'worth_following');
        if (button.dataset.action === 'ignore') decide(id, 'ignored');
    }

    function onCompetitorAction(event) {
        const button = event.target.closest('[data-sync-competitor]');
        if (button) syncOne(button.dataset.syncCompetitor);
    }

    function renderSelection() {
        elements['selected-count'].textContent = `已选 ${state.selected.size} 条`;
        elements['match-selected-btn'].disabled = state.selected.size === 0;
    }

    function changePage(delta) {
        const next = state.pagination.page + delta;
        if (next < 1 || next > state.pagination.pages) return;
        state.pagination.page = next;
        loadVideos();
    }

    async function api(path, options = {}) {
        const token = localStorage.getItem('access_token');
        const response = await fetch(`${apiBase()}${path}`, {
            credentials: 'include',
            ...options,
            headers: {
                Accept: 'application/json',
                ...(options.body ? { 'Content-Type': 'application/json' } : {}),
                ...(token ? { Authorization: `Bearer ${token}` } : {}),
                ...(options.headers || {}),
            },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.success) throw new Error(payload.message || `请求失败（HTTP ${response.status}）`);
        return payload;
    }

    function apiBase() {
        return (window.API_BASE_URL || window.CONFIG?.API_BASE_URL || '/api/v1').replace(/\/$/, '');
    }

    function setLoading(show, title = '', message = '') {
        state.loading = show;
        elements['loading-layer'].classList.toggle('hidden', !show);
        if (title) elements['loading-title'].textContent = title;
        if (message) elements['loading-message'].textContent = message;
    }

    function showToast(message, error = false) {
        const toast = document.createElement('div');
        toast.className = `toast${error ? ' error' : ''}`;
        toast.textContent = message;
        elements['toast-stack'].appendChild(toast);
        setTimeout(() => toast.remove(), 4200);
    }

    function matchStatusLabel(status) {
        return ({
            pending: '待匹配', matched: '对应素材', possible: '疑似对应', related: '仅同主题',
            not_found: '本地未发现', insufficient_evidence: '证据不足', failed: '匹配失败',
        })[status] || '待匹配';
    }

    function orchestrationLabel(status) {
        return ({
            pending: '已投递 · 排队中', processing: '正在编排', completed: '已经编排', failed: '编排失败',
            canceled: '编排已取消', not_orchestrated: '本地有素材 · 尚未编排',
        })[status] || (status ? `编排状态：${status}` : '尚未确认编排状态');
    }

    function formatNumber(value) {
        return new Intl.NumberFormat('zh-CN', { notation: Number(value) >= 10000 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(Number(value || 0));
    }

    function formatDuration(seconds) {
        const value = Number(seconds || 0);
        if (!value) return '--:--';
        const h = Math.floor(value / 3600);
        const m = Math.floor((value % 3600) / 60);
        const s = Math.floor(value % 60);
        return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
    }

    function formatRelative(value) {
        if (!value) return '时间未知';
        const date = new Date(value);
        const delta = Date.now() - date.getTime();
        if (!Number.isFinite(delta)) return String(value);
        const minutes = Math.floor(delta / 60000);
        if (minutes < 1) return '刚刚';
        if (minutes < 60) return `${minutes} 分钟前`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours} 小时前`;
        const days = Math.floor(hours / 24);
        if (days < 30) return `${days} 天前`;
        return date.toLocaleDateString('zh-CN');
    }

    function normalizeUrl(value) {
        const text = String(value || '');
        return text.startsWith('//') ? `https:${text}` : text;
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);
    }
})();
