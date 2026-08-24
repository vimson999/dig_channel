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
        experimentStage: 'all',
        page: 1,
        pageSize: 48,
        sort: 'latest',
        pageLoading: false,
        responseCache: new Map(),
    };
    const RESPONSE_CACHE_TTL_MS = 60_000;
    const SESSION_CACHE_MAX_AGE_MS = 6 * 60 * 60_000;
    const SESSION_CACHE_MAX_ENTRIES = 10;
    const SESSION_CACHE_KEY = 'zsxq-insights-response-cache-v2';
    const viewConfig = {
        overview: { theme: 'ai', sort: 'latest', label: '全景概览', description: '浏览全部 AI 与自动化主题的规模、结构和代表内容' },
        trend: { theme: 'ai-content', sort: 'latest', label: '趋势追踪', description: '聚焦近期升温的 AI 内容生产方向与周度变化' },
        opportunity: { theme: 'ai-sales', sort: 'signal', label: '机会筛选', description: '优先展示需求与结果信号更强的 AI 销售私域方向' },
        library: { theme: null, sort: 'latest', label: '主题下钻', description: '保留当前主题，从分类继续下钻到原始内容' },
    };

    const palette = ['#2563eb', '#14b8a6', '#f59e0b', '#8b5cf6', '#7dd3fc'];
    const experimentScoreGuide = [
        ['来源信号', 15, '精华、中标、收藏与代表样本'],
        ['结果证据', 20, '客户、采用、订单等结果仅作线索'],
        ['过程完整度', 20, '步骤、交付物、复盘与限制条件'],
        ['验证速度', 15, '能否在一到两周得到反馈'],
        ['能力匹配', 15, '复用现有内容、自动化与知识库能力'],
        ['获客路径', 10, '第一批客户是否可触达'],
        ['新鲜度', 5, '近期是否仍有需求与讨论'],
        ['风险扣分', -20, '平台、版权、资金和交付风险'],
    ];
    const experimentProjects = [
        {
            id: 'project-radar',
            rank: 1,
            stage: 'now',
            score: 89,
            title: '垂直项目情报雷达',
            label: '最匹配',
            themeIds: ['tools', 'resources', 'ai-coding'],
            match: '你已经具备本地数据同步、清洗、研究解读和知识库能力，最小实验不需要重新建设技术底座。',
            customer: '正在寻找项目方向的独立开发者、内容创业者和小型业务团队。',
            experiment: '连续两周各发布一期人工加半自动情报简报；每期只保留 10 个机会，并为前三个机会写出最小实验和停止条件。',
            metrics: ['20 名种子用户', '阅读完成率 ≥ 40%', '有效回复 ≥ 5 条', '付费或深聊意向 ≥ 2 个'],
            risks: '“信息很多”不等于“替用户做了判断”；需验证筛选质量、更新频率和用户是否愿意持续付费。',
            stop: '两期后没有获得 3 条以上明确使用反馈，先调整目标人群和输出颗粒度。',
            evidence: [
                { topic_id: '22255282111444581', title: 'BERTopic 分析 971 篇 AI 副业内容', author: '澜·森蒂', date: '2026-06-06', tab: '中标', signal: '结果案例' },
                { topic_id: '14422488485482112', title: 'Vibe Coding 项目雷达案例', author: 'Link', date: '2026-07-02', tab: '风向标', signal: '明确需求' },
            ],
        },
        {
            id: 'knowledge-assets',
            rank: 2,
            stage: 'now',
            score: 86,
            title: '音视频 / 资料到知识资产服务',
            label: '最快交付',
            themeIds: ['ai-content', 'content', 'agent-workflow'],
            match: '直接复用现有音视频自动化、内容提炼和本地知识库能力，交付物可在短周期内被客户检查。',
            customer: '播客主、课程主理人、知识社群和有大量历史内容的中文创作者。',
            experiment: '找一位真实客户，将 5–10 小时音视频或一批历史资料加工为可检索知识库、专题索引、10 个选题和 3 份成稿。',
            metrics: ['首版交付 ≤ 7 天', '人工校对 ≤ 总工时 25%', '客户采用内容 ≥ 3 条', '续做或转介绍意向 ≥ 1 个'],
            risks: '转录准确并不等于内容可用；需重点验证观点归属、事实校对、隐私和客户日常工作流是否真的接得进去。',
            stop: '客户未采用任何产出，或人工校对超过总工时一半时，暂停扩量并重做交付标准。',
            evidence: [
                { topic_id: '55522511854582284', title: '飞书音视频转录 Agent', author: '亦仁', date: '2026-06-03', tab: '精华', signal: '结果案例' },
                { topic_id: '22255482852154551', title: '音频自动整理到本地 Obsidian', author: '子澄', date: '2026-07-13', tab: '最新', signal: '明确需求' },
            ],
        },
        {
            id: 'content-workflow',
            rank: 3,
            stage: 'now',
            score: 82,
            title: '内容团队 AI 工作流小型交付',
            label: '服务切入',
            themeIds: ['ai-sales', 'agent-workflow', 'ai-content'],
            match: '你的优势不是泛泛讲 AI，而是把研究、内容、音视频和自动化连接成一个可验收的具体流程。',
            customer: '3–20 人的内容团队、知识服务团队，以及有稳定素材但生产和复盘效率低的小企业。',
            experiment: '访谈 5 家团队，完成 2 次流程诊断，从一个窄环节交付付费试点，例如竞品周报、播客拆稿或历史内容选题库。',
            metrics: ['有效访谈 5 家', '确认重复痛点 ≥ 3 家', '付费试点 ≥ 1 个', '单次流程节省工时 ≥ 30%'],
            risks: '定制化容易失控；必须限定输入、输出、异常处理和人工责任，避免承诺笼统的“企业 AI 转型”。',
            stop: '5 次访谈没有出现重复痛点，或试点无法量化节省时间时，不继续开发通用方案。',
            evidence: [
                { topic_id: '82255512528118552', title: '从基础服务进入企业 AI 定制需求', author: '悟空', date: '2026-04-07', tab: '精华', signal: '结果案例' },
                { topic_id: '45544582454181488', title: 'AI Native 内容 Agent 工厂实践', author: '坤汀', date: '2026-06-07', tab: '精华', signal: '结果案例' },
            ],
        },
        {
            id: 'vertical-database',
            rank: 4,
            stage: 'later',
            score: 74,
            title: '垂直数据库微工具',
            label: '后续产品化',
            themeIds: ['tools', 'ai-coding'],
            match: '适合把前三类服务中反复出现的数据、筛选和查询需求沉淀成产品，但不应先闭门开发完整 SaaS。',
            customer: '有明确地点、行业、项目或资料筛选需求的垂直兴趣人群和专业从业者。',
            experiment: '先用表格或轻量网页服务 10 名用户；只实现一个高频筛选动作，再根据真实使用记录决定是否开发。',
            metrics: ['目标用户访谈 10 人', '重复查询场景 ≥ 1 个', '一周回访使用 ≥ 3 人', '愿意提供数据或付费 ≥ 2 人'],
            risks: '公开数据清洗成本、数据授权与持续更新常被低估，产品流量也不等于商业需求。',
            stop: '用户只浏览不复访，或没有人愿意提交真实数据时，不进入正式产品开发。',
            evidence: [
                { topic_id: '14422112282255452', title: '垂直兴趣数据库网站案例', author: 'Link', date: '2026-07-24', tab: '中标', signal: '明确需求' },
            ],
        },
    ];
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
            'page-context', 'experiment-workspace', 'experiment-list',
            'experiment-signal-summary', 'experiment-score-guide',
        ].forEach((id) => { elements[id] = document.getElementById(id); });
    }

    function bindEvents() {
        elements['theme-navigation'].addEventListener('click', (event) => {
            const button = event.target.closest('[data-theme-id]');
            if (!button) return;
            state.theme = button.dataset.themeId;
            resetPagination();
            loadInsights();
        });

        elements['range-select'].addEventListener('change', () => {
            state.rangeDays = Number(elements['range-select'].value) || 90;
            resetPagination();
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
        elements['topic-sort'].addEventListener('change', () => {
            state.sort = elements['topic-sort'].value;
            resetPagination();
            loadInsights();
        });
        elements['show-more-btn'].addEventListener('click', () => {
            if (state.visibleLimit < state.data.topics.length) {
                state.visibleLimit += 6;
                renderTopicFeed();
                return;
            }
            if (state.data.pagination?.has_more) loadInsights({ append: true });
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
        document.querySelectorAll('[data-experiment-stage]').forEach((button) => {
            button.addEventListener('click', () => {
                state.experimentStage = button.dataset.experimentStage;
                document.querySelectorAll('[data-experiment-stage]').forEach((item) => {
                    item.classList.toggle('is-active', item === button);
                });
                renderExperiments();
            });
        });
        elements['experiment-list'].addEventListener('click', (event) => {
            const button = event.target.closest('[data-evidence-topic-id]');
            if (!button) return;
            const evidence = experimentProjects
                .flatMap((project) => project.evidence)
                .find((item) => item.topic_id === button.dataset.evidenceTopicId);
            if (evidence) openTopicDialog(evidence);
        });
        document.querySelectorAll('[data-scroll-target]').forEach((button) => {
            button.addEventListener('click', () => {
                document.getElementById(button.dataset.scrollTarget)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
            });
        });
    }

    async function loadInsights({ append = false } = {}) {
        const requestId = ++state.requestId;
        state.overviewController?.abort();
        state.overviewController = new AbortController();
        const page = append ? state.page + 1 : 1;
        const params = new URLSearchParams({
            group_id: GROUP_ID,
            theme: state.theme,
            range_days: String(state.rangeDays),
            page: String(page),
            page_size: String(state.pageSize),
            sort: state.sort,
        });
        if (state.query) params.set('query', state.query);
        const url = `${apiBaseUrl()}/zsxq-insights/overview?${params.toString()}`;
        const cached = getCachedResponse(url);
        const cachedAge = cached ? Date.now() - cached.savedAt : Infinity;
        state.pageLoading = append && !cached;
        if (cached) {
            applyInsightsPayload(cached.payload, { append, page });
            if (!append) setLoading(false);
        } else if (append) {
            renderShowMoreState();
        } else {
            setLoading(true);
        }
        try {
            if (cachedAge < RESPONSE_CACHE_TTL_MS) return;
            const response = await fetch(url, { signal: state.overviewController.signal });
            const payload = await readJsonResponse(response);
            if (!response.ok || !payload.success) throw new Error(payload?.error?.message || payload?.message || '情报接口请求失败');
            storeCachedResponse(url, payload);
            if (requestId !== state.requestId) return;
            applyInsightsPayload(payload, { append, page });
            if (response.headers.get('X-Insights-Cache') === 'STALE') {
                refreshInsightsInBackground(url, { append, page, requestId });
            }
        } catch (error) {
            if (error.name === 'AbortError' || requestId !== state.requestId) return;
            if (cached) {
                console.warn('[ZSXQ_INSIGHTS] 后台刷新失败，继续显示缓存快照', error);
                return;
            }
            console.error('[ZSXQ_INSIGHTS]', error);
            renderError(error.message);
        } finally {
            if (requestId === state.requestId) {
                state.pageLoading = false;
                if (append) renderShowMoreState();
                else setLoading(false);
            }
        }
    }

    function applyInsightsPayload(payload, { append, page }) {
        const previousTopics = append ? (state.data?.topics || []) : [];
        state.data = append
            ? { ...payload.data, topics: mergeTopics(previousTopics, payload.data.topics) }
            : payload.data;
        state.page = page;
        state.theme = payload.data.selected?.id || state.theme;
        if (append) {
            state.visibleLimit = state.data.topics.length;
            renderTopicFeed();
        } else {
            elements['topic-sort'].value = state.sort;
            renderAll();
        }
    }

    async function refreshInsightsInBackground(url, { append, page, requestId }) {
        const separator = url.includes('?') ? '&' : '?';
        try {
            const response = await fetch(`${url}${separator}_refresh=1`);
            const payload = await readJsonResponse(response);
            if (!response.ok || !payload.success) return;
            storeCachedResponse(url, payload);
            if (requestId === state.requestId) {
                applyInsightsPayload(payload, { append, page });
            }
        } catch (error) {
            console.warn('[ZSXQ_INSIGHTS] 静默刷新失败', error);
        }
    }

    function getCachedResponse(url) {
        const memoryCached = state.responseCache.get(url);
        if (memoryCached && Date.now() - memoryCached.savedAt < SESSION_CACHE_MAX_AGE_MS) {
            return memoryCached;
        }
        try {
            const entries = JSON.parse(sessionStorage.getItem(SESSION_CACHE_KEY) || '{}');
            const cached = entries[url];
            if (!cached || Date.now() - cached.savedAt >= SESSION_CACHE_MAX_AGE_MS) return null;
            state.responseCache.set(url, cached);
            return cached;
        } catch (_) {
            return null;
        }
    }

    function storeCachedResponse(url, payload) {
        const cached = { payload, savedAt: Date.now() };
        state.responseCache.set(url, cached);
        try {
            const entries = JSON.parse(sessionStorage.getItem(SESSION_CACHE_KEY) || '{}');
            entries[url] = cached;
            const trimmed = Object.fromEntries(
                Object.entries(entries)
                    .sort((left, right) => right[1].savedAt - left[1].savedAt)
                    .slice(0, SESSION_CACHE_MAX_ENTRIES)
            );
            sessionStorage.setItem(SESSION_CACHE_KEY, JSON.stringify(trimmed));
        } catch (_) {
            // Storage may be unavailable or full; the in-memory cache still works.
        }
    }

    function mergeTopics(existing, incoming) {
        const rows = new Map(existing.map((item) => [String(item.topic_id), item]));
        incoming.forEach((item) => rows.set(String(item.topic_id), item));
        return [...rows.values()];
    }

    function resetPagination() {
        state.page = 1;
        state.visibleLimit = 6;
        state.weekFilter = null;
    }

    function apiBaseUrl() {
        return window.API_BASE_URL || window.CONFIG?.API_BASE_URL || 'http://localhost:5001/api/v1';
    }

    function runSearch() {
        const nextQuery = elements['global-search'].value.trim();
        if (nextQuery === state.query) return;
        state.query = nextQuery;
        resetPagination();
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
        renderExperiments();
        syncViewVisibility();
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
        const mode = viewConfig[state.view] || viewConfig.library;
        const momentum = growth >= 0
            ? `近 30 天增长 ${Math.abs(growth)}%`
            : `近 30 天热度回落 ${Math.abs(growth)}%`;
        elements['page-context'].textContent = `${mode.label} · ${mode.description} · ${momentum}`;
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

        elements['topic-match-count'].textContent = `${state.weekFilter ? topics.length : state.data.topic_matches} 条`;
        const visible = topics.slice(0, state.visibleLimit);
        elements['topic-feed'].innerHTML = visible.length ? visible.map((topic) => `
            <button type="button" class="topic-row" data-topic-id="${escapeHtml(topic.topic_id)}">
                <span class="topic-main">
                    <span class="topic-title-line">
                        <span class="signal-chip ${signalClass(topic.signal)}">${escapeHtml(topic.signal)}</span>
                        ${renderYirenChip(topic)}
                        <strong>${escapeHtml(topic.title)}</strong>
                    </span>
                    <p>${escapeHtml(topic.excerpt || '该主题暂无摘要，点击查看本地原文。')}</p>
                </span>
                <span class="topic-cell">${escapeHtml(topic.author)}</span>
                <span class="topic-cell">${escapeHtml(topic.date)}</span>
                <span class="topic-cell"><span class="tab-chip">${escapeHtml(topic.tab)}</span></span>
                <span class="topic-action">查看原文 <i class="fa-solid fa-arrow-up-right-from-square"></i></span>
            </button>
        `).join('') : '<div class="empty-feed"><i class="fa-regular fa-folder-open"></i><p>当前筛选下没有主题</p></div>';
        renderShowMoreState(visible.length, topics.length);
    }

    function renderShowMoreState(visibleCount = null, loadedCount = null) {
        const button = elements['show-more-btn'];
        const visible = visibleCount ?? Math.min(state.visibleLimit, state.data?.topics?.length || 0);
        const loaded = loadedCount ?? (state.data?.topics?.length || 0);
        const hasBuffered = visible < loaded;
        const hasServerPage = Boolean(state.data?.pagination?.has_more);
        button.hidden = !hasBuffered && !hasServerPage;
        button.disabled = state.pageLoading;
        button.innerHTML = state.pageLoading
            ? '<i class="fa-solid fa-circle-notch fa-spin"></i>正在加载'
            : `${hasBuffered ? '查看更多主题' : '加载下一页'}<i class="fa-solid fa-chevron-down"></i>`;
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

    function renderExperiments() {
        if (!elements['experiment-list']) return;
        const themeMetrics = flattenThemeMetrics();
        const visible = experimentProjects.filter((project) => {
            return state.experimentStage === 'all' || project.stage === state.experimentStage;
        });

        elements['experiment-list'].innerHTML = visible.map((project) => {
            const evidenceMetrics = combineThemeMetrics(project.themeIds, themeMetrics);
            const metricText = evidenceMetrics
                ? `${formatNumber(evidenceMetrics.topics)} 次主题标签命中 · ${formatNumber(evidenceMetrics.demand)} 次需求信号 · ${formatNumber(evidenceMetrics.proof)} 次结果信号（分类可重叠）`
                : '实时主题指标暂不可用';
            const growthText = evidenceMetrics
                ? `${evidenceMetrics.growth >= 0 ? '+' : ''}${evidenceMetrics.growth}%`
                : '--';
            return `
                <article class="experiment-card">
                    <div class="experiment-card-header">
                        <div class="experiment-rank">${project.rank}</div>
                        <div class="experiment-card-title">
                            <div><h5>${escapeHtml(project.title)}</h5><span>${escapeHtml(project.label)}</span></div>
                            <p>${escapeHtml(project.match)}</p>
                        </div>
                        <div class="experiment-score">
                            <strong>${project.score}</strong><span>初版建议</span>
                        </div>
                    </div>
                    <div class="experiment-live-signal">
                        <span><i class="fa-solid fa-database"></i>${metricText}</span>
                        <span class="${evidenceMetrics?.growth < 0 ? 'trend-down' : 'trend-up'}"><i class="fa-solid fa-arrow-trend-up"></i>近 30 天 ${growthText}</span>
                    </div>
                    <div class="experiment-detail-grid">
                        <div><span>目标客户</span><p>${escapeHtml(project.customer)}</p></div>
                        <div><span>最小实验 · 1–2 周</span><p>${escapeHtml(project.experiment)}</p></div>
                    </div>
                    <div class="experiment-detail-grid experiment-detail-grid-bottom">
                        <div>
                            <span>正反馈指标</span>
                            <ul>${project.metrics.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
                        </div>
                        <div>
                            <span>风险与待验证</span>
                            <p>${escapeHtml(project.risks)}</p>
                            <p class="experiment-stop"><i class="fa-solid fa-stop"></i><strong>停止条件：</strong>${escapeHtml(project.stop)}</p>
                        </div>
                    </div>
                    <footer class="experiment-evidence">
                        <span>数据依据</span>
                        <div>${project.evidence.map((item) => `
                            <button type="button" data-evidence-topic-id="${escapeHtml(item.topic_id)}" title="读取本地原文">
                                <i class="fa-regular fa-file-lines"></i>${escapeHtml(item.title)}
                            </button>
                        `).join('')}</div>
                        <small>原帖中的收入、订单或增长均为作者陈述，仅作为需求和结果线索。</small>
                    </footer>
                </article>
            `;
        }).join('');

        elements['experiment-signal-summary'].innerHTML = [
            ['候选实验', experimentProjects.length],
            ['本周可测', experimentProjects.filter((item) => item.stage === 'now').length],
            ['数据更新', state.data?.updated_at || '暂不可用'],
        ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join('');

        elements['experiment-score-guide'].innerHTML = experimentScoreGuide.map(([label, points, description]) => `
            <div class="score-guide-row">
                <span>${escapeHtml(label)}</span>
                <strong class="${points < 0 ? 'is-penalty' : ''}">${points > 0 ? '+' : ''}${points}</strong>
                <small>${escapeHtml(description)}</small>
            </div>
        `).join('');
    }

    function flattenThemeMetrics() {
        if (!state.data?.themes) return new Map();
        const rows = state.data.themes.flatMap((theme) => [theme, ...(theme.children || [])]);
        return new Map(rows.map((item) => [item.id, item]));
    }

    function combineThemeMetrics(themeIds, metrics) {
        const rows = themeIds.map((id) => metrics.get(id)).filter(Boolean);
        if (!rows.length) return null;
        return {
            topics: rows.reduce((sum, item) => sum + Number(item.topics || 0), 0),
            demand: rows.reduce((sum, item) => sum + Number(item.demand || 0), 0),
            proof: rows.reduce((sum, item) => sum + Number(item.proof || 0), 0),
            growth: Math.round(rows.reduce((sum, item) => sum + Number(item.growth || 0), 0) / rows.length),
        };
    }

    function switchView(view) {
        state.view = view;
        document.querySelectorAll('[data-view]').forEach((button) => {
            const active = button.dataset.view === view;
            button.classList.toggle('is-active', active);
            if (active) button.setAttribute('aria-current', 'page');
            else button.removeAttribute('aria-current');
            if (active) button.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
        });
        syncViewVisibility();
        if (view === 'experiment') {
            renderExperiments();
            showToast('项目实验：评分是初版决策建议，不代表收益预测');
            return;
        }
        const next = viewConfig[view];
        const nextTheme = next.theme || state.theme;
        const needsReload = nextTheme !== state.theme || next.sort !== state.sort;
        showToast(`${next.label}：${next.description}`);
        if (needsReload) {
            state.theme = nextTheme;
            state.sort = next.sort;
            resetPagination();
            loadInsights();
        } else if (state.data) {
            renderSelectedHeader();
        }
    }

    function syncViewVisibility() {
        const researchWorkspace = document.querySelector('.research-workspace');
        const experimentMode = state.view === 'experiment';
        researchWorkspace.classList.toggle('is-hidden', experimentMode);
        elements['experiment-workspace'].classList.toggle('is-hidden', !experimentMode);
        if (experimentMode) {
            elements['page-context'].textContent = '将趋势与结果信号收敛为一到两周可验证的项目';
        }
    }

    async function openTopicDialog(topic) {
        state.detailController?.abort();
        state.detailController = new AbortController();
        elements['dialog-signal'].className = `signal-chip ${signalClass(topic.signal)}`;
        elements['dialog-signal'].textContent = topic.signal;
        elements['dialog-title'].textContent = topic.title;
        renderDialogMeta(topic);
        elements['dialog-content'].innerHTML = '<div class="empty-feed"><i class="fa-solid fa-circle-notch fa-spin"></i><p>正在读取本地原文...</p></div>';
        elements['topic-dialog'].showModal();
        try {
            const response = await fetch(`${apiBaseUrl()}/zsxq-insights/topic/${encodeURIComponent(topic.topic_id)}?group_id=${GROUP_ID}`, {
                signal: state.detailController.signal,
            });
            const payload = await readJsonResponse(response);
            if (!response.ok || !payload.success) throw new Error('原文读取失败');
            renderDialogMeta({ ...topic, ...payload.data });
            elements['dialog-content'].innerHTML = renderTopicContent(payload.data.content || topic.excerpt || '本地记录暂无正文。');
        } catch (error) {
            if (error.name === 'AbortError') return;
            elements['dialog-content'].innerHTML = renderTopicContent(topic.excerpt || error.message);
        }
    }

    function renderDialogMeta(topic) {
        const evidence = topic.yiren_evidence;
        const evidenceTitle = evidence?.text
            ? `${topic.yiren_label || '亦仁状态'}：${evidence.text}`
            : (topic.yiren_label || '未见亦仁认可');
        elements['dialog-meta'].innerHTML = `
            <span><i class="fa-regular fa-user"></i> ${escapeHtml(topic.author)}</span>
            <span><i class="fa-regular fa-calendar"></i> ${escapeHtml(topic.date)}</span>
            <span><i class="fa-solid fa-layer-group"></i> ${escapeHtml(topic.tab)}</span>
            <span><i class="fa-regular fa-image"></i> ${topic.images_count || 0} 图片</span>
            <span><i class="fa-regular fa-file"></i> ${topic.files_count || 0} 文件</span>
            <span class="dialog-yiren-status" title="${escapeAttribute(evidenceTitle)}">${renderYirenChip(topic)}</span>
            <span class="dialog-topic-id">主题 ID ${escapeHtml(topic.topic_id)}</span>
        `;
    }

    function renderYirenChip(topic) {
        const status = topic.yiren_recognition || 'not_found';
        const label = topic.yiren_label || '未见亦仁认可';
        const icon = status === 'recognized'
            ? 'fa-solid fa-circle-check'
            : (status === 'responded' ? 'fa-regular fa-comment-dots' : 'fa-regular fa-circle');
        return `<span class="yiren-chip yiren-${escapeHtml(status)}"><i class="${icon}"></i>${escapeHtml(label)}</span>`;
    }

    function renderTopicContent(value) {
        const lines = String(value || '')
            .replace(/\r\n?/g, '\n')
            .split(/\n+/)
            .map((line) => line.trim())
            .filter(Boolean);
        if (!lines.length) return '<p class="rich-empty">本地记录暂无正文。</p>';
        return `<div class="rich-article">${lines.map(renderRichLine).join('')}</div>`;
    }

    function renderRichLine(line) {
        const tokenPattern = /<e\b([^>]*?)(?:\/>|>\s*<\/e>)/gi;
        let cursor = 0;
        let html = '';
        let match;
        while ((match = tokenPattern.exec(line)) !== null) {
            html += escapeHtml(line.slice(cursor, match.index));
            const attrs = match[1] || '';
            const type = readRichAttribute(attrs, 'type');
            const title = decodeRichText(readRichAttribute(attrs, 'title'));
            if (type === 'text_bold') {
                html += `<strong class="rich-emphasis">${escapeHtml(title)}</strong>`;
            } else if (type === 'mention') {
                html += `<span class="rich-mention">${escapeHtml(title)}</span>`;
            } else if (title) {
                html += escapeHtml(title);
            }
            cursor = tokenPattern.lastIndex;
        }
        html += escapeHtml(line.slice(cursor).replace(/<\/?e\b[^>]*>/gi, ''));
        const isHeading = /^<e\b[^>]*type=(["'])text_bold\1[^>]*(?:\/>|>\s*<\/e>)$/i.test(line);
        return `<p class="${isHeading ? 'rich-heading' : ''}">${html || '&nbsp;'}</p>`;
    }

    function readRichAttribute(attrs, name) {
        const match = attrs.match(new RegExp(`${name}=(["'])(.*?)\\1`, 'i'));
        return match?.[2] || '';
    }

    function decodeRichText(value) {
        if (!value) return '';
        try {
            return decodeURIComponent(value);
        } catch (_) {
            return value;
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

    function escapeAttribute(value) {
        return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('"', '&quot;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;');
    }
})();
