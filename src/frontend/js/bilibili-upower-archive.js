(function () {
    'use strict';
    const section = document.getElementById('bilibili-upower-archive');
    if (!section) return;
    const rows = document.getElementById('bilibili-upower-rows');
    const status = document.getElementById('bilibili-upower-status');
    const info = document.getElementById('bilibili-upower-page-info');
    const prev = document.getElementById('bilibili-upower-prev');
    const next = document.getElementById('bilibili-upower-next');
    const search = document.getElementById('bilibili-upower-search');
    const sort = document.getElementById('bilibili-upower-sort');
    const size = document.getElementById('bilibili-upower-size');
    const direction = document.getElementById('bilibili-upower-direction');
    const totalLabel = document.getElementById('bilibili-upower-total');
    let page = 1;
    let total = 0;
    let sortDir = 'desc';
    let requestId = 0;
    let topicId = null;
    let analysis = null;
    const format = value => value == null ? '—' : Number(value).toLocaleString('zh-CN');
    const money = value => value == null ? '—' : `¥${(Number(value) / 100).toFixed(2)}`;
    const date = value => value ? new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit'
    }).format(new Date(value)) : '—';
    const cell = (row, className, value) => {
        const element = document.createElement('div');
        element.className = className;
        if (value != null) element.textContent = value;
        row.appendChild(element);
        return element;
    };
    async function load() {
        const current = ++requestId;
        status.textContent = '正在加载稿件数据...';
        try {
            const response = await api.get('/bilibili-intelligence/upower-archive-stats', {
                page, page_size: Number(size.value), keyword: search.value.trim(),
                sort_by: sort.value, sort_dir: sortDir, topic_id: topicId
            });
            if (current !== requestId) return;
            if (!response?.success) throw new Error(response?.message || '读取失败');
            const data = response.data || {};
            total = data.total || 0;
            totalLabel.textContent = format(data.overall_count ?? total);
            document.getElementById('bilibili-upower-income').textContent = money(data.overall_income_cents);
            rows.replaceChildren();
            for (const item of data.items || []) {
                const row = document.createElement('article');
                row.className = 'archive-row';
                const main = cell(row, 'archive-main');
                const link = document.createElement('a');
                link.textContent = item.title || String(item.aid);
                link.href = item.video_url || `https://www.bilibili.com/video/av${item.aid}`;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.className = 'archive-title';
                main.appendChild(link);
                cell(main, 'archive-time', date(item.pub_time));
                const level = cell(row, 'archive-level-cell');
                cell(level, 'archive-level', item.level_name || '—');
                cell(row, 'archive-number', format(item.play_count));
                cell(row, 'archive-income', money(item.income));
                rows.appendChild(row);
            }
            if (!rows.children.length) {
                const empty = document.createElement('div');
                empty.className = 'archive-empty';
                empty.textContent = '没有找到符合条件的稿件';
                rows.appendChild(empty);
            }
            status.textContent = `共 ${format(total)} 条 · 最近同步 ${date(data.last_fetched_at)}`;
            info.textContent = `第 ${page} / ${Math.max(1, Math.ceil(total / Number(size.value)))} 页`;
            prev.disabled = page <= 1;
            next.disabled = page * Number(size.value) >= total;
        } catch (error) {
            if (current === requestId) status.textContent = `加载失败：${error.message}`;
        }
    }
    prev.addEventListener('click', () => { if (page > 1) { page--; load(); } });
    next.addEventListener('click', () => { if (page * Number(size.value) < total) { page++; load(); } });
    let timer;
    search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { page = 1; load(); }, 300); });
    size.addEventListener('change', () => { page = 1; load(); });
    sort.addEventListener('change', () => { page = 1; load(); });
    direction.addEventListener('click', () => {
        sortDir = sortDir === 'desc' ? 'asc' : 'desc';
        direction.querySelector('span').textContent = sortDir === 'desc' ? '降序' : '升序';
        direction.querySelector('i').className = sortDir === 'desc' ? 'fas fa-arrow-down-wide-short' : 'fas fa-arrow-up-wide-short';
        direction.setAttribute('aria-label', `切换为${sortDir === 'desc' ? '升序' : '降序'}`);
        page = 1;
        load();
    });
    function selectTopic(topic) {
        topicId = topic?.id ?? null;
        document.querySelectorAll('.topic-tag').forEach(button => {
            const selected = Number(button.dataset.topicId) === topicId;
            button.setAttribute('aria-pressed', String(selected));
        });
        const detail = document.getElementById('topic-detail');
        detail.replaceChildren();
        if (topic) {
            const m = topic.metrics;
            cell(detail, 'topic-detail-title', topic.name);
            cell(detail, 'topic-stats', `${m.count} 条稿件 · 收益 ${money(m.income_cents)} · 贡献 ${(m.income_share * 100).toFixed(1)}% · 单条均值 ${money(m.mean_income_cents)} · 中位数 ${money(m.median_income_cents)}`);
            cell(detail, 'topic-conclusion', topic.conclusion);
            cell(detail, 'topic-concentration', `收益最高的两条稿件占本话题收益 ${(m.top_two_share * 100).toFixed(1)}%`);
        } else if (analysis) {
            cell(detail, 'topic-detail-title', '选题观察');
            cell(detail, 'topic-conclusion', analysis.summary.conclusion);
        }
        page = 1;
        load();
    }
    async function loadTopics() {
        const note = document.getElementById('topic-status');
        try {
            const response = await api.get('/bilibili-intelligence/upower-topic-analysis');
            if (!response?.success) throw new Error('分析读取失败');
            analysis = response.data;
            if (!analysis) { note.textContent = '暂无分析快照'; return; }
            note.textContent = `分析于 ${date(analysis.created_at)} · 基于 ${date(analysis.source_fetched_at)} 的 ${analysis.summary.archive_count} 条稿件${analysis.is_stale ? ' · 稿件数据已更新，以下为历史分析快照' : ''}`;
            const cloud = document.getElementById('topic-cloud');
            cloud.replaceChildren();
            const max = Math.max(1, ...analysis.topics.map(t => t.metrics.income_cents));
            for (const topic of analysis.topics) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'topic-tag';
                button.dataset.topicId = topic.id;
                button.style.fontSize = `${14 + 14 * Math.sqrt(topic.metrics.income_cents / max)}px`;
                button.textContent = topic.name;
                button.title = `${topic.metrics.count} 条 · ${money(topic.metrics.income_cents)} · 收益贡献 ${(topic.metrics.income_share * 100).toFixed(1)}%`;
                button.setAttribute('aria-pressed', 'false');
                button.addEventListener('click', () => selectTopic(topicId === topic.id ? null : topic));
                cloud.appendChild(button);
            }
            document.getElementById('topic-recommendations').textContent = '下一批 20 条选题实验：' + analysis.summary.recommendations.map(r => `${r.topic} ${r.count} 条`).join('；') + '。这是一轮实验建议，并非已验证的最优比例。';
            document.getElementById('topic-method').textContent = analysis.summary.method;
            document.getElementById('topic-caveat').textContent = analysis.summary.caveat;
            const detail = document.getElementById('topic-detail');
            cell(detail, 'topic-detail-title', '选题观察');
            cell(detail, 'topic-conclusion', analysis.summary.conclusion);
        } catch (error) { note.textContent = `话题分析加载失败：${error.message}`; }
    }
    document.getElementById('topic-reset').addEventListener('click', () => selectTopic(null));
    loadTopics();
    load();
})();
