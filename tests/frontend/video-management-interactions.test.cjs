const assert = require('node:assert/strict');
const path = require('node:path');

const {
    buildFavoritePayload,
    createLatestRequestGate,
    filterSearchableOptions,
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
