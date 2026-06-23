const ACTIVE_CREDENTIAL_ID_STORAGE_KEY = 'zsxq_active_credential_id';
const SYNC_ACTIVE_CREDENTIAL_ID_STORAGE_KEY = 'zsxq_sync_active_credential_id';
const PUBLISH_ACTIVE_CREDENTIAL_ID_STORAGE_KEY = 'zsxq_publish_active_credential_id';
const SYSTEM_SYNC_INTERVAL_MINUTES = 25;
const SYSTEM_SYNC_MAX_ROUNDS = 0;
const SYSTEM_SYNC_COUNT = 12;
const SYSTEM_SYNC_MAX_DOWNLOAD_FILES = 40;
const SYSTEM_SYNC_PDF_DOWNLOAD_THRESHOLD = 30;
const SYSTEM_SYNC_MP3_DOWNLOAD_THRESHOLD = 15;
const SYSTEM_SYNC_AUTO_VIDEO_DISPATCH = false;
const SYSTEM_SYNC_PDF_VIDEO_THRESHOLD = 35;
const SYSTEM_SYNC_MP3_VIDEO_THRESHOLD = 25;
const SYSTEM_SYNC_AUTO_PDF_CLEAN = false;
const SYSTEM_SYNC_AUTO_AUDIO_CLEAN = false;
const SYSTEM_SYNC_AUTO_PUBLISH_AFTER_CLEAN = false;
const SYSTEM_SYNC_STATUS_POLL_MS = 15000;
const SYSTEM_SYNC_LOG_LINES = 160;
const AUTO_PUBLISH_LOG_LINES = 160;

const state = {
    cookieProfiles: [],
    credentialLoadError: '',
    activeMode: 'sync',
    contexts: {
        sync: {
            cookie: '',
            draftName: '',
            activeCookieId: localStorage.getItem(SYNC_ACTIVE_CREDENTIAL_ID_STORAGE_KEY)
                || localStorage.getItem(ACTIVE_CREDENTIAL_ID_STORAGE_KEY)
                || '',
            groups: [],
            selectedGroupId: '',
            summaryText: '请先获取星球，系统会自动识别可用于同步的 tab。',
        },
        publish: {
            cookie: '',
            draftName: '',
            activeCookieId: localStorage.getItem(PUBLISH_ACTIVE_CREDENTIAL_ID_STORAGE_KEY) || '',
            groups: [],
            selectedGroupId: '',
            summaryText: '请先获取星球，系统会自动识别可用 hashtag。',
        },
    },
    selectedTabDefinitions: [],
    syncConfig: null,
    schedulerStatus: null,
    batches: [],
    batchesError: null,
    schedulerPollTimer: null,
    logsLoaded: false,
    autoPublishConfig: null,
    autoPublishScheduler: null,
    autoPublishPreview: null,
    autoPublishRuns: [],
    autoPublishLogsRaw: '',
    autoPublishTagOptions: [],
    autoPublishCookieAccounts: [],
    autoPublishEditingCookieAccountId: '',
};

const els = {
    modeSyncBtn: document.getElementById('mode-sync-btn'),
    modePublishBtn: document.getElementById('mode-publish-btn'),
    systemSyncSection: document.getElementById('system-sync-section'),
    autoPublishSection: document.getElementById('auto-publish-section'),
    contextCardTitle: document.getElementById('context-card-title'),
    contextCardSubtitle: document.getElementById('context-card-subtitle'),
    groupBlockSubtitle: document.getElementById('group-block-subtitle'),
    workspaceCookieStatus: document.getElementById('workspace-cookie-status'),
    workspaceGroupStatus: document.getElementById('workspace-group-status'),
    workspaceSyncStatus: document.getElementById('workspace-sync-status'),
    workspacePublishStatus: document.getElementById('workspace-publish-status'),
    workspaceSidebarGroup: document.getElementById('workspace-sidebar-group'),
    workspaceSidebarSync: document.getElementById('workspace-sidebar-sync'),
    workspaceSidebarPublish: document.getElementById('workspace-sidebar-publish'),
    cookieInput: document.getElementById('cookie-input'),
    cookieProfileSelect: document.getElementById('cookie-profile-select'),
    cookieNameInput: document.getElementById('cookie-name-input'),
    cookieEditor: document.getElementById('cookie-editor'),
    cookieSummary: document.getElementById('cookie-summary'),
    cookieFormatHint: document.getElementById('cookie-format-hint'),
    saveCookieBtn: document.getElementById('save-cookie-btn'),
    newCookieBtn: document.getElementById('new-cookie-btn'),
    deleteCookieBtn: document.getElementById('delete-cookie-btn'),
    toggleCookieBtn: document.getElementById('toggle-cookie-btn'),
    loadGroupsBtn: document.getElementById('load-groups-btn'),
    loadGroupsRefreshBtn: document.getElementById('load-groups-refresh-btn'),
    groupsSelect: document.getElementById('groups-select'),
    tabsSummary: document.getElementById('tabs-summary'),
    configIntervalInput: document.getElementById('system-sync-interval-input'),
    configMaxRoundsInput: document.getElementById('system-sync-max-rounds-input'),
    configCountInput: document.getElementById('system-sync-count-input'),
    configMaxDownloadFilesInput: document.getElementById('system-sync-max-download-files-input'),
    configPdfDownloadThresholdInput: document.getElementById('system-sync-pdf-download-threshold-input'),
    configMp3DownloadThresholdInput: document.getElementById('system-sync-mp3-download-threshold-input'),
    configAutoVideoDispatchCheckbox: document.getElementById('system-sync-auto-video-dispatch-checkbox'),
    configPdfVideoThresholdInput: document.getElementById('system-sync-pdf-video-threshold-input'),
    configMp3VideoThresholdInput: document.getElementById('system-sync-mp3-video-threshold-input'),
    configAutoPdfCleanCheckbox: document.getElementById('system-sync-auto-pdf-clean-checkbox'),
    configAutoAudioCleanCheckbox: document.getElementById('system-sync-auto-audio-clean-checkbox'),
    configAutoPublishAfterCleanCheckbox: document.getElementById('system-sync-auto-publish-after-clean-checkbox'),
    saveConfigBtn: document.getElementById('system-sync-test-config-btn'),
    pageMessage: document.getElementById('page-message'),
    schedulerStatusBadge: document.getElementById('system-scheduler-status-badge'),
    schedulerContent: document.getElementById('system-scheduler-content'),
    runOnceBtn: document.getElementById('system-sync-scheduler-run-once-btn'),
    startBtn: document.getElementById('system-sync-scheduler-start-btn'),
    stopBtn: document.getElementById('system-sync-scheduler-stop-btn'),
    refreshBtn: document.getElementById('system-sync-scheduler-refresh-btn'),
    openLogsBtn: document.getElementById('system-sync-scheduler-open-logs-btn'),
    logsPanel: document.getElementById('system-sync-scheduler-logs'),
    configBadge: document.getElementById('config-status-badge'),
    configSummary: document.getElementById('config-summary'),
    configDetail: document.getElementById('config-detail'),
    batchesMini: document.getElementById('system-sync-batches-mini'),
    batchesPanel: document.getElementById('sync-batches-panel'),
    batchCount: document.getElementById('batch-count'),
    batchSectionSubtitle: document.getElementById('batch-section-subtitle'),
    autoPublishCurrentGroup: document.getElementById('ap-current-group'),
    autoPublishStrategyNameInput: document.getElementById('ap-strategy-name-input'),
    autoPublishStrategyEnabledCheckbox: document.getElementById('ap-strategy-enabled-checkbox'),
    autoPublishIntervalInput: document.getElementById('ap-interval-minutes-input'),
    autoPublishMaxRoundsInput: document.getElementById('ap-max-rounds-input'),
    autoPublishLoadTagsBtn: document.getElementById('ap-load-tags-btn'),
    autoPublishSaveBtn: document.getElementById('ap-save-strategy-btn'),
    autoPublishPreviewBtn: document.getElementById('ap-preview-btn'),
    autoPublishRunOnceBtn: document.getElementById('ap-run-once-btn'),
    autoPublishMessage: document.getElementById('ap-message'),
    autoPublishAccountNameInput: document.getElementById('ap-account-name-input'),
    autoPublishAccountCookieInput: document.getElementById('ap-account-cookie-input'),
    autoPublishAccountGroupIdsInput: document.getElementById('ap-account-group-ids-input'),
    autoPublishAccountNotesInput: document.getElementById('ap-account-notes-input'),
    autoPublishAccountEnabledCheckbox: document.getElementById('ap-account-enabled-checkbox'),
    autoPublishAccountCurrentGroupOnlyCheckbox: document.getElementById('ap-account-current-group-only-checkbox'),
    autoPublishAccountFillCurrentCookieBtn: document.getElementById('ap-account-fill-current-cookie-btn'),
    autoPublishAccountFillCurrentGroupBtn: document.getElementById('ap-account-fill-current-group-btn'),
    autoPublishAccountSaveBtn: document.getElementById('ap-account-save-btn'),
    autoPublishAccountResetBtn: document.getElementById('ap-account-reset-btn'),
    autoPublishAccountList: document.getElementById('ap-account-list'),
    autoPublishPdfEnabledCheckbox: document.getElementById('ap-pdf-enabled-checkbox'),
    autoPublishPdfSourceDirInput: document.getElementById('ap-pdf-source-dir-input'),
    autoPublishPdfSourceDirPickBtn: document.getElementById('ap-pdf-source-dir-pick-btn'),
    autoPublishPdfExtensionsInput: document.getElementById('ap-pdf-extensions-input'),
    autoPublishPdfBatchSizeInput: document.getElementById('ap-pdf-batch-size-input'),
    autoPublishPdfUploadRetryInput: document.getElementById('ap-pdf-upload-retry-input'),
    autoPublishPdfMaxAttemptsInput: document.getElementById('ap-pdf-max-attempts-input'),
    autoPublishPdfTagSelect: document.getElementById('ap-pdf-tag-select'),
    autoPublishPdfHashtagIdInput: document.getElementById('ap-pdf-hashtag-id-input'),
    autoPublishPdfHashtagTitleInput: document.getElementById('ap-pdf-hashtag-title-input'),
    autoPublishPdfMaxSizeInput: document.getElementById('ap-pdf-max-size-input'),
    autoPublishPdfBodyPrefixInput: document.getElementById('ap-pdf-body-prefix-input'),
    autoPublishPdfIncludeFilenamesCheckbox: document.getElementById('ap-pdf-include-filenames-checkbox'),
    autoPublishPdfRecursiveCheckbox: document.getElementById('ap-pdf-recursive-checkbox'),
    autoPublishAudioEnabledCheckbox: document.getElementById('ap-audio-enabled-checkbox'),
    autoPublishAudioSourceDirInput: document.getElementById('ap-audio-source-dir-input'),
    autoPublishAudioSourceDirPickBtn: document.getElementById('ap-audio-source-dir-pick-btn'),
    autoPublishAudioExtensionsInput: document.getElementById('ap-audio-extensions-input'),
    autoPublishAudioBatchSizeInput: document.getElementById('ap-audio-batch-size-input'),
    autoPublishAudioUploadRetryInput: document.getElementById('ap-audio-upload-retry-input'),
    autoPublishAudioMaxAttemptsInput: document.getElementById('ap-audio-max-attempts-input'),
    autoPublishAudioTagSelect: document.getElementById('ap-audio-tag-select'),
    autoPublishAudioHashtagIdInput: document.getElementById('ap-audio-hashtag-id-input'),
    autoPublishAudioHashtagTitleInput: document.getElementById('ap-audio-hashtag-title-input'),
    autoPublishAudioMaxSizeInput: document.getElementById('ap-audio-max-size-input'),
    autoPublishAudioBodyPrefixInput: document.getElementById('ap-audio-body-prefix-input'),
    autoPublishAudioIncludeFilenamesCheckbox: document.getElementById('ap-audio-include-filenames-checkbox'),
    autoPublishAudioRecursiveCheckbox: document.getElementById('ap-audio-recursive-checkbox'),
    autoPublishSchedulerStatusBadge: document.getElementById('ap-scheduler-status-badge'),
    autoPublishSchedulerStatus: document.getElementById('ap-scheduler-status'),
    autoPublishSchedulerRunOnceBtn: document.getElementById('ap-scheduler-run-once-btn'),
    autoPublishSchedulerStartBtn: document.getElementById('ap-scheduler-start-btn'),
    autoPublishSchedulerStopBtn: document.getElementById('ap-scheduler-stop-btn'),
    autoPublishRefreshRuntimeBtn: document.getElementById('ap-refresh-runtime-btn'),
    autoPublishRefreshPreviewBtn: document.getElementById('ap-refresh-preview-btn'),
    autoPublishPreviewPanel: document.getElementById('ap-preview-panel'),
    autoPublishRefreshRunsBtn: document.getElementById('ap-refresh-runs-btn'),
    autoPublishRunsPanel: document.getElementById('ap-runs-panel'),
    autoPublishRefreshLogsBtn: document.getElementById('ap-refresh-logs-btn'),
    autoPublishLogsPanel: document.getElementById('ap-logs-panel'),
};

function currentModeKey() {
    return state.activeMode === 'publish' ? 'publish' : 'sync';
}

function modeContext(mode = state.activeMode) {
    return state.contexts[mode === 'publish' ? 'publish' : 'sync'];
}

function syncContext() {
    return state.contexts.sync;
}

function publishContext() {
    return state.contexts.publish;
}

document.addEventListener('DOMContentLoaded', () => {
    renderCookieProfiles(true);
    updateCookieSummary('正在读取平台凭据...', 'muted');
    setCookieEditorOpen(false);
    renderDefaultConfigSummary();
    bindEvents();

    if (els.configIntervalInput) {
        els.configIntervalInput.value = String(SYSTEM_SYNC_INTERVAL_MINUTES);
    }
    if (els.configMaxRoundsInput) {
        els.configMaxRoundsInput.value = String(SYSTEM_SYNC_MAX_ROUNDS);
    }
    if (els.configCountInput) {
        els.configCountInput.value = String(SYSTEM_SYNC_COUNT);
    }
    if (els.configMaxDownloadFilesInput) {
        els.configMaxDownloadFilesInput.value = String(SYSTEM_SYNC_MAX_DOWNLOAD_FILES);
    }
    if (els.configPdfDownloadThresholdInput) {
        els.configPdfDownloadThresholdInput.value = String(SYSTEM_SYNC_PDF_DOWNLOAD_THRESHOLD);
    }
    if (els.configMp3DownloadThresholdInput) {
        els.configMp3DownloadThresholdInput.value = String(SYSTEM_SYNC_MP3_DOWNLOAD_THRESHOLD);
    }
    if (els.configAutoVideoDispatchCheckbox) {
        els.configAutoVideoDispatchCheckbox.checked = SYSTEM_SYNC_AUTO_VIDEO_DISPATCH;
    }
    if (els.configPdfVideoThresholdInput) {
        els.configPdfVideoThresholdInput.value = String(SYSTEM_SYNC_PDF_VIDEO_THRESHOLD);
    }
    if (els.configMp3VideoThresholdInput) {
        els.configMp3VideoThresholdInput.value = String(SYSTEM_SYNC_MP3_VIDEO_THRESHOLD);
    }
    if (els.configAutoPdfCleanCheckbox) {
        els.configAutoPdfCleanCheckbox.checked = SYSTEM_SYNC_AUTO_PDF_CLEAN;
    }
    if (els.configAutoAudioCleanCheckbox) {
        els.configAutoAudioCleanCheckbox.checked = SYSTEM_SYNC_AUTO_AUDIO_CLEAN;
    }
    if (els.configAutoPublishAfterCleanCheckbox) {
        els.configAutoPublishAfterCleanCheckbox.checked = SYSTEM_SYNC_AUTO_PUBLISH_AFTER_CLEAN;
    }
    if (els.autoPublishStrategyNameInput) {
        els.autoPublishStrategyNameInput.value = '知识星球自动发帖策略';
    }
    if (els.autoPublishIntervalInput) {
        els.autoPublishIntervalInput.value = '30';
    }
    if (els.autoPublishMaxRoundsInput) {
        els.autoPublishMaxRoundsInput.value = '0';
    }
    resetAutoPublishCookieAccountForm();

    initializeCookieProfiles();
    setActiveMode(resolveModeFromHash(window.location.hash), { updateHash: false });
    const initialLoadTasks = [
        loadSystemSyncSchedulerStatus({ render: false }),
        loadSystemSyncBatches({ render: false }),
        loadAutoPublishConfig({ render: false }),
        loadAutoPublishSchedulerStatus({ render: false }),
        loadAutoPublishRuns({ render: false }),
        loadAutoPublishLogs({ render: false }),
    ];
    renderSyncSchedulerPanel();
    renderBatchesPanel();
    renderAutoPublishSchedulerPanel();
    renderAutoPublishPreviewPanel();
    renderAutoPublishRunsPanel();
    renderAutoPublishLogsPanel();
    renderWorkspaceSnapshot();
    void Promise.allSettled(initialLoadTasks).then(() => {
        renderSyncSchedulerPanel();
        renderBatchesPanel();
        renderAutoPublishCurrentGroup();
        renderAutoPublishSchedulerPanel();
        renderAutoPublishRunsPanel();
        renderAutoPublishLogsPanel();
        renderWorkspaceSnapshot();
    });
    startSchedulerPolling();
});

