const state = {
    services: [],
    selectedServiceId: 'render',
    busyServiceId: null,
    busyWatchdogAction: null,
    busyRestartAll: false,
    watchdog: null,
    watchdogError: '',
    refreshPollingController: null,
    refreshPromise: null,
    refreshing: false,
    inspecting: false,
    pollingPaused: false,
    lastStatusAt: null,
    statusError: '',
    broker: null,
    search: '',
    filter: 'all',
    logRequestId: 0,
    logLoading: false,
    logServiceId: null,
    noticeTimer: null,
    expandedDetails: new Set(),
};

const WORKFLOW_SERVICE_ORDER = ['report_audio_workflow', 'report_script_material_workflow', 'report_top10_workflow', 'mp3_video_workflow'];
const WORKFLOW_SERVICE_IDS = new Set(WORKFLOW_SERVICE_ORDER);
const ZSXQ_SUPPORT_SERVICE_ORDER = ['zsxq_sync', 'zsxq_workflow', 'content_pipeline', 'zsxq_publish'];
const ZSXQ_SUPPORT_SERVICE_IDS = new Set(ZSXQ_SUPPORT_SERVICE_ORDER);

const els = {
    brokerStatus: document.getElementById('broker-status'),
    restartAllBtn: document.getElementById('restart-all-btn'),
    refreshBtn: document.getElementById('refresh-btn'),
    notice: document.getElementById('notice'),
    watchdogCard: document.getElementById('watchdog-card'),
    serviceGrid: document.getElementById('service-grid'),
    serviceList: document.getElementById('service-list'),
    lastUpdated: document.getElementById('last-updated'),
    logTitle: document.getElementById('log-title'),
    logSubtitle: document.getElementById('log-subtitle'),
    logOutput: document.getElementById('log-output'),
    refreshLogBtn: document.getElementById('refresh-log-btn'),
    statusSummary: document.getElementById('status-summary'),
    serviceSearch: document.getElementById('service-search'),
    serviceFilters: document.getElementById('service-filters'),
    filterCount: document.getElementById('filter-count'),
    inspectBtn: document.getElementById('inspect-btn'),
    togglePollingBtn: document.getElementById('toggle-polling-btn'),
    pollingStatus: document.getElementById('polling-status'),
    logsSection: document.getElementById('logs-section'),
    logLines: document.getElementById('log-lines'),
    logFollow: document.getElementById('log-follow'),
};

document.addEventListener('DOMContentLoaded', () => {
    bindEvents();
    render();
    refreshStatus({ loadLogs: true });
    state.refreshPollingController = window.VisibilityPolling.create({
        intervalMs: 15000,
        poll: () => isActionBusy() ? Promise.resolve() : refreshStatus({ silent: true }),
    });
    state.refreshPollingController.start();
    document.addEventListener('visibilitychange', renderFreshness);
});

function bindEvents() {
    els.refreshBtn.addEventListener('click', () => refreshStatus({ loadLogs: true }));
    els.restartAllBtn.addEventListener('click', restartAll);
    els.refreshLogBtn.addEventListener('click', () => loadLogs(state.selectedServiceId));
    els.logLines.addEventListener('change', () => loadLogs(state.selectedServiceId));
    els.inspectBtn.addEventListener('click', () => refreshStatus({ inspect: true }));
    els.togglePollingBtn.addEventListener('click', () => {
        state.pollingPaused = !state.pollingPaused;
        if (state.pollingPaused) state.refreshPollingController.stop();
        else {
            state.refreshPollingController.start();
            if (!document.hidden && !isActionBusy()) refreshStatus({ silent: true });
        }
        renderFreshness();
    });
    els.serviceSearch.addEventListener('input', () => {
        state.search = els.serviceSearch.value.trim().toLowerCase();
        renderCards();
    });
    els.serviceFilters.addEventListener('click', event => {
        const button = event.target.closest('[data-filter]');
        if (!button) return;
        state.filter = button.dataset.filter;
        renderCards();
    });
    els.serviceGrid.addEventListener('toggle', rememberDetails, true);
    els.watchdogCard.addEventListener('toggle', rememberDetails, true);
    els.watchdogCard.addEventListener('click', handleWatchdogAction);
    els.serviceGrid.addEventListener('click', handleServiceAction);
    els.serviceList.addEventListener('click', (event) => {
        const button = event.target.closest('[data-select-service]');
        if (!button) return;
        selectService(button.dataset.selectService);
    });
}

async function handleServiceAction(event) {
    const logButton = event.target.closest('[data-view-logs]');
    if (logButton) {
        selectService(logButton.dataset.viewLogs, true);
        return;
    }
    if (event.target.closest('[data-clear-filters]')) {
        state.search = '';
        state.filter = 'all';
        els.serviceSearch.value = '';
        renderCards();
        els.serviceSearch.focus();
        return;
    }
    const button = event.target.closest('[data-action]');
    if (!button || button.disabled || isActionBusy()) return;
    const serviceId = button.dataset.serviceId;
    const action = button.dataset.action;
    if (action === 'purge' && !confirmPurge(serviceId)) {
        return;
    }
    if (['stop', 'restart'].includes(action) && !window.confirm(
        `确认${action === 'stop' ? '停止' : '重启'}「${serviceLabel(serviceId)}」？\n正在执行的任务可能中断，队列中尚未领取的消息会保留。`
    )) return;
    await runAction(serviceId, action);
}

function selectService(serviceId, scroll = false) {
    state.selectedServiceId = serviceId;
    renderCards();
    renderServiceList();
    loadLogs(serviceId);
    if (scroll) {
        els.logsSection.scrollIntoView({ block: 'start' });
        els.logTitle.focus({ preventScroll: true });
    }
}

