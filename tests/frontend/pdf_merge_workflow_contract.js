const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

const html = read('src/frontend/pdf-merge-workflow.html');
const script = read('src/frontend/js/pdf-merge-workflow.js');
const navigation = read('src/frontend/js/shared-navigation.js');
const pdfTools = read('src/frontend/pdf-tools.html');

for (const id of [
  'pdf-merge-files-input',
  'pdf-merge-asset-search',
  'pdf-merge-asset-list',
  'pdf-merge-queue',
  'pdf-merge-title',
  'pdf-merge-submit',
  'pdf-merge-job-list',
]) {
  assert(html.includes(`id="${id}"`), `missing PDF merge workbench element: ${id}`);
}

assert(
  html.includes('js/pdf-merge-workflow.js'),
  'PDF merge page must load its dedicated client controller',
);
assert(
  script.includes('/api/pdf-merge-workflows'),
  'client must use the first-party PDF merge workflow API',
);
assert(
  script.includes('/api/report-audio-workflows/pdf-assets'),
  'client must reuse the existing cleaned PDF asset catalog',
);
assert(script.includes('new FormData()'), 'mixed uploads must submit multipart FormData');
assert(script.includes("kind: 'pipeline_asset'"), 'existing assets must preserve their source kind');
assert(script.includes('asset_ref'), 'existing assets must submit canonical asset references');
assert(script.includes("form.append('items', JSON.stringify(items))"), 'the ordered source manifest must be submitted');
assert(script.includes('/cancel'), 'client must expose cancel action');
assert(script.includes('/retry'), 'client must expose retry action');
assert(script.includes('/download'), 'client must expose output download action');
assert(script.includes('textContent'), 'dynamic PDF names must be rendered as text, not HTML');
assert(!script.includes('.innerHTML'), 'PDF merge controller must not inject dynamic file names as HTML');
assert(
  navigation.includes("filename === 'pdf-merge-workflow.html'")
    && navigation.includes("return 'pdf-merge-workflow'"),
  'shared navigation must recognize the PDF merge workbench',
);
assert(
  navigation.includes("id: 'pdf-merge-workflow'")
    && navigation.includes("href: 'pdf-merge-workflow.html'"),
  'shared navigation must expose the PDF merge workbench',
);
assert(
  pdfTools.includes('href="pdf-merge-workflow.html"'),
  'PDF tools page must link to the first-party merge workbench',
);

console.log('pdf merge workflow frontend contract: ok');
