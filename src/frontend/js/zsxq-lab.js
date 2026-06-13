const COOKIE_STORAGE_KEY = 'zsxq_cookie';
const COOKIE_PROFILES_STORAGE_KEY = 'zsxq_cookie_profiles';
const ACTIVE_COOKIE_ID_STORAGE_KEY = 'zsxq_active_cookie_id';
const AUTO_SYNC_TEST_INTERVAL_MS = 5 * 60 * 1000;
const AUTO_SYNC_TEST_ROUNDS = 3;
const AUTO_SYNC_TEST_MAX_FILES = 5;
const SYSTEM_SYNC_INTERVAL_MINUTES = 5;
const SYSTEM_SYNC_COUNT = 50;
const SYSTEM_SYNC_MAX_ROUNDS = 3;
const SYSTEM_SYNC_STATUS_POLL_MS = 15000;
const SYSTEM_SYNC_LOG_LINES = 120;

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
    isAutoSyncTesting: false,
    autoSyncTestScope: 'full',
    autoSyncTestRound: 0,
    autoSyncTestTimer: null,
    syncPollTimer: null,
    systemSyncSchedulerStatus: null,
    systemSyncSchedulerPollTimer: null,
    syncRuns: [],
    systemSyncBatches: [],
    systemSyncBatchesError: null,
    systemSyncConfig: null,
    attachmentStatusCache: new Map(),
    attachmentType: 'pdf',
    attachmentViewFilter: 'all',
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
    attachmentViewFilterControl: document.getElementById('attachment-view-filter-control'),
    attachmentViewFilterSelect: document.getElementById('attachment-view-filter-select'),
    attachmentTypeControl: document.getElementById('attachment-type-control'),
    attachmentTypeSelect: document.getElementById('attachment-type-select'),
    copyTopicsJsonBtn: document.getElementById('copy-topics-json-btn'),
    syncCurrentTabBtn: document.getElementById('sync-current-tab-btn'),
    syncAllTabsBtn: document.getElementById('sync-all-tabs-btn'),
    autoSyncTestBtn: document.getElementById('auto-sync-test-btn'),
    autoSyncMemoTestBtn: document.getElementById('auto-sync-memo-test-btn'),
    systemSyncTestConfigBtn: document.getElementById('system-sync-test-config-btn'),
    systemSyncSchedulerPanel: document.getElementById('system-sync-scheduler-panel'),
    systemSchedulerStatusBadge: document.getElementById('system-scheduler-status-badge'),
    systemSchedulerContent: document.getElementById('system-scheduler-content'),
    systemSyncSchedulerRunOnceBtn: document.getElementById('system-sync-scheduler-run-once-btn'),
    systemSyncSchedulerStartBtn: document.getElementById('system-sync-scheduler-start-btn'),
    systemSyncSchedulerStopBtn: document.getElementById('system-sync-scheduler-stop-btn'),
    systemSyncSchedulerRefreshBtn: document.getElementById('system-sync-scheduler-refresh-btn'),
    systemSyncSchedulerOpenLogsBtn: document.getElementById('system-sync-scheduler-open-logs-btn'),
    systemSyncSchedulerLogs: document.getElementById('system-sync-scheduler-logs'),
    systemSyncIntervalInput: document.getElementById('system-sync-interval-input'),
    systemSyncMaxRoundsInput: document.getElementById('system-sync-max-rounds-input'),
    syncStatus: document.getElementById('sync-status'),
    syncBatchesPanel: document.getElementById('sync-batches-panel'),
    publishPanel: document.getElementById('publish-panel'),
    publishTextInput: document.getElementById('publish-text-input'),
    publishCurrentTabCheckbox: document.getElementById('publish-current-tab-checkbox'),
    publishCurrentTabLabel: document.getElementById('publish-current-tab-label'),
    publishFilesInput: document.getElementById('publish-files-input'),
    publishTopicBtn: document.getElementById('publish-topic-btn'),
    publishStatus: document.getElementById('publish-status'),
    hotRankPanel: document.getElementById('hot-rank-panel'),
    hotRankSubtitle: document.getElementById('hot-rank-subtitle'),
    hotRankCount: document.getElementById('hot-rank-count'),
    hotRankList: document.getElementById('hot-rank-list'),
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
    if (els.systemSyncIntervalInput) {
        els.systemSyncIntervalInput.value = String(SYSTEM_SYNC_INTERVAL_MINUTES);
    }
    if (els.systemSyncMaxRoundsInput) {
        els.systemSyncMaxRoundsInput.value = String(SYSTEM_SYNC_MAX_ROUNDS);
    }
    bindEvents();

    loadSystemSyncSchedulerStatus({ render: false })
        .then(() => loadSystemSyncSchedulerPanelsAfterBootstrap())
        .catch(() => {
            renderSystemSyncSchedulerPanel();
            renderSystemSyncBatches();
        });
});

function loadSystemSyncSchedulerPanelsAfterBootstrap() {
    const groupId = getSystemSyncTargetGroupId();
    return Promise.resolve()
        .then(() => {
            if (!groupId) {
                return null;
            }
            return loadSystemSyncBatches(groupId, { render: false });
        })
        .then(() => {
            renderSystemSyncBatches();
            renderSystemSyncSchedulerPanel();
        })
        .catch(() => {
            renderSystemSyncSchedulerPanel();
            renderSystemSyncBatches();
        });
}

function getSystemSyncTargetGroupId() {
    return state.selectedGroup?.group_id || state.systemSyncConfig?.group_id || '';
}

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
    els.attachmentViewFilterSelect.addEventListener('change', () => {
        state.attachmentViewFilter = els.attachmentViewFilterSelect.value || 'all';
        if (['mp3', 'pdf'].includes(state.attachmentViewFilter) && els.attachmentTypeSelect) {
            els.attachmentTypeSelect.value = state.attachmentViewFilter;
            state.attachmentType = state.attachmentViewFilter;
        }
        state.selectedTopicKeys.clear();
        renderTopics(state.currentTopics, activeTabState());
        renderSyncIntro();
    });
    els.copyTopicsJsonBtn?.addEventListener('click', copyLastTopicsJson);
    els.syncCurrentTabBtn?.addEventListener('click', () => syncTabs('current'));
    els.syncAllTabsBtn?.addEventListener('click', () => syncTabs('all'));
    els.autoSyncTestBtn?.addEventListener('click', () => toggleAutoSyncTest('full'));
    els.autoSyncMemoTestBtn?.addEventListener('click', () => toggleAutoSyncTest('memo'));
    els.systemSyncTestConfigBtn?.addEventListener('click', saveSystemSyncTestConfig);
    els.systemSyncSchedulerRunOnceBtn.addEventListener('click', runSystemSyncSchedulerOnce);
    els.systemSyncSchedulerStartBtn.addEventListener('click', startSystemSyncScheduler);
    els.systemSyncSchedulerStopBtn.addEventListener('click', stopSystemSyncScheduler);
    els.systemSyncSchedulerRefreshBtn.addEventListener('click', () => loadSystemSyncSchedulerStatus());
    els.systemSyncSchedulerOpenLogsBtn.addEventListener('click', toggleSystemSyncSchedulerLogs);
    els.publishTopicBtn.addEventListener('click', publishTopic);
    els.topicsList.addEventListener('scroll', handleTopicsScroll);
    els.clearBtn.addEventListener('click', clearAll);
    els.hotRankList.addEventListener('click', (event) => {
        const button = event.target.closest('[data-rank-file-id]');
        if (!button) return;
        focusRankedFile(button.dataset.rankFileId);
    });
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
    stopSyncPolling();
    cancelAutoSyncTest(false);
    state.selectedGroup = null;
    state.groups = [];
    state.fileMap.clear();
    state.topicMap.clear();
    state.selectedTopicKeys.clear();
    state.currentTopics = [];
    state.officialMenus = [];
    state.tabStates.clear();
    state.systemSyncSchedulerStatus = null;
    stopSystemSyncSchedulerPolling();
    state.attachmentStatusCache.clear();
    state.syncRuns = [];
    state.systemSyncBatches = [];
    state.systemSyncConfig = null;
    state.systemSyncBatchesError = null;
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
    els.copyTopicsJsonBtn?.classList.add('hidden');
    els.syncCurrentTabBtn?.classList.add('hidden');
    els.syncAllTabsBtn?.classList.add('hidden');
    els.autoSyncTestBtn?.classList.add('hidden');
    els.autoSyncMemoTestBtn?.classList.add('hidden');
    els.systemSyncTestConfigBtn?.classList.add('hidden');
    els.systemSyncSchedulerPanel.classList.add('hidden');
    els.syncStatus.classList.add('hidden');
    els.syncStatus.innerHTML = '';
    els.syncBatchesPanel.classList.add('hidden');
    els.syncBatchesPanel.innerHTML = '';
    els.topicsTabs.classList.add('hidden');
    els.topicsList.innerHTML = emptyCard('暂无数据');
    renderHotRankList();
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