function rememberDetails(event) {
    const details = event.target;
    if (!details.isConnected || !details.dataset.detailId) return;
    if (details.open) state.expandedDetails.add(details.dataset.detailId);
    else state.expandedDetails.delete(details.dataset.detailId);
}

function isActionBusy() {
    return Boolean(state.busyServiceId || state.busyWatchdogAction || state.busyRestartAll);
}

function updatePanel(container, html) {
    const focused = document.activeElement;
    const ownsFocus = focused && container.contains?.(focused);
    const isSummary = focused?.tagName === 'SUMMARY';
    const owner = isSummary ? focused.parentElement : focused;
    const identity = ownsFocus ? Object.entries(owner.dataset || {}) : [];
    const scrollTop = container.scrollTop;
    container.innerHTML = html;
    container.scrollTop = scrollTop;
    if (!identity.length) return;
    const selector = isSummary ? '[data-detail-id]' : 'button';
    const replacement = Array.from(container.querySelectorAll(selector)).find(candidate =>
        identity.every(([key, value]) => candidate.dataset[key] === value));
    const target = isSummary ? replacement?.querySelector('summary') : replacement;
    target?.focus({ preventScroll: true });
}

async function apiFetch(path, options = {}) {
    const { timeoutMs = 8000, ...fetchOptions } = options;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const response = await fetch(`${apiBaseUrl()}${path}`, {
            headers: { 'Content-Type': 'application/json' },
            signal: controller.signal,
            ...fetchOptions,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.success) {
            const error = new Error(data.message || data?.error?.message || `HTTP ${response.status}`);
            error.status = response.status;
            throw error;
        }
        return data.data;
    } catch (error) {
        if (error.name === 'AbortError') {
            throw new Error(`请求超时（${Math.round(timeoutMs / 1000)}s），后端可能繁忙但不一定是 5001 未启动。`);
        }
        throw error;
    } finally {
        clearTimeout(timeoutId);
    }
}

function apiBaseUrl() {
    return window.API_BASE_URL || window.CONFIG?.API_BASE_URL || 'http://localhost:5001/api/v1';
}

function refreshStatus(options = {}) {
    if (state.refreshPromise) return state.refreshPromise;
    state.refreshing = true;
    state.inspecting = options.inspect === true;
    renderGlobalControls();
    state.refreshPromise = fetchStatus(options).finally(() => {
        state.refreshPromise = null;
        state.refreshing = false;
        state.inspecting = false;
        renderGlobalControls();
        renderFreshness();
    });
    return state.refreshPromise;
}

async function fetchStatus(options = {}) {
    if (!options.silent) setNotice('正在刷新 Celery 状态...', 'muted');
    const includeInspect = options.inspect === true;
    const [statusResult, watchdogResult] = await Promise.all([
        apiFetch(includeInspect ? '/celery/status?inspect=1' : '/celery/status', { timeoutMs: includeInspect ? 30000 : 12000 })
            .then(data => ({ data }))
            .catch(error => ({ error })),
        apiFetch('/celery/watchdog/status', { timeoutMs: 8000 })
            .then(data => ({ data }))
            .catch(error => ({ error })),
    ]);

    if (watchdogResult.data) {
        state.watchdog = watchdogResult.data.watchdog || null;
        state.watchdogError = '';
    } else {
        state.watchdogError = watchdogResult.error?.message || 'Watchdog 状态接口请求失败';
    }

    if (statusResult.error) {
        state.statusError = statusResult.error.message;
        render();
        setNotice(`刷新 Worker 状态失败：${statusResult.error.message}`, 'error');
        return;
    }

    try {
        const data = statusResult.data;
        if (!data || !Array.isArray(data.services)) throw new Error('状态响应缺少 Worker 列表');
        const recovered = Boolean(state.statusError);
        state.services = data.services || [];
        state.broker = data.broker || {};
        state.lastStatusAt = new Date();
        state.statusError = '';
        const previousSelection = state.selectedServiceId;
        if (!state.services.some(service => service.id === state.selectedServiceId)) {
            state.selectedServiceId = state.services[0]?.id || null;
        }
        if (!state.selectedServiceId) {
            state.logRequestId++;
            state.logLoading = false;
            state.logServiceId = null;
            els.logTitle.textContent = '运行日志';
            els.logSubtitle.textContent = '当前没有可查看的 Worker。';
            els.logOutput.textContent = '暂无日志。';
            els.logOutput.setAttribute('aria-busy', 'false');
        }
        render();
        if (options.loadLogs || previousSelection !== state.selectedServiceId) {
            if (state.services.length) await loadLogs(state.selectedServiceId);
        }
        if (!options.silent || recovered) setNotice(recovered ? '连接已恢复，状态已刷新。' : '状态已刷新。', 'success');
    } catch (error) {
        state.statusError = error.message;
        render();
        setNotice(`刷新失败：${error.message}`, 'error');
    }
}

async function handleWatchdogAction(event) {
    const button = event.target.closest('[data-watchdog-action]');
    if (!button) return;
    const action = button.dataset.watchdogAction;
    await runWatchdogAction(action);
}

async function runWatchdogAction(action) {
    if (isActionBusy()) return;
    state.busyWatchdogAction = action;
    render();
    const actionLabel = {
        start: '启动守护',
        stop: '停止守护',
        restart: '重启守护',
        'run-once': '立即检查',
    }[action] || action;
    try {
        setNotice(`正在${actionLabel}...`, 'muted');
        const data = await apiFetch(`/celery/watchdog/${action}`, { method: 'POST' });
        state.watchdog = data.watchdog || data?.watchdog_status || state.watchdog;
        if (action === 'run-once') {
            const touched = (data.results || []).filter(item => item.action && item.action !== 'none');
            setNotice(`Watchdog 检查完成${touched.length ? `：${touched.length} 个队列有动作` : '。'}`, 'success');
        } else {
            setNotice(`Watchdog 已${actionLabel.replace('守护', '')}。`, 'success');
        }
        await state.refreshPromise;
        await refreshStatus({ silent: true });
    } catch (error) {
        setNotice(`${actionLabel}失败：${error.message}`, 'error');
    } finally {
        state.busyWatchdogAction = null;
        render();
    }
}

