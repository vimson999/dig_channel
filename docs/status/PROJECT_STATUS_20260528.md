# 2026-05-28 工作归档

## Publisher 横屏 CTA 黄色样式

- 页面：`http://127.0.0.1:8888/publisher.html`
- 需求：横屏正文 CTA 增强层的“悬层卡片颜色”下拉框增加黄色选项，让卡片更显眼。
- 修改：
  - `src/frontend/publisher.html` 的 `cta_style` 下拉框新增 `警示·亮黄色`，值为 `police_yellow`。
  - `src/frontend/js/publisher/renderer.js` 新增同名预览样式，黄色背景、黑色文字，保证预览与最终视频样式一致。
  - `publisher.html` 中 `renderer.js` 版本号更新到 `v=0528`，避免浏览器继续使用旧预览脚本缓存。
- 兼容性：
  - 后端 `src/backend/services/video_editor.py` 已存在 `police_yellow` CTA 样式，因此最终合成无需额外改动。

## CTA 默认文案更新

- 将横屏 CTA 默认文案从“会员空间获取研报 / 解锁精选研报独家分析”调整为：
  - `简介查看研报资料入口`
  - `每日更新100+外资研报与机构调研`
- 同步更新：
  - `src/frontend/publisher.html`
  - `src/frontend/video-editor.html`
- 目的：表达更贴近研报和调研信息服务，避免直接使用第三方引流敏感词。

## Publisher CTA 默认样式调整

- 将 `publisher.html` 横屏 CTA 默认选中样式从 `nomura_red` 改为 `police_yellow`。
- 将 `src/frontend/js/publisher/main.js` 中 CTA 提交和预览配置的兜底默认样式同步改为 `police_yellow`。
- `video-editor.html` 原本已经默认选中黄色 CTA，无需调整。

## CTA 三行排版修复

- 问题：当 CTA 文案被拆成三行时，发布台预览会把第一行和最后一行放大，中间行缩小，并自动给最后一行加箭头，导致悬层视觉层级混乱。
- 修复：
  - `src/frontend/js/publisher/renderer.js`：CTA 文案最多保留三行；超过三行时只把第 3 行之后归并到第 3 行，去掉自动箭头，统一第 2/3 行字重和字号策略。
  - `src/frontend/js/video-editor/main.js`：同步同一套三行展示和无箭头规则，避免两个入口预览不一致。
  - `src/backend/services/video_editor.py`：最终合成 CTA 图片同步使用三行展示逻辑，并按实际 CTA 图片尺寸计算 overlay 位置，避免动态宽度/高度后位置偏移。
  - 后端 CTA 样式默认兜底也改为 `police_yellow`。
  - `publisher.html` 的 `renderer.js` 版本号更新到 `v=0528-2`，避免浏览器缓存旧两行归并逻辑。

## 验证

- `node --check src/frontend/js/publisher/renderer.js src/frontend/js/publisher/main.js`
- `python3 -m py_compile src/backend/services/video_editor.py`
- `git diff --check`
