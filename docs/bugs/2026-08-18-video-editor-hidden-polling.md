# 视频编辑器后台标签页持续轮询 Celery 与 Publisher

## 基本信息

- 状态：CODE_CHANGED / OBSERVING
- 严重级别：P0
- 影响模块：视频编辑器 / Celery status / Publisher tasks / publication_tasks
- 首次发现：2026-08-18 日常数据库巡检
- 根因分类：后台标签页轮询、跨端点扇出、缺少 single-flight
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；`2026-08-16-hidden-status-pages-unbounded-polling.md` 后仍有页面直接新增无门禁 `setInterval`
- 防线层级：shared_guardrail / contract_test / runtime_acceptance

## 现象与排序依据

固定窗口内 `/api/v1/celery/status` 323 次、累计 635.286s、P95 8.457s、最大 21.231s；`/api/v1/publisher/tasks` 321 次、累计 296.454s、P95 4.751s、最大 16.827s。两端点从 06:02 至 09:52 精确成对出现，中位间隔 60s，合计消耗 931.740s；其中 Celery status 关联 42 条慢 SQL，Publisher 关联 57 条慢 SQL。

## 根因

`src/frontend/js/video-editor/main.js` 用 4 秒 `setInterval(refreshTaskList, 4000)` 无条件刷新；一次刷新同时访问 Celery status 与 Publisher tasks。Chrome 将隐藏标签页 timer 节流为约 60 秒但不会停止，因此页面在无人观看时持续制造鉴权、broker inspect、业务快照和远端数据库往返。旧代码也没有把 in-flight 请求纳入统一生命周期。

## 实际修复

- 视频编辑器接入既有 `visibility-polling.js`：hidden 停止、visible 立即单次刷新、in-flight single-flight、卸载清理。
- HTML 显式加载 helper 并更新版本，避免缓存继续执行旧脚本。
- 保留可见页 4 秒刷新与全部响应字段，不删除原始 JSON，不改变业务功能。

## 验证与未闭环

- TDD：`tests/frontend/video-editor-visibility-polling.test.cjs` 在入口不存在时先失败，修复后验证 controller 接线和生命周期。
- 静态运行：真实 `video-editor.html` 与 helper 均返回 200。
- 未闭环：修复发生在窗口结束后；没有完整后续窗口证明成对请求在 hidden 时归零，因此保持 OBSERVING。

## 防复发与下一轮验收

前端契约继续禁止页面直接新增周期 `setInterval`。下一轮以静态资源版本加载时点切窗：hidden 时两个端点计数必须同时停止，visible 只允许一次恢复刷新且不得 overlap；完整 24 小时无复发后才允许 CLOSED。
