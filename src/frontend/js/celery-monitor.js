const state = {
    services: [],
    selectedServiceId: 'render',
    busyServiceId: null,
    refreshTimer: null,
};

const els = {
    brokerStatus: document.getElementById('broker-status'),
    refreshBtn: document.getElementById('refresh-btn'),
    notice: document.getElementById('notice'),
    serviceGrid: document.getElementById('service-grid'),
    serviceList: document.getElementById('service-list'),
    lastUpdated: document.getElementById('last-updated'),
    logTitle: document.getElementById('log-title'),
    logSubtitle: document.getElementById('log-subtitle'),
    logOutput: document.getElementById('log-output'),
    refreshLogBtn: document.getElementById('refresh-log-btn'),
};

document.addEventListener('DOMContentLoaded', () => {
    bindEvents();
    refreshStatus({ loadLogs: true });
    state.refreshTimer = setInterval(() => refreshStatus({ silent: true }), 5000);
});

function bindEvents() {
    els.refreshBtn.addEventListener('click', () => refreshStatus({ loadLogs: true }));
    els.refreshLogBtn.addEventListener('click', () => loadLogs(state.selectedServiceId));
    els.serviceGrid.addEventListener('click', handleServiceAction);
    els.serviceList.addEventListener('click', (event) => {
        const button = event.target.closest('[data-select-service]');
        if (!button) return;
        state.selectedServiceId = button.dataset.selectService;
        render();
        loadLogs(state.selectedServiceId);
    });
}

async function handleServiceAction(event) {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const serviceId = button.dataset.serviceId;
    const action = button.dataset.action;
    if (action === 'purge' && !confirmPurge(serviceId)) {
        return;
    }
    await runAction(serviceId, action);
}

async function apiFetch(path, options = {}) {
    const response = await fetch(`${apiBaseUrl()}${path}`, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.success) {
        const error = new Error(data.message || data?.error?.message || `HTTP ${response.status}`);
        error.status = response.status;
        throw error;
    }
    return data.data;
}

function apiBaseUrl() {
    return window.API_BASE_URL || window.CONFIG?.API_BASE_URL || 'http://localhost:5001/api/v1';
}

async function refreshStatus(options = {}) {
    try {
        if (!options.silent) setNotice('正在刷新 Celery 状态...', 'muted');
        const data = await apiFetch('/celery/status');
        state.services = data.services || [];
        if (!state.services.some(service => service.id === state.selectedServiceId)) {
            state.selectedServiceId = state.services[0]?.id || 'render';
        }
        render(data.broker || {});
        if (options.loadLogs) {
            await loadLogs(state.selectedServiceId);
        }
        if (!options.silent) setNotice('状态已刷新。', 'success');
    } catch (error) {
        setNotice(`刷新失败：${error.message}`, 'error');
    }
}

async function runAction(serviceId, action) {
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
    try {
        const data = await apiFetch(`/celery/logs/${serviceId}?lines=160`);
        const service = state.services.find(item => item.id === serviceId);
        els.logTitle.textContent = `${service?.label || serviceId} 日志`;
        els.logSubtitle.textContent = data.log_file || '';
        els.logOutput.textContent = data.logs || '暂无日志。';
        els.logOutput.scrollTop = els.logOutput.scrollHeight;
    } catch (error) {
        els.logOutput.textContent = `日志读取失败：${error.message}`;
    }
}

