const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

const projectRoot = path.resolve(__dirname, '..', '..');

test('rankings open an individual channel-owner profile instead of only the video list', () => {
    const { RankingsPage } = require(path.join(projectRoot, 'src/frontend/js/rankings-page.js'));
    const row = RankingsPage.prototype.renderRow.call({
        formatValue: RankingsPage.prototype.formatValue,
    }, {
        rank: 1,
        channel_id: 17,
        channel_name: '频道主 A',
        youtube_id: 'creator-a',
        category: '教育',
        cpm: 4.5,
        total_revenue: 99,
    }, {
        valueField: 'total_revenue', valueKind: 'currency', valueClass: 'text-blue-600', progressColor: '#3b82f6',
    }, { leaderValue: 99 });

    assert.match(row, /href="channel-owner\.html\?channel_id=17"/);
    assert.match(row, /aria-label="查看 频道主 A 的频道主档案"/);
});

test('the channel-owner profile controller resolves the selected channel id', () => {
    const { ChannelOwnerProfile } = require(path.join(projectRoot, 'src/frontend/js/channel-owner-profile.js'));
    const controller = new ChannelOwnerProfile({
        apiClient: {},
        config: { ENDPOINTS: { CHANNEL_OWNER_PROFILE: '/rankings/channel' } },
        documentRef: {},
        locationRef: { search: '?channel_id=17' },
        chartFactory: null,
    });

    assert.equal(controller.channelId, 17);
});

test('the channel-owner profile selects matching daily monthly and yearly table rows', () => {
    const { ChannelOwnerProfile } = require(path.join(projectRoot, 'src/frontend/js/channel-owner-profile.js'));
    const controller = new ChannelOwnerProfile({
        apiClient: {},
        config: { ENDPOINTS: {} },
        documentRef: {},
        locationRef: { search: '?channel_id=17' },
        chartFactory: null,
    });
    controller.data = {
        periods: {
            daily: [{ period: '2026-09-15' }],
            monthly: [{ period: '2026-09' }],
            yearly: [{ period: '2026' }],
        },
    };

    controller.period = 'monthly';
    assert.deepEqual(controller.periodRows(), [{ period: '2026-09' }]);
    controller.period = 'yearly';
    assert.deepEqual(controller.periodRows(), [{ period: '2026' }]);
});

test('the channel-owner profile renders the selected period as a metrics table', () => {
    const { ChannelOwnerProfile } = require(path.join(projectRoot, 'src/frontend/js/channel-owner-profile.js'));
    const elements = new Map([
        ['channel-owner-trend-title', { textContent: '' }],
        ['channel-owner-period-summary', { textContent: '' }],
        ['channel-owner-period-table', { innerHTML: '' }],
    ]);
    const controller = new ChannelOwnerProfile({
        apiClient: {},
        config: { ENDPOINTS: {} },
        documentRef: { getElementById(id) { return elements.get(id) || null; }, querySelectorAll() { return []; } },
        locationRef: { search: '?channel_id=17' },
        chartFactory: null,
    });
    controller.data = {
        periods: {
            daily: [],
            monthly: [{
                period: '2026-09', subscribers: 12000, total_views: 240000, video_count: 12, subscriber_change: 300, view_change: 30000, estimated_revenue: 135,
            }],
            yearly: [],
        },
    };
    controller.period = 'monthly';
    controller.renderPeriodData();

    assert.equal(elements.get('channel-owner-trend-title').textContent, '月趋势');
    assert.equal(elements.get('channel-owner-period-summary').textContent, '最近 1 个月周期');
    assert.match(elements.get('channel-owner-period-table').innerHTML, /2026-09/);
    assert.match(elements.get('channel-owner-period-table').innerHTML, />12<\/td>/);
    assert.match(elements.get('channel-owner-period-table').innerHTML, /\$135\.00/);
});
