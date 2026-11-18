const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const htmlPath = path.join(root, 'src', 'frontend', 'content-pipeline-submit.html');
const scriptPath = path.join(root, 'src', 'frontend', 'js', 'content-pipeline-submit.js');
const html = fs.readFileSync(htmlPath, 'utf8');
const source = fs.readFileSync(scriptPath, 'utf8').replace(
    /\}\)\(\);\s*$/,
    'globalThis.ContentPipelineHistoryTestHooks = { state, els, renderSubmissionHistory }; })();',
);

assert(html.includes('id="submission-history"'), 'the right rail must expose the web submission history list');
assert(html.includes('id="history-refresh-btn"'), 'the history card must expose manual refresh');
assert(html.includes('id="history-load-more-btn"'), 'the history card must expose progressive loading');

const context = {
    console,
    document: { addEventListener() {} },
    window: {},
    Intl,
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: scriptPath });

const hooks = context.ContentPipelineHistoryTestHooks;
hooks.els.submissionHistory = { innerHTML: '' };
hooks.els.historySummary = { textContent: '' };
hooks.els.historyRefreshBtn = { disabled: false };
hooks.els.historyLoadMoreBtn = {
    disabled: false,
    classList: { toggle() {} },
};
hooks.state.history.items = [
    {
        submission_id: 'submission-1',
        submitted_at: '2026-09-12T06:30:00Z',
        input_mode: 'folder',
        media_types: ['pdf', 'mp3'],
        status: 'processing',
        progress: 45,
        tasks: [
            { kind: 'pdf', status: 'processing', progress: 45, current_step: 'publish', files_count: 2, success_count: 1, failed_count: 1 },
            { kind: 'mp3', status: 'completed', progress: 100, current_step: 'done', files_count: 1, success_count: 1, failed_count: 0 },
        ],
    },
];
hooks.state.history.hasMore = true;
hooks.state.history.loading = false;
hooks.state.history.error = '';
hooks.renderSubmissionHistory();

assert.match(hooks.els.submissionHistory.innerHTML, /处理中/, 'processing submissions must have a visible status label');
assert.match(hooks.els.submissionHistory.innerHTML, /PDF/, 'grouped submissions must show the PDF task');
assert.match(hooks.els.submissionHistory.innerHTML, /MP3/, 'grouped submissions must show the MP3 task');
assert.match(hooks.els.submissionHistory.innerHTML, /文件夹投递/, 'history must identify the submission input mode');
assert.match(hooks.els.submissionHistory.innerHTML, /45%/, 'history must show submission progress');
assert.match(hooks.els.submissionHistory.innerHTML, /清洗成功 1 · 清洗失败 1/, 'history must label cleaning file counts');

hooks.state.history.items = [{
    submission_id: 'submission-failed-publish',
    submitted_at: '2026-11-13T06:43:00Z',
    input_mode: 'folder',
    media_types: ['mp3'],
    status: 'failed',
    progress: 100,
    tasks: [{
        kind: 'mp3', status: 'failed', clean_status: 'completed', publish_status: 'failed',
        progress: 100, files_count: 28, success_count: 28, failed_count: 0,
        error: '两个账号上传均返回 HTTP 401 <auth>',
    }],
}];
hooks.renderSubmissionHistory();
assert.match(hooks.els.submissionHistory.innerHTML, /清洗已完成 · 知识星球发布失败/);
assert.match(hooks.els.submissionHistory.innerHTML, /清洗成功 28 · 清洗失败 0/);
assert.match(hooks.els.submissionHistory.innerHTML, /HTTP 401 &lt;auth&gt;/);

console.log('content pipeline submission history contract: ok');
