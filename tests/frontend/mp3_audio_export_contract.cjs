const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/frontend/js/mp3-video-workflow.js', 'utf8').replace(
    /document\.addEventListener\('DOMContentLoaded',[\s\S]*$/, 'globalThis.Page = Mp3VideoWorkflowPage;');
const fields = {
    'audio-export-btn': {}, 'audio-export-result': { replaceChildren(link) { this.link = link; } },
    'audio-export-start': { value: '00:01:00' }, 'audio-export-end': { value: '00:03:00' },
    'audio-export-speed': { value: '1.5' },
};
let request;
const context = {
    console, document: {getElementById: id => fields[id], createElement: () => ({click() { this.clicked = true; }})},
    URL: {createObjectURL: () => 'blob:audio', revokeObjectURL() {}},
    fetch: async (url, options) => {
        request = {url, options};
        return {ok: true, blob: async () => ({})};
    },
};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
    const page = Object.create(context.Page.prototype);
    page.apiRoot = () => '';
    page.headers = () => ({});
    await page.exportAudio();
    assert.equal(request, undefined);
    assert.match(fields['audio-export-result'].textContent, /选择音频/);
    page.selectedAudioAsset = {attachment_id:'pipeline:asset', filename:'研究.mp3'};
    await page.exportAudio();
    assert.equal(request.url, '/api/mp3-video-workflows/audio-export');
    assert.deepEqual(JSON.parse(request.options.body), {start:'00:01:00', end:'00:03:00', speed:'1.5', audio_asset_id:'pipeline:asset'});
    assert.equal(fields['audio-export-result'].link.download, '研究_剪辑.mp3');
    assert.equal(fields['audio-export-result'].link.clicked, true);
    assert.equal(fields['audio-export-btn'].disabled, false);
    console.log('audio export browser contract passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
