const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const scriptPath = path.join(root, 'src', 'frontend', 'js', 'content-pipeline-submit.js');
const source = fs.readFileSync(scriptPath, 'utf8').replace(
    /\}\)\(\);\s*$/,
    'globalThis.ContentPipelineSubmitTestHooks = { state, els, effectiveFiles }; })();',
);

const context = {
    console,
    document: { addEventListener() {} },
    window: {},
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: scriptPath });

const hooks = context.ContentPipelineSubmitTestHooks;
hooks.state.mode = 'folder';
hooks.state.files = [
    { filename: '根目录.mp3', relativePath: '0821手动下载PDF/根目录.mp3', mediaType: 'mp3', bytes: 1 },
    { filename: '子目录.pdf', relativePath: '0821手动下载PDF/0829/子目录.pdf', mediaType: 'pdf', bytes: 1 },
];
hooks.els.includeSubdirectories = { checked: false };

const accepted = hooks.effectiveFiles();
assert.deepEqual(
    accepted.map((item) => item.filename),
    ['根目录.mp3'],
    'the browser-added selected-folder prefix must not make root files look nested',
);

console.log('content pipeline folder relative-path contract: ok');
