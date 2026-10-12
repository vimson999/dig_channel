const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness() {
    const elements = new Map();
    const timers = new Map();
    let nextTimer = 0;
    function element(id) {
        if (!elements.has(id)) {
            const classes = new Set();
            elements.set(id, {
                textContent: '', innerHTML: '', className: '', dataset: {}, value: '', checked: true,
                disabled: false, hidden: false, scrollTop: 0, scrollHeight: 100, attributes: {},
                classList: { add: c => classes.add(c), remove: c => classes.delete(c),
                    contains: c => classes.has(c), toggle: (c, yes) => yes ? classes.add(c) : classes.delete(c) },
                setAttribute(name, value) { this.attributes[name] = String(value); },
                addEventListener() {}, querySelectorAll: () => [], focus() {}, scrollIntoView() {},
            });
        }
        return elements.get(id);
    }
    const context = vm.createContext({ console, Date, Intl, URLSearchParams, AbortController,
        setTimeout: callback => { timers.set(++nextTimer, callback); return nextTimer; },
        clearTimeout: id => timers.delete(id),
        document: { hidden: false, getElementById: element, addEventListener() {}, querySelectorAll: () => [] },
        fetch: async () => { throw new Error('Unexpected request'); },
    });
    context.window = context;
    context.confirm = () => true;
    context.addEventListener = () => {};
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../src/frontend/js/celery-monitor.js'), 'utf8') +
        '\nglobalThis.monitorState = state;', context);
    return { context, state: context.monitorState, element, timers };
}

function service(overrides = {}) {
    return { id: 'render', label: '主视频合成', queue: 'render', running: true, paused: false,
        queue_length: 0, tasks: { active: [], reserved: [], scheduled: [] },
        active_count: 0, reserved_count: 0, scheduled_count: 0, business_active_count: 0,
        business_tasks: [], pids: [123], processes: [{ etime: '00:30' }], inspect_error: 'skipped', ...overrides };
}

const response = data => ({ ok: true, json: async () => ({ success: true, data }) });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

test('a skipped inspect leaves the healthy broker healthy', () => {
    const { context, element } = harness();
    context.renderBroker({ ok: true, inspect_error: 'skipped', url_masked: 'localhost:6379/0' });
    assert.doesNotMatch(element('broker-status').innerHTML, /超时|异常/);
    assert.match(element('broker-status').className, /emerald/);
});

test('uninspected tasks are unknown rather than reported idle', () => {
    const { context } = harness();
    const html = context.renderTaskPanel(service());
    assert.match(html, /未检查/);
    assert.doesNotMatch(html, /当前没有 worker/);
    const checked = context.renderTaskPanel(service({ inspect_error: null }));
    assert.doesNotMatch(checked, /未检查/);
});

test('rendering after a local selection does not change the last successful refresh time', async () => {
    const { context, state, element } = harness();
    context.fetch = async url => response(url.includes('/watchdog/') ? { watchdog: { running: true } } :
        { services: [service()], broker: { ok: true, inspect_error: 'skipped' } });
    await context.refreshStatus({ silent: true });
    const refreshed = element('last-updated').textContent;
    vm.runInContext('Date = class extends Date { constructor(...args) { super(...(args.length ? args : [0])); } };', context);
    state.selectedServiceId = 'render';
    context.render();
    assert.equal(element('last-updated').textContent, refreshed);
});

test('a late log response cannot overwrite the newly selected worker', async () => {
    const { context, state, element } = harness();
    state.services = [service(), service({ id: 'cloud_tts', label: '豆包 TTS' })];
    const old = deferred();
    context.fetch = url => url.includes('/render?') ? old.promise : Promise.resolve(response({ logs: 'new log', log_file: 'tts.log' }));
    const first = context.loadLogs('render');
    state.selectedServiceId = 'cloud_tts';
    await context.loadLogs('cloud_tts');
    old.resolve(response({ logs: 'old log', log_file: 'render.log' }));
    await first;
    assert.equal(element('log-output').textContent, 'new log');
    assert.match(element('log-title').textContent, /豆包 TTS/);
});

test('simultaneous manual and automatic refreshes share one status request', async () => {
    const { context } = harness();
    const pending = deferred();
    let statusRequests = 0;
    context.fetch = url => {
        if (url.includes('/watchdog/')) return Promise.resolve(response({ watchdog: {} }));
        statusRequests++;
        return pending.promise;
    };
    const first = context.refreshStatus({ silent: true });
    const second = context.refreshStatus({ silent: true });
    pending.resolve(response({ services: [service()], broker: { ok: true } }));
    await Promise.all([first, second]);
    assert.equal(statusRequests, 1);
});

test('an old success timer cannot hide a newer error message', () => {
    const { context, element, timers } = harness();
    context.setNotice('done', 'success');
    context.setNotice('failed', 'error');
    for (const callback of timers.values()) callback();
    assert.equal(element('notice').textContent, 'failed');
    assert.equal(element('notice').classList.contains('hidden'), false);
});

