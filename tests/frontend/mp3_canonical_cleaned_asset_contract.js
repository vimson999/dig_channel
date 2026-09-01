const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');
const html = fs.readFileSync(path.join(root, 'src/frontend/mp3-video-workflow.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'src/frontend/js/mp3-video-workflow.js'), 'utf8');

assert(html.includes('已清洗 MP3'), 'MP3 workbench must describe the cleaned-asset catalog');
assert(!html.includes('已同步 MP3'), 'MP3 workbench must not promise raw synced MP3 inputs');
assert(script.includes("return '清洗产物';"), 'pipeline cards must not be hardcoded as manual submissions');
assert(script.includes('asset.source_label'), 'pipeline cards must render the backend source label');
assert(script.includes("'没有找到已清洗的 MP3。'"), 'empty state must describe cleaned assets');
assert(script.includes('this.audioAssetsLoading = true'), 'asset list must expose an in-flight loading state');
assert(script.includes('正在加载已清洗 MP3'), 'asset list must not render a false empty state while loading');
assert(
  html.includes('mp3-video-workflow.js?v=20260829-runtime-worker-status-v6'),
  'the browser must load the cleaned-asset list renderer after this change',
);

console.log('mp3 canonical cleaned asset contract: ok');
