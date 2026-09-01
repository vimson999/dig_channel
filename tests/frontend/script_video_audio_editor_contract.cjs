const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const scriptPath = path.resolve(__dirname, '../../src/frontend/js/script-video-workflow.js');
const source = fs.readFileSync(scriptPath, 'utf8');
const context = vm.createContext({
    console,
    URL,
    URLSearchParams,
    document: { addEventListener() {} },
    window: { location: new URL('http://127.0.0.1:8888/script-video-workflow.html') },
    setTimeout,
    clearTimeout,
});

vm.runInContext(source, context, { filename: scriptPath });
const Page = vm.runInContext('ScriptVideoWorkflowPage', context);
const page = Object.create(Page.prototype);

page.workflow = {
    audio: { state: 'edit_ready', source_wav_path: '/workflow/audio/source.wav', wav_path: '/workflow/audio/stale.wav' },
};
assert.equal(
    page.audioReady(),
    false,
    'a source WAV awaiting user confirmation must not unlock Gemini or rendering',
);

page.workflow = {
    audio: { state: 'finalized', source_wav_path: '/workflow/audio/source.wav', wav_path: '/workflow/audio/final-001.wav' },
};
assert.equal(page.audioReady(), true, 'only the confirmed final WAV may unlock the next workflow stage');

console.log('script video audio editor: source/final readiness contract passed');