async function apiGet(endpoint) {
    const response = await fetch(`${apiBaseUrl()}${endpoint}`, {
        method: 'GET',
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
    els.syncCurrentTabBtn?.classList.remove('hidden');
    els.syncAllTabsBtn?.classList.remove('hidden');
    els.autoSyncTestBtn?.classList.remove('hidden');
    els.autoSyncMemoTestBtn?.classList.remove('hidden');
    els.systemSyncTestConfigBtn?.classList.remove('hidden');
    els.systemSyncSchedulerPanel.classList.remove('hidden');
    els.copyTopicsJsonBtn?.classList.add('hidden');
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
    startSystemSyncSchedulerPolling();
    await Promise.all([
        loadSystemSyncSchedulerStatus(),
        loadSyncRuns(group.group_id),
    ]);
    renderSystemSyncSchedulerPanel();

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
            els.copyTopicsJsonBtn?.classList.remove('hidden');

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
    if (!els.copyTopicsJsonBtn) return;
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
    const originalHTML = button?.innerHTML || '';
    state.isSyncing = true;
    if (els.syncCurrentTabBtn) els.syncCurrentTabBtn.disabled = true;
    if (els.syncAllTabsBtn) els.syncAllTabsBtn.disabled = true;
    if (button) setLoading(button, true, scope === 'all' ? '同步全部中...' : '同步中...');
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
        if (result?.queued) {
            renderSyncQueuedResult(result);
            await loadSyncRuns(state.selectedGroup.group_id, { render: false });
            startSyncPolling();
        } else {
            renderSyncResult(result);
            await loadSyncRuns(state.selectedGroup.group_id, { render: false });
        }
        state.attachmentStatusCache.clear();
        refreshAttachmentStatusBadges();
    } catch (error) {
        renderSyncStatus({ type: 'error', message: `同步失败：${error.message}` });
    } finally {
        state.isSyncing = false;
        if (els.syncCurrentTabBtn) els.syncCurrentTabBtn.disabled = false;
        if (els.syncAllTabsBtn) els.syncAllTabsBtn.disabled = false;
        if (button) setLoading(button, false, originalHTML);
    }
}

async function toggleAutoSyncTest(scope = 'full') {
    if (state.isAutoSyncTesting) {
        cancelAutoSyncTest(true);
        return;
    }
    await startAutoSyncTest(scope);
}

async function startAutoSyncTest(scope = 'full') {
    state.cookie = els.cookieInput.value.trim();
    if (!state.cookie) {
        renderSyncStatus({ type: 'error', message: '请先选择或填写 Cookie。' });
        return;
    }
    if (!state.selectedGroup) {
        renderSyncStatus({ type: 'error', message: '请先选择一个星球。' });
        return;
    }
    const jobs = buildAutoSyncTestJobs(scope);
    if (!jobs.length || (scope === 'full' && jobs.length < 2)) {
        const targetText = scope === 'memo' ? '「调研纪要」Tab' : '「外资研报」和「调研纪要」Tab';
        renderSyncStatus({
            type: 'error',
            message: `没有找到${targetText}，无法启动自动测试。`,
            details: `<div class="text-xs text-slate-500">当前可用 Tab：${escapeHtml(getTabDefinitions().map(tab => tab.label).join('、') || '无')}</div>`,
        });
        return;
    }

    state.isAutoSyncTesting = true;
    state.autoSyncTestScope = scope;
    state.autoSyncTestRound = 0;
    updateAutoSyncTestButton();
    await runAutoSyncTestRound();
}

function cancelAutoSyncTest(showMessage = true) {
    if (state.autoSyncTestTimer) {
        clearTimeout(state.autoSyncTestTimer);
        state.autoSyncTestTimer = null;
    }
    const wasRunning = state.isAutoSyncTesting;
    state.isAutoSyncTesting = false;
    state.autoSyncTestScope = 'full';
    state.autoSyncTestRound = 0;
    updateAutoSyncTestButton();
    if (showMessage && wasRunning) {
        renderSyncStatus({ type: 'muted', message: '自动同步测试已停止。' });
    }
}

async function runAutoSyncTestRound() {
    if (!state.isAutoSyncTesting) return;
    const scope = state.autoSyncTestScope || 'full';
    const jobs = buildAutoSyncTestJobs(scope);
    if (!jobs.length || (scope === 'full' && jobs.length < 2)) {
        cancelAutoSyncTest(false);
        renderSyncStatus({ type: 'error', message: '自动测试停止：目标 Tab 不完整。' });
        return;
    }

    state.autoSyncTestRound += 1;
    const round = state.autoSyncTestRound;
    renderSyncStatus({
        type: 'loading',
        title: `${autoSyncScopeLabel(scope)} · 第 ${round}/${AUTO_SYNC_TEST_ROUNDS} 轮`,
        message: `${autoSyncScopeSubmitText(scope)}同一条内容最多下载 ${AUTO_SYNC_TEST_MAX_FILES} 个附件。`,
    });

    try {
        for (const job of jobs) {
            await queueAutoSyncJob(job, round);
            await sleep(800);
        }
        if (scope === 'memo' && jobs[0]?.tab?.id) {
            await activateTopicTab(jobs[0].tab.id, { reset: true });
        }
        await loadSyncRuns(state.selectedGroup.group_id, { render: false });
        startSyncPolling();

        if (round >= AUTO_SYNC_TEST_ROUNDS) {
            state.isAutoSyncTesting = false;
            state.autoSyncTestScope = 'full';
            updateAutoSyncTestButton();
            renderSyncStatus({
                type: 'loading',
                title: `${autoSyncScopeLabel(scope)}已提交完成`,
                message: `${AUTO_SYNC_TEST_ROUNDS} 轮任务都已加入队列。请在同步日志里观察 completed / failed，以及附件下载数量。`,
            });
            return;
        }

        const nextAt = new Date(Date.now() + AUTO_SYNC_TEST_INTERVAL_MS).toLocaleTimeString('zh-CN', { hour12: false });
        renderSyncStatus({
            type: 'loading',
            title: `${autoSyncScopeLabel(scope)} · 第 ${round}/${AUTO_SYNC_TEST_ROUNDS} 轮已提交`,
            message: `下一轮将在 ${nextAt} 提交。${autoSyncScopeNextText(scope)}`,
        });
        state.autoSyncTestTimer = setTimeout(runAutoSyncTestRound, AUTO_SYNC_TEST_INTERVAL_MS);
    } catch (error) {
        cancelAutoSyncTest(false);
        renderSyncStatus({ type: 'error', message: `自动同步测试失败：${error.message}` });
    }
}

async function queueAutoSyncJob(job, round, modePrefix = 'auto') {
    return apiPost('/zsxq/sync', {
        cookie: state.cookie,
        group_id: state.selectedGroup.group_id,
        group_name: state.selectedGroup.name,
        tabs: [job.tab],
        count: Number(els.countInput.value || 20),
        max_pages: 1,
        stop_on_existing: true,
        download_attachments: true,
        download_file_types: [job.fileType],
        download_pdf_only: job.fileType === 'pdf',
        max_download_files: AUTO_SYNC_TEST_MAX_FILES,
        mode: `${modePrefix}_${job.modeCode}_${round}`,
    });
}

async function saveSystemSyncTestConfig() {
    if (!state.selectedGroup) {
        renderSyncStatus({ type: 'error', message: '请先选择一个星球。' });
        return;
    }
    const jobs = buildAutoSyncTestJobs('full');
    if (jobs.length < 2) {
        renderSyncStatus({
            type: 'error',
            message: '没有同时找到「外资研报」和「调研纪要」Tab，无法保存系统计划配置。',
            details: `<div class="text-xs text-slate-500">当前可用 Tab：${escapeHtml(getTabDefinitions().map(tab => tab.label).join('、') || '无')}</div>`,
        });
        return;
    }

    const systemIntervalMinutes = parsePositiveInt(els.systemSyncIntervalInput?.value, SYSTEM_SYNC_INTERVAL_MINUTES, 1);
    const maxRoundsRaw = parsePositiveIntOrZero(els.systemSyncMaxRoundsInput?.value, SYSTEM_SYNC_MAX_ROUNDS, 0);

    const originalHTML = els.systemSyncTestConfigBtn?.innerHTML || '';
    if (els.systemSyncTestConfigBtn) setLoading(els.systemSyncTestConfigBtn, true, '保存中...');
    renderSyncStatus({
        type: 'loading',
        title: '系统计划配置',
        message: `正在保存本机系统计划任务配置。真实观察模式会每 ${systemIntervalMinutes} 分钟执行一次，每批获取 ${SYSTEM_SYNC_COUNT} 条主题，每条最多 ${AUTO_SYNC_TEST_MAX_FILES} 个附件。`,
    });

    try {
        const payload = {
            group_id: state.selectedGroup.group_id,
            group_name: state.selectedGroup.name,
            count: SYSTEM_SYNC_COUNT,
            max_pages: 1,
            stop_on_existing: true,
            download_attachments: true,
            max_download_files: AUTO_SYNC_TEST_MAX_FILES,
            interval_minutes: systemIntervalMinutes,
            max_rounds: maxRoundsRaw,
            jobs: jobs.map(job => ({
                key: job.key,
                mode_code: job.modeCode,
                file_type: job.fileType,
                tab: job.tab,
            })),
        };
        if (state.cookie) {
            payload.cookie = state.cookie;
        }
        const result = await apiPost('/zsxq/system-sync-test/config', payload);
        const config = result?.config || {};
        renderSyncStatus({
            type: 'success',
            title: '系统计划配置已保存',
            message: config.cookie_env_configured
                ? '配置已写入本机 runtime。Cookie 将从环境变量读取，现在可以启动项目内 ZSXQ 调度器。'
                : `配置已写入本机 runtime。请先在 .env 或系统环境里配置 ${config.cookie_env_name || 'ZSXQ_SYNC_TEST_COOKIE'}。`,
            details: `
                <div class="text-xs text-slate-500 leading-6">
                    <div>配置文件：<span class="font-mono">${escapeHtml(config.path || '-')}</span></div>
                    <div>Cookie 来源：<span class="font-mono">${escapeHtml(config.cookie_env_name || 'ZSXQ_SYNC_TEST_COOKIE')}</span>${config.cookie_env_configured ? ' · 已检测到' : ' · 未检测到'}</div>
                    <div>任务范围：外资研报 PDF + 调研纪要 MP3；每 ${config.interval_minutes || systemIntervalMinutes} 分钟 1 批；每批获取 ${SYSTEM_SYNC_COUNT} 条主题；每条内容最多 ${AUTO_SYNC_TEST_MAX_FILES} 个附件。</div>
                    <div>调度轮次：${formatConfigMaxRounds(config.max_rounds ?? maxRoundsRaw)}</div>
                </div>
            `,
        });
        state.systemSyncConfig = config;
        state.systemSyncBatchesError = null;
        applySystemSyncRuntimeConfig(config);
        await loadSystemSyncSchedulerStatus({ render: false });
        if (state.selectedGroup?.group_id) {
            await loadSystemSyncBatches(state.selectedGroup.group_id, { render: false });
        }
        renderSystemSyncBatches();
        renderSystemSyncSchedulerPanel();
    } catch (error) {
        renderSyncStatus({ type: 'error', message: `系统计划配置保存失败：${error.message}` });
    } finally {
        if (els.systemSyncTestConfigBtn) setLoading(els.systemSyncTestConfigBtn, false, originalHTML);
    }
}

function parsePositiveInt(value, fallback, minValue = 1) {
    if (value === null || value === undefined) {
        return fallback;
    }
    const text = String(value).trim();
    if (text === '') {
        return fallback;
    }
    const number = Number(value);
    if (!Number.isFinite(number)) {
        return fallback;
    }
    return Math.max(minValue, Math.min(Math.round(number), 1440));
}

function parsePositiveIntOrZero(value, fallback, minValue = 0) {
    if (value === null || value === undefined) {
        return fallback;
    }
    const text = String(value).trim();
    if (text === '') {
        return 0;
    }
    const number = Number(value);
    if (!Number.isFinite(number)) {
        return fallback;
    }
    const fixed = Math.round(number);
    if (fixed <= minValue) {
        return 0;
    }
    return Math.min(fixed, 100);
}

function formatConfigMaxRounds(value) {
    if (!value || Number(value) <= 0) {
        return '持续运行';
    }
    return `前 ${Number(value)} 轮`;
}

function applySystemSyncRuntimeConfig(config = {}) {
    if (!config || !els.systemSyncIntervalInput || !els.systemSyncMaxRoundsInput) {
        return;
    }
    const interval = Number(config.interval_minutes || SYSTEM_SYNC_INTERVAL_MINUTES);
    els.systemSyncIntervalInput.value = interval > 0 ? String(interval) : String(SYSTEM_SYNC_INTERVAL_MINUTES);
    const maxRounds = config.max_rounds;
    if (maxRounds === 0 || maxRounds === null || maxRounds === undefined) {
        els.systemSyncMaxRoundsInput.value = '';
    } else {
        els.systemSyncMaxRoundsInput.value = String(maxRounds);
    }
}

function buildAutoSyncTestJobs(scope = 'full') {
    const tabs = getTabDefinitions();
    const foreignReportTab = tabs.find(tab => tab.label.includes('外资研报'));
    const researchMemoTab = tabs.find(tab => tab.label.includes('调研纪要'));
    const jobs = [
        foreignReportTab ? { key: 'foreign_report', modeCode: 'fr', tab: foreignReportTab, fileType: 'pdf' } : null,
        researchMemoTab ? { key: 'research_memo_mp3', modeCode: 'rm', tab: researchMemoTab, fileType: 'mp3' } : null,
    ].filter(Boolean);
    return scope === 'memo' ? jobs.filter(job => job.key === 'research_memo_mp3') : jobs;
}

function autoSyncScopeLabel(scope) {
    return scope === 'memo' ? '调研纪要小范围测试' : '自动同步测试';
}

function autoSyncScopeSubmitText(scope) {
    return scope === 'memo'
        ? '正在提交调研纪要 MP3，'
        : '正在提交外资研报 PDF 与调研纪要 MP3，';
}

function autoSyncScopeNextText(scope) {
    return scope === 'memo'
        ? '当前测试仍会每轮只同步调研纪要 MP3。'
        : '当前测试仍会每轮同步外资研报 PDF 和调研纪要 MP3。';
}

function updateAutoSyncTestButton() {
    if (!els.autoSyncTestBtn || !els.autoSyncMemoTestBtn) return;
    const activeScope = state.autoSyncTestScope || 'full';
    els.autoSyncTestBtn.innerHTML = state.isAutoSyncTesting && activeScope === 'full'
        ? '<i class="fas fa-stop mr-2"></i>停止自动测试'
        : `<i class="fas fa-clock mr-2"></i>自动测试 ${AUTO_SYNC_TEST_ROUNDS} 轮`;
    els.autoSyncMemoTestBtn.innerHTML = state.isAutoSyncTesting && activeScope === 'memo'
        ? '<i class="fas fa-stop mr-2"></i>停止小范围测试'
        : `<i class="fas fa-music mr-2"></i>调研纪要测试 ${AUTO_SYNC_TEST_ROUNDS} 轮`;
    els.autoSyncTestBtn.classList.toggle('bg-red-50', state.isAutoSyncTesting && activeScope === 'full');
    els.autoSyncTestBtn.classList.toggle('border-red-300', state.isAutoSyncTesting && activeScope === 'full');
    els.autoSyncTestBtn.classList.toggle('text-red-700', state.isAutoSyncTesting && activeScope === 'full');
    els.autoSyncTestBtn.classList.toggle('bg-amber-50', !state.isAutoSyncTesting || activeScope !== 'full');
    els.autoSyncTestBtn.classList.toggle('border-amber-300', !state.isAutoSyncTesting || activeScope !== 'full');
    els.autoSyncTestBtn.classList.toggle('text-amber-700', !state.isAutoSyncTesting || activeScope !== 'full');
    els.autoSyncMemoTestBtn.disabled = state.isAutoSyncTesting && activeScope !== 'memo';
    els.autoSyncTestBtn.disabled = state.isAutoSyncTesting && activeScope !== 'full';
    els.autoSyncMemoTestBtn.classList.toggle('opacity-50', els.autoSyncMemoTestBtn.disabled);
    els.autoSyncTestBtn.classList.toggle('opacity-50', els.autoSyncTestBtn.disabled);
    els.autoSyncMemoTestBtn.classList.toggle('bg-red-50', state.isAutoSyncTesting && activeScope === 'memo');
    els.autoSyncMemoTestBtn.classList.toggle('border-red-300', state.isAutoSyncTesting && activeScope === 'memo');
    els.autoSyncMemoTestBtn.classList.toggle('text-red-700', state.isAutoSyncTesting && activeScope === 'memo');
    els.autoSyncMemoTestBtn.classList.toggle('bg-sky-50', !state.isAutoSyncTesting || activeScope !== 'memo');
    els.autoSyncMemoTestBtn.classList.toggle('border-sky-300', !state.isAutoSyncTesting || activeScope !== 'memo');
    els.autoSyncMemoTestBtn.classList.toggle('text-sky-700', !state.isAutoSyncTesting || activeScope !== 'memo');
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function renderSyncQueuedResult(result) {
    const run = result?.run || {};
    renderSyncStatus({
        type: 'loading',
        title: '已加入同步队列',
        message: `Run ID：${run.id || '-'} · 状态：${syncStatusLabel(run.status)}。后台 worker 会继续同步并下载附件，页面会自动刷新日志。`,
        details: run.requested_tabs?.length
            ? `<div class="text-xs text-slate-500">范围：${escapeHtml(run.requested_tabs.map(tab => tab.label || tab.id).join('、'))}</div>`
            : '',
    });
}

function startSyncPolling() {
    stopSyncPolling();
    if (!state.selectedGroup) return;
    state.syncPollTimer = setInterval(async () => {
        await loadSyncRuns(state.selectedGroup.group_id, { render: true });
        const stillActive = state.syncRuns.some(run => ['queued', 'running'].includes(run.status));
        if (!stillActive) {
            stopSyncPolling();
            state.attachmentStatusCache.clear();
            refreshAttachmentStatusBadges();
        }
    }, 5000);
}

function stopSyncPolling() {
    if (state.syncPollTimer) {
        clearInterval(state.syncPollTimer);
        state.syncPollTimer = null;
    }
}

async function loadSyncRuns(groupId, options = {}) {
    try {
        const result = await apiPost('/zsxq/sync/runs', {
            group_id: groupId,
            limit: 20,
        });
        state.syncRuns = result?.runs || [];
        await loadSystemSyncBatches(groupId, { render: false });
        if (state.syncRuns.some(run => ['queued', 'running'].includes(run.status)) && !state.syncPollTimer) {
            startSyncPolling();
        }
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
        state.systemSyncBatches = [];
        if (options.render === false) {
            return;
        }
        renderSyncIntro();
        renderSystemSyncBatches();
    }
}

async function loadSystemSyncBatches(groupId, options = {}) {
    const targetGroupId = groupId || getSystemSyncTargetGroupId();
    try {
        const [batchResult, configResult] = await Promise.all([
            apiPost('/zsxq/system-sync-test/batches', {
                group_id: targetGroupId || null,
                limit: 8,
            }),
            fetch(`${apiBaseUrl()}/zsxq/system-sync-test/config`).then(async response => {
                const data = await response.json();
                if (!response.ok || !data.success) throw new Error(data?.error?.message || `HTTP ${response.status}`);
                return data.data;
            }),
        ]);
        state.systemSyncBatches = batchResult?.batches || [];
        state.systemSyncBatchesError = batchResult?.error || null;
        state.systemSyncConfig = configResult?.config || null;
        applySystemSyncRuntimeConfig(state.systemSyncConfig);
    } catch {
        state.systemSyncBatches = [];
        state.systemSyncConfig = null;
        state.systemSyncBatchesError = '获取系统批次失败，请稍后重试。';
    }
    if (options.render !== false) {
        renderSystemSyncBatches();
    }
}

async function loadSystemSyncSchedulerStatus(options = {}) {
    try {
        const result = await apiGet('/zsxq/system-sync-scheduler/status');
        state.systemSyncSchedulerStatus = result?.scheduler || null;
        applySystemSyncRuntimeConfig(result?.scheduler?.config || null);
        if (!state.systemSyncConfig && result?.scheduler?.config) {
            state.systemSyncConfig = result.scheduler.config;
        }
        if (options.render !== false) {
            renderSystemSyncSchedulerPanel();
        }
    } catch {
        state.systemSyncSchedulerStatus = {
            running: false,
            exists: false,
            message: '状态查询失败',
        };
        if (options.render !== false) {
            renderSystemSyncSchedulerPanel();
        }
    }
}

async function startSystemSyncScheduler() {
    const original = els.systemSyncSchedulerStartBtn.innerHTML;
    setLoading(els.systemSyncSchedulerStartBtn, true, '启动中...');
    try {
        const result = await apiPost('/zsxq/system-sync-scheduler/start', {});
        const scheduler = result?.scheduler || null;
        state.systemSyncSchedulerStatus = scheduler;
        renderSyncStatus({
            type: scheduler?.started ? 'success' : 'muted',
            title: '系统计划任务',
            message: scheduler?.message || '启动请求已提交。',
        });
        await loadSystemSyncSchedulerStatus({ render: false });
        const targetGroupId = getSystemSyncTargetGroupId();
        if (targetGroupId) {
            await loadSystemSyncBatches(targetGroupId, { render: false });
            renderSystemSyncBatches();
        }
    } catch (error) {
        renderSyncStatus({ type: 'error', message: `启动计划任务失败：${error.message}` });
    } finally {
        setLoading(els.systemSyncSchedulerStartBtn, false, original);
        startSystemSyncSchedulerPolling();
        renderSystemSyncSchedulerPanel();
    }
}

async function stopSystemSyncScheduler() {
    const original = els.systemSyncSchedulerStopBtn.innerHTML;
    setLoading(els.systemSyncSchedulerStopBtn, true, '停止中...');
    try {
        const result = await apiPost('/zsxq/system-sync-scheduler/stop', {});
        const scheduler = result?.scheduler || null;
        const isStopped = Boolean(scheduler?.stopped);
        const isRunning = Boolean(scheduler?.running);
        const showType = isStopped || !isRunning ? 'success' : 'muted';
        state.systemSyncSchedulerStatus = scheduler;
        renderSyncStatus({
            type: showType,
            title: '系统计划任务',
            message: scheduler?.message || '停止请求已提交。',
        });
        await loadSystemSyncSchedulerStatus({ render: false });
        const targetGroupId = getSystemSyncTargetGroupId();
        if (targetGroupId) {
            await loadSystemSyncBatches(targetGroupId, { render: false });
            renderSystemSyncBatches();
        }
    } catch (error) {
        renderSyncStatus({ type: 'error', message: `停止计划任务失败：${error.message}` });
    } finally {
        setLoading(els.systemSyncSchedulerStopBtn, false, original);
        renderSystemSyncSchedulerPanel();
    }
}

async function runSystemSyncSchedulerOnce() {
    const original = els.systemSyncSchedulerRunOnceBtn.innerHTML;
    setLoading(els.systemSyncSchedulerRunOnceBtn, true, '执行中...');
    try {
        const result = await apiPost('/zsxq/system-sync-scheduler/run-once', {});
        const scheduler = result?.scheduler || {};
        const queued = Boolean(scheduler?.queued);
        const skipped = Boolean(scheduler?.skipped);
        const statusType = queued ? 'success' : (skipped ? 'muted' : 'muted');
        const reasonLabel = skipped && scheduler.reason ? `（原因：${scheduler.reason}）` : '';
        const title = queued ? '立即执行已触发' : (skipped ? '立即执行未提交' : '立即执行结果');
        renderSyncStatus({
            type: statusType,
            title,
            message: `${scheduler.message || '触发请求已提交。'}${reasonLabel}`,
        });
        await loadSystemSyncSchedulerStatus({ render: false });
        const targetGroupId = getSystemSyncTargetGroupId();
        if (targetGroupId) {
            await loadSystemSyncBatches(targetGroupId, { render: false });
            renderSystemSyncBatches();
        }
    } catch (error) {
        renderSyncStatus({ type: 'error', message: `立即执行失败：${error.message}` });
    } finally {
        setLoading(els.systemSyncSchedulerRunOnceBtn, false, original);
        renderSystemSyncSchedulerPanel();
    }
}

function startSystemSyncSchedulerPolling() {
    stopSystemSyncSchedulerPolling();
    state.systemSyncSchedulerPollTimer = setInterval(() => {
        if (!getSystemSyncTargetGroupId() && !state.systemSyncSchedulerStatus?.running) {
            return;
        }
        loadSystemSyncSchedulerStatus({ render: false }).then(() => {
            renderSystemSyncSchedulerPanel();
            renderSystemSyncBatches();
            if (els.systemSyncSchedulerLogs && !els.systemSyncSchedulerLogs.classList.contains('hidden')) {
                loadSystemSyncSchedulerLogs().then(logs => {
                    if (els.systemSyncSchedulerLogs && !els.systemSyncSchedulerLogs.classList.contains('hidden')) {
                        els.systemSyncSchedulerLogs.textContent = logs;
                    }
                });
            }
        });
    }, SYSTEM_SYNC_STATUS_POLL_MS);
}

async function loadSystemSyncSchedulerLogs() {
    if (!els.systemSyncSchedulerLogs) {
        return '（日志面板未挂载）';
    }
    try {
        const response = await fetch(`${apiBaseUrl()}/zsxq/system-sync-scheduler/logs?lines=${SYSTEM_SYNC_LOG_LINES}`);
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data?.error?.message || `HTTP ${response.status}`);
        }
        const raw = data?.data?.raw;
        return typeof raw === 'string' && raw.trim().length > 0 ? raw : '（暂无日志）';
    } catch (error) {
        return `读取日志失败：${error.message}`;
    }
}

