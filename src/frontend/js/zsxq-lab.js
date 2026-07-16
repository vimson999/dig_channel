const ACTIVE_CREDENTIAL_ID_STORAGE_KEY = 'zsxq_active_credential_id';
const MANUAL_PLAN_LIMIT_STORAGE_KEY = 'zsxq_manual_plan_limit';
const AUTO_SYNC_TEST_INTERVAL_MS = 5 * 60 * 1000;
const AUTO_SYNC_TEST_ROUNDS = 3;
const AUTO_SYNC_TEST_MAX_FILES = 40;
const SYSTEM_SYNC_PDF_DOWNLOAD_THRESHOLD = 30;
const SYSTEM_SYNC_MP3_DOWNLOAD_THRESHOLD = 15;
const MANUAL_PLAN_DEFAULT_LIMIT = 20;
const MANUAL_PLAN_MAX_LIMIT = 50;
const SYSTEM_SYNC_INTERVAL_MINUTES = 5;
const SYSTEM_SYNC_COUNT = 50;
const SYSTEM_SYNC_MAX_ROUNDS = 3;
const SYSTEM_SYNC_STATUS_POLL_MS = 15000;
const SYSTEM_SYNC_LOG_LINES = 120;
const SYSTEM_SYNC_GROUP_ID = '28888112822211';
const PUBLISH_GROUP_ID = '15554855514112';
const FAVORITES_GROUP_ID = '1824528822';