async function runAction(serviceId, action) {
    if (isActionBusy()) return;
    state.busyServiceId = serviceId;
    render();
    const actionLabel = { start: '启动', stop: '停止', restart: '重启', purge: '清空积压' }[action] || action;
    try {
        setNotice(`正在${actionLabel} ${serviceLabel(serviceId)}...`, 'muted');
        const result = await apiFetch(`/celery/${action}/${serviceId}`, { method: 'POST' });
        if (action === 'purge') {
            setNotice(`${serviceLabel(serviceId)} 已清空积压：${result.purged ?? 0} 条。`, 'success');
        } else {
            setNotice(`${serviceLabel(serviceId)} 已${actionLabel}。`, 'success');
        }
        await state.refreshPromise;
        await refreshStatus({ silent: true, loadLogs: state.selectedServiceId === serviceId });
    } catch (error) {
        if (action === 'purge' && (error.status === 405 || error.message.includes('POST 不被允许'))) {
            setNotice('清空积压接口未生效：请重启后端服务后再点清空。当前前端已经是新版本，但运行中的后端还没有加载 purge 路由。', 'error');
        } else {
            setNotice(`${actionLabel}失败：${error.message}`, 'error');
        }
    } finally {
        state.busyServiceId = null;
        render();
    }
}

async function restartAll() {
    if (isActionBusy()) return;
    const ok = window.confirm(
        '确认重启未暂停的 Celery Worker、Celery Watchdog，以及当前正在运行的 scheduler？已暂停的 Worker 会跳过。\n\n' +
        '正在执行中的任务可能会被中断，尚未被 worker 取走的队列消息会保留。'
    );
    if (!ok) return;

    state.busyRestartAll = true;
    state.busyServiceId = null;
    state.busyWatchdogAction = null;
    render();
    try {
        setNotice('正在重启全部 Celery 和 scheduler...', 'muted');
        const data = await apiFetch('/celery/restart-all', {
            method: 'POST',
            body: JSON.stringify({}),
            timeoutMs: 120000,
        });
        const summary = data.summary || {};
        setNotice(
            `重启完成：Celery ${summary.celery_started ?? 0}/${summary.celery_total ?? 0}，` +
            `scheduler ${summary.schedulers_started ?? 0} 个已重启，${summary.schedulers_skipped ?? 0} 个未运行已跳过。`,
            'success'
        );
        await state.refreshPromise;
        await refreshStatus({ silent: true, loadLogs: true });
    } catch (error) {
        setNotice(`一键重启失败：${error.message}`, 'error');
    } finally {
        state.busyRestartAll = false;
        render();
    }
}

function confirmPurge(serviceId) {
    const service = state.services.find(item => item.id === serviceId);
    const queueLength = Number(service?.queue_length || 0);
    return window.confirm(
        `确认清空「${service?.label || serviceId}」队列里的 ${queueLength} 条积压消息？\n\n` +
        `这个操作只会剔除还在 Redis 队列里排队、尚未被 worker 取走的消息；` +
        `不会中断正在 processing 的任务，也不会修改数据库任务记录。`
    );
}

async function loadLogs(serviceId) {
    if (!serviceId) return;
    const requestId = ++state.logRequestId;
    const changedService = state.logServiceId !== serviceId;
    state.logServiceId = serviceId;
    state.logLoading = true;
    els.logTitle.textContent = `${serviceLabel(serviceId)} 日志`;
    els.logSubtitle.textContent = '正在读取日志…';
    els.logOutput.setAttribute('aria-busy', 'true');
    if (changedService) els.logOutput.textContent = '正在读取日志…';
    renderGlobalControls();
    const scrollTop = els.logOutput.scrollTop;
    try {
        const lines = Number(els.logLines.value) || 160;
        const data = await apiFetch(`/celery/logs/${encodeURIComponent(serviceId)}?lines=${lines}`);
        if (requestId !== state.logRequestId || serviceId !== state.selectedServiceId) return;
        els.logSubtitle.textContent = `${data.log_file || '运行日志'} · 更新于 ${formatTime(new Date())} · 手动刷新`;
        els.logOutput.textContent = data.logs || '暂无日志。';
        els.logOutput.scrollTop = els.logFollow.checked ? els.logOutput.scrollHeight : (changedService ? 0 : scrollTop);
    } catch (error) {
        if (requestId !== state.logRequestId || serviceId !== state.selectedServiceId) return;
        els.logSubtitle.textContent = `日志读取失败：${error.message} · 可点击刷新日志重试`;
        if (changedService) els.logOutput.textContent = `日志读取失败：${error.message}`;
    } finally {
        if (requestId === state.logRequestId) {
            state.logLoading = false;
            els.logOutput.setAttribute('aria-busy', 'false');
            renderGlobalControls();
        }
    }
}

function render() {
    if (state.broker) renderBroker(state.broker);
    if (state.statusError) {
        els.brokerStatus.className = 'rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-700';
        els.brokerStatus.textContent = '连接待确认 · 状态刷新失败';
        els.brokerStatus.title = state.statusError;
    }
    renderGlobalControls();
    renderSummary();
    renderWatchdog();
    renderCards();
    renderServiceList();
    renderFreshness();
}

