const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const scriptPath = path.join(projectRoot, 'src', 'frontend', 'js', 'bilibili-charging-dashboard.js');
const source = fs.readFileSync(scriptPath, 'utf8');
const instrumented = source.replace(
    /    document\.addEventListener\('DOMContentLoaded',[\s\S]*?\n    \}\);\n\}\)\(\);\s*$/,
    '    globalThis.BilibiliChargingDashboard = BilibiliChargingDashboard;\n})();\n',
);
assert.notEqual(instrumented, source, 'the dashboard class must be exposed to the behavior harness');

const rankingPanel = {
    innerHTML: '',
    attributes: {},
    setAttribute(name, value) {
        this.attributes[name] = String(value);
    },
};
const tabListeners = {};
const rankingTabs = {
    addEventListener(type, handler) {
        tabListeners[type] = handler;
    },
};
const tabButtons = ['day', 'week', 'month'].map((period) => ({
    id: `ranking-tab-${period}`,
    dataset: { bilibiliChargingRankingPeriod: period },
    attributes: {},
    classes: new Set(),
    setAttribute(name, value) {
        this.attributes[name] = String(value);
    },
    classList: {
        toggle() {},
    },
    closest(selector) {
        return selector === '[data-bilibili-charging-ranking-period]' ? this : null;
    },
    focus() {
        context.document.activeElement = this;
    },
}));
const elements = {
    'bilibili-charging-growth-ranking-panel': rankingPanel,
    'bilibili-charging-growth-ranking-tabs': rankingTabs,
};
const context = {
    console,
    document: {
        getElementById: (id) => elements[id] || null,
        querySelectorAll: (selector) => selector === '[data-bilibili-charging-ranking-period]'
            ? tabButtons
            : [],
    },
    Intl,
    window: { addEventListener() {} },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(instrumented, context, { filename: scriptPath });

const dashboard = Object.create(context.BilibiliChargingDashboard.prototype);
dashboard.chart = null;
dashboard.rankingPeriod = 'day';
dashboard.growthRankings = {};
dashboard.bindEvents();

dashboard.render({
    accounts: [],
    dates: [],
    has_data: true,
    growth_rankings: {
        day: {
            items: [
                { rank: 1, uid: '100', name: '甲账号', source_url: 'https://example.test/100', growth: 5, latest_count: 20, latest_date: '2026-09-08', baseline_date: '2026-09-07' },
                { rank: null, uid: '300', name: '<script>新账号</script>', source_url: 'javascript:alert(1)', growth: null, latest_count: 1, latest_date: '2026-09-08', baseline_date: null },
            ],
        },
        week: {
            items: [
                { rank: 1, uid: '200', name: '乙账号', source_url: 'https://example.test/200', growth: 12, latest_count: 30, latest_date: '2026-09-08', baseline_date: '2026-09-01' },
            ],
        },
        month: { items: [] },
    },
});

assert.match(rankingPanel.innerHTML, /甲账号/, 'the default day ranking must render');
assert.match(rankingPanel.innerHTML, /\+5 人/, 'positive growth must include a sign and unit');
assert.match(rankingPanel.innerHTML, /数据不足/, 'accounts without a baseline must stay visible');
assert.doesNotMatch(rankingPanel.innerHTML, /<script>/, 'account names must be HTML escaped');
assert.doesNotMatch(rankingPanel.innerHTML, /javascript:/, 'profile links must reject unsafe URL schemes');

dashboard.selectGrowthRankingPeriod('week');
assert.equal(dashboard.rankingPeriod, 'week');
assert.match(rankingPanel.innerHTML, /乙账号/, 'switching periods must rerender the requested ranking');
assert.doesNotMatch(rankingPanel.innerHTML, /甲账号/, 'the previous period must not remain visible');
assert.equal(tabButtons[1].attributes['aria-selected'], 'true');
assert.equal(tabButtons[0].attributes['aria-selected'], 'false');
assert.equal(tabButtons[1].attributes.tabindex, '0');
assert.equal(tabButtons[0].attributes.tabindex, '-1');
assert.equal(rankingPanel.attributes['aria-labelledby'], tabButtons[1].id);

let prevented = false;
tabListeners.keydown({
    target: tabButtons[1],
    key: 'ArrowRight',
    preventDefault() {
        prevented = true;
    },
});
assert.equal(prevented, true, 'handled navigation keys must prevent page scrolling');
assert.equal(dashboard.rankingPeriod, 'month');
assert.equal(context.document.activeElement, tabButtons[2]);

tabListeners.keydown({ target: tabButtons[2], key: 'Home', preventDefault() {} });
assert.equal(dashboard.rankingPeriod, 'day');
assert.equal(context.document.activeElement, tabButtons[0]);

dashboard.selectGrowthRankingPeriod('unknown');
assert.equal(dashboard.rankingPeriod, 'day', 'unknown periods must be ignored');

dashboard.renderEmpty('加载失败');
assert.equal(Object.keys(dashboard.growthRankings).length, 0, 'failed loads must discard stale ranking data');
dashboard.selectGrowthRankingPeriod('week');
assert.doesNotMatch(rankingPanel.innerHTML, /乙账号/, 'period changes must not revive stale rankings');
