(function registerZsxqInsightsRefresh(root, factory) {
    const helper = factory();
    if (typeof module === 'object' && module.exports) module.exports = helper;
    if (root) root.ZsxqInsightsRefresh = helper;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
    const POLL_DELAYS_MS = Object.freeze([500, 1000, 2000, 4000]);
    const defaultWait = (delayMs) => new Promise((resolve) => setTimeout(resolve, delayMs));

    async function refresh(options) {
        const url = String(options.url || '');
        const state = String(options.refreshState || 'client-required');
        const request = options.request;
        const wait = options.wait || defaultWait;
        const isCurrent = options.isCurrent || (() => true);
        const onFresh = options.onFresh || (() => {});
        if (typeof request !== 'function') throw new TypeError('request is required');

        if (!['scheduled', 'in-flight'].includes(state)) {
            if (!isCurrent()) return 'superseded';
            const separator = url.includes('?') ? '&' : '?';
            const result = await request(`${url}${separator}_refresh=1`);
            if (!result?.ok || !result.payload?.success) return 'failed';
            if (!isCurrent()) return 'superseded';
            onFresh(result.payload);
            return 'fresh';
        }

        for (const delayMs of POLL_DELAYS_MS) {
            if (!isCurrent()) return 'superseded';
            await wait(delayMs);
            if (!isCurrent()) return 'superseded';
            const result = await request(url);
            if (!result?.ok || !result.payload?.success) return 'failed';
            if (result.cacheState === 'STALE') continue;
            if (!isCurrent()) return 'superseded';
            onFresh(result.payload);
            return 'fresh';
        }
        return 'stale';
    }

    return {POLL_DELAYS_MS, refresh};
}));
