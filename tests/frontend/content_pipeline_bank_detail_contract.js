const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function main() {
    const root = path.resolve(__dirname, '..', '..');
    const script = fs.readFileSync(path.join(root, 'src/frontend/js/content-pipeline-bank-detail.js'), 'utf8');
    const elements = {};
    const requests = [];
    let initialize;
    const context = {
        Date, Intl, URL, URLSearchParams, AbortController, console,
        document: {
            addEventListener(_event, callback) { initialize = callback; },
            getElementById(id) {
                return elements[id] ||= {
                    value: '', textContent: '', innerHTML: '', disabled: false, handlers: {},
                    addEventListener(event, callback) { this.handlers[event] = callback; },
                };
            },
        },
        window: {
            location: { search: '?bank=高盛&from=2026-10-14&to=2026-10-15', pathname: '/content-pipeline-bank-detail.html' },
            history: { replaceState() {} }, API_BASE_URL: '/api/v1',
        },
        fetch: async (url) => {
            const parsed = new URL(url, 'http://localhost');
            requests.push(parsed);
            const page = Number(parsed.searchParams.get('page'));
            return {
                ok: true,
                json: async () => ({ success: true, data: {
                    investment_bank: '高盛', page, page_size: 25, total_pages: 2, total_items: 26,
                    has_more: page === 1, totals: { pdf: 26, mp3: 0, total: 26 },
                    items: [{ filename: page === 1 ? '<script>report.pdf' : 'second-page.pdf',
                        kind: 'pdf', count: 1, published_at: '2026-10-15T01:00:00+08:00',
                        publish_url: page === 1 ? 'javascript:alert(1)' : 'https://wx.zsxq.com/dweb2/index/topic_detail/12345678901234' }],
                } }),
            };
        },
    };
    vm.runInNewContext(script, context);
    initialize();
    const settle = () => new Promise((resolve) => setImmediate(resolve));
    await settle();
    assert.equal(requests.length, 1);
    assert.equal(requests[0].pathname, '/api/v1/content-pipeline/web-submissions/files');
    assert.equal(requests[0].searchParams.get('bank'), '高盛');
    assert.equal(requests[0].searchParams.get('from'), '2026-10-14');
    assert.equal(elements['detail-title'].textContent, '高盛 · 发布详情');
    assert.equal(elements['detail-total'].textContent, '26');
    assert.match(elements['detail-table-body'].innerHTML, /&lt;script&gt;report.pdf/);
    assert.doesNotMatch(elements['detail-table-body'].innerHTML, /javascript:/);
    assert.equal(elements['detail-next'].disabled, false);
    elements['detail-next'].handlers.click();
    await settle();
    assert.equal(requests[1].searchParams.get('page'), '2');
    assert.match(elements['detail-table-body'].innerHTML, /second-page.pdf/);
    assert.match(elements['detail-table-body'].innerHTML, /noopener noreferrer/);
    assert.equal(elements['detail-next'].disabled, true);
    elements['detail-from'].value = '2026-10-15';
    elements['detail-kind'].value = 'pdf';
    elements['detail-filter-form'].handlers.submit({ preventDefault() {} });
    await settle();
    assert.equal(requests[2].searchParams.get('page'), '1');
    assert.equal(requests[2].searchParams.get('from'), '2026-10-15');
    assert.equal(requests[2].searchParams.get('kind'), 'pdf');
    assert.match(elements['detail-back'].href, /from=2026-10-15/);
    elements['detail-from'].value = '2026-10-16';
    elements['detail-filter-form'].handlers.submit({ preventDefault() {} });
    await settle();
    assert.equal(requests.length, 3, 'invalid date range must not issue a request');
    assert.match(elements['detail-status'].textContent, /开始日期不能晚于结束日期/);
    console.log('content pipeline bank detail contract: ok');
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
