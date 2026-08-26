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

console.log('mp3 canonical cleaned asset contract: ok');
