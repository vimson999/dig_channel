const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const projectRoot = path.resolve(__dirname, '..', '..');
const read = (relativePath) => fs.readFileSync(path.join(projectRoot, relativePath), 'utf8');

for (const relativePath of [
  'src/frontend/js/bilibili-publish-panel.js',
  'src/frontend/js/mp3-video-workflow-detail.js',
  'src/frontend/js/publisher/main.js',
]) {
  const source = read(relativePath);
  assert.match(
    source,
    /Date\.now\(\) \+ 16 \* 60 \* 1000/,
    `${relativePath} must default to a minute-safe 16-minute schedule margin`,
  );
  assert.match(
    source,
    /Date\.now\(\) \+ 15 \* 60 \* 1000/,
    `${relativePath} must enforce the 15-minute backend admission contract`,
  );
  assert.match(
    source,
    /B站定时发布时间需在 15 分钟后、15 天内/,
    `${relativePath} must explain the 15-minute contract to the user`,
  );
  assert.doesNotMatch(
    source,
    /Date\.now\(\) \+ [56] \* 60 \* 1000/,
    `${relativePath} must not retain a default or validator rejected by the backend`,
  );
}

console.log('Bilibili schedule margin contract: ok');
