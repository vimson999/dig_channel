const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const scriptPath = path.join(projectRoot, 'src', 'frontend', 'js', 'mp3-video-workflow.js');
const source = fs.readFileSync(scriptPath, 'utf8').replace(
    /document\.addEventListener\('DOMContentLoaded',[\s\S]*$/,
    'globalThis.Mp3VideoWorkflowPage = Mp3VideoWorkflowPage;',
);
const context = {
    AbortController,
    URLSearchParams,
    clearTimeout,
    console,
    document: {},
    setTimeout,
    window: {},
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: scriptPath });

const Page = context.Mp3VideoWorkflowPage;

async function testCompositionInputWaitsForCommittedText() {
    const page = Object.create(Page.prototype);
    const loads = [];
    page.audioAssetSearchTimer = null;
    page.audioAssetComposing = false;
    page.loadAudioAssets = async (options) => { loads.push(options); };

    page.handleAudioAssetCompositionStart();
    page.scheduleAudioAssetSearch({ isComposing: true });
    await new Promise((resolve) => setTimeout(resolve, 400));
    assert.equal(loads.length, 0, 'IME composition text must not issue intermediate database searches');

    page.handleAudioAssetCompositionEnd();
    await new Promise((resolve) => setTimeout(resolve, 400));
    assert.equal(loads.length, 1, 'committed IME text must issue exactly one debounced search');
    assert.equal(loads[0].trigger, 'composition_end', 'the committed IME request must be observable by trigger');
}

async function testSlowSearchesCollapseToTheLatestQuery() {
    const page = Object.create(Page.prototype);
    let releaseFirst;
    const paths = [];
    page.audioAssetPage = 1;
    page.audioAssetPerPage = 10;
    page.audioAssetPagination = { page: 1, per_page: 10, has_more: false };
    page.audioAssetScope = 'all';
    page.audioAssetRequestSeq = 0;
    page.audioAssetClientId = 'client-test';
    page.audioAssetClientInFlight = 0;
    page.audioAssetLoadPromise = null;
    page.audioAssetPendingLoad = null;
    page.audioAssetsLoading = false;
    page.audioAssets = [];
    page.els = {
        audioAssetSearch: { value: "tian'feng" },
        audioAssetSummary: { textContent: '' },
        audioAssetList: { innerHTML: '' },
        audioAssetPagination: { innerHTML: '' },
    };
    page.renderAudioAssetTabs = () => {};
    page.renderAudioAssets = () => {};
    page.loadAudioAssetWorkflowStats = () => {};
    page.request = async (requestPath) => {
        paths.push(requestPath);
        if (paths.length === 1) {
            return new Promise((resolve) => { releaseFirst = resolve; });
        }
        return { data: { items: [], pagination: { page: 1, per_page: 10, has_more: false } } };
    };

    const firstLoad = page.loadAudioAssets({ page: 1, silent: true, trigger: 'debounce' });
    await Promise.resolve();
    page.els.audioAssetSearch.value = '天风';
    const latestLoad = page.loadAudioAssets({ page: 1, silent: true, trigger: 'composition_end' });
    await Promise.resolve();

    assert.equal(paths.length, 1, 'a slow search must retain single ownership of the database request');
    releaseFirst({ data: { items: [], pagination: { page: 1, per_page: 10, has_more: false } } });
    await Promise.all([firstLoad, latestLoad]);

    assert.equal(paths.length, 2, 'only the latest queued search should run after the first request');
    assert.match(paths[1], /q=%E5%A4%A9%E9%A3%8E(?:&|$)/, 'the queued request must use the latest committed query');
    assert.match(paths[0], /client_id=client-test(?:&|$)/, 'each request must identify its page owner');
    assert.match(paths[0], /client_seq=1(?:&|$)/, 'the first request must expose its ownership sequence');
    assert.match(paths[0], /client_trigger=debounce(?:&|$)/, 'the first request must expose its trigger');
    assert.match(paths[0], /client_inflight=1(?:&|$)/, 'the request must report in-flight ownership at dispatch');
    assert.match(paths[1], /client_seq=2(?:&|$)/, 'the drained latest request must expose its final sequence');
    assert.match(paths[1], /client_trigger=composition_end(?:&|$)/, 'the final request must preserve the IME trigger');
    assert.equal(page.audioAssetClientInFlight, 0, 'in-flight telemetry must unwind after both requests');
}

async function testWorkerBadgeUsesRuntimeOnlyStatus() {
    const page = Object.create(Page.prototype);
    const paths = [];
    const badges = [];
    page.els = { workerStatus: {} };
    page.request = async (requestPath) => {
        paths.push(requestPath);
        return {
            data: {
                services: [
                    { id: 'mp3_video_workflow', running: true, queue_length: 2 },
                    { id: 'render', running: true, queue_length: 1 },
                ],
            },
        };
    };
    page.setBadge = (_element, text, level) => badges.push({ text, level });

    await page.refreshWorkerStatus();

    assert.deepEqual(paths, [
        '/api/v1/celery/runtime-status?services=mp3_video_workflow%2Crender',
    ]);
    assert.deepEqual(badges, [
        { text: 'Worker 正常 · MP3 2 / 渲染 1', level: 'success' },
    ]);
}

Promise.resolve()
    .then(testCompositionInputWaitsForCommittedText)
    .then(testSlowSearchesCollapseToTheLatestQuery)
    .then(testWorkerBadgeUsesRuntimeOnlyStatus)
    .then(() => console.log('mp3 audio asset search backpressure contract: ok'))
    .catch((error) => {
        console.error(error);
        process.exitCode = 1;
    });
