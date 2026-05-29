const COOKIE_STORAGE_KEY = 'zsxq_cookie';
const COOKIE_PROFILES_STORAGE_KEY = 'zsxq_cookie_profiles';
const ACTIVE_COOKIE_ID_STORAGE_KEY = 'zsxq_active_cookie_id';

const state = {
    cookie: '',
    cookieProfiles: [],
    activeCookieId: localStorage.getItem(ACTIVE_COOKIE_ID_STORAGE_KEY) || '',
    selectedGroup: null,
    groups: [],
    fileMap: new Map(),
    topicMap: new Map(),
    selectedTopicKeys: new Set(),
    currentTopics: [],
    activeTab: 'latest',
    officialMenus: [],
    menuError: null,
    tabStates: new Map(),
    lastTopicsResponse: null,
    lastTopicsRequest: null,
    lastTopicsScope: 'all',
    isLoadingMore: false,
    isSyncing: false,
    syncRuns: [],
    attachmentStatusCache: new Map(),
    attachmentType: 'pdf',
    lastTopicRequestAt: 0,
    topicCooldownUntil: 0,
};

const els = {
    cookieProfileSelect: document.getElementById('cookie-profile-select'),
    cookieNameInput: document.getElementById('cookie-name-input'),
    cookieInput: document.getElementById('cookie-input'),
    cookieEditor: document.getElementById('cookie-editor'),
    cookieSummary: document.getElementById('cookie-summary'),
    cookieFormatHint: document.getElementById('cookie-format-hint'),
    toggleCookieBtn: document.getElementById('toggle-cookie-btn'),
    newCookieBtn: document.getElementById('new-cookie-btn'),
    saveCookieBtn: document.getElementById('save-cookie-btn'),
    deleteCookieBtn: document.getElementById('delete-cookie-btn'),
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
    attachmentTypeControl: document.getElementById('attachment-type-control'),
    attachmentTypeSelect: document.getElementById('attachment-type-select'),
    copyTopicsJsonBtn: document.getElementById('copy-topics-json-btn'),
    syncCurrentTabBtn: document.getElementById('sync-current-tab-btn'),
    syncAllTabsBtn: document.getElementById('sync-all-tabs-btn'),
    syncStatus: document.getElementById('sync-status'),
    publishPanel: document.getElementById('publish-panel'),
    publishTextInput: document.getElementById('publish-text-input'),
    publishCurrentTabCheckbox: document.getElementById('publish-current-tab-checkbox'),
    publishCurrentTabLabel: document.getElementById('publish-current-tab-label'),
    publishFilesInput: document.getElementById('publish-files-input'),
    publishTopicBtn: document.getElementById('publish-topic-btn'),
    publishStatus: document.getElementById('publish-status'),
    topicsTabs: document.getElementById('topics-tabs'),
    topicsList: document.getElementById('topics-list'),
};

document.addEventListener('DOMContentLoaded', () => {
    initializeCookieProfiles();
    els.cookieInput.value = state.cookie;
    updateCookieFormatHint();
    renderCookieProfiles();
    updateCookieSummary();
    setCookieEditorOpen(!state.cookie);
    bindEvents();
});