async function toggleSystemSyncSchedulerLogs() {
    if (!els.systemSyncSchedulerLogs || !els.systemSyncSchedulerOpenLogsBtn) {
        return;
    }
    if (els.systemSyncSchedulerLogs.classList.contains('hidden')) {
        els.systemSyncSchedulerLogs.classList.remove('hidden');
        els.systemSyncSchedulerOpenLogsBtn.disabled = true;
        els.systemSyncSchedulerOpenLogsBtn.innerHTML = '<i class="fas fa-list-ul mr-2"></i>加载日志...';
        els.systemSyncSchedulerLogs.textContent = '正在读取日志...';
        const logs = await loadSystemSyncSchedulerLogs();
        els.systemSyncSchedulerLogs.textContent = logs;
        els.systemSyncSchedulerOpenLogsBtn.innerHTML = '<i class="fas fa-list-ul mr-2"></i>隐藏最近日志';
        els.systemSyncSchedulerOpenLogsBtn.disabled = false;
        return;
    }
    els.systemSyncSchedulerLogs.classList.add('hidden');
    els.systemSyncSchedulerOpenLogsBtn.innerHTML = '<i class="fas fa-list-ul mr-2"></i>查看最近日志';
}

function stopSystemSyncSchedulerPolling() {
    if (state.systemSyncSchedulerPollTimer) {
        clearInterval(state.systemSyncSchedulerPollTimer);
        state.systemSyncSchedulerPollTimer = null;
    }
}