function renderFreshness() {
    els.lastUpdated.textContent = state.lastStatusAt
        ? `${state.statusError ? '刷新失败 · 上次成功' : '上次成功刷新'} ${formatTime(state.lastStatusAt)}`
        : (state.statusError ? '状态获取失败，请重试' : '正在获取状态…');
    els.lastUpdated.dataset.stale = String(Boolean(state.statusError));
    els.pollingStatus.textContent = state.pollingPaused ? '自动刷新已暂停'
        : document.hidden ? '页面不可见，刷新已暂停' : '状态每 15 秒刷新';
    els.togglePollingBtn.textContent = state.pollingPaused ? '恢复自动刷新' : '暂停自动刷新';
    els.togglePollingBtn.setAttribute('aria-pressed', String(state.pollingPaused));
}

function renderGlobalControls() {
    const busy = isActionBusy();
    els.restartAllBtn.disabled = busy || !state.services.length || Boolean(state.statusError);
    els.restartAllBtn.innerHTML = state.busyRestartAll
        ? '<i class="fas fa-spinner fa-spin mr-2" aria-hidden="true"></i>重启中'
        : '<i class="fas fa-power-off mr-2" aria-hidden="true"></i>重启全部';
    els.refreshBtn.disabled = busy || state.refreshing;
    els.refreshBtn.innerHTML = state.refreshing
        ? '<i class="fas fa-spinner fa-spin mr-2" aria-hidden="true"></i>刷新中'
        : '<i class="fas fa-rotate mr-2" aria-hidden="true"></i>刷新';
    els.inspectBtn.disabled = busy || state.refreshing;
    els.inspectBtn.textContent = state.inspecting ? '检查中…' : '检查实时任务';
    els.refreshLogBtn.disabled = state.logLoading || !state.services.length;
    els.serviceGrid.setAttribute('aria-busy', String(state.refreshing));
}

function needsAttention(service) {
    return Boolean(service.queue_error || service.business_tasks_error || service.report_video_stats_error ||
        service.consumer_warning || (!service.running && !service.paused) ||
        (!service.running && Number(service.queue_length) > 0));
}

function renderSummary() {
    const services = state.services;
    const loaded = Boolean(state.lastStatusAt);
    const online = services.filter(service => service.running).length;
    const attention = services.filter(needsAttention).length;
    const paused = services.filter(service => service.paused).length;
    const queueUnknown = services.some(service => service.queue_error || service.queue_length == null);
    const businessUnknown = services.some(service => service.business_tasks_error);
    const backlog = services.reduce((sum, service) => sum + Number(service.queue_length || 0), 0);
    const business = services.reduce((sum, service) => sum + Number(service.business_active_count || 0), 0);
    const cards = [
        { label: '在线 Worker', icon: 'server', value: loaded ? `${online} <small>/ ${services.length}</small>` : '—', note: loaded ? `${paused} 个已暂停 · 进程状态` : '等待状态数据' },
        { label: '需关注', icon: 'triangle-exclamation', value: loaded ? attention : '—', note: loaded ? (attention ? '离线、消费告警或读取失败' : '当前没有 Worker 告警') : '等待状态数据', alert: attention > 0 },
        { label: '队列积压', icon: 'layer-group', value: loaded ? `${backlog}${queueUnknown ? '+' : ''}` : '—', note: queueUnknown ? '部分队列读取失败，显示已知数量' : '尚未被 Worker 领取的消息' },
        { label: '业务进行中', icon: 'bars-progress', value: loaded ? `${business}${businessUnknown ? '+' : ''}` : '—', note: businessUnknown ? '部分业务数据读取失败' : '当前返回的业务任务数' },
    ];
    els.statusSummary.innerHTML = cards.map(card => `
        <div class="monitor-stat ${card.alert ? 'monitor-stat-alert' : ''}">
            <div class="monitor-stat-label"><span>${card.label}</span><i class="fas fa-${card.icon}" aria-hidden="true"></i></div>
            <div class="monitor-stat-value">${card.value}</div>
            <div class="monitor-stat-note">${state.statusError ? '数据已过期 · 等待重新连接' : card.note}</div>
        </div>
    `).join('');
}

function renderWatchdog() {
    if (state.watchdogError) {
        els.watchdogCard.innerHTML = `<div class="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700" role="status">Watchdog 状态获取失败：${escapeHtml(state.watchdogError)}。可刷新重试。</div>`;
        return;
    }
    if (!state.watchdog) {
        els.watchdogCard.innerHTML = '<div class="rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500">正在获取 Watchdog 状态…</div>';
        return;
    }
    const watchdog = state.watchdog;
    const config = watchdog.config || {};
    const events = Array.isArray(watchdog.state?.events) ? watchdog.state.events.slice(-3).reverse() : [];
    const running = Boolean(watchdog.running);
    const busy = isActionBusy();
    const serviceText = Array.isArray(config.services) ? config.services.map(serviceLabel).join('、') : '未配置';
    updatePanel(els.watchdogCard, `
        <div class="rounded-lg border border-slate-200 bg-white px-4 py-3">
            <div class="flex flex-wrap items-center justify-between gap-3">
                <div class="flex flex-wrap items-center gap-2">
                    <h2 class="text-sm font-bold"><i class="fas fa-shield-halved mr-2 text-slate-400" aria-hidden="true"></i>Celery Watchdog</h2>
                    <span class="rounded-full px-2.5 py-1 text-xs font-bold ${running ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}">${running ? '运行中' : '未运行'}</span>
                    <span class="text-xs text-slate-500">${config.enabled ? '自动恢复已开启' : '仅告警'} · 每 ${escapeHtml(config.poll_seconds ?? '—')} 秒检查</span>
                </div>
                <div class="monitor-watchdog-actions">
                    <button data-watchdog-action="${running ? 'stop' : 'start'}" ${busy ? 'disabled' : ''} class="monitor-button">${running ? '停止守护' : '启动守护'}</button>
                    <button data-watchdog-action="restart" ${busy ? 'disabled' : ''} class="monitor-button">重启守护</button>
                    <button data-watchdog-action="run-once" ${busy ? 'disabled' : ''} class="monitor-button" title="检查队列并按当前策略执行自动恢复">立即检查</button>
                </div>
            </div>
            <details class="monitor-details mt-1" data-detail-id="watchdog" ${state.expandedDetails.has('watchdog') ? 'open' : ''}>
                <summary>守护配置与最近事件${events.length ? ` · ${events.length} 条` : ''}</summary>
                <div class="mb-2 leading-6">PID ${watchdog.pids?.length ? watchdog.pids.map(escapeHtml).join(', ') : '—'} · 连续确认 ${escapeHtml(config.confirmations ?? '—')} 次 · 冷却 ${formatDuration(config.cooldown_seconds)}</div>
                <p class="mb-2 leading-5">监控：${escapeHtml(serviceText)}</p>
                ${events.map(event => `<div class="mt-1 rounded bg-slate-50 px-3 py-2"><span class="font-semibold text-slate-700">${escapeHtml(serviceLabel(event.service_id))}</span> · ${escapeHtml(event.message || event.event_type || '—')} <span class="text-slate-500">${escapeHtml(formatTime(event.created_at))}</span></div>`).join('') || '<p class="py-2">暂无守护事件。</p>'}
            </details>
        </div>
    `);
}

