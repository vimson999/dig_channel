const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { navigationHarness } = require('./helpers/shared-navigation-harness.cjs');

// Independent inventory: a missing/moved URL must not silently remove an existing workflow.
const existingURLs = `index channels videos rankings favorites trending trending-radar indie-navigator zsxq-insights zsxq-analysis bilibili-intelligence zsxq-lab zsxq-system-sync publisher video-editor bilibili-publisher downloads audio-transcription batch-transcription video-auto-generator podcast-visualizer report-video-factory report-audio-workflow-test report-script-material-workflow-test report-stick-figure-video-workflow script-video-workflow report-visual-plan-test report-top10-workflow-test mp3-video-workflow tts doubao-browser-tts-test thumbnail-workbench finance-cover-generator closed-door-card-cover-generator pdf-tools pdf-merge-workflow celery-monitor content-retention-cleanup content-pipeline-submit platform-credentials zsxq-history-api-clients douyin-publish-test wechat-channels-publish-test document-converter word-long-image pdf-scroll-video mhtml-pdf watermark student-id-generator thumbnail-generator thumbnail-generator-classic gemini-test gemini-browser-test`.split(' ').map(p => `${p}.html`).concat('http://127.0.0.1:5005/batch').sort();
const leaves = nav => nav.getMenuStructure().flatMap(s => s.items || [s]);
const hrefs = html => [...html.matchAll(/href="([^"]+)"/g)].map(m => m[1]);

test('navigation stylesheet resolves beside the script, not beneath nested page directories', () => {
    for (const [src, expected] of [
        ['http://127.0.0.1:8888/js/shared-navigation.js?v=old', 'http://127.0.0.1:8888/css/shared-navigation.css'],
        ['file:///project/frontend/js/shared-navigation.js', 'file:///project/frontend/css/shared-navigation.css']
    ]) {
        const { nav } = navigationHarness('/tests/navigation_test.html', src);
        const css = nav.getNavigationHTML().match(/<link rel="stylesheet" href="([^"]+)"/)[1];
        assert.equal(css.split('?')[0], expected);
    }
});

test('desktop and mobile preserve every existing destination exactly once', () => {
    const { nav } = navigationHarness();
    assert.deepEqual(Array.from(leaves(nav), item => item.href).sort(), existingURLs);
    const desktop = hrefs(nav.getMenuItems() + nav.getSystemMenu());
    assert.deepEqual(desktop.sort(), existingURLs);
    assert.deepEqual(hrefs(nav.getMobileMenuItems()).sort(), existingURLs);
});

test('current page and displayed title come from the same catalog, including Celery', () => {
    for (const href of existingURLs.filter(u => !u.startsWith('http'))) {
        const { nav } = navigationHarness('/' + href);
        const entry = leaves(nav).find(e => e.href === href);
        assert.equal(nav.currentPage, entry.id, href);
        if (href !== 'index.html') assert.equal(nav.getPageTitle(), entry.name, href);
    }
});

test('legacy aliases keep their parent navigation; unknown pages never highlight home', () => {
    for (const [url, expected] of [
        ['/zsxq-auto-publish.html', 'zsxq-system-sync'],
        ['/report-stick-figure-video-workflow-detail.html', 'report-stick-figure-video-workflow'],
        ['/watermark', 'watermark'], ['/', 'dashboard'], ['/not-a-page.html', null]
    ]) assert.equal(navigationHarness(url).nav.currentPage, expected, url);
});

test('production, input materials and operations appear in their intended sections', () => {
    const { nav } = navigationHarness();
    const parentOf = id => nav.getMenuStructure().find(s => s.items?.some(i => i.id === id));
    assert.equal(parentOf('downloads').name, '数据与素材');
    assert.equal(parentOf('video-editor').name, '内容制作');
    assert.equal(parentOf('pdf-scroll-video').name, '内容制作');
    assert.equal(parentOf('zsxq-system-sync').name, '发布与任务');
    assert.equal(parentOf('celery-monitor').name, '系统管理');
    assert.equal(parentOf('celery-monitor').utility, true);
    const html = nav.getMenuItems();
    for (const group of ['音频与文字', '研报制作', '视频制作', '封面设计', '实验室', '旧版工具']) assert.ok(html.includes(group), group);
});

