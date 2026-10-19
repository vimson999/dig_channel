const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const htmlPath = path.join(root, 'src', 'frontend', 'content-pipeline-submit.html');
const scriptPath = path.join(root, 'src', 'frontend', 'js', 'content-pipeline-submit.js');
const html = fs.readFileSync(htmlPath, 'utf8');
assert(!html.includes('cdn.tailwindcss.com'), 'page layout must not depend on the remote Tailwind runtime');
assert.match(html, /href="css\/content-pipeline-submit\.css\?[^\"]+"/);
assert(fs.existsSync(path.join(root, 'src/frontend/css/content-pipeline-submit.css')), 'local page stylesheet must be shipped');
const source = fs.readFileSync(scriptPath, 'utf8').replace(
    /\}\)\(\);\s*$/,
    'globalThis.ContentPipelineStatsTestHooks = { state, els, renderSubmissionStats }; })();',
);

for (const id of [
    'stats-period', 'stats-from', 'stats-to', 'stats-bank',
    'stats-pdf-total', 'stats-mp3-total', 'stats-total', 'stats-table-body',
]) {
    assert(html.includes(`id="${id}"`), `statistics view must expose ${id}`);
}

const context = {
    console,
    document: { addEventListener() {} },
    window: {},
    Intl,
    URLSearchParams,
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: scriptPath });

const hooks = context.ContentPipelineStatsTestHooks;
hooks.els.statsPdfTotal = { textContent: '' };
hooks.els.statsMp3Total = { textContent: '' };
hooks.els.statsTotal = { textContent: '' };
hooks.els.statsTableBody = { innerHTML: '' };
hooks.state.stats.totals = { pdf: 3, mp3: 2, total: 5 };
hooks.state.stats.from = '2026-10-14';
hooks.state.stats.to = '2026-10-15';
hooks.state.stats.rows = [
    {
        period: '2026-10-14',
        investment_bank: '高盛',
        pdf: 3,
        mp3: 2,
        total: 5,
    },
];
hooks.state.stats.loading = false;
hooks.state.stats.error = '';
hooks.renderSubmissionStats();

assert.equal(hooks.els.statsPdfTotal.textContent, '3');
assert.equal(hooks.els.statsMp3Total.textContent, '2');
assert.equal(hooks.els.statsTotal.textContent, '5');
assert.match(hooks.els.statsTableBody.innerHTML, /2026-10-14/);
assert.match(hooks.els.statsTableBody.innerHTML, /高盛/);
const detailHref = hooks.els.statsTableBody.innerHTML.match(/href="([^"]+)"/)[1].replaceAll('&amp;', '&');
const detailUrl = new URL(detailHref, 'http://localhost/');
assert.equal(detailUrl.pathname, '/content-pipeline-bank-detail.html');
assert.equal(detailUrl.searchParams.get('bank'), '高盛');
assert.equal(detailUrl.searchParams.get('from'), '2026-10-14');
assert.equal(detailUrl.searchParams.get('to'), '2026-10-15');
assert.equal((hooks.els.statsTableBody.innerHTML.match(/<td\b/g) || []).length, 5);
assert.match(hooks.els.statsTableBody.innerHTML, /<td[^>]*>3<\/td>/);
assert.match(hooks.els.statsTableBody.innerHTML, /<td[^>]*>2<\/td>/);

hooks.state.stats.rows = [
    { period: '2026-10-14', investment_bank: '旧日期', pdf: 100, mp3: 0, total: 100 },
    { period: '2026-10-15', investment_bank: '伯恩斯坦', pdf: 3, mp3: 0, total: 3 },
    { period: '2026-10-15', investment_bank: '内资机构', pdf: 0, mp3: 24, total: 24 },
    { period: '2026-10-15', investment_bank: '高盛', pdf: 27, mp3: 0, total: 27 },
];
hooks.renderSubmissionStats();
const orderedBanks = [...hooks.els.statsTableBody.innerHTML.matchAll(/<a[^>]+>([^<]+) <span/g)].map(match => match[1]);
assert.deepEqual(orderedBanks, ['高盛', '内资机构', '伯恩斯坦', '旧日期']);
assert.equal(hooks.state.stats.rows[0].investment_bank, '旧日期', 'rendering must not mutate source rows');

async function verifyActiveSubmissionPollingDoesNotRepeatStatistics() {
    const requests = [];
    let historyRequests = 0;
    let initialize;
    let poll;
    const elements = {};
    const makeElement = () => ({
        value: '', checked: false, disabled: false, textContent: '', innerHTML: '',
        className: '', files: [], handlers: {},
        classList: { toggle() {} },
        addEventListener(event, callback) { this.handlers[event] = callback; },
    });
    const runtime = {
        console, Intl, URLSearchParams, FormData,
        document: {
            hidden: false,
            addEventListener(event, callback) {
                if (event === 'DOMContentLoaded') initialize = callback;
            },
            getElementById(id) { return elements[id] ||= makeElement(); },
        },
        window: {
            API_BASE_URL: '/api/v1',
            location: { search: '', assign() {} },
            setInterval(callback, delay) {
                assert.equal(delay, 10000);
                poll = callback;
                return 1;
            },
        },
        fetch: async (url) => {
            const parsed = new URL(url, 'http://localhost');
            requests.push(parsed.pathname);
            if (parsed.pathname.endsWith('/web-submissions/statistics')) {
                return { ok: true, json: async () => ({ success: true, data: {
                    rows: [], totals: { pdf: 0, mp3: 0, total: 0 }, filters: { banks: [] },
                } }) };
            }
            historyRequests += 1;
            return { ok: true, json: async () => ({ success: true, data: {
                items: [{
                    submission_id: 'active',
                    status: historyRequests >= 3 ? 'completed' : 'processing',
                    tasks: [],
                }],
                has_more: false, next_offset: null,
            } }) };
        },
    };
    runtime.globalThis = runtime;
    vm.runInNewContext(fs.readFileSync(scriptPath, 'utf8'), runtime, { filename: scriptPath });
    initialize();
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(requests, [
        '/api/v1/content-pipeline/web-submissions',
        '/api/v1/content-pipeline/web-submissions/statistics',
    ]);

    poll();
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(requests, [
        '/api/v1/content-pipeline/web-submissions',
        '/api/v1/content-pipeline/web-submissions/statistics',
        '/api/v1/content-pipeline/web-submissions',
    ], 'active-task polling must refresh status without repeating the expensive statistics query');

    poll();
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(requests, [
        '/api/v1/content-pipeline/web-submissions',
        '/api/v1/content-pipeline/web-submissions/statistics',
        '/api/v1/content-pipeline/web-submissions',
        '/api/v1/content-pipeline/web-submissions',
        '/api/v1/content-pipeline/web-submissions/statistics',
    ], 'the active-to-terminal transition refreshes statistics exactly once');

    poll();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(requests.length, 5, 'terminal histories stop the active-task polling loop');
}

verifyActiveSubmissionPollingDoesNotRepeatStatistics()
    .then(() => console.log('content pipeline submission statistics contract: ok'))
    .catch((error) => { console.error(error); process.exitCode = 1; });