function renderSystemSyncSchedulerPanel() {
    if (!els.systemSyncSchedulerPanel) return;

    const scheduler = state.systemSyncSchedulerStatus || {};
    const config = scheduler.config || state.systemSyncConfig || {};
    const running = Boolean(scheduler.running);
    const hasConfig = Boolean(config?.exists || config?.group_id);

    els.systemSyncSchedulerPanel.classList.remove('hidden');
    els.systemSchedulerStatusBadge.textContent = running ? '运行中' : '未运行';
    els.systemSchedulerStatusBadge.className = `rounded-full px-2 py-1 text-[10px] font-black ${running ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'}`;

    if (!hasConfig) {
        els.systemSyncSchedulerStartBtn.disabled = true;
        els.systemSyncSchedulerStopBtn.disabled = true;
        els.systemSyncSchedulerRunOnceBtn.disabled = true;
        if (els.systemSyncSchedulerOpenLogsBtn) {
            els.systemSyncSchedulerOpenLogsBtn.classList.add('hidden');
        }
        const detail = scheduler.message || '未检测到系统计划配置，请先点击“保存系统计划配置”。';
        els.systemSchedulerContent.innerHTML = `<div>当前状态：${escapeHtml(detail)}</div>`;
        return;
    }

    const interval = scheduler.interval_minutes || config.interval_minutes || SYSTEM_SYNC_INTERVAL_MINUTES;
    const count = config.count || SYSTEM_SYNC_COUNT;
    const maxFiles = config.max_download_files || AUTO_SYNC_TEST_MAX_FILES;
    const lastTrigger = scheduler.last_trigger_at ? formatZsxqTime(scheduler.last_trigger_at) : '尚未触发';
    const nextRun = scheduler.next_run ? formatZsxqTime(scheduler.next_run) : '尚未预测';
    const roundsText = scheduler.continuous ? `已运行 ${scheduler.rounds_completed || 0} 轮 · 持续模式` : `进度 ${scheduler.rounds_completed || 0}/${scheduler.max_rounds || 0} 轮`;
    els.systemSyncSchedulerStartBtn.disabled = running;
    els.systemSyncSchedulerStopBtn.disabled = !running;
    els.systemSyncSchedulerRunOnceBtn.disabled = false;
    els.systemSyncSchedulerStartBtn.classList.toggle('opacity-50', running);
    els.systemSyncSchedulerStopBtn.classList.toggle('opacity-50', !running);

    els.systemSyncSchedulerPanel.classList.toggle('border-emerald-200', running);
    els.systemSyncSchedulerPanel.classList.toggle('border-slate-200', !running);

    els.systemSchedulerContent.innerHTML = `
        <div class="space-y-1">
            <div>进程状态：${escapeHtml(scheduler.running ? '已启动' : '未启动')} · PID ${escapeHtml(String(scheduler.pid || '-'))}</div>
            <div>调度配置：${escapeHtml(String(interval))} 分钟一次 · 每批 ${escapeHtml(String(count))} 条 · 每条最多 ${escapeHtml(String(maxFiles))} 个附件</div>
            <div>本轮状态：${escapeHtml(roundsText)} · ${escapeHtml(scheduler.message || '')}</div>
            <div>上次触发：${escapeHtml(lastTrigger)} · 下一次：${escapeHtml(nextRun)}</div>
            <div>日志：<span class="font-mono break-all">${escapeHtml(scheduler.log_path || '-')}</span></div>
        </div>
    `;
    if (els.systemSyncSchedulerOpenLogsBtn) {
        els.systemSyncSchedulerOpenLogsBtn.classList.remove('hidden');
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
    const summary = latest.result?.partial || latest.result || {};
    renderSyncStatus({
        type: latest.status === 'failed' ? 'error' : 'muted',
        title: '同步日志',
        message: `最近一次：${formatZsxqTime(latest.completed_at || latest.created_at)} · ${syncStatusLabel(latest.status)} · 新增 ${summary.topics_new || 0} · 附件下载 ${summary.attachments_downloaded || 0}`,
        details: renderSyncRunsList(state.syncRuns.slice(0, 10)),
    });
    renderSystemSyncBatches();
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
    renderSystemSyncBatches();
}

function renderSyncRunsList(runs) {
    if (!runs.length) return '';
    return `
        <div role="list" class="space-y-2">
            ${runs.map(run => {
                const runSummary = run.result?.partial || run.result || {};
                const label = run.tab_label || (run.requested_tabs?.length ? `${run.requested_tabs.length} 个 tab` : '未知范围');
                const statusClass = run.status === 'failed' ? 'text-red-600' : run.status === 'completed' ? 'text-emerald-700' : 'text-blue-600';
                const limitReached = Boolean(runSummary.download_limit_reached);
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
                        ${limitReached ? '<div class="mt-1 text-amber-600">有内容达到单条附件上限。</div>' : ''}
                        ${run.error_message ? `<div class="mt-1 text-red-600">${escapeHtml(run.error_message)}</div>` : ''}
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function renderSystemSyncBatches() {
    if (!els.syncBatchesPanel) return;
    const config = state.systemSyncConfig || {};
    const batches = state.systemSyncBatches || [];
    const batchError = state.systemSyncBatchesError;
    els.syncBatchesPanel.classList.remove('hidden');
    els.syncBatchesPanel.innerHTML = `
        <div class="flex items-start justify-between gap-3">
            <div>
                <div class="zsxq-side-title">系统测试批次</div>
                <p class="text-xs text-slate-400 mt-1">
                    ${escapeHtml(systemSyncConfigSummary(config))}
                </p>
            </div>
            <span class="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-black text-slate-500">${escapeHtml(String(batches.length))}</span>
        </div>
        ${batchError ? `<div class="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-700">${escapeHtml(batchError)}</div>` : ''}
        <div class="mt-3 space-y-2">
            ${batches.length ? batches.map(renderSystemSyncBatchCard).join('') : `
                <div class="rounded-lg border border-dashed border-slate-200 p-4 text-center text-xs text-slate-400">
                    暂无系统计划批次。保存配置并启动调度器后会出现在这里。
                </div>
            `}
        </div>
    `;
    if (els.systemSyncSchedulerPanel) {
        renderSystemSyncSchedulerPanel();
    }
}

function systemSyncConfigSummary(config) {
    if (!config || !config.exists) return '未保存系统计划配置';
    const maxFiles = config.max_download_files || AUTO_SYNC_TEST_MAX_FILES;
    const interval = config.interval_minutes || SYSTEM_SYNC_INTERVAL_MINUTES;
    const count = config.count || SYSTEM_SYNC_COUNT;
    const maxRounds = config.max_rounds;
    const done = config.rounds_completed || 0;
    const roundsText = maxRounds ? `${done}/${maxRounds} 轮` : `已运行 ${done} 轮 · 持续运行`;
    const cookie = config.cookie_env_configured ? 'Cookie 已配置' : 'Cookie 未配置';
    const complete = config.testing_complete ? ' · 已达测试轮数' : '';
    return `${interval} 分钟一次 · 每批 ${count} 条 · 每条最多 ${maxFiles} 个附件 · ${roundsText} · ${cookie}${complete}`;
}

function renderSystemSyncBatchCard(batch) {
    const summary = batch.summary || {};
    const statusClass = batch.status === 'failed'
        ? 'text-red-600'
        : batch.status === 'completed'
            ? 'text-emerald-700'
            : 'text-blue-600';
    const roundText = batch.round && batch.max_rounds
        ? `第 ${batch.round}/${batch.max_rounds} 轮`
        : batch.round && batch.continuous
            ? `第 ${batch.round} 轮`
        : '历史批次';
    const failedAttachments = (batch.runs || []).flatMap(run => run.failed_attachments || []);
    return `
        <div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-3 text-xs text-slate-600">
            <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="font-black text-slate-800">${escapeHtml(roundText)}</div>
                <div class="${statusClass} font-bold">${escapeHtml(syncStatusLabel(batch.status))}</div>
            </div>
            <div class="mt-1 text-slate-400 font-mono">${escapeHtml(batch.batch_id || batch.attempt_id || '-')}</div>
            <div class="mt-2 grid grid-cols-2 gap-2">
                <div class="rounded-md bg-white border border-slate-200 px-2 py-1">新增 ${escapeHtml(String(summary.topics_new || 0))}</div>
                <div class="rounded-md bg-white border border-slate-200 px-2 py-1">更新 ${escapeHtml(String(summary.topics_updated || 0))}</div>
                <div class="rounded-md bg-white border border-slate-200 px-2 py-1">下载 ${escapeHtml(String(summary.attachments_downloaded || 0))}</div>
                <div class="rounded-md bg-white border border-slate-200 px-2 py-1">失败 ${escapeHtml(String(summary.attachments_failed || 0))}</div>
            </div>
            <div class="mt-2 text-slate-500">
                ${escapeHtml(formatZsxqTime(batch.completed_at || batch.created_at))}
                ${summary.download_limit_reached ? ' · 有内容达到单条附件上限' : ''}
            </div>
            ${renderSystemSyncBatchRuns(batch.runs || [])}
            ${failedAttachments.length ? renderFailedAttachments(failedAttachments) : ''}
        </div>
    `;
}

function renderSystemSyncBatchRuns(runs) {
    if (!runs.length) return '';
    return `
        <div class="mt-2 space-y-1">
            ${runs.map(run => {
                const result = run.result || {};
                const tabs = run.requested_tabs || [];
                const label = run.tab_label || tabs.map(tab => tab.label || tab.id).join(' + ') || '未知范围';
                return `
                    <div class="rounded-md border border-slate-200 bg-white px-2 py-1.5">
                        <div class="flex flex-wrap items-center justify-between gap-2">
                            <span class="font-bold">${escapeHtml(label)}</span>
                            <span>${escapeHtml(syncStatusLabel(run.status))}</span>
                        </div>
                        <div class="mt-1 text-slate-500">
                            Run ${escapeHtml(shortId(run.id))} · 附件 ${escapeHtml(String(result.attachments_seen || 0))}
                            · 下载 ${escapeHtml(String(result.attachments_downloaded || 0))}
                            · 失败 ${escapeHtml(String(result.attachments_failed || 0))}
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function renderFailedAttachments(items) {
    return `
        <div class="mt-2 rounded-md border border-red-200 bg-red-50 px-2 py-2 text-red-700">
            <div class="font-black">失败附件</div>
            <div class="mt-1 space-y-1">
                ${items.slice(0, 6).map(item => `
                    <div>
                        <div class="font-bold">${escapeHtml(item.name || item.file_id || item.attachment_key || '未知附件')}</div>
                        <div class="text-red-600">${escapeHtml(item.error_message || '未记录失败原因')}</div>
                    </div>
                `).join('')}
            </div>
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
                    ${tab.download_limit_reached || tab.stopped_by_download_limit ? '<div class="mt-1 text-amber-600">有内容达到单条附件上限。</div>' : ''}
                </div>
            `).join('')}
        </div>
    `;
}

function syncStatusLabel(status) {
    const map = {
        queued: '排队中',
        completed: '成功',
        failed: '失败',
        running: '进行中',
    };
    return map[status] || status || '未知';
}

function shortId(value) {
    const text = String(value || '');
    return text.length > 8 ? text.slice(0, 8) : (text || '-');
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
            await activateTopicTab(tabId);
        });
    });
    updatePublishTabState();
}

