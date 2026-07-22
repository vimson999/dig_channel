# 抖音 Cookie 发布开发计划（2026-07-20）

## 目标

复用现有平台凭据、`PublicationTask`、`platform_upload` 队列和发布任务监控，支持用户在平台凭据页保存浏览器导出的抖音 Cookie，并把研报/MP3 已生成视频投递到抖音创作者中心。

Cookie 明文只允许在 worker 运行时从加密凭据表解密到内存，不进入任务 `params/result`、日志、截图、邮件或前端响应。

## 复用清单

- 凭据：`platform_credential_service` 的加密、脱敏、用途、优先级、失效标记。
- 任务：`PublicationTask` 的 pending/processing/completed/failed/canceled 状态与父子 metadata。
- 队列：现有 `platform_upload` 专用 worker，不占用视频 render worker。
- 提交入口：参考 `bilibili_publish` 的“从既有视频产物创建上传子任务”模式。
- 恢复：沿用 stale celery id、终态幂等、孤儿修复和人工取消纪律。
- 页面：复用平台凭据管理页、研报详情页和 MP3 详情页的发布面板，不增加平行账号管理页。

## 分期

### P0：凭据与真实登录态校验

- 抖音支持 `cookie_json`（推荐）和 `cookie_header`（兼容）。
- 浏览器导出 JSON 保留 domain/path/expires/httpOnly/secure/sameSite。
- “登录校验”访问创作者发布页，区分 `valid`、`auth_required`、`verification_required`、`unknown`。
- 远程浏览器等待前释放数据库 session；不记录 Cookie 明文。

验收：纯转换单测通过；无 Cookie 泄漏；使用真实凭据能在平台凭据页确认登录态。

### P1：独立上传子任务

- 新增抖音上传执行投影，热路径不反复读取 `PublicationTask.params/result` 大 JSON。
- 创建/取消/查询/入队接口与 B站上传保持同一契约。
- worker 上传视频、填写标题/描述/话题；默认先支持立即发布。
- 点击发布后仅记为 `submitted`，不得直接宣称 `visible`。
- 登录失效标记凭据 expired；安全验证进入明确人工处理状态，不无限重试。

验收：本地测试视频可提交；DB、队列、日志三方状态一致；旧消息不会重复发布。

当前进展：已增加 `douyin-publish-test.html` 隔离测试台和 `douyin_upload_test` 任务。默认只验证上传及文案填写，不点击发布；真实发布必须在页面二次确认，结果只记录为 `submitted`。正式接入研报/MP3 详情页前，仍需用真实账号完成一次页面选择器现场验收。

### P2：接入研报与 MP3 详情页

- 在现有发布面板增加抖音账号、标题、描述、话题和视频版本选择。
- 复用横屏/竖屏视频和封面产物，不重新合成。
- 页面展示 queued/running/submitted/reviewing/visible/failed/unknown，并支持取消排队任务。

验收：两条业务线均能从详情页投递；列表轮询只读投影列；通用导航不另造入口。

### P3：发布确认与定时发布

- 在平台允许的情况下查询作品管理页，回填作品 ID/URL 和 reviewing/visible。
- 增加定时发布及账号轮换前置校验。
- 接入通知和 Celery Monitor 业务运行映射。

验收：提交与可见状态不混淆；失败可定位到账号、任务阶段和人工动作。

## 不做事项

- 不绕过短信、滑块、扫码或平台风控。
- 不把 Cookie 写入 `.env`、任务 JSON 或源码。
- 不直接嵌入第三方整套项目；只参考交互经验，保留本项目的状态、凭据和恢复边界。
