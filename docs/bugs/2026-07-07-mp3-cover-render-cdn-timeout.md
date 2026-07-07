# MP3 封面后台渲染被外部 CDN 阻塞超时

## 基本信息

- 状态：已修复
- 严重级别：P1
- 影响模块：MP3 视频编排 / 四张封皮背景图生成 / Playwright 后台渲染
- 首次发现：2026-07-07
- 现场任务：`f57420a1-04ca-49e3-8515-bac11b662b06`

## 现象

MP3 视频编排在 `generate_visual_assets` 阶段失败，进度停在 68%。错误信息为：

```text
无法调用前端封面页面 finance-cover-generator.html / renderWorkflowFinanceCover:
Page.goto: Timeout 25000ms exceeded, waiting until "domcontentloaded"
```

## 影响

视觉资产没有生成，父编排直接失败。用户即使已经完成 IMA/DS/标题规划，也无法继续进入关键帧或视频合成阶段。

## 根因

后台渲染通过 Playwright 打开 `finance-cover-generator.html`、`thumbnail-workbench.html`、`closed-door-card-cover-generator.html` 等前端页面。这些页面用于人工操作时引用了 Tailwind、FontAwesome、Google Fonts 等外部 CDN。

在后台自动化渲染场景中，这些外部资源不是必要依赖，但 parser-blocking 脚本可能阻塞页面解析，导致 `domcontentloaded` 长时间不触发，最终 `Page.goto` 超时。

## 解决思路

后台截图/导出必须是本地确定性流程，不能依赖外部 CDN 的可用性。封面绘制函数只依赖本地页面脚本和传入的 data URL 图片，因此应在 Playwright 上下文里屏蔽非本地网络请求。

## 实际方案

- 在 MP3 编排的封面浏览器上下文中增加请求路由。
- 允许 `file://`、`data:`、`blob:`、`http://localhost`、`http://127.0.0.1`、`http://[::1]`。
- 拦截并 abort 其他 `http/https` 请求，避免 CDN 脚本、字体、样式拖住页面生命周期。
- 同步加固通用 `FrontendThumbnailRenderer` 的封面工具台渲染路径。

## 验证

已完成：

```bash
PYTHONPATH=src/backend venv/bin/python -m py_compile src/backend/services/mp3_video_workflow.py src/backend/services/frontend_thumbnail_renderer.py
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_mp3_video_workflow_service -q
```

真实 Playwright 渲染验证：

- `finance-cover-generator.html / renderWorkflowFinanceCover` 成功生成 `1280x720` JPG。
- 完整四张视觉资产链路成功生成：
  - 此刻此声横屏视频封皮
  - 此刻此声竖屏视频背景图
  - 小君君横屏视频封皮
  - 小君君横屏视频背景

## 复发预防

- 后台渲染页面不得把外部 CDN 作为必需路径。
- 如果后续新增前端页面给后台截图/导出使用，必须在 Playwright 上下文中屏蔽非本地网络请求，或者使用纯本地 shell 渲染。
- `Page.goto(..., wait_until="domcontentloaded")` 的超时不能只靠调大 timeout 解决，优先排查 parser-blocking 外部脚本。

## 后续事项

- 现场失败任务可通过页面“失败续跑”从失败处继续，系统会复用已有 IMA/DS/plan。