async function activateTopicTab(tabId, options = {}) {
    const targetTab = getTabDefinitions().find(tab => tab.id === tabId);
    if (!targetTab) return false;

    state.activeTab = targetTab.id;
    if (!targetTab.hashtagId) els.scopeSelect.value = targetTab.scope || 'all';
    state.selectedTopicKeys.clear();

    const tabState = getTabState(targetTab.id);
    if (options.reset || !tabState.loaded) {
        await loadActiveTabPage({ reset: true });
    } else {
        renderActiveTab();
    }
    updatePublishTabState();
    return true;
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
    const viewFilter = currentAttachmentViewFilter();
    const visibleTopics = filterTopicsByAttachmentView(topics, viewFilter);
    if (!visibleTopics.length) {
        const message = tabState?.loading
            ? '正在加载...'
            : (tabState?.hasMore ? '当前页还没有匹配内容，继续下滑会尝试加载更多。' : attachmentViewEmptyText(viewFilter));
        els.topicsList.innerHTML = emptyCard(message, tabState?.loading ? 'muted' : 'warning') + renderLoadMoreFooter(tabState);
        state.fileMap.clear();
        state.topicMap.clear();
        updateBatchDownloadButton();
        updateSelectAllButton();
        renderHotRankList();
        bindLoadMoreAction();
        return;
    }

    state.fileMap.clear();
    state.topicMap.clear();
    els.topicsList.innerHTML = visibleTopics.map(topic => {
        const topicKey = topicKeyForRender(topic);
        state.topicMap.set(topicKey, topic);
        const content = extractContent(topic);
        const title = topic.title || topic?.talk?.text?.slice(0, 48) || topic?.article?.title || '无标题主题';
        const author = topic?.owner?.name || topic?.talk?.owner?.name || '未知作者';
        const created = topic.create_time || topic?.talk?.create_time || '';
        const createdText = formatZsxqTime(created);
        const allFiles = topicFiles(topic);
        const files = filterFilesByAttachmentView(allFiles, viewFilter);
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
    renderHotRankList();
    refreshAttachmentStatusBadges();
}

function activeTabState() {
    return state.tabStates.get(state.activeTab) || null;
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

function currentAttachmentViewFilter() {
    state.attachmentViewFilter = els.attachmentViewFilterSelect?.value || state.attachmentViewFilter || 'all';
    return state.attachmentViewFilter;
}

function filterTopicsByAttachmentView(topics, filter = currentAttachmentViewFilter()) {
    if (filter === 'all') return topics;
    return topics.filter(topic => topicFiles(topic).some(file => fileTypeForFile(file) === filter));
}

function filterFilesByAttachmentView(files, filter = currentAttachmentViewFilter()) {
    if (filter === 'all') return files;
    return files.filter(file => fileTypeForFile(file) === filter);
}

function attachmentViewEmptyText(filter = currentAttachmentViewFilter()) {
    const map = {
        mp3: '当前列表里没有 MP3 附件。',
        pdf: '当前列表里没有 PDF 附件。',
        all: '这个筛选下没有主题。',
    };
    return map[filter] || map.all;
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
                            <span class="download-failed-badge hidden shrink-0 rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-[10px] font-bold text-red-600">下载失败</span>
                            </div>
                            ${downloadCount !== null ? `<div class="pl-6 text-xs text-slate-400">下载 ${escapeHtml(String(downloadCount))} 次</div>` : ''}
                            <div class="download-error-text hidden pl-6 text-xs text-red-500"></div>
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

function renderHotRankList() {
    if (!els.hotRankList) return;
    const entries = buildHotRankEntries();
    const filter = currentAttachmentViewFilter();
    const filterLabel = filter === 'all' ? '全部附件' : attachmentTypeText(filter);
    els.hotRankSubtitle.textContent = `${filterLabel} · 按下载次数排序`;
    els.hotRankCount.textContent = String(entries.length);

    if (!entries.length) {
        els.hotRankList.innerHTML = '<div class="rounded-lg border border-dashed border-slate-200 p-4 text-center text-xs text-slate-400">暂无排行数据</div>';
        return;
    }

    els.hotRankList.innerHTML = entries.slice(0, 20).map((entry, index) => `
        <button class="zsxq-hot-rank-item w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-left hover:bg-blue-50 hover:border-blue-200 transition-colors" data-rank-file-id="${escapeHtml(entry.fileId)}">
            <div class="flex items-start justify-between gap-2">
                <div class="min-w-0 flex items-start gap-2">
                    <span class="zsxq-rank-number shrink-0">${index + 1}</span>
                    <div class="min-w-0">
                        <div class="line-clamp-2 text-xs font-bold text-slate-700">${escapeHtml(entry.name)}</div>
                        <div class="mt-1 flex flex-wrap items-center gap-1 text-[10px] text-slate-400">
                            <span class="${escapeHtml(fileIconClass(entry.type))} font-black">${escapeHtml(entry.type.toUpperCase())}</span>
                            <span>${escapeHtml(entry.author)}</span>
                            ${entry.createdText ? `<span>· ${escapeHtml(entry.createdText)}</span>` : ''}
                        </div>
                    </div>
                </div>
                <span class="shrink-0 rounded-full bg-white px-2 py-1 text-[10px] font-black text-slate-700">${escapeHtml(String(entry.downloadCount))}</span>
            </div>
            <div class="mt-1 line-clamp-2 pl-6 text-[10px] text-slate-500">${escapeHtml(entry.topicTitle)}</div>
        </button>
    `).join('');
}

function buildHotRankEntries() {
    const entries = Array.from(state.fileMap.entries()).map(([fileId, entry]) => {
        const topic = state.topicMap.get(entry.topicKey) || {};
        const file = entry.file || {};
        const downloadCount = getDownloadCount(file);
        return {
            fileId,
            name: file.name || file.title || file.file_name || '附件',
            type: fileTypeForFile(file),
            downloadCount: Number(downloadCount || 0),
            topicTitle: topic.title || topic?.talk?.text?.slice(0, 48) || topic?.article?.title || '无标题主题',
            author: topic?.owner?.name || topic?.talk?.owner?.name || '未知作者',
            createdText: formatZsxqTime(topic.create_time || topic?.talk?.create_time || ''),
        };
    });

    return entries
        .filter(entry => Number.isFinite(entry.downloadCount) && entry.downloadCount > 0)
        .sort((a, b) => b.downloadCount - a.downloadCount || a.name.localeCompare(b.name, 'zh-CN'));
}

function focusRankedFile(fileId) {
    const row = els.topicsList.querySelector(`.zsxq-file-row[data-file-id="${cssEscape(fileId)}"]`);
    if (!row) return;
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    row.classList.add('zsxq-rank-focus');
    setTimeout(() => row.classList.remove('zsxq-rank-focus'), 1600);
}

function cssEscape(value) {
    if (window.CSS?.escape) return CSS.escape(value);
    return String(value).replace(/["\\]/g, '\\$&');
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
    try {
        const result = await apiPost('/zsxq/attachments/status', {
            group_id: state.selectedGroup.group_id,
            tab_id: tab.id,
            topic_ids: topicIds,
            attachments: visibleAttachmentRefs(),
        });
        const statuses = result?.statuses || {};
        topicIds.forEach(topicId => {
            const cacheKey = `${cachePrefix}${topicId}`;
            const incoming = statuses[topicId] || [];
            if (incoming.length || !state.attachmentStatusCache.has(cacheKey)) {
                state.attachmentStatusCache.set(cacheKey, incoming);
            }
        });
    } catch (error) {
        console.warn('[ZSXQ] attachment status lookup failed', error);
        return;
    }

    els.topicsList.querySelectorAll('.zsxq-file-row').forEach(row => {
        const entry = state.fileMap.get(row.dataset.fileId);
        if (!entry?.topicId) return;
        const records = state.attachmentStatusCache.get(`${cachePrefix}${entry.topicId}`) || [];
        const matched = matchAttachmentStatus(records, entry);
        updateFileRowDownloadStatus(row, matched);
    });
}

function visibleAttachmentRefs() {
    return Array.from(state.fileMap.values()).map(entry => ({
        topic_id: String(entry.topicId || ''),
        attachment_key: entry.attachmentKey || '',
        file_id: entry.fileId || '',
        name: entry.file?.name || entry.file?.title || entry.file?.file_name || '',
    })).filter(item => item.topic_id && (item.attachment_key || item.file_id || item.name));
}

function updateFileRowDownloadStatus(row, matched) {
    const downloadedBadge = row.querySelector('.downloaded-badge');
    const failedBadge = row.querySelector('.download-failed-badge');
    const errorText = row.querySelector('.download-error-text');
    const button = row.querySelector('.download-file-btn');
    const downloaded = matched?.download_status === 'downloaded';
    const failed = matched?.download_status === 'failed';

    if (downloadedBadge) {
        downloadedBadge.classList.toggle('hidden', !downloaded);
        downloadedBadge.title = downloaded ? (matched.local_path || '已通过浏览器下载') : '';
    }
    if (failedBadge) {
        failedBadge.classList.toggle('hidden', !failed);
        failedBadge.title = failed ? (matched.error_message || '上次下载失败，可以重新下载') : '';
    }
    if (errorText) {
        const message = failed ? truncate(matched.error_message || '上次下载失败，可以重新下载。', 96) : '';
        errorText.textContent = message;
        errorText.classList.toggle('hidden', !message);
    }
    if (button && !button.disabled) {
        if (failed) {
            button.className = 'download-file-btn rounded-md bg-amber-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-amber-700';
            button.innerHTML = '<i class="fas fa-rotate-right mr-1"></i>重新下载';
        } else {
            button.className = 'download-file-btn rounded-md bg-blue-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-700';
            button.innerHTML = '<i class="fas fa-download mr-1"></i>下载';
        }
    }
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
        markEntryDownloadFailed(entry, filename, error.message);
        alert(`下载失败：${error.message}`);
    } finally {
        setLoading(button, false, originalHTML);
        await refreshAttachmentStatusBadges();
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
    if (hasCurrentTopicAttachments()) {
        els.attachmentViewFilterControl.classList.remove('hidden');
    } else {
        els.attachmentViewFilterControl.classList.add('hidden');
    }

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

function hasCurrentTopicAttachments() {
    return state.currentTopics.some(topic => topicFiles(topic).length > 0);
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

function markEntryDownloadFailed(entry, filename, errorMessage) {
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
        download_status: 'failed',
        local_path: '',
        error_message: errorMessage || '下载失败',
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
