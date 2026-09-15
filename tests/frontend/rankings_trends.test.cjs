const assert = require('node:assert/strict');
const test = require('node:test');

const { RankingsTrends } = require('../../src/frontend/js/rankings-trends.js');

class FakeClassList {
    constructor() { this.values = new Set(); }
    add(...values) { values.forEach((value) => this.values.add(value)); }
    remove(...values) { values.forEach((value) => this.values.delete(value)); }
    toggle(value, force) {
        if (force === undefined) force = !this.values.has(value);
        if (force) this.values.add(value);
        else this.values.delete(value);
        return force;
    }
    contains(value) { return this.values.has(value); }
}

class FakeElement {
    constructor({ value = '', dataset = {} } = {}) {
        this.value = value;
        this.dataset = dataset;
        this.listeners = {};
        this.attributes = {};
        this.classList = new FakeClassList();
        this.innerHTML = '';
        this.textContent = '';
        this.hidden = false;
        this.checked = false;
    }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    removeAttribute(name) { delete this.attributes[name]; }
    querySelectorAll() { return []; }
}

function createHarness() {
    const elements = {
        'trend-chart': new FakeElement(),
        'trend-status': new FakeElement(),
        'trend-meta': new FakeElement(),
        'trend-empty': new FakeElement(),
        'trend-metric': new FakeElement({ value: 'revenue' }),
        'trend-daily-range': new FakeElement({ value: '7' }),
        'trend-monthly-range': new FakeElement({ value: '6' }),
        'trend-channel-filter': new FakeElement(),
        'trend-channel-panel': new FakeElement(),
        'trend-channel-list': new FakeElement(),
        'trend-channel-search': new FakeElement(),
        'trend-channel-summary': new FakeElement(),
        'trend-filter-apply': new FakeElement(),
        'trend-filter-clear': new FakeElement(),
        'trend-filter-close': new FakeElement(),
    };
    const chartButtons = ['line', 'bar', 'pie'].map((type) => new FakeElement({ dataset: { trendChart: type } }));
    const granularityButtons = ['daily', 'monthly'].map((type) => new FakeElement({ dataset: { trendGranularity: type } }));
    const documentRef = {
        getElementById: (id) => elements[id] || null,
        querySelectorAll(selector) {
            if (selector === '[data-trend-chart]') return chartButtons;
            if (selector === '[data-trend-granularity]') return granularityButtons;
            return [];
        },
    };
    const calls = [];
    const apiClient = {
        async getChannels(params) {
            calls.push({ method: 'channels', params });
            return {
                success: true,
                data: {
                    channels: [
                        { id: 11, name: 'Alpha', is_active: true, category: 'AI' },
                        { id: 22, name: 'Beta', is_active: true, category: '科技' },
                    ],
                    pagination: { total: 2, pages: 1 },
                },
            };
        },
        async getChartData(metric, params) {
            calls.push({ method: 'chart', metric, params });
            return {
                success: true,
                data: {
                    chart_type: metric,
                    total_channels: params.channel_ids ? 1 : 2,
                    date_range: { start_date: '2026-09-09', end_date: '2026-09-15' },
                    granularity: params.group,
                    chart_data: {
                        dates: params.group === 'monthly'
                            ? ['2026-08-01', '2026-09-01']
                            : ['2026-09-14', '2026-09-15'],
                        series: [
                            { name: 'Alpha', data: [10, 20] },
                            { name: 'Beta', data: [5, 8] },
                        ],
                    },
                },
            };
        },
    };
    const chart = {
        options: [],
        setOption(option) { this.options.push(option); },
        resize() {},
    };
    const echartsRef = { init(element) { assert.equal(element, elements['trend-chart']); return chart; } };

    return {
        page: new RankingsTrends({ apiClient, echartsRef, documentRef, windowRef: null }),
        elements,
        calls,
        chart,
    };
}

test('trend workspace loads active channels and the seven-day revenue series', async () => {
    const { page, calls, chart, elements } = createHarness();

    await page.init();

    assert.deepEqual(calls[0], {
        method: 'channels',
        params: { active_only: true, page: 1, per_page: 100 },
    });
    assert.deepEqual(calls[1], {
        method: 'chart',
        metric: 'revenue',
        params: { time_range: 7, group: 'daily' },
    });
    assert.equal(chart.options.at(-1).series[0].type, 'line');
    assert.deepEqual(chart.options.at(-1).xAxis.data, ['2026-09-14', '2026-09-15']);
    assert.equal(elements['trend-meta'].textContent, '2 个频道 · 2026-09-09 至 2026-09-15');
    assert.equal(elements['trend-channel-panel'].classList.contains('hidden'), true);
    assert.equal(elements['trend-empty'].classList.contains('hidden'), true);

    page.openChannelFilter();
    assert.equal(elements['trend-channel-panel'].classList.contains('hidden'), false);
    page.closeChannelFilter();
    assert.equal(elements['trend-channel-panel'].classList.contains('hidden'), true);
});

test('chart shape switches locally while monthly granularity reloads aggregated data', async () => {
    const { page, calls, chart } = createHarness();
    await page.init();

    page.selectChartType('pie');
    assert.equal(calls.filter((call) => call.method === 'chart').length, 1);
    assert.equal(chart.options.at(-1).series[0].type, 'pie');
    assert.deepEqual(chart.options.at(-1).series[0].data, [
        { name: 'Alpha', value: 20 },
        { name: 'Beta', value: 8 },
    ]);

    await page.selectGranularity('monthly');
    assert.deepEqual(calls.at(-1), {
        method: 'chart',
        metric: 'revenue',
        params: { time_range: 7, group: 'monthly', months: 6 },
    });
});

test('pie view keeps the ten largest channels and combines the long tail', async () => {
    const { page, chart } = createHarness();
    await page.init();
    page.chartData.series = Array.from({ length: 12 }, (_value, index) => ({
        name: `频道 ${index + 1}`,
        data: [index + 1],
    }));

    page.selectChartType('pie');

    const slices = chart.options.at(-1).series[0].data;
    assert.equal(slices.length, 11);
    assert.deepEqual(slices.slice(0, 3), [
        { name: '频道 12', value: 12 },
        { name: '频道 11', value: 11 },
        { name: '频道 10', value: 10 },
    ]);
    assert.deepEqual(slices.at(-1), { name: '其他频道', value: 3 });
});

test('applying a channel selection reloads the trend with stable internal IDs', async () => {
    const { page, calls, elements } = createHarness();
    await page.init();

    await page.applyChannelSelection([22]);

    assert.deepEqual(calls.at(-1), {
        method: 'chart',
        metric: 'revenue',
        params: { time_range: 7, group: 'daily', channel_ids: '22' },
    });
    assert.equal(elements['trend-channel-summary'].textContent, '已选 1 个频道');
});

test('malformed chart responses show a recoverable empty state instead of throwing', async () => {
    const { page, elements } = createHarness();
    page.api.getChartData = async () => ({ success: true, data: { chart_data: null } });

    await page.init();

    assert.equal(elements['trend-empty'].hidden, false);
    assert.match(elements['trend-status'].textContent, /暂无趋势数据/);
});
