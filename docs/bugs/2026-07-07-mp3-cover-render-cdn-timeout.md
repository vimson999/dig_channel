# MP3 封面后台渲染被外部 CDN 阻塞超时

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING（封皮重做已通过真实页面验收，待后续窗口确认未复发）
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

## 2026-11-10 复发跟进与运行态验收

- 任务 `0c13f13d-1b6b-4a89-9b4a-4cc9061f3829` 的主工作流最终完成，`keyframes_ready` 为 100%；用户随后在详情页手动重做横屏封皮，16:26 的 `POST /artifacts/finance_cover/regenerate` 返回 400，错误仍是 `Page.goto` 被导航到 `chrome-error://chromewebdata/` 中断。
- 后端和 MP3 worker 日志显示最后一次启动在 2026-11-04 13:56；当时工作区已有“每个候选地址使用独立 Playwright 页面”的修复，但运行进程未重载，因而这次是旧运行体继续执行旧导航行为，不是新代码已经通过运行态验证后又复发。
- 2026-11-10 16:31，`scripts/dev/check-restart-safety.py` 成功连接数据库并确认无不可中断任务；随后通过 `scripts/dev/restart-dev-stack.sh` 安全重启。后端新 PID 为 46766，工作台显示 Worker 正常。
- 重启后从真实任务详情页再次点击“生成并替换”：页面提示“封皮已重做并替换”，下载入口版本时间更新为 `20261110T083554936569Z`，详情页仍为 `keyframes_ready` 100%。这验证了已加载代码的真实浏览器/API 路径。
- 结论保持 `RUNTIME_REPLACED / OBSERVING`，不标记 CLOSED。下一轮需检查重启后的 `logs/celery/mp3_video_workflow.log` 与 `logs/backend.log`：确认没有新的封面 `Page.goto`/`chrome-error`，并用下一条自然封面生成或重做任务复核；若复发，优先确认触发进程的启动时间、代码 checkout 和实际候选 URL。
