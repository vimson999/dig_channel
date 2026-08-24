const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const VisibilityPolling = require(path.resolve(
    __dirname,
    '../../src/frontend/js/visibility-polling.js',
));

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

function elementStub() {
    const element = {
        value: '',
        checked: false,
        style: {},
        dataset: {},
        classList: {
            add() {},
            remove() {},
            toggle() {},
        },
        addEventListener() {},
        appendChild() {},
        getContext() { return {}; },
        getBoundingClientRect() { return { left: 0, top: 0, width: 1, height: 1 }; },
    };
    return new Proxy(element, {
        get(target, key) {
            if (key in target) return target[key];
            return () => {};
        },
    });
}

async function flushPromises() {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
}

async function main() {
    const documentRef = eventTarget({
        hidden: true,
        getElementById() { return elementStub(); },
        createElement() { return elementStub(); },
        body: elementStub(),
    });
    const windowRef = eventTarget({ VisibilityPolling });
    let taskRequests = 0;
    let celeryRequests = 0;
    const PublisherService = {
        API_BASE: 'http://localhost:5001/api/v1',
        async fetchTasks() {
            taskRequests += 1;
            return { success: true, data: [] };
        },
        async fetchCeleryStatus() {
            celeryRequests += 1;
            return { success: true, data: { services: [] } };
        },
    };
    const context = vm.createContext({
        console,
        document: documentRef,
        window: windowRef,
        PublisherService,
        Image: class {},
        URLSearchParams,
        encodeURIComponent,
        setTimeout,
        clearTimeout,
        requestAnimationFrame() {},
        cancelAnimationFrame() {},
    });
    const scriptPath = path.resolve(__dirname, '../../src/frontend/js/video-editor/main.js');
    vm.runInContext(fs.readFileSync(scriptPath, 'utf8'), context, { filename: scriptPath });

    assert.equal(
        typeof context.startTaskListPolling,
        'function',
        'video editor must expose its visibility-aware task polling lifecycle',
    );

    const timers = new Map();
    let nextTimerId = 0;
    context.startTaskListPolling({
        documentRef,
        windowRef,
        setIntervalFn(callback, delay) {
            const id = ++nextTimerId;
            timers.set(id, { callback, delay });
            return id;
        },
        clearIntervalFn(id) {
            timers.delete(id);
        },
    });

    assert.equal(timers.size, 0, 'a hidden video editor must not schedule task polling');
    assert.equal(taskRequests, 0, 'a hidden video editor must not request publisher tasks');
    assert.equal(celeryRequests, 0, 'a hidden video editor must not request Celery status');

    documentRef.hidden = false;
    documentRef.dispatch('visibilitychange');
    await flushPromises();
    assert.equal(taskRequests, 1, 'becoming visible refreshes publisher tasks once');
    assert.equal(celeryRequests, 1, 'becoming visible refreshes Celery status once');
    assert.equal(timers.size, 1, 'a visible video editor schedules one timer');
    assert.equal([...timers.values()][0].delay, 4000);

    console.log('video-editor: hidden task polling pauses and visible polling resumes');
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
