const assert = require('node:assert/strict');
const path = require('node:path');

const helper = require(path.resolve(
    __dirname,
    '../../src/frontend/js/zsxq-insights-refresh.js',
));

async function main() {
    const waits = [];
    const urls = [];
    const freshPayloads = [];
    const responses = [
        {ok: true, cacheState: 'STALE', payload: {success: true, data: {version: 1}}},
        {ok: true, cacheState: 'STALE', payload: {success: true, data: {version: 1}}},
        {ok: true, cacheState: 'HIT', payload: {success: true, data: {version: 2}}},
    ];
    const serverResult = await helper.refresh({
        url: '/api/v1/zsxq-insights/overview?a=1',
        refreshState: 'scheduled',
        wait: async (delayMs) => { waits.push(delayMs); },
        request: async (url) => { urls.push(url); return responses.shift(); },
        isCurrent: () => true,
        onFresh: (payload) => { freshPayloads.push(payload); },
    });
    assert.equal(serverResult, 'fresh');
    assert.deepEqual(waits, [500, 1000, 2000]);
    assert.deepEqual(urls, [
        '/api/v1/zsxq-insights/overview?a=1',
        '/api/v1/zsxq-insights/overview?a=1',
        '/api/v1/zsxq-insights/overview?a=1',
    ]);
    assert.equal(freshPayloads.length, 1);
    assert.equal(freshPayloads[0].data.version, 2);

    const fallbackUrls = [];
    await helper.refresh({
        url: '/api/v1/zsxq-insights/overview?a=1',
        refreshState: 'client-required',
        request: async (url) => {
            fallbackUrls.push(url);
            return {
                ok: true,
                cacheState: 'MISS',
                payload: {success: true, data: {version: 3}},
            };
        },
        isCurrent: () => true,
        onFresh: () => {},
    });
    assert.deepEqual(
        fallbackUrls,
        ['/api/v1/zsxq-insights/overview?a=1&_refresh=1'],
    );

    let stalePersisted = false;
    const exhausted = await helper.refresh({
        url: '/overview',
        refreshState: 'in-flight',
        wait: async () => {},
        request: async () => ({
            ok: true,
            cacheState: 'STALE',
            payload: {success: true},
        }),
        isCurrent: () => true,
        onFresh: () => { stalePersisted = true; },
    });
    assert.equal(exhausted, 'stale');
    assert.equal(stalePersisted, false);

    let supersededRequests = 0;
    const superseded = await helper.refresh({
        url: '/overview',
        refreshState: 'scheduled',
        wait: async () => {},
        request: async () => { supersededRequests += 1; },
        isCurrent: () => false,
        onFresh: () => {},
    });
    assert.equal(superseded, 'superseded');
    assert.equal(supersededRequests, 0);
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
