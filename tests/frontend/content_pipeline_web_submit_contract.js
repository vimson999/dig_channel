const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..', '..');
const htmlPath = path.join(root, 'src', 'frontend', 'content-pipeline-submit.html');
const scriptPath = path.join(root, 'src', 'frontend', 'js', 'content-pipeline-submit.js');
const navigationPath = path.join(root, 'src', 'frontend', 'js', 'shared-navigation.js');

const html = fs.readFileSync(htmlPath, 'utf8');
const script = fs.readFileSync(scriptPath, 'utf8');
const navigation = fs.readFileSync(navigationPath, 'utf8');

assert(html.includes('选择文件夹'), 'the page must expose a folder-select mode');
assert(html.includes('上传文件'), 'the page must expose a multi-file upload mode');
assert(html.includes('webkitdirectory'), 'folder mode must use the browser directory picker');
assert(html.includes('include-subdirectories'), 'folder mode must expose the nested-folder choice');
assert(html.includes('accept=".mp3,.pdf"'), 'the browser picker must restrict the page to MP3/PDF');
assert(html.includes('content-pipeline-submit.js?v='), 'the page must load its submission controller');

assert(script.includes('new FormData()'), 'the page must transmit browser-selected files as multipart data');
assert(script.includes("formData.append('relative_paths'"), 'folder selection must preserve relative paths for server-side enforcement');
assert(script.includes("'/content-pipeline/web-submit'"), 'the page must use the web submission API');
assert(html.includes('auto publish'), 'the page must explain that the shared pipeline will auto publish after cleaning');
assert(script.includes('selectFiles'), 'the page must render a local file preview before submission');

assert(navigation.includes("'content-pipeline-submit': '网页内容投递'"), 'the page must have a navigation title');
assert(navigation.includes("id: 'content-pipeline-submit'"), 'the page must be reachable from the shared navigation');

console.log('content pipeline web submit contract: ok');
