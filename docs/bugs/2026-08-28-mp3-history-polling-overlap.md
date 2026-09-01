# MP3 编排历史轮询在隐藏页和慢请求时继续触发

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING；运行中的 backend 已提供新静态资源，待真实隐藏页与慢请求窗口验收
- 严重级别：高
- 影响模块：MP3 视频编排列表 / `publication_tasks` / PostgreSQL / PgBouncer
- 首次发现：2026-08-28 每日数据库性能巡检
- 修复提交：当前工作区
- 根因分类：频繁状态轮询、Page Visibility 缺失、轮询请求非 single-flight
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；属于隐藏页轮询与慢网络重叠簇的新入口
- 关联记录：`2026-08-16-hidden-status-pages-unbounded-polling.md`、`2026-08-18-video-editor-hidden-polling.md`、`2026-08-25-mp3-detail-hidden-publish-panel-request-fanout.md`
- 防线层级：shared_guardrail / contract_test / runtime_observation

## 现象

固定窗口 `2026-08-27 06:02:19` 至 `2026-08-28 06:02:19` 内，`GET /api/mp3-video-workflows/jobs` 212 次、累计 95.265 秒、P95 2.352 秒、最大 7.908 秒，是请求量和累计耗时最高的前台端点。流量集中在两个真实操作窗口：12 时 66 次（12:07:56–12:23:14），17 时 145 次（17:08:15–17:39:13），另有 14 时 1 次；这不是全天无人操作时的持续流量，但会把活跃任务页的远端数据库往返放大。

## 影响

- 212 次列表请求包含 16 条慢 SQL、累计 29.194 秒；同窗 `load_task` 大字段点查指纹 13 次累计 83.707 秒，P95 10.903 秒。
- 页面定时器按固定间隔触发；请求慢于间隔时，旧实现不会等待上一请求结束，可能并发叠加。
- 页面进入后台后旧定时器仍会继续检查并发请求，违反已有 Page Visibility 纪律。

## 根因

`Mp3VideoWorkflowPage.resetPollTimer()` 直接使用 `setInterval()` 调用 `loadHistory()`，只检查当前页是否有活动任务，没有检查 `document.hidden`，也没有 in-flight 状态。列表查询本身仍是有界分页；代表 `publication_tasks` 主键点查的 PostgreSQL executor 为 0.025ms，说明秒级长尾来自远端数据库/PgBouncer 往返，而不是缺索引。固定周期缺少背压会在该长尾下放大请求与连接占用。

## 解决思路

保持活动任务的实时刷新语义和用户可调间隔，但把“页面可见”和“上一轮已完成”设为发请求的必要条件。不要通过延长超时、缓存陈旧任务状态或删除任务字段掩盖问题。

## 实际方案

- 为 MP3 历史轮询增加 `pollInFlight` 状态；上一轮未完成时跳过新的 timer tick。
- 页面隐藏时直接跳过轮询；重新可见后由下一次 tick 恢复。
- 继续只在当前页存在 `queued/running/pending/processing` 任务时刷新，终态页不产生轮询。
- 更新页面静态资源版本为 `20260828-cleaned-audio-assets-v2-visibility-singleflight`，保留已清洗资产版本语义并避免浏览器继续命中旧脚本。

## 验证

- TDD 红灯：旧实现缺少 `pollInFlight`，定向契约明确失败。
- 修复后定向契约通过，JavaScript 语法、完整 unit、compileall 与 Bug 严格门禁结果见当日巡检报告。
- 运行中的 backend `/health` 为 healthy，并已返回带新版本标记的真实 HTML；本修复仅为静态前端变更，无需重启 backend/Celery/scheduler。
- 尚未取得真实浏览器在“隐藏页 + 活动任务 + 慢请求”组合下的 request id 序列，因此不能标记 CLOSED。

## 复发预防

- 所有状态轮询必须同时满足 Page Visibility、single-flight、活动态限定和终态停止。
- 定时器回调内的异步请求必须由 in-flight 门禁提供背压，不能依赖请求序号仅丢弃旧响应。
- 每日巡检按小时和活跃操作窗口量化轮询请求，不能只报总次数。

## 后续事项

- 下一轮确认隐藏时段没有 MP3 jobs 请求；可见且存在活动任务时，同一页面同时在途的 jobs 请求不得超过 1。
- 比较请求量/活跃分钟、累计 SQL 与连接持有；如仍有重叠，继续追查旧静态资源、多个浏览器标签和其他调用 owner。
- 只有真实浏览器运行态证据和后续完整窗口均通过，才可转 CLOSED。