function render(broker = null) {
    if (broker) renderBroker(broker);
    renderCards();
    renderServiceList();
    els.lastUpdated.textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

function renderBroker(broker) {
    const ok = Boolean(broker.ok);
    els.brokerStatus.className = ok
        ? 'rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-700'
        : 'rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-bold text-red-600';
    els.brokerStatus.innerHTML = ok
        ? `<i class="fas fa-circle-check mr-2"></i>Broker 正常 · ${escapeHtml(broker.url_masked || '')}`
        : `<i class="fas fa-triangle-exclamation mr-2"></i>Broker 异常`;
    els.brokerStatus.title = broker.error || '';
}

function renderTaskPanel(service) {
    const tasks = service.tasks || {};
    const active = tasks.active || [];
    const reserved = tasks.reserved || [];
    const scheduled = tasks.scheduled || [];
    const hasTasks = active.length || reserved.length || scheduled.length;
    const inspectError = service.inspect_error;
    const consumerWarning = service.consumer_warning;

    return `
        <div class="mt-3 rounded-md border border-slate-100 bg-slate-50 px-3 py-2 text-xs">
            <div class="grid grid-cols-3 gap-2">
                ${renderTaskCount('执行中', active.length, 'text-emerald-700')}
                ${renderTaskCount('已领取', reserved.length, 'text-blue-700')}
                ${renderTaskCount('定时', scheduled.length, 'text-slate-700')}
            </div>
            ${inspectError ? `<div class="mt-2 rounded bg-amber-50 px-2 py-1 font-bold text-amber-700">任务检查失败：${escapeHtml(inspectError)}</div>` : ''}
            ${consumerWarning ? `<div class="mt-2 rounded border border-amber-200 bg-amber-50 px-2 py-1.5 font-bold leading-5 text-amber-700">${escapeHtml(consumerWarning)}</div>` : ''}
            ${hasTasks ? `
                <div class="mt-2 space-y-1">
                    ${renderTaskRows('执行中', active, 'border-emerald-200 bg-emerald-50')}
                    ${renderTaskRows('已领取', reserved, 'border-blue-200 bg-blue-50')}
                    ${renderTaskRows('定时', scheduled, 'border-slate-200 bg-white')}
                </div>
            ` : '<div class="mt-2 text-slate-400">当前没有 worker 已领取的任务。</div>'}
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
    els.serviceGrid.innerHTML = state.services.map(service => {
        const running = Boolean(service.running);
        const busy = state.busyServiceId === service.id;
        const statusClass = running
            ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
            : 'border-slate-200 bg-slate-50 text-slate-500';
        const risk = !running && Number(service.queue_length || 0) > 0;
        return `
            <article class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                        <h2 class="truncate text-base font-black">${escapeHtml(service.label)}</h2>
                        <p class="mt-1 text-xs text-slate-500">${escapeHtml(service.description || '')}</p>
                    </div>
                    <span class="shrink-0 rounded-full border px-2.5 py-1 text-xs font-black ${statusClass}">
                        ${running ? '在线' : '离线'}
                    </span>
                </div>
                <div class="mt-4 grid grid-cols-2 gap-2 text-xs">
                    <div class="rounded-md bg-slate-50 px-3 py-2">
                        <div class="text-slate-400">队列</div>
                        <div class="mt-1 font-black text-slate-700">${escapeHtml(service.queue)}</div>
                    </div>
                    <div class="rounded-md bg-slate-50 px-3 py-2">
                        <div class="text-slate-400">积压</div>
                        <div class="mt-1 font-black ${risk ? 'text-red-600' : 'text-slate-700'}">${service.queue_length ?? '-'}</div>
                    </div>
                </div>
                <div class="mt-3 text-xs text-slate-500">
                    <div>PID：${service.pids?.length ? service.pids.map(escapeHtml).join(', ') : '-'}</div>
                    <div class="mt-1">运行时长：${service.processes?.length ? escapeHtml(service.processes[0].etime) : '-'}</div>
                    ${risk ? '<div class="mt-2 rounded-md bg-red-50 px-2 py-1 font-bold text-red-600">队列有积压，但 worker 离线</div>' : ''}
                    ${service.queue_error ? `<div class="mt-2 rounded-md bg-amber-50 px-2 py-1 font-bold text-amber-700">队列读取失败：${escapeHtml(service.queue_error)}</div>` : ''}
                </div>
                ${renderTaskPanel(service)}
                ${renderReportVideoBusinessPanel(service)}
                <div class="mt-4 grid grid-cols-2 gap-2">
                    <button data-action="start" data-service-id="${escapeHtml(service.id)}" ${busy || running ? 'disabled' : ''} class="rounded-md bg-emerald-600 px-2 py-2 text-xs font-bold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-40">
                        <i class="fas fa-play mr-1"></i>启动
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
    }).join('');
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
        <div class="mt-3 rounded-md border border-indigo-100 bg-indigo-50/60 px-3 py-3 text-xs">
            <div class="flex items-center justify-between gap-2">
                <div class="font-black text-slate-700">研报工厂阶段</div>
                <div class="text-[11px] text-slate-500">任务 ${totals.tasks ?? 0} · 研报 ${totals.reports ?? 0}</div>
            </div>
            <div class="mt-3 grid grid-cols-3 gap-2">
                ${renderBusinessStat('待解析', parse.uploaded || 0)}
                ${renderBusinessStat('解析中', parse.parsing || 0, 'text-amber-700')}
                ${renderBusinessStat('已解析', parse.parsed || 0, 'text-emerald-700')}
                ${renderBusinessStat('解析失败', parse.parse_failed || 0, 'text-red-600')}
                ${renderBusinessStat('AI 待处理', ai.ai_pending || 0)}
                ${renderBusinessStat('AI 生成中', ai.ai_generating || 0, 'text-blue-700')}
                ${renderBusinessStat('AI 阻塞', ai.ai_blocked || 0, 'text-red-600')}
                ${renderBusinessStat('脚本就绪', task.script_ready || 0, 'text-emerald-700')}
                ${renderBusinessStat('视频生成中', task.video_generating || 0, 'text-blue-700')}
            </div>
        </div>
    `;
}

function renderBusinessStat(label, count, valueClass = 'text-slate-700') {
    return `
        <div class="rounded-md border border-white/80 bg-white/80 px-2 py-2">
            <div class="text-[11px] text-slate-400">${escapeHtml(label)}</div>
            <div class="mt-1 text-sm font-black ${valueClass}">${Number(count || 0)}</div>
        </div>
    `;
}

function renderServiceList() {
    els.serviceList.innerHTML = state.services.map(service => {
        const selected = service.id === state.selectedServiceId;
        const running = Boolean(service.running);
        const active = Number(service.active_count || 0);
        const reserved = Number(service.reserved_count || 0);
        const scheduled = Number(service.scheduled_count || 0);
        return `
            <button data-select-service="${escapeHtml(service.id)}" class="w-full rounded-lg border px-3 py-3 text-left transition-colors ${selected ? 'border-blue-300 bg-blue-50' : 'border-slate-200 hover:bg-slate-50'}">
                <div class="flex items-center justify-between gap-2">
                    <span class="text-sm font-black">${escapeHtml(service.label)}</span>
                    <span class="h-2.5 w-2.5 rounded-full ${running ? 'bg-emerald-500' : 'bg-slate-300'}"></span>
                </div>
                <div class="mt-1 text-xs text-slate-500">队列 ${escapeHtml(service.queue)} · 积压 ${service.queue_length ?? '-'}</div>
                <div class="mt-1 text-xs ${active ? 'font-bold text-emerald-700' : 'text-slate-400'}">执行 ${active} · 已领取 ${reserved} · 定时 ${scheduled}</div>
            </button>
        `;
    }).join('');
}

function serviceLabel(serviceId) {
    return state.services.find(service => service.id === serviceId)?.label || serviceId;
}

function setNotice(message, type) {
    const classes = {
        muted: 'border-slate-200 bg-white text-slate-600',
        success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
        error: 'border-red-200 bg-red-50 text-red-600',
    };
    els.notice.className = `mb-4 rounded-lg border px-4 py-3 text-sm ${classes[type] || classes.muted}`;
    els.notice.textContent = message;
    els.notice.classList.remove('hidden');
    if (type === 'success') {
        setTimeout(() => els.notice.classList.add('hidden'), 1800);
    }
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
