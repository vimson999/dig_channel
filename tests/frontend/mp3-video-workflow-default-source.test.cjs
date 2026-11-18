const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..', '..');
const html = fs.readFileSync(path.join(root, 'src/frontend/mp3-video-workflow.html'), 'utf8');
const select = html.match(/<select\s+id="input-source-select"[^>]*>([\s\S]*?)<\/select>/);

assert.ok(select, 'MP3 workflow page must include the content source selector');
const options = [...select[1].matchAll(/<option\b([^>]*)>/g)];
assert.equal(options.filter(([, attrs]) => /\bselected\b/.test(attrs)).length, 1,
  'exactly one content source should be selected by default');
assert.ok(options.some(([, attrs]) => /\bvalue="ds_response"/.test(attrs) && /\bselected\b/.test(attrs)),
  'directly pasting the DS response should be the default content source');

const renderModeSelect = html.match(/<select\s+id="render-mode-select"[^>]*>([\s\S]*?)<\/select>/);
assert.ok(renderModeSelect, 'MP3 workflow page must include the output mode selector');
const renderModes = [...renderModeSelect[1].matchAll(/<option\b([^>]*)>/g)];
assert.equal(renderModes.filter(([, attrs]) => /\bselected\b/.test(attrs)).length, 1,
  'exactly one output mode should be selected by default');
assert.ok(renderModes.some(([, attrs]) => /\bvalue="keyframes"/.test(attrs) && /\bselected\b/.test(attrs)),
  'keyframe preview must remain the default until the user reviews the cover');
assert.ok(renderModes.some(([, attrs]) => /\bvalue="full_video"/.test(attrs) && !/\bselected\b/.test(attrs)),
  'complete video rendering must require an explicit selection or continuation');
