/* 使用真实页面脚本验证轮询生命周期与结果页签。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

test('polling pauses when hidden, is single flight, and stops at completion', async () => {
    const nodes = new Map();
    function node() {
        return {textContent: '', value: '', hidden: false, listeners: {},
            classList: {add() {}, remove() {}},
            addEventListener(name, fn) {this.listeners[name] = fn;},
            replaceChildren() {}, append() {}, setAttribute() {}, removeAttribute() {}};
    }
    const document = {...node(), hidden: false,
        getElementById(id) {if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);},
        createElement: node};
    const timers = new Map();
    let sequence = 0, calls = 0, resolvePending, copied;
    let job = {id: 'job', status: 'processing', current_step: 'transcribing', task_title: '测试.wav'};
    let delayed = false;
    const reply = data => ({ok: true, json: async () => ({success: true, data})});
    const fetch = async url => {
        if (url.endsWith('/status')) return reply({max_upload_bytes: 2147483648});
        if (url.includes('/jobs?page=')) return reply({items: [], has_more: false});
        if (url.endsWith('/text')) return {ok: true, text: async () => '测试结果'};
        if (url.endsWith('/srt')) return {ok: true, text: async () => '1\n00:00:00,000 --> 00:00:01,000\n测试结果\n'};
        calls++;
        if (delayed) return new Promise(resolve => {resolvePending = () => resolve(reply({...job}));});
        return reply({...job});
    };
    const context = {document, fetch, URL, location: {href: 'http://localhost/audio-transcription.html?job=job'},
        window: {...node(), API_BASE_URL: '/api/v1', history: {replaceState() {}}},
        setTimeout(fn, delay) {assert.equal(delay, 5000); timers.set(++sequence, fn); return sequence;},
        clearTimeout(id) {timers.delete(id);},
        navigator: {clipboard: {async writeText(text) {copied = text;}}}, console};
    const flush = () => new Promise(resolve => setImmediate(resolve));
    vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../src/frontend/js/audio-transcription.js'), 'utf8'), context);
    await flush();
    assert.equal(calls, 1);
    assert.equal(timers.size, 1);
    document.hidden = true;
    document.listeners.visibilitychange();
    assert.equal(timers.size, 0);
    document.hidden = false;
    delayed = true;
    document.listeners.visibilitychange();
    await flush();
    document.listeners.visibilitychange();
    await flush();
    assert.equal(calls, 2, '第二个事件不能重叠发起请求');
    job = {...job, status: 'completed', current_step: 'completed'};
    resolvePending();
    await flush();
    assert.equal(timers.size, 0);
    assert.equal(nodes.get('output').value, '测试结果');
    assert.equal(nodes.get('results').hidden, false);
    await nodes.get('copy').listeners.click();
    assert.equal(copied, '测试结果');
    assert.match(nodes.get('download').href, /\/text\?download=1$/);
    await nodes.get('srt-tab').listeners.click();
    assert.match(nodes.get('output').value, /00:00:00,000 --> 00:00:01,000/);
    await nodes.get('copy').listeners.click();
    assert.equal(copied, nodes.get('output').value);
    assert.match(nodes.get('download').href, /\/srt\?download=1$/);
    assert.equal(calls, 2);
    assert.equal(timers.size, 0);
});