function bindEvents() {
    els.cookieInput.addEventListener('input', () => {
        state.cookie = els.cookieInput.value.trim();
        updateCookieFormatHint();
        updateCookieSummary();
    });

    els.cookieNameInput.addEventListener('input', updateCookieSummary);
    els.cookieProfileSelect.addEventListener('change', switchCookieProfile);
    els.newCookieBtn.addEventListener('click', startNewCookieProfile);
    els.saveCookieBtn.addEventListener('click', saveCookieProfile);
    els.deleteCookieBtn.addEventListener('click', deleteCookieProfile);
    els.toggleCookieBtn.addEventListener('click', () => {
        setCookieEditorOpen(els.cookieEditor.classList.contains('hidden'));
    });

    els.loadGroupsBtn.addEventListener('click', loadGroups);
    els.refreshTopicsBtn.addEventListener('click', () => {
        if (state.selectedGroup) loadTopics(state.selectedGroup);
    });
    els.scopeSelect.addEventListener('change', () => {
        const tab = getTabDefinitions().find(item => !item.hashtagId && item.scope === els.scopeSelect.value);
        state.activeTab = tab?.id || 'preset:all';
        if (state.selectedGroup) loadTopics(state.selectedGroup);
    });
    els.selectAllTopicsBtn.addEventListener('click', toggleSelectAllTopics);
    els.batchDownloadBtn.addEventListener('click', downloadVisibleFiles);
    els.attachmentTypeSelect.addEventListener('change', () => {
        state.attachmentType = els.attachmentTypeSelect.value || 'pdf';
        updateBatchDownloadButton();
        renderSyncIntro();
    });
    els.copyTopicsJsonBtn.addEventListener('click', copyLastTopicsJson);
    els.syncCurrentTabBtn.addEventListener('click', () => syncTabs('current'));
    els.syncAllTabsBtn.addEventListener('click', () => syncTabs('all'));
    els.publishTopicBtn.addEventListener('click', publishTopic);
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

function initializeCookieProfiles() {
    state.cookieProfiles = loadCookieProfiles();
    const oldCookie = localStorage.getItem(COOKIE_STORAGE_KEY) || sessionStorage.getItem('zsxq_cookie') || '';
    if (oldCookie && !state.cookieProfiles.length) {
        const id = newCookieProfileId();
        state.cookieProfiles = [{
            id,
            name: '默认 Cookie',
            cookie: oldCookie,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
        }];
        state.activeCookieId = id;
        persistCookieProfiles();
        localStorage.removeItem(COOKIE_STORAGE_KEY);
        sessionStorage.removeItem('zsxq_cookie');
    }

    if (!state.cookieProfiles.some(profile => profile.id === state.activeCookieId)) {
        state.activeCookieId = state.cookieProfiles[0]?.id || '';
    }
    applyActiveCookieProfile();
}

function loadCookieProfiles() {
    try {
        const parsed = JSON.parse(localStorage.getItem(COOKIE_PROFILES_STORAGE_KEY) || '[]');
        return Array.isArray(parsed)
            ? parsed.filter(profile => profile && profile.id && profile.cookie)
            : [];
    } catch {
        return [];
    }
}

function persistCookieProfiles() {
    localStorage.setItem(COOKIE_PROFILES_STORAGE_KEY, JSON.stringify(state.cookieProfiles));
    if (state.activeCookieId) {
        localStorage.setItem(ACTIVE_COOKIE_ID_STORAGE_KEY, state.activeCookieId);
    } else {
        localStorage.removeItem(ACTIVE_COOKIE_ID_STORAGE_KEY);
    }
}

function activeCookieProfile() {
    return state.cookieProfiles.find(profile => profile.id === state.activeCookieId) || null;
}

function applyActiveCookieProfile() {
    const profile = activeCookieProfile();
    state.cookie = profile?.cookie || '';
    els.cookieNameInput.value = profile?.name || '';
    els.cookieInput.value = state.cookie;
}

function renderCookieProfiles() {
    const emptyOption = '<option value="">新增/未保存 Cookie</option>';
    if (!state.cookieProfiles.length) {
        els.cookieProfileSelect.innerHTML = emptyOption;
        els.cookieProfileSelect.value = '';
        els.deleteCookieBtn.disabled = true;
        els.deleteCookieBtn.classList.add('opacity-50');
        return;
    }

    els.cookieProfileSelect.innerHTML = emptyOption + state.cookieProfiles.map(profile => {
        const token = extractCookieToken(profile.cookie);
        const label = `${profile.name || '未命名 Cookie'}${token ? ` (${maskToken(token)})` : ''}`;
        return `<option value="${escapeHtml(profile.id)}">${escapeHtml(label)}</option>`;
    }).join('');
    els.cookieProfileSelect.value = state.activeCookieId;
    els.deleteCookieBtn.disabled = false;
    els.deleteCookieBtn.classList.remove('opacity-50');
}

function switchCookieProfile() {
    state.activeCookieId = els.cookieProfileSelect.value;
    applyActiveCookieProfile();
    persistCookieProfiles();
    updateCookieFormatHint();
    updateCookieSummary();
    setCookieEditorOpen(false);
    resetZsxqWorkspace();
}

function startNewCookieProfile() {
    state.activeCookieId = '';
    state.cookie = '';
    els.cookieProfileSelect.value = '';
    els.cookieNameInput.value = '';
    els.cookieInput.value = '';
    updateCookieFormatHint();
    updateCookieSummary();
    setCookieEditorOpen(true);
    resetZsxqWorkspace();
}

function saveCookieProfile() {
    const cookie = els.cookieInput.value.trim();
    if (!cookie) {
        updateCookieSummary('请先填写 Cookie', 'error');
        return;
    }

    const now = new Date().toISOString();
    const existing = activeCookieProfile();
    const token = extractCookieToken(cookie);
    const existingToken = extractCookieToken(existing?.cookie || '');
    const sameTokenProfile = token
        ? state.cookieProfiles.find(profile => extractCookieToken(profile.cookie) === token)
        : null;
    const name = (els.cookieNameInput.value || '').trim()
        || (existing && (!token || token === existingToken) ? existing.name : '')
        || (token ? `Cookie ${maskToken(token)}` : '未命名 Cookie');

    if (sameTokenProfile) {
        sameTokenProfile.name = name;
        sameTokenProfile.cookie = cookie;
        sameTokenProfile.updated_at = now;
        state.activeCookieId = sameTokenProfile.id;
    } else if (existing && (!token || token === existingToken)) {
        existing.name = name;
        existing.cookie = cookie;
        existing.updated_at = now;
    } else {
        const id = newCookieProfileId();
        state.cookieProfiles.push({
            id,
            name,
            cookie,
            created_at: now,
            updated_at: now,
        });
        state.activeCookieId = id;
    }

    state.cookie = cookie;
    persistCookieProfiles();
    renderCookieProfiles();
    updateCookieSummary('已保存当前 Cookie', 'success');
    setCookieEditorOpen(false);
}

function deleteCookieProfile() {
    const currentId = state.activeCookieId;
    if (!currentId) return;
    state.cookieProfiles = state.cookieProfiles.filter(profile => profile.id !== currentId);
    state.activeCookieId = state.cookieProfiles[0]?.id || '';
    persistCookieProfiles();
    applyActiveCookieProfile();
    renderCookieProfiles();
    updateCookieFormatHint();
    updateCookieSummary();
    setCookieEditorOpen(!state.cookie);
    resetZsxqWorkspace();
}

function newCookieProfileId() {
    return `cookie_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function resetZsxqWorkspace() {
    state.selectedGroup = null;
    state.groups = [];
    state.fileMap.clear();
    state.topicMap.clear();
    state.selectedTopicKeys.clear();
    state.currentTopics = [];
    state.officialMenus = [];
    state.tabStates.clear();
    state.attachmentStatusCache.clear();
    state.lastTopicsResponse = null;
    state.lastTopicsRequest = null;
    state.menuError = null;
    showGroupsMessage('等待获取...');
    els.topicsTitle.textContent = '最新主题';
    els.topicsSubtitle.textContent = '选择一个星球后查看主题内容。';
    els.refreshTopicsBtn.classList.add('hidden');
    els.publishPanel.classList.add('hidden');
    els.selectAllTopicsBtn.classList.add('hidden');
    els.batchDownloadBtn.classList.add('hidden');
    els.attachmentTypeControl.classList.add('hidden');
    els.copyTopicsJsonBtn.classList.add('hidden');
    els.syncCurrentTabBtn.classList.add('hidden');
    els.syncAllTabsBtn.classList.add('hidden');
    els.syncStatus.classList.add('hidden');
    els.syncStatus.innerHTML = '';
    els.topicsTabs.classList.add('hidden');
    els.topicsList.innerHTML = emptyCard('暂无数据');
}

function setCookieEditorOpen(open) {
    els.cookieEditor.classList.toggle('hidden', !open);
    els.toggleCookieBtn.textContent = open ? '收起' : '修改';
    if (open) {
        updateCookieFormatHint();
    } else {
        els.cookieFormatHint.classList.add('hidden');
    }
}

function updateCookieSummary(message = '', type = '') {
    const value = els.cookieInput.value.trim();
    if (message) {
        const classByType = type === 'error'
            ? 'border-red-200 bg-red-50 text-red-600'
            : 'border-emerald-200 bg-emerald-50 text-emerald-700';
        els.cookieSummary.className = `mt-2 rounded-lg border px-3 py-2 text-xs ${classByType}`;
        els.cookieSummary.textContent = message;
        return;
    }

    if (!value) {
        els.cookieSummary.className = 'mt-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500';
        els.cookieSummary.textContent = '未保存 Cookie';
        return;
    }

    const token = extractCookieToken(value);
    const name = (els.cookieNameInput.value || activeCookieProfile()?.name || '').trim();
    els.cookieSummary.className = 'mt-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700';
    els.cookieSummary.textContent = token
        ? `${name ? `${name} · ` : ''}zsxq_access_token：${maskToken(token)}`
        : '已保存 Cookie';
}

function extractCookieToken(value) {
    if (!value) return '';
    if (value.startsWith('[') || value.startsWith('{')) {
        try {
            const parsed = JSON.parse(value);
            const items = Array.isArray(parsed) ? parsed : [parsed];
            const tokenItem = items.find(item => item?.name === 'zsxq_access_token');
            return tokenItem?.value || '';
        } catch {
            return '';
        }
    }

    const match = value.match(/(?:^|;\s*)zsxq_access_token=([^;]+)/);
    return match ? match[1] : '';
}

function maskToken(token) {
    const value = String(token || '');
    if (value.length <= 14) return '已隐藏';
    return `${value.slice(0, 8)}...${value.slice(-6)}`;
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
    state.cookie = cookie;

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
    const requestedActiveTab = state.activeTab;
    state.selectedGroup = group;
    if (options.resetTabs !== false) {
        state.tabStates.clear();
    }
    renderGroups(state.groups);
    els.refreshTopicsBtn.classList.remove('hidden');
    els.publishPanel.classList.remove('hidden');
    els.syncCurrentTabBtn.classList.remove('hidden');
    els.syncAllTabsBtn.classList.remove('hidden');
    els.copyTopicsJsonBtn.classList.add('hidden');
    els.topicsTitle.textContent = group.name || `星球 ${group.group_id}`;
    els.topicsSubtitle.textContent = `Group ID: ${group.group_id}`;
    els.topicsList.innerHTML = loadingCard('正在获取最新主题...');
    state.officialMenus = await loadOfficialMenus(group.group_id);
    const tabs = getTabDefinitions();
    if (!tabs.length) {
        els.topicsTabs.classList.add('hidden');
        els.topicsList.innerHTML = emptyCard(state.menuError || '官方菜单不可用，无法展示 tab 数据。', 'error');
        return;
    }
    state.activeTab = tabs.some(tab => tab.id === requestedActiveTab)
        ? requestedActiveTab
        : (tabs[0]?.id || 'latest');
    renderTopicTabs();
    renderSyncIntro();
    loadSyncRuns(group.group_id);

    await loadActiveTabPage({ reset: true });
}

async function loadOfficialMenus(groupId) {
    state.menuError = null;
    try {
        const result = await apiPost('/zsxq/menus', {
            cookie: state.cookie,
            group_id: groupId,
        });
        const menus = result?.resp_data?.menus || [];
        state.menuError = menus.length ? null : '官方菜单为空，不能展示本地猜测分类。';
        return menus;
    } catch (error) {
        state.menuError = `官方菜单加载失败：${error.message}`;
        return [];
    }
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

async function syncTabs(scope) {
    state.cookie = els.cookieInput.value.trim();
    if (!state.cookie) {
        renderSyncStatus({ type: 'error', message: '请先选择或填写 Cookie。' });
        return;
    }
    if (!state.selectedGroup) {
        renderSyncStatus({ type: 'error', message: '请先选择一个星球。' });
        return;
    }

    const activeTab = getActiveTabConfig();
    const tabs = scope === 'all' ? getTabDefinitions() : [activeTab];
    if (!tabs.length || !activeTab) {
        renderSyncStatus({ type: 'error', message: '当前没有可同步的官方 tab。' });
        return;
    }

    const maxPages = scope === 'all' ? 1 : 3;
    const attachmentType = currentAttachmentType();
    const attachmentTypeLabel = attachmentTypeText(attachmentType);
    const button = scope === 'all' ? els.syncAllTabsBtn : els.syncCurrentTabBtn;
    const originalHTML = button.innerHTML;
    state.isSyncing = true;
    els.syncCurrentTabBtn.disabled = true;
    els.syncAllTabsBtn.disabled = true;
    setLoading(button, true, scope === 'all' ? '同步全部中...' : '同步中...');
    renderSyncStatus({
        type: 'loading',
        message: scope === 'all'
            ? `正在同步全部 ${tabs.length} 个 tab，每个 tab 默认拉 1 页，并自动下载${attachmentTypeLabel}。`
            : `正在同步「${activeTab.label}」，最多拉 ${maxPages} 页，并自动下载${attachmentTypeLabel}。`,
    });

    try {
        const result = await apiPost('/zsxq/sync', {
            cookie: state.cookie,
            group_id: state.selectedGroup.group_id,
            group_name: state.selectedGroup.name,
            tabs,
            count: Number(els.countInput.value || 20),
            max_pages: maxPages,
            stop_on_existing: true,
            download_attachments: true,
            download_file_types: attachmentType === 'all' ? ['all'] : [attachmentType],
            download_pdf_only: attachmentType === 'pdf',
            mode: scope === 'all' ? 'manual_all_tabs' : 'manual_current_tab',
        });
        renderSyncResult(result);
        state.attachmentStatusCache.clear();
        refreshAttachmentStatusBadges();
        await loadSyncRuns(state.selectedGroup.group_id, { render: false });
    } catch (error) {
        renderSyncStatus({ type: 'error', message: `同步失败：${error.message}` });
    } finally {
        state.isSyncing = false;
        els.syncCurrentTabBtn.disabled = false;
        els.syncAllTabsBtn.disabled = false;
        setLoading(button, false, originalHTML);
    }
}

async function loadSyncRuns(groupId, options = {}) {
    try {
        const result = await apiPost('/zsxq/sync/runs', {
            group_id: groupId,
            limit: 5,
        });
        state.syncRuns = result?.runs || [];
        if (options.render === false) {
            return;
        }
        if (state.syncRuns.length) {
            renderSyncRuns();
        } else {
            renderSyncIntro();
        }
    } catch {
        state.syncRuns = [];
        if (options.render === false) {
            return;
        }
        renderSyncIntro();
    }
}

function renderSyncResult(result) {
    const summary = result?.summary || result?.run?.result || {};
    const lines = [
        `新增 ${summary.topics_new || 0}`,
        `更新 ${summary.topics_updated || 0}`,
        `已存在 ${summary.topics_existing || 0}`,
        `附件记录 ${summary.attachments_seen || 0}`,
        `附件下载 ${summary.attachments_downloaded || 0}`,
        `下载失败 ${summary.attachments_failed || 0}`,
    ];
    renderSyncStatus({
        type: 'success',
        title: '本次同步结果',
        message: lines.join(' · '),
        details: renderSyncTabsList(summary.tabs || []),
    });
}

function renderSyncRuns() {
    const latest = state.syncRuns[0];
    if (!latest) return;
    const summary = latest.result || {};
    renderSyncStatus({
        type: latest.status === 'failed' ? 'error' : 'muted',
        title: '同步日志',
        message: `最近一次：${formatZsxqTime(latest.completed_at || latest.created_at)} · ${syncStatusLabel(latest.status)} · 新增 ${summary.topics_new || 0} · 附件下载 ${summary.attachments_downloaded || 0}`,
        details: renderSyncRunsList(state.syncRuns.slice(0, 10)),
    });
}

function renderSyncIntro() {
    if (!state.selectedGroup) return;
    const activeTab = getActiveTabConfig();
    const count = Number(els.countInput.value || 20);
    const attachmentTypeLabel = attachmentTypeText(currentAttachmentType());
    renderSyncStatus({
        type: 'muted',
        title: '同步日志',
        message: `未看到同步记录。当前 Tab 同步最多 ${count * 3} 条；全部 Tab 每个 Tab 最多 ${count} 条。`,
        details: `
            <div class="text-xs text-slate-500 leading-6">
                <div>保存内容：主题元数据、附件元数据、原始 topic JSON 和匹配类型的附件文件。</div>
                <div>保存位置：数据库同步表 + <span class="font-mono">data/zsxq/${escapeHtml(String(state.selectedGroup.group_id))}/</span></div>
                <div>${escapeHtml(attachmentTypeLabel)}保存位置：当前 tab 目录下的 <span class="font-mono">files/</span>；下载间隔随机 9-12 秒。</div>
                ${activeTab ? `<div>当前 Tab：${escapeHtml(activeTab.label)}</div>` : ''}
            </div>
        `,
    });
}

function renderSyncRunsList(runs) {
    if (!runs.length) return '';
    return `
        <div role="list" class="space-y-2">
            ${runs.map(run => {
                const runSummary = run.result || {};
                const label = run.tab_label || (run.requested_tabs?.length ? `${run.requested_tabs.length} 个 tab` : '未知范围');
                const statusClass = run.status === 'failed' ? 'text-red-600' : run.status === 'completed' ? 'text-emerald-700' : 'text-blue-600';
                return `
                    <div role="listitem" class="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
                        <div class="flex flex-wrap items-center justify-between gap-2">
                            <span class="font-bold">${escapeHtml(formatZsxqTime(run.completed_at || run.created_at))}</span>
                            <span class="${statusClass} font-bold">${escapeHtml(syncStatusLabel(run.status))}</span>
                        </div>
                        <div class="mt-1 flex flex-wrap items-center justify-between gap-2">
                            <span>${escapeHtml(label)}</span>
                            <span>新增 ${escapeHtml(String(runSummary.topics_new || 0))} · 更新 ${escapeHtml(String(runSummary.topics_updated || 0))} · 已存在 ${escapeHtml(String(runSummary.topics_existing || 0))} · 附件下载 ${escapeHtml(String(runSummary.attachments_downloaded || 0))} · 失败 ${escapeHtml(String(runSummary.attachments_failed || 0))}</span>
                        </div>
                        ${run.error_message ? `<div class="mt-1 text-red-600">${escapeHtml(run.error_message)}</div>` : ''}
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function renderSyncTabsList(tabs) {
    if (!tabs.length) return '';
    return `
        <div role="list" class="space-y-2">
            ${tabs.map(tab => `
                <div role="listitem" class="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
                    <div class="flex flex-wrap items-center justify-between gap-2">
                        <span class="font-bold">${escapeHtml(tab.tab_label || tab.tab_id)}</span>
                        <span>${escapeHtml(String(tab.pages || 0))} 页 · 拉取 ${escapeHtml(String(tab.topics_fetched || 0))} 条</span>
                    </div>
                    <div class="mt-1 flex flex-wrap items-center justify-between gap-2">
                        <span>新增 ${escapeHtml(String(tab.topics_new || 0))} · 更新 ${escapeHtml(String(tab.topics_updated || 0))}</span>
                        <span>已存在 ${escapeHtml(String(tab.topics_existing || 0))} · 附件 ${escapeHtml(String(tab.attachments_seen || 0))} · 附件下载 ${escapeHtml(String(tab.attachments_downloaded || 0))} · 失败 ${escapeHtml(String(tab.attachments_failed || 0))}</span>
                    </div>
                    ${tab.stopped_by_existing ? '<div class="mt-1 text-slate-400">遇到已同步主题，已提前停止。</div>' : ''}
                </div>
            `).join('')}
        </div>
    `;
}

function syncStatusLabel(status) {
    const map = {
        completed: '成功',
        failed: '失败',
        running: '进行中',
    };
    return map[status] || status || '未知';
}

function renderSyncStatus({ type = 'muted', title = '同步日志', message = '', details = '' }) {
    const colorClasses = {
        loading: 'border-blue-200 bg-blue-50 text-blue-600',
        success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
        error: 'border-red-200 bg-red-50 text-red-600',
        muted: 'border-slate-200 bg-slate-50 text-slate-600',
    };
    els.syncStatus.classList.remove('hidden');
    els.syncStatus.innerHTML = `
        <div class="rounded-lg border px-4 py-3 ${colorClasses[type] || colorClasses.muted}">
            <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="text-sm font-black">${escapeHtml(title)}</div>
                ${state.syncRuns.length ? `<div class="text-xs opacity-60">最近 ${escapeHtml(String(state.syncRuns.length))} 次</div>` : ''}
            </div>
            ${message ? `<div class="mt-1 text-sm font-bold">${escapeHtml(message)}</div>` : ''}
            ${details ? `<div class="mt-2 space-y-2">${details}</div>` : ''}
        </div>
    `;
}

function buildTopicsRequest(tab, tabState) {
    return {
        cookie: state.cookie,
        group_id: state.selectedGroup.group_id,
        count: Number(els.countInput.value || 20),
        scope: tab.scope || 'all',
        end_time: tabState.endTime || null,
        extra_params: {
            ...(tab.extraParams || {}),
            ...(tab.hashtagId ? { hashtag_id: tab.hashtagId } : {}),
        },
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
    pageTopics.forEach(topic => {
        const key = topicKey(topic);
        if (tabState.topicIds.has(key)) return;
        tabState.topicIds.add(key);
        tabState.topics.push(topic);
    });
}

function renderActiveTab() {
    const tab = getActiveTabConfig();
    const tabState = getTabState(tab.id);
    state.currentTopics = tabState.topics;

    if (state.selectedGroup) {
        els.topicsSubtitle.textContent = [
            `Group ID: ${state.selectedGroup.group_id}`,
            tab.hashtagId ? `hashtag ${tab.hashtagId}` : `scope ${tab.scope || 'all'}`,
            `接口 ${tabState.rawCount} 条`,
            `当前 tab ${tabState.topics.length} 条`,
            tabState.hasMore ? '可继续下滑' : '已到底',
        ].join(' · ');
    }

    renderTopicTabs();
    updatePublishTabState();
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
            if (!tab.hashtagId) els.scopeSelect.value = tab.scope || 'all';
            state.selectedTopicKeys.clear();

            const tabState = getTabState(tabId);
            if (!tabState.loaded) {
                await loadActiveTabPage({ reset: true });
            } else {
                renderActiveTab();
            }
            updatePublishTabState();
        });
    });
    updatePublishTabState();
}

function getTabDefinitions() {
    return buildOfficialMenuTabs();
}

function buildOfficialMenuTabs() {
    return state.officialMenus.map(menu => {
        const label = cleanMenuTitle(menu.title || menu.name || '未命名');
        const hashtagId = extractMenuHashtagId(menu);
        if (hashtagId) {
            return {
                id: `hashtag:${hashtagId}`,
                label,
                scope: 'all',
                hashtagId,
            };
        }

        const presetType = extractMenuPresetType(menu);
        if (presetType) {
            return {
                id: `preset:${presetType || menu.menu_id}`,
                label,
                scope: normalizePresetScope(presetType),
            };
        }

        return null;
    }).filter(Boolean);
}

function extractMenuHashtagId(menu) {
    return menu?.hashtag?.hashtag_id
        || menu?.hashtag_id
        || menu?.target?.hashtag_id
        || menu?.target?.id
        || (menu?.type === 'hashtag' ? menu.menu_id : null);
}

function extractMenuPresetType(menu) {
    if (!menu?.preset && !menu?.preset_type) return null;
    return menu.preset_type || menu.type || menu.menu_id;
}

function cleanMenuTitle(title) {
    return String(title || '').replace(/^#|#$/g, '').trim() || '未命名';
}

function normalizePresetScope(presetType) {
    const map = {
        all: 'all',
        with_files: 'with_files',
        with_images: 'with_images',
        digests: 'digests',
    };
    return map[presetType] || presetType || 'all';
}

function getActiveTabConfig() {
    return getTabDefinitions().find(tab => tab.id === state.activeTab) || getTabDefinitions()[0];
}

function updatePublishTabState() {
    const tab = getActiveTabConfig();
    const hasTag = Boolean(tab?.hashtagId);
    els.publishCurrentTabCheckbox.disabled = !hasTag;
    els.publishCurrentTabCheckbox.checked = hasTag;
    els.publishCurrentTabLabel.textContent = hasTag
        ? `发布到当前标签：${tab.label}`
        : '当前不是标签页，仅发布到星球';
    els.publishCurrentTabLabel.classList.toggle('text-slate-400', !hasTag);
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
                const tab = getTabDefinitions().find(item => !item.hashtagId && item.scope === els.scopeSelect.value);
                state.activeTab = tab?.id || 'preset:all';
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
        const createdText = formatZsxqTime(created);
        const files = topicFiles(topic);
        const images = topicImages(topic);

        return `
            <article class="rounded-lg border border-slate-200 p-4 hover:border-blue-200 hover:shadow-sm transition-all min-w-0" data-topic-key="${escapeHtml(topicKey)}">
                <div class="flex items-start justify-between gap-4 min-w-0">
                    <div class="flex items-start gap-3 min-w-0 flex-1">
                        <input type="checkbox" class="topic-select-checkbox mt-1 h-4 w-4 rounded border-slate-300 text-blue-600 shrink-0" data-topic-key="${escapeHtml(topicKey)}" ${files.length ? '' : 'disabled'} ${state.selectedTopicKeys.has(topicKey) ? 'checked' : ''} title="${files.length ? '选择该主题附件' : '该主题没有附件'}">
                        <div class="min-w-0 flex-1">
                        <h4 class="font-black text-slate-900 leading-snug line-clamp-2 break-words">${escapeHtml(title)}</h4>
                        <div class="mt-1 text-xs text-slate-400">${escapeHtml(author)} ${createdText ? `· ${escapeHtml(createdText)}` : ''}</div>
                        </div>
                    </div>
                    <div class="shrink-0 flex flex-wrap justify-end items-center gap-2 max-w-[240px]">
                        <button class="copy-topic-json-btn rounded-md border border-slate-300 px-2 py-1 text-xs font-bold text-slate-500 hover:bg-slate-50" data-topic-key="${escapeHtml(topicKey)}">详情 JSON</button>
                        <span class="text-xs text-slate-400">#${escapeHtml(String(topic.topic_id || ''))}</span>
                    </div>
                </div>
                <p class="mt-3 text-sm text-slate-600 leading-6 whitespace-pre-wrap break-words">${escapeHtml(truncate(content, 900))}</p>
                ${images.length ? renderImages(images) : ''}
                ${files.length ? renderFiles(files, topic.topic_id, topicKey) : ''}
            </article>
        `;
    }).join('') + renderLoadMoreFooter(tabState);

    bindTopicSelection();
    bindFileActions();
    bindTopicActions();
    bindLoadMoreAction();
    updateBatchDownloadButton();
    refreshAttachmentStatusBadges();
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

function renderImages(images) {
    const visibleImages = images.slice(0, 9);
    return `
        <div class="mt-3 grid grid-cols-2 md:grid-cols-3 gap-2">
            ${visibleImages.map(image => {
                const thumb = image?.thumbnail?.url || image?.large?.url || image?.original?.url || image?.url;
                const full = image?.large?.url || image?.original?.url || thumb;
                const width = image?.thumbnail?.width || image?.large?.width || image?.original?.width || 1;
                const height = image?.thumbnail?.height || image?.large?.height || image?.original?.height || 1;
                if (!thumb) return '';
                const ratio = Math.max(0.6, Math.min(1.6, height / width));
                return `
                    <a href="${escapeHtml(full)}" target="_blank" rel="noopener noreferrer" class="block overflow-hidden rounded-lg border border-slate-200 bg-slate-100 hover:border-blue-300 transition-colors" style="aspect-ratio: 1 / ${ratio.toFixed(3)}">
                        <img src="${escapeHtml(thumb)}" alt="主题图片" loading="lazy" referrerpolicy="no-referrer" class="h-full w-full object-cover">
                    </a>
                `;
            }).join('')}
        </div>
    `;
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
                const attachmentKey = attachmentKeyForFile(file, index);
                const fileId = fileIdForFile(file);
                state.fileMap.set(id, { file, topicId, topicKey, attachmentKey, fileId });
                const name = file.name || file.title || file.file_name || '附件';
                const downloadCount = getDownloadCount(file);
                const fileType = fileTypeForFile(file);
                const fileIcon = fileIconClass(fileType);
                const fileTypeLabel = fileType.toUpperCase();
                return `
                    <div class="zsxq-file-row flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 min-w-0" data-file-id="${escapeHtml(id)}">
                        <div class="min-w-0 flex flex-col gap-1">
                            <div class="min-w-0 flex items-center gap-2 text-sm text-slate-700">
                            <span class="shrink-0 text-[10px] font-black ${fileIcon}">${escapeHtml(fileTypeLabel)}</span>
                            <span class="break-words min-w-0">${escapeHtml(name)}</span>
                            <span class="downloaded-badge hidden shrink-0 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700">下载过</span>
                            </div>
                            ${downloadCount !== null ? `<div class="pl-6 text-xs text-slate-400">下载 ${escapeHtml(String(downloadCount))} 次</div>` : ''}
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

async function refreshAttachmentStatusBadges() {
    if (!state.selectedGroup || !state.currentTopics.length) return;
    const tab = getActiveTabConfig();
    if (!tab) return;

    const topicIds = Array.from(new Set(
        state.currentTopics.map(topic => String(topic.topic_id || '')).filter(Boolean)
    ));
    if (!topicIds.length) return;

    const cachePrefix = `${state.selectedGroup.group_id}:${tab.id}:`;
    const missingTopicIds = topicIds.filter(topicId => !state.attachmentStatusCache.has(`${cachePrefix}${topicId}`));
    if (missingTopicIds.length) {
        try {
            const result = await apiPost('/zsxq/attachments/status', {
                group_id: state.selectedGroup.group_id,
                tab_id: tab.id,
                topic_ids: missingTopicIds,
            });
            const statuses = result?.statuses || {};
            missingTopicIds.forEach(topicId => {
                state.attachmentStatusCache.set(`${cachePrefix}${topicId}`, statuses[topicId] || []);
            });
        } catch (error) {
            console.warn('[ZSXQ] attachment status lookup failed', error);
            return;
        }
    }

    els.topicsList.querySelectorAll('.zsxq-file-row').forEach(row => {
        const entry = state.fileMap.get(row.dataset.fileId);
        if (!entry?.topicId) return;
        const records = state.attachmentStatusCache.get(`${cachePrefix}${entry.topicId}`) || [];
        const matched = matchAttachmentStatus(records, entry);
        const badge = row.querySelector('.downloaded-badge');
        if (!badge) return;
        const downloaded = matched?.download_status === 'downloaded';
        badge.classList.toggle('hidden', !downloaded);
        if (downloaded) {
            badge.title = matched.local_path || '已通过浏览器下载';
        }
    });
}

function matchAttachmentStatus(records, entry) {
    const name = entry.file?.name || entry.file?.title || entry.file?.file_name || '';
    return records.find(record => {
        return (
            (entry.attachmentKey && record.attachment_key === entry.attachmentKey)
            || (entry.fileId && record.file_id === entry.fileId)
            || (name && record.name === name)
        );
    }) || null;
}

function attachmentKeyForFile(file, index) {
    return String(
        file?.file_id
        || file?.image_id
        || file?.id
        || file?.hash_id
        || file?.name
        || file?.url
        || `file-${index}`
    ).slice(0, 255);
}

function fileIdForFile(file) {
    const value = file?.file_id || file?.image_id || file?.id || file?.hash_id;
    return value === undefined || value === null ? '' : String(value).slice(0, 128);
}

function getDownloadCount(file) {
    const candidates = [
        file?.download_count,
        file?.downloads_count,
        file?.downloadCount,
        file?.downloadsCount,
        file?.statistics?.download_count,
    ];
    const value = candidates.find(item => item !== undefined && item !== null && item !== '');
    return value === undefined ? null : value;
}

function currentAttachmentType() {
    state.attachmentType = els.attachmentTypeSelect?.value || state.attachmentType || 'pdf';
    return state.attachmentType;
}

function attachmentTypeText(type) {
    const map = {
        pdf: 'PDF 附件',
        mp3: 'MP3 附件',
        all: '全部附件',
    };
    return map[type] || '匹配附件';
}

function fileTypeForFile(file) {
    const name = String(file?.name || file?.title || file?.file_name || '').toLowerCase();
    const contentType = String(file?.content_type || file?.mime_type || file?.mime || '').toLowerCase();
    if (name.endsWith('.mp3') || contentType.includes('mpeg') || contentType.includes('mp3')) return 'mp3';
    if (name.endsWith('.pdf') || contentType.includes('pdf')) return 'pdf';
    const match = name.match(/\.([a-z0-9]{2,5})(?:$|\?)/);
    return match ? match[1] : 'file';
}

function fileIconClass(type) {
    if (type === 'mp3') return 'text-purple-600';
    if (type === 'pdf') return 'text-red-500';
    return 'text-slate-500';
}

function fileMatchesAttachmentType(entry, type = currentAttachmentType()) {
    if (type === 'all') return true;
    return fileTypeForFile(entry?.file) === type;
}

function formatZsxqTime(value) {
    if (!value) return '';
    let normalized = String(value).trim().replace(/([+-]\d{2})(\d{2})$/, '$1:$2');
    if (/^\d{4}-\d{2}-\d{2}T/.test(normalized) && !/(Z|[+-]\d{2}:\d{2})$/.test(normalized)) {
        normalized = `${normalized}Z`;
    }
    const date = new Date(normalized);
    if (Number.isNaN(date.getTime())) return String(value);

    const parts = new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23',
    }).formatToParts(date).reduce((acc, part) => {
        acc[part.type] = part.value;
        return acc;
    }, {});
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
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
                filename,
                ...downloadMetadataPayload(entry),
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
        markEntryDownloaded(entry, filename);
    } catch (error) {
        alert(`下载失败：${error.message}`);
    } finally {
        setLoading(button, false, originalHTML);
    }
}

function clearAll() {
    resetZsxqWorkspace();
    els.publishTextInput.value = '';
    els.publishFilesInput.value = '';
    setPublishStatus('');
}

async function publishTopic() {
    state.cookie = els.cookieInput.value.trim();
    if (!state.cookie) {
        setPublishStatus('请先填写 Cookie。', 'error');
        return;
    }
    if (!state.selectedGroup) {
        setPublishStatus('请先选择一个星球。', 'error');
        return;
    }

    const text = els.publishTextInput.value.trim();
    const files = Array.from(els.publishFilesInput.files || []);
    if (!text && !files.length) {
        setPublishStatus('正文和附件不能同时为空。', 'error');
        return;
    }

    const tab = getActiveTabConfig();
    const useCurrentTab = els.publishCurrentTabCheckbox.checked && tab?.hashtagId;
    const formData = new FormData();
    formData.append('cookie', state.cookie);
    formData.append('group_id', String(state.selectedGroup.group_id));
    formData.append('text', text);
    if (useCurrentTab) {
        formData.append('hashtag_id', String(tab.hashtagId));
        formData.append('hashtag_title', tab.label);
    }
    files.forEach(file => formData.append('files', file, file.name));

    const originalHTML = els.publishTopicBtn.innerHTML;
    setLoading(els.publishTopicBtn, true, files.length ? `上传中 0/${files.length}` : '发布中...');
    setPublishStatus('正在发布到知识星球，请不要关闭页面...');

    try {
        const response = await fetch(`${apiBaseUrl()}/zsxq/publish`, {
            method: 'POST',
            body: formData,
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            const message = data?.error?.message || data?.message || `HTTP ${response.status}`;
            throw new Error(message);
        }

        const uploadedCount = data?.data?.uploaded?.length || 0;
        els.publishTextInput.value = '';
        els.publishFilesInput.value = '';
        setPublishStatus(`发布成功，已上传 ${uploadedCount} 个附件。`, 'success');
        if (state.selectedGroup) {
            await loadTopics(state.selectedGroup, { resetTabs: false });
        }
    } catch (error) {
        setPublishStatus(`发布失败：${error.message}`, 'error');
    } finally {
        setLoading(els.publishTopicBtn, false, originalHTML);
    }
}

function setPublishStatus(message, type = 'muted') {
    const classes = {
        muted: 'text-slate-500',
        error: 'text-red-600',
        success: 'text-emerald-600',
    };
    els.publishStatus.className = `min-h-5 text-xs ${classes[type] || classes.muted}`;
    els.publishStatus.textContent = message;
}

function updateBatchDownloadButton() {
    const selectedEntries = getSelectedFileEntries();
    const selectedTopics = Array.from(state.fileMap.values()).filter(entry => state.selectedTopicKeys.has(entry.topicKey));
    const typeLabel = attachmentTypeText(currentAttachmentType());
    if (state.fileMap.size > 0) {
        els.batchDownloadBtn.classList.remove('hidden');
        els.selectAllTopicsBtn.classList.remove('hidden');
        els.attachmentTypeControl.classList.remove('hidden');
        els.batchDownloadBtn.innerHTML = `<i class="fas fa-download mr-2"></i>排队下载${typeLabel}（${selectedEntries.length}）`;
        els.batchDownloadBtn.disabled = selectedEntries.length === 0;
        els.batchDownloadBtn.classList.toggle('opacity-50', selectedEntries.length === 0);
        els.batchDownloadBtn.title = selectedEntries.length === 0 && selectedTopics.length > 0
            ? `选中的主题里没有${typeLabel}`
            : '';
    } else {
        els.batchDownloadBtn.classList.add('hidden');
        els.selectAllTopicsBtn.classList.add('hidden');
        els.attachmentTypeControl.classList.add('hidden');
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
    const type = currentAttachmentType();
    return Array.from(state.fileMap.values()).filter(entry => (
        state.selectedTopicKeys.has(entry.topicKey)
        && fileMatchesAttachmentType(entry, type)
    ));
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
            const entry = entries[index];
            const { file } = entry;
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
                        filename,
                        ...downloadMetadataPayload(entry),
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
                markEntryDownloaded(entry, filename);
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

function downloadMetadataPayload(entry) {
    const tab = getActiveTabConfig();
    return {
        file: entry.file,
        topic_id: entry.topicId,
        group_id: state.selectedGroup?.group_id || '',
        tab_id: tab?.id || '',
        attachment_key: entry.attachmentKey || '',
        file_id: entry.fileId || '',
    };
}

function markEntryDownloaded(entry, filename) {
    const tab = getActiveTabConfig();
    if (!state.selectedGroup || !tab || !entry?.topicId) return;
    const cacheKey = `${state.selectedGroup.group_id}:${tab.id}:${entry.topicId}`;
    const records = state.attachmentStatusCache.get(cacheKey) || [];
    const existing = matchAttachmentStatus(records, entry);
    const record = {
        ...(existing || {}),
        topic_id: String(entry.topicId),
        attachment_key: entry.attachmentKey,
        file_id: entry.fileId,
        name: filename,
        download_status: 'downloaded',
        local_path: `browser-download:${filename}`,
    };
    if (existing) {
        Object.assign(existing, record);
    } else {
        records.push(record);
        state.attachmentStatusCache.set(cacheKey, records);
    }
    refreshAttachmentStatusBadges();
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
