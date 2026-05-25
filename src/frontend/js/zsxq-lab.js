const COOKIE_STORAGE_KEY = 'zsxq_cookie';

const state = {
    cookie: localStorage.getItem(COOKIE_STORAGE_KEY) || sessionStorage.getItem('zsxq_cookie') || '',
    selectedGroup: null,
    groups: [],
    fileMap: new Map(),
    topicMap: new Map(),
    selectedTopicKeys: new Set(),
    currentTopics: [],
    activeTab: 'latest',
    hashtagTabs: [],
    tabStates: new Map(),
    lastTopicsResponse: null,
    lastTopicsRequest: null,
    lastTopicsScope: 'all',
    isLoadingMore: false,
    lastTopicRequestAt: 0,
    topicCooldownUntil: 0,
};

const els = {
    cookieInput: document.getElementById('cookie-input'),
    cookieFormatHint: document.getElementById('cookie-format-hint'),
    countInput: document.getElementById('count-input'),
    scopeSelect: document.getElementById('scope-select'),
    loadGroupsBtn: document.getElementById('load-groups-btn'),
    clearBtn: document.getElementById('clear-btn'),
    groupsList: document.getElementById('groups-list'),
    topicsTitle: document.getElementById('topics-title'),
    topicsSubtitle: document.getElementById('topics-subtitle'),
    refreshTopicsBtn: document.getElementById('refresh-topics-btn'),
    selectAllTopicsBtn: document.getElementById('select-all-topics-btn'),
    batchDownloadBtn: document.getElementById('batch-download-btn'),
    copyTopicsJsonBtn: document.getElementById('copy-topics-json-btn'),
    topicsTabs: document.getElementById('topics-tabs'),
    topicsList: document.getElementById('topics-list'),
};

document.addEventListener('DOMContentLoaded', () => {
    if (state.cookie) {
        localStorage.setItem(COOKIE_STORAGE_KEY, state.cookie);
        sessionStorage.removeItem('zsxq_cookie');
    }
    els.cookieInput.value = state.cookie;
    updateCookieFormatHint();
    bindEvents();
});

function bindEvents() {
    els.cookieInput.addEventListener('input', () => {
        state.cookie = els.cookieInput.value.trim();
        updateCookieFormatHint();
        if (state.cookie) {
            localStorage.setItem(COOKIE_STORAGE_KEY, state.cookie);
        } else {
            localStorage.removeItem(COOKIE_STORAGE_KEY);
        }
    });

    els.loadGroupsBtn.addEventListener('click', loadGroups);
    els.refreshTopicsBtn.addEventListener('click', () => {
        if (state.selectedGroup) loadTopics(state.selectedGroup);
    });
    els.scopeSelect.addEventListener('change', () => {
        state.activeTab = els.scopeSelect.value === 'digg' ? 'digg' : 'latest';
        if (state.selectedGroup) loadTopics(state.selectedGroup);
    });
    els.selectAllTopicsBtn.addEventListener('click', toggleSelectAllTopics);
    els.batchDownloadBtn.addEventListener('click', downloadVisibleFiles);
    els.copyTopicsJsonBtn.addEventListener('click', copyLastTopicsJson);
    els.topicsList.addEventListener('scroll', handleTopicsScroll);
    els.clearBtn.addEventListener('click', clearAll);
}

function updateCookieFormatHint() {
    const value = els.cookieInput.value.trim();
    if (!value) {
        els.cookieFormatHint.classList.add('hidden');
        els.cookieFormatHint.textContent = '';
        return;
    }

    if (value.startsWith('[') || value.startsWith('{')) {
        els.cookieFormatHint.classList.remove('hidden');
        els.cookieFormatHint.textContent = '检测到 Cookie JSON 导出格式，后端会自动转换。';
        return;
    }

    els.cookieFormatHint.classList.remove('hidden');
    els.cookieFormatHint.textContent = '检测到 Cookie 请求头格式。';
}

