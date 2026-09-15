const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

test('home page no longer exposes retired creative-workshop quick entries', () => {
    const page = fs.readFileSync(path.resolve(__dirname, '../../src/frontend/index.html'), 'utf8');
    for (const href of ['thumbnail-workbench.html', 'finance-cover-generator.html', 'video-auto-generator.html']) {
        assert.doesNotMatch(page, new RegExp(`href=["']${href}["']`));
    }
    assert.doesNotMatch(page, /创意工坊快捷入口/);
    assert.match(page, /数据趋势图表/);
});
