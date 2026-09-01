const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const pollingSource = fs.readFileSync(
    path.join(projectRoot, 'src/frontend/js/visibility-polling.js'),
    'utf8',
);
const workbenchSource = fs.readFileSync(
    path.join(projectRoot, 'src/frontend/js/pdf-merge-workflow.js'),
    'utf8',
);

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
    };
}

async function flushPromises() {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
}

async function main() {
    const timers = new Map();
    let timerSequence = 0;
    const setIntervalFn = (callback, delay) => {
        const id = ++timerSequence;
        timers.set(id, { callback, delay });
        return id;
    };
    const clearIntervalFn = (id) => timers.delete(id);
    const documentRef = eventTarget({
        hidden: false,
        getElementById() {
            return null;
        },
    });
    const windowRef = eventTarget({
        localStorage: { getItem() { return ''; } },
        setInterval: setIntervalFn,
        clearInterval: clearIntervalFn,
    });
    const context = vm.createContext({
        console,
        document: documentRef,
        window: windowRef,
        globalThis: null,
        URLSearchParams,
        setInterval: setIntervalFn,
        clearInterval: clearIntervalFn,
    });
    context.globalThis = context;
    vm.runInContext(pollingSource, context, { filename: 'visibility-polling.js' });
    windowRef.VisibilityPolling = context.VisibilityPolling;
    vm.runInContext(workbenchSource, context, { filename: 'pdf-merge-workflow.js' });
    const Workbench = vm.runInContext('PdfMergeWorkflowWorkbench', context);

    let requestCount = 0;
    const responses = [];
    Workbench.prototype.request = function request(requestPath) {
        if (requestPath.startsWith('/api/report-audio-workflows/pdf-assets?')) {
            return Promise.resolve({ success: true, data: { items: [] } });
        }
        requestCount += 1;
        assert.ok(responses.length, 'unexpected PDF merge list request');
        const response = responses.shift();
        return typeof response === 'function' ? response() : Promise.resolve(response);
    };

    responses.push({ success: true, data: { items: [] } });
    const workbench = new Workbench();
    await flushPromises();
    assert.equal(requestCount, 1, 'visible construction loads the list once');
    assert.equal(timers.size, 0, 'an empty or terminal list must not keep polling');

    responses.push({
        success: true,
        data: { items: [{ job_id: 'pending-1', status: 'pending', progress: 0 }] },
    });
    await workbench.loadJobs({ silent: true });
    assert.equal(timers.size, 1, 'an active job starts exactly one polling timer');

    let resolveInflight;
    responses.push(() => new Promise((resolve) => { resolveInflight = resolve; }));
    const first = workbench.loadJobs({ silent: true });
    const second = workbench.loadJobs({ silent: true });
    await flushPromises();
    assert.equal(requestCount, 3, 'overlapping refreshes share one in-flight request');
    resolveInflight({
        success: true,
        data: { items: [{ job_id: 'pending-1', status: 'processing', progress: 50 }] },
    });
    await Promise.all([first, second]);
    assert.equal(timers.size, 1, 'settling an active refresh does not duplicate the timer');

    responses.push({
        success: true,
        data: { items: [{ job_id: 'pending-1', status: 'completed', progress: 100 }] },
    });
    [...timers.values()][0].callback();
    await flushPromises();
    assert.equal(timers.size, 0, 'polling stops as soon as every job is terminal');

    console.log('pdf merge polling: active-only/single-flight lifecycle passed');
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
