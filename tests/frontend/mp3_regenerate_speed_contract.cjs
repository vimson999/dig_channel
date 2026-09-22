const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const scriptPath = path.join(projectRoot, 'src', 'frontend', 'js', 'mp3-video-workflow-detail.js');
const source = fs.readFileSync(scriptPath, 'utf8').replace(
    /document\.addEventListener\('DOMContentLoaded',[\s\S]*$/,
    'globalThis.Mp3VideoWorkflowDetailPage = Mp3VideoWorkflowDetailPage;',
);
const context = {
    console,
    document: {},
    window: { setTimeout: (callback) => callback() },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: scriptPath });

const Page = context.Mp3VideoWorkflowDetailPage;

function makePage() {
    const page = Object.create(Page.prototype);
    const errors = new Set(['hidden']);
    page.jobId = 'job-1';
    page.job = { plan: { audio: { start_seconds: 27, end_seconds: 891, speed: 1.2 } } };
    page.els = {
        regenerateStart: { value: '', focus() {}, select() {} },
        regenerateEnd: { value: '' },
        regenerateSpeed: { value: '' },
        regenerateError: {
            textContent: '',
            classList: {
                add: (name) => errors.add(name),
                remove: (name) => errors.delete(name),
            },
        },
        regenerateDialog: { showModal() {}, close() {} },
        regenerateSubmit: { disabled: false, innerHTML: 'submit' },
    };
    page.render = () => {};
    page.scheduleAutoRefresh = () => {};
    page.setNotice = () => {};
    return { page, errors };
}

async function testEditedSpeedIsSubmitted() {
    const { page } = makePage();
    let submitted;
    page.request = async (_url, options) => {
        submitted = JSON.parse(options.body);
        return { data: page.job };
    };

    page.regenerateVideos(null);
    assert.equal(page.els.regenerateSpeed.value, '1.2', 'dialog must preload the prior speed');
    page.els.regenerateSpeed.value = '1.5';
    await page.submitRegenerateVideos();

    assert.equal(submitted.start_seconds, 27);
    assert.equal(submitted.end_seconds, 891);
    assert.equal(submitted.speed, 1.5, 'the edited speed must reach the API request');
}

async function testInvalidSpeedNeverSubmits() {
    const { page, errors } = makePage();
    let requests = 0;
    page.request = async () => { requests += 1; return { data: page.job }; };
    page.regenerateVideos(null);
    page.els.regenerateSpeed.value = '2.1';

    await page.submitRegenerateVideos();

    assert.equal(requests, 0);
    assert.equal(errors.has('hidden'), false);
    assert.match(page.els.regenerateError.textContent, /倍速/);
}

Promise.resolve()
    .then(testEditedSpeedIsSubmitted)
    .then(testInvalidSpeedNeverSubmits)
    .then(() => console.log('mp3 regenerate speed contract: ok'))
    .catch((error) => {
        console.error(error);
        process.exitCode = 1;
    });
