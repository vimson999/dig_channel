const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness() {
    const elements = {};
    const context = vm.createContext({
        console, Intl, Date, URLSearchParams, setTimeout, clearTimeout,
        document: { addEventListener() {}, getElementById: id => elements[id] || null, querySelector: () => null, querySelectorAll: () => [] },
        window: { addEventListener() {} },
        Utils: { formatCurrency: v => `$${Number(v)}`, formatNumber: v => String(v),
            formatChange: v => String(v), formatDate: v => String(v) },
    });
    context.window = context; // Classic browser scripts share the Window/global binding.
    context.addEventListener = () => {};
    for (const [file, name] of [['new-main.js', 'YouTubeDataApp'], ['data-table.js', 'DataTable']]) {
        vm.runInContext(fs.readFileSync(path.join(__dirname, '../../src/frontend/js', file), 'utf8') +
            `\nglobalThis.${name} = ${name};`, context);
    }
    const app = Object.create(context.YouTubeDataApp.prototype);
    Object.assign(app, { chartMetric: 'revenue', chartMeta: {}, selectedChartChannelIds: null,
        currentChartType: 'line', channels: [], chartGranularity: 'daily' });
    return { context, elements, app };
}

test('rankings keep detail links and cap the homepage preview without interpreting zero as missing', () => {
    const { app, elements } = harness();
    elements['daily-revenue-ranking'] = { innerHTML: '' };
    app.renderRanking('daily-revenue-ranking', Array.from({ length: 12 }, (_, i) => ({
        channel_id: i, channel_name: `Channel ${i}`, revenue: 0,
    })), 'revenue');
    const html = elements['daily-revenue-ranking'].innerHTML;
    assert.equal((html.match(/class="ranking-item/g) || []).length, 10);
    assert.match(html, /rankings.html\?type=daily_revenue/);
    assert.doesNotMatch(html, /NaN/);
    app.renderRanking('daily-revenue-ranking', [], 'revenue');
    assert.match(elements['daily-revenue-ranking'].innerHTML, /rankings.html\?type=daily_revenue/);
});

test('homepage requests ten ranking entries so the preview can show places six through ten', async () => {
    const { app, context, elements } = harness();
    elements['daily-revenue-ranking'] = { innerHTML: '' };
    context.api = { getAllRankings: async limit => ({ success: true, data: Array.from({ length: limit }, (_, i) => ({
        channel_id: i, channel_name: `Rank ${i + 1}`, revenue: i,
    })) }) };
    app.updateRankings = rankings => app.renderRanking('daily-revenue-ranking', rankings, 'revenue');
    assert.equal(await app.loadRankings(), true);
    assert.match(elements['daily-revenue-ranking'].innerHTML, /Rank 10/);
});

test('charts default to top five, preserve gaps and show explicit units and empty selection', () => {
    const { app } = harness();
    let option;
    app.chart = { setOption(value) { option = value; } };
    app.chartData = { dates: ['2026-10-01', '2026-10-02'],
        series: Array.from({ length: 8 }, (_, i) => ({ id: i + 1, name: `C${i}`, data: [i, null] })) };
    app.refreshChart();
    assert.equal(option.series.length, 5);
    assert.equal(option.series[0].name, 'C7');
    assert.equal(option.series[0].data[1], null);
    assert.equal(option.legend.type, 'scroll');
    assert.match(option.yAxis.name, /USD/);
    assert.doesNotMatch(app.formatChartValue(null), /\$0/);
    app.selectedChartChannelIds = [];
    app.refreshChart();
    assert.equal(option.series.length, 0);
});

test('summary table shows one row per channel and distinguishes absent snapshots from real zero', () => {
    const { context, elements } = harness();
    const thead = { innerHTML: '' }, tbody = { innerHTML: '' };
    elements['data-table'] = { querySelector: name => name === 'thead' ? thead : tbody };
    const table = Object.create(context.DataTable.prototype);
    table.viewMode = 'summary';
    table.summarySearch = '';
    table.summarySort = 'estimated_revenue';
    table.tableData = { channels: [{ channel_id: 1, channel_name: 'Zero' }, { channel_id: 2, channel_name: 'Missing' }],
        rows: [{ date: '2026-10-01', channels_data: [
            { channel_id: 1, has_data: true, subscribers: 0, estimated_revenue: 0 },
            { channel_id: 2, has_data: false, subscribers: 0, estimated_revenue: 0 },
        ] }] };
    table.renderTable();
    assert.equal((tbody.innerHTML.match(/<tr[ >]/g) || []).length, 2);
    assert.match(tbody.innerHTML, /待同步/);
    assert.match(tbody.innerHTML, /\$0/);
    table.summarySearch = 'missing';
    table.renderTable();
    assert.equal((tbody.innerHTML.match(/<tr[ >]/g) || []).length, 1);
    assert.doesNotMatch(tbody.innerHTML, />Zero</);
});

test('opening table filter after chart filter restores table actions and leaves chart selection alone', async () => {
    const { context, app, elements } = harness();
    for (const id of ['channelFilterModal', 'channelFilterContent']) elements[id] = {};
    context.homeChannelDialog = { open() {}, focus() {} };
    context.api = { getChannels: async () => ({ success: true, data: { channels: [{ id: 1, name: 'A', is_active: true }] } }),
        getChannelCategories: async () => ({ success: true, data: { categories: [] } }) };
    app.tempSelectedIds = [123];
    app.allChannelsForFilter = [];
    app.bindFilterModalEvents();
    await context.openChannelFilterModal();
    context.clearAllChannels();
    assert.deepEqual(app.tempSelectedIds, [123], 'table clear must not change the chart draft');
    assert.equal(vm.runInContext('channelFilterData.selectedChannelIds.length', context), 0);
});

test('late chart picker response cannot replace a newer table picker', async () => {
    const { context, app, elements } = harness();
    for (const id of ['channelFilterModal', 'channelFilterContent']) elements[id] = {};
    let generation = 0, resolveChannels, renders = 0;
    context.homeChannelDialog = { open: () => ++generation, isActive: token => token === generation,
        close: () => { generation++; }, focus() {}, setBusy() {} };
    context.api = { getChannels: () => new Promise(resolve => { resolveChannels = resolve; }) };
    app.selectedChartChannelIds = [1];
    app.renderFilterList = () => { renders++; };
    const opening = app.openChartChannelFilter();
    context.homeChannelDialog.close();
    context.homeChannelDialog.open('table');
    resolveChannels({ success: true, data: { channels: [{ id: 1, name: 'A', is_active: true }] } });
    await opening;
    assert.equal(renders, 0, 'stale chart response must leave the table picker untouched');
});

test('detail pagination exposes remaining dates and hides controls in summary mode', () => {
    const { context, elements } = harness();
    elements['home-detail-pagination'] = { innerHTML: '', hidden: false };
    const table = Object.create(context.DataTable.prototype);
    table.viewMode = 'detail';
    table.renderPagination({ page: 1, pages: 3, has_next: true, has_prev: false });
    assert.match(elements['home-detail-pagination'].innerHTML, /goToPage\(2\)/);
    assert.match(elements['home-detail-pagination'].innerHTML, /disabled/);
    table.viewMode = 'summary';
    table.renderPagination({ page: 1, pages: 3 });
    assert.equal(elements['home-detail-pagination'].hidden, true);
});

test('homepage auto refresh uses one visibility-aware poller without another initial load', async () => {
    const { context, app } = harness();
    const { create } = require('../../src/frontend/js/visibility-polling.js');
    let calls = 0, timers = 0;
    context.VisibilityPolling = { create: options => create({ ...options,
        documentRef: context.document, windowRef: context,
        setIntervalFn: () => { timers++; return 1; }, clearIntervalFn() {},
    }) };
    app.loadRankings = async () => { calls++; };
    app.loadChartData = async () => { calls++; };
    app.startAutoRefresh();
    app.startAutoRefresh();
    assert.equal(timers, 1);
    assert.equal(calls, 0);
    await app.refreshPoller.tick();
    assert.equal(calls, 2);
    context.document.hidden = true;
    await app.refreshPoller.tick();
    assert.equal(calls, 2);
});
