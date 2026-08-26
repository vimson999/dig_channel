const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');
const files = [
  'src/frontend/report-script-material-workflow-test.html',
  'src/frontend/report-top10-workflow-test.html',
  'src/frontend/report-stick-figure-video-workflow.html',
  'src/frontend/report-visual-plan-test.html',
  'src/frontend/js/report-audio-workflow-test.js',
  'src/frontend/js/report-script-material-workflow-test.js',
  'src/frontend/js/report-top10-workflow-test.js',
  'src/frontend/js/report-stick-figure-video-workflow.js',
  'src/frontend/js/report-visual-plan-test.js',
];

const contents = files.map((file) => ({
  file,
  text: fs.readFileSync(path.join(root, file), 'utf8'),
}));

for (const { file, text } of contents) {
  assert(!text.includes('已同步 PDF'), `${file} must not promise raw synced PDF inputs`);
}

assert(
  contents.some(({ text }) => text.includes('已清洗 PDF')),
  'PDF workbenches must describe cleaned assets',
);
assert(
  contents.some(({ text }) => text.includes('source_label') && text.includes('已清洗')),
  'PDF cards must render their source label and cleaned state',
);

console.log('pdf canonical cleaned asset contract: ok');
