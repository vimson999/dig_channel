# 文案到成片图片批量提交逐场景重复宽读与提交

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING
- 严重级别：P0
- 影响模块：文案到成片 / 图片批量提交 / `publication_tasks` / Celery broker / PgBouncer
- 首次发现：2026-09-01 每日数据库性能与错误巡检
- 修复提交：当前工作区
- 根因分类：逐场景 N+1、过宽 ORM 投影、循环 commit、broker 后二次写
- 复发键：`HOT-PATH-PAYLOAD-POLLING`、`DB-SESSION-LIFECYCLE`
- 是否复发：是；与历史 child 创建后整行回读、宽 JSON 轮询和 broker 跨事务属于同一根因簇
- 关联记录：`2026-07-21-report-child-create-post-commit-json-reload.md`、`2026-08-31-script-video-detail-polling-n-plus-one.md`、`2026-08-31-mp3-bilibili-upload-wide-parent-double-commit.md`
- 防线层级：constant_query_budget / narrow_projection / batch_commit / stable_broker_id / runtime_observation

## 现象

固定窗口 `2026-08-31 06:00:00` 至 `2026-09-01 06:00:00` 中，`publication_tasks`
产生 550 条应用慢 SQL。最重的自然请求是 request `0ebf3a88`：
`POST /api/script-video-workflows/jobs/9e7f18db-3bdb-440a-8de4-2657dbda751e/images`
耗时 `382,617.64ms` 并失败，包含 52 条慢 SQL、累计 `190,466.73ms`。旧图片 child 查询指纹
`0b91e04fb500a3eb` 在后窗执行 80 次、累计 `315,128.20ms`、P95/最大
`13,976.37/16,985.38ms`；父任务宽读与逐 child 全行查询又贡献数十万毫秒。

该工作流当前已有 61 条图片 child。对相同生产父任务执行只读
`EXPLAIN (ANALYZE, BUFFERS)`，一次批量 child 查询返回 61 行的 executor 仅 `0.136ms`，
shared hit 51、read 0；现有 `ix_publication_tasks_parent_created` 已使用 452,005 次，
`ix_publication_tasks_feature_created_desc_v2` 已使用 107,905 次。秒级到十几秒等待来自重复远端往返和大 JSON
传输，不是 PostgreSQL executor 或缺索引。

## 影响

- 场景数直接放大 child 全集查询、父任务整行查询、事务和 broker 往返；一次 22 场景提交持续六分多钟并
  返回失败，用户无法判断哪些图片已真正入队。
- 旧查询加载共享任务模型的所有业务投影以及 `params/result`；大量不相关的报告音频、Publisher 字段也被
  网络传输。
- 每个 child 各自 commit，broker 返回后再持久化 Celery ID；网络长尾下会延长连接租约，并产生“任务已投递
  但 ID 未 durable”的歧义窗口。

## 根因

1. `_create_or_reuse_image_children()` 在每个 scene 内重新调用按父任务加载全部 child 的 helper，查询数随场景
   数增长；`enqueue_image_stage()` 还重复加载父任务。
2. `_workflow_or_raise()`、`_refresh_parent_image_state()` 和 child helper 使用完整 ORM，加载了与图片状态无关的
   大量投影列和 JSON。
3. `_create_image_child()` 在循环中逐项 insert/commit/publish/回写 broker ID；稳定 broker ID 未在首次事务中
   预分配。
4. 既有索引能在 `0.136ms` 内完成真实 61 行查询，因此新增索引不会消除应用 N+1、字节和往返成本。

## 解决思路

把一次图片提交收敛为“窄父任务读取 + 一次窄 child 批量预取 + 内存匹配 + 一次批量 insert commit + 释放
连接后逐条 broker publish”。每个 child 在首次事务内同时生成业务 ID 和 Celery task ID；成功路径不再做
broker 后数据库写，只有真实 publish 失败的 child 才以一次短事务统一标记 `queue_failed`。

## 实际方案

- 为图片 child 与工作流父任务增加显式 `load_only` 白名单；父任务 loader、父状态刷新和 child 批量 helper
  共用这些投影，禁止加载无关报告音频/Publisher 列。
- 场景循环前只查询一次全部现有 child，按 sequence + scene hash 在内存复用 completed/active/failed attempt。
- `_new_image_child()` 变为纯对象构造器，预分配 UUID 业务 ID 和稳定 Celery ID；全部新 child 用一次
  `add_all + commit` durable 落库。
- commit 时临时关闭 expire-on-commit，避免发布阶段访问稳定 ID 触发整行回读；每次 publish 走共享
  `apply_async_after_db_release` 并显式传 `task_id`。正常成功路径零 post-broker DB write；失败项汇总后一次提交。
- 没有执行 CREATE/DROP INDEX、ALTER、回填或生产配置修改；真实 EXPLAIN 与索引使用量明确反对 DDL。

## 验证

- TDD 红灯：4 场景旧实现执行 13 条 `publication_tasks` SELECT，并加载
  `report_audio_pdf_bytes`；broker publish 前数据库中的 `celery_task_id` 为空；完整 enqueue 仍出现父任务宽读。
- 修复后 4 场景 child 创建的 SELECT 不超过 2 条，完整 enqueue 不超过 4 条且不含报告音频/Publisher
  无关列；broker 调用前每个 child 的 durable ID 与实际 `task_id` 一致，Session 无活动事务。
- broker 故障注入证明两个 child 首次事务均 durable，只有失败 publish 的 child 转为 `failed/queue_failed`，
  成功项保持 `pending` 且两个稳定 broker ID 不变。
- 真实数据库 child 查询 61 行 executor `0.136ms`。定向脚本视频/API/service/通知/outbox 套件最终
  66 项通过；compileall、前端合同、Remotion typecheck、AST revoke gate、迁移 single-head、registry/bug
  strict 与 diff check 均通过。
- 06:38 重启安全门再次确认 47 条近期任务、0 个活动 sync、0 个不可中断任务；随后无 FORCE 标准整栈
  重启。最终 backend PID 77653 healthy，bootstrap 17/17、`errors=[]`，脚本视频 worker 所在最终进程组已加载
  新代码。
- 最终运行切点后尚未出现自然 `POST .../images`，因此只能提升到 `RUNTIME_REPLACED / OBSERVING`。
  在自然成功/失败提交与下一完整窗口前不得标记 CLOSED。

## 复发预防

- 多 child 创建/复用必须有与 scene 数无关的 SELECT 与 commit 预算；循环内不得调用重新加载同一集合的
  helper，也不得逐项 commit。
- broker ID 必须在首次 durable 事务生成，外部发布只携带纯 ID；成功路径禁止 broker 后第二次 UPDATE，失败
  只能窄写明确受影响项。
- `PublicationTask` 热路径必须用消费列白名单；任何新增共享模型列不得自动进入图片、轮询、列表投影。
- 应用 SQL 超过 500ms 而等价 executor 小于 10ms 时，先查往返、字节、Session hold 和 PgBouncer/网络，
  禁止用索引掩盖 N+1。

## 后续事项

- 已通过安全门加载新 backend/worker；下一条自然 `POST .../images` 仍需对账 request id、场景数、
  SELECT/commit 数、Session hold、broker ID 与失败收口。
- 从最终运行切点统计旧 origin/指纹、接口成功率/P50/P95/max、PgBouncer 六类错误和 idle-in-transaction；
  要求完整 24 小时并在后续窗口复核。短窗无自然提交只能记为未捕获。
