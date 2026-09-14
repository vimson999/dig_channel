const assert = require('node:assert/strict');
const path = require('node:path');

const {
    buildVisibleOptionSet,
    buildFavoritePayload,
    createLatestRequestGate,
    filterSearchableOptions,
    updateRecentOptionValues,
} = require(path.resolve(
    __dirname,
    '../../src/frontend/js/searchable-combobox.js',
));

const options = [
    {value: '', label: '所有频道'},
    {value: '1', label: 'AI LABS', keywords: '科技'},
    {value: '2', label: '灵姐说AI | Ling Talk AI', keywords: '科技'},
    {value: '3', label: '大佬透镜', keywords: '教育'},
];

assert.deepEqual(
    filterSearchableOptions(options, ' ai ').map((option) => option.value),
    ['1', '2'],
    'search must be case-insensitive and match anywhere in the label',
);
assert.deepEqual(
    filterSearchableOptions(options, '教育').map((option) => option.value),
    ['3'],
    'search must include option keywords such as the linked category',
);
assert.deepEqual(
    filterSearchableOptions(options, '').map((option) => option.value),
    ['', '1', '2', '3'],
    'clearing search must restore every option in source order',
);

const channelOptions = [
    {value: '', label: '所有频道'},
    {value: '1', label: 'AI Channel 1', popularity: 10},
    {value: '2', label: 'AI Channel 2', popularity: 50},
    {value: '3', label: 'AI Channel 3', popularity: 30},
    {value: '4', label: 'AI Channel 4', popularity: 80},
    {value: '5', label: 'AI Channel 5', popularity: 20},
    {value: '6', label: 'AI Channel 6', popularity: 70},
    {value: '7', label: 'AI Channel 7', popularity: 60},
    {value: '8', label: 'AI Channel 8', popularity: 40},
    {value: '9', label: 'AI Channel 9', popularity: 90},
    {value: '10', label: 'AI Channel 10', popularity: 100},
];

assert.deepEqual(
    buildVisibleOptionSet(channelOptions, '', {
        idleMode: 'recent',
        recentValues: ['5', '2'],
        maxRecentItems: 5,
        maxResults: 8,
    }),
    {
        items: [
            channelOptions[0],
            channelOptions[5],
            channelOptions[2],
            channelOptions[10],
            channelOptions[9],
            channelOptions[4],
        ],
        total: 11,
        hiddenCount: 5,
    },
    'an idle channel picker must fill recent history with popular channels',
);

assert.deepEqual(
    buildVisibleOptionSet(channelOptions, '', {
        idleMode: 'recent',
        recentValues: [],
        maxRecentItems: 5,
        maxResults: 8,
    }),
    {
        items: [
            channelOptions[0],
            channelOptions[10],
            channelOptions[9],
            channelOptions[4],
            channelOptions[6],
            channelOptions[7],
        ],
        total: 11,
        hiddenCount: 5,
    },
    'a first-time channel picker must show five popular channels instead of an empty default',
);

assert.deepEqual(
    buildVisibleOptionSet(channelOptions, 'AI', {
        idleMode: 'recent',
        recentValues: ['5', '2'],
        maxRecentItems: 5,
        maxResults: 8,
    }),
    {
        items: channelOptions.slice(1, 9),
        total: 10,
        hiddenCount: 2,
    },
    'channel search must cap visible results and report how many matches remain hidden',
);

assert.deepEqual(
    buildVisibleOptionSet(channelOptions, '', {
        idleMode: 'recent',
        recentValues: ['5', '2'],
        maxRecentItems: 5,
        maxResults: 8,
        showAll: true,
    }),
    {
        items: channelOptions,
        total: 11,
        hiddenCount: 0,
    },
    'showing all channels must expose every option for browsing without requiring a search term',
);

assert.deepEqual(
    updateRecentOptionValues(['5', '2', '8', '3', '7'], '2', 5),
    ['2', '5', '8', '3', '7'],
    'choosing a recent channel must move it to the front without creating duplicates',
);
assert.deepEqual(
    updateRecentOptionValues(['5', '2', '8', '3', '7'], '9', 5),
    ['9', '5', '2', '8', '3'],
    'recent channel history must stay bounded to five items',
);

const requestGate = createLatestRequestGate();
const firstRequest = requestGate.next();
const secondRequest = requestGate.next();
assert.equal(requestGate.isCurrent(firstRequest), false, 'an older response must be ignored');
assert.equal(requestGate.isCurrent(secondRequest), true, 'the newest response must remain current');

assert.deepEqual(
    buildFavoritePayload(
        {
            id: 8,
            video_id: 'abc',
            title: '视频标题',
            channel_id: 4,
            channel_name: '频道名',
        },
        {
            description: '完整描述',
            tags: ['AI'],
            language: 'zh',
        },
    ),
    {
        id: 8,
        video_id: 'abc',
        title: '视频标题',
        channel_id: 4,
        channel_name: '频道名',
        description: '完整描述',
        tags: ['AI'],
        language: 'zh',
        channel_title: '频道名',
        channel_subscriber_count: 0,
        channel_video_count: 0,
        channel_view_count: 0,
        channel_thumbnail_url: '',
        channel_published_at: null,
    },
    'favorite creation must merge on-demand detail data into the compact card payload',
);
