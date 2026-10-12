const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');

function harness() {
    const elements = {
        'bilibili-charging-status': {}, 'bilibili-charging-sync-result': {},
        'bilibili-charging-summary': {}, 'bilibili-charging-chart': {},
    };
    const context = vm.createContext({ console, Date, Intl,
        document: { getElementById: id => elements[id], querySelectorAll: () => [] },
        window: {}, echarts: {},
        api: { get: async () => ({ success: true, data: { accounts: [], has_data: false } }),
            post: async () => ({ success: true, data: { synced: 5, failed: 2, errors: [{ name: '甲', error: '请求失败' }] } }) },
    });
    const source = fs.readFileSync('src/frontend/js/bilibili-charging-dashboard.js', 'utf8');
    vm.runInContext(source.replace(/    document.addEventListener\('DOMContentLoaded',[\s\S]*$/, '    globalThis.Dashboard = BilibiliChargingDashboard;\n})();'), context);
    const dashboard = Object.create(context.Dashboard.prototype);
    Object.assign(dashboard, { chart: { setOption(value) { dashboard.option = value; } }, rankingPeriod: 'day', growthRankings: {}, accountCount: 7 });
    return { dashboard, elements };
}

test('missing growth and estimates are not rendered as zero', () => {
    const { dashboard, elements } = harness();
    assert.equal(dashboard.signed(null), '—');
    assert.equal(dashboard.currency(null), '—');
    dashboard.renderSummary([{ uid: '1', name: 'New', latest: { date: '2026-10-01', charger_count: 0, daily_growth: null, monthly_income: 0 } }]);
    assert.match(elements['bilibili-charging-summary'].innerHTML, /2026-10-01/);
    assert.match(elements['bilibili-charging-summary'].innerHTML, /¥0/);
});

test('account line colors are stable across order changes and support seven accounts', () => {
    const { dashboard } = harness();
    const accounts = Array.from({ length: 7 }, (_, i) => ({ uid: String(i), name: `A${i}`, points: [] }));
    dashboard.renderChart(accounts, []);
    const colors = Object.fromEntries(dashboard.option.series.map(item => [item.name, item.itemStyle.color]));
    assert.equal(new Set(Object.values(colors)).size, 7);
    dashboard.renderChart([...accounts].reverse(), []);
    for (const series of dashboard.option.series) assert.equal(series.itemStyle.color, colors[series.name]);
});

test('partial sync failure survives the subsequent dashboard reload', async () => {
    const { dashboard, elements } = harness();
    await dashboard.sync();
    assert.match(elements['bilibili-charging-sync-result'].textContent, /2.*失败/);
    assert.match(elements['bilibili-charging-sync-result'].textContent, /甲/);
    assert.equal(dashboard.loading, false);
});
