const assert = require('node:assert/strict');
const path = require('node:path');

const helperPath = path.resolve(
    __dirname,
    '../../src/frontend/js/visibility-polling.js',
);
const VisibilityPolling = require(helperPath);

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
}

async function main() {
    const documentRef = eventTarget({ hidden: true });
    const windowRef = eventTarget();
    const timers = new Map();
    const cleared = [];
    let timerSequence = 0;
    let releasePoll;
    let pollCount = 0;

    const controller = VisibilityPolling.create({
        documentRef,
        windowRef,
        intervalMs: 15000,
        poll: () => {
            pollCount += 1;
            return new Promise((resolve) => {
                releasePoll = resolve;
            });
        },
        setIntervalFn(callback, delay) {
            const id = ++timerSequence;
            timers.set(id, { callback, delay });
            return id;
        },
        clearIntervalFn(id) {
            cleared.push(id);
            timers.delete(id);
        },
    });

    controller.start();
    assert.equal(timers.size, 0, 'hidden pages must not schedule polling');
    assert.equal(pollCount, 0, 'hidden pages must not issue an initial poll');

    documentRef.hidden = false;
    documentRef.dispatch('visibilitychange');
    await flushPromises();
    assert.equal(pollCount, 1, 'becoming visible refreshes immediately');
    assert.equal(timers.size, 1, 'visible pages schedule one polling timer');
    assert.equal([...timers.values()][0].delay, 15000);

    const timer = [...timers.values()][0];
    timer.callback();
    timer.callback();
    await flushPromises();
    assert.equal(pollCount, 1, 'an in-flight poll coalesces overlapping ticks');

    releasePoll();
    await flushPromises();
    timer.callback();
    await flushPromises();
    assert.equal(pollCount, 2, 'polling resumes after the prior request settles');

    documentRef.hidden = true;
    documentRef.dispatch('visibilitychange');
    assert.equal(timers.size, 0, 'hiding the page clears its polling timer');
    assert.equal(cleared.length, 1);

    controller.stop();
    documentRef.hidden = false;
    documentRef.dispatch('visibilitychange');
    assert.equal(timers.size, 0, 'stopped controllers detach visibility listeners');

    console.log('visibility-polling: hidden/resume/overlap lifecycle passed');
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
