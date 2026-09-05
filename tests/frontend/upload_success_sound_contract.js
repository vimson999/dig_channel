const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const read = (relativePath) => fs.readFileSync(path.join(projectRoot, relativePath), 'utf8');

const soundPath = path.join(projectRoot, 'src', 'frontend', 'assets', 'sounds', 'xm4012.mp3');
assert.ok(fs.existsSync(soundPath), 'the upload success sound must be stored in the frontend assets');
assert.ok(fs.statSync(soundPath).size > 0, 'the upload success sound must not be empty');

const soundSource = read('src/frontend/js/upload-success-sound.js');
assert.match(soundSource, /xm4012\.mp3/, 'the sound helper must use xm4012.mp3');
assert.match(soundSource, /window\.UploadSuccessSound/, 'the sound helper must expose a browser API');
assert.match(soundSource, /play\s*\(/, 'the sound helper must provide playback');

for (const page of [
  'src/frontend/mp3-video-workflow-detail.html',
  'src/frontend/report-audio-workflow-detail.html',
  'src/frontend/publisher.html',
  'src/frontend/bilibili-publisher.html',
  'src/frontend/douyin-publish-test.html',
  'src/frontend/wechat-channels-publish-test.html',
]) {
  assert.match(
    read(page),
    /js\/upload-success-sound\.js\?v=20260905-upload-success-sound/,
    `${page} must load the current upload sound helper version`,
  );
}

for (const [page, consumerPattern] of [
  ['src/frontend/mp3-video-workflow-detail.html', /js\/bilibili-publish-panel\.js\?v=20260905-upload-sound-consumer/],
  ['src/frontend/mp3-video-workflow-detail.html', /js\/platform-browser-publish-panel\.js\?v=20260905-upload-sound-consumer/],
  ['src/frontend/report-audio-workflow-detail.html', /js\/bilibili-publish-panel\.js\?v=20260905-upload-sound-consumer/],
  ['src/frontend/report-audio-workflow-detail.html', /js\/platform-browser-publish-panel\.js\?v=20260905-upload-sound-consumer/],
  ['src/frontend/douyin-publish-test.html', /js\/platform-browser-publish-test\.js\?v=20260905-upload-sound-consumer/],
  ['src/frontend/wechat-channels-publish-test.html', /js\/platform-browser-publish-test\.js\?v=20260905-upload-sound-consumer/],
  ['src/frontend/publisher.html', /js\/publisher\/main\.js\?v=20260905-upload-sound-consumer/],
]) {
  assert.match(read(page), consumerPattern, `${page} must refresh its upload sound consumer`);
}

assert.match(
  read('src/frontend/js/bilibili-publish-panel.js'),
  /UploadSuccessSound\?\.play/,
  'the Bilibili panel must play the sound only after a successful status transition',
);
assert.match(
  read('src/frontend/js/platform-browser-publish-panel.js'),
  /UploadSuccessSound\?\.play/,
  'the shared browser publish panel must play the sound after upload success',
);
assert.match(
  read('src/frontend/js/platform-browser-publish-test.js'),
  /UploadSuccessSound\?\.play/,
  'the platform test panel must play the sound after upload success',
);
assert.match(
  read('src/frontend/js/publisher/main.js'),
  /UploadSuccessSound\?\.play/,
  'the legacy publisher must play the sound after a real platform upload succeeds',
);
assert.match(
  read('src/frontend/bilibili-publisher.html'),
  /UploadSuccessSound\?\.play/,
  'the legacy Bilibili batch page must play the sound after history changes to success',
);

const publisherSource = read('src/frontend/js/publisher/main.js');
const publisherSoundFunction = publisherSource.slice(
  publisherSource.indexOf('function playUploadSuccessSound'),
  publisherSource.indexOf('\nfunction updateProgressUI'),
);
let legacyPlayCount = 0;
const publisherContext = {
  window: { UploadSuccessSound: { play: () => { legacyPlayCount += 1; } } },
};
publisherContext.globalThis = publisherContext;
vm.createContext(publisherContext);
vm.runInContext(
  `const uploadSuccessSoundTaskIds = new Set();
   const uploadSuccessSoundStates = new Map();
   let currentTaskId = '';
   ${publisherSoundFunction}
   globalThis.playUploadSuccessSound = playUploadSuccessSound;`,
  publisherContext,
  { filename: 'publisher-main-sound.js' },
);
publisherContext.playUploadSuccessSound({ id: 'transitioned', status: 'processing', result: {} });
publisherContext.playUploadSuccessSound({ id: 'transitioned', status: 'completed', result: { bilibili_result: {} } });
publisherContext.playUploadSuccessSound({ id: 'already-complete', status: 'completed', result: { youtube_result: {} } });
publisherContext.playUploadSuccessSound({ id: 'transitioned', status: 'completed', result: { bilibili_result: {} } });
assert.equal(legacyPlayCount, 1, 'the legacy page must play only for an observed incomplete-to-complete transition');

const panelSource = read('src/frontend/js/bilibili-publish-panel.js').replace(
  /window\.BilibiliPublishPanel\s*=\s*BilibiliPublishPanel;[\s\S]*$/,
  'globalThis.BilibiliPublishPanel = BilibiliPublishPanel;',
);
let playCount = 0;
const context = {
  console,
  document: {},
  window: { UploadSuccessSound: { play: () => { playCount += 1; } } },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(panelSource, context, { filename: 'bilibili-publish-panel.js' });

const panel = Object.create(context.BilibiliPublishPanel.prototype);
panel.options = { uploads: (job) => job.bilibili_uploads || [] };
panel.lastUpload = null;
panel.uploadStatuses = new Map();
panel.uploadStatusInitialized = false;
panel.observeUploadStatuses({ bilibili_uploads: [{ task_id: 'upload-1', status: 'queued' }] });
panel.observeUploadStatuses({ bilibili_uploads: [{ task_id: 'upload-1', status: 'completed' }] });
panel.observeUploadStatuses({ bilibili_uploads: [{ task_id: 'upload-1', status: 'completed' }] });
assert.equal(playCount, 1, 'one upload must produce one sound when it transitions into success');

const interactionListeners = {};
let playbackAttempts = 0;
class BlockedOnceAudio {
  play() {
    playbackAttempts += 1;
    return playbackAttempts === 1
      ? Promise.reject(new Error('autoplay blocked'))
      : Promise.resolve();
  }
}
const soundContext = {
  Audio: BlockedOnceAudio,
  Date: { now: () => 1000 },
  Promise,
  URL,
  document: {
    baseURI: 'https://example.test/',
    addEventListener: (name, callback) => { interactionListeners[name] = callback; },
  },
  window: { location: { href: 'https://example.test/' } },
};
soundContext.globalThis = soundContext;
vm.createContext(soundContext);
vm.runInContext(soundSource, soundContext, { filename: 'upload-success-sound.js' });

soundContext.window.UploadSuccessSound.play()
  .then((played) => {
    assert.equal(played, false, 'blocked autoplay is reported without throwing');
    interactionListeners.pointerdown();
    return new Promise((resolve) => setImmediate(resolve));
  })
  .then(() => {
    assert.equal(playbackAttempts, 2, 'the next user gesture retries a blocked playback immediately');
  })
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });

console.log('upload success sound contract: ok');