function bindEvents() {
    els.modeSyncBtn?.addEventListener('click', () => setActiveMode('sync'));
    els.modePublishBtn?.addEventListener('click', () => setActiveMode('publish'));
    els.cookieInput.addEventListener('input', () => {
        const context = modeContext();
        context.cookie = els.cookieInput.value.trim();
        updateCookieFormatHint();
        updateCookieSummary();
    });
    els.cookieNameInput.addEventListener('input', () => {
        modeContext().draftName = (els.cookieNameInput.value || '').trim();
        updateCookieSummary();
    });
    els.cookieProfileSelect.addEventListener('change', switchCookieProfile);
    els.newCookieBtn.addEventListener('click', startNewCookieProfile);
    els.saveCookieBtn.addEventListener('click', saveCookieProfile);
    els.deleteCookieBtn.addEventListener('click', deleteCookieProfile);
    els.toggleCookieBtn.addEventListener('click', () => setCookieEditorOpen(els.cookieEditor.classList.contains('hidden')));
    els.loadGroupsBtn.addEventListener('click', () => loadGroups(currentModeKey()));
    els.loadGroupsRefreshBtn.addEventListener('click', () => loadGroups(currentModeKey()));
    els.groupsSelect.addEventListener('change', () => onGroupChange(currentModeKey()));
    els.saveConfigBtn.addEventListener('click', saveSystemSyncConfig);
    els.runOnceBtn.addEventListener('click', runSystemSyncOnce);
    els.startBtn.addEventListener('click', startSystemSyncScheduler);
    els.stopBtn.addEventListener('click', stopSystemSyncScheduler);
    els.refreshBtn.addEventListener('click', () => loadSystemSyncSchedulerStatus());
    els.openLogsBtn.addEventListener('click', toggleSchedulerLogs);
    els.autoPublishLoadTagsBtn.addEventListener('click', loadAutoPublishTags);
    els.autoPublishAccountFillCurrentCookieBtn?.addEventListener('click', fillAutoPublishAccountWithCurrentCookie);
    els.autoPublishAccountFillCurrentGroupBtn?.addEventListener('click', fillAutoPublishAccountWithCurrentGroup);
    els.autoPublishAccountSaveBtn?.addEventListener('click', saveAutoPublishCookieAccount);
    els.autoPublishAccountResetBtn?.addEventListener('click', resetAutoPublishCookieAccountForm);
    els.autoPublishPdfSourceDirPickBtn?.addEventListener('click', () => pickAutoPublishDirectory('pdf'));
    els.autoPublishAudioSourceDirPickBtn?.addEventListener('click', () => pickAutoPublishDirectory('audio'));
    els.autoPublishSaveBtn.addEventListener('click', saveAutoPublishConfig);
    els.autoPublishPreviewBtn.addEventListener('click', previewAutoPublish);
    els.autoPublishRefreshPreviewBtn.addEventListener('click', previewAutoPublish);
    els.autoPublishRunOnceBtn.addEventListener('click', runAutoPublishOnce);
    els.autoPublishSchedulerRunOnceBtn.addEventListener('click', runAutoPublishOnce);
    els.autoPublishSchedulerStartBtn.addEventListener('click', startAutoPublishScheduler);
    els.autoPublishSchedulerStopBtn.addEventListener('click', stopAutoPublishScheduler);
    els.autoPublishRefreshRuntimeBtn.addEventListener('click', refreshAutoPublishRuntime);
    els.autoPublishRefreshRunsBtn.addEventListener('click', () => loadAutoPublishRuns());
    els.autoPublishRefreshLogsBtn.addEventListener('click', () => loadAutoPublishLogs());
    els.autoPublishPdfTagSelect.addEventListener('change', () => applyAutoPublishSelectedTag('pdf'));
    els.autoPublishAudioTagSelect.addEventListener('change', () => applyAutoPublishSelectedTag('audio'));

    window.addEventListener('hashchange', handleHashChange);
    window.addEventListener('beforeunload', stopSchedulerPolling);
}

function handleHashChange() {
    setActiveMode(resolveModeFromHash(window.location.hash), { updateHash: false });
}

function resolveModeFromHash(hash = '') {
    const normalized = String(hash || '').toLowerCase();
    return normalized.includes('auto-publish') ? 'publish' : 'sync';
}

function setActiveMode(mode, options = {}) {
    state.activeMode = mode === 'publish' ? 'publish' : 'sync';
    const syncMode = state.activeMode === 'sync';

    if (els.systemSyncSection) {
        els.systemSyncSection.hidden = !syncMode;
    }
    if (els.autoPublishSection) {
        els.autoPublishSection.hidden = syncMode;
    }

    syncModeButtons();

    applyActiveCookieProfile(state.activeMode);
    renderCookieProfiles();
    renderGroups();
    renderContextPanel();
    updateCookieFormatHint();
    updateCookieSummary();
    setCookieEditorOpen(!hasRequestCredential(state.activeMode));

    if (options.updateHash === false) {
        return;
    }

    const nextHash = syncMode ? '#system-sync-section' : '#auto-publish-section';
    if (window.location.hash !== nextHash) {
        history.replaceState(null, '', nextHash);
    }
}

function syncModeButtons() {
    const syncSelected = state.activeMode === 'sync';
    if (els.modeSyncBtn) {
        els.modeSyncBtn.classList.toggle('is-active', syncSelected);
        els.modeSyncBtn.setAttribute('aria-selected', syncSelected ? 'true' : 'false');
    }
    if (els.modePublishBtn) {
        els.modePublishBtn.classList.toggle('is-active', !syncSelected);
        els.modePublishBtn.setAttribute('aria-selected', syncSelected ? 'false' : 'true');
    }
}

function apiBaseUrl() {
    return window.API_BASE_URL || window.CONFIG?.API_BASE_URL || 'http://localhost:5001/api/v1';
}

async function apiPost(endpoint, payload) {
    return apiRequest(endpoint, { method: 'POST', payload });
}

async function apiGet(endpoint) {
    return apiRequest(endpoint);
}

async function apiRequest(endpoint, options = {}) {
    const requestOptions = { method: options.method || 'GET' };
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
        throw new Error(message);
    }
    return data.data;
}

function defaultContextSummary(mode) {
    return mode === 'publish'
        ? '请先获取星球，系统会自动识别可用 hashtag。'
        : '请先获取星球，系统会自动识别可用于同步的 tab。';
}

async function initializeCookieProfiles() {
    state.credentialLoadError = '';
    renderCookieProfiles(true);
    const syncCtx = syncContext();
    const publishCtx = publishContext();

    try {
        state.cookieProfiles = await loadCookieProfiles();
    } catch (error) {
        state.cookieProfiles = [];
        state.credentialLoadError = error.message || '平台凭据读取失败';
    }

    [syncCtx, publishCtx].forEach((context, index) => {
        if (!state.cookieProfiles.some(profile => profile.id === context.activeCookieId)) {
            context.activeCookieId = '';
        }
        context.summaryText = context.summaryText || defaultContextSummary(index === 1 ? 'publish' : 'sync');
    });

    if (!syncCtx.activeCookieId && state.cookieProfiles.length) {
        syncCtx.activeCookieId = state.cookieProfiles[0].id;
    }
    if (!publishCtx.activeCookieId && state.cookieProfiles.length) {
        publishCtx.activeCookieId = state.cookieProfiles[0].id;
    }

    applyActiveCookieProfile('sync');
    applyActiveCookieProfile('publish');
    persistCookieProfiles();
    renderCookieProfiles();
    updateCookieFormatHint();
    updateCookieSummary();
    setCookieEditorOpen(!hasRequestCredential());
    renderWorkspaceSnapshot();
}

async function loadCookieProfiles() {
    const params = new URLSearchParams({
        platform: 'zsxq',
        status: 'active',
        per_page: '100',
    });
    const data = await apiGet(`/platform-credentials?${params.toString()}`);
    return Array.isArray(data?.items) ? data.items : [];
}

function persistCookieProfiles() {
    const syncActiveId = syncContext().activeCookieId;
    const publishActiveId = publishContext().activeCookieId;

    if (syncActiveId) {
        localStorage.setItem(ACTIVE_CREDENTIAL_ID_STORAGE_KEY, syncActiveId);
    } else {
        localStorage.removeItem(ACTIVE_CREDENTIAL_ID_STORAGE_KEY);
    }

    if (syncActiveId) {
        localStorage.setItem(SYNC_ACTIVE_CREDENTIAL_ID_STORAGE_KEY, syncActiveId);
    } else {
        localStorage.removeItem(SYNC_ACTIVE_CREDENTIAL_ID_STORAGE_KEY);
    }

    if (publishActiveId) {
        localStorage.setItem(PUBLISH_ACTIVE_CREDENTIAL_ID_STORAGE_KEY, publishActiveId);
    } else {
        localStorage.removeItem(PUBLISH_ACTIVE_CREDENTIAL_ID_STORAGE_KEY);
    }
}

function activeCookieProfile(mode = state.activeMode) {
    const context = modeContext(mode);
    return state.cookieProfiles.find(profile => profile.id === context.activeCookieId) || null;
}

function applyActiveCookieProfile(mode = state.activeMode) {
    const context = modeContext(mode);
    const profile = activeCookieProfile(mode);
    context.cookie = '';
    context.draftName = profile?.name || '';

    if (mode === currentModeKey()) {
        if (els.cookieNameInput) {
            els.cookieNameInput.value = context.draftName || '';
        }
        if (els.cookieInput) {
            els.cookieInput.value = '';
            els.cookieInput.placeholder = profile
                ? '如需更新这个 DB Cookie，请粘贴新的 Cookie 请求头或浏览器导出 JSON；留空只更新名称。'
                : '粘贴完整 Cookie 请求头，或浏览器导出的 Cookie JSON 数组；保存后会加密写入平台凭据 DB。';
        }
    }
}

function renderCookieProfiles(loading = false) {
    if (!els.cookieProfileSelect) return;
    if (loading) {
        els.cookieProfileSelect.innerHTML = '<option value="">读取 DB Cookie 中...</option>';
        els.cookieProfileSelect.value = '';
        if (els.deleteCookieBtn) {
            els.deleteCookieBtn.disabled = true;
            els.deleteCookieBtn.classList.add('opacity-50');
        }
        return;
    }
    if (state.credentialLoadError) {
        els.cookieProfileSelect.innerHTML = '<option value="">DB Cookie 读取失败</option>';
        els.cookieProfileSelect.value = '';
        if (els.deleteCookieBtn) {
            els.deleteCookieBtn.disabled = true;
            els.deleteCookieBtn.classList.add('opacity-50');
        }
        return;
    }

    const emptyOption = '<option value="">新增 DB Cookie</option>';
    if (!state.cookieProfiles.length) {
        els.cookieProfileSelect.innerHTML = emptyOption;
        els.cookieProfileSelect.value = '';
        if (els.deleteCookieBtn) {
            els.deleteCookieBtn.disabled = true;
            els.deleteCookieBtn.classList.add('opacity-50');
        }
        return;
    }

    els.cookieProfileSelect.innerHTML = emptyOption + state.cookieProfiles.map(profile => {
        const label = credentialProfileLabel(profile);
        return `<option value="${escapeHtml(profile.id)}">${escapeHtml(label)}</option>`;
    }).join('');
    els.cookieProfileSelect.value = modeContext().activeCookieId || '';
    if (els.deleteCookieBtn) {
        const deletable = Boolean(modeContext().activeCookieId);
        els.deleteCookieBtn.disabled = !deletable;
        els.deleteCookieBtn.classList.toggle('opacity-50', !deletable);
    }
}

function switchCookieProfile() {
    modeContext().activeCookieId = els.cookieProfileSelect.value;
    applyActiveCookieProfile();
    persistCookieProfiles();
    updateCookieFormatHint();
    updateCookieSummary();
    setCookieEditorOpen(!hasRequestCredential());
    resetWorkspaceForMode();
}

function startNewCookieProfile() {
    const context = modeContext();
    context.activeCookieId = '';
    context.cookie = '';
    context.draftName = '';
    if (els.cookieProfileSelect) {
        els.cookieProfileSelect.value = '';
    }
    if (els.cookieNameInput) {
        els.cookieNameInput.value = '';
    }
    if (els.cookieInput) {
        els.cookieInput.value = '';
    }
    persistCookieProfiles();
    renderCookieProfiles();
    updateCookieFormatHint();
    updateCookieSummary();
    setCookieEditorOpen(true);
    resetWorkspaceForMode();
}