test('a worker action prevents a conflicting restart-all request', async () => {
    const { context, state } = harness();
    state.busyServiceId = 'render';
    let requests = 0;
    context.fetch = async () => { requests++; return response({}); };
    await context.restartAll();
    assert.equal(requests, 0);
});

test('status failure keeps the last snapshot and marks it stale, then clears the error after recovery', async () => {
    const { context, state, element } = harness();
    let failing = false;
    context.fetch = async url => {
        if (url.includes('/watchdog/')) return response({ watchdog: {} });
        if (failing) throw new Error('offline');
        return response({ services: [service()], broker: { ok: true, inspect_error: 'skipped' } });
    };
    await context.refreshStatus({ silent: true });
    const refreshed = state.lastStatusAt;
    failing = true;
    await context.refreshStatus({ silent: true });
    assert.equal(state.services.length, 1);
    assert.equal(state.lastStatusAt, refreshed);
    assert.equal(element('last-updated').dataset.stale, 'true');
    assert.equal(element('restart-all-btn').disabled, true);
    assert.match(element('status-summary').innerHTML, /数据已过期/);
    failing = false;
    await context.refreshStatus({ silent: true });
    assert.equal(element('last-updated').dataset.stale, 'false');
    assert.doesNotMatch(element('notice').textContent, /失败/);
});

test('search and attention filters combine without flagging an intentionally paused empty worker', () => {
    const { context, state, element } = harness();
    state.lastStatusAt = new Date();
    state.services = [service(), service({ id: 'paused', label: '暂停渲染', running: false, paused: true }),
        service({ id: 'report', label: '研报解析', queue: 'report_pdf_gemini', running: false, queue_length: 3 })];
    state.filter = 'attention';
    context.renderCards();
    assert.match(element('service-grid').innerHTML, /研报解析/);
    assert.doesNotMatch(element('service-grid').innerHTML, /暂停渲染|主视频合成/);
    state.search = 'gemini';
    context.renderCards();
    assert.equal(element('filter-count').textContent, '1 / 3 个 Worker');
    state.search = 'absent';
    context.renderCards();
    assert.match(element('service-grid').innerHTML, /data-clear-filters/);
    assert.equal(element('filter-count').textContent, '0 / 3 个 Worker');
});

test('paused queues with backlog need attention and expose resume', () => {
    const { context } = harness();
    const paused = service({ paused: true, running: false, queue_length: 2 });
    assert.equal(context.needsAttention(paused), true);
    const html = context.renderServiceCard(paused);
    assert.match(html, /已暂停/);
    assert.match(html, /恢复运行/);
    assert.doesNotMatch(html.match(/<button data-action="start"[^>]*>/)[0], /\sdisabled(?:\s|=|>)/);
});

test('a failed log refresh preserves the current log and exposes a retryable error', async () => {
    const { context, element } = harness();
    context.fetch = async () => response({ logs: 'useful log', log_file: 'render.log' });
    await context.loadLogs('render');
    context.fetch = async () => { throw new Error('timeout'); };
    await context.loadLogs('render');
    assert.equal(element('log-output').textContent, 'useful log');
    assert.match(element('log-subtitle').textContent, /失败.*timeout/);
});

test('older requests for the same log cannot replace newer log content', async () => {
    const { context, element } = harness();
    const old = deferred();
    let calls = 0;
    context.fetch = async () => ++calls === 1 ? old.promise : response({ logs: 'latest' });
    const first = context.loadLogs('render');
    await context.loadLogs('render');
    old.resolve(response({ logs: 'outdated' }));
    await first;
    assert.equal(element('log-output').textContent, 'latest');
});

test('queue and business read errors do not become zero counts', () => {
    const { context, state, element } = harness();
    state.lastStatusAt = new Date();
    state.services = [service({ queue_length: null, queue_error: 'connection lost', business_tasks_error: 'db unavailable' })];
    context.renderSummary();
    assert.match(element('status-summary').innerHTML, /部分队列读取失败/);
    assert.match(element('status-summary').innerHTML, /部分业务数据读取失败/);
    const html = context.renderServiceCard(state.services[0]);
    assert.match(html, /未知/);
});

test('real-time task checks opt in to inspect while ordinary polling stays lightweight', async () => {
    const { context } = harness();
    const urls = [];
    context.fetch = async url => {
        urls.push(url);
        return response(url.includes('/watchdog/') ? { watchdog: {} } : { services: [service()], broker: { ok: true } });
    };
    await context.refreshStatus({ silent: true });
    await context.refreshStatus({ silent: true, inspect: true });
    assert.equal(urls.filter(url => url.endsWith('/celery/status')).length, 1);
    assert.equal(urls.filter(url => url.endsWith('/celery/status?inspect=1')).length, 1);
});