async function apiPost(endpoint, payload) {
    const response = await fetch(`${apiBaseUrl()}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
        const message = data?.error?.message || data?.message || `HTTP ${response.status}`;
        console.warn('[ZSXQ] request failed', { endpoint, status: response.status, message });
        throw new Error(message);
    }
    return data.data;
}

function apiBaseUrl() {
    return window.API_BASE_URL || window.CONFIG?.API_BASE_URL || 'http://localhost:5001/api/v1';
}

async function loadGroups() {
    const cookie = els.cookieInput.value.trim();
    if (!cookie) {
        showGroupsMessage('请先粘贴 Cookie。', 'warning');
        return;
    }

    setLoading(els.loadGroupsBtn, true, '获取中...');
    showGroupsMessage('正在请求知识星球 API...');
    try {
        const result = await apiPost('/zsxq/groups', { cookie, count: 50 });
        const groups = result?.resp_data?.groups || [];
        state.groups = groups;
        renderGroups(groups);
    } catch (error) {
        showGroupsMessage(error.message, 'error');
    } finally {
        setLoading(els.loadGroupsBtn, false, '<i class="fas fa-satellite-dish mr-2"></i>获取星球');
    }
}

async function loadTopics(group, options = {}) {
    state.selectedGroup = group;
    if (options.resetTabs !== false) {
        state.tabStates.clear();
    }
    renderGroups(state.groups);
    els.refreshTopicsBtn.classList.remove('hidden');
    els.copyTopicsJsonBtn.classList.add('hidden');
    els.topicsTitle.textContent = group.name || `星球 ${group.group_id}`;
    els.topicsSubtitle.textContent = `Group ID: ${group.group_id}`;
    els.topicsList.innerHTML = loadingCard('正在获取最新主题...');
    renderTopicTabs();

    await loadActiveTabPage({ reset: true });
}

async function loadActiveTabPage(options = {}) {
    if (!state.selectedGroup || state.isLoadingMore) return;
    const cooldownMs = state.topicCooldownUntil - Date.now();
    if (cooldownMs > 0) {
        showCooldownMessage(cooldownMs);
        return;
    }

    const tab = getActiveTabConfig();
    const tabState = getTabState(tab.id);
    const reset = Boolean(options.reset);
    if (!reset && !tabState.hasMore) return;

    if (reset) {
        resetTabState(tabState);
        els.topicsList.innerHTML = loadingCard(`正在获取${tab.label}...`);
    }

    state.isLoadingMore = true;
    tabState.loading = true;
    tabState.error = null;
    renderActiveTab();

    try {
        let pagesLoaded = 0;
        const maxAutoPages = 1;

        do {
            const requestPayload = buildTopicsRequest(tab, tabState);
            await waitForTopicRequestSlot();
            const result = await apiPost('/zsxq/topics', requestPayload);
            state.lastTopicsRequest = withoutSensitiveCookie(requestPayload);
            state.lastTopicsResponse = result;
            state.lastTopicsScope = requestPayload.scope || 'all';
            els.copyTopicsJsonBtn.classList.remove('hidden');

            const pageTopics = extractTopicsFromResponse(result);
            appendTabTopics(tabState, tab, pageTopics);
            tabState.loaded = true;
            tabState.endTime = result?.resp_data?.next_end_time || oldestTopicTime(pageTopics);
            tabState.hasMore = pageTopics.length > 0 && Boolean(tabState.endTime);
            state.hashtagTabs = collectHashtags(getAllCachedTopics());
            pagesLoaded += 1;

            if (!tabState.hasMore) break;
        } while (
            pagesLoaded < maxAutoPages
        );
    } catch (error) {
        tabState.error = error.message;
        if (isRateLimitedError(error)) {
            state.topicCooldownUntil = Date.now() + 30000;
            tabState.error = '知识星球接口暂时限流，已暂停 30 秒后再试。';
        }
        if (tabState.topics.length > 0) {
            tabState.hasMore = false;
        }
        state.lastTopicsResponse = null;
    } finally {
        tabState.loading = false;
        state.isLoadingMore = false;
        renderActiveTab();
    }
}

async function waitForTopicRequestSlot() {
    const minGap = randomDelay(4.5, 7);
    const now = Date.now();
    const waitMs = Math.max(0, minGap - (now - state.lastTopicRequestAt));
    if (waitMs > 0) {
        await sleep(waitMs);
    }
    state.lastTopicRequestAt = Date.now();
}

function isRateLimitedError(error) {
    return String(error?.message || error || '').includes('1059');
}

function showCooldownMessage(cooldownMs) {
    const tab = getActiveTabConfig();
    const tabState = getTabState(tab.id);
    tabState.error = `知识星球接口冷却中，约 ${Math.ceil(cooldownMs / 1000)} 秒后再试。`;
    renderActiveTab();
}

async function copyLastTopicsJson() {
    if (!state.lastTopicsResponse && !state.lastTopicsRequest) return;
    await navigator.clipboard.writeText(JSON.stringify({
        request: state.lastTopicsRequest,
        response: state.lastTopicsResponse,
    }, null, 2));
    const original = els.copyTopicsJsonBtn.innerHTML;
    els.copyTopicsJsonBtn.innerHTML = '<i class="fas fa-check mr-2"></i>已复制';
    setTimeout(() => { els.copyTopicsJsonBtn.innerHTML = original; }, 1200);
}

function buildTopicsRequest(tab, tabState) {
    return {
        cookie: state.cookie,
        group_id: state.selectedGroup.group_id,
        count: Number(els.countInput.value || 20),
        scope: tab.scope || 'all',
        end_time: tabState.endTime || null,
        extra_params: tab.extraParams || null,
    };
}

function withoutSensitiveCookie(payload) {
    return { ...payload, cookie: payload.cookie ? '[saved locally]' : '' };
}

function getTabState(tabId) {
    const key = `${state.selectedGroup?.group_id || 'none'}:${tabId}`;
    if (!state.tabStates.has(key)) {
        state.tabStates.set(key, {
            id: tabId,
            topics: [],
            rawCount: 0,
            topicIds: new Set(),
            endTime: null,
            hasMore: true,
            loading: false,
            loaded: false,
            error: null,
        });
    }
    return state.tabStates.get(key);
}

function resetTabState(tabState) {
    tabState.topics = [];
    tabState.rawCount = 0;
    tabState.topicIds = new Set();
    tabState.endTime = null;
    tabState.hasMore = true;
    tabState.loading = false;
    tabState.loaded = false;
    tabState.error = null;
    state.selectedTopicKeys.clear();
}

function appendTabTopics(tabState, tab, pageTopics) {
    tabState.rawCount += pageTopics.length;
    const visibleTopics = tab.localFilter ? pageTopics.filter(tab.localFilter) : pageTopics;
    visibleTopics.forEach(topic => {
        const key = topicKey(topic);
        if (tabState.topicIds.has(key)) return;
        tabState.topicIds.add(key);
        tabState.topics.push(topic);
    });
}

function getAllCachedTopics() {
    const topics = [];
    const seen = new Set();
    state.tabStates.forEach(tabState => {
        tabState.topics.forEach(topic => {
            const key = topicKey(topic);
            if (seen.has(key)) return;
            seen.add(key);
            topics.push(topic);
        });
    });
    return topics;
}

function renderActiveTab() {
    const tab = getActiveTabConfig();
    const tabState = getTabState(tab.id);
    state.currentTopics = tabState.topics;

    if (state.selectedGroup) {
        els.topicsSubtitle.textContent = [
            `Group ID: ${state.selectedGroup.group_id}`,
            `接口 ${tabState.rawCount} 条`,
            `当前 tab ${tabState.topics.length} 条`,
            tabState.hasMore ? '可继续下滑' : '已到底',
        ].join(' · ');
    }

    renderTopicTabs();
    if (tabState.error && !tabState.topics.length) {
        els.topicsList.innerHTML = emptyCard(tabState.error, 'error');
        return;
    }
    renderTopics(tabState.topics, tabState);
}

function handleTopicsScroll() {
    const distanceToBottom = els.topicsList.scrollHeight - els.topicsList.scrollTop - els.topicsList.clientHeight;
    if (distanceToBottom < 180) {
        loadActiveTabPage();
    }
}

function assertUpstreamSucceeded(result) {
    if (!result || result.succeeded !== false) return;
    const code = result.code ? `code ${result.code}` : '上游接口失败';
    const message = result.error || result.message || result?.resp_data?.error || JSON.stringify(result).slice(0, 300);
    throw new Error(`${code}: ${message}`);
}

function extractTopicsFromResponse(result) {
    assertUpstreamSucceeded(result);
    const respData = result?.resp_data || result || {};
    const directArrays = [
        respData.topics,
        respData.sticky_topics,
        respData.pin_topics,
        respData.pinned_topics,
        respData.top_topics,
        respData.items,
        respData.list,
    ].filter(Array.isArray);

    const collected = [];
    directArrays.forEach(items => collected.push(...items.filter(isTopicLike)));

    if (!collected.length) {
        collectTopicArrays(respData, collected);
    }

    const seen = new Set();
    return collected.filter(topic => {
        const key = String(topic.topic_id || topic.id || JSON.stringify(topic).slice(0, 200));
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function collectTopicArrays(value, output) {
    if (Array.isArray(value)) {
        const topicItems = value.filter(isTopicLike);
        if (topicItems.length) {
            output.push(...topicItems);
            return;
        }
        value.forEach(item => collectTopicArrays(item, output));
        return;
    }

    if (value && typeof value === 'object') {
        Object.values(value).forEach(child => collectTopicArrays(child, output));
    }
}

function isTopicLike(item) {
    return Boolean(
        item &&
        typeof item === 'object' &&
        (item.topic_id || item.talk || item.article || item.question || item.create_time)
    );
}

function renderTopicTabs() {
    const tabs = getTabDefinitions();

    els.topicsTabs.classList.remove('hidden');
    els.topicsTabs.innerHTML = `
        <div class="flex flex-wrap items-center gap-2 min-w-0">
            ${tabs.map(tab => {
                const active = state.activeTab === tab.id;
                return `
                    <button class="topic-tab relative rounded-md px-3 py-1.5 text-sm font-bold transition-colors ${active ? 'bg-slate-800 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}" data-tab-id="${escapeHtml(tab.id)}">
                        ${escapeHtml(tab.label)}
                    </button>
                `;
            }).join('')}
        </div>
    `;

    els.topicsTabs.querySelectorAll('.topic-tab').forEach(button => {
        button.addEventListener('click', async () => {
            const tabId = button.dataset.tabId;
            if (tabId === state.activeTab) return;

            state.activeTab = tabId;
            const tab = getActiveTabConfig();
            els.scopeSelect.value = tab.scope || 'all';
            state.selectedTopicKeys.clear();

            const tabState = getTabState(tabId);
            if (!tabState.loaded) {
                await loadActiveTabPage({ reset: true });
            } else {
                renderActiveTab();
            }
        });
    });
}

function getTabDefinitions() {
    const categoryTab = label => ({
        id: `category:${label}`,
        label,
        scope: 'all',
        localFilter: topic => matchTopicCategory(topic, label),
    });
    const categoryLabels = [
        '调研纪要',
        '国内研报',
        '外资研报',
        '本营调研',
        'AI总结',
        '外媒资讯',
        '小作文',
        '红Bao书',
        '财联社VIP',
        '信息平权',
    ];
    const categoryLabelSet = new Set(categoryLabels);
    const categoryTabs = categoryLabels.map(categoryTab);

    return [
        { id: 'latest', label: '最新', scope: 'all' },
        ...categoryTabs,
        { id: 'database', label: '数据库', scope: 'all', localFilter: topic => matchTopicCategory(topic, '数据库') },
        { id: 'files', label: '文件', scope: 'all', localFilter: topic => topicFiles(topic).length > 0 },
        { id: 'images', label: '图片', scope: 'all', localFilter: topic => topicImages(topic).length > 0 },
        { id: 'links', label: '链接', scope: 'all', localFilter: topic => /https?:\/\//.test(topicSearchText(topic)) },
        { id: 'digg', label: '精华', scope: 'digg' },
        ...state.hashtagTabs
            .filter(tag => !['外资', '信息'].includes(tag) && !categoryLabelSet.has(tag))
            .slice(0, 6)
            .map(tag => ({
                id: `hashtag:${tag}`,
                label: tag,
                scope: 'all',
                localFilter: topic => extractHashtags(topic).includes(tag),
            })),
    ];
}

function getActiveTabConfig() {
    return getTabDefinitions().find(tab => tab.id === state.activeTab) || getTabDefinitions()[0];
}

function normalizeTopicText(text) {
    return String(text || '').replace(/<e\s+[^>]*type=["']hashtag["'][^>]*\/?>/g, match => {
        const titleMatch = match.match(/title=["']([^"']+)["']/);
        if (!titleMatch) return '';
        try {
            return decodeURIComponent(titleMatch[1]);
        } catch {
            return '';
        }
    }).trim();
}

function topicSearchText(topic) {
    return [
        topic.title,
        topic?.talk?.text,
        topic?.article?.title,
        topic?.article?.article_content,
        topic?.question?.text,
        topicFiles(topic).map(file => file.name || file.title || file.file_name).join('\n'),
    ].filter(Boolean).join('\n');
}

function matchTopicCategory(topic, category) {
    const tags = extractHashtags(topic);
    const text = topicSearchText(topic);
    const normalized = text.toLowerCase();
    const rules = {
        '调研纪要': ['调研纪要', '纪要', '调研'],
        '国内研报': ['国内研报', '国内', '中金', '中信', '国君', '华泰', '招商', '海通', '申万', '广发', '天风', '东吴', '民生', '兴业'],
        '外资研报': ['外资研报', '外资', '高盛', '摩根', '大摩', '小摩', '瑞银', '花旗', '美银', '巴克莱', '杰富瑞', 'bernstein', 'ubs', 'goldman', 'morgan', 'jpmorgan', 'citi'],
        '本营调研': ['本营调研', '本营'],
        'AI总结': ['ai总结', 'ai 总结', 'ai助手', 'ai 助手', '总结'],
        '外媒资讯': ['外媒资讯', '外媒', 'bloomberg', 'reuters', 'wsj', 'ft.com'],
        '小作文': ['小作文'],
        '红Bao书': ['红bao书', '红宝书', '红Bao书'],
        '财联社VIP': ['财联社vip', '财联社 vip', '财联社'],
        '信息平权': ['信息平权'],
        '数据库': ['数据库', 'data', 'database'],
    };

    if (tags.includes(category)) return true;
    return (rules[category] || [category]).some(keyword => normalized.includes(keyword.toLowerCase()));
}

function collectHashtags(topics) {
    const counts = new Map();
    topics.forEach(topic => {
        extractHashtags(topic).forEach(tag => counts.set(tag, (counts.get(tag) || 0) + 1));
    });
    return Array.from(counts.entries())
        .sort((a, b) => b[1] - a[1])
        .map(([tag]) => tag);
}

function renderGroups(groups) {
    if (!groups.length) {
        showGroupsMessage('没有获取到星球。请检查 Cookie 是否过期。', 'warning');
        return;
    }

    els.groupsList.innerHTML = groups.map(group => {
        const active = state.selectedGroup?.group_id === group.group_id;
        return `
            <button class="w-full text-left p-4 hover:bg-blue-50 transition-colors ${active ? 'bg-blue-50' : ''}" data-group-id="${escapeHtml(String(group.group_id))}">
                <div class="flex items-start justify-between gap-3">
                    <div>
                        <div class="font-bold text-slate-800">${escapeHtml(group.name || '未命名星球')}</div>
                        <div class="text-xs text-slate-400 mt-1">ID: ${escapeHtml(String(group.group_id))}</div>
                    </div>
                    <span class="text-[10px] font-bold uppercase rounded-full px-2 py-1 ${group.type === 'paid' ? 'bg-emerald-50 text-emerald-600' : 'bg-slate-100 text-slate-500'}">${escapeHtml(group.type || 'group')}</span>
                </div>
            </button>
        `;
    }).join('');

    els.groupsList.querySelectorAll('[data-group-id]').forEach(button => {
        button.addEventListener('click', () => {
            const group = state.groups.find(item => String(item.group_id) === button.dataset.groupId);
            if (group) {
                state.activeTab = els.scopeSelect.value === 'digg' ? 'digg' : 'latest';
                loadTopics(group);
            }
        });
    });
}

function renderTopics(topics, tabState = null) {
    if (!topics.length) {
        const message = tabState?.loading
            ? '正在加载...'
            : (tabState?.hasMore ? '当前页还没有匹配内容，继续下滑会尝试加载更多。' : '这个筛选下没有主题。');
        els.topicsList.innerHTML = emptyCard(message, tabState?.loading ? 'muted' : 'warning') + renderLoadMoreFooter(tabState);
        state.fileMap.clear();
        state.topicMap.clear();
        updateBatchDownloadButton();
        updateSelectAllButton();
        bindLoadMoreAction();
        return;
    }

    state.fileMap.clear();
    state.topicMap.clear();
    els.topicsList.innerHTML = topics.map(topic => {
        const topicKey = topicKeyForRender(topic);
        state.topicMap.set(topicKey, topic);
        const content = extractContent(topic);
        const title = topic.title || topic?.talk?.text?.slice(0, 48) || topic?.article?.title || '无标题主题';
        const author = topic?.owner?.name || topic?.talk?.owner?.name || '未知作者';
        const created = topic.create_time || topic?.talk?.create_time || '';
        const files = topicFiles(topic);

        return `
            <article class="rounded-lg border border-slate-200 p-4 hover:border-blue-200 hover:shadow-sm transition-all min-w-0" data-topic-key="${escapeHtml(topicKey)}">
                <div class="flex items-start justify-between gap-4 min-w-0">
                    <div class="flex items-start gap-3 min-w-0 flex-1">
                        <input type="checkbox" class="topic-select-checkbox mt-1 h-4 w-4 rounded border-slate-300 text-blue-600 shrink-0" data-topic-key="${escapeHtml(topicKey)}" ${files.length ? '' : 'disabled'} ${state.selectedTopicKeys.has(topicKey) ? 'checked' : ''} title="${files.length ? '选择该主题附件' : '该主题没有附件'}">
                        <div class="min-w-0 flex-1">
                        <h4 class="font-black text-slate-900 leading-snug line-clamp-2 break-words">${escapeHtml(title)}</h4>
                        <div class="mt-1 text-xs text-slate-400">${escapeHtml(author)} ${created ? `· ${escapeHtml(created)}` : ''}</div>
                        </div>
                    </div>
                    <div class="shrink-0 flex flex-wrap justify-end items-center gap-2 max-w-[240px]">
                        <button class="copy-topic-json-btn rounded-md border border-slate-300 px-2 py-1 text-xs font-bold text-slate-500 hover:bg-slate-50" data-topic-key="${escapeHtml(topicKey)}">详情 JSON</button>
                        <span class="text-xs text-slate-400">#${escapeHtml(String(topic.topic_id || ''))}</span>
                    </div>
                </div>
                <p class="mt-3 text-sm text-slate-600 leading-6 whitespace-pre-wrap break-words">${escapeHtml(truncate(content, 900))}</p>
                ${files.length ? renderFiles(files, topic.topic_id, topicKey) : ''}
            </article>
        `;
    }).join('') + renderLoadMoreFooter(tabState);

    bindTopicSelection();
    bindFileActions();
    bindTopicActions();
    bindLoadMoreAction();
    updateBatchDownloadButton();
}

function renderLoadMoreFooter(tabState) {
    if (!tabState) return '';
    if (tabState.error) {
        return `<div class="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-center text-sm text-amber-700">${escapeHtml(tabState.error)}</div>`;
    }
    if (tabState.loading) {
        return `<div class="py-5 text-center text-sm text-slate-400"><i class="fas fa-circle-notch fa-spin mr-2"></i>加载更多...</div>`;
    }
    if (tabState.hasMore) {
        return `<button id="load-more-topics-btn" class="w-full rounded-lg border border-dashed border-slate-300 py-4 text-sm font-bold text-slate-500 hover:bg-slate-50">继续加载</button>`;
    }
    return `<div class="py-5 text-center text-xs text-slate-400">已加载到底</div>`;
}

function topicKeyForRender(topic) {
    return String(topic.topic_id || topic.topic_uid || `${topic.create_time || ''}-${topic.title || ''}`);
}

function topicKey(topic) {
    return topicKeyForRender(topic);
}

function oldestTopicTime(topics) {
    const times = topics
        .map(topic => topic?.create_time || topic?.talk?.create_time)
        .filter(Boolean)
        .sort();
    return times[0] || null;
}

function extractContent(topic) {
    if (topic?.talk?.text) return normalizeTopicText(topic.talk.text);
    if (topic?.article?.article_content) return normalizeTopicText(topic.article.article_content);
    if (topic?.article?.title) return normalizeTopicText(topic.article.title);
    if (topic?.question?.text) return normalizeTopicText(topic.question.text);
    if (topic?.files?.length) return `[附件] ${topic.files[0].name || '未命名附件'}`;
    return '无正文预览';
}

function topicFiles(topic) {
    return topic.files || topic?.talk?.files || topic?.article?.files || [];
}

function topicImages(topic) {
    return topic.images || topic?.talk?.images || topic?.article?.images || [];
}

function extractHashtags(topic) {
    const chunks = [
        topic.title,
        topic?.talk?.text,
        topic?.article?.title,
        topic?.article?.article_content,
        topic?.question?.text,
    ].filter(Boolean).join('\n');

    const tags = new Set();
    const xmlTagRegex = /<e\s+[^>]*type=["']hashtag["'][^>]*>/g;
    const titleRegex = /title=["']([^"']+)["']/;
    const xmlMatches = chunks.match(xmlTagRegex) || [];

    xmlMatches.forEach(match => {
        const titleMatch = match.match(titleRegex);
        if (!titleMatch) return;
        try {
            const decoded = decodeURIComponent(titleMatch[1]).replace(/^#|#$/g, '').trim();
            if (decoded) tags.add(decoded);
        } catch {}
    });

    const plainMatches = chunks.match(/#[^#\s<]+#/g) || [];
    plainMatches.forEach(match => {
        const tag = match.replace(/^#|#$/g, '').trim();
        if (tag) tags.add(tag);
    });

    chunks.split('\n').forEach(line => {
        const match = line.trim().match(/^#\s*([^\s#，,。；;：:]{2,12})\s*$/);
        if (match) tags.add(match[1].trim());
    });

    return Array.from(tags);
}

function renderFiles(files, topicId, topicKey) {
    return `
        <div class="mt-3 space-y-2">
            ${files.map((file, index) => {
                const id = `${topicId || 'topic'}-${index}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
                state.fileMap.set(id, { file, topicId, topicKey });
                const name = file.name || file.title || file.file_name || '附件';
                return `
                    <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 min-w-0">
                        <div class="min-w-0 flex items-center gap-2 text-sm text-slate-700">
                            <i class="fas fa-file-pdf text-red-500 shrink-0"></i>
                            <span class="break-words min-w-0">${escapeHtml(name)}</span>
                        </div>
                        <div class="flex items-center gap-2 shrink-0">
                            <button class="copy-file-json-btn rounded-md border border-slate-300 px-2 py-1 text-xs font-bold text-slate-500 hover:bg-white" data-file-id="${escapeHtml(id)}">
                                JSON
                            </button>
                            <button class="download-file-btn rounded-md bg-blue-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-700" data-file-id="${escapeHtml(id)}">
                                <i class="fas fa-download mr-1"></i>下载
                            </button>
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function bindTopicSelection() {
    els.topicsList.querySelectorAll('.topic-select-checkbox').forEach(input => {
        input.addEventListener('change', () => {
            if (input.checked) {
                state.selectedTopicKeys.add(input.dataset.topicKey);
            } else {
                state.selectedTopicKeys.delete(input.dataset.topicKey);
            }
            updateBatchDownloadButton();
            updateSelectAllButton();
        });
    });
    updateSelectAllButton();
}

function bindLoadMoreAction() {
    const button = document.getElementById('load-more-topics-btn');
    if (button) {
        button.addEventListener('click', () => loadActiveTabPage());
    }
}

function bindFileActions() {
    els.topicsList.querySelectorAll('.download-file-btn').forEach(button => {
        button.addEventListener('click', () => downloadFile(button));
    });
    els.topicsList.querySelectorAll('.copy-file-json-btn').forEach(button => {
        button.addEventListener('click', async () => {
            const entry = state.fileMap.get(button.dataset.fileId);
            if (!entry) return;
            await navigator.clipboard.writeText(JSON.stringify(entry.file, null, 2));
            const original = button.textContent;
            button.textContent = '已复制';
            setTimeout(() => { button.textContent = original; }, 1200);
        });
    });
}

function bindTopicActions() {
    els.topicsList.querySelectorAll('.copy-topic-json-btn').forEach(button => {
        button.addEventListener('click', async () => {
            const topic = state.topicMap.get(button.dataset.topicKey);
            if (!topic) return;
            let detail = topic;

            if (topic.topic_id) {
                try {
                    const result = await apiPost('/zsxq/topic', {
                        cookie: state.cookie,
                        topic_id: topic.topic_id,
                    });
                    detail = result;
                } catch {}
            }

            await navigator.clipboard.writeText(JSON.stringify(detail, null, 2));
            const original = button.textContent;
            button.textContent = '已复制';
            setTimeout(() => { button.textContent = original; }, 1200);
        });
    });
}

async function downloadFile(button) {
    const entry = state.fileMap.get(button.dataset.fileId);
    if (!entry) return;
    const { file, topicId } = entry;

    const filename = file.name || file.title || file.file_name || 'zsxq_file';
    const originalHTML = button.innerHTML;
    setLoading(button, true, '下载中...');

    try {
        const response = await fetch(`${apiBaseUrl()}/zsxq/download`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                cookie: state.cookie,
                file,
                topic_id: topicId,
                filename,
            }),
        });

        if (!response.ok) {
            let message = `HTTP ${response.status}`;
            try {
                const data = await response.json();
                message = data?.error?.message || data?.message || message;
            } catch {}
            throw new Error(message);
        }

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
    } catch (error) {
        alert(`下载失败：${error.message}`);
    } finally {
        setLoading(button, false, originalHTML);
    }
}

function clearAll() {
    state.cookie = '';
    state.selectedGroup = null;
    state.groups = [];
    state.tabStates.clear();
    localStorage.removeItem(COOKIE_STORAGE_KEY);
    sessionStorage.removeItem('zsxq_cookie');
    els.cookieInput.value = '';
    showGroupsMessage('等待获取...');
    els.topicsTitle.textContent = '最新主题';
    els.topicsSubtitle.textContent = '选择一个星球后查看主题内容。';
    els.refreshTopicsBtn.classList.add('hidden');
    els.selectAllTopicsBtn.classList.add('hidden');
    els.batchDownloadBtn.classList.add('hidden');
    els.copyTopicsJsonBtn.classList.add('hidden');
    els.topicsTabs.classList.add('hidden');
    els.topicsList.innerHTML = emptyCard('暂无数据');
}

function updateBatchDownloadButton() {
    const selectedEntries = getSelectedFileEntries();
    if (state.fileMap.size > 0) {
        els.batchDownloadBtn.classList.remove('hidden');
        els.selectAllTopicsBtn.classList.remove('hidden');
        els.batchDownloadBtn.innerHTML = `<i class="fas fa-download mr-2"></i>排队下载选中附件（${selectedEntries.length}）`;
        els.batchDownloadBtn.disabled = selectedEntries.length === 0;
        els.batchDownloadBtn.classList.toggle('opacity-50', selectedEntries.length === 0);
    } else {
        els.batchDownloadBtn.classList.add('hidden');
        els.selectAllTopicsBtn.classList.add('hidden');
    }
}

function getSelectableTopicKeys() {
    const keys = new Set();
    for (const entry of state.fileMap.values()) {
        if (entry.topicKey) keys.add(entry.topicKey);
    }
    return keys;
}

function getSelectedFileEntries() {
    return Array.from(state.fileMap.values()).filter(entry => state.selectedTopicKeys.has(entry.topicKey));
}

function updateSelectAllButton() {
    const selectable = getSelectableTopicKeys();
    if (!selectable.size) {
        els.selectAllTopicsBtn.classList.add('hidden');
        return;
    }
    els.selectAllTopicsBtn.classList.remove('hidden');
    const allSelected = Array.from(selectable).every(key => state.selectedTopicKeys.has(key));
    els.selectAllTopicsBtn.innerHTML = allSelected
        ? '<i class="fas fa-square-minus mr-2"></i>取消全选'
        : '<i class="fas fa-check-double mr-2"></i>全选主题';
}

function toggleSelectAllTopics() {
    const selectable = getSelectableTopicKeys();
    const allSelected = Array.from(selectable).every(key => state.selectedTopicKeys.has(key));
    if (allSelected) {
        selectable.forEach(key => state.selectedTopicKeys.delete(key));
    } else {
        selectable.forEach(key => state.selectedTopicKeys.add(key));
    }

    els.topicsList.querySelectorAll('.topic-select-checkbox').forEach(input => {
        input.checked = state.selectedTopicKeys.has(input.dataset.topicKey);
    });
    updateSelectAllButton();
    updateBatchDownloadButton();
}

function randomDelay(minSeconds = 9, maxSeconds = 12) {
    return (minSeconds + Math.random() * (maxSeconds - minSeconds)) * 1000;
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function downloadVisibleFiles() {
    const entries = getSelectedFileEntries();
    if (!entries.length) {
        alert('请先勾选要下载的主题。');
        return;
    }

    const originalHTML = els.batchDownloadBtn.innerHTML;
    setLoading(els.batchDownloadBtn, true, '排队中...');

    const failures = [];

    try {
        for (let index = 0; index < entries.length; index++) {
            const { file, topicId } = entries[index];
            const filename = file.name || file.title || file.file_name || `zsxq_file_${index + 1}`;

            if (index > 0) {
                const waitMs = randomDelay(9, 12);
                els.batchDownloadBtn.innerHTML = `<i class="fas fa-clock mr-2"></i>等待 ${Math.ceil(waitMs / 1000)}s (${index + 1}/${entries.length})`;
                await sleep(waitMs);
            }

            els.batchDownloadBtn.innerHTML = `<i class="fas fa-circle-notch fa-spin mr-2"></i>下载中 ${index + 1}/${entries.length}`;

            try {
                const response = await fetch(`${apiBaseUrl()}/zsxq/download`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        cookie: state.cookie,
                        file,
                        topic_id: topicId,
                        filename,
                    }),
                });

                if (!response.ok) {
                    let message = `HTTP ${response.status}`;
                    try {
                        const data = await response.json();
                        message = data?.error?.message || data?.message || message;
                    } catch {}
                    throw new Error(message);
                }

                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = url;
                link.download = filename;
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(url);
            } catch (error) {
                failures.push(`${filename}: ${error.message}`);
            }
        }

        if (failures.length) {
            alert(`部分附件下载失败：\n${failures.join('\n')}`);
        }
    } catch (error) {
        alert(`批量下载失败：${error.message}`);
    } finally {
        setLoading(els.batchDownloadBtn, false, originalHTML);
    }
}