test('navigation markup exposes disclosure state, current links and safe external destinations', () => {
    const { nav } = navigationHarness('/celery-monitor.html');
    const html = nav.getNavigationHTML();
    assert.match(html, /aria-expanded="false"/);
    assert.match(html, /aria-controls="nav-panel-/);
    assert.match(html, /href="celery-monitor.html"[^>]*aria-current="page"/);
    assert.doesNotMatch(html, /href="#"/);
    assert.match(html, /target="_blank" rel="noopener noreferrer"/);
    assert.match(html, /<dialog[^>]+id="mobile-menu-panel"/);
    assert.match(html, /<dialog[^>]+id="navigation-help"/);
});

test('icon-only system and help buttons remain visible on pages using local icon fallbacks', () => {
    const { nav } = navigationHarness();
    const html = nav.getNavigationHTML();
    for (const page of ['zsxq-lab', 'report-video-factory']) {
        const css = fs.readFileSync(path.join(__dirname, '../../src/frontend/css', `${page}.css`), 'utf8');
        for (const label of ['系统管理', '导航帮助']) {
            const button = [...html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g)]
                .map(m => m[0]).find(b => b.includes(`aria-label="${label}"`));
            const icon = button.match(/class="fas (fa-[a-z-]+)"/)[1];
            assert.ok(css.includes(`.${icon}::before`), `${page}: ${label} needs a local glyph`);
        }
    }
});

function disclosureHarness() {
    const h = navigationHarness();
    const a = h.element('trigger-a'), b = h.element('trigger-b');
    const pa = h.element('panel-a'), pb = h.element('panel-b');
    a.setAttribute('aria-controls', pa.id); b.setAttribute('aria-controls', pb.id);
    a.setAttribute('aria-expanded', 'false'); b.setAttribute('aria-expanded', 'false');
    const first = h.element('first-link'); pa.children = [first];
    h.document.querySelectorAll = selector => selector === '[data-nav-trigger]' ? [a, b] : [];
    h.nav.setupEventListeners();
    return { ...h, a, b, pa, pb, first };
}

test('opening a dropdown centers it below its own trigger, not at the header right edge', () => {
    const { a, pa } = disclosureHarness();
    a.getBoundingClientRect = () => ({ left: 520, width: 120 });
    pa.getBoundingClientRect = () => ({ width: 360 });
    a.fire('click');
    assert.equal(pa.style.left, '400px');
    assert.equal(pa.style.right, 'auto');
});

test('dropdown placement clamps to both viewport edges and accounts for centered header offset', () => {
    for (const [left, triggerWidth, panelWidth, viewport, origin, expected] of [
        [1200, 40, 360, 1280, 0, '904px'],
        [0, 40, 360, 1280, 0, '16px'],
        [960, 120, 680, 2000, 280, '400px']
    ]) {
        const { a, pa, document } = disclosureHarness();
        a.getBoundingClientRect = () => ({ left, width: triggerWidth });
        pa.getBoundingClientRect = () => ({ width: panelWidth });
        pa.offsetParent.getBoundingClientRect = () => ({ left: origin });
        document.documentElement.clientWidth = viewport;
        a.fire('click');
        assert.equal(pa.style.left, expected);
    }
});

test('reopening recalculates placement after the trigger moves', () => {
    const { a, pa } = disclosureHarness();
    let left = 200;
    a.getBoundingClientRect = () => ({ left, width: 120 });
    pa.getBoundingClientRect = () => ({ width: 360 });
    a.fire('click'); assert.equal(pa.style.left, '80px');
    a.fire('click'); left = 600;
    a.fire('click'); assert.equal(pa.style.left, '480px');
});

test('click toggles menus, opening another closes the previous, outside click dismisses', () => {
    const { a, b, pa, pb, events } = disclosureHarness();
    a.fire('click'); assert.equal(pa.hidden, false); assert.equal(a.getAttribute('aria-expanded'), 'true');
    b.fire('click'); assert.equal(pa.hidden, true); assert.equal(pb.hidden, false);
    b.fire('click'); assert.equal(pb.hidden, true);
    a.fire('click');
    for (const fn of events.click) fn({ target: {} });
    assert.equal(pa.hidden, true);
});

test('ArrowDown enters the menu; Escape dismisses and returns focus to its trigger', () => {
    const { a, pa, first, document, events } = disclosureHarness();
    a.fire('keydown', { key: 'ArrowDown', preventDefault() {} });
    assert.equal(pa.hidden, false); assert.equal(document.activeElement, first);
    for (const fn of events.keydown) fn({ key: 'Escape', preventDefault() {} });
    assert.equal(pa.hidden, true); assert.equal(document.activeElement, a);
});

test('Tab focus leaving an open disclosure closes it without stealing focus', () => {
    const { a, pa, events } = disclosureHarness();
    a.fire('click');
    for (const fn of events.focusin) fn({ target: {} });
    assert.equal(pa.hidden, true);
});

test('mobile dialog closes and restores scroll and trigger focus', () => {
    const h = navigationHarness();
    const toggle = h.element('mobile-menu-toggle'), panel = h.element('mobile-menu-panel');
    const close = h.element('mobile-menu-close');
    h.nav.setupEventListeners();
    toggle.fire('click');
    assert.equal(panel.open, true); assert.equal(h.document.body.style.overflow, 'hidden');
    close.fire('click'); panel.fire('close');
    assert.equal(panel.open, false); assert.equal(h.document.body.style.overflow, '');
    assert.equal(h.document.activeElement, toggle);
});
