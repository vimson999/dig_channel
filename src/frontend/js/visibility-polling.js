(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.VisibilityPolling = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    function create(options = {}) {
        const documentRef = options.documentRef || document;
        const windowRef = options.windowRef || window;
        const setIntervalFn = options.setIntervalFn || setInterval;
        const clearIntervalFn = options.clearIntervalFn || clearInterval;
        const intervalMs = Math.max(1000, Number(options.intervalMs) || 15000);
        const poll = options.poll;
        const onError = options.onError || (() => {});
        let timerId = null;
        let started = false;
        let inFlight = false;

        if (typeof poll !== 'function') {
            throw new TypeError('VisibilityPolling requires a poll function');
        }

        function clearTimer() {
            if (timerId === null) return;
            clearIntervalFn(timerId);
            timerId = null;
        }

        function tick() {
            if (!started || documentRef.hidden || inFlight) return Promise.resolve(false);
            inFlight = true;
            return Promise.resolve()
                .then(() => poll())
                .catch(onError)
                .finally(() => {
                    inFlight = false;
                });
        }

        function schedule() {
            if (!started || documentRef.hidden || timerId !== null) return;
            timerId = setIntervalFn(() => void tick(), intervalMs);
        }

        function handleVisibilityChange() {
            if (documentRef.hidden) {
                clearTimer();
                return;
            }
            void tick();
            schedule();
        }

        function start() {
            if (started) return;
            started = true;
            documentRef.addEventListener('visibilitychange', handleVisibilityChange);
            windowRef.addEventListener('beforeunload', stop);
            if (!documentRef.hidden) {
                schedule();
                if (options.pollOnStart === true) void tick();
            }
        }

        function stop() {
            if (!started) return;
            started = false;
            clearTimer();
            documentRef.removeEventListener('visibilitychange', handleVisibilityChange);
            windowRef.removeEventListener('beforeunload', stop);
        }

        return { start, stop, tick };
    }

    return { create };
}));
