const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
let source = fs.readFileSync(
  path.join(root, 'src/frontend/js/mp3-video-workflow-detail.js'),
  'utf8',
);
const bootstrapIndex = source.lastIndexOf("document.addEventListener('DOMContentLoaded'");
assert.notEqual(bootstrapIndex, -1, 'detail page bootstrap must exist');
source = `${source.slice(0, bootstrapIndex)}\nglobalThis.Mp3VideoWorkflowDetailPage = Mp3VideoWorkflowDetailPage;`;

const context = { URLSearchParams };
vm.runInNewContext(source, context, { filename: 'mp3-video-workflow-detail.js' });
const page = Object.create(context.Mp3VideoWorkflowDetailPage.prototype);

const manualTitle = '人工编辑的当前封皮文案';
assert.equal(
  page.defaultArtifactTitleText({
    plan: { xiaojunjun_subtitle: '规划阶段的初始文案' },
    artifacts: { xiaojunjun_background: { manual_title_text: manualTitle } },
  }, 'xiaojunjun_background'),
  manualTitle,
  'editor default must reflect the title currently rendered in the artifact',
);

assert.equal(
  page.defaultArtifactTitleText({ plan: { xiaojunjun_subtitle: '黄色高亮条文案' } }, 'xiaojunjun_cover'),
  '黄色高亮条文案',
  'editor default must use the dedicated subtitle before the full video title',
);

page.absoluteUrl = (url) => url;
const wsjUrl = '/api/mp3-video-workflows/jobs/job/download-artifact/wsj_cover?v=new';
const wsjPreview = page.renderArtifacts({
  status: 'succeeded',
  plan: { title: '规划阶段标题' },
  artifacts: { wsj_cover: { manual_title_text: '人工调整后的 WSJ 标题' } },
  artifact_download_urls: { wsj_cover: wsjUrl },
});
assert.match(wsjPreview, /alt="WSJ 封皮"/);
assert.match(wsjPreview, /alt="WSJ 背景图"/);
assert.equal((wsjPreview.match(/<img src="([^"]+)"/g) || []).length, 2);
assert.equal([...wsjPreview.matchAll(/<img src="([^"]+)"/g)].every(([, url]) => url === wsjUrl), true);
assert.equal((wsjPreview.match(/data-artifact-key="wsj_cover"/g) || []).length, 1);
assert.match(wsjPreview, /人工调整后的 WSJ 标题/);
assert.match(page.renderArtifacts({
  status: 'succeeded', plan: { title: '历史任务标题' },
}), /生成 WSJ 封皮与背景图/);
assert.match(page.renderVideos({ download_wsj_video_url: '/download-video/wsj' }), /WSJ 4:3 视频/);

const ninePartSource = `1. 标题
主标题
7. 此刻此声视频标题
内资闭门会 | 软件财报 | 2026-11-16
8. WSJ视频标题（竖线格式）
内资闭门会 | 数字员工收入空间 | 2026-11-16
9. WSJ封面三行文案
\`\`\`text
商业化进入新阶段
数字员工有望放量
关注能力向收入转化
\`\`\`
`;
const dsNine = page.normalizedDsExtract({ initial_response_text: ninePartSource });
assert.equal(dsNine.wsj_title_lines.length, 3);
assert.match(dsNine.title_8, /数字员工收入空间/);
assert.doesNotMatch(dsNine.title_7, /数字员工/);
assert.match(page.renderDsExtract({ ds_extract: { ...dsNine, has_content: true } }), /WSJ 封面三行文案/);
assert.equal(page.defaultArtifactTitleText({ plan: { wsj_title_lines: ['甲', '乙', '丙'] } }, 'wsj_cover'), '甲\n乙\n丙');
