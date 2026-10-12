const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const VideoPageUtils = require('../../src/frontend/js/searchable-combobox.js');

function harness() {
    const elements = {}, events = {};
    const document = { body: { style: { overflow: '' } }, activeElement: null,
        getElementById: id => elements[id] || null,
        querySelectorAll: () => [],
        addEventListener(type, fn) { (events[type] ||= []).push(fn); } };
    function element(id, classes = []) {
        const names = new Set(classes), attrs = {};
        return elements[id] = { id, innerHTML: '', textContent: '', hidden: false, disabled: false,
            value: '', children: [], dataset: {}, isConnected: true,
            classList: { add: (...v) => v.forEach(n => names.add(n)), remove: (...v) => v.forEach(n => names.delete(n)),
                contains: n => names.has(n), toggle(n, value) { value ? names.add(n) : names.delete(n); } },
            setAttribute: (key, value) => { attrs[key] = String(value); }, getAttribute: key => attrs[key],
            addEventListener(type, fn) { this[type] = fn; },
            focus() { document.activeElement = this; },
            appendChild(child) { this.children.push(child); },
            querySelector: () => null, querySelectorAll() { return this.children; },
            contains(child) { return child === this || this.children.includes(child); }, getClientRects: () => [1] };
    }
    document.createElement = () => element(`created-${Object.keys(elements).length}`);
    const context = vm.createContext({ document, console: { log() {}, error() {} },
        setTimeout, clearTimeout, URL, URLSearchParams, VideoPageUtils });
    context.window = context;
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../src/frontend/js/video-management.js'), 'utf8') +
        '\nglobalThis.VideoManager = VideoManager;', context);
    const manager = Object.create(context.VideoManager.prototype);
    Object.assign(manager, { currentPage: 1, pageSize: 20, totalRecords: 0, totalPages: 0,
        currentFilters: { search: '', channel: '', category: '', timePeriod: '7', sortBy: 'published_at', order: 'desc' },
        videoRequestGate: VideoPageUtils.createLatestRequestGate(), favoriteStatus: new Map(), detailRequestId: 0 });
    for (const id of ['videos-grid', 'pagination', 'page-start', 'page-end', 'total-records', 'prev-page', 'next-page', 'page-numbers', 'videos-count']) element(id);
    return { context, manager, elements, element, document, events };
}
const video = { id: 8, video_id: 'abc', title: 'A "quoted" <title>', channel_name: 'Creator',
    category_name: '科技', thumbnail_url: 'https://example.test/thumb.jpg', url: 'https://youtube.com/watch?v=abc',
    duration: 'PT2M', published_at: '2026-10-01T08:00:00', view_count: 10, like_count: 0, comment_count: 0, tags: ['AI'] };

test('filtered empty results offer recovery instead of asking the user to synchronize', () => {
    const { manager, elements } = harness();
    manager.currentFilters.search = 'no match';
    manager.renderVideos([]);
    assert.match(elements['videos-grid'].innerHTML, /没有符合筛选条件的视频/);
    assert.match(elements['videos-grid'].innerHTML, /clearFilters/);
    assert.match(elements['videos-grid'].innerHTML, /showAllTime/);
    assert.doesNotMatch(elements['videos-grid'].innerHTML, /请先同步/);
    manager.currentFilters.search = '';
    manager.currentFilters.timePeriod = 'all';
    manager.renderVideos([]);
    assert.match(elements['videos-grid'].innerHTML, /尚未收录视频/);
});

test('empty pagination is 0–0 and requests outside the available pages do not run', () => {
    const { manager, elements } = harness();
    manager.updatePagination({ page: 1, per_page: 20, total: 0, pages: 0, has_prev: false, has_next: false });
    assert.equal(Number(elements['page-start'].textContent), 0);
    assert.equal(Number(elements['page-end'].textContent), 0);
    let loads = 0;
    manager.loadVideos = () => { loads++; };
    manager.goToPage(2);
    assert.equal(loads, 0);
    manager.updatePagination({ page: 1, per_page: 20, total: 21, pages: 2, has_prev: false, has_next: true });
    manager.goToPage(2);
    assert.equal(loads, 1);
    manager.goToPage(3);
    assert.equal(loads, 1);
});

