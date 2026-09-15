const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '../..');

test('each dashboard ranking card links to its matching full ranking tab', () => {
    const homepage = fs.readFileSync(path.join(projectRoot, 'src/frontend/index.html'), 'utf8');
    for (const type of [
        'daily_revenue',
        'monthly_revenue',
        'daily_subscriber',
        'monthly_subscriber',
    ]) {
        assert.match(
            homepage,
            new RegExp(`href=["']rankings\\.html\\?type=${type}["']`),
            `missing full-ranking entry for ${type}`,
        );
    }
});

test('the dedicated page exposes six accessible ranking tabs and one live result panel', () => {
    const pagePath = path.join(projectRoot, 'src/frontend/rankings.html');
    assert.ok(fs.existsSync(pagePath), 'the dedicated rankings page must exist');

    const page = fs.readFileSync(pagePath, 'utf8');
    assert.match(page, /id=["']ranking-tabs["'][^>]*role=["']tablist["']/);
    assert.match(page, /id=["']ranking-results["'][^>]*role=["']tabpanel["']/);
    assert.match(page, /aria-live=["']polite["']/);

    for (const type of [
        'daily_revenue',
        'monthly_revenue',
        'daily_subscriber',
        'monthly_subscriber',
        'yearly_revenue',
        'yearly_subscriber',
    ]) {
        assert.match(page, new RegExp(`data-ranking-type=["']${type}["']`));
    }
});

test('the rankings page loads its controller and provides pagination controls', () => {
    const pagePath = path.join(projectRoot, 'src/frontend/rankings.html');
    assert.ok(fs.existsSync(pagePath), 'the dedicated rankings page must exist');

    const page = fs.readFileSync(pagePath, 'utf8');
    assert.match(page, /id=["']ranking-total["']/);
    assert.match(page, /id=["']ranking-pagination["']/);
    assert.match(page, /js\/rankings-page\.js/);
});

test('the dedicated page integrates the dashboard trend workspace', () => {
    const page = fs.readFileSync(
        path.join(projectRoot, 'src/frontend/rankings.html'),
        'utf8',
    );

    assert.match(page, /data-page-view=["']rankings["']/);
    assert.match(page, /data-page-view=["']trends["']/);
    assert.match(page, /id=["']trend-workspace["']/);
    assert.match(page, /id=["']trend-chart["']/);
    assert.match(page, /id=["']trend-channel-filter["']/);
    assert.match(page, /data-trend-chart=["']line["']/);
    assert.match(page, /data-trend-chart=["']bar["']/);
    assert.match(page, /data-trend-chart=["']pie["']/);
    assert.match(page, /data-trend-granularity=["']daily["']/);
    assert.match(page, /data-trend-granularity=["']monthly["']/);
    assert.match(page, /js\/rankings-trends\.js/);
    assert.match(page, /echarts(?:\.min)?\.js/);
});

test('the page controller loads the URL-selected ranking and switches tabs back to page one', async () => {
    const controllerPath = path.join(projectRoot, 'src/frontend/js/rankings-page.js');
    assert.ok(fs.existsSync(controllerPath), 'the rankings page controller must exist');
    const { RankingsPage } = require(controllerPath);

    class FakeElement {
        constructor(type = '') {
            this.dataset = type ? { rankingType: type } : {};
            this.listeners = {};
            this.innerHTML = '';
            this.textContent = '';
            this.disabled = false;
            this.attributes = {};
            this.classList = { toggle() {}, add() {}, remove() {} };
        }
        addEventListener(name, callback) { this.listeners[name] = callback; }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        dispatch(name) { return this.listeners[name]?.({ currentTarget: this, target: this }); }
    }

    const monthlyTab = new FakeElement('monthly_subscriber');
    const dailyTab = new FakeElement('daily_revenue');
    const elements = {
        'ranking-results': new FakeElement(),
        'ranking-total': new FakeElement(),
        'ranking-period': new FakeElement(),
        'ranking-pagination': new FakeElement(),
        'ranking-page-size': Object.assign(new FakeElement(), { value: '20' }),
        'ranking-status': new FakeElement(),
        'ranking-period-input': new FakeElement(),
        'ranking-current-period': new FakeElement(),
    };
    const documentRef = {
        getElementById: (id) => elements[id] || null,
        querySelectorAll: (selector) => selector === '[data-ranking-type]'
            ? [monthlyTab, dailyTab]
            : [],
    };
    const calls = [];
    const apiClient = {
        async get(endpoint, params) {
            calls.push({ endpoint, params });
            return {
                success: true,
                data: {
                    rankings: [{
                        rank: params.page === 2 ? 21 : 1,
                        channel_id: 7,
                        channel_name: '测试频道',
                        total_subscriber_change: 1234,
                        revenue: 9.5,
                    }],
                    total_count: 41,
                    pagination: { page: params.page, per_page: params.per_page, total: 41, total_pages: 3 },
                    period: { start_date: '2026-09-01', end_date: '2026-09-15' },
                },
            };
        },
    };
    const historyCalls = [];
    const locationRef = { pathname: '/rankings.html', search: '?type=monthly_subscriber' };
    const historyRef = { replaceState: (_state, _title, url) => historyCalls.push(url) };
    const config = {
        ENDPOINTS: {
            RANKINGS_DAILY_REVENUE: '/rankings/daily-revenue',
            RANKINGS_MONTHLY_REVENUE: '/rankings/monthly-revenue',
            RANKINGS_DAILY_SUBSCRIBER: '/rankings/daily-subscriber',
            RANKINGS_MONTHLY_SUBSCRIBER: '/rankings/monthly-subscriber',
        },
    };

    const page = new RankingsPage({ apiClient, config, documentRef, locationRef, historyRef, today: '2026-09-15' });
    await page.init();

    assert.deepEqual(calls[0], {
        endpoint: '/rankings/monthly-subscriber',
        params: { page: 1, per_page: 20, year: 2026, month: 9 },
    });
    assert.match(elements['ranking-results'].innerHTML, /测试频道/);
    assert.match(elements['ranking-results'].innerHTML, /1,234/);
    assert.equal(elements['ranking-total'].textContent, '共 41 个频道');

    page.page = 3;
    await dailyTab.dispatch('click');
    assert.deepEqual(calls[1], {
        endpoint: '/rankings/daily-revenue',
        params: { page: 1, per_page: 20, date: '2026-09-15' },
    });
    assert.equal(page.page, 1);
    assert.equal(historyCalls.at(-1), '/rankings.html?type=daily_revenue&date=2026-09-15');
});

test('ranking rows keep channel identity on one compact line', () => {
    const { RankingsPage } = require(path.join(projectRoot, 'src/frontend/js/rankings-page.js'));
    const page = new RankingsPage({
        apiClient: {},
        config: { ENDPOINTS: {} },
        documentRef: { getElementById() { return null; }, querySelectorAll() { return []; } },
        locationRef: { pathname: '/rankings.html', search: '' },
        historyRef: {},
    });

    const row = page.renderRow({
        rank: 20,
        channel_id: 26,
        channel_name: 'Skill Leap AI',
        youtube_id: 'UCwSozl89jl2zUDzQ4jGJD3g',
        cpm: 3.25,
        category: 'AI工具',
        total_revenue: 2214.61,
    }, {
        valueField: 'total_revenue',
        valueKind: 'currency',
        valueClass: 'text-blue-600',
    });

    assert.match(row, /data-ranking-row/);
    assert.match(row, /AI工具/);
    assert.match(row, /\$3\.25\/千次/);
    assert.match(row, /data-channel-identity[^>]*>[\s\S]*?<\/div>\s*<div data-category-column/);
    assert.match(row, /data-category-column[^>]*>[\s\S]*?<\/div>\s*<div data-cpm-column/);
    assert.match(row, /data-channel-identity[^>]*>\s*<span[^>]*>Skill Leap AI<\/span>\s*<span[^>]*>UCwSozl89jl2zUDzQ4jGJD3g<\/span>/);
    assert.doesNotMatch(row, /mt-1/);
});

test('ranking rows turn unused middle space into relative-to-leader context', () => {
    const { RankingsPage } = require(path.join(projectRoot, 'src/frontend/js/rankings-page.js'));
    const page = new RankingsPage({
        apiClient: {},
        config: { ENDPOINTS: {} },
        documentRef: { getElementById() { return null; }, querySelectorAll() { return []; } },
        locationRef: { pathname: '/rankings.html', search: '' },
        historyRef: {},
    });

    const row = page.renderRow({
        rank: 2,
        channel_id: 27,
        channel_name: 'AI Search',
        youtube_id: 'UCIgnGlGkVRhd4qNFcEwLL4A',
        total_revenue: 28173.25,
    }, {
        valueField: 'total_revenue',
        valueKind: 'currency',
        valueClass: 'text-blue-600',
    }, { leaderValue: 163801 });

    assert.match(row, /<progress[^>]*value="17\.2"[^>]*max="100"/);
    assert.match(row, />17\.2%<\/span>/);
});

test('selected time period drives ranking API parameters', async () => {
    const { RankingsPage } = require(path.join(projectRoot, 'src/frontend/js/rankings-page.js'));
    class FakeElement {
        constructor(value = '') {
            this.value = value;
            this.listeners = {};
            this.dataset = {};
            this.innerHTML = '';
            this.textContent = '';
            this.hidden = false;
            this.disabled = false;
            this.attributes = {};
            this.classList = { toggle() {}, add() {}, remove() {} };
        }
        addEventListener(name, callback) { this.listeners[name] = callback; }
        setAttribute(name, value) { this.attributes[name] = String(value); }
    }
    const elements = Object.fromEntries([
        'ranking-results', 'ranking-total', 'ranking-period', 'ranking-pagination',
        'ranking-status', 'ranking-value-header', 'ranking-period-input',
        'ranking-current-period', 'ranking-period-controls',
    ].map((id) => [id, new FakeElement()]));
    elements['ranking-page-size'] = new FakeElement('20');
    const shiftBack = new FakeElement();
    shiftBack.dataset.periodShift = '-1';
    const shiftForward = new FakeElement();
    shiftForward.dataset.periodShift = '1';
    const calls = [];
    const page = new RankingsPage({
        apiClient: {
            async get(endpoint, params) {
                calls.push({ endpoint, params });
                return {
                    success: true,
                    data: {
                        rankings: [],
                        pagination: { page: 1, per_page: 20, total: 0, total_pages: 0 },
                    },
                };
            },
        },
        config: { ENDPOINTS: {
            RANKINGS_DAILY_REVENUE: '/rankings/daily-revenue',
            RANKINGS_MONTHLY_REVENUE: '/rankings/monthly-revenue',
        } },
        documentRef: {
            getElementById(id) { return elements[id] || null; },
            querySelectorAll(selector) {
                if (selector === '[data-period-shift]') return [shiftBack, shiftForward];
                return [];
            },
        },
        locationRef: { pathname: '/rankings.html', search: '?type=monthly_revenue&month=2026-08' },
        historyRef: { replaceState() {} },
        today: '2026-09-15',
    });

    await page.init();
    assert.deepEqual(calls[0], {
        endpoint: '/rankings/monthly-revenue',
        params: { page: 1, per_page: 20, year: 2026, month: 8 },
    });
    assert.equal(elements['ranking-period-input'].type, 'month');
    assert.equal(elements['ranking-period-input'].value, '2026-08');

    await page.shiftPeriod(-1);
    assert.deepEqual(calls[1].params, { page: 1, per_page: 20, year: 2026, month: 7 });

    page.type = 'daily_revenue';
    page.dailyDate = '2026-08-07';
    await page.load();
    assert.deepEqual(calls[2].params, { page: 1, per_page: 20, date: '2026-08-07' });
});

test('yearly rankings use a year selector and preserve the selected year', async () => {
    const { RankingsPage } = require(path.join(projectRoot, 'src/frontend/js/rankings-page.js'));
    class FakeElement {
        constructor() {
            this.value = '';
            this.dataset = {};
            this.disabled = false;
            this.listeners = {};
            this.classList = { toggle() {} };
        }
        addEventListener(name, callback) { this.listeners[name] = callback; }
        setAttribute() {}
    }
    const elements = Object.fromEntries([
        'ranking-period-input', 'ranking-current-period', 'ranking-results',
        'ranking-total', 'ranking-period', 'ranking-pagination', 'ranking-status',
    ].map((id) => [id, new FakeElement()]));
    elements['ranking-page-size'] = Object.assign(new FakeElement(), { value: '20' });
    const calls = [];
    const page = new RankingsPage({
        apiClient: { async get(endpoint, params) {
            calls.push({ endpoint, params });
            return { success: true, data: { rankings: [], pagination: { page: 1, per_page: 20, total: 0, total_pages: 0 } } };
        } },
        config: { ENDPOINTS: { RANKINGS_YEARLY_REVENUE: '/rankings/yearly-revenue' } },
        documentRef: {
            getElementById(id) { return elements[id] || null; },
            querySelectorAll() { return []; },
        },
        locationRef: { pathname: '/rankings.html', search: '?type=yearly_revenue&year=2025' },
        historyRef: { replaceState() {} },
        today: '2026-09-15',
    });

    await page.init();
    assert.deepEqual(calls[0], {
        endpoint: '/rankings/yearly-revenue',
        params: { page: 1, per_page: 20, year: 2025 },
    });
    assert.equal(elements['ranking-period-input'].type, 'number');
    assert.equal(elements['ranking-period-input'].value, '2025');

    await page.shiftPeriod(-1);
    assert.deepEqual(calls[1].params, { page: 1, per_page: 20, year: 2024 });
});

test('the browser default constructor uses the API client exposed by the classic api script', () => {
    const source = fs.readFileSync(
        path.join(projectRoot, 'src/frontend/js/rankings-page.js'),
        'utf8',
    );
    const classicApi = { get() {} };
    const context = {
        api: classicApi,
        CONFIG: { ENDPOINTS: {} },
        Intl,
        URLSearchParams,
        module: { exports: {} },
        document: {
            addEventListener() {},
            getElementById() { return null; },
            querySelectorAll() { return []; },
        },
        window: {
            history: { replaceState() {} },
            location: { pathname: '/rankings.html', search: '' },
        },
    };
    vm.runInNewContext(source, context);

    const page = new context.module.exports.RankingsPage();
    assert.equal(page.api, classicApi);
});

test('the hub initializes only the URL-selected workspace and lazy-loads the other view', async () => {
    const { RankingsHub } = require(path.join(projectRoot, 'src/frontend/js/rankings-page.js'));
    const rankingSection = { hidden: false };
    const trendSection = { hidden: true };
    const buttons = ['rankings', 'trends'].map((pageView) => ({
        dataset: { pageView },
        attributes: {},
        classList: { toggle() {} },
        addEventListener() {},
        setAttribute(name, value) { this.attributes[name] = String(value); },
    }));
    const documentRef = {
        getElementById(id) {
            return id === 'ranking-workspace' ? rankingSection : id === 'trend-workspace' ? trendSection : null;
        },
        querySelectorAll(selector) { return selector === '[data-page-view]' ? buttons : []; },
    };
    let rankingInitCount = 0;
    let trendInitCount = 0;
    const historyCalls = [];
    const hub = new RankingsHub({
        rankingPage: { type: 'daily_revenue', async init() { rankingInitCount += 1; } },
        trendsPage: { async init() { trendInitCount += 1; }, resize() {} },
        documentRef,
        locationRef: { pathname: '/rankings.html', search: '?view=trends' },
        historyRef: { replaceState: (_state, _title, url) => historyCalls.push(url) },
    });

    await hub.init();
    assert.equal(rankingInitCount, 0);
    assert.equal(trendInitCount, 1);
    assert.equal(rankingSection.hidden, true);
    assert.equal(trendSection.hidden, false);

    await hub.selectView('rankings');
    assert.equal(rankingInitCount, 1);
    assert.equal(trendInitCount, 1);
    assert.equal(historyCalls.at(-1), '/rankings.html?type=daily_revenue');
});
