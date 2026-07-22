# 任务进度 JSON 写放大与热点读 120 秒失败

## 基本信息

- 状态：2026-07-19 再次打开；查询执行计划正常，应用往返抖动及重复轻量/载荷查询放大已修复，待安全新进程替换与运行窗口验收
- 严重级别：P0
- 影响模块：`publication_tasks`、report audio API、Doubao Browser TTS、Gemini Browser、MP3 视频父子续跑、ZSXQ、PgBouncer
- 首次发现：2026-07-18
- 修复提交：未提交
- 根因分类：热点 JSON 写放大、延迟续跑重复解包 JSON、瞬时数据库阻塞、热点读缺少快速降级、监控分类缺口
- 复发键：`PUBLICATION-TASK-PROGRESS-JSON-WRITE`
- 是否复发：同类问题复发
- 关联记录：`2026-07-10-report-audio-projection-and-parent-roundtrips.md`、`2026-07-13-mp3-hot-path-json-payload-regression.md`、`2026-07-17-content-pipeline-progress-json-and-retry-contract.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

2026-07-18 10:49 前 `logs/slow_query.log` 已记录 209 条 1 秒以上 SQL；10 点后占 168 条。研报音频资产统计成功查询耗时 65.87 秒，任务列表查询运行 120.27 秒后被 PgBouncer 关闭并返回 HTTP 500。与此同时，多个 Doubao worker 持续执行如下进度更新：

```sql
UPDATE publication_tasks
SET current_step = ?, progress = ?, result = ?, updated_at = ?
WHERE id = ? AND status != ?;
```

## 影响

- 任务列表偶发等待两分钟并直接显示数据库操作失败。
- 进度事件越多，`result.logs` 越大，每次更新都重写完整 JSON/TOAST 值。
- 多 worker 并发进度写入期间，主键点查、列表、ZSXQ 和 MP3 查询一起出现秒级抖动。
- 旧监控只识别 SELECT 的 `params/result`，把 UPDATE `result` 错记为 `payload=none`，导致防线漏报。
- 2026-07-18 12:04 重启后的真实 MP3 续跑任务又暴露一条独立机制：父任务等待 publisher 子任务时，延迟消息只携带父任务 ID。每次 60 秒续跑都重新读取父任务 `params/result`，并在本轮等待截止时无条件读取子任务 `params/result`。新 worker 实测父载荷读取最高 4.26 秒、子载荷读取最高 1.85 秒。
- 重启期间积压的旧续跑消息没有代次标识，旧消息和最新消息无法区分，会继续放大同一热点读取。
- 16:50 新豆包 worker（PID `46173`）收到一个随后被取消的任务。进度 CAS 正确返回 0 并抛出“任务已取消”，但通用异常分支又读取 `publication_tasks.result`；PgBouncer 在 16:53:10 以 `query timeout` 关闭该连接，SQL 总耗时 120.41 秒。该行 `result` 实际只有约 1 KB，因此这不是大对象传输，而是一条不必要查询撞上瞬时阻塞。
- 2026-07-19 当日窗口去重后有 285 条 `publication_tasks` SQL、75 个签名；MP3 列表 14 次平均 13.49 秒、最高 119.25 秒，runtime guard 仍在准入循环读取 `result`，Celery 正常 Retry 又被误记成 ERROR。

## 根因

生产库现场数据证明问题不能继续归结为“缺索引”：

- `publication_tasks` 仅 4519 行，研报音频任务 263 行，相关 5 个热点索引均存在且持续命中。
- 相同资产统计 SQL 的现场执行计划为 0.138ms；相同任务列表 SQL 为 0.055ms。
- PgBouncer 在 10:45:03 明确记录 `query timeout`，其全局 `query_timeout` 为 120 秒。
- 表总大小 85MB：heap 17MB、索引约 7.3MB、TOAST/辅助数据 61MB。
- 累计 4525 次插入却有 136348 次更新；仅 2494 次 HOT update，39750 次写到新页，并触发 207 次 autovacuum、345 次 autoanalyze。
- 2026-07-19 生产库重新执行同形 SQL：MP3 列表 0.049ms、研报列表 0.138ms、watchdog 0.054ms，均正确命中索引；但应用一次 EXPLAIN 往返仍可达 728ms。PgBouncer 与直连各执行 20 次 `SELECT 1`，平均约 38-39ms、P95 约 116ms，证明主要尖峰位于远程网络/连接往返而非 PostgreSQL executor。

因此，65/120 秒是瞬时数据库阻塞在高频写放大下的异常尖峰；查询本身仍需设置明确的失败边界，但继续添加同形索引不能解决根因。

## 解决思路

1. 使用现有结构化状态列承接实时进度，JSON 只保存冷数据、审计日志和终态产物。
2. 热点列表/统计查询使用短语句超时，失败时优先返回有界过期快照，避免等满 PgBouncer 120 秒。
3. 列表计数与页面投影分开，使 count 走轻量索引扫描，页面查询只访问当前页的业务列。
4. 扩展 SQL 监控，使慢 UPDATE `params/result` 被归类为 `slow_payload_write`。
5. 延迟续跑消息携带结构化等待快照和代次令牌；等待中的每一轮只读取父子状态投影，子任务进入终态后才读取一次完整结果。

## 实际方案

- Doubao 和 Gemini 的 `update_progress` 仅写 `current_step/progress/updated_at`。
- 进度日志保存在 worker 内存快照中，在完成、失败、重试或验证码阻塞终态一次写入 `result`，没有删除日志或产物字段。
- report audio 列表和资产统计设置默认 5 秒 PostgreSQL `SET LOCAL statement_timeout`。
- 两个接口分别增加 15/10 秒新鲜缓存和最长 5 分钟的失败降级快照；无快照时返回 503，而不是等待 120 秒后返回 500。
- 修复组合可见用户缓存键无法按单个用户清理的问题。
- 数据库日志分类器开始识别 UPDATE 的 `params/result` 写入。
- 修复监控对原生 SQL `UPDATE publication_tasks ... WHERE id = :task_id` 的主键识别，避免把单行原子写误报为 `multirow_payload`；超过 500ms 的写仍保留 `slow_payload_write` 告警。
- MP3 父任务的首次等待把 `waiting_child` 快照写入终态/审计 JSON，后续延迟消息直接携带 `label/child_id/status/step/defer_count/updated_at`。
- MP3 等待续跑使用 `waiting_child_*` 结构化列和 compare-and-set 代次令牌；过期或重复消息直接退出，不再读取父任务完整 JSON，也不再派生下一条重复消息。
- MP3 子任务等待截止分支先读状态投影；只有 `completed/failed/canceled` 时才加载完整子任务结果。
- ZSXQ 自动文案素材复用查询把 active status 下推到 SQL，并只投影 6 个轻量列，不再先读取 30 行完整任务 JSON 后在 Python 过滤。
- ZSXQ 同步进度每次仍提交 topic/附件事务并实时更新结构化计数与 `updated_at`，但约 68KB 的完整 `zsxq_sync_runs.result` 最多每 30 秒写一次；内容清洗交接和终态使用 `force_result=True`/终态赋值完整落库，审计结果不丢失。
- PDF/音频清洗任务在首次 flush 后、commit 前保存完整响应快照；后续准入、Celery 入队和 ID 回写只使用字符串主键，不再因 `task.to_dict()` 触发 `publication_tasks.params/result` 整行刷新。
- 同步完成后的资产解析默认错峰 90 秒，finalizer 消息和 Celery 返回值只保留结构化计数、tab 摘要及必要子任务状态，避免完成瞬间重复搬运两份同步结果。
- MP3 详情接口先按 `updated_at` 查询轻量版本，版本未变化时复用完整详情快照；读取设置 5 秒语句超时和有界 stale fallback。
- 前三轮没有新增索引或数据库列，避免在执行计划已正确时继续堆重复索引；第 106 轮证明查询仍缺少源端结构，因此第四轮新增五个清洗终态投影列和一张批次文件关系表，而不是新增同形索引。
- ZSXQ 清洗/发布阶段轮询改成结构化状态 `Row`；清洗终态只读取已完成任务结果，发布正常轮询依赖运行时对账与阶段摘要。旧工作流兼容和异常恢复仍可按主键读取一次必要 JSON，业务结果与历史流程没有被裁剪。
- Doubao 在首次任务载荷读取后持有有界内存结果快照；成功、验证码和失败收口只重新查询状态列，进度 CAS 已确认取消时直接退出且不再查询。终态完整 JSON 由已有快照一次写入。
- runtime guard 的活动计数改为直接 `count(publication_tasks.id)`，不再让 ORM 生成包含整张宽表的 count 子查询；PostgreSQL 延迟状态改为单条原子 JSONB UPDATE，在服务端保留最近 20 条审计历史，只返回轻量状态。
- MP3 列表改为显式轻量 count + page 投影，增加 5 秒语句超时、5 秒新鲜缓存和最长 5 分钟 stale fallback；数据库繁忙且无缓存时返回 503，不再等待 PgBouncer 120 秒。
- MP3 子任务修复提交前保存父任务 progress，避免 commit 后访问 expired ORM 实体触发整行刷新。
- request logger 将 Celery `Retry` 识别为正常调度事件，不再把容量保护重试污染为系统 ERROR。
- 本地 PgBouncer `server_idle_timeout` 调整为 600 秒、`query_timeout` 调整为 30 秒并启用 TCP keepalive；启动脚本每次重写配置后都会向已有 PgBouncer 发送 HUP，修复“脚本重启过但新配置从未生效”。
- MP3 publisher 子任务磁盘恢复改为 PostgreSQL 单条原子 JSONB 合并 UPDATE；现有 `result` 完整保留，
  恢复补丁只追加视频路径、完成时间和恢复审计字段。恢复判断与恢复后摘要只读结构化投影列，
  不再为了一个已落盘视频先加载子任务 `params/result`。

## 验证

- 现场只读核验：5 个相关索引存在并有扫描计数。
- `EXPLAIN (ANALYZE, BUFFERS)`：资产统计 0.138ms，任务列表 0.055ms。
- 第一阶段单元与契约测试：155 passed；追加轻量续跑门禁后的相关回归集：160 passed。
- 2026-07-18 12:04 首次重启确认 19 个 Celery 节点在线，bootstrap `errors=[]`；真实续跑日志促使问题重新打开并追加本轮修复，没有把首次重启误判为完成。
- 2026-07-18 12:26 定向替换 MP3 worker：旧 PID `38830/38825`，新 PID `5308/5303`，没有中断独立 render 队列。
- 同一真实父子任务中，129 代积压消息以 29ms 状态读取判定为 stale 并 no-op；130、131 代分别推进到 131、132，日志明确记录 `without JSON payload read`。每轮只执行父/子状态投影和单行 compare-and-set，耗时 27-119ms。
- `2026-07-18 12:27:39` 之后专项审计为 `records=0 duplicates=0 signatures=0`；修复前同一路径出现 1.5-4.3 秒载荷读取，并有一次 120.18 秒 query timeout。
- 尚未完成：当前 publisher render 子任务完成后的父任务终态收口，以及更长观察窗口。
- 13:03 真实 ZSXQ 批次又暴露同步完成扇出和清洗提交后的宽行刷新，问题按同一复发键重新打开；第二轮查询形状与业务完整性定向测试 65 项通过，尚未重启相关 worker，因此不能标记已解决。
- 13:37 真实第 105 轮继续抓到清洗与发布各一条 `multirow_payload` 轮询，即使仅 54-55ms 仍按 P0 门槛重新打开；第三轮聚焦 65 项、相关全量 176 项测试通过，等待新 worker 重放相同业务阶段。
- 14:06 后第 106 轮业务完整成功，但仍发现一条清洗终态 `multirow_payload`，以及清洗交接/资产扫描各一次 224142-byte 同步结果读取；问题继续打开并进入结构化入库第四轮，84 项聚焦测试已通过，尚未迁移或重启。
- 16:53 PgBouncer 与 worker 日志共同确认取消收口 query timeout；新增实际 SQL 测试模拟运行中取消，断言取消信号后不再发起任何 SELECT。B站完整 worker、豆包取消收口及相关运行时回归共 `201 passed, 4 subtests passed`。
- 2026-07-19 本轮 runtime guard、MP3 API/service、日志、监控重启与 PgBouncer 定向回归 `173 passed`，`py_compile` 和 `git diff --check` 通过；原子 JSONB UPDATE 已对不存在的全零 ID 在生产 PostgreSQL 完成语法/事务验证，影响 0 行。
- 21:55 完整重启在停止进程前发现新 publisher 渲染并安全退出，因此当前状态仍是“代码已修改、待部署”，不能表述为已解决。
- 22:10:42 新进程窗口去重后共有 6 条慢样本，没有 statement timeout、死锁或错误 SQL；列表和
  watchdog 已是轻量投影，但远端往返分别抖到 1.89 秒和 1.44 秒。唯一仍可消除的载荷热读是
  子任务磁盘恢复：`params/result` 点查 2.75 秒；随后父任务恢复主流程所需的一次完整工作流载荷为
  1.08 秒。磁盘恢复原子化修复相关回归 `150 passed`，生产 PostgreSQL 零行同形 UPDATE 在
  292.97ms 内成功，无类型或语法错误。
- 同一旧 MP3 worker 22:25 的真实等待轮次中，结构化父子状态读取主要为 27-112ms，续跑任务
  约 0.99-1.83 秒完成且日志明确为 `without JSON payload read`。当前长视频仍在本地 render，
  因此最终补丁尚未滚动部署，记录继续保持打开。

## 复发预防

- 进度回调禁止出现 `PublicationTask.result` 写入，契约测试直接扫描两个浏览器 worker。
- report audio 热点读必须保留语句级超时和 stale fallback，契约测试禁止移除。
- `slow_payload_write` 纳入 `publication_tasks` P0 验收门槛。
- 同一 SQL 当前计划很快但运行日志极慢时，必须先调查连接、锁和写放大，禁止再次盲加索引并宣布解决。
- 延迟父任务不得只传父任务 ID 后重新解包完整运行状态；必须携带最小等待快照。等待轮次禁止读取父子 `params/result`，代次令牌不匹配的旧消息必须 no-op。
- 契约测试同时锁定 Celery 任务参数透传、父状态列白名单、截止分支轻量读取、代次 compare-and-set 和下一轮消息参数，任何一项退化都会阻断测试。
- 高频进度 JSON 必须有明确写入预算；默认 30 秒节流、终态强制写入，测试必须同时证明“过程不重写 JSON”和“终态完整结果可读”。
- 任务创建函数若需要返回完整请求/结果，必须在 flush 后、commit 前构造响应；commit 后只允许按 ID 定向写或明确的轻量投影，禁止再次 `to_dict()`。
- 完成阶段新增消费者必须声明延迟、消息体预算和结果来源；默认从既有快照/数据库事实读取，不得让多个消费者各自整行加载同一 JSON。
- worker 已持有终态所需内存快照时，成功、失败、验证码、取消和重试分支只能读取状态投影；任何异常分支重新加载 `params/result` 都按 P0 复发处理。
- 执行计划毫秒级而中间件秒级时，必须分别记录 executor、连接获取和端到端往返；禁止仅凭 SQL 文本继续添加重复索引。
- 任何配置生成脚本修改运行中代理配置后必须执行 reload 并读取运行时配置验收；只检查生成文件不算部署成功。

## 后续事项

- 当前 MP3 worker 已单独加载轻量续跑协议；保持 render 队列运行到当前业务子任务自然结束。
- 在新进程边界后覆盖至少一轮 publisher 等待续跑和一轮真实研报音频任务，确认状态、结果日志、音视频、通知、失败/重试和取消语义完整。
- 观察 30 分钟 `slow_payload_write`、MP3 父/子载荷指纹、列表/统计指纹和 PgBouncer `query timeout`；达标前状态保持“代码已修改”或“已部署待验收”。
