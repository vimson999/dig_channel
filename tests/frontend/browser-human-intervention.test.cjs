const assert = require('node:assert/strict');
const path = require('node:path');

const helperPath = path.resolve(
    __dirname,
    '../../src/frontend/js/browser-human-intervention.js',
);
const helper = require(helperPath);

const sent = helper.resolve({
    status: 'blocked_by_captcha',
    captcha_blocked: {
        requires_human_action: true,
        workflow_url: 'http://localhost:5001/report-audio-workflow-detail.html?job_id=parent-1',
        page_url: 'https://www.doubao.com/chat/local_1',
        profile: '/runtime/browser_profiles/doubao',
        resolution_steps: ['完成验证', '点击“从失败处续跑”'],
    },
    notifications: {
        doubao_captcha_blocked: {
            sent: true,
            sent_at: '2026-08-12T08:34:14Z',
            recipient_hint: 'o***@example.test',
        },
    },
});

assert.equal(sent.state, 'sent');
assert.match(sent.label, /已邮件通知/);
assert.equal(sent.recipientHint, 'o***@example.test');
assert.equal(sent.pageUrl, 'https://www.doubao.com/chat/local_1');
assert.match(sent.resolutionSteps[1], /从失败处续跑/);

const cooldown = helper.resolve({
    status: 'blocked_by_captcha',
    notifications: {
        doubao_captcha_blocked: {
            sent: false,
            reason: 'cooldown',
            retry_after_seconds: 900,
        },
    },
});
assert.equal(cooldown.state, 'cooldown');
assert.equal(cooldown.label, '邮件已在冷却期内通知');
assert.equal(cooldown.retryAfterSeconds, 900);

const missingConfig = helper.resolve({
    status: 'blocked_by_captcha',
    captcha_blocked: {requires_human_action: true},
    notifications: {
        doubao_captcha_blocked: {
            sent: false,
            reason: 'missing_config',
            missing: ['WORKFLOW_EMAIL_PASSWORD'],
        },
    },
}, {
    workflowUrl: 'report-audio-workflow-detail.html?job_id=parent-2',
});
assert.equal(missingConfig.state, 'failed');
assert.equal(missingConfig.label, '邮件通知未配置');
assert.equal(
    missingConfig.workflowUrl,
    'report-audio-workflow-detail.html?job_id=parent-2',
);
assert.match(missingConfig.resolutionSteps[2], /从失败处续跑/);

const rawDetail = helper.resolve({
    status: 'blocked_by_captcha',
    raw_result: {
        captcha_blocked: {
            requires_human_action: true,
            screenshot_path: '/tmp/doubao-captcha.png',
        },
        notifications: {
            doubao_captcha_blocked: {sent: false, reason: 'send_failed'},
        },
    },
});
assert.equal(rawDetail.state, 'failed');
assert.equal(rawDetail.label, '邮件通知失败');
assert.equal(rawDetail.screenshotPath, '/tmp/doubao-captcha.png');

assert.equal(helper.resolve({status: 'completed'}), null);

console.log('browser-human-intervention: 5 scenarios passed');