function renderBroker(broker) {
    const ok = Boolean(broker.ok);
    const inspectError = broker.inspect_error && broker.inspect_error !== 'skipped' ? broker.inspect_error : '';
    els.brokerStatus.className = ok
        ? (inspectError
            ? 'rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-700'
            : 'rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-700')
        : 'rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-bold text-red-600';
    els.brokerStatus.innerHTML = ok && inspectError
        ? `<i class="fas fa-triangle-exclamation mr-2"></i>Broker 正常 · 任务检查不完整`
        : ok
            ? `<i class="fas fa-circle-check mr-2"></i>Broker 正常`
        : `<i class="fas fa-triangle-exclamation mr-2"></i>Broker 异常`;
    els.brokerStatus.title = inspectError || broker.error || broker.url_masked || '';
}

function renderTaskPanel(service) {
    const tasks = service.tasks || {};
    const active = tasks.active || [];
    const reserved = tasks.reserved || [];
    const scheduled = tasks.scheduled || [];
    const businessTasks = service.business_tasks || [];
    const hasTasks = active.length || reserved.length || scheduled.length || businessTasks.length;
    const consumerWarning = service.consumer_warning;
    const businessError = service.business_tasks_error;
    const inspected = !service.inspect_error;
    const unknown = service.inspect_error === 'skipped' ? '未检查' : '未知';

    return `
        <div class="mt-3 rounded-md border border-slate-100 bg-slate-50 px-3 py-2 text-xs">
            <div class="grid grid-cols-4 gap-2">
                ${renderTaskCount('执行中', inspected ? active.length : unknown, inspected ? 'text-emerald-700' : 'text-slate-500')}
                ${renderTaskCount('已领取', inspected ? reserved.length : unknown, inspected ? 'text-blue-700' : 'text-slate-500')}
                ${renderTaskCount('定时', inspected ? scheduled.length : unknown, 'text-slate-500')}
                ${renderTaskCount('业务', businessError ? '未知' : businessTasks.length, businessTasks.length ? 'text-indigo-700' : 'text-slate-700')}
            </div>
            ${consumerWarning ? `<div class="mt-2 rounded border border-amber-200 bg-amber-50 px-2 py-1.5 font-bold leading-5 text-amber-700">${escapeHtml(consumerWarning)}</div>` : ''}
            ${businessError ? `<div class="mt-2 rounded border border-amber-200 bg-amber-50 px-2 py-1.5 font-bold leading-5 text-amber-700">业务任务读取失败：${escapeHtml(businessError)}</div>` : ''}
            ${service.consumer_note ? `<p class="mt-2 leading-5 text-slate-500">${escapeHtml(service.consumer_note)}</p>` : ''}
            ${service.inspect_error && service.inspect_error !== 'skipped' ? `<p class="mt-2 text-amber-700">实时任务检查未完成：${escapeHtml(service.inspect_error)}</p>` : ''}
            ${hasTasks ? `
                <details class="monitor-details mt-2" data-detail-id="tasks-${escapeHtml(service.id)}" ${state.expandedDetails.has(`tasks-${service.id}`) ? 'open' : ''}>
                    <summary>查看任务详情</summary>
                    ${renderTaskRows('执行中', active, 'border-emerald-200 bg-emerald-50')}
                    ${renderTaskRows('已领取', reserved, 'border-blue-200 bg-blue-50')}
                    ${renderTaskRows('定时', scheduled, 'border-slate-200 bg-white')}
                    ${renderBusinessTaskRows('业务进行中', businessTasks, 'border-indigo-200 bg-indigo-50')}
                </details>
            ` : inspected ? '<div class="mt-2 text-slate-500">当前没有已领取或进行中的任务。</div>' : ''}
        </div>
    `;
}

function renderTaskCount(label, count, colorClass) {
    return `
        <div>
            <div class="text-slate-400">${label}</div>
            <div class="mt-0.5 font-black ${colorClass}">${count}</div>
        </div>
    `;
}