const state = {
    cookie: '',
    cookieProfiles: [],
    activeCookieId: localStorage.getItem(ACTIVE_CREDENTIAL_ID_STORAGE_KEY) || '',
    credentialLoadError: '',
    selectedGroup: null,
    groups: [],
    groupSource: 'local',
    fileMap: new Map(),
    topicMap: new Map(),
    selectedTopicKeys: new Set(),
    selectedAttachmentKeys: new Set(),
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
    syncPollInFlight: false,
    systemSyncSchedulerStatus: null,
    systemSyncSchedulerPollTimer: null,
    syncRuns: [],
    systemSyncBatches: [],
    systemSyncBatchesError: null,
    systemSyncConfig: null,
    attachmentStatusCache: new Map(),
    attachmentType: 'pdf',
    attachmentViewFilter: 'all',
    favoriteTotal: 0,
    favoriteOnly: false,
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
    loadRemoteGroupsBtn: document.getElementById('load-remote-groups-btn'),
    clearBtn: document.getElementById('clear-btn'),
    groupsList: document.getElementById('groups-list'),
    topicsTitle: document.getElementById('topics-title'),
    topicsSubtitle: document.getElementById('topics-subtitle'),
    localTopicFilterControl: document.getElementById('local-topic-filter-control'),
    localTopicDateStartInput: document.getElementById('local-topic-date-start-input'),
    localTopicDateEndInput: document.getElementById('local-topic-date-end-input'),
    localTopicKeywordInput: document.getElementById('local-topic-keyword-input'),
    localTopicSearchBtn: document.getElementById('local-topic-search-btn'),
    refreshTopicsBtn: document.getElementById('refresh-topics-btn'),
    selectAllTopicsBtn: document.getElementById('select-all-topics-btn'),
    batchDownloadBtn: document.getElementById('batch-download-btn'),
    manualPlanLimitControl: document.getElementById('manual-plan-limit-control'),
    manualPlanLimitInput: document.getElementById('manual-plan-limit-input'),
    manualPlanPublishControl: document.getElementById('manual-plan-publish-control'),
    manualPlanAutoPublishCheckbox: document.getElementById('manual-plan-auto-publish-checkbox'),
    favoriteTopicsFilterBtn: document.getElementById('favorite-topics-filter-btn'),
    favoriteTopicsCount: document.getElementById('favorite-topics-count'),
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

let zsxqKnowledgeGraph = null;

document.addEventListener('DOMContentLoaded', () => {
    initializeLocalTopicDateRange();
    initZsxqKnowledgeGraph();
    renderCookieProfiles();
    updateCookieSummary('正在读取平台凭据...', 'muted');
    setCookieEditorOpen(false);
    if (els.systemSyncIntervalInput) {
        els.systemSyncIntervalInput.value = String(SYSTEM_SYNC_INTERVAL_MINUTES);
    }
    if (els.systemSyncMaxRoundsInput) {
        els.systemSyncMaxRoundsInput.value = String(SYSTEM_SYNC_MAX_ROUNDS);
    }
    if (els.manualPlanLimitInput) {
        const savedLimit = localStorage.getItem(MANUAL_PLAN_LIMIT_STORAGE_KEY);
        els.manualPlanLimitInput.value = String(readManualPlanLimit(savedLimit || els.manualPlanLimitInput.value));
    }
    if (els.manualPlanAutoPublishCheckbox) {
        els.manualPlanAutoPublishCheckbox.checked = true;
    }
    bindEvents();
    loadGroups({ source: 'local', auto: true });
    initializeCookieProfiles();

    loadSystemSyncSchedulerStatus({ render: false })
        .then(() => loadSystemSyncSchedulerPanelsAfterBootstrap())
        .catch(() => {
            renderSystemSyncSchedulerPanel();
            renderSystemSyncBatches();
        });
});

function initZsxqKnowledgeGraph() {
    const root = document.getElementById('zsxq-knowledge-map');
    if (!root || !window.ShioaThreeGraph) return;
    zsxqKnowledgeGraph = window.ShioaThreeGraph.mount(root, {
        kicker: 'Knowledge Graph',
        title: '知识对象图谱',
        subtitle: '星球、Tab、主题、附件、外链、总结和语音的当前连接。',
    });
    updateZsxqKnowledgeGraph();
}

function updateZsxqKnowledgeGraph() {
    if (!zsxqKnowledgeGraph) return;
    zsxqKnowledgeGraph.update(buildZsxqKnowledgeGraphModel());
}

function buildZsxqKnowledgeGraphModel() {
    const selectedGroup = state.selectedGroup || null;
    const tabs = typeof getTabDefinitions === 'function' ? getTabDefinitions() : [];
    const activeTab = typeof getActiveTabConfig === 'function' ? getActiveTabConfig() : null;
    const topics = Array.isArray(state.currentTopics) ? state.currentTopics : [];
    const visibleTopics = topics.slice(0, 5);
    const nodes = [];
    const links = [];
    const groupId = selectedGroup ? `group:${selectedGroup.group_id}` : 'group:pending';
    const groupLabel = selectedGroup?.name || '选择星球';
    let fileCount = 0;
    let externalCount = 0;
    let summaryCount = 0;
    let audioCount = 0;

    nodes.push({
        id: groupId,
        type: 'group',
        label: groupLabel,
        primary: true,
        rank: 0,
        value: selectedGroup ? 10 : 5,
        status: selectedGroup ? 'success' : 'idle',
        labelMax: 24,
    });

    if (!selectedGroup && state.groups.length) {
        state.groups.slice(0, 6).forEach(group => {
            const id = `group-option:${group.group_id}`;
            nodes.push({
                id,
                type: 'group',
                label: group.name || group.group_id,
                rank: 1,
                value: group.topic_count ? 4 : 2,
                status: 'idle',
                labelMax: 18,
            });
            links.push({ source: groupId, target: id, opacity: 0.26 });
        });
    }

    topics.forEach(topic => {
        const files = topicFiles(topic);
        const docs = Array.isArray(topic.external_documents) ? topic.external_documents : [];
        fileCount += files.length;
        externalCount += docs.length;
        docs.forEach(link => {
            const document = link?.document || {};
            const summaryStatus = document.summary_status || 'pending';
            const ttsStatus = document.tts_status || 'not_submitted';
            if (summaryStatus === 'completed' || summaryStatus === 'summarized') summaryCount += 1;
            if (ttsStatus === 'completed') audioCount += 1;
        });
    });

    if (selectedGroup) {
        const activeTabNode = activeTab || tabs[0] || null;
        const graphTabs = [];
        if (activeTabNode) graphTabs.push(activeTabNode);
        tabs
            .filter(tab => !activeTabNode || tab.id !== activeTabNode.id)
            .slice(0, 4)
            .forEach(tab => graphTabs.push(tab));

        graphTabs.forEach(tab => {
            const id = `tab:${tab.id}`;
            const active = tab.id === state.activeTab;
            nodes.push({
                id,
                type: 'tab',
                label: tab.label || tab.id,
                rank: 1,
                value: active ? 7 : 4,
                status: active ? 'success' : 'idle',
                labelMax: 18,
            });
            links.push({ source: groupId, target: id, opacity: active ? 0.78 : 0.28 });
        });

        if (tabs.length > graphTabs.length) {
            const moreTabId = 'tab:more';
            nodes.push({
                id: moreTabId,
                type: 'tab',
                label: `其余 ${tabs.length - graphTabs.length} 个 Tab`,
                rank: 1,
                value: 3,
                status: 'idle',
                labelMax: 16,
            });
            links.push({ source: groupId, target: moreTabId, opacity: 0.18 });
        }

        const activeTabId = activeTabNode ? `tab:${activeTabNode.id}` : groupId;
        visibleTopics.forEach((topic, index) => {
            const topicId = `topic:${topicKeyForRender(topic)}`;
            const title = normalizeTopicTitle(topic.title || topic?.article?.title || topic?.talk?.text || `主题 ${index + 1}`) || `主题 ${index + 1}`;
            const files = topicFiles(topic);
            const docs = Array.isArray(topic.external_documents) ? topic.external_documents : [];
            nodes.push({
                id: topicId,
                type: 'topic',
                label: title,
                rank: 2,
                value: Math.min(9, 3 + files.length + docs.length),
                status: files.length || docs.length ? 'success' : 'idle',
                labelMax: 22,
            });
            links.push({ source: activeTabId, target: topicId, opacity: 0.44 });
        });

        if (topics.length > visibleTopics.length) {
            const moreTopicId = 'topic:more';
            nodes.push({
                id: moreTopicId,
                type: 'topic',
                label: `其余 ${topics.length - visibleTopics.length} 条主题`,
                rank: 2,
                value: 4,
                status: 'idle',
                labelMax: 16,
            });
            links.push({ source: activeTabId, target: moreTopicId, opacity: 0.2 });
        }

        const aggregateSource = visibleTopics.length ? activeTabId : groupId;
        [
            { id: 'aggregate:files', type: 'attachment', label: `附件 ${fileCount || state.fileMap.size}`, value: Math.min(9, 3 + (fileCount || state.fileMap.size) / 8), count: fileCount || state.fileMap.size },
            { id: 'aggregate:external', type: 'external', label: `外链 ${externalCount}`, value: Math.min(8, 3 + externalCount / 5), count: externalCount },
            { id: 'aggregate:summary', type: 'summary', label: `总结 ${summaryCount}`, value: Math.min(8, 3 + summaryCount / 4), count: summaryCount },
            { id: 'aggregate:audio', type: 'audio', label: `语音 ${audioCount}`, value: Math.min(8, 3 + audioCount / 4), count: audioCount },
        ].forEach(item => {
            nodes.push({
                id: item.id,
                type: item.type,
                label: item.label,
                rank: 3,
                value: item.value,
                status: item.count ? 'success' : 'idle',
                labelMax: 14,
            });
            links.push({ source: aggregateSource, target: item.id, opacity: item.count ? 0.36 : 0.16 });
        });
    }

    const loadedText = topics.length ? `${topics.length} 条主题` : (selectedGroup ? '等待主题' : '等待星球');
    const assetText = `附件 ${fileCount || state.fileMap.size} · 外链 ${externalCount} · AI ${summaryCount}/${audioCount}`;
    return {
        layout: 'columns',
        title: selectedGroup ? groupLabel : '知识星球实验台',
        subtitle: activeTab ? `${activeTab.label} · ${loadedText} · ${assetText}` : '选择星球后形成主题、附件和外部文档网络',
        metrics: [
            { label: '星球', value: selectedGroup ? 1 : state.groups.length },
            { label: 'Tab', value: tabs.length },
            { label: '主题', value: topics.length },
            { label: '附件', value: fileCount || state.fileMap.size },
        ],
        legend: [
            { label: '星球', color: window.ShioaThreeGraph.colors.group },
            { label: 'Tab', color: window.ShioaThreeGraph.colors.tab },
            { label: '主题', color: window.ShioaThreeGraph.colors.topic },
            { label: '附件', color: window.ShioaThreeGraph.colors.attachment },
            { label: '外链', color: window.ShioaThreeGraph.colors.external },
            { label: '总结/语音', color: window.ShioaThreeGraph.colors.summary },
        ],
        nodes,
        links,
    };
}

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

function isSystemSyncGroupId(groupId) {
    return String(groupId || '').trim() === SYSTEM_SYNC_GROUP_ID;
}

function isSelectedSystemSyncGroup() {
    return Boolean(state.selectedGroup && isSystemSyncGroupId(state.selectedGroup.group_id));
}

function isPublishGroupId(groupId) {
    return String(groupId || '').trim() === PUBLISH_GROUP_ID;
}

function isSelectedPublishGroup() {
    return Boolean(state.selectedGroup && isPublishGroupId(state.selectedGroup.group_id));
}

function renderPublishPanelVisibility() {
    const visible = isSelectedPublishGroup();
    els.publishPanel?.classList.toggle('hidden', !visible);
    if (!visible) {
        setPublishStatus('');
    }
}

function renderHotRankPanelVisibility() {
    els.hotRankPanel?.classList.toggle('hidden', !isSelectedSystemSyncGroup());
}

function getSystemSyncTargetGroupId() {
    if (state.selectedGroup) {
        return isSelectedSystemSyncGroup() ? String(state.selectedGroup.group_id) : '';
    }
    const configGroupId = state.systemSyncConfig?.group_id;
    return isSystemSyncGroupId(configGroupId) ? String(configGroupId) : '';
}

function setSystemSyncControlsVisible(visible) {
    els.autoSyncTestBtn?.classList.toggle('hidden', !visible);
    els.autoSyncMemoTestBtn?.classList.toggle('hidden', !visible);
    els.systemSyncTestConfigBtn?.classList.toggle('hidden', !visible);
    els.systemSyncSchedulerPanel?.classList.toggle('hidden', !visible);
    if (!visible) {
        stopSystemSyncSchedulerPolling();
        state.systemSyncBatches = [];
        state.systemSyncBatchesError = null;
        els.syncBatchesPanel?.classList.add('hidden');
    }
}

function hideManualDownloadControls() {
    els.attachmentViewFilterControl?.classList.add('hidden');
    els.attachmentTypeControl?.classList.add('hidden');
    els.selectAllTopicsBtn?.classList.add('hidden');
    els.batchDownloadBtn?.classList.add('hidden');
    els.manualPlanLimitControl?.classList.add('hidden');
    els.manualPlanPublishControl?.classList.add('hidden');
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

    els.loadGroupsBtn.addEventListener('click', () => loadGroups({ source: 'local' }));
    els.loadRemoteGroupsBtn?.addEventListener('click', () => loadGroups({ source: 'remote' }));
    els.refreshTopicsBtn.addEventListener('click', () => {
        if (state.selectedGroup) loadActiveTabPage({ reset: true });
    });
    els.localTopicDateStartInput?.addEventListener('change', () => {
        normalizeLocalTopicDateRange('start');
        applyLocalTopicFilters();
    });
    els.localTopicDateEndInput?.addEventListener('change', () => {
        normalizeLocalTopicDateRange('end');
        applyLocalTopicFilters();
    });
    els.localTopicSearchBtn?.addEventListener('click', applyLocalTopicFilters);
    els.localTopicKeywordInput?.addEventListener('keydown', event => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        applyLocalTopicFilters();
    });
    els.scopeSelect.addEventListener('change', () => {
        const tab = getTabDefinitions().find(item => !item.hashtagId && item.scope === els.scopeSelect.value);
        state.activeTab = tab?.id || 'preset:all';
        if (tab) applyAttachmentDefaultsForTab(tab);
        if (state.selectedGroup) loadActiveTabPage({ reset: true });
    });
    els.selectAllTopicsBtn.addEventListener('click', toggleSelectAllTopics);
    els.batchDownloadBtn.addEventListener('click', createManualDownloadWorkflow);
    els.manualPlanLimitInput?.addEventListener('change', () => {
        const limit = readManualPlanLimit();
        els.manualPlanLimitInput.value = String(limit);
        localStorage.setItem(MANUAL_PLAN_LIMIT_STORAGE_KEY, String(limit));
        updateBatchDownloadButton();
    });
    els.attachmentTypeSelect.addEventListener('change', () => {
        state.attachmentType = els.attachmentTypeSelect.value || 'pdf';
        state.selectedTopicKeys.clear();
        state.selectedAttachmentKeys.clear();
        updateBatchDownloadButton();
        renderTopics(state.currentTopics, activeTabState());
        renderSyncIntro();
    });
    els.attachmentViewFilterSelect.addEventListener('change', () => {
        state.attachmentViewFilter = els.attachmentViewFilterSelect.value || 'all';
        if (['mp3', 'pdf'].includes(state.attachmentViewFilter) && els.attachmentTypeSelect) {
            els.attachmentTypeSelect.value = state.attachmentViewFilter;
            state.attachmentType = state.attachmentViewFilter;
        }
        state.selectedTopicKeys.clear();
        state.selectedAttachmentKeys.clear();
        if (state.selectedGroup) {
            loadActiveTabPage({ reset: true });
        } else {
            renderTopics(state.currentTopics, activeTabState());
        }
        renderSyncIntro();
    });
    els.favoriteTopicsFilterBtn?.addEventListener('click', () => {
        state.favoriteOnly = !state.favoriteOnly;
        renderFavoriteFilterControl();
        if (state.selectedGroup) loadActiveTabPage({ reset: true });
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

async function initializeCookieProfiles() {
    state.credentialLoadError = '';
    renderCookieProfiles(true);
    try {
        state.cookieProfiles = await loadCookieProfiles();
    } catch (error) {
        state.cookieProfiles = [];
        state.credentialLoadError = error.message || '平台凭据读取失败';
    }

    if (!state.cookieProfiles.some(profile => profile.id === state.activeCookieId)) {
        state.activeCookieId = state.cookieProfiles[0]?.id || '';
    }
    persistCookieProfiles();
    applyActiveCookieProfile();
    renderCookieProfiles();
    updateCookieFormatHint();
    updateCookieSummary();
    setCookieEditorOpen(!state.activeCookieId);
}

async function loadCookieProfiles() {
    const params = new URLSearchParams({
        platform: 'zsxq',
        purpose: 'zsxq.sync',
        status: 'active',
        per_page: '100',
    });
    const data = await apiGet(`/platform-credentials?${params.toString()}`);
    return Array.isArray(data?.items) ? data.items : [];
}

function persistCookieProfiles() {
    if (state.activeCookieId) {
        localStorage.setItem(ACTIVE_CREDENTIAL_ID_STORAGE_KEY, state.activeCookieId);
    } else {
        localStorage.removeItem(ACTIVE_CREDENTIAL_ID_STORAGE_KEY);
    }
}

function activeCookieProfile() {
    return state.cookieProfiles.find(profile => profile.id === state.activeCookieId) || null;
}

function applyActiveCookieProfile() {
    const profile = activeCookieProfile();
    state.cookie = '';
    els.cookieNameInput.value = profile?.name || '';
    els.cookieInput.value = '';
    els.cookieInput.placeholder = profile
        ? '如需更新这个 DB Cookie，请粘贴新的 Cookie 请求头或浏览器导出 JSON；留空只更新名称。'
        : '粘贴完整 Cookie 请求头，或浏览器导出的 Cookie JSON 数组；保存后会加密写入平台凭据 DB。';
}

function renderCookieProfiles(loading = false) {
    if (loading) {
        els.cookieProfileSelect.innerHTML = '<option value="">读取 DB Cookie 中...</option>';
        els.cookieProfileSelect.value = '';
        els.deleteCookieBtn.disabled = true;
        els.deleteCookieBtn.classList.add('opacity-50');
        return;
    }
    if (state.credentialLoadError) {
        els.cookieProfileSelect.innerHTML = '<option value="">DB Cookie 读取失败</option>';
        els.cookieProfileSelect.value = '';
        els.deleteCookieBtn.disabled = true;
        els.deleteCookieBtn.classList.add('opacity-50');
        return;
    }

    const emptyOption = '<option value="">新增 DB Cookie</option>';
    if (!state.cookieProfiles.length) {
        els.cookieProfileSelect.innerHTML = emptyOption;
        els.cookieProfileSelect.value = '';
        els.deleteCookieBtn.disabled = true;
        els.deleteCookieBtn.classList.add('opacity-50');
        return;
    }

    els.cookieProfileSelect.innerHTML = emptyOption + state.cookieProfiles.map(profile => {
        const label = credentialProfileLabel(profile);
        return `<option value="${escapeHtml(profile.id)}">${escapeHtml(label)}</option>`;
    }).join('');
    els.cookieProfileSelect.value = state.activeCookieId;
    els.deleteCookieBtn.disabled = !state.activeCookieId;
    els.deleteCookieBtn.classList.toggle('opacity-50', !state.activeCookieId);
}

function switchCookieProfile() {
    state.activeCookieId = els.cookieProfileSelect.value;
    applyActiveCookieProfile();
    persistCookieProfiles();
    updateCookieFormatHint();
    updateCookieSummary();
    setCookieEditorOpen(!state.activeCookieId);
    resetZsxqWorkspace();
}

function startNewCookieProfile() {
    state.activeCookieId = '';
    state.cookie = '';
    persistCookieProfiles();
    els.cookieProfileSelect.value = '';
    els.cookieNameInput.value = '';
    els.cookieInput.value = '';
    applyActiveCookieProfile();
    renderCookieProfiles();
    updateCookieFormatHint();
    updateCookieSummary();
    setCookieEditorOpen(true);
    resetZsxqWorkspace();
}

async function saveCookieProfile() {
    const cookie = els.cookieInput.value.trim();
    const existing = activeCookieProfile();
    if (!cookie && !existing) {
        updateCookieSummary('新增 DB Cookie 需要填写 Cookie / JSON。', 'error');
        return;
    }

    const token = extractCookieToken(cookie);
    const name = (els.cookieNameInput.value || '').trim()
        || existing?.name
        || (token ? `Cookie ${maskToken(token)}` : '未命名 Cookie');
    const payload = {
        platform: 'zsxq',
        name,
        credential_type: cookie ? credentialTypeForPayload(cookie) : (existing?.credential_type || 'cookie_header'),
        purposes: Array.isArray(existing?.purposes) && existing.purposes.length
            ? existing.purposes
            : ['zsxq.sync', 'zsxq.publish'],
        status: existing?.status && existing.status !== 'deleted' ? existing.status : 'active',
        priority: Number.isFinite(Number(existing?.priority)) ? Number(existing.priority) : 100,
        owner_label: existing?.owner_label || '',
        notes: existing?.notes || '',
        extra: existing?.extra && typeof existing.extra === 'object' ? existing.extra : {},
    };
    if (cookie) {
        payload.payload = cookie;
    }

    const originalHTML = els.saveCookieBtn.innerHTML;
    setLoading(els.saveCookieBtn, true, '保存中...');
    try {
        const result = await apiRequest(
            existing ? `/platform-credentials/${encodeURIComponent(existing.id)}` : '/platform-credentials',
            {
                method: existing ? 'PUT' : 'POST',
                payload,
            },
        );
        const account = result?.account;
        state.activeCookieId = account?.id || existing?.id || '';
        persistCookieProfiles();
        await initializeCookieProfiles();
        updateCookieSummary('DB Cookie 已保存', 'success');
        setCookieEditorOpen(false);
    } catch (error) {
        updateCookieSummary(`保存失败：${error.message}`, 'error');
    } finally {
        setLoading(els.saveCookieBtn, false, originalHTML);
    }
}

async function deleteCookieProfile() {
    const currentId = state.activeCookieId;
    if (!currentId) return;
    const profile = activeCookieProfile();
    const label = credentialProfileLabel(profile);
    if (!window.confirm(`确认删除 DB Cookie「${label}」？`)) return;

    const originalHTML = els.deleteCookieBtn.innerHTML;
    setLoading(els.deleteCookieBtn, true, '删除中...');
    try {
        await apiRequest(`/platform-credentials/${encodeURIComponent(currentId)}`, { method: 'DELETE' });
        state.activeCookieId = '';
        persistCookieProfiles();
        await initializeCookieProfiles();
        updateCookieSummary('DB Cookie 已删除', 'success');
        resetZsxqWorkspace();
    } catch (error) {
        updateCookieSummary(`删除失败：${error.message}`, 'error');
    } finally {
        setLoading(els.deleteCookieBtn, false, originalHTML);
        renderCookieProfiles();
    }
}

function credentialTypeForPayload(value) {
    const text = String(value || '').trim();
    return text.startsWith('[') || text.startsWith('{') ? 'cookie_json' : 'cookie_header';
}

function credentialProfileLabel(profile) {
    if (!profile) return '未选择 Cookie';
    const name = profile.owner_label || profile.name || '未命名 Cookie';
    const summary = profile.masked_summary ? ` · ${profile.masked_summary}` : '';
    return `${name}${summary}`;
}

function resetZsxqWorkspace() {
    stopSyncPolling();
    cancelAutoSyncTest(false);
    state.selectedGroup = null;
    state.groups = [];
    state.fileMap.clear();
    state.topicMap.clear();
    state.selectedTopicKeys.clear();
    state.selectedAttachmentKeys.clear();
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
    showGroupsMessage('等待读取本地缓存...');
    els.topicsTitle.textContent = '最新主题';
    els.topicsSubtitle.textContent = '选择一个星球后查看主题内容。';
    els.refreshTopicsBtn.classList.add('hidden');
    els.localTopicFilterControl?.classList.add('hidden');
    els.publishPanel.classList.add('hidden');
    els.selectAllTopicsBtn.classList.add('hidden');
    els.batchDownloadBtn.classList.add('hidden');
    els.attachmentTypeControl.classList.add('hidden');
    els.copyTopicsJsonBtn?.classList.add('hidden');
    els.syncCurrentTabBtn?.classList.add('hidden');
    els.syncAllTabsBtn?.classList.add('hidden');
    setSystemSyncControlsVisible(false);
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
            : type === 'success'
            ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
            : 'border-slate-200 bg-slate-50 text-slate-600';
        els.cookieSummary.className = `mt-2 rounded-lg border px-3 py-2 text-xs ${classByType}`;
        els.cookieSummary.textContent = message;
        return;
    }

    if (state.credentialLoadError) {
        els.cookieSummary.className = 'mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600';
        els.cookieSummary.textContent = `平台凭据读取失败：${state.credentialLoadError}`;
        return;
    }

    const profile = activeCookieProfile();
    if (profile) {
        els.cookieSummary.className = 'mt-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700';
        els.cookieSummary.textContent = value
            ? `将更新 DB Cookie：${credentialProfileLabel(profile)}`
            : `使用 DB Cookie：${credentialProfileLabel(profile)}`;
        return;
    }

    if (!value) {
        els.cookieSummary.className = 'mt-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500';
        els.cookieSummary.textContent = '未选择 DB Cookie';
        return;
    }

    const token = extractCookieToken(value);
    const name = (els.cookieNameInput.value || '').trim();
    els.cookieSummary.className = 'mt-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700';
    els.cookieSummary.textContent = token
        ? `待保存到 DB：${name ? `${name} · ` : ''}zsxq_access_token：${maskToken(token)}`
        : '待保存到 DB Cookie';
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

function hasRequestCredential() {
    return Boolean(state.activeCookieId);
}

function credentialRequestPayload() {
    return state.activeCookieId ? { credential_id: state.activeCookieId } : {};
}

function appendCredentialToFormData(formData) {
    if (state.activeCookieId) {
        formData.append('credential_id', state.activeCookieId);
    }
}

async function apiRequest(endpoint, options = {}) {
    const method = options.method || 'GET';
    const requestOptions = { method };
    if (options.payload !== undefined) {
        requestOptions.headers = { 'Content-Type': 'application/json' };
        requestOptions.body = JSON.stringify(options.payload);
    }
    const response = await fetch(`${apiBaseUrl()}${endpoint}`, requestOptions);
    let data = {};
    try {
        data = await response.json();
    } catch {
        data = {};
    }
    if (!response.ok || data.success === false) {
        const message = data?.error?.message || data?.message || `HTTP ${response.status}`;
        console.warn('[ZSXQ] request failed', { endpoint, status: response.status, message });
        throw new Error(message);
    }
    return data.data;
}

async function apiPost(endpoint, payload) {
    return apiRequest(endpoint, { method: 'POST', payload });
}

async function apiGet(endpoint) {
    return apiRequest(endpoint);
}

function apiBaseUrl() {
    return window.API_BASE_URL || window.CONFIG?.API_BASE_URL || 'http://localhost:5001/api/v1';
}

async function loadGroups(options = {}) {
    const source = options.source === 'remote' ? 'remote' : 'local';
    if (source === 'remote') {
        await loadRemoteGroups();
        return;
    }

    await loadLocalGroups();
}

async function loadLocalGroups() {
    state.groupSource = 'local';
    setLoading(els.loadGroupsBtn, true, '读取中...');
    showGroupsMessage('正在读取本地缓存星球...');
    try {
        const result = await apiGet('/zsxq/groups/local');
        const groups = (result?.resp_data?.groups || []).map(group => ({
            ...group,
            local_cached: true,
        }));
        state.groups = groups;
        renderGroups(groups);
    } catch (error) {
        showGroupsMessage(`本地缓存读取失败：${error.message}`, 'error');
    } finally {
        setLoading(els.loadGroupsBtn, false, '<i class="fas fa-database mr-2"></i>本地缓存');
    }
}

async function loadRemoteGroups() {
    if (!hasRequestCredential()) {
        showGroupsMessage('远端刷新需要先选择 DB Cookie，或新增保存 Cookie。', 'warning');
        return;
    }

    state.groupSource = 'remote';
    setLoading(els.loadRemoteGroupsBtn, true, '刷新中...');
    showGroupsMessage('正在请求知识星球 API...');
    try {
        const result = await apiPost('/zsxq/groups', { ...credentialRequestPayload(), count: 50 });
        const groups = (result?.resp_data?.groups || []).map(group => ({
            ...group,
            local_cached: false,
            remote_loaded: true,
        }));
        state.groups = groups;
        renderGroups(groups);
    } catch (error) {
        showGroupsMessage(error.message, 'error');
    } finally {
        setLoading(els.loadRemoteGroupsBtn, false, '<i class="fas fa-cloud-arrow-down mr-2"></i>远端刷新');
    }
}

async function loadTopics(group, options = {}) {
    const requestedActiveTab = state.activeTab;
    const previousGroupId = String(state.selectedGroup?.group_id || '');
    state.selectedGroup = group;
    if (previousGroupId !== String(group.group_id || '')) {
        state.favoriteOnly = false;
    }
    const systemSyncEnabledForGroup = isSystemSyncGroupId(group.group_id);
    if (options.resetTabs !== false) {
        state.tabStates.clear();
    }
    renderGroups(state.groups);
    els.refreshTopicsBtn.classList.remove('hidden');
    els.localTopicFilterControl?.classList.remove('hidden');
    renderPublishPanelVisibility();
    renderHotRankPanelVisibility();
    renderFavoriteFilterControl();
    els.syncCurrentTabBtn?.classList.remove('hidden');
    els.syncAllTabsBtn?.classList.remove('hidden');
    if (!systemSyncEnabledForGroup) {
        state.selectedTopicKeys.clear();
        state.selectedAttachmentKeys.clear();
        hideManualDownloadControls();
    }
    setSystemSyncControlsVisible(systemSyncEnabledForGroup);
    els.copyTopicsJsonBtn?.classList.add('hidden');
    els.topicsTitle.textContent = group.name || `星球 ${group.group_id}`;
    const sourceLabel = group.local_cached === false ? '远端刷新' : '本地缓存';
    els.topicsSubtitle.textContent = `Group ID: ${group.group_id} · ${sourceLabel}`;
    els.topicsList.innerHTML = loadingCard('正在读取本地主题...');
    state.officialMenus = await loadOfficialMenus(group.group_id, {
        allowRemote: Boolean(options.allowRemoteMenus || group.remote_loaded || group.local_cached === false),
    });
    const tabs = getTabDefinitions();
    if (!tabs.length) {
        els.topicsTabs.classList.add('hidden');
        els.topicsList.innerHTML = emptyCard(state.menuError || '官方菜单不可用，无法展示 tab 数据。', 'error');
        return;
    }
    state.activeTab = tabs.some(tab => tab.id === requestedActiveTab)
        ? requestedActiveTab
        : (tabs[0]?.id || 'latest');
    applyAttachmentDefaultsForTab(getActiveTabConfig());
    renderTopicTabs();
    renderSyncIntro();
    const sideLoads = [loadSyncRuns(group.group_id)];
    if (systemSyncEnabledForGroup) {
        startSystemSyncSchedulerPolling();
        sideLoads.push(loadSystemSyncSchedulerStatus());
    } else {
        stopSystemSyncSchedulerPolling();
    }
    await Promise.all(sideLoads);
    if (systemSyncEnabledForGroup) {
        renderSystemSyncSchedulerPanel();
    }

    await loadActiveTabPage({ reset: true });
}

async function loadOfficialMenus(groupId, options = {}) {
    state.menuError = null;
    const cachedTabs = await loadLocalCachedTabs(groupId);
    if (cachedTabs.length) {
        return cachedTabs;
    }

    if (!options.allowRemote) {
        state.menuError = '本地库暂无这个星球的 Tab。请先同步这个星球，或点击左侧“远端刷新”后再选择。';
        return [];
    }

    if (!hasRequestCredential()) {
        state.menuError = '本地库暂无这个星球的 Tab；远端获取 Tab 需要先选择 DB Cookie。';
        return [];
    }

    try {
        const result = await apiPost('/zsxq/menus', {
            ...credentialRequestPayload(),
            group_id: groupId,
        });
        const menus = result?.resp_data?.menus || [];
        state.menuError = menus.length ? null : '官方菜单为空，不能展示本地猜测分类。';
        return menus;
    } catch (error) {
        state.menuError = `官方菜单加载失败，且本地库暂无 Tab：${error.message}`;
        return [];
    }
}

async function loadLocalCachedTabs(groupId) {
    try {
        const result = await apiPost('/zsxq/topics/local/tabs', { group_id: groupId });
        return (result?.tabs || []).map(tab => ({
            local_cached: true,
            tab_id: tab.id || tab.tab_id,
            title: tab.label || tab.tab_label || tab.id || '本地缓存',
            label: tab.label || tab.tab_label || tab.id || '本地缓存',
            topic_count: tab.topic_count || 0,
            last_synced_at: tab.last_synced_at || '',
        })).filter(tab => tab.tab_id);
    } catch {
        return [];
    }
}

async function loadActiveTabPage(options = {}) {
    if (!state.selectedGroup || state.isLoadingMore) return;

    const tab = getActiveTabConfig();
    const tabState = getTabState(tab.id);
    const reset = Boolean(options.reset);
    if (!reset && !tabState.hasMore) return;

    if (reset) {
        resetTabState(tabState);
        els.topicsList.innerHTML = loadingCard(`正在读取本地${tab.label}...`);
    }

    state.isLoadingMore = true;
    tabState.loading = true;
    tabState.error = null;
    renderActiveTab();

    try {
        let pagesLoaded = 0;
        const maxAutoPages = 1;

        do {
            const requestPayload = buildLocalTopicsRequest(tab, tabState);
            const result = await apiPost('/zsxq/topics/local', requestPayload);
            state.lastTopicsRequest = withoutSensitiveCookie(requestPayload);
            state.lastTopicsResponse = result;
            state.lastTopicsScope = requestPayload.scope || 'local';
            state.favoriteTotal = Number(result?.resp_data?.favorite_total || 0);
            els.copyTopicsJsonBtn?.classList.remove('hidden');

            const pageTopics = extractTopicsFromResponse(result);
            appendTabTopics(tabState, tab, pageTopics);
            tabState.loaded = true;
            tabState.source = result?.source || 'local_db';
            tabState.total = result?.resp_data?.total ?? tabState.total;
            tabState.emptyMessage = result?.resp_data?.message || '';
            tabState.endTime = result?.resp_data?.next_end_time || oldestTopicTime(pageTopics);
            tabState.hasMore = pageTopics.length > 0 && Boolean(tabState.endTime);
            pagesLoaded += 1;

            if (!tabState.hasMore) break;
        } while (
            pagesLoaded < maxAutoPages
        );
    } catch (error) {
        tabState.error = error.message;
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
    if (!hasRequestCredential()) {
        renderSyncStatus({ type: 'error', message: '请先选择 DB Cookie，或新增保存 Cookie。' });
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
            ? `正在同步全部 ${tabs.length} 个 tab，每个 tab 默认拉 1 页；只同步主题和附件索引，不自动下载${attachmentTypeLabel}。`
            : `正在同步「${activeTab.label}」，最多拉 ${maxPages} 页；只同步主题和附件索引，不自动下载${attachmentTypeLabel}。`,
    });

    try {
        const result = await apiPost('/zsxq/sync', {
            ...credentialRequestPayload(),
            group_id: state.selectedGroup.group_id,
            group_name: state.selectedGroup.name,
            tabs,
            count: Number(els.countInput.value || 20),
            max_pages: maxPages,
            stop_on_existing: true,
            download_attachments: false,
            allow_attachment_downloads: false,
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
            await loadActiveTabPage({ reset: true });
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
    if (!hasRequestCredential()) {
        renderSyncStatus({ type: 'error', message: '请先选择 DB Cookie，或新增保存 Cookie。' });
        return;
    }
    if (!state.selectedGroup) {
        renderSyncStatus({ type: 'error', message: '请先选择一个星球。' });
        return;
    }
    if (!isSelectedSystemSyncGroup()) {
        renderSyncStatus({ type: 'error', message: '自动同步测试仅适用于「前沿信息收录」。生财有术和洪灝请使用普通 Tab 同步。' });
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
        message: `${autoSyncScopeSubmitText(scope)}仅同步主题和附件索引，不自动下载 PDF/音频附件。`,
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
        ...credentialRequestPayload(),
        group_id: state.selectedGroup.group_id,
        group_name: state.selectedGroup.name,
        tabs: [job.tab],
        count: Number(els.countInput.value || 20),
        max_pages: 1,
        stop_on_existing: true,
        download_attachments: false,
        allow_attachment_downloads: false,
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
    if (!isSelectedSystemSyncGroup()) {
        renderSyncStatus({ type: 'error', message: '系统计划配置仅适用于「前沿信息收录」。当前星球请使用普通 Tab 同步或人工计划。' });
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
        message: `正在保存本机系统计划任务配置。真实观察模式会每 ${systemIntervalMinutes} 分钟执行一次，每批获取 ${SYSTEM_SYNC_COUNT} 条主题；达到下载量阈值后才自动下载附件。`,
    });

    try {
        const attachmentDownloadRules = buildSystemAttachmentDownloadRules(jobs);
        const thresholdedDownloadEnabled = AUTO_SYNC_TEST_MAX_FILES > 0 && attachmentDownloadRules.rules.length > 0;
        const payload = {
            group_id: state.selectedGroup.group_id,
            group_name: state.selectedGroup.name,
            count: SYSTEM_SYNC_COUNT,
            max_pages: 1,
            stop_on_existing: true,
            download_attachments: thresholdedDownloadEnabled,
            allow_attachment_downloads: thresholdedDownloadEnabled,
            max_download_files: AUTO_SYNC_TEST_MAX_FILES,
            skip_existing_downloads: true,
            attachment_download_rules: attachmentDownloadRules,
            interval_minutes: systemIntervalMinutes,
            max_rounds: maxRoundsRaw,
            jobs: jobs.map(job => ({
                key: job.key,
                mode_code: job.modeCode,
                file_type: job.fileType,
                tab: job.tab,
            })),
        };
        Object.assign(payload, credentialRequestPayload());
        const result = await apiPost('/zsxq/system-sync-test/config', payload);
        const config = result?.config || {};
        const cookieReady = systemSyncCookieReady(config);
        renderSyncStatus({
            type: 'success',
            title: '系统计划配置已保存',
            message: cookieReady
                ? `配置已写入本机 runtime。Cookie 将从 ${systemSyncCookieSourceText(config)} 读取，现在可以启动项目内 ZSXQ 调度器。`
                : `配置已写入本机 runtime。请先在 .env 或系统环境里配置 ${config.cookie_env_name || 'ZSXQ_SYNC_TEST_COOKIE'}。`,
            details: `
                <div class="text-xs text-slate-500 leading-6">
                    <div>配置文件：<span class="font-mono">${escapeHtml(config.path || '-')}</span></div>
                    <div>Cookie 来源：${escapeHtml(systemSyncCookieSourceText(config))}${cookieReady ? ' · 已检测到' : ' · 未检测到'}</div>
                    <div>任务范围：外资研报 PDF + 调研纪要 MP3；每 ${config.interval_minutes || systemIntervalMinutes} 分钟 1 批；每批获取 ${SYSTEM_SYNC_COUNT} 条主题；${escapeHtml(formatSystemThresholdDownloadText(config))}。</div>
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

function readManualPlanLimit(value = undefined) {
    const raw = value !== undefined && value !== null && String(value).trim() !== ''
        ? value
        : (els.manualPlanLimitInput?.value || localStorage.getItem(MANUAL_PLAN_LIMIT_STORAGE_KEY));
    const number = Number(raw);
    if (!Number.isFinite(number)) {
        return MANUAL_PLAN_DEFAULT_LIMIT;
    }
    return Math.max(1, Math.min(Math.round(number), MANUAL_PLAN_MAX_LIMIT));
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
    if (!isSelectedSystemSyncGroup()) {
        return [];
    }
    const tabs = getTabDefinitions();
    const foreignReportTab = tabs.find(tab => tab.label.includes('外资研报'));
    const researchMemoTab = tabs.find(tab => tab.label.includes('调研纪要'));
    const jobs = [
        foreignReportTab ? { key: 'foreign_report', modeCode: 'fr', tab: foreignReportTab, fileType: 'pdf' } : null,
        researchMemoTab ? { key: 'research_memo_mp3', modeCode: 'rm', tab: researchMemoTab, fileType: 'mp3' } : null,
    ].filter(Boolean);
    return scope === 'memo' ? jobs.filter(job => job.key === 'research_memo_mp3') : jobs;
}

function buildSystemAttachmentDownloadRules(jobs) {
    const rules = [];
    (jobs || []).forEach(job => {
        const tab = job.tab || {};
        if (job.fileType === 'pdf' && (job.key === 'foreign_report' || String(tab.label || '').includes('外资'))) {
            rules.push({
                key: job.key,
                tab_id: String(tab.id || ''),
                tab_label: String(tab.label || ''),
                file_type: 'pdf',
                min_download_count: SYSTEM_SYNC_PDF_DOWNLOAD_THRESHOLD,
            });
        }
        if (job.fileType === 'mp3' && (job.key === 'research_memo_mp3' || String(tab.label || '').includes('调研'))) {
            rules.push({
                key: job.key,
                tab_id: String(tab.id || ''),
                tab_label: String(tab.label || ''),
                file_type: 'mp3',
                min_download_count: SYSTEM_SYNC_MP3_DOWNLOAD_THRESHOLD,
            });
        }
    });
    return {
        enabled: rules.length > 0,
        rules,
    };
}

function formatSystemThresholdDownloadText(config = {}) {
    const rules = config.attachment_download_rules || {};
    const items = Array.isArray(rules.rules) ? rules.rules : [];
    const maxFilesRaw = Number(config.max_download_files);
    const maxFiles = Number.isFinite(maxFilesRaw) ? maxFilesRaw : AUTO_SYNC_TEST_MAX_FILES;
    const detail = items.map(rule => `${rule.tab_label || rule.tab_id || '目标 Tab'} ${String(rule.file_type || '').toUpperCase()}≥${rule.min_download_count || 0}`).join('；');
    if (!config.download_attachments) {
        if (rules.enabled && items.length) {
            return `达标下载规则已保存：${detail}；但当前每目标下载上限为 ${maxFiles}，本轮只同步索引`;
        }
        return '当前只同步索引，不自动下载附件';
    }
    if (!rules.enabled || !items.length) {
        return `每目标自动下载上限 ${maxFiles} 个；已下载过的不重复下载`;
    }
    return `满足规则且未下载过才自动下载：${detail}；每个目标最多 ${maxFiles} 个`;
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
        message: `Run ID：${run.id || '-'} · 状态：${syncStatusLabel(run.status)}。后台 worker 会同步主题和附件索引，完成后页面会回读本地库。`,
        details: run.requested_tabs?.length
            ? `<div class="text-xs text-slate-500">范围：${escapeHtml(run.requested_tabs.map(tab => tab.label || tab.id).join('、'))}</div>`
            : '',
    });
}

function startSyncPolling() {
    stopSyncPolling();
    if (!state.selectedGroup) return;
    state.syncPollTimer = setInterval(async () => {
        if (state.syncPollInFlight) return;
        state.syncPollInFlight = true;
        try {
            await loadSyncRuns(state.selectedGroup.group_id, { render: true });
            const stillActive = state.syncRuns.some(run => ['queued', 'running'].includes(run.status));
            if (!stillActive) {
                stopSyncPolling();
                state.attachmentStatusCache.clear();
                await loadActiveTabPage({ reset: true });
                refreshAttachmentStatusBadges();
            }
        } finally {
            state.syncPollInFlight = false;
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
        if (isSystemSyncGroupId(groupId)) {
            await loadSystemSyncBatches(groupId, { render: false });
        } else {
            state.systemSyncBatches = [];
            state.systemSyncBatchesError = null;
        }
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
    if (!isSystemSyncGroupId(targetGroupId)) {
        state.systemSyncBatches = [];
        state.systemSyncBatchesError = null;
        if (options.render !== false) {
            renderSystemSyncBatches();
        }
        return;
    }
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
    if (state.selectedGroup && !isSelectedSystemSyncGroup()) {
        state.systemSyncSchedulerStatus = null;
        if (options.render !== false) {
            renderSystemSyncSchedulerPanel();
        }
        return;
    }
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
    if (!isSelectedSystemSyncGroup()) {
        renderSyncStatus({ type: 'error', message: '系统计划任务仅适用于「前沿信息收录」。' });
        return;
    }
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
    if (!isSelectedSystemSyncGroup()) {
        renderSyncStatus({ type: 'error', message: '系统计划任务仅适用于「前沿信息收录」。' });
        return;
    }
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
    if (!isSelectedSystemSyncGroup()) {
        renderSyncStatus({ type: 'error', message: '系统计划任务仅适用于「前沿信息收录」。' });
        return;
    }
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
    if (!isSelectedSystemSyncGroup()) {
        return;
    }
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

function renderLabRuntimeItem(label, value, tone = 'muted', wide = false) {
    const safeValue = String(value || '-');
    return `
        <div class="zsxq-lab-runtime-item${wide ? ' zsxq-lab-runtime-item--wide' : ''}" data-tone="${escapeHtml(tone)}">
            <span class="zsxq-lab-runtime-label">${escapeHtml(label)}</span>
            <strong class="zsxq-lab-runtime-value" title="${escapeHtml(safeValue)}">${escapeHtml(safeValue)}</strong>
        </div>
    `;
}

function renderSystemSyncSchedulerPanel() {
    if (!els.systemSyncSchedulerPanel) return;
    if (!isSelectedSystemSyncGroup()) {
        els.systemSyncSchedulerPanel.classList.add('hidden');
        return;
    }

    const scheduler = state.systemSyncSchedulerStatus || {};
    const config = scheduler.config || state.systemSyncConfig || {};
    const running = Boolean(scheduler.running);
    const hasConfig = Boolean(config?.exists || config?.group_id) && (!config?.group_id || isSystemSyncGroupId(config.group_id));

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
        els.systemSchedulerContent.innerHTML = `<div class="zsxq-lab-runtime-empty">${escapeHtml(detail)}</div>`;
        return;
    }

    const interval = scheduler.interval_minutes || config.interval_minutes || SYSTEM_SYNC_INTERVAL_MINUTES;
    const count = config.count || SYSTEM_SYNC_COUNT;
    const downloadText = formatSystemThresholdDownloadText(config);
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

    const scheduleText = `${interval} 分钟一次 · 每批 ${count} 条`;
    const nextText = `${lastTrigger} / ${nextRun}`;
    els.systemSchedulerContent.innerHTML = `
        <div class="zsxq-lab-runtime-grid">
            ${renderLabRuntimeItem('进程', scheduler.running ? `已启动 · PID ${scheduler.pid || '-'}` : '未启动', scheduler.running ? 'success' : 'muted')}
            ${renderLabRuntimeItem('节奏', scheduleText, 'info')}
            ${renderLabRuntimeItem('下载规则', downloadText, 'info', true)}
            ${renderLabRuntimeItem('轮次', roundsText, scheduler.continuous || running ? 'success' : 'muted', true)}
            ${renderLabRuntimeItem('上次 / 下次', nextText, nextRun !== '尚未预测' ? 'success' : 'warning', true)}
        </div>
        ${scheduler.message ? `<div class="zsxq-lab-runtime-empty">${escapeHtml(scheduler.message)}</div>` : ''}
        <div class="zsxq-lab-runtime-path">日志：<span>${escapeHtml(scheduler.log_path || '-')}</span></div>
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
    const queueMessage = renderSyncQueueMessage(latest);
    const retryMessage = renderSyncRetryMessage(latest);
    const timingMessage = renderSyncTimingMessage(latest);
    renderSyncStatus({
        type: latest.status === 'failed' ? 'error' : latest.status === 'running' ? 'loading' : 'muted',
        title: '同步日志',
        message: `最近活动：${formatZsxqTime(syncRunActivityTime(latest))} · ${syncStatusLabel(latest.status)}${queueMessage ? ` · ${queueMessage}` : ''}${retryMessage ? ` · ${retryMessage}` : ''}${timingMessage ? ` · ${timingMessage}` : ''} · 新增 ${summary.topics_new || 0} · 附件下载 ${summary.attachments_downloaded || 0}`,
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
                <div>列表来源：默认读取本地数据库；只有点击同步按钮才访问星球接口。</div>
                <div>同步内容：主题元数据、附件元数据、原始 topic JSON；不会自动下载 PDF/音频文件。</div>
                <div>保存位置：数据库同步表 + <span class="font-mono">data/zsxq/${escapeHtml(String(state.selectedGroup.group_id))}/</span></div>
                <div>${escapeHtml(attachmentTypeLabel)}下载：在列表中显式勾选附件后创建下载计划。</div>
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
                const queueMessage = renderSyncQueueMessage(run);
                const retryMessage = renderSyncRetryMessage(run);
                const timingMessage = renderSyncTimingMessage(run);
                return `
                    <div role="listitem" class="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
                        <div class="flex flex-wrap items-center justify-between gap-2">
                            <span class="font-bold">${escapeHtml(formatZsxqTime(syncRunActivityTime(run)))}</span>
                            <span class="${statusClass} font-bold">${escapeHtml(syncStatusLabel(run.status))}</span>
                        </div>
                        <div class="mt-1 flex flex-wrap items-center justify-between gap-2">
                            <span>${escapeHtml(label)}</span>
                            <span>新增 ${escapeHtml(String(runSummary.topics_new || 0))} · 更新 ${escapeHtml(String(runSummary.topics_updated || 0))} · 已存在 ${escapeHtml(String(runSummary.topics_existing || 0))} · 附件下载 ${escapeHtml(String(runSummary.attachments_downloaded || 0))} · 失败 ${escapeHtml(String(runSummary.attachments_failed || 0))}</span>
                        </div>
                        ${queueMessage ? `<div class="mt-1 font-semibold text-amber-700">${escapeHtml(queueMessage)}</div>` : ''}
                        ${retryMessage ? `<div class="mt-1 font-semibold text-amber-700">${escapeHtml(retryMessage)}</div>` : ''}
                        ${timingMessage ? `<div class="mt-1 text-slate-500">${escapeHtml(timingMessage)}</div>` : ''}
                        ${limitReached ? '<div class="mt-1 text-amber-600">有内容达到单条附件上限。</div>' : ''}
                        ${run.error_message ? `<div class="mt-1 text-red-600">${escapeHtml(run.error_message)}</div>` : ''}
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function syncRunActivityTime(run) {
    if (run?.status === 'completed' || run?.status === 'failed') {
        return run.completed_at || run.updated_at || run.created_at;
    }
    return run?.updated_at || run?.started_at || run?.created_at;
}

function renderSyncQueueMessage(run) {
    if (run?.status !== 'queued') return '';
    const ahead = Number(run.queue_ahead || 0);
    const blocker = String(run.blocking_run?.group_name || '').trim();
    if (blocker && ahead > 0) return `等待“${blocker}”同步完成，前方 ${ahead} 项`;
    if (ahead > 0) return `同步队列忙，前方 ${ahead} 项`;
    return '等待同步 worker 领取';
}

function renderSyncRetryMessage(run) {
    if (run?.status !== 'retrying') return '';
    const retry = run.result?.retry_state?.upstream || {};
    const nextRetryAt = retry.next_retry_at ? formatZsxqTime(retry.next_retry_at) : '';
    const count = Number(retry.retry_number || 0);
    const limit = Number(retry.retry_limit || 0);
    const countText = count && limit ? `第 ${count}/${limit} 次` : '';
    return `${countText}${countText && nextRetryAt ? '，' : ''}${nextRetryAt ? `${nextRetryAt} 重试` : '等待重试'}`;
}

function renderSyncTimingMessage(run) {
    const createdAt = Date.parse(run?.created_at || '');
    const startedAt = Date.parse(run?.started_at || '');
    const endedAt = Date.parse(run?.completed_at || run?.updated_at || '');
    if (!Number.isFinite(createdAt) || !Number.isFinite(startedAt)) return '';
    const waitSeconds = Math.max(0, Math.round((startedAt - createdAt) / 1000));
    const parts = [];
    if (waitSeconds >= 2) parts.push(`排队 ${formatSyncDuration(waitSeconds)}`);
    if (run?.status !== 'queued' && Number.isFinite(endedAt)) {
        const runSeconds = Math.max(0, Math.round((endedAt - startedAt) / 1000));
        parts.push(`${run?.status === 'completed' || run?.status === 'failed' ? '执行' : '已执行'} ${formatSyncDuration(runSeconds)}`);
    }
    return parts.join(' · ');
}

function formatSyncDuration(seconds) {
    const value = Math.max(0, Number(seconds || 0));
    if (value < 60) return `${Math.round(value)} 秒`;
    const minutes = Math.floor(value / 60);
    const remainingSeconds = Math.round(value % 60);
    if (minutes < 60) return remainingSeconds ? `${minutes} 分 ${remainingSeconds} 秒` : `${minutes} 分`;
    const hours = Math.floor(minutes / 60);
    const remainingMinutes = minutes % 60;
    return remainingMinutes ? `${hours} 小时 ${remainingMinutes} 分` : `${hours} 小时`;
}

function renderSystemSyncBatches() {
    if (!els.syncBatchesPanel) return;
    if (!isSelectedSystemSyncGroup()) {
        els.syncBatchesPanel.classList.add('hidden');
        els.syncBatchesPanel.innerHTML = '';
        return;
    }
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
    const interval = config.interval_minutes || SYSTEM_SYNC_INTERVAL_MINUTES;
    const count = config.count || SYSTEM_SYNC_COUNT;
    const maxRounds = config.max_rounds;
    const done = config.rounds_completed || 0;
    const roundsText = maxRounds ? `${done}/${maxRounds} 轮` : `已运行 ${done} 轮 · 持续运行`;
    const cookie = systemSyncCookieReady(config) ? `Cookie 已配置（${systemSyncCookieSourceText(config)}）` : 'Cookie 未配置';
    const complete = config.testing_complete ? ' · 已达测试轮数' : '';
    const downloadText = formatSystemThresholdDownloadText(config);
    return `${interval} 分钟一次 · 每批 ${count} 条 · ${downloadText} · ${roundsText} · ${cookie}${complete}`;
}

function systemSyncCookieReady(config) {
    return Boolean(config?.credential_id || config?.cookie_db_configured || config?.cookie_env_configured);
}

function systemSyncCookieSourceText(config) {
    if (config?.credential_id || config?.cookie_source_mode === 'db' || config?.cookie_db_configured) {
        return config?.credential_id ? `DB 凭据 ${shortId(config.credential_id)}` : 'DB 凭据';
    }
    if (config?.cookie_source_mode === 'page') return '页面 Cookie';
    if (config?.cookie_source_mode === 'env' || config?.cookie_env_configured) {
        return `环境变量 ${config?.cookie_env_name || 'ZSXQ_SYNC_TEST_COOKIE'}`;
    }
    return config?.cookie_env_name ? `环境变量 ${config.cookie_env_name}` : '未配置';
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
        retrying: '重试等待',
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
        ...credentialRequestPayload(),
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

function buildLocalTopicsRequest(tab, tabState) {
    const publishFilter = currentAttachmentViewFilter() === 'publish_attention' ? 'attention' : '';
    return {
        group_id: state.selectedGroup.group_id,
        tab_id: tab.id,
        count: Number(els.countInput.value || 20),
        scope: tab.scope || 'all',
        end_time: tabState.endTime || null,
        hashtag_id: tab.hashtagId || '',
        publish_filter: publishFilter,
        publish_date_start: els.localTopicDateStartInput?.value || '',
        publish_date_end: els.localTopicDateEndInput?.value || '',
        keyword: els.localTopicKeywordInput?.value.trim() || '',
        favorite_only: state.favoriteOnly && isSelectedFavoritesGroup(),
    };
}

function localDateInputValue(date) {
    const value = date instanceof Date ? date : new Date(date);
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, '0');
    const day = String(value.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function initializeLocalTopicDateRange() {
    if (!els.localTopicDateStartInput || !els.localTopicDateEndInput) return;
    const endDate = new Date();
    const startDate = new Date(endDate);
    startDate.setDate(startDate.getDate() - 2);
    if (!els.localTopicDateStartInput.value) {
        els.localTopicDateStartInput.value = localDateInputValue(startDate);
    }
    if (!els.localTopicDateEndInput.value) {
        els.localTopicDateEndInput.value = localDateInputValue(endDate);
    }
    normalizeLocalTopicDateRange();
}

function normalizeLocalTopicDateRange(changedBoundary = '') {
    const startInput = els.localTopicDateStartInput;
    const endInput = els.localTopicDateEndInput;
    if (!startInput || !endInput) return;
    const start = startInput.value;
    const end = endInput.value;
    if (start && end && start > end) {
        if (changedBoundary === 'end') {
            startInput.value = end;
        } else {
            endInput.value = start;
        }
    }
    startInput.max = endInput.value || '';
    endInput.min = startInput.value || '';
}

async function applyLocalTopicFilters() {
    if (!state.selectedGroup || state.isLoadingMore) return;
    state.tabStates.clear();
    state.selectedTopicKeys.clear();
    state.selectedAttachmentKeys.clear();
    await loadActiveTabPage({ reset: true });
}

function withoutSensitiveCookie(payload) {
    return {
        ...payload,
        cookie: payload.cookie ? '[inline cookie]' : '',
        credential_id: payload.credential_id ? '[db credential]' : '',
    };
}

function getTabState(tabId) {
    const key = `${state.selectedGroup?.group_id || 'none'}:${tabId}`;
    if (!state.tabStates.has(key)) {
        state.tabStates.set(key, {
            id: tabId,
            topics: [],
            rawCount: 0,
            total: null,
            source: 'local_db',
            topicIds: new Set(),
            endTime: null,
            hasMore: true,
            loading: false,
            loaded: false,
            error: null,
            emptyMessage: '',
        });
    }
    return state.tabStates.get(key);
}

function resetTabState(tabState) {
    tabState.topics = [];
    tabState.rawCount = 0;
    tabState.total = null;
    tabState.source = 'local_db';
    tabState.topicIds = new Set();
    tabState.endTime = null;
    tabState.hasMore = true;
    tabState.loading = false;
    tabState.loaded = false;
    tabState.error = null;
    tabState.emptyMessage = '';
    state.selectedTopicKeys.clear();
    state.selectedAttachmentKeys.clear();
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
        const sourceLabel = tabState.source === 'local_db' ? '本地库' : '接口';
        const totalText = tabState.total === null || tabState.total === undefined
            ? `${sourceLabel} ${tabState.rawCount} 条`
            : `${sourceLabel} ${tabState.total} 条`;
        els.topicsSubtitle.textContent = [
            `Group ID: ${state.selectedGroup.group_id}`,
            tab.hashtagId ? `hashtag ${tab.hashtagId}` : `scope ${tab.scope || 'all'}`,
            totalText,
            `当前 tab ${tabState.topics.length} 条`,
            tabState.hasMore ? '可继续下滑' : '已到底',
        ].join(' · ');
    }

    renderTopicTabs();
    renderFavoriteFilterControl();
    updatePublishTabState();
    if (tabState.error && !tabState.topics.length) {
        els.topicsList.innerHTML = emptyCard(tabState.error, 'error');
        updateZsxqKnowledgeGraph();
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
    updateZsxqKnowledgeGraph();
}

async function activateTopicTab(tabId, options = {}) {
    const targetTab = getTabDefinitions().find(tab => tab.id === tabId);
    if (!targetTab) return false;

    state.activeTab = targetTab.id;
    if (!targetTab.hashtagId) els.scopeSelect.value = targetTab.scope || 'all';
    applyAttachmentDefaultsForTab(targetTab);
    state.selectedTopicKeys.clear();
    state.selectedAttachmentKeys.clear();

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
        if (menu.local_cached && menu.tab_id) {
            const tabId = String(menu.tab_id);
            const hashtagMatch = tabId.match(/^hashtag:(.+)$/);
            return {
                id: tabId,
                label,
                scope: tabId.startsWith('preset:') ? normalizePresetScope(tabId.replace(/^preset:/, '')) : 'all',
                hashtagId: hashtagMatch ? hashtagMatch[1] : '',
            };
        }

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

function preferredAttachmentTypeForTab(tab) {
    if (!isSelectedSystemSyncGroup()) {
        return '';
    }
    const label = String(tab?.label || '');
    if (label.includes('调研')) return 'mp3';
    if (label.includes('外资')) return 'pdf';
    return '';
}

function applyAttachmentDefaultsForTab(tab) {
    const preferredType = preferredAttachmentTypeForTab(tab);
    state.attachmentViewFilter = preferredType || 'all';
    if (preferredType) {
        state.attachmentType = preferredType;
    }
    if (els.attachmentTypeSelect) {
        els.attachmentTypeSelect.value = state.attachmentType;
    }
    if (els.attachmentViewFilterSelect) {
        els.attachmentViewFilterSelect.value = state.attachmentViewFilter;
    }
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

function normalizeTopicText(text, options = {}) {
    const includeWebUrl = options.includeWebUrl !== false;
    return String(text || '')
        .replace(/<e\b[^>]*\/?>/g, match => formatZsxqEntity(match, { includeWebUrl }))
        .replace(/<\/e>/g, '')
        .replace(/&nbsp;/g, ' ')
        .replace(/\r\n?/g, '\n')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
}

function normalizeTopicTitle(text) {
    return normalizeTopicText(text, { includeWebUrl: false }).replace(/\s+/g, ' ').trim();
}

function formatZsxqEntity(tag, { includeWebUrl = true } = {}) {
    const attrs = parseZsxqEntityAttributes(tag);
    const type = String(attrs.type || '').toLowerCase();
    const title = safeDecodeZsxqAttr(attrs.title || '').trim();
    const href = safeDecodeZsxqAttr(attrs.href || attrs.url || '').trim();

    if (type === 'web') {
        if (!includeWebUrl) return title || '';
        if (title && href && title !== href) return `${title}\n${href}`;
        return title || href || '';
    }
    if (title) return title;
    if (includeWebUrl && href) return href;
    return '';
}

function parseZsxqEntityAttributes(tag) {
    const attrs = {};
    String(tag || '').replace(/([a-zA-Z0-9_-]+)=["']([^"']*)["']/g, (_, key, value) => {
        attrs[key] = value;
        return '';
    });
    return attrs;
}

function safeDecodeZsxqAttr(value) {
    const raw = String(value || '');
    try {
        return decodeURIComponent(raw);
    } catch {
        return raw;
    }
}

function renderGroups(groups) {
    if (!groups.length) {
        const message = state.groupSource === 'remote'
            ? '远端没有获取到星球。请检查 Cookie 是否过期。'
            : '本地暂无星球缓存。请先执行同步，或点击“远端刷新”查看账号星球。';
        showGroupsMessage(message, 'warning');
        updateZsxqKnowledgeGraph();
        return;
    }

    els.groupsList.innerHTML = groups.map(group => {
        const active = state.selectedGroup?.group_id === group.group_id;
        const sourceBadge = group.local_cached === false ? '远端' : '本地';
        const sourceClass = group.local_cached === false
            ? 'bg-blue-50 text-blue-700'
            : 'bg-emerald-50 text-emerald-700';
        const metaParts = [
            `ID: ${group.group_id}`,
            group.topic_count ? `${group.topic_count} 篇主题` : '',
            group.tab_count ? `${group.tab_count} 个 Tab` : '',
            group.last_synced_at ? `同步 ${formatZsxqTime(group.last_synced_at)}` : '',
        ].filter(Boolean);
        return `
            <button class="w-full text-left p-4 hover:bg-blue-50 transition-colors ${active ? 'bg-blue-50' : ''}" data-group-id="${escapeHtml(String(group.group_id))}">
                <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                        <div class="font-bold text-slate-800">${escapeHtml(group.name || '未命名星球')}</div>
                        <div class="text-xs text-slate-400 mt-1">${escapeHtml(metaParts.join(' · '))}</div>
                    </div>
                    <span class="shrink-0 text-[10px] font-bold rounded-full px-2 py-1 ${sourceClass}">${escapeHtml(sourceBadge)}</span>
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
                loadTopics(group, { allowRemoteMenus: Boolean(group.remote_loaded || group.local_cached === false) });
            }
        });
    });
    updateZsxqKnowledgeGraph();
}

function renderTopics(topics, tabState = null) {
    const manualDownloadEnabled = isSelectedSystemSyncGroup();
    const viewFilter = currentAttachmentViewFilter();
    const visibleTopics = filterTopicsByAttachmentView(topics, viewFilter);
    if (!visibleTopics.length) {
        const message = tabState?.loading
            ? '正在加载...'
            : (state.favoriteOnly && isSelectedFavoritesGroup()
                ? '当前已加载范围内还没有收藏内容。'
                : (tabState?.emptyMessage || (tabState?.hasMore ? '当前页还没有匹配内容，继续下滑会尝试加载更多。' : attachmentViewEmptyText(viewFilter))));
        els.topicsList.innerHTML = emptyCard(message, tabState?.loading ? 'muted' : 'warning') + renderLoadMoreFooter(tabState);
        state.fileMap.clear();
        state.topicMap.clear();
        state.selectedAttachmentKeys.clear();
        updateBatchDownloadButton();
        updateSelectAllButton();
        renderHotRankList();
        bindLoadMoreAction();
        updateZsxqKnowledgeGraph();
        return;
    }

    state.fileMap.clear();
    state.topicMap.clear();
    els.topicsList.innerHTML = visibleTopics.map(topic => {
        const topicKey = topicKeyForRender(topic);
        state.topicMap.set(topicKey, topic);
        const content = extractContent(topic);
        const rawTitle = topic.title || topic?.talk?.text?.slice(0, 120) || topic?.article?.title || '无标题主题';
        const title = normalizeTopicTitle(rawTitle) || '无标题主题';
        const author = topic?.owner?.name || topic?.talk?.owner?.name || '未知作者';
        const created = topic.create_time || topic?.talk?.create_time || '';
        const createdText = formatZsxqTime(created);
        const allFiles = topicFiles(topic);
        const files = filterFilesByAttachmentView(allFiles, viewFilter);
        const topicSelection = topicFileSelectionState(files, topic.topic_id, topicKey);
        const images = topicImages(topic);
        const articleLink = renderTopicArticleLink(topic, content);
        const externalDocuments = renderExternalDocuments(topic);
        const hashtags = extractHashtags(topic);
        const comments = renderTopicComments(topic);
        const favoriteButton = isSelectedFavoritesGroup() ? renderFavoriteTopicButton(topic, topicKey) : '';

        return `
            <article class="rounded-lg border border-slate-200 p-4 hover:border-blue-200 hover:shadow-sm transition-all min-w-0" data-topic-key="${escapeHtml(topicKey)}">
                <div class="flex items-start justify-between gap-4 min-w-0">
                    <div class="flex items-start gap-3 min-w-0 flex-1">
                        ${manualDownloadEnabled ? `<input type="checkbox" class="topic-select-checkbox mt-1 h-4 w-4 rounded border-slate-300 text-blue-600 shrink-0" data-topic-key="${escapeHtml(topicKey)}" ${topicSelection.total ? '' : 'disabled'} ${topicSelection.allSelected ? 'checked' : ''} title="${topicSelection.total ? '勾选该主题下当前类型的全部附件' : '该主题没有匹配附件'}">` : ''}
                        <div class="min-w-0 flex-1">
                        <h4 class="font-black text-slate-900 leading-snug line-clamp-2 break-words">${escapeHtml(title)}</h4>
                        <div class="mt-1 text-xs text-slate-400">${escapeHtml(author)} ${createdText ? `· ${escapeHtml(createdText)}` : ''}</div>
                        </div>
                    </div>
                    <div class="shrink-0 flex flex-wrap justify-end items-center gap-2 max-w-[240px]">
                        ${favoriteButton}
                        <button class="copy-topic-json-btn rounded-md border border-slate-300 px-2 py-1 text-xs font-bold text-slate-500 hover:bg-slate-50" data-topic-key="${escapeHtml(topicKey)}">详情 JSON</button>
                        <span class="text-xs text-slate-400">#${escapeHtml(String(topic.topic_id || ''))}</span>
                    </div>
                </div>
                <div class="mt-3 text-sm text-slate-600 leading-6 whitespace-pre-wrap break-words">${renderTopicText(content)}</div>
                ${articleLink}
                ${externalDocuments}
                ${renderTopicHashtags(hashtags)}
                ${images.length ? renderImages(images) : ''}
                ${files.length ? renderFiles(files, topic.topic_id, topicKey) : ''}
                ${comments}
            </article>
        `;
    }).join('') + renderLoadMoreFooter(tabState);
    pruneSelectedAttachmentsToCurrentFiles();

    bindTopicSelection();
    bindFileActions();
    bindTopicActions();
    bindLoadMoreAction();
    updateBatchDownloadButton();
    renderHotRankList();
    refreshAttachmentStatusBadges();
    updateZsxqKnowledgeGraph();
}

function renderTopicComments(topic) {
    const items = Array.isArray(topic?.show_comments) ? topic.show_comments : [];
    const total = Math.max(Number(topic?.comments_count || 0), items.length);
    if (!total && !items.length) return '';
    const rows = items.map(comment => {
        const owner = comment?.owner?.name || '未知用户';
        const created = formatZsxqTime(comment?.create_time || '');
        const isReply = Boolean(comment?.parent_comment_id);
        return `
            <div class="${isReply ? 'ml-5 border-l-2 border-slate-200 pl-3' : ''}">
                <div class="text-xs font-bold text-slate-600">${escapeHtml(owner)}${created ? `<span class="ml-2 font-normal text-slate-400">${escapeHtml(created)}</span>` : ''}</div>
                <div class="mt-1 text-sm leading-5 text-slate-600 whitespace-pre-wrap break-words">${renderTopicText(comment?.text || '')}</div>
            </div>
        `;
    }).join('');
    const truncated = Boolean(topic?.comments_truncated) || items.length < total;
    return `
        <details class="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <summary class="cursor-pointer text-xs font-bold text-slate-600">评论 ${total} 条${truncated ? ' · 当前为已抓取部分' : ''}</summary>
            <div class="mt-3 space-y-3">${rows || '<div class="text-xs text-slate-400">评论内容尚未抓取</div>'}</div>
        </details>
    `;
}

function activeTabState() {
    if (!state.selectedGroup) return null;
    const tab = getActiveTabConfig();
    return tab ? getTabState(tab.id) : null;
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

function isSelectedFavoritesGroup() {
    return String(state.selectedGroup?.group_id || '') === FAVORITES_GROUP_ID;
}

function isTopicFavorite(topic) {
    return Boolean(topic?.is_favorite);
}

function renderFavoriteFilterControl() {
    if (!els.favoriteTopicsFilterBtn) return;
    const visible = isSelectedFavoritesGroup();
    els.favoriteTopicsFilterBtn.classList.toggle('hidden', !visible);
    if (!visible) return;

    const count = Number(state.favoriteTotal || 0);
    if (els.favoriteTopicsCount) els.favoriteTopicsCount.textContent = String(count);
    els.favoriteTopicsFilterBtn.setAttribute('aria-pressed', String(state.favoriteOnly));
    els.favoriteTopicsFilterBtn.classList.toggle('bg-amber-100', state.favoriteOnly);
    els.favoriteTopicsFilterBtn.classList.toggle('ring-2', state.favoriteOnly);
    els.favoriteTopicsFilterBtn.classList.toggle('ring-amber-200', state.favoriteOnly);
    const icon = els.favoriteTopicsFilterBtn.querySelector('i');
    icon?.classList.toggle('fa-regular', !state.favoriteOnly);
    icon?.classList.toggle('fa-solid', state.favoriteOnly);
}

function renderFavoriteTopicButton(topic, topicKey) {
    const saved = isTopicFavorite(topic);
    const style = saved
        ? 'border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100'
        : 'border-slate-300 bg-white text-slate-500 hover:bg-slate-50';
    return `<button type="button" class="favorite-topic-btn rounded-md border px-2 py-1 text-xs font-bold transition-colors ${style}" data-topic-key="${escapeHtml(topicKey)}" aria-pressed="${String(saved)}" title="${saved ? '取消本地收藏' : '保存到本地收藏'}"><i class="${saved ? 'fa-solid' : 'fa-regular'} fa-bookmark mr-1" aria-hidden="true"></i>${saved ? '已收藏' : '收藏'}</button>`;
}

async function toggleTopicFavorite(topic) {
    const result = await apiPost('/zsxq/topics/local/favorite', {
        group_id: state.selectedGroup?.group_id,
        topic_id: topic.topic_id,
        favorite: !isTopicFavorite(topic),
    });
    const topicId = String(topic.topic_id || '');
    state.currentTopics.forEach(item => {
        if (String(item.topic_id || '') === topicId) item.is_favorite = Boolean(result.is_favorite);
    });
    state.favoriteTotal = Number(result.favorite_total || 0);
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
    const questionText = normalizeTopicText(topic?.question?.text || '');
    const answerText = normalizeTopicText(topic?.answer?.text || '');
    if (questionText && answerText) return `提问：${questionText}\n\n回答：${answerText}`;
    if (topic?.talk?.text) return normalizeTopicText(topic.talk.text);
    if (topic?.article?.article_content) return normalizeTopicText(topic.article.article_content);
    if (topic?.article?.title) return normalizeTopicText(topic.article.title);
    if (questionText) return questionText;
    if (answerText) return answerText;
    if (topic?.files?.length) return `[附件] ${topic.files[0].name || '未命名附件'}`;
    return '无正文预览';
}

function renderTopicText(text, maxLength = 0) {
    const normalized = normalizeTopicText(text);
    const value = Number.isFinite(Number(maxLength)) && Number(maxLength) > 0
        ? truncate(normalized, Number(maxLength))
        : normalized;
    const urlRegex = /https?:\/\/[^\s<>"'）)]+/g;
    let html = '';
    let cursor = 0;
    let match;

    while ((match = urlRegex.exec(value)) !== null) {
        const url = match[0];
        html += escapeHtml(value.slice(cursor, match.index));
        html += `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="font-bold text-blue-600 hover:text-blue-700 hover:underline break-all">${escapeHtml(url)}</a>`;
        cursor = match.index + url.length;
    }

    html += escapeHtml(value.slice(cursor));
    return html || escapeHtml(value);
}

function renderTopicArticleLink(topic, content = '') {
    const link = topicArticleLink(topic);
    if (!link?.url) return '';
    if (String(content || '').includes(link.url)) return '';
    return `
        <a href="${escapeHtml(link.url)}" target="_blank" rel="noopener noreferrer" class="mt-3 inline-flex max-w-full items-center gap-2 rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-sm font-bold text-blue-700 hover:border-blue-200 hover:bg-blue-100">
            <i class="fas fa-link shrink-0"></i>
            <span class="min-w-0 truncate">${escapeHtml(link.title || '查看原文')}</span>
        </a>
    `;
}

function renderExternalDocuments(topic) {
    const links = Array.isArray(topic?.external_documents) ? topic.external_documents : [];
    if (!links.length) return '';
    const uniqueLinks = [];
    const seen = new Set();
    links.forEach((link, index) => {
        const document = link?.document || {};
        const key = String(
            document.id
            || link.external_document_id
            || document.canonical_url
            || link.canonical_url
            || document.source_url
            || link.source_url
            || `external-${index}`
        );
        if (seen.has(key)) return;
        seen.add(key);
        uniqueLinks.push(link);
    });
    return `
        <section class="external-documents-block" aria-label="关联内容">
            <div class="external-documents-heading">
                <span><i class="fas fa-link" aria-hidden="true"></i>关联内容</span>
                <span class="external-documents-count">${escapeHtml(String(uniqueLinks.length))}</span>
                <a href="linked-content-detail.html" class="ml-auto text-xs font-bold text-blue-600 hover:underline">内容中心</a>
            </div>
            <div class="external-documents-list">
                ${uniqueLinks.map(renderExternalDocumentItem).join('')}
            </div>
        </section>
    `;
}

function renderExternalDocumentItem(link) {
    const document = link?.document || {};
    const documentId = document.id || link.external_document_id || '';
    const url = safeExternalUrl(document.canonical_url || link.canonical_url || document.source_url || link.source_url);
    const title = externalDocumentTitle(link);
    const providerLabel = externalProviderLabel(document.provider || link.provider);
    const fetchMeta = externalDocumentStatusMeta('fetch', document.fetch_status);
    const summaryMeta = externalDocumentStatusMeta('summary', document.summary_status);
    const ttsMeta = externalDocumentStatusMeta('tts', document.tts_status);
    const isGenericWeb = (document.provider || link.provider) === 'generic_web';
    const summary = String(document.summary_text || '').trim();
    const fetchError = document.fetch_error ? `<div class="mt-2 text-xs text-rose-600 break-words">${escapeHtml(truncate(document.fetch_error, 240))}</div>` : '';
    const summaryError = document.summary_error ? `<div class="mt-2 text-xs text-amber-700 break-words">${escapeHtml(truncate(document.summary_error, 240))}</div>` : '';
    const ttsError = document.tts_error ? `<div class="mt-2 text-xs text-rose-600 break-words">${escapeHtml(truncate(document.tts_error, 240))}</div>` : '';
    const canSummarize = document.fetch_status === 'fetched';
    const canTts = document.fetch_status === 'fetched' || Boolean(summary);
    const audioHref = documentId ? `${apiBaseUrl()}/external-documents/${encodeURIComponent(documentId)}/tts/audio` : '';
    const primaryAction = document.fetch_status !== 'fetched'
        ? 'external-fetch'
        : (!['completed', 'summarized'].includes(document.summary_status) ? 'external-summarize' : 'external-tts');

    return `
        <article class="external-document-card" data-external-doc-id="${escapeHtml(documentId)}">
            <div class="external-document-main">
                <div class="external-document-identity">
                    <div class="external-document-icon" aria-hidden="true">
                        <i class="fas fa-file-lines"></i>
                    </div>
                    <div class="external-document-copy">
                        <div class="external-document-meta">
                            <span class="external-document-provider">${escapeHtml(providerLabel)}</span>
                            ${document.content_chars ? `<span>${escapeHtml(String(document.content_chars))} 字</span>` : ''}
                        </div>
                        ${url ? `
                            <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="external-document-title">
                                <span>${escapeHtml(title)}</span>
                                <i class="fas fa-arrow-up-right-from-square" aria-hidden="true"></i>
                            </a>
                        ` : `<div class="external-document-title"><span>${escapeHtml(title)}</span></div>`}
                        <div class="external-document-progress" aria-label="处理进度">
                            ${renderExternalDocumentStatusBadge('抓取', fetchMeta)}
                            <i class="fas fa-chevron-right" aria-hidden="true"></i>
                            ${renderExternalDocumentStatusBadge('总结', summaryMeta)}
                            <i class="fas fa-chevron-right" aria-hidden="true"></i>
                            ${renderExternalDocumentStatusBadge('语音', ttsMeta)}
                        </div>
                    </div>
                </div>
                <div class="external-document-actions">
                    <a href="linked-content-detail.html?id=${encodeURIComponent(documentId)}" class="external-document-action" title="查看更多"><i class="fas fa-eye"></i><span>查看更多</span></a>
                    ${renderExternalDocumentButton('external-fetch', documentId, 'fas fa-cloud-arrow-down', '抓取', isGenericWeb || document.fetch_status === 'fetching', primaryAction === 'external-fetch' && !isGenericWeb)}
                    ${renderExternalDocumentButton('external-summarize', documentId, 'fas fa-wand-magic-sparkles', '总结', document.summary_status === 'summarizing' || !canSummarize, primaryAction === 'external-summarize')}
                    ${renderExternalDocumentButton('external-tts', documentId, 'fas fa-volume-high', '语音', ['queued', 'running'].includes(document.tts_status) || !canTts, primaryAction === 'external-tts')}
                    ${document.tts_status === 'completed' && audioHref ? `
                        <a href="${escapeHtml(audioHref)}" target="_blank" rel="noopener noreferrer" class="external-document-action external-document-action--download" title="下载音频">
                            <i class="fas fa-download"></i><span>下载</span>
                        </a>
                    ` : ''}
                </div>
            </div>
            ${summary ? `<div class="external-document-summary">${renderExternalTextPreview(summary, 500)}</div>` : ''}
            ${fetchError || summaryError || ttsError ? `<div class="external-document-errors">${fetchError}${summaryError}${ttsError}</div>` : ''}
            <div class="external-document-detail hidden" data-external-detail="${escapeHtml(documentId)}"></div>
        </article>
    `;
}

function renderExternalDocumentButton(action, documentId, iconClass, label, disabled = false, primary = false, iconOnly = false) {
    const isDisabled = disabled || !documentId;
    return `
        <button type="button" data-external-action="${escapeHtml(action)}" data-document-id="${escapeHtml(documentId)}"
            class="external-document-action${primary ? ' external-document-action--primary' : ''}${iconOnly ? ' external-document-action--icon' : ''}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}" ${isDisabled ? 'disabled' : ''}>
            <i class="${escapeHtml(iconClass)}"></i>${iconOnly ? '' : `<span>${escapeHtml(label)}</span>`}
        </button>
    `;
}

function renderExternalDocumentStatusBadge(label, meta) {
    return `<span class="external-document-status ${meta.className}"><span class="external-document-status-dot"></span>${escapeHtml(label)} ${escapeHtml(meta.text)}</span>`;
}

function externalDocumentStatusMeta(kind, status) {
    const value = status || (kind === 'tts' ? 'not_submitted' : 'pending');
    const maps = {
        fetch: {
            registered: { text: '仅登记', className: 'border-slate-200 bg-slate-50 text-slate-500' },
            pending: { text: '待抓取', className: 'border-slate-200 bg-white text-slate-500' },
            fetching: { text: '抓取中', className: 'border-blue-200 bg-blue-50 text-blue-700' },
            fetched: { text: '已抓取', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
            empty: { text: '空正文', className: 'border-amber-200 bg-amber-50 text-amber-700' },
            failed: { text: '失败', className: 'border-rose-200 bg-rose-50 text-rose-700' },
        },
        summary: {
            pending: { text: '待总结', className: 'border-slate-200 bg-white text-slate-500' },
            summarizing: { text: '总结中', className: 'border-blue-200 bg-blue-50 text-blue-700' },
            completed: { text: '已总结', className: 'border-violet-200 bg-violet-50 text-violet-700' },
            summarized: { text: '已总结', className: 'border-violet-200 bg-violet-50 text-violet-700' },
            failed: { text: '失败', className: 'border-amber-200 bg-amber-50 text-amber-700' },
        },
        tts: {
            not_submitted: { text: '未提交', className: 'border-slate-200 bg-white text-slate-500' },
            submitted: { text: '已提交', className: 'border-blue-200 bg-blue-50 text-blue-700' },
            queued: { text: '排队中', className: 'border-blue-200 bg-blue-50 text-blue-700' },
            running: { text: '生成中', className: 'border-blue-200 bg-blue-50 text-blue-700' },
            processing: { text: '生成中', className: 'border-blue-200 bg-blue-50 text-blue-700' },
            completed: { text: '已生成', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
            failed: { text: '失败', className: 'border-rose-200 bg-rose-50 text-rose-700' },
            blocked: { text: '需处理', className: 'border-amber-200 bg-amber-50 text-amber-700' },
            canceled: { text: '已取消', className: 'border-slate-200 bg-slate-50 text-slate-500' },
        },
    };
    return maps[kind]?.[value] || { text: value, className: 'border-slate-200 bg-white text-slate-500' };
}

function externalProviderLabel(provider) {
    const labels = {
        feishu: '飞书',
        zsxq: '知识星球 item',
        wechat: '微信文章',
        generic_web: '普通网页',
    };
    return labels[provider] || provider || '外链';
}

function externalDocumentTitle(link) {
    const document = link?.document || {};
    const title = document.title || link.link_text || document.canonical_url || link.canonical_url || link.source_url || '外部文档';
    return truncate(String(title).replace(/\s+/g, ' ').trim(), 120);
}

function renderExternalTextPreview(text, limit) {
    return escapeHtml(truncate(text, limit)).replace(/\n/g, '<br>');
}

function topicArticleLink(topic) {
    const article = topic?.talk?.article || topic?.article || topic?.question?.article || topic?.answer?.article || null;
    if (!article || typeof article !== 'object') return null;
    const url = safeExternalUrl(article.article_url || article.url || article.link || article.inline_article_url);
    if (!url) return null;
    return {
        title: normalizeTopicTitle(article.title || topic.title || '查看原文') || '查看原文',
        url,
    };
}

function safeExternalUrl(value) {
    const url = String(value || '').trim();
    return /^https?:\/\//i.test(url) ? url : '';
}

function topicFiles(topic) {
    return topic.files || topic?.talk?.files || topic?.article?.files || [];
}

function topicImages(topic) {
    return dedupeTopicImages([
        ...(topic.images || []),
        ...(topic?.talk?.images || []),
        ...(topic?.article?.images || []),
        ...(topic?.question?.images || []),
        ...(topic?.answer?.images || []),
    ]);
}

function renderTopicHashtags(tags) {
    if (!tags?.length) return '';
    return `
        <div class="mt-3 flex flex-wrap gap-2">
            ${tags.map(tag => `<span class="rounded border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">${escapeHtml(tag)}</span>`).join('')}
        </div>
    `;
}

function attachmentSelectionKey({ topicId, attachmentKey, fileId, name }) {
    return [
        String(topicId || ''),
        String(attachmentKey || ''),
        String(fileId || ''),
        String(name || ''),
    ].join('::');
}

function attachmentSelectionKeyForFile(file, index, topicId) {
    const attachmentKey = attachmentKeyForFile(file, index);
    const fileId = fileIdForFile(file);
    const name = file?.name || file?.title || file?.file_name || '附件';
    return attachmentSelectionKey({ topicId, attachmentKey, fileId, name });
}

function selectableFilesForTopic(files, topicId) {
    const type = currentAttachmentType();
    return (files || [])
        .map((file, index) => ({
            file,
            selectionKey: attachmentSelectionKeyForFile(file, index, topicId),
        }))
        .filter(item => type === 'all' || fileTypeForFile(item.file) === type);
}

function topicFileSelectionState(files, topicId) {
    const selectable = selectableFilesForTopic(files, topicId);
    const selected = selectable.filter(item => state.selectedAttachmentKeys.has(item.selectionKey)).length;
    return {
        total: selectable.length,
        selected,
        allSelected: selectable.length > 0 && selected === selectable.length,
        partial: selected > 0 && selected < selectable.length,
    };
}

function currentAttachmentViewFilter() {
    state.attachmentViewFilter = els.attachmentViewFilterSelect?.value || state.attachmentViewFilter || 'all';
    return state.attachmentViewFilter;
}

function filterTopicsByAttachmentView(topics, filter = currentAttachmentViewFilter()) {
    if (filter === 'all') return topics;
    if (filter === 'publish_attention') return topics;
    return topics.filter(topic => topicFiles(topic).some(file => fileTypeForFile(file) === filter));
}

function filterFilesByAttachmentView(files, filter = currentAttachmentViewFilter()) {
    if (filter === 'all') return files;
    if (filter === 'publish_attention') return files;
    return files.filter(file => fileTypeForFile(file) === filter);
}

function attachmentViewEmptyText(filter = currentAttachmentViewFilter()) {
    const map = {
        mp3: '当前列表里没有 MP3 附件。',
        pdf: '当前列表里没有 PDF 附件。',
        publish_attention: '当前列表里没有需要人工处理的发布异常附件。',
        all: '这个筛选下没有主题。',
    };
    return map[filter] || map.all;
}

function renderImages(images) {
    const visibleImages = images.slice(0, 9);
    const cards = visibleImages.map(image => {
        const thumb = topicImageUrl(image, ['thumbnail', 'small', 'large', 'original']);
        const full = topicImageUrl(image, ['large', 'original', 'thumbnail', 'small']) || thumb;
        const width = topicImageDimension(image, 'width') || 1;
        const height = topicImageDimension(image, 'height') || 1;
        if (!thumb) return '';
        const ratio = Math.max(0.6, Math.min(1.6, height / width));
        return `
            <a href="${escapeHtml(full || thumb)}" target="_blank" rel="noopener noreferrer" class="block overflow-hidden rounded-lg border border-slate-200 bg-slate-100 hover:border-blue-300 transition-colors" style="aspect-ratio: 1 / ${ratio.toFixed(3)}">
                <img src="${escapeHtml(thumb)}" alt="主题图片" loading="lazy" referrerpolicy="no-referrer" class="h-full w-full object-cover">
            </a>
        `;
    }).filter(Boolean);
    if (!cards.length) return '';
    return `
        <div class="mt-3 grid grid-cols-2 md:grid-cols-3 gap-2">
            ${cards.join('')}
        </div>
    `;
}

function topicImageUrl(image, preferredVariants = []) {
    if (!image) return '';
    if (typeof image === 'string') return image.trim();
    if (typeof image !== 'object') return '';
    const candidates = [];
    preferredVariants.forEach(variant => {
        const value = image[variant];
        if (typeof value === 'string') {
            candidates.push(value);
        } else if (value && typeof value === 'object') {
            candidates.push(value.url, value.src, value.href, value.link, value.image_url);
        }
    });
    [
        image.thumbnail_url,
        image.small_url,
        image.large_url,
        image.original_url,
        image.url,
        image.image_url,
        image.raw_url,
        image.source_url,
        image.download_url,
        image.src,
        image.href,
        image.link,
    ].forEach(value => candidates.push(value));
    ['file', 'picture', 'photo'].forEach(key => {
        const value = image[key];
        if (typeof value === 'string') {
            candidates.push(value);
        } else if (value && typeof value === 'object') {
            candidates.push(value.url, value.src, value.href, value.link, value.image_url);
        }
    });
    return String(candidates.find(value => String(value || '').trim()) || '').trim();
}

function dedupeTopicImages(images) {
    const seen = new Set();
    return (images || []).filter(image => {
        if (!image) return false;
        const key = topicImageKey(image);
        if (!key) return true;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function topicImageKey(image) {
    if (typeof image === 'string') return image.trim();
    if (!image || typeof image !== 'object') return '';
    return String(
        image.image_id
        || image.file_id
        || image.id
        || image.hash_id
        || topicImageUrl(image, ['original', 'large', 'thumbnail', 'small'])
        || ''
    ).trim();
}

function topicImageDimension(image, axis) {
    if (!image || typeof image !== 'object') return 0;
    const directKeys = axis === 'width'
        ? ['width', 'w', 'image_width', 'natural_width']
        : ['height', 'h', 'image_height', 'natural_height'];
    const nestedKeys = ['thumbnail', 'small', 'large', 'original'];
    for (const key of directKeys) {
        const value = Number(image[key]);
        if (Number.isFinite(value) && value > 0) return value;
    }
    for (const nestedKey of nestedKeys) {
        const nested = image[nestedKey];
        if (!nested || typeof nested !== 'object') continue;
        for (const key of directKeys) {
            const value = Number(nested[key]);
            if (Number.isFinite(value) && value > 0) return value;
        }
    }
    return 0;
}

function extractHashtags(topic) {
    const chunks = [
        topic.annotation,
        topic.title,
        topic?.talk?.text,
        topic?.article?.title,
        topic?.article?.article_content,
        topic?.question?.text,
        topic?.answer?.text,
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
    const activeTab = getActiveTabConfig();
    const manualDownloadEnabled = isSelectedSystemSyncGroup();
    return `
        <div class="mt-3 space-y-2">
            ${files.map((file, index) => {
                const id = `${topicId || 'topic'}-${index}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
                const attachmentKey = attachmentKeyForFile(file, index);
                const fileId = fileIdForFile(file);
                const name = file.name || file.title || file.file_name || '附件';
                const selectionKey = attachmentSelectionKey({ topicId, attachmentKey, fileId, name });
                state.fileMap.set(id, { file, topicId, topicKey, attachmentKey, fileId, selectionKey });
                const downloadCount = getDownloadCount(file);
                const fileType = fileTypeForFile(file);
                const fileIcon = fileIconClass(fileType);
                const fileTypeLabel = fileType.toUpperCase();
                const heatClass = downloadHeatClass(activeTab, fileType, downloadCount);
                const selected = state.selectedAttachmentKeys.has(selectionKey);
                return `
                    <div class="zsxq-file-row ${heatClass} flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 min-w-0" data-file-id="${escapeHtml(id)}">
                        <div class="min-w-0 flex items-start gap-2 flex-1">
                            ${manualDownloadEnabled ? `<input type="checkbox" class="file-select-checkbox mt-1 h-4 w-4 rounded border-slate-300 text-emerald-600 shrink-0" data-file-id="${escapeHtml(id)}" ${selected ? 'checked' : ''} title="选择这个附件加入下载计划">` : ''}
                            <div class="min-w-0 flex flex-col gap-1">
                                <div class="min-w-0 flex items-center gap-2 text-sm text-slate-700">
                                <span class="shrink-0 text-[10px] font-black ${fileIcon}">${escapeHtml(fileTypeLabel)}</span>
                                <span class="break-words min-w-0">${escapeHtml(name)}</span>
                                <span class="downloaded-badge hidden shrink-0 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700">下载过</span>
                                <span class="publish-status-badge hidden shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-bold"></span>
                                <span class="download-failed-badge hidden shrink-0 rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-[10px] font-bold text-red-600">下载失败</span>
                                </div>
                                ${downloadCount !== null ? `<div class="pl-6 text-xs text-slate-400">下载 ${escapeHtml(String(downloadCount))} 次</div>` : ''}
                                <div class="download-error-text hidden pl-6 text-xs text-red-500"></div>
                            </div>
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
    renderHotRankPanelVisibility();
    if (!isSelectedSystemSyncGroup()) return;
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
            const existing = state.attachmentStatusCache.get(cacheKey) || [];
            const incoming = statuses[topicId] || [];
            state.attachmentStatusCache.set(cacheKey, mergeAttachmentStatusRecords(existing, incoming));
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
    applyAttachmentViewStatusFilter();
}

function mergeAttachmentStatusRecords(existing, incoming) {
    const incomingRecords = Array.isArray(incoming) ? incoming : [];
    const incomingKeys = new Set(incomingRecords.map(attachmentStatusRecordKey).filter(Boolean));
    const localRecords = (Array.isArray(existing) ? existing : [])
        .filter(isLocalAttachmentStatusRecord)
        .map(stripLocalPublishStatus)
        .filter(record => {
            const key = attachmentStatusRecordKey(record);
            return !key || !incomingKeys.has(key);
        });
    return [...incomingRecords, ...localRecords];
}

function isLocalAttachmentStatusRecord(record) {
    const localPath = String(record?.local_path || '');
    return localPath.startsWith('browser-download:')
        || (record?.download_status === 'failed' && !record?.id);
}

function stripLocalPublishStatus(record) {
    const copy = { ...(record || {}) };
    delete copy.publish_status;
    delete copy.published;
    delete copy.publish_submitted;
    delete copy.publish_task_id;
    delete copy.publish_source_task_id;
    delete copy.publish_topic_id;
    delete copy.publish_submitted_count;
    delete copy.publish_verified_count;
    delete copy.publish_failed_count;
    delete copy.publish_updated_at;
    return copy;
}

function attachmentStatusRecordKey(record) {
    if (!record) return '';
    return [
        record.topic_id || '',
        record.attachment_key || '',
        record.file_id || '',
        record.name || '',
    ].map(value => String(value || '')).join('|');
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
    const publishStatusBadge = row.querySelector('.publish-status-badge');
    const failedBadge = row.querySelector('.download-failed-badge');
    const errorText = row.querySelector('.download-error-text');
    const button = row.querySelector('.download-file-btn');
    const downloaded = matched?.download_status === 'downloaded';
    const failed = matched?.download_status === 'failed';
    const publishBadge = publishBadgeState(matched);
    const publishAttention = isPublishAttentionRecord(matched);
    row.dataset.publishAttention = publishAttention ? '1' : '0';

    if (downloadedBadge) {
        downloadedBadge.classList.toggle('hidden', !downloaded);
        downloadedBadge.title = downloaded ? (matched.local_path || '已通过浏览器下载') : '';
    }
    if (publishStatusBadge) {
        publishStatusBadge.textContent = publishBadge.label;
        publishStatusBadge.title = publishBadge.title;
        publishStatusBadge.className = `publish-status-badge shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-bold ${publishBadge.className}`;
        publishStatusBadge.classList.toggle('hidden', !publishBadge.label);
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
    row.classList.toggle('border-amber-300', publishAttention);
    row.classList.toggle('bg-amber-50', publishAttention);
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

function publishBadgeState(matched) {
    const status = String(matched?.publish_status || '').toLowerCase();
    const updatedAt = matched?.publish_updated_at ? ` · ${formatZsxqTime(matched.publish_updated_at)}` : '';
    const taskText = matched?.publish_task_id ? `发布任务 ${matched.publish_task_id}${updatedAt}` : `发布状态${updatedAt}`;
    if (status === 'published_unverified') {
        return {
            label: '发布待核验',
            className: 'border-amber-200 bg-amber-50 text-amber-700',
            title: `${taskText}，DB 曾标记已发布，但缺少发布主题 ID，需要人工确认。`,
        };
    }
    if (status === 'published' || matched?.published === true) {
        return {
            label: '已发布',
            className: 'border-violet-200 bg-violet-50 text-violet-700',
            title: `${taskText}，已确认可见。`,
        };
    }
    if (status === 'submitted' || matched?.publish_submitted === true) {
        return {
            label: '已提交',
            className: 'border-blue-200 bg-blue-50 text-blue-700',
            title: `${taskText}，已提交发布，可能仍在审核/待确认。`,
        };
    }
    if (status === 'publishing') {
        return {
            label: '发布中',
            className: 'border-amber-200 bg-amber-50 text-amber-700',
            title: `${taskText}，发布任务仍在执行。`,
        };
    }
    if (status === 'publish_failed') {
        return {
            label: '发布失败',
            className: 'border-red-200 bg-red-50 text-red-600',
            title: `${taskText}，上次发布失败。`,
        };
    }
    if (status === 'publish_skipped') {
        return {
            label: '发布跳过',
            className: 'border-amber-200 bg-amber-50 text-amber-700',
            title: `${taskText}，发布流程跳过了这个附件。`,
        };
    }
    return { label: '', className: 'hidden', title: '' };
}

function isPublishAttentionRecord(matched) {
    const status = String(matched?.publish_status || '').toLowerCase();
    return matched?.download_status === 'downloaded'
        && (
            matched?.publish_attention_required === true
            || ['publish_failed', 'publish_skipped', 'published_unverified'].includes(status)
        );
}

function applyAttachmentViewStatusFilter() {
    if (currentAttachmentViewFilter() !== 'publish_attention') return;
    let visibleRows = 0;
    els.topicsList.querySelectorAll('.zsxq-file-row').forEach(row => {
        const visible = row.dataset.publishAttention === '1';
        row.classList.toggle('hidden', !visible);
        if (visible) visibleRows += 1;
    });
    els.topicsList.querySelectorAll('article[data-topic-key]').forEach(article => {
        const hasVisibleFile = Boolean(article.querySelector('.zsxq-file-row:not(.hidden)'));
        article.classList.toggle('hidden', !hasVisibleFile);
    });
    const existingEmpty = document.getElementById('publish-attention-empty-card');
    if (existingEmpty) existingEmpty.remove();
    if (!visibleRows && els.topicsList) {
        els.topicsList.insertAdjacentHTML(
            'afterbegin',
            `<div id="publish-attention-empty-card">${emptyCard(attachmentViewEmptyText('publish_attention'), 'warning')}</div>`
        );
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

function numericDownloadCount(value) {
    if (value === undefined || value === null || value === '') return null;
    const match = String(value).replace(/,/g, '').match(/\d+(?:\.\d+)?/);
    if (!match) return null;
    const count = Number(match[0]);
    return Number.isFinite(count) ? count : null;
}

function downloadHeatClass(tab, fileType, downloadCount) {
    const count = numericDownloadCount(downloadCount);
    if (count === null) return '';
    const preferredType = preferredAttachmentTypeForTab(tab);
    if (preferredType !== fileType) return '';

    if (preferredType === 'pdf') {
        if (count >= 60) return 'zsxq-file-row--heat-4';
        if (count >= 50) return 'zsxq-file-row--heat-3';
        if (count >= 40) return 'zsxq-file-row--heat-2';
        if (count > 30) return 'zsxq-file-row--heat-1';
        return '';
    }

    if (preferredType === 'mp3') {
        if (count >= 30) return 'zsxq-file-row--heat-4';
        if (count >= 20) return 'zsxq-file-row--heat-3';
        if (count >= 15) return 'zsxq-file-row--heat-2';
        if (count >= 10) return 'zsxq-file-row--heat-1';
    }

    return '';
}

function currentAttachmentType() {
    state.attachmentType = els.attachmentTypeSelect?.value || state.attachmentType || 'pdf';
    return state.attachmentType;
}

function attachmentTypeText(type) {
    const map = {
        pdf: 'PDF 附件',
        mp3: 'MP3 附件',
        publish_attention: '待人工发布附件',
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
            const entries = fileEntriesForTopic(input.dataset.topicKey);
            if (input.checked) {
                state.selectedTopicKeys.add(input.dataset.topicKey);
                entries.forEach(entry => state.selectedAttachmentKeys.add(entry.selectionKey));
            } else {
                state.selectedTopicKeys.delete(input.dataset.topicKey);
                entries.forEach(entry => state.selectedAttachmentKeys.delete(entry.selectionKey));
            }
            syncSelectionControls();
            updateBatchDownloadButton();
            updateSelectAllButton();
        });
    });
    syncSelectionControls();
    updateSelectAllButton();
}

function bindLoadMoreAction() {
    const button = document.getElementById('load-more-topics-btn');
    if (button) {
        button.addEventListener('click', () => loadActiveTabPage());
    }
}

function bindFileActions() {
    els.topicsList.querySelectorAll('.file-select-checkbox').forEach(input => {
        input.addEventListener('change', () => {
            const entry = state.fileMap.get(input.dataset.fileId);
            if (!entry?.selectionKey) return;
            if (input.checked) {
                state.selectedAttachmentKeys.add(entry.selectionKey);
            } else {
                state.selectedAttachmentKeys.delete(entry.selectionKey);
            }
            syncSelectionControls();
            updateBatchDownloadButton();
            updateSelectAllButton();
        });
    });
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
    els.topicsList.querySelectorAll('.favorite-topic-btn').forEach(button => {
        button.addEventListener('click', async () => {
            const topic = state.topicMap.get(button.dataset.topicKey);
            if (!topic) return;
            const originalHTML = button.innerHTML;
            setLoading(button, true, '<i class="fas fa-circle-notch fa-spin mr-1"></i>保存中');
            try {
                await toggleTopicFavorite(topic);
                renderFavoriteFilterControl();
                if (state.favoriteOnly) {
                    await loadActiveTabPage({ reset: true });
                } else {
                    renderTopics(state.currentTopics, activeTabState());
                }
            } catch (error) {
                setLoading(button, false, '<i class="fas fa-triangle-exclamation mr-1"></i>收藏失败');
                button.title = `收藏失败：${error.message}`;
                setTimeout(() => {
                    if (button.isConnected) button.innerHTML = originalHTML;
                }, 1800);
            }
        });
    });

    els.topicsList.querySelectorAll('.copy-topic-json-btn').forEach(button => {
        button.addEventListener('click', async () => {
            const topic = state.topicMap.get(button.dataset.topicKey);
            if (!topic) return;
            let detail = topic;

            if (topic.topic_id) {
                try {
                    const result = await apiPost('/zsxq/topic', {
                        ...credentialRequestPayload(),
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

    els.topicsList.querySelectorAll('button[data-external-action]').forEach(button => {
        button.addEventListener('click', () => handleExternalDocumentAction(button));
    });
}

async function handleExternalDocumentAction(button) {
    const action = button.dataset.externalAction;
    const documentId = button.dataset.documentId;
    if (!documentId) return;

    const originalHTML = button.innerHTML;
    setLoading(button, true, externalDocumentLoadingLabel(action));

    try {
        let result;
        if (action === 'external-detail') {
            result = await apiGet(`/external-documents/${encodeURIComponent(documentId)}`);
            const document = result?.document || result;
            updateExternalDocumentInTopics(document);
            renderExternalDocumentDetailPanel(button, document);
            setLoading(button, false, originalHTML);
            return;
        }
        if (action === 'external-fetch') {
            result = await apiPost(`/external-documents/${encodeURIComponent(documentId)}/fetch`, {});
        } else if (action === 'external-summarize') {
            result = await apiPost(`/external-documents/${encodeURIComponent(documentId)}/summarize`, {});
        } else if (action === 'external-tts') {
            result = await apiPost(`/external-documents/${encodeURIComponent(documentId)}/tts`, {});
        } else {
            setLoading(button, false, originalHTML);
            return;
        }

        const document = result?.document || result;
        updateExternalDocumentInTopics(document);
        renderActiveTab();
    } catch (error) {
        setLoading(button, false, originalHTML);
        renderExternalDocumentInlineError(button, error.message || '操作失败');
    }
}

function externalDocumentLoadingLabel(action) {
    return {
        'external-detail': '读取...',
        'external-fetch': '抓取...',
        'external-summarize': '总结...',
        'external-tts': '提交...',
    }[action] || '处理中...';
}

function renderExternalDocumentDetailPanel(button, document) {
    const panel = button.closest('[data-external-doc-id]')?.querySelector('.external-document-detail');
    if (!panel || !document) return;
    const summary = String(document.summary_text || '').trim();
    const content = String(document.content_text || '').trim();
    const rawMeta = document.raw_payload || {};
    panel.classList.remove('hidden');
    panel.innerHTML = `
        <div class="space-y-3">
            ${summary ? `
                <section>
                    <div class="text-xs font-black text-slate-400">AI 总结</div>
                    <div class="mt-1 rounded-md bg-violet-50 px-3 py-2 text-sm leading-6 text-slate-700 break-words">${renderExternalTextPreview(summary, 1600)}</div>
                </section>
            ` : ''}
            ${content ? `
                <section>
                    <div class="text-xs font-black text-slate-400">飞书正文</div>
                    <div class="mt-1 max-h-80 overflow-auto rounded-md bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-700 whitespace-pre-wrap break-words">${escapeHtml(truncate(content, 4000))}</div>
                </section>
            ` : ''}
            <div class="flex flex-wrap gap-2 text-xs text-slate-400">
                ${document.fetched_at ? `<span>抓取 ${escapeHtml(formatZsxqTime(document.fetched_at) || document.fetched_at)}</span>` : ''}
                ${document.summarized_at ? `<span>总结 ${escapeHtml(formatZsxqTime(document.summarized_at) || document.summarized_at)}</span>` : ''}
                ${rawMeta.final_url ? `<a class="font-bold text-blue-600 hover:underline break-all" href="${escapeHtml(rawMeta.final_url)}" target="_blank" rel="noopener noreferrer">最终链接</a>` : ''}
            </div>
            ${!summary && !content ? '<div class="text-sm text-slate-400">暂无正文。</div>' : ''}
        </div>
    `;
}

function renderExternalDocumentInlineError(button, message) {
    const panel = button.closest('[data-external-doc-id]')?.querySelector('.external-document-detail');
    if (!panel) return;
    panel.classList.remove('hidden');
    panel.innerHTML = `<div class="text-sm font-bold text-rose-600 break-words">${escapeHtml(message)}</div>`;
}

function updateExternalDocumentInTopics(document) {
    if (!document?.id) return;
    const documentId = String(document.id);
    state.tabStates.forEach(tabState => {
        (tabState.topics || []).forEach(topic => {
            (topic.external_documents || []).forEach(link => {
                const linkDocumentId = String(link.external_document_id || link.document?.id || '');
                if (linkDocumentId !== documentId) return;
                link.external_document_id = documentId;
                link.document = { ...(link.document || {}), ...document };
            });
        });
    });
    state.currentTopics.forEach(topic => {
        (topic.external_documents || []).forEach(link => {
            const linkDocumentId = String(link.external_document_id || link.document?.id || '');
            if (linkDocumentId !== documentId) return;
            link.external_document_id = documentId;
            link.document = { ...(link.document || {}), ...document };
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
                ...credentialRequestPayload(),
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
    if (!isSelectedPublishGroup()) {
        renderPublishPanelVisibility();
        return;
    }
    if (!hasRequestCredential()) {
        setPublishStatus('请先选择 DB Cookie，或新增保存 Cookie。', 'error');
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
    appendCredentialToFormData(formData);
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
    if (!isSelectedSystemSyncGroup()) {
        state.selectedTopicKeys.clear();
        state.selectedAttachmentKeys.clear();
        hideManualDownloadControls();
        return;
    }
    const selectedEntries = getSelectedFileEntries();
    const manualPlanLimit = readManualPlanLimit();
    const limitExceeded = selectedEntries.length > manualPlanLimit;
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
        els.manualPlanLimitControl?.classList.remove('hidden');
        els.manualPlanPublishControl?.classList.remove('hidden');
        els.batchDownloadBtn.innerHTML = limitExceeded
            ? `<i class="fas fa-triangle-exclamation mr-2"></i>已选 ${selectedEntries.length} / 上限 ${manualPlanLimit}`
            : selectedEntries.length
            ? `<i class="fas fa-list-check mr-2"></i>创建下载计划（${selectedEntries.length} 个）`
            : `<i class="fas fa-list-check mr-2"></i>创建下载计划`;
        els.batchDownloadBtn.disabled = !selectedEntries.length || limitExceeded;
        els.batchDownloadBtn.classList.toggle('opacity-50', !selectedEntries.length || limitExceeded);
        els.batchDownloadBtn.title = limitExceeded
            ? `已选 ${selectedEntries.length} 个，超过本次上限 ${manualPlanLimit} 个。`
            : selectedEntries.length
            ? `创建人工计划，只下载已勾选的 ${typeLabel}。`
            : `先勾选具体附件，再创建 ${typeLabel} 下载计划。`;
    } else {
        els.batchDownloadBtn.classList.add('hidden');
        els.selectAllTopicsBtn.classList.add('hidden');
        els.attachmentTypeControl.classList.add('hidden');
        els.manualPlanLimitControl?.classList.add('hidden');
        els.manualPlanPublishControl?.classList.add('hidden');
    }
}

function hasCurrentTopicAttachments() {
    return state.currentTopics.some(topic => topicFiles(topic).length > 0);
}

function fileEntriesForTopic(topicKey) {
    const type = currentAttachmentType();
    return Array.from(state.fileMap.values()).filter(entry => (
        entry.topicKey === topicKey
        && fileMatchesAttachmentType(entry, type)
    ));
}

function getSelectableFileEntries() {
    const type = currentAttachmentType();
    return Array.from(state.fileMap.values()).filter(entry => fileMatchesAttachmentType(entry, type));
}

function getSelectableTopicKeys() {
    const keys = new Set();
    for (const entry of getSelectableFileEntries()) {
        if (entry.topicKey) keys.add(entry.topicKey);
    }
    return keys;
}

function getSelectedFileEntries() {
    return Array.from(state.fileMap.values()).filter(entry => (
        state.selectedAttachmentKeys.has(entry.selectionKey)
        && fileMatchesAttachmentType(entry)
    ));
}

function updateSelectAllButton() {
    if (!isSelectedSystemSyncGroup()) {
        els.selectAllTopicsBtn.classList.add('hidden');
        return;
    }
    const selectable = getSelectableFileEntries();
    if (!selectable.length) {
        els.selectAllTopicsBtn.classList.add('hidden');
        return;
    }
    els.selectAllTopicsBtn.classList.remove('hidden');
    const allSelected = selectable.every(entry => state.selectedAttachmentKeys.has(entry.selectionKey));
    els.selectAllTopicsBtn.innerHTML = allSelected
        ? '<i class="fas fa-square-minus mr-2"></i>取消全选'
        : '<i class="fas fa-check-double mr-2"></i>全选附件';
}

function toggleSelectAllTopics() {
    const selectable = getSelectableFileEntries();
    const allSelected = selectable.every(entry => state.selectedAttachmentKeys.has(entry.selectionKey));
    if (allSelected) {
        selectable.forEach(entry => state.selectedAttachmentKeys.delete(entry.selectionKey));
    } else {
        selectable.forEach(entry => state.selectedAttachmentKeys.add(entry.selectionKey));
    }

    syncSelectionControls();
    updateSelectAllButton();
    updateBatchDownloadButton();
}

function syncSelectionControls() {
    state.selectedTopicKeys.clear();
    els.topicsList.querySelectorAll('.file-select-checkbox').forEach(input => {
        const entry = state.fileMap.get(input.dataset.fileId);
        input.checked = Boolean(entry?.selectionKey && state.selectedAttachmentKeys.has(entry.selectionKey));
    });

    els.topicsList.querySelectorAll('.topic-select-checkbox').forEach(input => {
        const entries = fileEntriesForTopic(input.dataset.topicKey);
        const selectedCount = entries.filter(entry => state.selectedAttachmentKeys.has(entry.selectionKey)).length;
        input.checked = entries.length > 0 && selectedCount === entries.length;
        input.indeterminate = selectedCount > 0 && selectedCount < entries.length;
        if (input.checked) {
            state.selectedTopicKeys.add(input.dataset.topicKey);
        }
    });
}

function pruneSelectedAttachmentsToCurrentFiles() {
    const available = new Set(Array.from(state.fileMap.values()).map(entry => entry.selectionKey));
    for (const key of Array.from(state.selectedAttachmentKeys)) {
        if (!available.has(key)) {
            state.selectedAttachmentKeys.delete(key);
        }
    }
}

function randomDelay(minSeconds = 9, maxSeconds = 12) {
    return (minSeconds + Math.random() * (maxSeconds - minSeconds)) * 1000;
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function createManualDownloadWorkflow() {
    if (!isSelectedSystemSyncGroup()) {
        renderSyncStatus({
            type: 'error',
            message: `人工计划仅支持“前沿信息收录”（Group ID: ${SYSTEM_SYNC_GROUP_ID}）。`,
        });
        hideManualDownloadControls();
        return;
    }
    if (!hasRequestCredential()) {
        renderSyncStatus({ type: 'error', message: '请先选择 DB Cookie，或新增保存 Cookie。' });
        return;
    }
    if (!state.selectedGroup) {
        renderSyncStatus({ type: 'error', message: '请先选择一个星球。' });
        return;
    }

    const selectedEntries = getSelectedFileEntries();
    if (!selectedEntries.length) {
        renderSyncStatus({ type: 'error', message: '请先勾选至少一个 PDF/MP3 附件。' });
        return;
    }

    const manualPlanLimit = readManualPlanLimit();
    if (selectedEntries.length > manualPlanLimit) {
        renderSyncStatus({
            type: 'error',
            title: '人工计划未提交',
            message: `已选择 ${selectedEntries.length} 个附件，超过本次上限 ${manualPlanLimit} 个。请调大上限或减少勾选。`,
        });
        updateBatchDownloadButton();
        return;
    }
    localStorage.setItem(MANUAL_PLAN_LIMIT_STORAGE_KEY, String(manualPlanLimit));
    if (els.manualPlanLimitInput) {
        els.manualPlanLimitInput.value = String(manualPlanLimit);
    }

    const publishEnabled = els.manualPlanAutoPublishCheckbox?.checked === true;
    const typeLabel = attachmentTypeText(currentAttachmentType());
    const cleanerConfig = isSelectedSystemSyncGroup() ? (state.systemSyncConfig || {}) : {};
    const originalHTML = els.batchDownloadBtn.innerHTML;
    setLoading(els.batchDownloadBtn, true, '创建中...');
    renderSyncStatus({
        type: 'loading',
        title: '人工计划',
        message: `正在创建 ${selectedEntries.length} 个${typeLabel}的下载编排，只处理当前显式勾选的附件。`,
    });

    try {
        const attachmentTypes = new Set(selectedEntries.map(entry => fileTypeForFile(entry.file)));
        const payload = {
            ...credentialRequestPayload(),
            group_id: state.selectedGroup.group_id,
            group_name: state.selectedGroup.name,
            tab: getActiveTabConfig(),
            attachments: selectedEntries.map(manualDownloadAttachmentPayload),
            options: {
                max_download_files: manualPlanLimit,
                auto_pdf_clean: attachmentTypes.has('pdf') || currentAttachmentType() === 'all',
                auto_audio_clean: Array.from(attachmentTypes).some(type => ['mp3', 'm4a', 'wav', 'aac', 'flac', 'ogg'].includes(type)),
                auto_publish_after_clean: {
                    enabled: publishEnabled,
                },
                pdf_cleaner: cleanerConfig.pdf_cleaner || {},
                audio_cleaner: cleanerConfig.audio_cleaner || {},
            },
            trigger: 'zsxq-lab-manual-download-plan',
        };
        const result = await apiPost('/zsxq/manual-download-workflows', payload);
        const workflow = result?.workflow || {};
        renderSyncStatus({
            type: 'success',
            title: '人工计划已提交',
            message: `工作流 ${shortId(result?.workflow_run_id || workflow.id)} 已进入队列。`,
            details: `
                <div class="text-xs text-slate-500 leading-6">
                    <div>附件数量：${escapeHtml(String(selectedEntries.length))} 个；本次上限：${escapeHtml(String(manualPlanLimit))} 个；类型：${escapeHtml(typeLabel)}。</div>
                    <div>后续节点：下载 → 清洗${publishEnabled ? ' → 发布' : ''}。</div>
                    <div>Workflow ID：<span class="font-mono">${escapeHtml(result?.workflow_run_id || workflow.id || '-')}</span></div>
                    <div>Celery ID：<span class="font-mono">${escapeHtml(result?.celery_task_id || '-')}</span></div>
                </div>
            `,
        });
        await loadSyncRuns(state.selectedGroup.group_id, { render: false });
        await loadSystemSyncBatches(state.selectedGroup.group_id, { render: false });
        renderSystemSyncBatches();
    } catch (error) {
        renderSyncStatus({ type: 'error', title: '人工计划提交失败', message: error.message });
    } finally {
        setLoading(els.batchDownloadBtn, false, originalHTML);
        updateBatchDownloadButton();
    }
}

function manualDownloadAttachmentPayload(entry) {
    const tab = getActiveTabConfig();
    const topic = state.topicMap.get(entry.topicKey) || {};
    const file = entry.file || {};
    return {
        ...downloadMetadataPayload(entry),
        tab_label: tab?.label || '',
        hashtag_id: tab?.hashtagId || '',
        topic_title: topic.title || topic?.article?.title || extractContent(topic).slice(0, 48) || file.name || file.title || file.file_name || '附件',
        topic_content: extractContent(topic),
        author_name: topic?.owner?.name || topic?.talk?.owner?.name || '未知作者',
        create_time: topic.create_time || topic?.talk?.create_time || '',
        name: file.name || file.title || file.file_name || '附件',
        file_type: fileTypeForFile(file),
    };
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
    if (!button) return;
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
