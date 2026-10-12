const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const homepage = fs.readFileSync('src/frontend/index.html', 'utf8');

function harness() {
    const elements = {};
    const context = vm.createContext({
        console,
        Date,
        document: { addEventListener() {}, getElementById: id => elements[id] || null },
        window: { addEventListener() {} },
    });
    context.window = context;
    context.addEventListener = () => {};
    const source = fs.readFileSync(path.join(__dirname, '../../src/frontend/js/new-main.js'), 'utf8');
    vm.runInContext(source + '\nglobalThis.YouTubeDataApp = YouTubeDataApp;', context);
    const app = Object.create(context.YouTubeDataApp.prototype);
    const refreshes = [];
    app.loadInitialData = async () => refreshes.push('homepage');
    context.dataTableInstance = { loadTableData: async () => refreshes.push('table') };
    context.bilibiliChargingDashboard = { load: async () => refreshes.push('bilibili-dashboard') };
    const button = {
        disabled: false,
        attributes: {},
        setAttribute(name, value) { this.attributes[name] = value; },
        removeAttribute(name) { delete this.attributes[name]; },
        classList: { add() {}, remove() {} },
    };
    const status = { hidden: true, textContent: '', dataset: {} };
    const label = { textContent: '执行三项同步' };
    elements['home-run-sync-btn'] = button;
    elements['home-run-sync-status'] = status;
    elements['home-run-sync-label'] = label;
    return { app, context, button, status, label, refreshes };
}

test('homepage exposes a clearly labelled action to run the three data sync tasks', () => {
    const button = homepage.match(/<button\b(?=[^>]*\bid="home-run-sync-btn")[^>]*>[\s\S]*?<\/button>/);
    assert.ok(button, 'homepage should expose the manual three-task sync button');
    assert.match(button[0], /onclick="app\.runHomepageSync\(\)"/);
    assert.match(button[0], /执行三项同步/);
    assert.match(homepage, /id="home-run-sync-status"[^>]*role="status"[^>]*aria-live="polite"/);
});

test('homepage action runs the three existing sync interfaces in sequence and summarizes results', async () => {
    const { app, context, button, status, label, refreshes } = harness();
    const calls = [];
    const progressAtCall = [];
    context.api = {
        async post(endpoint, payload) {
            calls.push(['post', endpoint, payload]);
            progressAtCall.push(status.textContent);
            return { success: true, data: { synced: 7, failed: 0 } };
        },
        async syncAllChannels(payload) {
            calls.push(['channels', payload]);
            progressAtCall.push(status.textContent);
            return { success: true, data: { success_count: 49, failed_count: 2 } };
        },
        async syncVideos(payload) {
            calls.push(['videos', payload]);
            progressAtCall.push(status.textContent);
            return { success: true, data: { processed: 49, new_videos: 19, updated_videos: 59, errors: 2 } };
        },
    };

    assert.equal(typeof app.runHomepageSync, 'function', 'the homepage action should be handled by the app');
    await app.runHomepageSync();

    assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
        ['post', '/bilibili-intelligence/charging-dashboard/sync', {}],
        ['channels', { use_parallel: true, max_workers: 5, active_only: true }],
        ['videos', { days_back: 10, use_optimization: true, max_workers: 5 }],
    ]);
    assert.match(progressAtCall[0], /1\/3.*B站充电人数采集/);
    assert.match(progressAtCall[1], /2\/3.*YouTube 频道数据同步/);
    assert.match(progressAtCall[2], /3\/3.*YouTube 视频同步/);
    assert.match(status.textContent, /B站充电人数采集：7 个成功/);
    assert.match(status.textContent, /YouTube 频道数据同步：49 个成功，2 个失败/);
    assert.match(status.textContent, /YouTube 视频同步：处理 49 个频道，新增 19 个，更新 59 个，2 个错误/);
    assert.equal(status.dataset.state, 'warning');
    assert.deepEqual(refreshes, ['homepage', 'table', 'bilibili-dashboard']);
    assert.equal(button.disabled, false);
    assert.equal(button.attributes['aria-busy'], undefined);
    assert.equal(label.textContent, '执行三项同步');
});

test('a failed sync is reported without preventing the remaining tasks from running', async () => {
    const { app, context, status } = harness();
    const calls = [];
    context.api = {
        async post() { calls.push('bilibili'); throw new Error('认证失败'); },
        async syncAllChannels() { calls.push('channels'); return { success: true, data: { success_count: 1, failed_count: 0 } }; },
        async syncVideos() { calls.push('videos'); return { success: true, data: { processed: 1, new_videos: 0, updated_videos: 0, errors: 0 } }; },
    };

    await app.runHomepageSync();

    assert.deepEqual(calls, ['bilibili', 'channels', 'videos']);
    assert.match(status.textContent, /B站充电人数采集：失败（认证失败）/);
    assert.match(status.textContent, /YouTube 视频同步：处理 1 个频道/);
    assert.equal(status.dataset.state, 'warning');
});

test('duplicate clicks while a sync is running do not start a second batch', async () => {
    const { app, context, button, status } = harness();
    const calls = [];
    let resolveFirst;
    context.api = {
        post() {
            calls.push('bilibili');
            return new Promise(resolve => { resolveFirst = resolve; });
        },
        async syncAllChannels() { calls.push('channels'); return { success: true, data: {} }; },
        async syncVideos() { calls.push('videos'); return { success: true, data: {} }; },
    };

    const firstRun = app.runHomepageSync();
    assert.equal(button.disabled, true);
    assert.equal(button.attributes['aria-busy'], 'true');
    await app.runHomepageSync();
    assert.deepEqual(calls, ['bilibili']);

    resolveFirst({ success: true, data: { synced: 1, failed: 0 } });
    await firstRun;
    assert.deepEqual(calls, ['bilibili', 'channels', 'videos']);
    assert.equal(button.disabled, false);
    assert.match(status.textContent, /执行完成/);
});
