const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function eventTarget(initial = {}) {
    const listeners = new Map();
    return {
        ...initial,
        addEventListener(name, callback) {
            const callbacks = listeners.get(name) || [];
            callbacks.push(callback);
            listeners.set(name, callbacks);
        },
        removeEventListener(name, callback) {
            const callbacks = listeners.get(name) || [];
            listeners.set(name, callbacks.filter((item) => item !== callback));
        },
        dispatch(name) {
            for (const callback of listeners.get(name) || []) callback();
        },
    };
}

async function flushPromises() {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
}

function pageScripts() {
    const frontendRoot = path.resolve(__dirname, '../../src/frontend');
    const html = fs.readFileSync(
        path.join(frontendRoot, 'report-script-material-workflow-test.html'),
        'utf8',
    );
    return [...html.matchAll(/<script\s+src="([^"]+)"/g)]
        .map((match) => match[1].split('?')[0])
        .filter((src) => ['js/visibility-polling.js', 'js/report-script-material-workflow-test.js'].includes(src))
        .map((src) => path.join(frontendRoot, src));
}

async function main() {
    const documentRef = eventTarget({ hidden: true });
    const windowRef = eventTarget();
    const context = vm.createContext({
        console,
        document: documentRef,
        window: windowRef,
        URLSearchParams,
        setTimeout,
        clearTimeout,
        setInterval,
        clearInterval,
    });
    for (const scriptPath of pageScripts()) {
        vm.runInContext(fs.readFileSync(scriptPath, 'utf8'), context, { filename: scriptPath });
        if (context.VisibilityPolling) windowRef.VisibilityPolling = context.VisibilityPolling;
    }

    assert.equal(
        typeof context.startReportScriptMaterialPolling,
        'function',
        'report material page must expose its visibility-aware status polling lifecycle',
    );

    let historyRequests = 0;
    let celeryRequests = 0;
    const page = {
        jobs: [{ status: 'running' }],
        async loadHistory() { historyRequests += 1; },
        async refreshWorkerStatus() { celeryRequests += 1; },
    };
    const timers = new Map();
    let nextTimerId = 0;
    context.startReportScriptMaterialPolling(page, {
        documentRef,
        windowRef,
        setIntervalFn(callback, delay) {
            const id = ++nextTimerId;
            timers.set(id, { callback, delay });
            return id;
        },
        clearIntervalFn(id) { timers.delete(id); },
    });

    assert.equal(timers.size, 0, 'a hidden report material page must not schedule status polling');
    assert.equal(historyRequests, 0, 'a hidden report material page must not refresh job history');
    assert.equal(celeryRequests, 0, 'a hidden report material page must not request Celery status');

    documentRef.hidden = false;
    documentRef.dispatch('visibilitychange');
    await flushPromises();
    assert.equal(historyRequests, 1, 'becoming visible refreshes an active job history once');
    assert.equal(celeryRequests, 1, 'becoming visible refreshes worker status once');
    assert.equal(timers.size, 1, 'a visible report material page schedules one timer');
    assert.equal([...timers.values()][0].delay, 12000);

    console.log('report-script-material: hidden status polling pauses and visible polling resumes');
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
