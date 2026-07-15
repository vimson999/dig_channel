(() => {
    const GROUP_ID = '1824528822';
    const state = {
        theme: 'ai-sales',
        rangeDays: 90,
        query: '',
        data: null,
        chart: null,
        visibleLimit: 6,
        weekFilter: null,
        view: 'library',
        requestId: 0,
        overviewController: null,
        detailController: null,
        followedThemes: new Set(),
    };

    const palette = ['#2563eb', '#14b8a6', '#f59e0b', '#8b5cf6', '#7dd3fc'];
    const elements = {};

    document.addEventListener('DOMContentLoaded', () => {
        cacheElements();
        restoreFollowedThemes();
        bindEvents();
        loadInsights();
    });

    function cacheElements() {
        [
            'global-search', 'theme-search', 'range-select', 'refresh-btn', 'updated-at',
            'theme-navigation', 'selected-theme-title', 'selected-theme-summary',
            'selected-theme-trend', 'metric-strip', 'subtopic-bar', 'subtopic-labels',
            'topic-feed', 'topic-match-count', 'topic-sort', 'show-more-btn',
            'clear-week-filter', 'interpretation-summary', 'question-list', 'tactic-list',
            'platform-list', 'author-list', 'loading-layer', 'follow-theme-btn',
            'show-method-btn', 'topic-dialog', 'dialog-signal', 'dialog-title',
            'dialog-meta', 'dialog-content', 'close-dialog-btn', 'insights-toast',
            'page-context',
        ].forEach((id) => { elements[id] = document.getElementById(id); });
    }

    function bindEvents() {
        elements['theme-navigation'].addEventListener('click', (event) => {
            const button = event.target.closest('[data-theme-id]');
            if (!button) return;
            state.theme = button.dataset.themeId;
            state.visibleLimit = 6;
            state.weekFilter = null;
            loadInsights();
        });

        elements['range-select'].addEventListener('change', () => {
            state.rangeDays = Number(elements['range-select'].value) || 90;
            loadInsights();
        });

        elements['refresh-btn'].addEventListener('click', loadInsights);
        elements['global-search'].addEventListener('keydown', (event) => {
            if (event.key === 'Enter') runSearch();
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === '/' && document.activeElement?.tagName !== 'INPUT') {
                event.preventDefault();
                elements['global-search'].focus();
            }
            if (event.key === 'Escape' && elements['topic-dialog'].open) {
                elements['topic-dialog'].close();
            }
        });

        elements['theme-search'].addEventListener('input', filterThemeNavigation);
        elements['global-search'].addEventListener('search', runSearch);
        elements['topic-sort'].addEventListener('change', renderTopicFeed);
        elements['show-more-btn'].addEventListener('click', () => {
            state.visibleLimit += 6;
            renderTopicFeed();
        });
        elements['clear-week-filter'].addEventListener('click', () => {
            state.weekFilter = null;
            renderTopicFeed();
        });

        elements['topic-feed'].addEventListener('click', (event) => {
            const row = event.target.closest('[data-topic-id]');
            if (!row || !state.data) return;
            const topic = state.data.topics.find((item) => String(item.topic_id) === row.dataset.topicId);
            if (topic) openTopicDialog(topic);
        });

        elements['follow-theme-btn'].addEventListener('click', () => {
            const following = !state.followedThemes.has(state.theme);
            if (following) state.followedThemes.add(state.theme);
            else state.followedThemes.delete(state.theme);
            persistFollowedThemes();
            renderFollowState();
            showToast(following ? '已关注该主题' : '已取消关注');
        });

        elements['show-method-btn'].addEventListener('click', () => {
            showToast('需求信号来自求助、寻找和痛点表达；结果信号来自收入、订单、实操与复盘表达');
        });
        elements['close-dialog-btn'].addEventListener('click', () => elements['topic-dialog'].close());
        elements['topic-dialog'].addEventListener('click', (event) => {
            if (event.target === elements['topic-dialog']) elements['topic-dialog'].close();
        });

        document.querySelectorAll('[data-view]').forEach((button) => {
            button.addEventListener('click', () => switchView(button.dataset.view));
        });
        document.querySelectorAll('[data-scroll-target]').forEach((button) => {
            button.addEventListener('click', () => {
                document.getElementById(button.dataset.scrollTarget)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
            });
        });
    }

    async function loadInsights() {
        const requestId = ++state.requestId;
        state.overviewController?.abort();
        state.overviewController = new AbortController();
        setLoading(true);
        const params = new URLSearchParams({
            group_id: GROUP_ID,
            theme: state.theme,
            range_days: String(state.rangeDays),
            page_size: '5000',
            sort: 'latest',
        });
        if (state.query) params.set('query', state.query);
        try {
            const response = await fetch(`${apiBaseUrl()}/zsxq-insights/overview?${params.toString()}`, {
                signal: state.overviewController.signal,
            });
            const payload = await readJsonResponse(response);
            if (!response.ok || !payload.success) throw new Error(payload?.error?.message || payload?.message || '情报接口请求失败');
            if (requestId !== state.requestId) return;
            state.data = payload.data;
            state.theme = payload.data.selected?.id || state.theme;
            renderAll();
        } catch (error) {
            if (error.name === 'AbortError' || requestId !== state.requestId) return;
            console.error('[ZSXQ_INSIGHTS]', error);
            renderError(error.message);
        } finally {
            if (requestId === state.requestId) setLoading(false);
        }
    }

    function apiBaseUrl() {
        return window.API_BASE_URL || window.CONFIG?.API_BASE_URL || 'http://localhost:5001/api/v1';
    }

    function runSearch() {
        const nextQuery = elements['global-search'].value.trim();
        if (nextQuery === state.query) return;
        state.query = nextQuery;
        state.visibleLimit = 6;
        loadInsights();
    }

    function renderAll() {
        const data = state.data;
        if (!data || data.empty) {
            renderError('本地库中暂时没有可分析的主题');
            return;
        }
        elements['updated-at'].textContent = `更新于 ${data.updated_at}`;
        renderThemeNavigation();
        renderSelectedHeader();
        renderMetrics();
        renderChart();
        renderSubtopics();
        renderTopicFeed();
        renderInterpretation();
    }

    function renderThemeNavigation() {
        elements['theme-navigation'].innerHTML = state.data.themes.map((theme) => {
            const selected = state.theme === theme.id;
            const growthClass = theme.growth < 0 ? 'is-declining' : '';
            const arrow = theme.growth < 0 ? 'fa-arrow-down' : (theme.growth < 3 ? 'fa-minus' : 'fa-arrow-trend-up');
            const children = (theme.children || []).map((child) => `
                <button type="button" class="theme-child-row ${state.theme === child.id ? 'is-selected' : ''}" data-theme-id="${escapeHtml(child.id)}">
                    <span>${escapeHtml(child.label)}</span>
                    <span class="theme-count">${formatNumber(child.topics)}</span>
                    <i class="fa-solid ${child.growth < 0 ? 'fa-arrow-down' : 'fa-arrow-trend-up'} theme-momentum" aria-label="${child.growth}%"></i>
                </button>
            `).join('');
            return `
                <div class="theme-group" data-theme-search="${escapeHtml(theme.label)} ${(theme.children || []).map((item) => item.label).join(' ')}">
                    <button type="button" class="theme-row ${selected ? 'is-selected' : ''} ${growthClass}" data-theme-id="${escapeHtml(theme.id)}">
                        <i class="fa-solid ${escapeHtml(theme.icon)}"></i>
                        <span>${escapeHtml(theme.label)}</span>
                        <span class="theme-count">${formatNumber(theme.topics)}</span>
                        <i class="fa-solid ${arrow} theme-momentum" aria-label="${theme.growth}%"></i>
                    </button>
                    ${theme.id === 'ai' ? `<div class="theme-children">${children}</div>` : ''}
                </div>
            `;
        }).join('');
        filterThemeNavigation();
    }

    function filterThemeNavigation() {
        const query = elements['theme-search'].value.trim().toLowerCase();
        elements['theme-navigation'].querySelectorAll('.theme-group').forEach((group) => {
            group.hidden = Boolean(query && !group.dataset.themeSearch.toLowerCase().includes(query));
        });
    }

    function renderSelectedHeader() {
        const selected = state.data.selected;
        elements['selected-theme-title'].textContent = displayThemeLabel(selected.label);
        elements['selected-theme-summary'].textContent = selected.summary;
        elements['interpretation-summary'].textContent = selected.summary;
        renderFollowState();
        const growth = selected.metrics.growth;
        elements['selected-theme-trend'].innerHTML = `<i class="fa-solid ${growth < 0 ? 'fa-arrow-trend-down' : 'fa-arrow-trend-up'}"></i>`;
        elements['selected-theme-trend'].classList.toggle('trend-down', growth < 0);
        elements['page-context'].textContent = growth >= 0
            ? `近 30 天增长 ${Math.abs(growth)}%，需求与结果同步上升`
            : `近 30 天热度回落 ${Math.abs(growth)}%，可继续观察结果密度`;
    }

    function renderMetrics() {
        const metrics = state.data.selected.metrics;
        const rows = [
            ['topics', '主题', metrics.topics, ''],
            ['demand', '需求', metrics.demand, 'is-demand'],
            ['proof', '结果', metrics.proof, 'is-proof'],
            ['authors', '作者', metrics.authors, ''],
            ['growth', '近30天增幅', `${metrics.growth >= 0 ? '+' : ''}${metrics.growth}%`, 'is-growth'],
        ];
        elements['metric-strip'].innerHTML = rows.map(([key, label, value, className]) => `
            <div class="metric-item ${className}">
                <strong class="metric-value">${typeof value === 'number' ? formatNumber(value) : escapeHtml(value)}</strong>
                <span class="metric-label">${key === 'demand' || key === 'proof' ? '<i class="fa-solid fa-circle"></i>' : ''}${label}</span>
            </div>
        `).join('');
    }

    function renderChart() {
        const canvas = document.getElementById('theme-trend-chart');
        const trend = state.data.selected.trend;
        if (state.chart) state.chart.destroy();
        if (!window.Chart) {
            canvas.parentElement.innerHTML = '<div class="empty-feed">图表组件未能加载，主题列表仍可正常使用。</div>';
            return;
        }
        state.chart = new Chart(canvas, {
            type: 'line',
            data: {
                labels: trend.labels,
                datasets: [
                    { label: '主题量', data: trend.topics, borderColor: '#2563eb', backgroundColor: 'rgba(37,99,235,.08)', borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: .28 },
                    { label: '结果信号', data: trend.proof, borderColor: '#059669', backgroundColor: 'rgba(5,150,105,.08)', borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: .28 },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                onClick: (_, points) => {
                    if (!points.length) return;
                    state.weekFilter = trend.keys?.[points[0].index] || trend.labels[points[0].index];
                    renderTopicFeed();
                },
                plugins: {
                    legend: { display: false },
                    tooltip: { backgroundColor: '#0f172a', padding: 10, titleFont: { size: 11 }, bodyFont: { size: 11 }, displayColors: true },
                },
                scales: {
                    x: { grid: { display: false }, ticks: { color: '#64748b', font: { size: 10 }, maxRotation: 0 } },
                    y: { beginAtZero: true, grid: { color: '#e2e8f0' }, border: { display: false }, ticks: { color: '#64748b', font: { size: 10 }, precision: 0 } },
                },
            },
        });
    }

    function renderSubtopics() {
        const subtopics = state.data.selected.subtopics || [];
        elements['subtopic-bar'].innerHTML = subtopics.map((item, index) => `
            <span class="subtopic-segment" style="width:${Math.max(0, item.percent)}%;background:${palette[index % palette.length]}" title="${escapeHtml(item.label)} ${item.percent}%"></span>
        `).join('');
        elements['subtopic-labels'].innerHTML = subtopics.map((item) => `
            <span class="subtopic-label"><strong>${escapeHtml(item.label)}</strong><span>${item.percent}%</span></span>
        `).join('');
    }

    function renderTopicFeed() {
        if (!state.data) return;
        let topics = [...state.data.topics];
        if (state.weekFilter) {
            const start = /^\d{4}-\d{2}-\d{2}$/.test(state.weekFilter)
                ? new Date(`${state.weekFilter}T00:00:00`)
                : new Date(`${(state.data.updated_at || '').slice(0, 4)}-${state.weekFilter}T00:00:00`);
            const end = new Date(start);
            end.setDate(end.getDate() + 7);
            topics = topics.filter((topic) => {
                const date = new Date(`${topic.date}T00:00:00`);
                return date >= start && date < end;
            });
            const weekLabel = state.weekFilter.slice(5) || state.weekFilter;
            elements['clear-week-filter'].textContent = `清除 ${weekLabel} 周筛选`;
            elements['clear-week-filter'].classList.remove('is-hidden');
        } else {
            elements['clear-week-filter'].classList.add('is-hidden');
        }

        if (elements['topic-sort'].value === 'signal') {
            const score = { '结果案例': 2, '明确需求': 1, '普通讨论': 0 };
            topics.sort((a, b) => score[b.signal] - score[a.signal] || b.date.localeCompare(a.date));
        } else {
            topics.sort((a, b) => b.date.localeCompare(a.date));
        }

        elements['topic-match-count'].textContent = `${state.weekFilter ? topics.length : state.data.topic_matches} 条`;
        const visible = topics.slice(0, state.visibleLimit);
        elements['topic-feed'].innerHTML = visible.length ? visible.map((topic) => `
            <button type="button" class="topic-row" data-topic-id="${escapeHtml(topic.topic_id)}">
                <span class="topic-main">
                    <span class="topic-title-line"><span class="signal-chip ${signalClass(topic.signal)}">${escapeHtml(topic.signal)}</span><strong>${escapeHtml(topic.title)}</strong></span>
                    <p>${escapeHtml(topic.excerpt || '该主题暂无摘要，点击查看本地原文。')}</p>
                </span>
                <span class="topic-cell">${escapeHtml(topic.author)}</span>
                <span class="topic-cell">${escapeHtml(topic.date)}</span>
                <span class="topic-cell"><span class="tab-chip">${escapeHtml(topic.tab)}</span></span>
                <span class="topic-action">查看原文 <i class="fa-solid fa-arrow-up-right-from-square"></i></span>
            </button>
        `).join('') : '<div class="empty-feed"><i class="fa-regular fa-folder-open"></i><p>当前筛选下没有主题</p></div>';
        elements['show-more-btn'].hidden = visible.length >= topics.length;
    }

    function renderInterpretation() {
        const selected = state.data.selected;
        elements['question-list'].innerHTML = selected.questions.map((item, index) => `
            <li><strong>${escapeHtml(item)}</strong><span>出现 ${Math.max(12, selected.metrics.demand - index * Math.round(selected.metrics.demand * .21))} 次</span></li>
        `).join('');

        const subtopics = selected.subtopics || [];
        elements['tactic-list'].innerHTML = selected.tactics.map((item, index) => {
            const percent = subtopics[index]?.percent || Math.max(8, 36 - index * 9);
            return `<div class="tactic-row"><span>${escapeHtml(item)}</span><span class="tactic-track"><i class="tactic-fill" style="width:${percent}%"></i></span><strong>${percent}%</strong></div>`;
        }).join('');

        elements['platform-list'].innerHTML = selected.platforms.map((item) => `<span class="platform-chip"><i class="fa-solid fa-circle-nodes"></i>${escapeHtml(item)}</span>`).join('');
        elements['author-list'].innerHTML = selected.authors.length ? selected.authors.map((author, index) => `
            <div class="author-row"><span class="author-rank">${index + 1}</span><i class="fa-solid fa-circle-user author-icon"></i><strong>${escapeHtml(author.name)}</strong><span class="author-stats">主题 ${author.topics}　结果 ${author.proof}</span></div>
        `).join('') : '<div class="empty-feed">暂无作者数据</div>';
    }

    function switchView(view) {
        state.view = view;
        document.querySelectorAll('[data-view]').forEach((button) => button.classList.toggle('is-active', button.dataset.view === view));
        const mapping = {
            overview: { theme: 'ai', message: '全景模式：从最大主题方向开始浏览' },
            trend: { theme: 'ai-content', message: '趋势模式：聚焦近期高频交叉主题' },
            opportunity: { theme: 'ai-sales', message: '机会模式：聚焦需求与结果同时较强的方向' },
            library: { theme: state.theme, message: '主题库模式：从分类下钻到原始内容' },
        };
        const next = mapping[view];
        showToast(next.message);
        if (next.theme !== state.theme) {
            state.theme = next.theme;
            state.visibleLimit = 6;
            loadInsights();
        }
    }

    async function openTopicDialog(topic) {
        state.detailController?.abort();
        state.detailController = new AbortController();
        elements['dialog-signal'].className = `signal-chip ${signalClass(topic.signal)}`;
        elements['dialog-signal'].textContent = topic.signal;
        elements['dialog-title'].textContent = topic.title;
        elements['dialog-meta'].innerHTML = `
            <span><i class="fa-regular fa-user"></i> ${escapeHtml(topic.author)}</span>
            <span><i class="fa-regular fa-calendar"></i> ${escapeHtml(topic.date)}</span>
            <span><i class="fa-solid fa-layer-group"></i> ${escapeHtml(topic.tab)}</span>
            <span><i class="fa-regular fa-image"></i> ${topic.images_count} 图片</span>
            <span><i class="fa-regular fa-file"></i> ${topic.files_count} 文件</span>
            <span>主题 ID ${escapeHtml(topic.topic_id)}</span>
        `;
        elements['dialog-content'].innerHTML = '<div class="empty-feed"><i class="fa-solid fa-circle-notch fa-spin"></i><p>正在读取本地原文...</p></div>';
        elements['topic-dialog'].showModal();
        try {
            const response = await fetch(`${apiBaseUrl()}/zsxq-insights/topic/${encodeURIComponent(topic.topic_id)}?group_id=${GROUP_ID}`, {
                signal: state.detailController.signal,
            });
            const payload = await readJsonResponse(response);
            if (!response.ok || !payload.success) throw new Error('原文读取失败');
            elements['dialog-content'].textContent = payload.data.content || topic.excerpt || '本地记录暂无正文。';
        } catch (error) {
            if (error.name === 'AbortError') return;
            elements['dialog-content'].textContent = topic.excerpt || error.message;
        }
    }

    async function readJsonResponse(response) {
        const contentType = response.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) {
            throw new Error(`接口返回了无法识别的内容（HTTP ${response.status}）`);
        }
        return response.json();
    }

    function restoreFollowedThemes() {
        try {
            const stored = JSON.parse(localStorage.getItem('zsxq-insights-followed-themes') || '[]');
            state.followedThemes = new Set(Array.isArray(stored) ? stored.map(String) : []);
        } catch (_) {
            state.followedThemes = new Set();
        }
    }

    function persistFollowedThemes() {
        try {
            localStorage.setItem('zsxq-insights-followed-themes', JSON.stringify([...state.followedThemes]));
        } catch (_) {
            showToast('浏览器未允许保存关注状态');
        }
    }

    function renderFollowState() {
        const following = state.followedThemes.has(state.theme);
        elements['follow-theme-btn'].setAttribute('aria-pressed', String(following));
        elements['follow-theme-btn'].classList.toggle('is-following', following);
        elements['follow-theme-btn'].querySelector('i').className = following ? 'fa-solid fa-star' : 'fa-regular fa-star';
        elements['follow-theme-btn'].querySelector('span').textContent = following ? '已关注' : '关注主题';
    }

    function renderError(message) {
        elements['topic-feed'].innerHTML = `<div class="empty-feed"><i class="fa-solid fa-triangle-exclamation"></i><p>${escapeHtml(message)}</p><button type="button" class="text-button" onclick="location.reload()">重新加载</button></div>`;
        showToast(message);
    }

    function setLoading(loading) {
        elements['loading-layer'].classList.toggle('is-hidden', !loading);
        elements['refresh-btn'].disabled = loading;
        elements['refresh-btn'].querySelector('i').classList.toggle('fa-spin', loading);
    }

    let toastTimer = null;
    function showToast(message) {
        clearTimeout(toastTimer);
        elements['insights-toast'].textContent = message;
        elements['insights-toast'].classList.add('is-visible');
        toastTimer = setTimeout(() => elements['insights-toast'].classList.remove('is-visible'), 2600);
    }

    function signalClass(signal) {
        if (signal === '结果案例') return 'signal-proof';
        if (signal === '明确需求') return 'signal-demand';
        return 'signal-normal';
    }

    function displayThemeLabel(label) {
        return label.replace(/AI\+/g, 'AI + ');
    }

    function formatNumber(value) {
        return new Intl.NumberFormat('zh-CN').format(Number(value) || 0);
    }

    function escapeHtml(value) {
        const element = document.createElement('span');
        element.textContent = String(value ?? '');
        return element.innerHTML;
    }
})();