function showGroupsMessage(message, type = 'muted') {
    const color = type === 'error' ? 'text-red-500' : type === 'warning' ? 'text-amber-600' : 'text-slate-400';
    els.groupsList.innerHTML = `<div class="p-4 text-sm ${color}">${escapeHtml(message)}</div>`;
}

function loadingCard(message) {
    return `<div class="rounded-lg border border-slate-200 p-8 text-center text-slate-400"><i class="fas fa-circle-notch fa-spin mr-2"></i>${escapeHtml(message)}</div>`;
}

function emptyCard(message, type = 'muted') {
    const color = type === 'error' ? 'text-red-500 border-red-200' : type === 'warning' ? 'text-amber-600 border-amber-200' : 'text-slate-400 border-slate-300';
    return `<div class="rounded-lg border border-dashed ${color} p-8 text-center">${escapeHtml(message)}</div>`;
}

function setLoading(button, isLoading, html) {
    button.disabled = isLoading;
    button.classList.toggle('opacity-60', isLoading);
    button.innerHTML = isLoading ? `<i class="fas fa-circle-notch fa-spin mr-2"></i>${html}` : html;
}

function truncate(text, maxLength) {
    const value = String(text || '');
    return value.length > maxLength ? `${value.slice(0, maxLength)}...` : value;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