function renderTaskRows(label, tasks, className) {
    if (!tasks.length) return '';
    return tasks.map(task => `
        <div class="rounded border ${className} px-2 py-1">
            <div class="flex items-center justify-between gap-2">
                <span class="shrink-0 font-black text-slate-700">${label}</span>
                <span class="truncate font-mono text-[10px] text-slate-500">${escapeHtml(task.short_id || task.id || '-')}</span>
            </div>
            <div class="mt-0.5 truncate font-bold text-slate-700" title="${escapeHtml(task.name || '')}">
                ${escapeHtml(shortTaskName(task.name))}
            </div>
            ${taskPayloadText(task) ? `<div class="mt-0.5 truncate text-slate-500" title="${escapeHtml(taskPayloadText(task))}">${escapeHtml(taskPayloadText(task))}</div>` : ''}
            ${taskRuntimeText(task) ? `<div class="mt-0.5 text-slate-400">${escapeHtml(taskRuntimeText(task))}</div>` : ''}
        </div>
    `).join('');
}

function renderBusinessTaskRows(label, tasks, className) {
    if (!tasks.length) return '';
    return tasks.map(task => `
        <div class="rounded border ${className} px-2 py-1">
            <div class="flex items-center justify-between gap-2">
                <span class="shrink-0 font-black text-indigo-700">${label}</span>
                <span class="truncate font-mono text-[10px] text-slate-500">${escapeHtml(task.short_id || task.id || '-')}</span>
            </div>
            <div class="mt-0.5 truncate font-bold text-slate-800" title="${escapeHtml(task.title || '')}">
                ${escapeHtml(task.title || businessFeatureLabel(task.feature))}
            </div>
            <div class="mt-0.5 truncate text-slate-500" title="${escapeHtml(businessTaskDetail(task))}">
                ${escapeHtml(businessTaskDetail(task))}
            </div>
            ${task.parent_task_id ? `<div class="mt-0.5 truncate text-[10px] text-slate-400" title="${escapeHtml(task.parent_task_id)}">父任务 ${escapeHtml(task.parent_feature || '-')}: ${escapeHtml(String(task.parent_task_id).slice(0, 8))}</div>` : ''}
        </div>
    `).join('');
}

function businessTaskDetail(task) {
    const pieces = [
        businessStatusLabel(task.status),
        task.step || '-',
        `${Number(task.progress || 0)}%`,
        task.updated_at ? `更新 ${formatShortTime(task.updated_at)}` : '',
    ];
    return pieces.filter(Boolean).join(' · ');
}

function businessStatusLabel(status) {
    return {
        pending: '排队中',
        processing: '处理中',
        completed: '已完成',
        failed: '失败',
        canceled: '已取消',
    }[status] || status || '未知';
}

function businessFeatureLabel(feature) {
    return {
        publisher: '视频合成任务',
        report_audio_workflow: '研报转语音编排',
        report_script_material_workflow: '研报基础文案物料',
        mp3_video_workflow: 'MP3 视频编排',
        content_pdf_clean: 'PDF 清洗任务',
        content_audio_clean: '音频清洗任务',
        zsxq_clean_output_publish: '知识星球发布任务',
        zsxq_sync_run: '知识星球同步',
        zsxq_workflow_run: '知识星球编排',
    }[feature] || feature || '业务任务';
}

function formatShortTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function shortTaskName(name) {
    return String(name || '-').replace(/^tasks\./, '').replace(/^src\.backend\./, '');
}

function taskPayloadText(task) {
    const args = compactValue(task.args);
    const kwargs = compactValue(task.kwargs);
    return truncateText([args, kwargs].filter(Boolean).join(' · '), 96);
}

function compactValue(value) {
    if (value === null || value === undefined || value === '' || value === '[]' || value === '{}') return '';
    if (Array.isArray(value)) {
        return value.slice(0, 3).map(item => compactValue(item)).filter(Boolean).join(', ');
    }
    if (typeof value === 'object') {
        return truncateText(JSON.stringify(value), 96);
    }
    return String(value).replace(/^\(|\)$/g, '').replace(/^'|'$/g, '');
}

function truncateText(text, maxLength = 80) {
    const value = String(text || '');
    return value.length > maxLength ? `${value.slice(0, maxLength - 1)}…` : value;
}