async function saveCookieProfile() {
    const cookie = (els.cookieInput.value || '').trim();
    const context = modeContext();
    const existing = activeCookieProfile();
    if (!cookie && !existing) {
        updateCookieSummary('新增 DB Cookie 需要填写 Cookie / JSON。', 'error');
        return;
    }

    const token = extractCookieToken(cookie);
    const name = (els.cookieNameInput.value || '').trim()
        || existing?.name
        || context.draftName
        || (token ? `Cookie ${maskToken(token)}` : '未命名 Cookie');
    const payload = {
        platform: 'zsxq',
        name,
        credential_type: cookie ? credentialTypeForPayload(cookie) : (existing?.credential_type || 'cookie_header'),
        purposes: Array.isArray(existing?.purposes) && existing.purposes.length
            ? existing.purposes
            : ['zsxq.publish', 'zsxq.sync'],
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
        context.activeCookieId = account?.id || existing?.id || '';
        context.cookie = '';
        context.draftName = name;
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
    const currentId = modeContext().activeCookieId;
    if (!currentId) {
        return;
    }
    const profile = activeCookieProfile();
    const label = credentialProfileLabel(profile);
    if (!window.confirm(`确认删除 DB Cookie「${label}」？`)) return;

    const originalHTML = els.deleteCookieBtn.innerHTML;
    setLoading(els.deleteCookieBtn, true, '删除中...');
    try {
        await apiRequest(`/platform-credentials/${encodeURIComponent(currentId)}`, { method: 'DELETE' });
        Object.values(state.contexts).forEach((context) => {
            if (context.activeCookieId === currentId) {
                context.activeCookieId = '';
                context.cookie = '';
                context.draftName = '';
            }
        });
        persistCookieProfiles();
        await initializeCookieProfiles();
        updateCookieSummary('DB Cookie 已删除', 'success');
        resetWorkspaceForMode();
    } catch (error) {
        updateCookieSummary(`删除失败：${error.message}`, 'error');
    } finally {
        setLoading(els.deleteCookieBtn, false, originalHTML);
        renderCookieProfiles();
    }
}

function currentCookieValue(mode = state.activeMode) {
    const context = modeContext(mode);
    if (mode === currentModeKey()) {
        const inputValue = (els.cookieInput?.value || '').trim();
        if (inputValue) {
            context.cookie = inputValue;
        }
        const draftName = (els.cookieNameInput?.value || '').trim();
        if (draftName) {
            context.draftName = draftName;
        }
    }
    return String(context.cookie || '').trim();
}

function resolveContextCookieMeta(mode) {
    const context = modeContext(mode);
    const profile = activeCookieProfile(mode);
    const cookie = currentCookieValue(mode);
    const token = extractCookieToken(cookie);
    const name = (mode === currentModeKey()
        ? (els.cookieNameInput?.value || '').trim()
        : context.draftName) || profile?.name || '';

    return {
        text: profile
            ? credentialProfileLabel(profile)
            : (cookie ? (name || (token ? maskToken(token) : '待保存')) : '未准备'),
        tone: profile || cookie ? 'success' : 'idle',
    };
}

function hasRequestCredential(mode = currentModeKey()) {
    return Boolean(modeContext(mode).activeCookieId);
}

function credentialRequestPayload(mode = currentModeKey()) {
    const credentialId = modeContext(mode).activeCookieId;
    return credentialId ? { credential_id: credentialId } : {};
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

function resolveContextGroupMeta(mode) {
    const context = modeContext(mode);
    const fallbackConfig = mode === 'publish' ? state.autoPublishConfig : state.syncConfig;
    const groupId = String(context.selectedGroupId || fallbackConfig?.group_id || '').trim();
    const group = (context.groups || []).find(item => String(item.group_id) === groupId);
    const groupName = group?.name || fallbackConfig?.group_name || '';
    return {
        id: groupId,
        name: groupName,
        text: groupId ? `${groupName || groupId}${groupName ? ` (${groupId})` : ''}` : '未选择',
    };
}

function contextCardMeta(mode = state.activeMode) {
    return mode === 'publish'
        ? {
            title: '自动发帖上下文',
            subtitle: '当前侧栏只服务于自动发帖，可独立选择发帖账号和发帖星球。',
            groupSubtitle: '选择自动发帖要使用的星球。',
        }
        : {
            title: '系统同步上下文',
            subtitle: '当前侧栏只服务于系统同步，不会再跟自动发帖共用同一组星球上下文。',
            groupSubtitle: '选择系统同步要使用的星球。',
        };
}

function renderContextPanel() {
    const meta = contextCardMeta();
    const context = modeContext();
    if (els.contextCardTitle) {
        els.contextCardTitle.textContent = meta.title;
    }
    if (els.contextCardSubtitle) {
        els.contextCardSubtitle.textContent = meta.subtitle;
    }
    if (els.groupBlockSubtitle) {
        els.groupBlockSubtitle.textContent = meta.groupSubtitle;
    }
    if (els.tabsSummary) {
        els.tabsSummary.textContent = context.summaryText || defaultContextSummary(state.activeMode);
    }
}

function currentSyncStatusMeta() {
    const scheduler = state.schedulerStatus || {};
    const config = scheduler.config || state.syncConfig || {};
    if (scheduler.running) {
        return {
            text: `运行中 · ${config.interval_minutes || SYSTEM_SYNC_INTERVAL_MINUTES} 分钟`,
            tone: 'success',
        };
    }
    if (config.exists || config.group_id) {
        return {
            text: '已配置，待运行',
            tone: config.cookie_env_configured ? 'success' : 'warning',
        };
    }
    if (syncContext().selectedGroupId) {
        return {
            text: '待保存配置',
            tone: 'warning',
        };
    }
    return {
        text: '未运行',
        tone: 'idle',
    };
}

function currentAutoPublishStatusMeta() {
    const scheduler = state.autoPublishScheduler || {};
    const config = scheduler.config || state.autoPublishConfig || {};
    if (scheduler.running) {
        return {
            text: `运行中 · ${config.interval_minutes || 30} 分钟`,
            tone: 'success',
        };
    }
    if (config.exists || config.group_id || currentAutoPublishGroupId()) {
        return {
            text: config.cookie_pool_ready
                ? `策略已保存 · ${config.active_cookie_account_count || 0} 个账号可用`
                : '策略已保存',
            tone: (config.cookie_pool_ready || config.cookie_env_configured) ? 'success' : 'warning',
        };
    }
    return {
        text: '未运行',
        tone: 'idle',
    };
}

function setWorkspaceStatusText(element, text, tone = 'idle') {
    if (!element) return;
    element.textContent = text;
    element.dataset.tone = tone;
}

function renderWorkspaceSnapshot() {
    const syncCookieMeta = resolveContextCookieMeta('sync');
    const publishCookieMeta = resolveContextCookieMeta('publish');
    const syncGroupMeta = resolveContextGroupMeta('sync');
    const publishGroupMeta = resolveContextGroupMeta('publish');
    const currentGroupMeta = resolveContextGroupMeta(state.activeMode);
    const syncMeta = currentSyncStatusMeta();
    const publishMeta = currentAutoPublishStatusMeta();

    setWorkspaceStatusText(
        els.workspaceCookieStatus,
        `同步：${syncCookieMeta.text} / 发帖：${publishCookieMeta.text}`,
        (syncCookieMeta.tone === 'success' || publishCookieMeta.tone === 'success') ? 'success' : 'idle',
    );
    setWorkspaceStatusText(
        els.workspaceGroupStatus,
        `同步：${syncGroupMeta.text} / 发帖：${publishGroupMeta.text}`,
        (syncGroupMeta.id || publishGroupMeta.id) ? 'success' : 'idle',
    );
    setWorkspaceStatusText(els.workspaceSyncStatus, syncMeta.text, syncMeta.tone);
    setWorkspaceStatusText(els.workspacePublishStatus, publishMeta.text, publishMeta.tone);
    setWorkspaceStatusText(els.workspaceSidebarGroup, currentGroupMeta.text, currentGroupMeta.id ? 'success' : 'idle');
    setWorkspaceStatusText(els.workspaceSidebarSync, syncMeta.text, syncMeta.tone);
    setWorkspaceStatusText(els.workspaceSidebarPublish, publishMeta.text, publishMeta.tone);
}

function resetWorkspaceForMode(mode = state.activeMode) {
    const context = modeContext(mode);
    context.groups = [];
    context.selectedGroupId = '';
    context.summaryText = defaultContextSummary(mode);

    if (mode === 'publish') {
        clearAutoPublishTagOptions();
        renderAutoPublishCurrentGroup();
    } else {
        state.selectedTabDefinitions = [];
        state.syncConfig = null;
        state.batches = [];
        state.batchesError = null;
        renderSyncOverviewCard();
        renderBatchesPanel();
    }

    renderGroups();
    renderContextPanel();
    renderWorkspaceSnapshot();
}

function setCookieEditorOpen(open) {
    if (!els.cookieEditor) return;
    els.cookieEditor.classList.toggle('hidden', !open);
    if (els.toggleCookieBtn) {
        els.toggleCookieBtn.textContent = open ? '收起' : '修改';
    }
    if (open) {
        updateCookieFormatHint();
    } else if (els.cookieFormatHint) {
        els.cookieFormatHint.classList.add('hidden');
    }
}

function updateCookieSummary(message = '', type = '') {
    const value = (els.cookieInput.value || '').trim();
    if (message) {
        const classByType = type === 'error'
            ? 'border-red-200 bg-red-50 text-red-600'
            : type === 'success'
            ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
            : 'border-slate-200 bg-slate-50 text-slate-600';
        els.cookieSummary.className = `mt-2 rounded-lg border px-3 py-2 text-xs ${classByType}`;
        els.cookieSummary.textContent = message;
        renderWorkspaceSnapshot();
        return;
    }

    if (state.credentialLoadError) {
        els.cookieSummary.className = 'mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600';
        els.cookieSummary.textContent = `平台凭据读取失败：${state.credentialLoadError}`;
        renderWorkspaceSnapshot();
        return;
    }

    const profile = activeCookieProfile();
    if (profile) {
        els.cookieSummary.className = 'mt-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700';
        els.cookieSummary.textContent = value
            ? `将更新 DB Cookie：${credentialProfileLabel(profile)}`
            : `使用 DB Cookie：${credentialProfileLabel(profile)}`;
        renderWorkspaceSnapshot();
        return;
    }

    if (!value) {
        els.cookieSummary.className = 'mt-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500';
        els.cookieSummary.textContent = '未选择 DB Cookie';
        renderWorkspaceSnapshot();
        return;
    }

    const token = extractCookieToken(value);
    const name = (els.cookieNameInput.value || '').trim();
    els.cookieSummary.className = 'mt-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700';
    els.cookieSummary.textContent = token
        ? `待保存到 DB：${name ? `${name} · ` : ''}zsxq_access_token：${maskToken(token)}`
        : '待保存到 DB Cookie';
    renderWorkspaceSnapshot();
}

function updateCookieFormatHint() {
    const value = (els.cookieInput.value || '').trim();
    if (!els.cookieFormatHint || !value) {
        if (els.cookieFormatHint) {
            els.cookieFormatHint.classList.add('hidden');
            els.cookieFormatHint.textContent = '';
        }
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

function extractCookieToken(value) {
    if (!value) return '';
    const trimmed = String(value).trim();

    if (trimmed.startsWith('[') || trimmed.startsWith('{')) {
        try {
            const parsed = JSON.parse(trimmed);
            const items = Array.isArray(parsed) ? parsed : [parsed];
            const tokenItem = items.find(item => item?.name === 'zsxq_access_token');
            return tokenItem?.value || '';
        } catch {
            return '';
        }
    }

    const match = trimmed.match(/(?:^|;\s*)zsxq_access_token=([^;]+)/);
    return match ? match[1] : '';
}

function maskToken(token) {
    if (!token) return '未检测到';
    return token.length <= 14 ? '****' : `${token.slice(0, 8)}...${token.slice(-6)}`;
}

async function loadGroups(mode = currentModeKey()) {
    const context = modeContext(mode);
    if (!hasRequestCredential(mode)) {
        if (mode === 'publish') {
            showAutoPublishMessage('warning', '无法获取星球', '请先选择 DB Cookie，或新增保存 Cookie。');
        } else {
            renderMessage('warning', '请先选择 DB Cookie，或新增保存 Cookie。');
        }
        return;
    }

    setLoading(els.loadGroupsBtn, true, '获取中...');
    setLoading(els.loadGroupsRefreshBtn, true, '获取中...');

    try {
        const result = await apiPost('/zsxq/groups', { ...credentialRequestPayload(mode), count: 50 });
        context.groups = result?.resp_data?.groups || [];
        renderGroups();
        if (context.groups.length) {
            const preferredGroupId = String(
                context.selectedGroupId
                || (mode === 'publish' ? state.autoPublishConfig?.group_id : state.syncConfig?.group_id)
                || context.groups[0]?.group_id
                || ''
            ).trim();
            const matchedGroup = context.groups.find(item => String(item.group_id) === preferredGroupId) || context.groups[0];
            if (matchedGroup?.group_id) {
                context.selectedGroupId = String(matchedGroup.group_id);
                els.groupsSelect.value = context.selectedGroupId;
                await onGroupChange(mode);
            }
        }
        if (mode === 'publish') {
            showAutoPublishMessage('success', '星球已刷新', `已加载 ${context.groups.length} 个星球。`);
        } else {
            renderMessage('success', `已加载 ${context.groups.length} 个星球。`);
        }
    } catch (error) {
        context.groups = [];
        renderGroups();
        if (mode === 'publish') {
            showAutoPublishMessage('error', '获取星球失败', error.message);
        } else {
            renderMessage('error', `获取星球失败：${error.message}`);
        }
    } finally {
        setLoading(els.loadGroupsBtn, false, '<i class="fas fa-satellite-dish mr-2"></i>获取星球');
        setLoading(els.loadGroupsRefreshBtn, false, '<i class="fas fa-rotate mr-2"></i>刷新');
    }
}

function renderGroups() {
    const mode = currentModeKey();
    const context = modeContext(mode);
    const fallbackConfig = mode === 'publish' ? state.autoPublishConfig : state.syncConfig;
    if (!els.groupsSelect) return;
    if (!context.groups.length) {
        const fallbackId = String(context.selectedGroupId || fallbackConfig?.group_id || '').trim();
        const fallbackName = fallbackConfig?.group_name || fallbackId;
        els.groupsSelect.innerHTML = fallbackId
            ? `<option value="${escapeHtml(fallbackId)}">${escapeHtml(fallbackName || fallbackId)} (${escapeHtml(fallbackId)})</option>`
            : '<option value="">暂无星球，请先获取</option>';
        els.groupsSelect.value = fallbackId;
        renderAutoPublishCurrentGroup();
        renderWorkspaceSnapshot();
        return;
    }

    els.groupsSelect.innerHTML = '<option value="">请选择星球</option>' + context.groups
        .map(group => `<option value="${escapeHtml(String(group.group_id))}">${escapeHtml(group.name || `星球 ${group.group_id}`)} (${escapeHtml(String(group.group_id))})</option>`)
        .join('');
    if (context.selectedGroupId) {
        els.groupsSelect.value = String(context.selectedGroupId);
    }
    renderAutoPublishCurrentGroup();
    renderWorkspaceSnapshot();
}

async function onGroupChange(mode = currentModeKey()) {
    const context = modeContext(mode);
    const groupId = String(els.groupsSelect.value || '').trim();
    context.selectedGroupId = groupId;
    context.summaryText = defaultContextSummary(mode);

    if (mode === 'publish') {
        clearAutoPublishTagOptions();
    } else {
        state.selectedTabDefinitions = [];
        state.syncConfig = null;
    }

    if (!groupId) {
        context.summaryText = '请先选择星球。';
        renderContextPanel();
        if (mode === 'sync') {
            renderSyncOverviewCard();
        }
        renderAutoPublishCurrentGroup();
        renderAutoPublishCookieAccounts();
        renderWorkspaceSnapshot();
        return;
    }

    if (!hasRequestCredential(mode)) {
        context.summaryText = mode === 'publish'
            ? '当前未选择 DB Cookie，无法加载标签菜单。'
            : '当前未选择 DB Cookie，无法加载星球 tab。';
        renderContextPanel();
        renderAutoPublishCurrentGroup();
        renderWorkspaceSnapshot();
        return;
    }

    setLoading(els.saveConfigBtn, true, '检测中...');
    try {
        const menuResult = await apiPost('/zsxq/menus', {
            ...credentialRequestPayload(mode),
            group_id: groupId,
        });
        const menus = menuResult?.resp_data?.menus || [];
        if (mode === 'publish') {
            hydrateAutoPublishTagsFromMenus(menus);
            context.summaryText = state.autoPublishTagOptions.length
                ? `已识别 ${state.autoPublishTagOptions.length} 个 hashtag，可直接给 PDF / 音频规则选择。`
                : '当前星球菜单里没有识别到 hashtag 标签，可继续手动填写标签 ID / 名称。';
        } else {
            const tabs = buildOfficialMenuTabs(menus);
            state.selectedTabDefinitions = tabs;
            renderTabSummary(tabs, mode);

            const jobs = buildSystemJobs(tabs);
            context.summaryText = !jobs.length
                ? '未检测到外资研报或调研纪要 Tab。请确认该星球是否包含目标 tab。'
                : jobs
                    .map(job => `${job.tab.label}（${job.fileType === 'pdf' ? 'PDF' : 'MP3'}）`)
                    .join('；');
        }
        renderContextPanel();
    } catch (error) {
        context.summaryText = `读取星球菜单失败：${error.message}`;
        renderContextPanel();
        if (mode === 'publish') {
            showAutoPublishMessage('warning', '读取星球菜单失败', error.message);
        } else {
            renderMessage('warning', `读取星球菜单失败：${error.message}`);
        }
    } finally {
        setLoading(els.saveConfigBtn, false, '<i class="fas fa-floppy-disk mr-2"></i>保存系统同步配置');
    }

    if (mode === 'sync') {
        await loadSystemSyncBatches(groupId, { render: false });
        renderSyncOverviewCard();
    }
    renderAutoPublishCurrentGroup();
    renderAutoPublishCookieAccounts();
    renderWorkspaceSnapshot();
}

function buildOfficialMenuTabs(menus) {
    return (menus || []).map(menu => {
        const label = String(cleanMenuTitle(menu.title || menu.name || menu.label || '未命名')).trim();
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
                id: `preset:${presetType || menu.menu_id || 'unknown'}`,
                label,
                scope: normalizePresetScope(presetType),
            };
        }

        return null;
    }).filter(Boolean);
}

function cleanMenuTitle(title) {
    return String(title || '').replace(/^#|#$/g, '').trim() || '未命名';
}

function extractMenuHashtagId(menu) {
    return menu?.hashtag?.hashtag_id
        || menu?.hashtag_id
        || menu?.target?.hashtag_id
        || menu?.target?.id
        || (menu?.type === 'hashtag' ? menu.menu_id : null);
}

function extractMenuPresetType(menu) {
    if (!menu?.preset && !menu?.preset_type && !menu?.type) return null;
    return menu.preset_type || menu.type || menu.menu_id;
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

function renderTabSummary(tabs, mode = 'sync') {
    const context = modeContext(mode);
    if (!tabs.length) {
        context.summaryText = '未找到可识别 tab。';
        if (mode === currentModeKey()) {
            els.tabsSummary.textContent = context.summaryText;
        }
        return;
    }
    const allNames = tabs.map(tab => tab.label).join('、');
    context.summaryText = `检测到 ${tabs.length} 个 tab：${allNames}`;
    if (mode === currentModeKey()) {
        els.tabsSummary.textContent = context.summaryText;
    }
}

function buildSystemJobs(tabs = state.selectedTabDefinitions) {
    const foreignReportTab = tabs.find(tab => tab.label.includes('外资研报'));
    const memoTab = tabs.find(tab => tab.label.includes('调研纪要'));
    const jobs = [
        foreignReportTab ? { key: 'foreign_report', modeCode: 'fr', tab: foreignReportTab, fileType: 'pdf' } : null,
        memoTab ? { key: 'research_memo_mp3', modeCode: 'rm', tab: memoTab, fileType: 'mp3' } : null,
    ].filter(Boolean);

    if (!jobs.length && tabs.length >= 2) {
        return [
            { key: 'default_one', modeCode: 'all', tab: tabs[0], fileType: 'all' },
            { key: 'default_two', modeCode: 'all', tab: tabs[1], fileType: 'all' },
        ];
    }

    return jobs;
}

function buildAttachmentDownloadRules(jobs, pdfThreshold, mp3Threshold) {
    const rules = [];
    (jobs || []).forEach(job => {
        const tab = job.tab || {};
        if (job.fileType === 'pdf' && (job.key === 'foreign_report' || String(tab.label || '').includes('外资'))) {
            rules.push({
                key: job.key,
                tab_id: String(tab.id || ''),
                tab_label: String(tab.label || ''),
                file_type: 'pdf',
                min_download_count: Number(pdfThreshold),
            });
        }
        if (job.fileType === 'mp3' && (job.key === 'research_memo_mp3' || String(tab.label || '').includes('调研'))) {
            rules.push({
                key: job.key,
                tab_id: String(tab.id || ''),
                tab_label: String(tab.label || ''),
                file_type: 'mp3',
                min_download_count: Number(mp3Threshold),
            });
        }
    });
    return {
        enabled: rules.length > 0,
        rules,
    };
}

function attachmentDownloadRules(config = {}) {
    const value = config.attachment_download_rules;
    if (!value || typeof value !== 'object') {
        return { enabled: false, rules: [] };
    }
    const rules = Array.isArray(value.rules) ? value.rules : [];
    return {
        enabled: value.enabled === true && rules.length > 0,
        rules,
    };
}

function formatThresholdDownloadText(config = {}, fallbackMaxFiles = SYSTEM_SYNC_MAX_DOWNLOAD_FILES) {
    const rules = attachmentDownloadRules(config);
    const fallbackNumber = Number(fallbackMaxFiles);
    const configLimitNumber = Number(config.max_download_files);
    const maxFiles = Number.isFinite(fallbackNumber)
        ? fallbackNumber
        : (Number.isFinite(configLimitNumber) ? configLimitNumber : SYSTEM_SYNC_MAX_DOWNLOAD_FILES);
    const ruleText = rules.rules.map(rule => {
        const tab = rule.tab_label || rule.tab_id || '目标 Tab';
        const type = String(rule.file_type || '').toUpperCase() || '附件';
        return `${tab} ${type}≥${rule.min_download_count || 0}`;
    }).join('；');
    if (!config.download_attachments) {
        if (rules.enabled) {
            return `达标下载规则已保存：${ruleText}；但当前每目标下载上限为 ${maxFiles}，本轮只同步索引`;
        }
        return '当前只同步索引，不自动下载附件';
    }
    if (!rules.enabled) {
        return `每目标自动下载上限：${maxFiles}；已下载过的不重复下载`;
    }
    return `满足规则且未下载过才自动下载：${ruleText}；每个目标最多 ${maxFiles} 个`;
}

function downloadThresholdFromConfig(config, fileType, fallback) {
    const rules = attachmentDownloadRules(config).rules || [];
    const matched = rules.find(rule => String(rule.file_type || '').toLowerCase() === fileType);
    return parseBoundedInt(matched?.min_download_count, fallback, 0, 100000);
}

function buildAutoVideoDispatchConfig(enabled, pdfThreshold, mp3Threshold) {
    return {
        enabled: Boolean(enabled),
        pdf_min_download_count: Number(pdfThreshold),
        mp3_min_download_count: Number(mp3Threshold),
        wait_for_completion: false,
    };
}

function autoVideoDispatchConfig(config = {}) {
    const value = config.auto_video_dispatch;
    if (!value || typeof value !== 'object') {
        return buildAutoVideoDispatchConfig(
            SYSTEM_SYNC_AUTO_VIDEO_DISPATCH,
            SYSTEM_SYNC_PDF_VIDEO_THRESHOLD,
            SYSTEM_SYNC_MP3_VIDEO_THRESHOLD,
        );
    }
    return buildAutoVideoDispatchConfig(
        value.enabled === true,
        parseBoundedInt(value.pdf_min_download_count, SYSTEM_SYNC_PDF_VIDEO_THRESHOLD, 0, 100000),
        parseBoundedInt(value.mp3_min_download_count, SYSTEM_SYNC_MP3_VIDEO_THRESHOLD, 0, 100000),
    );
}

function formatAutoVideoDispatchText(config = {}) {
    const value = autoVideoDispatchConfig(config);
    if (!value.enabled) {
        return '视频编排旁路未开启';
    }
    return `视频编排旁路：PDF≥${value.pdf_min_download_count}，MP3≥${value.mp3_min_download_count}`;
}

async function saveSystemSyncConfig() {
    const context = syncContext();
    if (!context.selectedGroupId) {
        renderMessage('error', '请先选择星球。');
        return;
    }

    const jobs = buildSystemJobs(state.selectedTabDefinitions);
    if (!jobs.length) {
        renderMessage('error', '未找到适用的目标 Tab（需至少有外资研报/调研纪要之一）。');
        return;
    }

    const intervalMinutes = parsePositiveInt(els.configIntervalInput?.value, SYSTEM_SYNC_INTERVAL_MINUTES, 1);
    const maxRoundsRaw = parsePositiveIntOrZero(els.configMaxRoundsInput?.value, SYSTEM_SYNC_MAX_ROUNDS, 0);
    const countRaw = parseBoundedInt(
        els.configCountInput?.value,
        SYSTEM_SYNC_COUNT,
        1,
        50,
    );
    const maxDownloadFilesRaw = parseBoundedInt(
        els.configMaxDownloadFilesInput?.value,
        SYSTEM_SYNC_MAX_DOWNLOAD_FILES,
        0,
        40,
    );
    const pdfDownloadThreshold = parseBoundedInt(
        els.configPdfDownloadThresholdInput?.value,
        SYSTEM_SYNC_PDF_DOWNLOAD_THRESHOLD,
        0,
        100000,
    );
    const mp3DownloadThreshold = parseBoundedInt(
        els.configMp3DownloadThresholdInput?.value,
        SYSTEM_SYNC_MP3_DOWNLOAD_THRESHOLD,
        0,
        100000,
    );
    const pdfVideoThreshold = parseBoundedInt(
        els.configPdfVideoThresholdInput?.value,
        SYSTEM_SYNC_PDF_VIDEO_THRESHOLD,
        0,
        100000,
    );
    const mp3VideoThreshold = parseBoundedInt(
        els.configMp3VideoThresholdInput?.value,
        SYSTEM_SYNC_MP3_VIDEO_THRESHOLD,
        0,
        100000,
    );
    const autoVideoDispatch = buildAutoVideoDispatchConfig(
        Boolean(els.configAutoVideoDispatchCheckbox?.checked),
        pdfVideoThreshold,
        mp3VideoThreshold,
    );
    const attachmentDownloadRules = buildAttachmentDownloadRules(
        jobs,
        pdfDownloadThreshold,
        mp3DownloadThreshold,
    );
    const thresholdedDownloadEnabled = Number(maxDownloadFilesRaw) > 0 && attachmentDownloadRules.rules.length > 0;
    const group = selectedGroup('sync');

    const original = els.saveConfigBtn.innerHTML;
    setLoading(els.saveConfigBtn, true, '保存中...');
    renderMessage('loading', '正在保存系统同步配置...');

    try {
        const payload = {
            group_id: context.selectedGroupId,
            group_name: group?.name || '',
            count: Number(countRaw),
            max_pages: 1,
            stop_on_existing: true,
            download_attachments: thresholdedDownloadEnabled,
            allow_attachment_downloads: thresholdedDownloadEnabled,
            max_download_files: Number(maxDownloadFilesRaw),
            skip_existing_downloads: true,
            attachment_download_rules: attachmentDownloadRules,
            auto_video_dispatch: autoVideoDispatch,
            auto_pdf_clean: Boolean(els.configAutoPdfCleanCheckbox?.checked),
            auto_audio_clean: Boolean(els.configAutoAudioCleanCheckbox?.checked),
            auto_publish_after_clean: {
                enabled: Boolean(els.configAutoPublishAfterCleanCheckbox?.checked),
            },
            interval_minutes: Number(intervalMinutes),
            max_rounds: Number(maxRoundsRaw),
            jobs: jobs.map(job => ({
                key: job.key,
                mode_code: job.modeCode,
                file_type: job.fileType,
                tab: job.tab,
            })),
        };
        Object.assign(payload, credentialRequestPayload('sync'));

        const result = await apiPost('/zsxq/system-sync-test/config', payload);
        const config = result?.config || {};
        state.syncConfig = config;
        const serverInterval = Number(config.interval_minutes);
        const serverCount = Number(config.count);
        const serverMaxDownloadFiles = Number(config.max_download_files);
        if (serverInterval && serverInterval !== Number(intervalMinutes)) {
            renderMessage(
                'warning',
                `已保存但回读间隔为 ${serverInterval} 分钟（你输入的是 ${intervalMinutes}），请确认是否有外部进程覆盖配置。`,
            );
        } else {
            const cleanParts = [
                config.auto_pdf_clean === true ? 'PDF清洗' : null,
                config.auto_audio_clean === true ? '音频清洗' : null,
            ].filter(Boolean);
            const workflowText = `同步索引${cleanParts.length ? ` → ${cleanParts.join('+')}` : ''}${cleanParts.length && config.auto_publish_after_clean?.enabled ? ' → 发布' : ''}`;
            const downloadText = formatThresholdDownloadText(config, serverMaxDownloadFiles || maxDownloadFilesRaw);
            const videoDispatchText = formatAutoVideoDispatchText(config);
            renderMessage('success', `配置已保存。${workflowText}；${downloadText}；${videoDispatchText}；间隔 ${serverInterval || intervalMinutes} 分钟，每目标 Tab ${serverCount || countRaw} 条，任务轮次：${formatConfigMaxRounds(config.max_rounds)}。`);
        }
        if (Number.isFinite(serverCount)) {
            els.configCountInput.value = String(serverCount);
        }
        if (Number.isFinite(serverMaxDownloadFiles)) {
            els.configMaxDownloadFilesInput.value = String(serverMaxDownloadFiles);
        }
        hydrateSystemSyncRuntimeConfigInputs(config);
        renderSyncOverviewCard();
        await loadSystemSyncSchedulerStatus({ render: false });
        await loadSystemSyncBatches(config.group_id, { render: false });
        renderBatchesPanel();
        renderSyncSchedulerPanel();
    } catch (error) {
        renderMessage('error', `保存失败：${error.message}`);
    } finally {
        setLoading(els.saveConfigBtn, false, original);
    }
}

function selectedGroup(mode = currentModeKey()) {
    const context = modeContext(mode);
    return (context.groups || []).find(item => String(item.group_id) === String(context.selectedGroupId));
}

async function loadSystemSyncSchedulerStatus(options = {}) {
    try {
        const data = await apiGet('/zsxq/system-sync-scheduler/status');
        state.schedulerStatus = data?.scheduler || null;
        if (data?.scheduler?.config) {
            state.syncConfig = data.scheduler.config;
            if (!syncContext().selectedGroupId && state.syncConfig?.group_id) {
                syncContext().selectedGroupId = String(state.syncConfig.group_id);
            }
            hydrateSystemSyncRuntimeConfigInputs(state.syncConfig);
            if (state.activeMode === 'sync') {
                renderGroups();
                renderContextPanel();
            }
        }
    } catch {
        state.schedulerStatus = {
            running: false,
            exists: false,
            message: '状态查询失败',
        };
    }

    if (options.render !== false) {
        renderSyncSchedulerPanel();
        renderSyncOverviewCard();
        renderContextPanel();
    }
}

async function loadSystemSyncBatches(groupId = syncContext().selectedGroupId, options = {}) {
    try {
        const [batchResult, configResult] = await Promise.all([
            apiPost('/zsxq/system-sync-test/batches', {
                group_id: groupId || null,
                limit: 10,
                refresh: Boolean(options.refresh),
            }),
            apiGet('/zsxq/system-sync-test/config'),
        ]);
        state.batches = batchResult?.batches || [];
        state.batchesError = batchResult?.error || null;
        state.syncConfig = configResult?.config || state.syncConfig;
        if (configResult?.config) {
            if (!syncContext().selectedGroupId && configResult.config.group_id) {
                syncContext().selectedGroupId = String(configResult.config.group_id);
            }
            hydrateSystemSyncRuntimeConfigInputs(configResult.config);
            if (state.activeMode === 'sync') {
                renderGroups();
                renderContextPanel();
            }
        }
    } catch (error) {
        state.batches = [];
        state.batchesError = error.message || '获取系统批次失败';
    }

    if (options.render !== false) {
        renderBatchesPanel();
        renderSyncOverviewCard();
        renderContextPanel();
    }
}

async function startSystemSyncScheduler() {
    const original = els.startBtn.innerHTML;
    setLoading(els.startBtn, true, '启动中...');
    try {
        const result = await apiPost('/zsxq/system-sync-scheduler/start', credentialRequestPayload('sync'));
        state.schedulerStatus = result?.scheduler || null;
        renderMessage('success', result?.scheduler?.message || '启动请求已提交。');
        await loadSystemSyncBatches(syncContext().selectedGroupId, { render: false });
        renderBatchesPanel();
    } catch (error) {
        renderMessage('error', `启动失败：${error.message}`);
    } finally {
        setLoading(els.startBtn, false, original);
        renderSyncSchedulerPanel();
    }
}

async function stopSystemSyncScheduler() {
    const original = els.stopBtn.innerHTML;
    setLoading(els.stopBtn, true, '停止中...');
    try {
        const result = await apiPost('/zsxq/system-sync-scheduler/stop', {});
        state.schedulerStatus = result?.scheduler || null;
        renderMessage('success', result?.scheduler?.message || '停止请求已提交。');
    } catch (error) {
        renderMessage('error', `停止失败：${error.message}`);
    } finally {
        setLoading(els.stopBtn, false, original);
        renderSyncSchedulerPanel();
    }
}

async function runSystemSyncOnce() {
    const original = els.runOnceBtn.innerHTML;
    setLoading(els.runOnceBtn, true, '执行中...');
    try {
        const result = await apiPost('/zsxq/system-sync-scheduler/run-once', credentialRequestPayload('sync'));
        const scheduler = result?.scheduler || {};
        const statusText = scheduler.skipped ? `未提交（原因：${scheduler.reason || '系统跳过'}）` : '已触发';
        renderMessage(scheduler.skipped ? 'muted' : 'success', scheduler.message || statusText);
        state.schedulerStatus = scheduler;
        if (!scheduler.skipped) {
            await loadSystemSyncBatches(syncContext().selectedGroupId, { render: false });
            renderBatchesPanel();
        }
    } catch (error) {
        renderMessage('error', `立即执行失败：${error.message}`);
    } finally {
        setLoading(els.runOnceBtn, false, original);
        renderSyncSchedulerPanel();
    }
}

function startSchedulerPolling() {
    stopSchedulerPolling();
    state.schedulerPollTimer = setInterval(() => {
        loadSystemSyncSchedulerStatus({ render: false }).then(() => {
            renderSyncSchedulerPanel();
            renderSyncOverviewCard();
            renderAutoPublishCurrentGroup();
            if (state.logsLoaded && !els.logsPanel.classList.contains('hidden')) {
                loadSchedulerLogs().then((logs) => {
                    if (state.logsLoaded && !els.logsPanel.classList.contains('hidden')) {
                        els.logsPanel.textContent = logs;
                    }
                });
            }
        });
        loadSystemSyncBatches(syncContext().selectedGroupId, { render: false }).then(renderBatchesPanel);
        loadAutoPublishSchedulerStatus({ render: false }).then(renderAutoPublishSchedulerPanel);
        loadAutoPublishRuns({ render: false }).then(renderAutoPublishRunsPanel);
    }, SYSTEM_SYNC_STATUS_POLL_MS);
}

function stopSchedulerPolling() {
    if (state.schedulerPollTimer) {
        clearInterval(state.schedulerPollTimer);
        state.schedulerPollTimer = null;
    }
}

async function loadSchedulerLogs() {
    try {
        const response = await fetch(`${apiBaseUrl()}/zsxq/system-sync-scheduler/logs?lines=${SYSTEM_SYNC_LOG_LINES}`);
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data?.error?.message || `HTTP ${response.status}`);
        }
        const raw = data?.data?.raw;
        return sanitizeLogText(typeof raw === 'string' ? raw : '') || '（暂无日志）';
    } catch (error) {
        return `读取日志失败：${error.message}`;
    }
}

async function toggleSchedulerLogs() {
    if (els.logsPanel.classList.contains('hidden')) {
        els.logsPanel.classList.remove('hidden');
        els.openLogsBtn.disabled = true;
        els.openLogsBtn.innerHTML = '<i class="fas fa-list-ul mr-2"></i>加载日志...';
        els.logsPanel.textContent = '正在读取日志...';
        state.logsLoaded = true;
        const logs = await loadSchedulerLogs();
        els.logsPanel.textContent = logs;
        els.openLogsBtn.disabled = false;
        els.openLogsBtn.innerHTML = '<i class="fas fa-list-ul mr-2"></i>隐藏最近日志';
        return;
    }

    state.logsLoaded = false;
    els.logsPanel.classList.add('hidden');
    els.openLogsBtn.innerHTML = '<i class="fas fa-list-ul mr-2"></i>查看最近日志';
}

function renderSyncSchedulerPanel() {
    const scheduler = state.schedulerStatus || {};
    const config = scheduler.config || state.syncConfig || {};
    const running = Boolean(scheduler.running);
    const cookieMode = systemSyncCookieSourceText(config, scheduler);

    els.schedulerStatusBadge.textContent = running ? '运行中' : '未运行';
    els.schedulerStatusBadge.className = `rounded-full px-2 py-1 text-[10px] font-black ${running ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'}`;
    const interval = config.interval_minutes || SYSTEM_SYNC_INTERVAL_MINUTES;
    const count = config.count || SYSTEM_SYNC_COUNT;
    const maxFiles = Number(config.max_download_files ?? SYSTEM_SYNC_MAX_DOWNLOAD_FILES);
    const downloadText = formatThresholdDownloadText(config, maxFiles);
    const videoDispatchText = formatAutoVideoDispatchText(config);
    const autoPdfClean = config.auto_pdf_clean === true;
    const autoAudioClean = config.auto_audio_clean === true;
    const autoPublishAfterClean = config.auto_publish_after_clean?.enabled === true;
    const cleanParts = [
        autoPdfClean ? 'PDF' : null,
        autoAudioClean ? '音频' : null,
    ].filter(Boolean);
    const cleanText = cleanParts.length ? `清洗 ${cleanParts.join(' + ')}` : '不清洗';
    const publishEnabled = cleanParts.length > 0 && autoPublishAfterClean;
    const lastTrigger = formatZsxqTime(scheduler.last_trigger_at || config.last_trigger_at);
    const nextRun = formatZsxqTime(scheduler.next_run);
    const rounds = scheduler.continuous
        ? `已运行 ${scheduler.rounds_completed || 0} 轮（持续）`
        : `进度 ${scheduler.rounds_completed || 0}/${scheduler.max_rounds || 0} 轮`;

    els.schedulerContent.innerHTML = `
        <div class="space-y-1">
            <div>进程状态：${escapeHtml(running ? '已启动' : '未启动')} · PID ${escapeHtml(String(scheduler.pid || '-'))}</div>
            <div>计划配置：${escapeHtml(String(interval))} 分钟一次 · 每目标 Tab ${escapeHtml(String(count))} 条 · ${escapeHtml(downloadText)}</div>
            <div>旁路任务：${escapeHtml(videoDispatchText)}</div>
            <div>工作流：同步 → ${escapeHtml(cleanText)}${escapeHtml(publishEnabled ? ' → 发布' : '')}</div>
            <div>轮次：${escapeHtml(rounds)}</div>
            <div>Cookie：${escapeHtml(cookieMode)}</div>
            <div>上次触发：${escapeHtml(lastTrigger)} · 下一次：${escapeHtml(nextRun)}</div>
            <div>日志：<span class="font-mono break-all">${escapeHtml(scheduler.log_path || (config.log_path || '-'))}</span></div>
            <div class="text-slate-400">${escapeHtml(scheduler.message || config.group_id ? '' : '未检测到配置')}</div>
        </div>
    `;

    const hasConfig = Boolean(config?.exists || config?.group_id || scheduler?.config?.group_id);
    if (!hasConfig) {
        els.startBtn.disabled = true;
        els.stopBtn.disabled = true;
        els.runOnceBtn.disabled = true;
    } else {
        els.startBtn.disabled = running;
        els.stopBtn.disabled = !running;
        els.runOnceBtn.disabled = false;
    }

    els.startBtn.classList.toggle('opacity-50', running);
    els.stopBtn.classList.toggle('opacity-50', !running);
    renderWorkspaceSnapshot();
}

function renderSyncOverviewCard() {
    const config = state.syncConfig || {};
    const scheduler = state.schedulerStatus || {};
    const configured = Boolean(config && config.exists);
    const hasGroup = Boolean(syncContext().selectedGroupId || config.group_id);

    if (!configured) {
        els.configBadge.textContent = hasGroup ? '未保存' : '未配置';
        els.configBadge.className = 'rounded-full bg-slate-100 px-2 py-1 text-[10px] font-black text-slate-500';
        els.configSummary.textContent = hasGroup ? '星球已选择，待保存配置。' : '尚未选择星球。';
        const tabHint = state.selectedTabDefinitions.length
            ? `检测到 ${state.selectedTabDefinitions.length} 个 Tab：${state.selectedTabDefinitions.map(tab => tab.label).join('、')}`
            : '请先选择星球并加载配置。';
        els.configDetail.textContent = tabHint;
        renderWorkspaceSnapshot();
        return;
    }

    const rounds = config.max_rounds
        ? `${config.rounds_completed || 0}/${config.max_rounds} 轮`
        : `持续模式（已运行 ${config.rounds_completed || 0} 轮）`;
    const cookieSourceText = systemSyncCookieSourceText(config, scheduler);
    const hasRuntimeCookie = systemSyncCookieReady(config, scheduler);
    els.configBadge.textContent = hasRuntimeCookie ? '已配置' : '缺少 Cookie';
    els.configBadge.className = `rounded-full px-2 py-1 text-[10px] font-black ${hasRuntimeCookie ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`;
    els.configSummary.textContent = `${config.interval_minutes || SYSTEM_SYNC_INTERVAL_MINUTES} 分钟一次 · 目标星球：${config.group_name || config.group_id || '-'} · ${rounds}`;
    const autoPdfClean = config.auto_pdf_clean === true;
    const autoAudioClean = config.auto_audio_clean === true;
    const cleanParts = [
        autoPdfClean ? 'PDF 清洗' : null,
        autoAudioClean ? '音频清洗' : null,
    ].filter(Boolean);
    const cleanText = cleanParts.length ? cleanParts.join(' + ') : '不清洗';
    const autoPublishText = cleanParts.length && config.auto_publish_after_clean?.enabled === true ? '清洗后自动发帖开启' : '不自动发帖';
    const downloadText = formatThresholdDownloadText(config, config.max_download_files);
    const videoDispatchText = formatAutoVideoDispatchText(config);
    els.configDetail.textContent = `${downloadText} · ${videoDispatchText} · 每目标 Tab ${config.count || SYSTEM_SYNC_COUNT} 条 · ${cleanText} · ${autoPublishText} · 任务来源：${cookieSourceText}。`;
    renderWorkspaceSnapshot();
}

function systemSyncCookieReady(config = {}, scheduler = {}) {
    return Boolean(
        scheduler.cookie_source === 'db'
        || config.credential_id
        || config.cookie_db_configured
        || scheduler.cookie_source === 'env'
        || config.cookie_env_configured
        || scheduler.cookie_source === 'page'
    );
}

function systemSyncCookieSourceText(config = {}, scheduler = {}) {
    if (scheduler.cookie_source === 'db' || config.credential_id || config.cookie_db_configured) {
        return config.credential_id ? `DB 凭据 ${shortId(config.credential_id)}` : 'DB 凭据';
    }
    if (scheduler.cookie_source === 'page') {
        return '旧页面 Cookie（当前运行）';
    }
    if (scheduler.cookie_source === 'env' || config.cookie_env_configured) {
        return `环境变量 ${config.cookie_env_name || 'ZSXQ_SYNC_TEST_COOKIE'}`;
    }
    return '未配置';
}

function autoPublishCookieSourceText(config = {}, scheduler = {}) {
    const source = scheduler.cookie_source || config.cookie_source_mode;
    if (source === 'db' || config.cookie_db_configured) {
        return `DB publish 凭据池（${config.db_cookie_account_count || config.active_cookie_account_count || 0} 个可用）`;
    }
    if (source === 'pool' || config.cookie_pool_ready) {
        return `策略账号池（${config.active_cookie_account_count || 0} 个可用）`;
    }
    if (source === 'page') {
        return '旧页面 Cookie（当前运行）';
    }
    if (source === 'env' || config.cookie_env_configured) {
        return `环境变量 ${config.cookie_env_name || 'ZSXQ_AUTO_PUBLISH_COOKIE'}`;
    }
    return '未配置';
}

function shortId(value) {
    const text = String(value || '');
    return text.length <= 8 ? text : `${text.slice(0, 8)}...`;
}

function hydrateSystemSyncRuntimeConfigInputs(config = {}) {
    if (els.configIntervalInput) {
        const intervalMinutes = parseBoundedInt(
            config.interval_minutes,
            SYSTEM_SYNC_INTERVAL_MINUTES,
            1,
            1440,
        );
        els.configIntervalInput.value = String(intervalMinutes);
    }

    if (els.configCountInput) {
        const count = parseBoundedInt(
            config.count,
            SYSTEM_SYNC_COUNT,
            1,
            50,
        );
        els.configCountInput.value = String(count);
    }

    if (els.configMaxDownloadFilesInput) {
        const maxDownloadFiles = parseBoundedInt(
            config.max_download_files,
            SYSTEM_SYNC_MAX_DOWNLOAD_FILES,
            0,
            40,
        );
        els.configMaxDownloadFilesInput.value = String(maxDownloadFiles);
    }

    if (els.configPdfDownloadThresholdInput) {
        els.configPdfDownloadThresholdInput.value = String(downloadThresholdFromConfig(
            config,
            'pdf',
            SYSTEM_SYNC_PDF_DOWNLOAD_THRESHOLD,
        ));
    }

    if (els.configMp3DownloadThresholdInput) {
        els.configMp3DownloadThresholdInput.value = String(downloadThresholdFromConfig(
            config,
            'mp3',
            SYSTEM_SYNC_MP3_DOWNLOAD_THRESHOLD,
        ));
    }

    const autoVideoDispatch = autoVideoDispatchConfig(config);
    if (els.configAutoVideoDispatchCheckbox) {
        els.configAutoVideoDispatchCheckbox.checked = autoVideoDispatch.enabled === true;
    }
    if (els.configPdfVideoThresholdInput) {
        els.configPdfVideoThresholdInput.value = String(autoVideoDispatch.pdf_min_download_count);
    }
    if (els.configMp3VideoThresholdInput) {
        els.configMp3VideoThresholdInput.value = String(autoVideoDispatch.mp3_min_download_count);
    }

    if (els.configMaxRoundsInput) {
        const maxRounds = parsePositiveIntOrZero(
            config.max_rounds,
            SYSTEM_SYNC_MAX_ROUNDS,
            0,
        );
        els.configMaxRoundsInput.value = String(maxRounds);
    }

    if (els.configAutoPdfCleanCheckbox) {
        els.configAutoPdfCleanCheckbox.checked = config.auto_pdf_clean === true;
    }

    if (els.configAutoAudioCleanCheckbox) {
        els.configAutoAudioCleanCheckbox.checked = config.auto_audio_clean === true;
    }

    if (els.configAutoPublishAfterCleanCheckbox) {
        els.configAutoPublishAfterCleanCheckbox.checked = config.auto_publish_after_clean?.enabled === true;
    }
}

function renderDefaultConfigSummary() {
    els.configSummary.textContent = '尚未读取系统配置。请先获取星球并保存。';
    els.configDetail.textContent = '保存成功后，批次和日志将会自动同步回显。';
    els.configBadge.textContent = '未配置';
    els.batchSectionSubtitle.textContent = '请先完成配置保存后，系统会记录每次执行批次。';
    if (els.batchesMini) {
        els.batchesMini.textContent = '暂无系统批次摘要。';
    }
    renderWorkspaceSnapshot();
}

function renderBatchesPanel() {
    if (!els.batchesPanel) return;
    const batches = state.batches || [];
    const batchError = state.batchesError;
    els.batchSectionSubtitle.textContent = state.batchesError
        ? `读取批次失败：${state.batchesError}`
        : `最后 ${batches.length} 条批次（按时间倒序）`;
    els.batchCount.textContent = String(batches.length);

    if (!batches.length) {
        els.batchesPanel.innerHTML = batchError
            ? `<div class="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-700">${escapeHtml(batchError)}</div>`
            : '<div class="rounded-lg border border-dashed border-slate-200 p-4 text-center text-xs text-slate-400">暂无系统测试批次。</div>';
        if (els.batchesMini) {
            els.batchesMini.textContent = batchError
                ? `批次读取失败：${batchError}`
                : '暂无系统批次摘要。';
        }
        renderWorkspaceSnapshot();
        return;
    }

    els.batchesPanel.innerHTML = (state.batches || []).map(renderSystemSyncBatchCard).join('');
    const latest = batches[0];
    if (els.batchesMini) {
        els.batchesMini.textContent = latest
            ? `最近批次 ${syncStatusLabel(latest.status)} · ${formatZsxqTime(latest.completed_at || latest.created_at)}`
            : '暂无系统批次摘要。';
    }
    renderWorkspaceSnapshot();
}

function renderSystemSyncBatchCard(batch) {
    const summary = batch.summary || {};
    const topics = batch.topics || [];
    const topicCount = Number(batch.topics_count ?? topics.length ?? 0);
    const status = batchStatusMeta(batch.status);
    const detailUrl = systemSyncBatchDetailUrl(batch);
    const roundText = batch.round && batch.max_rounds
        ? `第 ${batch.round}/${batch.max_rounds} 轮`
        : batch.round && batch.continuous
            ? `第 ${batch.round} 轮`
            : '历史批次';
    const completedText = formatZsxqTime(batch.completed_at || batch.created_at);

    return `
        <article class="zsxq-batch-card">
            <div class="zsxq-batch-card__top">
                <div class="min-w-0">
                    <div class="zsxq-batch-card__title">${escapeHtml(roundText)}</div>
                    <div class="zsxq-batch-card__meta">
                        <span class="font-mono">${escapeHtml(batch.batch_id || batch.attempt_id || '-')}</span>
                        <span>${escapeHtml(completedText)}</span>
                    </div>
                </div>
                <span class="zsxq-batch-status" data-tone="${escapeHtml(status.tone)}">${escapeHtml(status.label)}</span>
            </div>

            <div class="zsxq-batch-metrics">
                ${renderBatchMetric('新增', summary.topics_new || 0, 'blue')}
                ${renderBatchMetric('更新', summary.topics_updated || 0, 'violet')}
                ${renderBatchMetric('下载', summary.attachments_downloaded || 0, 'emerald')}
                ${renderBatchMetric('失败', summary.attachments_failed || 0, 'red')}
            </div>

            <a class="zsxq-batch-detail-link" href="${escapeHtml(detailUrl)}" target="_blank" rel="noopener">
                <span>打开明细</span>
                <strong>${escapeHtml(String(topicCount))} 条内容</strong>
                ${batch.topics_truncated ? '<em>已截断</em>' : ''}
                <i class="fas fa-up-right-from-square" aria-hidden="true"></i>
            </a>
        </article>
    `;
}

function systemSyncBatchDetailUrl(batch) {
    const params = new URLSearchParams();
    params.set('batch_id', batch.batch_id || batch.attempt_id || '');
    if (batch.group_id) {
        params.set('group_id', batch.group_id);
    }
    return `zsxq-system-sync-detail.html?${params.toString()}`;
}

function renderBatchMetric(label, value, tone = 'slate') {
    return `
        <div class="zsxq-batch-metric" data-tone="${escapeHtml(tone)}">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(String(value || 0))}</strong>
        </div>
    `;
}

function renderSystemSyncTopicDetails(topics) {
    return `
        <div class="zsxq-topic-detail-list">
            ${topics.map(topic => {
                const attachments = topic.attachments || [];
                return `
                    <section class="zsxq-topic-detail">
                        <div class="zsxq-topic-detail__head">
                            <div class="min-w-0">
                                <div class="zsxq-topic-detail__title">${escapeHtml(topic.title || topic.content || '无标题内容')}</div>
                                <div class="zsxq-topic-detail__meta">
                                    <span>${escapeHtml(topic.tab_label || topic.tab_id || '-')}</span>
                                    <span>${escapeHtml(topicStatusLabel(topic.status))}</span>
                                    <span>${escapeHtml(formatZsxqTime(topic.create_time || ''))}</span>
                                    ${topic.author_name ? `<span>${escapeHtml(topic.author_name)}</span>` : ''}
                                </div>
                            </div>
                            <span class="zsxq-topic-detail__count">${escapeHtml(String(attachments.length))} 附件</span>
                        </div>
                        ${topic.content ? `<div class="zsxq-topic-detail__content">${escapeHtml(topic.content)}</div>` : ''}
                        ${topic.raw_json_path ? `<div class="zsxq-topic-detail__path">JSON：${escapeHtml(topic.raw_json_path)}</div>` : ''}
                        ${attachments.length ? renderSystemSyncAttachmentDetails(attachments) : '<div class="mt-2 text-xs text-slate-400">无附件记录。</div>'}
                    </section>
                `;
            }).join('')}
        </div>
    `;
}

function renderSystemSyncAttachmentDetails(attachments) {
    return `
        <div class="zsxq-attachment-detail-list">
            ${attachments.map(item => {
                const status = attachmentStatusMeta(item);
                return `
                    <div class="zsxq-attachment-detail">
                        <div class="min-w-0">
                            <div class="zsxq-attachment-detail__name">
                                <span>${escapeHtml(String(item.file_type || item.attachment_type || 'file').toUpperCase())}</span>
                                ${escapeHtml(item.name || item.file_id || item.attachment_key || '附件')}
                            </div>
                            ${item.local_path ? `<div class="zsxq-attachment-detail__path">${escapeHtml(item.local_path)}</div>` : ''}
                            ${item.error_message ? `<div class="zsxq-attachment-detail__error">${escapeHtml(item.error_message)}</div>` : ''}
                        </div>
                        <span class="zsxq-attachment-status" data-tone="${escapeHtml(status.tone)}">${escapeHtml(status.label)}</span>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

async function loadAutoPublishConfig(options = {}) {
    try {
        const data = await apiGet('/zsxq/auto-publish/config');
        state.autoPublishConfig = data?.config || null;
        if (state.autoPublishConfig?.exists) {
            if (!publishContext().selectedGroupId && state.autoPublishConfig?.group_id) {
                publishContext().selectedGroupId = String(state.autoPublishConfig.group_id);
            }
            applyAutoPublishConfigToForm(state.autoPublishConfig);
            if (state.activeMode === 'publish') {
                renderGroups();
                renderContextPanel();
            }
        }
    } catch (error) {
        state.autoPublishConfig = null;
        showAutoPublishMessage('warning', '自动发帖配置读取失败', error.message);
    }

    if (options.render !== false) {
        renderAutoPublishCurrentGroup();
        renderAutoPublishSchedulerPanel();
    }
}

async function loadAutoPublishSchedulerStatus(options = {}) {
    try {
        const data = await apiGet('/zsxq/auto-publish/scheduler/status');
        state.autoPublishScheduler = data?.scheduler || null;
        if (!state.autoPublishConfig && state.autoPublishScheduler?.config?.exists) {
            state.autoPublishConfig = state.autoPublishScheduler.config;
            if (!publishContext().selectedGroupId && state.autoPublishConfig?.group_id) {
                publishContext().selectedGroupId = String(state.autoPublishConfig.group_id);
            }
            applyAutoPublishConfigToForm(state.autoPublishConfig);
            if (state.activeMode === 'publish') {
                renderGroups();
                renderContextPanel();
            }
        }
    } catch (error) {
        state.autoPublishScheduler = {
            running: false,
            message: `状态查询失败：${error.message}`,
        };
    }

    if (options.render !== false) {
        renderAutoPublishCurrentGroup();
        renderAutoPublishSchedulerPanel();
    }
}

async function loadAutoPublishRuns(options = {}) {
    try {
        const data = await apiGet('/zsxq/auto-publish/runs?limit=20');
        state.autoPublishRuns = data?.runs || [];
    } catch (error) {
        state.autoPublishRuns = [];
        showAutoPublishMessage('warning', '自动发帖历史读取失败', error.message);
    }

    if (options.render !== false) {
        renderAutoPublishRunsPanel();
    }
}

async function loadAutoPublishLogs(options = {}) {
    try {
        const data = await apiGet(`/zsxq/auto-publish/scheduler/logs?lines=${AUTO_PUBLISH_LOG_LINES}`);
        state.autoPublishLogsRaw = sanitizeLogText(data?.raw || '');
    } catch (error) {
        state.autoPublishLogsRaw = `读取日志失败：${error.message}`;
    }

    if (options.render !== false) {
        renderAutoPublishLogsPanel();
    }
}

async function refreshAutoPublishRuntime() {
    await Promise.all([
        loadAutoPublishSchedulerStatus({ render: false }),
        loadAutoPublishRuns({ render: false }),
        loadAutoPublishLogs({ render: false }),
    ]);
    renderAutoPublishCurrentGroup();
    renderAutoPublishSchedulerPanel();
    renderAutoPublishRunsPanel();
    renderAutoPublishLogsPanel();
}

function currentAutoPublishGroupId() {
    return String(publishContext().selectedGroupId || state.autoPublishConfig?.group_id || '').trim();
}

function currentAutoPublishGroupName() {
    const id = currentAutoPublishGroupId();
    const currentGroup = publishContext().groups.find(item => String(item.group_id) === id);
    return currentGroup?.name || state.autoPublishConfig?.group_name || '';
}

function renderAutoPublishCurrentGroup() {
    if (!els.autoPublishCurrentGroup) return;
    const groupId = currentAutoPublishGroupId();
    const groupName = currentAutoPublishGroupName();
    els.autoPublishCurrentGroup.textContent = groupId
        ? `${groupName || groupId} (${groupId})`
        : '未选择';
    renderWorkspaceSnapshot();
}

function showAutoPublishMessage(type, title, message) {
    if (!els.autoPublishMessage) return;
    const colorMap = {
        loading: 'border-blue-200 bg-blue-50 text-blue-700',
        success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
        error: 'border-red-200 bg-red-50 text-red-700',
        warning: 'border-amber-200 bg-amber-50 text-amber-700',
        muted: 'border-slate-200 bg-slate-50 text-slate-500',
    };
    els.autoPublishMessage.className = `rounded-lg border px-3 py-3 text-sm ${colorMap[type] || colorMap.muted}`;
    els.autoPublishMessage.innerHTML = `
        <div class="font-bold">${escapeHtml(title || '')}</div>
        <div class="mt-1">${escapeHtml(message || '')}</div>
    `;
    els.autoPublishMessage.classList.remove('hidden');
}

async function pickAutoPublishDirectory(ruleType) {
    const isPdf = ruleType === 'pdf';
    const input = isPdf ? els.autoPublishPdfSourceDirInput : els.autoPublishAudioSourceDirInput;
    const button = isPdf ? els.autoPublishPdfSourceDirPickBtn : els.autoPublishAudioSourceDirPickBtn;
    if (!input || !button) return;

    const original = button.innerHTML;
    setLoading(button, true, '选择中...');
    try {
        const data = await apiPost('/batch-transcription/select-directory', {
            purpose: 'input',
            initial_dir: (input.value || '').trim(),
            title: isPdf ? '选择 PDF 来源目录' : '选择音频来源目录',
        });
        input.value = data?.path || '';
    } catch (error) {
        if (error.message !== '未选择目录') {
            showAutoPublishMessage('error', '目录选择失败', error.message);
        }
    } finally {
        setLoading(button, false, original);
    }
}

async function loadAutoPublishTags() {
    const groupId = currentAutoPublishGroupId();
    if (!hasRequestCredential('publish')) {
        showAutoPublishMessage('warning', '无法读取标签', '请先选择 DB Cookie，或新增保存 Cookie。');
        return;
    }
    if (!groupId) {
        showAutoPublishMessage('warning', '无法读取标签', '请先在左侧选择星球。');
        return;
    }

    const original = els.autoPublishLoadTagsBtn.innerHTML;
    setLoading(els.autoPublishLoadTagsBtn, true, '读取中...');
    try {
        const result = await apiPost('/zsxq/menus', {
            ...credentialRequestPayload('publish'),
            group_id: groupId,
        });
        const menus = result?.resp_data?.menus || result?.resp_data?.group_menus || [];
        hydrateAutoPublishTagsFromMenus(menus);
        publishContext().summaryText = state.autoPublishTagOptions.length
            ? `已识别 ${state.autoPublishTagOptions.length} 个 hashtag，可直接给 PDF / 音频规则选择。`
            : '当前星球菜单里没有识别到 hashtag 标签，可继续手动填写标签 ID / 名称。';
        if (state.activeMode === 'publish') {
            renderContextPanel();
        }
        showAutoPublishMessage(
            'success',
            '标签已刷新',
            state.autoPublishTagOptions.length
                ? `共识别到 ${state.autoPublishTagOptions.length} 个可用标签。`
                : '当前星球菜单里没有识别到 hashtag 标签，可继续手动填写标签 ID / 名称。'
        );
    } catch (error) {
        showAutoPublishMessage('error', '读取标签失败', error.message);
    } finally {
        setLoading(els.autoPublishLoadTagsBtn, false, original);
    }
}

function clearAutoPublishTagOptions() {
    state.autoPublishTagOptions = [];
    renderAutoPublishTagOptions();
}

function hydrateAutoPublishTagsFromMenus(menus) {
    state.autoPublishTagOptions = extractAutoPublishHashtagOptions(menus);
    renderAutoPublishTagOptions();
}

function extractAutoPublishHashtagOptions(menus) {
    const seen = new Set();
    const output = [];
    (menus || []).forEach((menu) => {
        const hashtagId = menu?.hashtag?.hashtag_id
            || menu?.hashtag_id
            || menu?.target?.hashtag_id
            || menu?.target?.id
            || (menu?.type === 'hashtag' ? menu.menu_id : null);
        if (!hashtagId) return;
        const id = String(hashtagId).trim();
        if (!id || seen.has(id)) return;
        seen.add(id);
        output.push({
            id,
            title: String(menu?.title || menu?.name || id).replace(/^#|#$/g, '').trim() || id,
        });
    });
    return output;
}

function renderAutoPublishTagOptions() {
    const renderSelect = (select, idInput, titleInput) => {
        const current = select.value;
        const fallbackId = (idInput?.value || '').trim();
        select.innerHTML = `<option value="">不绑定标签</option>${state.autoPublishTagOptions.map((item) => `
            <option value="${escapeHtml(item.id)}">${escapeHtml(item.title)} / ${escapeHtml(item.id)}</option>
        `).join('')}`;
        if (state.autoPublishTagOptions.some((item) => item.id === current)) {
            select.value = current;
            return;
        }
        if (fallbackId && state.autoPublishTagOptions.some((item) => item.id === fallbackId)) {
            select.value = fallbackId;
            if (titleInput && !titleInput.value.trim()) {
                const matched = state.autoPublishTagOptions.find((item) => item.id === fallbackId);
                if (matched) {
                    titleInput.value = matched.title;
                }
            }
        }
    };

    renderSelect(els.autoPublishPdfTagSelect, els.autoPublishPdfHashtagIdInput, els.autoPublishPdfHashtagTitleInput);
    renderSelect(els.autoPublishAudioTagSelect, els.autoPublishAudioHashtagIdInput, els.autoPublishAudioHashtagTitleInput);
}

function applyAutoPublishSelectedTag(prefix) {
    const isPdf = prefix === 'pdf';
    const select = isPdf ? els.autoPublishPdfTagSelect : els.autoPublishAudioTagSelect;
    const idInput = isPdf ? els.autoPublishPdfHashtagIdInput : els.autoPublishAudioHashtagIdInput;
    const titleInput = isPdf ? els.autoPublishPdfHashtagTitleInput : els.autoPublishAudioHashtagTitleInput;
    const selected = state.autoPublishTagOptions.find((item) => item.id === select.value);
    if (!selected) {
        idInput.value = '';
        titleInput.value = '';
        return;
    }
    idInput.value = selected.id;
    titleInput.value = selected.title;
}

function parseGroupIds(raw = '') {
    return String(raw || '')
        .replace(/，/g, ',')
        .split(',')
        .map(item => item.trim())
        .filter(Boolean)
        .filter((item, index, arr) => arr.indexOf(item) === index);
}

function resetAutoPublishCookieAccountForm() {
    state.autoPublishEditingCookieAccountId = '';
    if (els.autoPublishAccountNameInput) {
        els.autoPublishAccountNameInput.value = '';
    }
    if (els.autoPublishAccountCookieInput) {
        els.autoPublishAccountCookieInput.value = '';
    }
    if (els.autoPublishAccountGroupIdsInput) {
        els.autoPublishAccountGroupIdsInput.value = '';
    }
    if (els.autoPublishAccountNotesInput) {
        els.autoPublishAccountNotesInput.value = '';
    }
    if (els.autoPublishAccountEnabledCheckbox) {
        els.autoPublishAccountEnabledCheckbox.checked = true;
    }
    if (els.autoPublishAccountCurrentGroupOnlyCheckbox) {
        els.autoPublishAccountCurrentGroupOnlyCheckbox.checked = true;
    }
    renderAutoPublishCookieAccounts();
}

function fillAutoPublishAccountWithCurrentCookie() {
    if (els.autoPublishAccountNameInput && !els.autoPublishAccountNameInput.value.trim()) {
        const profile = activeCookieProfile('publish');
        els.autoPublishAccountNameInput.value = profile?.name || `发布账号 ${state.autoPublishCookieAccounts.length + 1}`;
    }
    showAutoPublishMessage(
        'warning',
        'DB Cookie 不会回填明文',
        '顶部下拉框现在只保存凭据 id。自动发帖运行会优先使用 DB publish 凭据池；如需维护策略账号池，请手动粘贴对应 Cookie。'
    );
}

function fillAutoPublishAccountWithCurrentGroup() {
    const groupId = currentAutoPublishGroupId();
    if (!groupId) {
        showAutoPublishMessage('warning', '当前没有选中星球', '请先在左上角选择自动发帖目标星球。');
        return;
    }
    if (els.autoPublishAccountCurrentGroupOnlyCheckbox) {
        els.autoPublishAccountCurrentGroupOnlyCheckbox.checked = true;
    }
    if (els.autoPublishAccountGroupIdsInput) {
        els.autoPublishAccountGroupIdsInput.value = groupId;
    }
}

function collectAutoPublishCookieAccountDraft() {
    const onlyCurrentGroup = Boolean(els.autoPublishAccountCurrentGroupOnlyCheckbox?.checked);
    const currentGroupId = currentAutoPublishGroupId();
    const groupIds = onlyCurrentGroup ? (currentGroupId ? [currentGroupId] : []) : parseGroupIds(els.autoPublishAccountGroupIdsInput?.value || '');

    return {
        id: state.autoPublishEditingCookieAccountId || `account_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
        name: (els.autoPublishAccountNameInput?.value || '').trim(),
        cookie: (els.autoPublishAccountCookieInput?.value || '').trim(),
        enabled: Boolean(els.autoPublishAccountEnabledCheckbox?.checked),
        allowed_group_ids: groupIds,
        notes: (els.autoPublishAccountNotesInput?.value || '').trim(),
    };
}

function saveAutoPublishCookieAccount() {
    const draft = collectAutoPublishCookieAccountDraft();
    if (!draft.name) {
        showAutoPublishMessage('warning', '账号名称不能为空', '请给这个管理员 Cookie 起一个便于识别的名字。');
        return;
    }
    if (!draft.cookie) {
        showAutoPublishMessage('warning', 'Cookie 不能为空', '请先填入该管理员的 Cookie。');
        return;
    }
    if (!draft.allowed_group_ids.length) {
        showAutoPublishMessage('warning', '缺少星球范围', '请至少绑定一个允许发布的星球，或者勾选“仅当前星球”。');
        return;
    }

    const existingIndex = state.autoPublishCookieAccounts.findIndex(item => item.id === draft.id);
    if (existingIndex >= 0) {
        const previous = state.autoPublishCookieAccounts[existingIndex];
        state.autoPublishCookieAccounts.splice(existingIndex, 1, {
            ...previous,
            ...draft,
        });
        showAutoPublishMessage('success', '账号已更新', `${draft.name} 已更新到自动发帖账号表。`);
    } else {
        state.autoPublishCookieAccounts.push({
            ...draft,
            quota_hit_count: 0,
            last_limit_hit_at: '',
            last_limit_error: '',
            last_success_at: '',
            last_used_at: '',
        });
        showAutoPublishMessage('success', '账号已加入', `${draft.name} 已加入自动发帖账号表。`);
    }
    resetAutoPublishCookieAccountForm();
}

function editAutoPublishCookieAccount(accountId) {
    const account = state.autoPublishCookieAccounts.find(item => item.id === accountId);
    if (!account) return;
    state.autoPublishEditingCookieAccountId = account.id;
    if (els.autoPublishAccountNameInput) {
        els.autoPublishAccountNameInput.value = account.name || '';
    }
    if (els.autoPublishAccountCookieInput) {
        els.autoPublishAccountCookieInput.value = account.cookie || '';
    }
    if (els.autoPublishAccountGroupIdsInput) {
        els.autoPublishAccountGroupIdsInput.value = (account.allowed_group_ids || []).join(',');
    }
    if (els.autoPublishAccountNotesInput) {
        els.autoPublishAccountNotesInput.value = account.notes || '';
    }
    if (els.autoPublishAccountEnabledCheckbox) {
        els.autoPublishAccountEnabledCheckbox.checked = Boolean(account.enabled ?? true);
    }
    if (els.autoPublishAccountCurrentGroupOnlyCheckbox) {
        const currentGroupId = currentAutoPublishGroupId();
        const groupIds = account.allowed_group_ids || [];
        els.autoPublishAccountCurrentGroupOnlyCheckbox.checked = Boolean(currentGroupId && groupIds.length === 1 && groupIds[0] === currentGroupId);
    }
    renderAutoPublishCookieAccounts();
}

function deleteAutoPublishCookieAccount(accountId) {
    const account = state.autoPublishCookieAccounts.find(item => item.id === accountId);
    if (!account) return;
    state.autoPublishCookieAccounts = state.autoPublishCookieAccounts.filter(item => item.id !== accountId);
    if (state.autoPublishEditingCookieAccountId === accountId) {
        resetAutoPublishCookieAccountForm();
    } else {
        renderAutoPublishCookieAccounts();
    }
    showAutoPublishMessage('success', '账号已删除', `${account.name || accountId} 已从自动发帖账号表移除。`);
}

function moveAutoPublishCookieAccount(accountId, delta) {
    const index = state.autoPublishCookieAccounts.findIndex(item => item.id === accountId);
    if (index < 0) return;
    const nextIndex = index + delta;
    if (nextIndex < 0 || nextIndex >= state.autoPublishCookieAccounts.length) return;
    const items = [...state.autoPublishCookieAccounts];
    const [moved] = items.splice(index, 1);
    items.splice(nextIndex, 0, moved);
    state.autoPublishCookieAccounts = items;
    renderAutoPublishCookieAccounts();
}

function renderAutoPublishCookieAccounts() {
    if (!els.autoPublishAccountList) return;
    const currentGroupId = currentAutoPublishGroupId();
    const currentGroupName = currentAutoPublishGroupName();
    const editingId = state.autoPublishEditingCookieAccountId;
    const items = state.autoPublishCookieAccounts || [];

    if (!items.length) {
        els.autoPublishAccountList.innerHTML = '<div class="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-400">暂无发帖账号</div>';
        return;
    }

    els.autoPublishAccountList.innerHTML = items.map((account, index) => {
        const token = extractCookieToken(account.cookie || '');
        const masked = token ? maskToken(token) : '未检测到 token';
        const groupIds = account.allowed_group_ids || [];
        const appliesToCurrentGroup = !groupIds.length || !currentGroupId || groupIds.includes(currentGroupId);
        const currentGroupLabel = currentGroupId
            ? (appliesToCurrentGroup ? `覆盖当前星球 ${currentGroupName || currentGroupId}` : `未授权当前星球 ${currentGroupName || currentGroupId}`)
            : (groupIds.length ? `绑定 ${groupIds.length} 个星球` : '未限制星球');
        const badgeClass = appliesToCurrentGroup
            ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
            : 'border-amber-200 bg-amber-50 text-amber-700';
        const selectedClass = editingId === account.id ? 'border-blue-300 ring-2 ring-blue-100' : 'border-slate-200';

        return `
            <article class="rounded-lg border ${selectedClass} bg-white p-4">
                <div class="flex flex-wrap items-start justify-between gap-3">
                    <div class="min-w-0">
                        <div class="flex flex-wrap items-center gap-2">
                            <span class="inline-flex h-6 min-w-[1.5rem] items-center justify-center rounded-full bg-slate-900 px-2 text-[10px] font-black text-white">${escapeHtml(String(index + 1))}</span>
                            <div class="text-sm font-black text-slate-800">${escapeHtml(account.name || `发布账号 ${index + 1}`)}</div>
                            <span class="rounded-full border px-2 py-1 text-[10px] font-black ${account.enabled ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-slate-200 bg-slate-100 text-slate-500'}">${account.enabled ? '启用' : '停用'}</span>
                        </div>
                        <div class="mt-2 text-xs text-slate-500">Cookie：${escapeHtml(masked)}</div>
                        <div class="mt-1 text-xs text-slate-500">${escapeHtml(currentGroupLabel)}</div>
                        ${account.notes ? `<div class="mt-1 text-xs text-slate-400">${escapeHtml(account.notes)}</div>` : ''}
                        <div class="mt-2 flex flex-wrap gap-2 text-[11px] text-slate-400">
                            <span>成功：${escapeHtml(formatZsxqTime(account.last_success_at || '')) || '-'}</span>
                            <span>限流：${escapeHtml(formatZsxqTime(account.last_limit_hit_at || '')) || '-'}</span>
                            <span>累计：${escapeHtml(String(account.quota_hit_count || 0))}</span>
                        </div>
                    </div>

                    <div class="flex flex-wrap gap-2">
                        <span class="rounded-full border px-2 py-1 text-[10px] font-black ${badgeClass}">${escapeHtml(appliesToCurrentGroup ? '当前策略可用' : '当前策略不可用')}</span>
                        <button type="button" data-ap-account-action="up" data-account-id="${escapeHtml(account.id)}" class="rounded-lg border border-slate-300 px-2.5 py-1 text-[11px] font-bold text-slate-600 hover:bg-slate-50">上移</button>
                        <button type="button" data-ap-account-action="down" data-account-id="${escapeHtml(account.id)}" class="rounded-lg border border-slate-300 px-2.5 py-1 text-[11px] font-bold text-slate-600 hover:bg-slate-50">下移</button>
                        <button type="button" data-ap-account-action="edit" data-account-id="${escapeHtml(account.id)}" class="rounded-lg border border-blue-300 px-2.5 py-1 text-[11px] font-bold text-blue-700 hover:bg-blue-50">编辑</button>
                        <button type="button" data-ap-account-action="delete" data-account-id="${escapeHtml(account.id)}" class="rounded-lg border border-red-300 px-2.5 py-1 text-[11px] font-bold text-red-700 hover:bg-red-50">删除</button>
                    </div>
                </div>
            </article>
        `;
    }).join('');

    els.autoPublishAccountList.querySelectorAll('[data-ap-account-action]').forEach((button) => {
        button.addEventListener('click', () => {
            const accountId = button.getAttribute('data-account-id') || '';
            const action = button.getAttribute('data-ap-account-action') || '';
            if (action === 'edit') {
                editAutoPublishCookieAccount(accountId);
            } else if (action === 'delete') {
                deleteAutoPublishCookieAccount(accountId);
            } else if (action === 'up') {
                moveAutoPublishCookieAccount(accountId, -1);
            } else if (action === 'down') {
                moveAutoPublishCookieAccount(accountId, 1);
            }
        });
    });
}

function collectAutoPublishPayload() {
    const groupId = currentAutoPublishGroupId();
    const groupName = currentAutoPublishGroupName();
    return {
        name: (els.autoPublishStrategyNameInput.value || '').trim() || '知识星球自动发帖策略',
        enabled: Boolean(els.autoPublishStrategyEnabledCheckbox.checked),
        group_id: groupId,
        group_name: groupName,
        interval_minutes: parseBoundedInt(els.autoPublishIntervalInput.value, 30, 1, 1440),
        max_rounds: parsePositiveIntOrZero(els.autoPublishMaxRoundsInput.value, 0, 0),
        cookie_accounts: (state.autoPublishCookieAccounts || []).map((account) => ({
            id: account.id,
            name: account.name,
            cookie: account.cookie,
            enabled: Boolean(account.enabled),
            allowed_group_ids: [...(account.allowed_group_ids || [])],
            notes: account.notes || '',
            quota_hit_count: account.quota_hit_count || 0,
            last_limit_hit_at: account.last_limit_hit_at || '',
            last_limit_error: account.last_limit_error || '',
            last_success_at: account.last_success_at || '',
            last_used_at: account.last_used_at || '',
        })),
        rules: [
            {
                id: 'pdf',
                label: 'PDF 批次',
                enabled: Boolean(els.autoPublishPdfEnabledCheckbox.checked),
                file_type: 'pdf',
                source_dir: (els.autoPublishPdfSourceDirInput.value || '').trim(),
                recursive: Boolean(els.autoPublishPdfRecursiveCheckbox.checked),
                extensions: (els.autoPublishPdfExtensionsInput.value || '').trim(),
                batch_size: parseBoundedInt(els.autoPublishPdfBatchSizeInput.value, 4, 1, 8),
                upload_retry_count: parseBoundedInt(els.autoPublishPdfUploadRetryInput.value, 2, 0, 5),
                max_run_attempts: parseBoundedInt(els.autoPublishPdfMaxAttemptsInput.value, 3, 1, 10),
                max_file_size_mb: parseBoundedInt(els.autoPublishPdfMaxSizeInput.value, 50, 1, 50),
                body_prefix: (els.autoPublishPdfBodyPrefixInput.value || '').trim(),
                include_filenames: Boolean(els.autoPublishPdfIncludeFilenamesCheckbox.checked),
                hashtag_id: (els.autoPublishPdfHashtagIdInput.value || '').trim(),
                hashtag_title: (els.autoPublishPdfHashtagTitleInput.value || '').trim(),
            },
            {
                id: 'audio',
                label: '音频批次',
                enabled: Boolean(els.autoPublishAudioEnabledCheckbox.checked),
                file_type: 'audio',
                source_dir: (els.autoPublishAudioSourceDirInput.value || '').trim(),
                recursive: Boolean(els.autoPublishAudioRecursiveCheckbox.checked),
                extensions: (els.autoPublishAudioExtensionsInput.value || '').trim(),
                batch_size: parseBoundedInt(els.autoPublishAudioBatchSizeInput.value, 4, 1, 8),
                upload_retry_count: parseBoundedInt(els.autoPublishAudioUploadRetryInput.value, 2, 0, 5),
                max_run_attempts: parseBoundedInt(els.autoPublishAudioMaxAttemptsInput.value, 3, 1, 10),
                max_file_size_mb: parseBoundedInt(els.autoPublishAudioMaxSizeInput.value, 50, 1, 50),
                body_prefix: (els.autoPublishAudioBodyPrefixInput.value || '').trim(),
                include_filenames: Boolean(els.autoPublishAudioIncludeFilenamesCheckbox.checked),
                hashtag_id: (els.autoPublishAudioHashtagIdInput.value || '').trim(),
                hashtag_title: (els.autoPublishAudioHashtagTitleInput.value || '').trim(),
            },
        ],
    };
}

function applyAutoPublishConfigToForm(config = {}) {
    state.autoPublishCookieAccounts = Array.isArray(config.cookie_accounts)
        ? config.cookie_accounts.map((account) => ({
            id: account.id,
            name: account.name || '',
            cookie: account.cookie || '',
            enabled: Boolean(account.enabled ?? true),
            allowed_group_ids: [...(account.allowed_group_ids || [])],
            notes: account.notes || '',
            quota_hit_count: account.quota_hit_count || 0,
            last_limit_hit_at: account.last_limit_hit_at || '',
            last_limit_error: account.last_limit_error || '',
            last_success_at: account.last_success_at || '',
            last_used_at: account.last_used_at || '',
        }))
        : [];
    state.autoPublishEditingCookieAccountId = '';
    if (els.autoPublishStrategyNameInput) {
        els.autoPublishStrategyNameInput.value = config.name || '知识星球自动发帖策略';
    }
    if (els.autoPublishStrategyEnabledCheckbox) {
        els.autoPublishStrategyEnabledCheckbox.checked = Boolean(config.enabled ?? true);
    }
    if (els.autoPublishIntervalInput) {
        els.autoPublishIntervalInput.value = String(parseBoundedInt(config.interval_minutes, 30, 1, 1440));
    }
    if (els.autoPublishMaxRoundsInput) {
        els.autoPublishMaxRoundsInput.value = String(parsePositiveIntOrZero(config.max_rounds, 0, 0));
    }

    const ruleMap = new Map((config.rules || []).map((rule) => [rule.id, rule]));
    const pdf = ruleMap.get('pdf');
    if (pdf) {
        els.autoPublishPdfEnabledCheckbox.checked = Boolean(pdf.enabled ?? true);
        els.autoPublishPdfSourceDirInput.value = pdf.source_dir || '';
        els.autoPublishPdfExtensionsInput.value = (pdf.extensions || []).join(',');
        els.autoPublishPdfBatchSizeInput.value = String(pdf.batch_size || 4);
        els.autoPublishPdfUploadRetryInput.value = String(pdf.upload_retry_count || 2);
        els.autoPublishPdfMaxAttemptsInput.value = String(pdf.max_run_attempts || 3);
        els.autoPublishPdfHashtagIdInput.value = pdf.hashtag_id || '';
        els.autoPublishPdfHashtagTitleInput.value = pdf.hashtag_title || '';
        els.autoPublishPdfMaxSizeInput.value = String(Math.min(Number(pdf.max_file_size_mb || 50), 50));
        els.autoPublishPdfBodyPrefixInput.value = pdf.body_prefix || '';
        els.autoPublishPdfIncludeFilenamesCheckbox.checked = Boolean(pdf.include_filenames ?? true);
        els.autoPublishPdfRecursiveCheckbox.checked = Boolean(pdf.recursive ?? true);
    }
    const audio = ruleMap.get('audio');
    if (audio) {
        els.autoPublishAudioEnabledCheckbox.checked = Boolean(audio.enabled ?? true);
        els.autoPublishAudioSourceDirInput.value = audio.source_dir || '';
        els.autoPublishAudioExtensionsInput.value = (audio.extensions || []).join(',');
        els.autoPublishAudioBatchSizeInput.value = String(audio.batch_size || 4);
        els.autoPublishAudioUploadRetryInput.value = String(audio.upload_retry_count || 2);
        els.autoPublishAudioMaxAttemptsInput.value = String(audio.max_run_attempts || 3);
        els.autoPublishAudioHashtagIdInput.value = audio.hashtag_id || '';
        els.autoPublishAudioHashtagTitleInput.value = audio.hashtag_title || '';
        els.autoPublishAudioMaxSizeInput.value = String(Math.min(Number(audio.max_file_size_mb || 50), 50));
        els.autoPublishAudioBodyPrefixInput.value = audio.body_prefix || '';
        els.autoPublishAudioIncludeFilenamesCheckbox.checked = Boolean(audio.include_filenames ?? true);
        els.autoPublishAudioRecursiveCheckbox.checked = Boolean(audio.recursive ?? true);
    }
    renderAutoPublishTagOptions();
    renderAutoPublishCurrentGroup();
    renderAutoPublishCookieAccounts();
    resetAutoPublishCookieAccountForm();
}

async function persistAutoPublishConfig() {
    const payload = collectAutoPublishPayload();
    Object.assign(payload, credentialRequestPayload('publish'));
    const result = await apiPost('/zsxq/auto-publish/config', payload);
    state.autoPublishConfig = result?.config || null;
    applyAutoPublishConfigToForm(state.autoPublishConfig || {});
    renderGroups();
    renderAutoPublishCurrentGroup();
    return result;
}

async function saveAutoPublishConfig() {
    const original = els.autoPublishSaveBtn.innerHTML;
    setLoading(els.autoPublishSaveBtn, true, '保存中...');
    try {
        const result = await persistAutoPublishConfig();
        showAutoPublishMessage(
            'success',
            '自动发帖策略已保存',
            result?.config?.cookie_pool_ready
                ? `配置已写入本地 runtime，当前策略已带 ${result?.config?.active_cookie_account_count || 0} 个可用发帖账号。`
                : (
                    result?.config?.cookie_env_configured
                        ? '配置已写入本地 runtime。当前未配置账号池，持续调度会回退到后端环境变量 Cookie。'
                        : `配置已保存。当前既没有可用账号池，也还没有检测到 ${result?.config?.cookie_env_name || 'ZSXQ_AUTO_PUBLISH_COOKIE'}。建议在平台凭据里保存 zsxq.publish Cookie。`
                )
        );
        await refreshAutoPublishRuntime();
    } catch (error) {
        showAutoPublishMessage('error', '保存失败', error.message);
    } finally {
        setLoading(els.autoPublishSaveBtn, false, original);
    }
}

async function previewAutoPublish() {
    const original = els.autoPublishPreviewBtn.innerHTML;
    setLoading(els.autoPublishPreviewBtn, true, '预览中...');
    try {
        const payload = collectAutoPublishPayload();
        Object.assign(payload, credentialRequestPayload('publish'));
        const result = await apiPost('/zsxq/auto-publish/preview', payload);
        state.autoPublishPreview = result?.preview || null;
        renderAutoPublishPreviewPanel();
        showAutoPublishMessage('success', '预览已刷新', '页面已根据当前表单重新扫描下一批候选文件。');
    } catch (error) {
        showAutoPublishMessage('error', '预览失败', error.message);
    } finally {
        setLoading(els.autoPublishPreviewBtn, false, original);
    }
}

async function runAutoPublishOnce() {
    const original = els.autoPublishRunOnceBtn.innerHTML;
    const originalSecondary = els.autoPublishSchedulerRunOnceBtn.innerHTML;
    setLoading(els.autoPublishRunOnceBtn, true, '提交中...');
    setLoading(els.autoPublishSchedulerRunOnceBtn, true, '提交中...');
    try {
        await persistAutoPublishConfig();
        const result = await apiPost('/zsxq/auto-publish/scheduler/run-once', credentialRequestPayload('publish'));
        showAutoPublishMessage('success', '试跑已提交', result?.scheduler?.message || '后台子进程已启动，请稍后刷新历史记录。');
        await refreshAutoPublishRuntime();
    } catch (error) {
        showAutoPublishMessage('error', '试跑失败', error.message);
    } finally {
        setLoading(els.autoPublishRunOnceBtn, false, original);
        setLoading(els.autoPublishSchedulerRunOnceBtn, false, originalSecondary);
    }
}

async function startAutoPublishScheduler() {
    const original = els.autoPublishSchedulerStartBtn.innerHTML;
    setLoading(els.autoPublishSchedulerStartBtn, true, '启动中...');
    try {
        await persistAutoPublishConfig();
        const result = await apiPost('/zsxq/auto-publish/scheduler/start', credentialRequestPayload('publish'));
        showAutoPublishMessage('success', '调度器已处理', result?.scheduler?.message || '启动请求已提交。');
        await refreshAutoPublishRuntime();
    } catch (error) {
        showAutoPublishMessage('error', '启动失败', error.message);
    } finally {
        setLoading(els.autoPublishSchedulerStartBtn, false, original);
    }
}

async function stopAutoPublishScheduler() {
    const original = els.autoPublishSchedulerStopBtn.innerHTML;
    setLoading(els.autoPublishSchedulerStopBtn, true, '停止中...');
    try {
        const result = await apiPost('/zsxq/auto-publish/scheduler/stop', {});
        showAutoPublishMessage('success', '调度器已处理', result?.scheduler?.message || '停止请求已提交。');
        await refreshAutoPublishRuntime();
    } catch (error) {
        showAutoPublishMessage('error', '停止失败', error.message);
    } finally {
        setLoading(els.autoPublishSchedulerStopBtn, false, original);
    }
}

function renderAutoPublishSchedulerPanel() {
    const scheduler = state.autoPublishScheduler || {};
    const config = scheduler.config || state.autoPublishConfig || {};
    const running = Boolean(scheduler.running);
    const cookieMode = autoPublishCookieSourceText(config, scheduler);
    if (els.autoPublishSchedulerStatusBadge) {
        els.autoPublishSchedulerStatusBadge.textContent = running ? '运行中' : '未运行';
        els.autoPublishSchedulerStatusBadge.className = `rounded-full px-2 py-1 text-[10px] font-black ${running ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'}`;
    }
    if (els.autoPublishSchedulerStatus) {
        els.autoPublishSchedulerStatus.innerHTML = `
            <div class="space-y-1">
                <div>进程状态：${escapeHtml(running ? '已启动' : '未启动')} · PID ${escapeHtml(String(scheduler.pid || '-'))}</div>
                <div>策略：${escapeHtml(config.name || '未保存')}</div>
                <div>星球：${escapeHtml(config.group_name || config.group_id || currentAutoPublishGroupName() || currentAutoPublishGroupId() || '未选择')}</div>
                <div>间隔：${escapeHtml(String(config.interval_minutes || 30))} 分钟一次 · 轮次：${config.continuous ? '持续模式' : escapeHtml(String(config.max_rounds || 0))}</div>
                <div>Cookie：${escapeHtml(cookieMode)}</div>
                <div>账号池：${escapeHtml(String(config.cookie_account_count || 0))} 个账号 · 当前策略可用 ${escapeHtml(String(config.active_cookie_account_count || 0))} 个</div>
                <div>日志：<span class="font-mono break-all">${escapeHtml(scheduler.log_path || '-')}</span></div>
                <div class="text-slate-400">${escapeHtml(scheduler.message || '')}</div>
            </div>
        `;
    }
    const hasConfig = Boolean(config.exists || config.group_id || currentAutoPublishGroupId());
    els.autoPublishSchedulerStartBtn.disabled = !hasConfig || running;
    els.autoPublishSchedulerStopBtn.disabled = !running;
    els.autoPublishSchedulerRunOnceBtn.disabled = !hasConfig;
    els.autoPublishSchedulerStartBtn.classList.toggle('opacity-50', els.autoPublishSchedulerStartBtn.disabled);
    els.autoPublishSchedulerStopBtn.classList.toggle('opacity-50', els.autoPublishSchedulerStopBtn.disabled);
    renderWorkspaceSnapshot();
}

function renderAutoPublishPreviewPanel() {
    const preview = state.autoPublishPreview;
    if (!preview?.rules?.length) {
        els.autoPublishPreviewPanel.innerHTML = '<div class="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-400">尚未生成预览</div>';
        return;
    }
    els.autoPublishPreviewPanel.innerHTML = preview.rules.map((item) => {
        const nextBatch = item.next_batch || [];
        const reasons = Object.entries(item.skipped_reasons || {}).map(([key, value]) => `${key}:${value}`).join('，') || '无';
        return `
            <article class="rounded-lg border border-slate-200 bg-white p-4">
                <div class="flex items-center justify-between gap-3">
                    <div>
                        <div class="font-black text-slate-800">${escapeHtml(item.rule?.label || item.rule?.id)}</div>
                        <div class="text-xs text-slate-500 mt-1">候选 ${item.eligible_count || 0} 个；本批 ${nextBatch.length} 个；跳过 ${item.skipped_count || 0} 个</div>
                    </div>
                    <span class="auto-publish-badge auto-publish-badge-muted">${escapeHtml(item.rule?.file_type || '-')}</span>
                </div>
                <div class="text-xs text-slate-500 mt-2">跳过原因：${escapeHtml(reasons)}</div>
                <div class="auto-publish-file-list mt-3 space-y-2">
                    ${nextBatch.length ? nextBatch.map((file) => `
                        <div class="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-xs">
                            <div class="font-bold text-slate-700 break-words">${escapeHtml(file.name)}</div>
                            <div class="text-slate-500 mt-1">${escapeHtml(file.path)}</div>
                            <div class="text-slate-400 mt-1">${escapeHtml(String(file.size_mb))} MB · 尝试 ${escapeHtml(String(file.attempts || 0))}</div>
                        </div>
                    `).join('') : '<div class="rounded-lg border border-dashed border-slate-200 p-3 text-xs text-slate-400">没有可发布的新文件</div>'}
                </div>
            </article>
        `;
    }).join('');
}

function autoPublishRuleMetrics(rule = {}) {
    const files = Array.isArray(rule.files) ? rule.files : [];
    const publishFailed = rule.status === 'failed' && (
        Boolean(rule.publish_error) || String(rule.message || '').includes('发布失败')
    );
    const uploadedFromFiles = publishFailed
        ? 0
        : files.filter((item) => ['uploaded', 'published'].includes(String(item?.status || ''))).length;
    const failedFromFiles = files.filter((item) => String(item?.status || '') === 'failed').length;
    const selected = Number(rule.selected_count ?? files.length ?? 0) || 0;
    const uploaded = publishFailed ? 0 : (Number(rule.uploaded_count ?? uploadedFromFiles) || 0);
    const failed = publishFailed ? selected : (Number(rule.failed_count ?? failedFromFiles) || 0);
    return { selected, uploaded, failed };
}

function effectiveAutoPublishRunStatus(run = {}) {
    const rules = Array.isArray(run.rules) ? run.rules : [];
    const hasFailed = rules.some((rule) => rule?.status === 'failed');
    const hasPartial = rules.some((rule) => rule?.status === 'partial');
    if (hasFailed && rules.some((rule) => ['success', 'partial'].includes(rule?.status))) {
        return 'partial';
    }
    if (hasPartial) {
        return 'partial';
    }
    return run.status || '-';
}

function renderAutoPublishRunsPanel() {
    if (!state.autoPublishRuns.length) {
        els.autoPublishRunsPanel.innerHTML = '<div class="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-400">暂无运行记录</div>';
        return;
    }
    els.autoPublishRunsPanel.innerHTML = state.autoPublishRuns.map((run) => {
        const runStatus = effectiveAutoPublishRunStatus(run);
        const badgeClass = runStatus === 'success'
            ? 'auto-publish-badge-success'
            : runStatus === 'partial'
                ? 'auto-publish-badge-warning'
                : runStatus === 'failed'
                    ? 'auto-publish-badge-danger'
                    : 'auto-publish-badge-muted';
        const rules = (run.rules || []).map((rule) => {
            const metrics = autoPublishRuleMetrics(rule);
            return `
                <div class="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-xs">
                    <div class="flex items-center justify-between gap-2">
                        <span class="font-bold text-slate-700">${escapeHtml(rule.label || rule.rule_id)}</span>
                        <span>${escapeHtml(rule.status || '-')}</span>
                    </div>
                    <div class="mt-1 text-slate-500">${escapeHtml(rule.message || '')}</div>
                    <div class="mt-1 text-slate-400">选中 ${escapeHtml(String(metrics.selected))} · 已发布 ${escapeHtml(String(metrics.uploaded))} · 失败 ${escapeHtml(String(metrics.failed))}</div>
                    ${rule.cookie_account_name ? `<div class="mt-1 text-slate-400">账号：${escapeHtml(rule.cookie_account_name)}</div>` : ''}
                    ${(rule.cookie_attempts || []).length ? `<div class="mt-1 text-slate-400">切换：${escapeHtml((rule.cookie_attempts || []).map(item => `${item.index}.${item.cookie_account_name}:${item.status}`).join('；'))}</div>` : ''}
                    ${rule.topic_id ? `<div class="mt-1 text-slate-400">topic_id=${escapeHtml(String(rule.topic_id))}</div>` : ''}
                </div>
            `;
        }).join('');
        return `
            <article class="auto-publish-history-item">
                <div class="flex items-center justify-between gap-3">
                    <div>
                        <div class="font-black text-slate-800">${escapeHtml(run.name || '自动发帖批次')}</div>
                        <div class="text-xs text-slate-500 mt-1">${escapeHtml(run.group_name || run.group_id || '-')} · ${escapeHtml(formatZsxqTime(run.started_at || ''))}</div>
                        <div class="text-[11px] text-slate-400 mt-1">来源：${escapeHtml(run.cookie_source || '-')} · 账号数：${escapeHtml(String(run.cookie_account_count || 0))}</div>
                    </div>
                    <span class="auto-publish-badge ${badgeClass}">${escapeHtml(runStatus)}</span>
                </div>
                <div class="text-sm text-slate-600 mt-2">${escapeHtml(run.message || '')}</div>
                <div class="mt-3 space-y-2">${rules || '<div class="text-xs text-slate-400">暂无规则结果</div>'}</div>
            </article>
        `;
    }).join('');
}

function renderAutoPublishLogsPanel() {
    els.autoPublishLogsPanel.textContent = state.autoPublishLogsRaw || '暂无日志';
}

function renderMessage(type, text) {
    if (!els.pageMessage) return;
    const colorMap = {
        loading: 'border-blue-200 bg-blue-50 text-blue-700',
        success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
        error: 'border-red-200 bg-red-50 text-red-700',
        warning: 'border-amber-200 bg-amber-50 text-amber-700',
        muted: 'border-slate-200 bg-slate-50 text-slate-500',
    };
    els.pageMessage.className = `rounded-lg border ${colorMap[type] || colorMap.muted}`;
    els.pageMessage.textContent = text;
    els.pageMessage.classList.remove('hidden');
}

function setLoading(button, isLoading, html) {
    button.disabled = isLoading;
    button.classList.toggle('opacity-60', isLoading);
    button.innerHTML = isLoading ? `<i class="fas fa-circle-notch fa-spin mr-2"></i>${html}` : html;
}

function formatZsxqTime(value) {
    if (!value) return '尚未触发';
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

function parsePositiveInt(value, fallback, minValue = 1) {
    if (value === null || value === undefined) return fallback;
    const text = String(value).trim();
    if (text === '') return fallback;
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.max(minValue, Math.min(Math.round(number), 1440));
}

function parsePositiveIntOrZero(value, fallback, minValue = 0) {
    if (value === null || value === undefined) return fallback;
    const text = String(value).trim();
    if (text === '') return 0;
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    const fixed = Math.round(number);
    if (fixed <= minValue) return 0;
    return Math.min(fixed, 100);
}

function parseBoundedInt(value, fallback, minValue, maxValue) {
    if (value === null || value === undefined) return fallback;
    const text = String(value).trim();
    if (text === '') return fallback;
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    const fixed = Math.round(number);
    return Math.max(minValue, Math.min(fixed, maxValue));
}

function sanitizeLogText(value) {
    if (Array.isArray(value)) {
        return value
            .map(item => sanitizeLogText(item))
            .filter(Boolean)
            .join('\n');
    }

    const text = typeof value === 'string' ? value : String(value ?? '');
    if (!text) {
        return '';
    }

    return text
        .replace(/\r\n/g, '\n')
        .replace(/\r/g, '\n')
        .replace(/\x1b\[[0-9;]*[A-Za-z]/g, '')
        .replace(/\x1b\][^\x07]*(\x07|$)/g, '')
        .replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, '')
        .replace(/\u0000/g, '')
        .replace(/\u2028|\u2029/g, '\n')
        .replace(/\t/g, '    ')
        .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, '')
        .trimEnd();
}

function formatConfigMaxRounds(value) {
    if (!value || Number(value) <= 0) return '持续运行';
    return `前 ${Number(value)} 轮`;
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

function batchStatusMeta(status) {
    if (status === 'completed') {
        return { label: '成功', tone: 'success' };
    }
    if (status === 'failed') {
        return { label: '失败', tone: 'danger' };
    }
    if (status === 'queued' || status === 'running') {
        return { label: syncStatusLabel(status), tone: 'info' };
    }
    return { label: syncStatusLabel(status), tone: 'muted' };
}

function topicStatusLabel(status) {
    const map = {
        new: '新增',
        touched: '本轮处理',
    };
    return map[status] || status || '本轮处理';
}

function attachmentStatusMeta(item) {
    const status = item?.download_status || 'pending';
    if (status === 'downloaded') {
        const localPath = String(item.local_path || '');
        return {
            label: localPath.startsWith('browser-download:') ? '下载过' : '已归档',
            tone: 'success',
        };
    }
    if (status === 'failed') {
        return { label: '失败', tone: 'danger' };
    }
    if (status === 'skipped') {
        return { label: '已跳过', tone: 'muted' };
    }
    return { label: '待处理', tone: 'warning' };
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