test('failed list requests hide obsolete pagination and counts, with a retry action', async () => {
    const { manager, context, elements } = harness();
    elements['videos-count'].textContent = '126';
    context.api = { getVideos: async () => { throw new Error('offline'); } };
    await manager.loadVideos();
    assert.equal(elements.pagination.hidden, true);
    assert.equal(elements['videos-count'].textContent, '—');
    assert.match(elements['videos-grid'].innerHTML, /重试/);
    assert.equal(elements['videos-grid'].getAttribute('aria-busy'), 'false');
});

test('loading blocks pagination until the latest response is displayed', async () => {
    const { manager, context, elements } = harness();
    let resolve;
    context.api = { getVideos: () => new Promise(done => { resolve = done; }) };
    manager.totalPages = 2;
    const loading = manager.loadVideos();
    assert.equal(elements['next-page'].disabled, true);
    manager.goToPage(2);
    assert.equal(manager.currentPage, 1);
    resolve({ success: true, data: { videos: [], pagination: { page: 1, pages: 0, total: 0, per_page: 20 } } });
    await loading;
});

test('card actions are labelled and external text cannot become HTML or an inline handler', () => {
    const { manager } = harness();
    const html = manager.renderVideoCard(video);
    assert.match(html, /video-card-actions/);
    assert.match(html, /查看详情/);
    assert.match(html, /data-video-action="favorite"/);
    assert.doesNotMatch(html, /group-hover:opacity/);
    assert.match(html, /&lt;title&gt;/);
    assert.match(html, /&quot;quoted&quot;/);
    assert.doesNotMatch(html, /toggleFavorite\(/);
});

test('favorite state is exposed in visible text and aria-pressed', () => {
    const { manager, element } = harness();
    const button = element('favorite'), label = element('label'), icon = element('icon');
    button.querySelector = selector => selector === 'i' ? icon : label;
    manager.updateButtonState(button, true);
    assert.equal(label.textContent, '已收藏');
    assert.equal(button.getAttribute('aria-pressed'), 'true');
    manager.updateButtonState(button, false);
    assert.equal(label.textContent, '收藏');
    assert.equal(button.getAttribute('aria-pressed'), 'false');
});

function dialogHarness() {
    const h = harness();
    const modal = h.element('videoDetailModal', ['hidden']);
    h.element('videoModalContent');
    h.element('video-detail-content');
    const close = h.element('video-detail-close'), last = h.element('last-action'), trigger = h.element('trigger');
    modal.children = [close, last];
    trigger.focus();
    h.manager.setupEventListeners();
    return { ...h, modal, close, last, trigger };
}

test('detail dialog traps Tab, closes on Escape, unlocks scroll and restores focus', () => {
    const { manager, document, modal, close, last, trigger, events } = dialogHarness();
    manager.showVideoDetailModal();
    assert.equal(document.activeElement, close);
    assert.equal(document.body.style.overflow, 'hidden');
    last.focus();
    for (const handler of events.keydown || []) handler({ key: 'Tab', preventDefault() {} });
    assert.equal(document.activeElement, close);
    for (const handler of events.keydown || []) handler({ key: 'Tab', shiftKey: true, preventDefault() {} });
    assert.equal(document.activeElement, last);
    for (const handler of events.keydown || []) handler({ key: 'Escape', preventDefault() {} });
    assert.equal(modal.classList.contains('hidden'), true);
    assert.equal(document.activeElement, trigger);
    assert.equal(document.body.style.overflow, '');
});

test('closing a loading detail prevents a late response from reopening the dialog', async () => {
    const { manager, context, modal, elements } = dialogHarness();
    let resolve;
    context.api = { getVideo: () => new Promise(done => { resolve = done; }) };
    const request = manager.showVideoDetail(8);
    assert.equal(modal.classList.contains('hidden'), false, 'loading is visible and dismissible');
    manager.closeVideoDetailModal();
    resolve({ success: true, data: { video } });
    await request;
    assert.equal(modal.classList.contains('hidden'), true);
    assert.doesNotMatch(elements['video-detail-content'].innerHTML, /&lt;title&gt;/);
});

test('closing details returns to search if a list refresh removed the original trigger', () => {
    const { manager, document, element, trigger } = dialogHarness();
    const search = element('search-videos');
    manager.showVideoDetailModal();
    trigger.isConnected = false;
    manager.closeVideoDetailModal();
    assert.equal(document.activeElement, search);
});

test('a failed detail can be retried inside the same open dialog', async () => {
    const { manager, context, modal, elements } = dialogHarness();
    context.api = { getVideo: async () => { throw new Error('offline'); } };
    await manager.showVideoDetail(8);
    assert.equal(modal.classList.contains('hidden'), false);
    assert.match(elements['video-detail-content'].innerHTML, /重试/);
    context.api.getVideo = async () => ({ success: true, data: { video } });
    await manager.showVideoDetail(8);
    assert.match(elements['video-detail-content'].innerHTML, /&lt;title&gt;/);
});

test('notification messages display imported titles as text, not HTML', () => {
    const { context, element } = harness();
    const container = element('toast-container');
    context.setTimeout = () => 0;
    context.showToast('已收藏视频: <img src=x onerror=alert(1)>', 'success');
    assert.match(container.children[0].innerHTML, /&lt;img/);
    assert.doesNotMatch(container.children[0].innerHTML, /<img/);
});

test('extending the time range keeps the channel and search and restarts at page one', async () => {
    const { context, manager, element } = harness();
    element('time-period').value = '7';
    manager.currentFilters.search = 'AI';
    manager.currentFilters.channel = 4;
    manager.currentPage = 3;
    let params;
    context.api = { searchVideos: async input => {
        params = input;
        return { success: true, data: { videos: [], pagination: { page: 1, per_page: 20, total: 0, pages: 0 } } };
    } };
    manager.showAllTime();
    await Promise.resolve();
    assert.equal(params.page, 1);
    assert.equal(params.q, 'AI');
    assert.equal(params.channel_id, 4);
    assert.equal(params.time_period, undefined);
});

test('a newer detail response wins even if the earlier one finishes later', async () => {
    const { manager, context, elements } = dialogHarness();
    let resolveFirst;
    context.api = { getVideo: id => id === 8 ? new Promise(done => { resolveFirst = done; })
        : Promise.resolve({ success: true, data: { video: { ...video, title: 'Latest selection' } } }) };
    const earlier = manager.showVideoDetail(8);
    await manager.showVideoDetail(9);
    resolveFirst({ success: true, data: { video } });
    await earlier;
    assert.match(elements['video-detail-content'].innerHTML, /Latest selection/);
    assert.doesNotMatch(elements['video-detail-content'].innerHTML, /&lt;title&gt;/);
});

test('card favorite action still loads full detail, toggles state, and prevents duplicate submissions', async () => {
    const { manager, context, element, elements } = harness();
    const button = element('favorite'), label = element('label'), icon = element('icon');
    button.dataset = { id: '8', videoAction: 'favorite' };
    button.querySelector = selector => selector === 'i' ? icon : label;
    manager.currentVideos = [video];
    manager.setupEventListeners();
    let payload, resolveAdd, writes = 0;
    context.api = {
        getVideo: async () => ({ success: true, data: { video: { ...video, description: 'Full description' } } }),
        addFavorite: input => { payload = input; writes++; return new Promise(done => { resolveAdd = done; }); },
        removeFavorite: async id => { assert.equal(id, 'abc'); return { success: true }; },
    };
    elements['videos-grid'].click({ target: { closest: () => button } });
    await new Promise(setImmediate); // Drain the async detail request across the VM boundary.
    elements['videos-grid'].click({ target: { closest: () => button } });
    assert.equal(writes, 1);
    assert.equal(payload.title, 'A "quoted" <title>');
    assert.equal(payload.description, 'Full description');
    resolveAdd({ success: true });
    await new Promise(setImmediate);
    assert.equal(label.textContent, '已收藏');
    assert.equal(button.disabled, false);
    await manager.toggleFavorite('abc', video.title, button);
    assert.equal(label.textContent, '收藏');
});
