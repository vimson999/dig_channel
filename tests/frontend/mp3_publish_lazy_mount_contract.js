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
    window: {},
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: scriptPath });

const Page = context.Mp3VideoWorkflowDetailPage;
const page = Object.create(Page.prototype);
const job = { job_id: 'job-1' };
const mounts = [];
context.document.getElementById = () => null;
page.activePublishPlatform = 'bilibili';
page.mountBilibiliPanel = (payload, draft) => mounts.push(['bilibili', payload, draft]);
page.mountDouyinPanel = (payload, draft) => mounts.push(['douyin', payload, draft]);
page.mountWechatChannelsPanel = (payload, draft) => mounts.push(['wechat_channels', payload, draft]);

assert.equal(typeof page.mountActivePublishPanel, 'function', 'detail page must expose one active-panel mount dispatcher');
page.mountActivePublishPanel(job, {
    bilibili: { title: 'B draft' },
    douyin: { title: 'D draft' },
    wechat_channels: { title: 'W draft' },
});
assert.deepEqual(mounts.map(([key]) => key), ['bilibili']);
assert.equal(mounts[0][2].title, 'B draft');

let captured = '';
let mounted = '';
page.els = { root: { querySelector: () => ({}) } };
page.job = job;
page.captureActivePublishPanelDraft = () => { captured = page.activePublishPlatform; };
page.mountActivePublishPanel = () => { mounted = page.activePublishPlatform; };
context.window.PlatformPublishTabs = class PlatformPublishTabs {
    constructor(options) {
        this.options = options;
    }
};
page.mountPublishTabs();
page.publishTabs.options.onChange('douyin');
assert.equal(captured, 'bilibili');
assert.equal(page.activePublishPlatform, 'douyin');
assert.equal(mounted, 'douyin');

let duplicateCredentialLoads = 0;
const container = {};
context.document.getElementById = () => container;
context.window.BilibiliPublishPanel = class BilibiliPublishPanel {
    constructor(options) {
        this.container = options.container;
        this.credentials = [];
        this.credentialsLoaded = false;
    }

    setJob() {
        if (!this.credentialsLoaded) duplicateCredentialLoads += 1;
    }

    readFormDraft() {
        return null;
    }
};
delete page.mountBilibiliPanel;
page.biliPanel = null;
page.biliCredentials = [{ id: 'credential-1', name: 'primary' }];
page.biliCredentialsError = '';
page.jobId = 'job-1';
page.apiRoot = () => 'http://127.0.0.1:5001';
page.request = async () => ({});
page.setNotice = () => {};
page.biliVideoOptions = () => [];
page.biliCoverOptions = () => [];
page.defaultBiliCoverKey = () => '';
page.mp3BilibiliAccountDefaults = () => ({});
page.defaultBiliTitle = () => '';
page.refreshBilibiliUploads = async () => {};
page.mountBilibiliPanel(job);
assert.equal(duplicateCredentialLoads, 0, 'the Bilibili panel must reuse credentials already loaded by the detail page');
assert.deepEqual(page.biliPanel.credentials, page.biliCredentials);

page.biliPanel.credentialsLoaded = false;
page.biliCredentials = [];
page.biliCredentialsError = '请求超时（15s）';
page.mountBilibiliPanel(job);
assert.equal(
    duplicateCredentialLoads,
    0,
    'a timed-out detail-page credential request must not trigger an automatic second owner request',
);
assert.equal(page.biliPanel.credentialsLoaded, true);
assert.equal(page.biliPanel.credentialsError, page.biliCredentialsError);

console.log('mp3 publish lazy mount contract: ok');
