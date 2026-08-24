(function registerBrowserHumanIntervention(root, factory) {
    const helper = factory();
    if (typeof module === 'object' && module.exports) module.exports = helper;
    if (root) root.BrowserHumanIntervention = helper;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
    const DEFAULT_STEPS = [
        '使用通知中指定的浏览器 Profile 打开豆包页面',
        '完成验证码或安全验证，并确认页面可以正常发起朗读',
        '返回工作流详情页，点击“从失败处续跑”',
    ];

    function object(value) {
        return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    }

    function notificationState(notification) {
        if (notification.sent === true) {
            return {state: 'sent', label: '已邮件通知'};
        }
        const reason = String(notification.reason || '').trim().toLowerCase();
        if (reason === 'cooldown') {
            return {state: 'cooldown', label: '邮件已在冷却期内通知'};
        }
        if (reason === 'missing_config') {
            return {state: 'failed', label: '邮件通知未配置'};
        }
        if (reason === 'disabled' || reason.startsWith('unsupported_provider:')) {
            return {state: 'failed', label: '邮件通知未启用'};
        }
        if (reason || notification.sent === false) {
            return {state: 'failed', label: '邮件通知失败'};
        }
        return {state: 'pending', label: '等待邮件通知结果'};
    }

    function resolve(child, options = {}) {
        const item = object(child);
        const rawResult = object(item.raw_result);
        const captcha = Object.keys(object(item.captcha_blocked)).length
            ? object(item.captcha_blocked)
            : object(rawResult.captcha_blocked);
        const directNotifications = object(item.notifications);
        const rawNotifications = object(rawResult.notifications);
        const notification = Object.keys(object(directNotifications.doubao_captcha_blocked)).length
            ? object(directNotifications.doubao_captcha_blocked)
            : object(rawNotifications.doubao_captcha_blocked);
        const status = String(item.status || '').trim().toLowerCase();
        const isCaptchaBlock = status === 'blocked_by_captcha'
            || captcha.requires_human_action === true
            || captcha.reason === 'captcha'
            || Object.keys(notification).length > 0;
        if (!isCaptchaBlock) return null;

        const notificationMeta = notificationState(notification);
        const steps = Array.isArray(captcha.resolution_steps)
            ? captcha.resolution_steps.map((step) => String(step || '').trim()).filter(Boolean)
            : [];
        return {
            ...notificationMeta,
            sentAt: String(notification.sent_at || ''),
            recipientHint: String(notification.recipient_hint || ''),
            retryAfterSeconds: Number(notification.retry_after_seconds || 0),
            notificationReason: String(notification.reason || ''),
            notificationError: String(notification.error || ''),
            missing: Array.isArray(notification.missing) ? notification.missing.map(String) : [],
            workflowUrl: String(captcha.workflow_url || options.workflowUrl || ''),
            pageUrl: String(captcha.page_url || ''),
            screenshotPath: String(captcha.screenshot_path || ''),
            profile: String(captcha.profile || ''),
            workerId: String(captcha.worker_id || ''),
            message: String(captcha.message || item.error || ''),
            resolutionSteps: steps.length ? steps : DEFAULT_STEPS.slice(),
        };
    }

    return {resolve};
}));
