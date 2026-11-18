const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const frontendRoot = path.resolve(__dirname, '../../src/frontend');
const scriptPath = path.join(frontendRoot, 'js/doubao-tts.js');
const pagePath = path.join(frontendRoot, 'tts.html');

const DoubaoTTSPage = require(scriptPath);

const starts = [];
let stops = 0;
const page = Object.create(DoubaoTTSPage.prototype);
page.polling = {
    start() { starts.push('start'); },
    stop() { stops += 1; },
};

page.updatePolling([{ status: 'processing' }]);
page.updatePolling([{ status: 'pending' }]);
assert.equal(starts.length, 1, 'active jobs start exactly one visibility-aware poller');

page.updatePolling([{ status: 'completed' }, { status: 'failed' }]);
assert.equal(stops, 1, 'terminal history stops polling instead of refreshing forever');

page.apiBase = 'http://127.0.0.1:5001/api/v2/tts';
const markup = page.renderTaskItem({
    id: 'task-1',
    title: 'demo',
    status: 'completed',
    config: {},
    segment_count: 1,
    success_count: 1,
    first_audio_segment: {
        sequence: 1,
        audio_url: '/api/v2/tts/task/task-1/segment/1/audio',
    },
});
assert.match(markup, /<audio[^>]+preload="none"/, 'history audio must not fetch until playback');

const html = fs.readFileSync(pagePath, 'utf8');
assert.ok(
    html.indexOf('js/visibility-polling.js') < html.indexOf('js/doubao-tts.js'),
    'the shared visibility lifecycle must load before the TTS page',
);

console.log('doubao-tts: active-only visible polling and lazy audio passed');
