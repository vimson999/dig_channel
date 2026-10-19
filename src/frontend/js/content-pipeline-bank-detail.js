(function () {
    const params = new URLSearchParams(window.location.search);
    const today = shanghaiDate(new Date());
    const validDate = (name) => /^\d{4}-\d{2}-\d{2}$/.test(params.get(name) || '') ? params.get(name) : today;
    const state = {
        bank: (params.get('bank') || '').trim(),
        from: validDate('from'), to: validDate('to'),
        kind: ['pdf', 'mp3'].includes(params.get('kind')) ? params.get('kind') : '',
        period: ['day', 'week', 'month', 'year'].includes(params.get('period')) ? params.get('period') : 'day',
        page: Math.max(1, Number.parseInt(params.get('page'), 10) || 1),
        pageSize: 25, data: null, loading: false, error: '', requestId: 0, controller: null,
    };
    const els = {};

    document.addEventListener('DOMContentLoaded', () => {
        for (const id of ['detail-back', 'detail-title', 'detail-filter-form', 'detail-from', 'detail-to',
            'detail-kind', 'detail-search', 'detail-pdf-total', 'detail-mp3-total', 'detail-total',
            'detail-status', 'detail-table-body', 'detail-page-info', 'detail-prev', 'detail-next']) {
            els[id] = document.getElementById(id);
        }
        els['detail-from'].value = state.from;
        els['detail-to'].value = state.to;
        els['detail-kind'].value = state.kind;
        els['detail-filter-form'].addEventListener('submit', (event) => {
            event.preventDefault();
            applyFilters();
        });
        els['detail-kind'].addEventListener('change', applyFilters);
        els['detail-prev'].addEventListener('click', () => { state.page -= 1; loadDetails(); });
        els['detail-next'].addEventListener('click', () => { state.page += 1; loadDetails(); });
        loadDetails();
    });

    function applyFilters() {
        state.from = els['detail-from'].value;
        state.to = els['detail-to'].value;
        state.kind = els['detail-kind'].value;
        state.page = 1;
        loadDetails();
    }

    async function loadDetails() {
        const requestId = ++state.requestId;
        state.controller?.abort();
        state.error = '';
        state.loading = true;
        render();
        if (!state.bank || !state.from || !state.to || state.from > state.to) {
            state.error = !state.bank ? '请从发布统计中选择机构查看明细。' : '请选择有效日期，开始日期不能晚于结束日期。';
            state.loading = false;
            render();
            return;
        }
        state.controller = new AbortController();
        const query = new URLSearchParams({
            bank: state.bank, from: state.from, to: state.to, kind: state.kind,
            page: String(state.page), page_size: String(state.pageSize),
        });
        try {
            const apiBase = String(window.API_BASE_URL || '/api/v1').replace(/\/$/, '');
            const response = await fetch(`${apiBase}/content-pipeline/web-submissions/files?${query}`, {
                credentials: 'include', signal: state.controller.signal,
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.success === false) throw new Error(payload.message || `HTTP ${response.status}`);
            if (requestId !== state.requestId) return;
            state.data = payload.data;
            state.bank = state.data.investment_bank;
            state.page = state.data.page;
            const locationQuery = new URLSearchParams({
                bank: state.bank, from: state.from, to: state.to, kind: state.kind,
                period: state.period, page: String(state.page),
            });
            window.history.replaceState(null, '', `${window.location.pathname}?${locationQuery}`);
        } catch (error) {
            if (requestId === state.requestId && error.name !== 'AbortError') state.error = error.message || '明细加载失败，请重试。';
        } finally {
            if (requestId === state.requestId) { state.loading = false; render(); }
        }
    }

    function render() {
        const title = state.bank ? `${state.bank} · 发布详情` : '机构发布详情';
        els['detail-title'].textContent = title;
        document.title = `${title} - Shioa`;
        els['detail-back'].href = `content-pipeline-submit.html?${new URLSearchParams({
            from: state.from, to: state.to, period: state.period,
        })}`;
        const data = state.data;
        for (const [id, key] of [['detail-pdf-total', 'pdf'], ['detail-mp3-total', 'mp3'], ['detail-total', 'total']]) {
            els[id].textContent = data ? String(data.totals[key]) : '—';
        }
        els['detail-search'].disabled = state.loading;
        els['detail-prev'].disabled = state.loading || !data || data.page <= 1;
        els['detail-next'].disabled = state.loading || !data || !data.has_more;
        els['detail-page-info'].textContent = data
            ? `第 ${data.page} / ${data.total_pages} 页 · 共 ${data.total_items} 条明细` : '第 1 页';
        els['detail-status'].className = `detail-status${state.error ? ' error' : ''}`;
        els['detail-status'].textContent = state.error || (state.loading ? '正在加载明细…'
            : `已确认发布 ${data?.totals.total || 0} 个文件 · 最新发布在前`);
        if (state.error || state.loading || !data?.items.length) {
            const message = state.error || (state.loading ? '正在加载明细…' : '所选日期和类型下暂无确认发布的文件。');
            els['detail-table-body'].innerHTML = `<tr><td colspan="5" class="empty-state">${escapeHtml(message)}</td></tr>`;
            return;
        }
        els['detail-table-body'].innerHTML = renderFileRows(data.items);
    }

    function renderFileRows(items) {
        return items.map((item) => {
            const url = safePublicationUrl(item.publish_url);
            const name = item.filename || `历史文件名未记录（${Number(item.count)} 个文件）`;
            return `<tr>
                <td class="time-cell">${escapeHtml(shanghaiTime(item.published_at))}</td>
                <td class="filename">${escapeHtml(name)}</td>
                <td class="type-cell"><span class="type-badge">${item.kind === 'mp3' ? 'MP3' : 'PDF'}</span></td>
                <td class="numeric">${Number(item.count || 0)}</td>
                <td>${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">查看发布 ↗</a>` : '<span class="muted">未记录链接</span>'}</td>
            </tr>`;
        }).join('');
    }

    function safePublicationUrl(value) {
        try {
            const url = new URL(value);
            return url.protocol === 'https:' && url.hostname === 'wx.zsxq.com'
                && /^\/dweb2\/index\/topic_detail\/\d+$/.test(url.pathname) ? url.href : '';
        } catch { return ''; }
    }

    function shanghaiDate(date) {
        const parts = new Intl.DateTimeFormat('en', {
            timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
        }).formatToParts(date);
        const part = (type) => parts.find((item) => item.type === type).value;
        return `${part('year')}-${part('month')}-${part('day')}`;
    }

    function shanghaiTime(value) {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '—';
        return new Intl.DateTimeFormat('zh-CN', {
            timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', hour12: false,
        }).format(date);
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (char) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[char]);
    }
})();