function taskRuntimeText(task) {
    if (!task.time_start) return '';
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - Number(task.time_start)));
    if (!Number.isFinite(seconds)) return '';
    if (seconds < 60) return `已运行 ${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    return `已运行 ${minutes}m ${rest}s`;
}

function renderCards() {
    els.serviceGrid.dataset.count = String(state.services.length);
    const services = state.services.filter(service => {
        const matchesSearch = [service.label, service.queue, service.id, service.description]
            .some(value => String(value || '').toLowerCase().includes(state.search));
        const matchesFilter = state.filter === 'all'
            || (state.filter === 'attention' && needsAttention(service))
            || (state.filter === 'backlog' && Number(service.queue_length) > 0)
            || (state.filter === 'paused' && service.paused)
            || (state.filter === 'offline' && !service.running);
        return matchesSearch && matchesFilter;
    });
    els.filterCount.textContent = `${services.length} / ${state.services.length} 个 Worker`;
    els.serviceFilters.querySelectorAll('[data-filter]').forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.filter === state.filter));
    });
    if (!services.length) {
        els.serviceGrid.innerHTML = `<div class="monitor-empty">${!state.lastStatusAt
            ? (state.statusError ? '无法获取 Worker 状态，请点击顶部刷新重试。' : '正在获取 Worker 状态…')
            : state.services.length ? '没有符合条件的 Worker。' : '当前没有可用的 Worker。'}
            ${state.services.length ? '<button data-clear-filters class="monitor-text-button block mx-auto mt-2">清除筛选</button>' : ''}</div>`;
        return;
    }
    const workflowServices = WORKFLOW_SERVICE_ORDER
        .map(id => services.find(service => service.id === id))
        .filter(Boolean);
    const zsxqSupportServices = ZSXQ_SUPPORT_SERVICE_ORDER
        .map(id => services.find(service => service.id === id))
        .filter(Boolean);
    const groupedIds = new Set([...WORKFLOW_SERVICE_IDS, ...ZSXQ_SUPPORT_SERVICE_IDS]);
    const otherServices = services.filter(service => !groupedIds.has(service.id));
    const groups = [
        workflowServices.length
            ? renderServiceGroup({
                title: '编排任务',
                subtitle: '业务编排横向集中查看，便于对比积压、状态和日志。',
                services: workflowServices,
                layout: 'workflow-row',
            })
            : '',
        zsxqSupportServices.length
            ? renderServiceGroup({
                title: '知识星球支撑队列',
                subtitle: '同步、清洗、发布等编排下游队列。',
                services: zsxqSupportServices,
            })
            : '',
        otherServices.length
            ? renderServiceGroup({
                title: '其他 Worker',
                subtitle: '视频、TTS、播客和研报工厂队列。',
                services: otherServices,
            })
            : '',
    ];
    updatePanel(els.serviceGrid, groups.filter(Boolean).join(''));
}

function renderServiceGroup({ title, subtitle, services, layout = 'grid' }) {
    const listClass = layout === 'workflow-row' ? 'celery-workflow-row' : 'celery-service-grid';
    return `
        <section>
            <div class="mb-3 flex items-end justify-between gap-3">
                <div>
                    <h2 class="text-sm font-black text-slate-700">${escapeHtml(title)}</h2>
                    <p class="mt-1 text-xs text-slate-500">${escapeHtml(subtitle)}</p>
                </div>
                <span class="rounded-full bg-white px-2.5 py-1 text-xs font-black text-slate-500">${services.length}</span>
            </div>
            <div class="${listClass}">
                ${services.map(renderServiceCard).join('')}
            </div>
        </section>
    `;
}

function renderServiceCard(service) {
    const running = Boolean(service.running);
    const busy = isActionBusy() || Boolean(state.statusError);
    const businessActive = Number(service.business_active_count || 0);
    const statusClass = service.paused
        ? 'border-amber-200 bg-amber-50 text-amber-700'
        : running
        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
        : 'border-slate-200 bg-slate-50 text-slate-500';
    const risk = !running && Number(service.queue_length || 0) > 0;
    return `
        <article data-attention="${needsAttention(service)}" data-selected="${state.selectedServiceId === service.id}" class="celery-service-card flex h-full flex-col rounded-lg border border-slate-200 bg-white p-4">
            <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                    <h3 class="text-sm font-bold" title="${escapeHtml(service.label)}">${escapeHtml(service.label)}</h3>
                </div>
                <span class="shrink-0 rounded-full border px-2.5 py-1 text-xs font-black ${statusClass}">
                    ${service.paused ? (running ? '已暂停 · 在线' : '已暂停') : running ? '在线' : '离线'}
                </span>
            </div>
            <code class="monitor-queue" title="${escapeHtml(service.queue)}">${escapeHtml(service.queue)}</code>
            <p class="celery-service-desc mt-2 text-xs leading-4 text-slate-500" title="${escapeHtml(service.description || '')}">${escapeHtml(service.description || '')}</p>
            <div class="mt-3 grid grid-cols-2 gap-2 text-xs">
                <div class="rounded-md bg-slate-50 px-3 py-2">
                    <div class="text-slate-400">积压</div>
                    <div class="mt-1 text-lg font-bold ${risk ? 'text-red-600' : 'text-slate-700'}">${service.queue_error ? '未知' : service.queue_length ?? '—'}</div>
                </div>
                <div class="rounded-md bg-slate-50 px-3 py-2">
                    <div class="text-slate-400">业务进行中</div>
                    <div class="mt-1 text-lg font-bold ${businessActive ? 'text-indigo-700' : 'text-slate-700'}">${service.business_tasks_error ? '未知' : businessActive}</div>
                </div>
            </div>
            <div class="mt-3 text-xs text-slate-500">
                <details class="monitor-details" data-detail-id="process-${escapeHtml(service.id)}" ${state.expandedDetails.has(`process-${service.id}`) ? 'open' : ''}>
                    <summary>进程信息${service.pids?.length ? ` · ${service.pids.length} 个` : ''}</summary>
                    <div>PID：${service.pids?.length ? service.pids.map(escapeHtml).join(', ') : '—'}</div>
                    <div class="mt-1">运行时长：${service.processes?.length ? escapeHtml(service.processes[0].etime) : '—'}</div>
                </details>
                ${risk ? '<div class="mt-2 rounded-md bg-red-50 px-2 py-1 font-bold text-red-600">队列有积压，但 worker 离线</div>' : ''}
                ${service.queue_error ? `<div class="mt-2 rounded-md bg-amber-50 px-2 py-1 font-bold text-amber-700">队列读取失败：${escapeHtml(service.queue_error)}</div>` : ''}
            </div>
            ${renderTaskPanel(service)}
            ${renderReportVideoBusinessPanel(service)}
            <div class="mt-auto grid grid-cols-2 gap-2 pt-4">
                <button data-view-logs="${escapeHtml(service.id)}" class="col-span-2 rounded-md border border-blue-200 bg-blue-50 px-2 py-2 text-xs font-bold text-blue-700 hover:bg-blue-100"><i class="fas fa-file-lines mr-1" aria-hidden="true"></i>查看日志</button>
                <button data-action="start" data-service-id="${escapeHtml(service.id)}" ${busy || (running && !service.paused) ? 'disabled' : ''} class="rounded-md bg-emerald-600 px-2 py-2 text-xs font-bold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-40">
                    <i class="fas fa-play mr-1" aria-hidden="true"></i>${service.paused ? '恢复运行' : '启动'}
                </button>
                <button data-action="stop" data-service-id="${escapeHtml(service.id)}" ${busy || !running ? 'disabled' : ''} class="rounded-md bg-slate-800 px-2 py-2 text-xs font-bold text-white hover:bg-slate-900 disabled:cursor-not-allowed disabled:opacity-40">
                    <i class="fas fa-stop mr-1"></i>停止
                </button>
                <button data-action="restart" data-service-id="${escapeHtml(service.id)}" ${busy ? 'disabled' : ''} class="rounded-md border border-slate-300 px-2 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40">
                    <i class="fas fa-rotate mr-1"></i>重启
                </button>
                <button data-action="purge" data-service-id="${escapeHtml(service.id)}" ${busy || Number(service.queue_length || 0) <= 0 ? 'disabled' : ''} class="rounded-md border border-red-200 px-2 py-2 text-xs font-bold text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40">
                    <i class="fas fa-trash-can mr-1"></i>清空积压
                </button>
            </div>
        </article>
    `;
}

function renderReportVideoBusinessPanel(service) {
    if (service.id !== 'report_video') return '';
    if (service.report_video_stats_error) {
        return `<div class="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs font-bold text-amber-700">业务统计读取失败：${escapeHtml(service.report_video_stats_error)}</div>`;
    }
    const stats = service.report_video_stats;
    if (!stats) return '';

    const parse = stats.parse_status || {};
    const ai = stats.ai_status || {};
    const task = stats.task_status || {};
    const totals = stats.totals || {};

    return `
        <div class="mt-3 rounded-md border border-indigo-100 bg-indigo-50/60 px-2.5 py-2 text-xs">
            <div class="flex items-center justify-between gap-2">
                <div class="font-black text-slate-700">研报工厂阶段</div>
                <div class="text-[11px] text-slate-500">任务 ${totals.tasks ?? 0} · 研报 ${totals.reports ?? 0}</div>
            </div>
            <div class="mt-2 grid grid-cols-3 gap-1.5">
                ${renderBusinessStat('待解析', parse.uploaded || 0)}
                ${renderBusinessStat('解析中', parse.parsing || 0, 'text-amber-700')}
                ${renderBusinessStat('已解析', parse.parsed || 0, 'text-emerald-700')}
                ${renderBusinessStat('解析失败', parse.parse_failed || 0, 'text-red-600')}
                ${renderBusinessStat('AI待处理', ai.ai_pending || 0)}
                ${renderBusinessStat('AI生成中', ai.ai_generating || 0, 'text-blue-700')}
                ${renderBusinessStat('AI阻塞', ai.ai_blocked || 0, 'text-red-600')}
                ${renderBusinessStat('脚本就绪', task.script_ready || 0, 'text-emerald-700')}
                ${renderBusinessStat('视频生成中', task.video_generating || 0, 'text-blue-700')}
            </div>
        </div>
    `;
}

function renderBusinessStat(label, count, valueClass = 'text-slate-700') {
    return `
        <div class="rounded-md border border-white/80 bg-white/80 px-2 py-1.5">
            <div class="truncate whitespace-nowrap text-[10px] text-slate-400">${escapeHtml(label)}</div>
            <div class="mt-0.5 text-sm font-black ${valueClass}">${Number(count || 0)}</div>
        </div>
    `;
}

function renderServiceList() {
    updatePanel(els.serviceList, state.services.map(service => {
        const selected = service.id === state.selectedServiceId;
        const running = Boolean(service.running);
        const active = Number(service.active_count || 0);
        const reserved = Number(service.reserved_count || 0);
        const scheduled = Number(service.scheduled_count || 0);
        const businessActive = Number(service.business_active_count || 0);
        return `
            <button data-select-service="${escapeHtml(service.id)}" aria-pressed="${selected}" class="w-full rounded-lg border px-3 py-3 text-left transition-colors ${selected ? 'border-blue-300 bg-blue-50' : 'border-slate-200 hover:bg-slate-50'}">
                <div class="flex items-center justify-between gap-2">
                    <span class="text-sm font-black">${escapeHtml(service.label)}</span>
                    <span class="shrink-0 text-xs ${service.paused ? 'text-amber-700' : running ? 'text-emerald-700' : 'text-slate-500'}">${service.paused ? '已暂停' : running ? '在线' : '离线'}</span>
                </div>
                <div class="mt-1 truncate text-xs text-slate-500" title="${escapeHtml(service.queue)}">${escapeHtml(service.queue)}</div>
                <div class="mt-1 text-xs text-slate-500">积压 ${service.queue_error ? '未知' : service.queue_length ?? '—'} · 业务 ${service.business_tasks_error ? '未知' : businessActive}${service.inspect_error ? '' : ` · 执行 ${active} · 已领取 ${reserved} · 定时 ${scheduled}`}</div>
            </button>
        `;
    }).join(''));
}

function serviceLabel(serviceId) {
    return state.services.find(service => service.id === serviceId)?.label || serviceId;
}

function setNotice(message, type) {
    clearTimeout(state.noticeTimer);
    state.noticeTimer = null;
    const classes = {
        muted: 'border-slate-200 bg-white text-slate-600',
        success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
        error: 'border-red-200 bg-red-50 text-red-600',
    };
    els.notice.className = `mb-4 rounded-lg border px-4 py-3 text-sm ${classes[type] || classes.muted}`;
    els.notice.textContent = message;
    els.notice.classList.remove('hidden');
    if (type === 'success') {
        state.noticeTimer = setTimeout(() => els.notice.classList.add('hidden'), 4000);
    }
}

function formatDuration(seconds) {
    const value = Number(seconds || 0);
    if (!Number.isFinite(value) || value <= 0) return '-';
    if (value < 60) return `${Math.round(value)}s`;
    const minutes = Math.floor(value / 60);
    const rest = Math.round(value % 60);
    return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

function formatTime(value) {
    if (!value) return '-';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleTimeString('zh-CN', { hour12: false });
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
