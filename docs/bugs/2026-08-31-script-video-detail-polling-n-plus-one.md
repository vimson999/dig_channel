# 文案到成片详情轮询重复加载子任务与父任务大 JSON

## 基本信息

- 状态：CODE_CHANGED / AWAITING_RUNTIME；两层根因修复和定向合同已通过，待最终全量回归、安全重启、真实 API 与后续窗口验收
- 严重级别：P0
- 影响模块：文案到成片工作台 / `GET /api/script-video-workflows/jobs/<id>` / `publication_tasks` / PgBouncer
- 首次发现：2026-08-31 watchdog 修复运行替换后的自然页面流量
- 修复提交：当前工作区
- 根因分类：详情 N+1、自动轮询大 JSON、过宽 ORM 投影、网络长尾、低价值状态轮询
- 复发键：`HOT-PATH-PAYLOAD-POLLING`、`DB-SESSION-LIFECYCLE`
- 是否复发：是；与历史详情 N+1、终态/隐藏页轮询和 `publication_tasks` 大 JSON 网络放大属于同一根因簇
- 关联记录：`2026-08-07-visual-storyboard-wide-polling-network-timeout.md`、`2026-08-10-report-audio-finalization-json-polling-n-plus-one.md`、`2026-08-31-pdf-merge-list-wide-polling.md`
- 防线层级：constant_query_budget / narrow_projection / dynamic_poll_dto / active_only_polling / content_hash_merge / runtime_observation

## 现象

自然页面流量在 14:53:40 至 15:12 短窗产生 17 次详情 GET，全部返回 200，但累计耗时
`70,782ms`，P50/P95/最大为 `1,820/18,836/22,073ms`。其中 12 条应用慢 SQL 累计
`28,921ms`：子任务指纹 `0b91e04fb500a3eb` 10 条累计 `17,240ms`、P95/最大
`4,410ms`；父任务指纹 `3560d73c229dcf15` 2 条累计 `11,681ms`、最大 `6,175ms`。
最慢请求连接持有 `21,773ms`，主要时间与 SQL 等待重合。

正常 request `fef03a25` 更直接暴露应用放大：28 个场景在序列化时逐个调用
`_current_image_child()`，同一条“按父任务加载全部图片 child”的 SQL 被执行 28 次，累计
`1,385.8ms`、最大 `202.49ms`。前端又把任何非终态都视为活动状态，每 4 秒拉一次完整详情，编辑中或
等待用户操作时也会持续回源。

第一层批量预取替换后，真实完整详情已从 28 次重复 child 查询降到父任务 + child 批次固定 2 条；但运行态
热请求仍出现 `10,607/6,211ms` 尾延迟，其中父任务 SQL 分别为 `10,024/5,735ms`。因此一次 N+1
下降不能当成闭环，本轮继续追到自动轮询仍搬运父任务约 29KB `params` 的第二层根因。

## 影响

- 场景数直接决定查询次数；28 场景的一次刷新可重复搬运同一组 child 28 次，多个页面或慢请求重叠时会
  消耗 PgBouncer 连接和数据库网络往返预算。
- 即使 N+1 消失，自动轮询仍读取 `source_text/audio_chunks/scenes` 等静态大 JSON；远端链路尾延迟会把
  单条轻 executor 查询放大到数秒至 10 秒以上。
- 编辑/空闲状态的 4 秒轮询没有业务进度可观察，却会持续刷新完整详情；分镜内容变更后若仅按 sequence
  合并 child 状态，还可能把旧图片结果误贴到新场景。

## 根因

1. `_workflow_payload()` 遍历场景时没有共享已加载 child 集合，`_current_image_child()` 每次重新查询同一
   父任务全部图片 child，形成按场景数增长的 N+1。
2. 详情 loader 复用完整 ORM，自动轮询和首次页面加载使用同一 DTO；父任务完整行约 `28,977B`，旧详情
   投影仍约 `28,952B`，而不含 `params` 的动态投影仅约 `528B`。
3. 前端缺少静态/动态数据分相：轮询每次覆盖完整 workflow，无法只合并 status/progress/logs 和图片状态；
   轮询条件也把所有非终态误当成正在执行。
4. PostgreSQL executor 不是根因。真实 child SQL `EXPLAIN (ANALYZE, BUFFERS)` 执行仅
   `0.028–0.031ms`、shared read 0，并使用既有
   `ix_publication_tasks_feature_created_desc_v2`；父主键查询 executor `0.018ms`。秒级等待来自远端
   PgBouncer/网络传输与重复往返，新增索引不会修复。

## 解决思路

保持完整详情和原始 JSON 审计数据不变，把首次/显式加载与自动状态轮询分相。完整详情固定一次父查询、一次
child 批量查询；轮询父查询禁止加载 `params`，只返回动态字段和按“场景序号 + 内容哈希”标识的图片增量。
前端只在实际执行阶段、页面可见时以有界周期轮询，并把增量合并到首次取得的静态 DTO。

## 实际方案

- `_workflow_payload()` 一次预取父任务全部图片 child，并把同一集合传入每个场景的匹配函数；列表完整模式
  同样跨多个父任务批量加载，查询数不随父/场景数增长。
- 历史列表默认改用 summary 投影；显式完整列表保留公开字段兼容。count 使用独立 `count(id)`，避免 ORM
  paginate 的 count 子查询扩展完整任务列。
- 新增 `?poll=1` 动态详情：父任务使用不含 `params`、标题和媒体宽列的白名单；child 轮询也不加载
  `params`，从创建时已持久化的 result metadata 读取 sequence/hash。响应不含 `source_text/scenes`。
- 完整场景 DTO 增加 `image_scene_hash`；轮询只返回 `scene_updates`。前端以
  `sequence:image_scene_hash` 合并，旧场景图片任务无法覆盖已编辑的新场景。分镜规划从 active 转出时只额外
  完整刷新一次，以取得新生成的静态 scenes。
- 自动轮询由 4 秒改为 10 秒，只覆盖 audio/storyboard/images/render 活动阶段；隐藏页暂停，完成、失败、
  编辑等待等状态停止。页面初始加载和人工刷新仍使用完整详情，字段完整性不牺牲。
- 未删除或改写 `params/result` 原始 JSON，没有加 timeout、限流或重试掩盖问题；没有执行 DDL、回填或生产
  配置变更。现有索引和 executor/buffer 证据明确反对新增索引。

## 验证

- TDD 红灯先捕获：3 场景详情执行 4 条任务查询；完整列表按父任务重复查询 child；轮询响应不存在且加载
  `params`；前端缺少动态合并/active-only 合同。修复后 3 个核心轮询/查询预算测试转绿。
- 第一层运行替换后，真实完整 API request `b75a3542` 返回 200/`5,424.95ms`、`119,146B`，28 场景、
  5 个音频块、visual scheme、logs、storyboard 等字段完整；任务查询固定 2 条，旧 28-query N+1 为 0。
  后续热请求 child 查询仅 `31.89/32.00/28.18ms`，证明 child 批量化生效；父完整 JSON 查询仍出现
  `10,024/5,735ms` 尾延迟，故继续保持未闭环并实施动态轮询。
- 动态轮询最终全量测试、运行替换、真实响应大小/SQL/request id、idle-in-transaction 与后续窗口证据待
  本轮后续补充；这些证据完成前不得标记 CLOSED。

## 复发预防

- 多场景/多子任务详情必须有与场景数无关的 SQL 预算测试；循环内不得调用会重新加载同一集合的 helper。
- 自动轮询必须使用显式动态 DTO；静态大 JSON 只允许首次加载、用户显式刷新或动作响应读取。轮询查询与
  响应都必须用投影列、SQL 数和 payload 大小预算锁定。
- child 增量合并必须携带版本或内容哈希，不能只按序号/ID 覆盖；内容变化后的旧 delivery 必须 fail closed。
- 轮询同时满足 Page Visibility、active-only、single-flight、终态停止和有界间隔；测试通过不能替代真实
  request id、SQL origin、Session hold 和后续窗口复核。

## 后续事项

- 完成最终全量回归后只按安全门允许的窗口标准重启，不使用 FORCE；复验完整详情字段、动态轮询字节、固定
  SQL 数、父查询不含 `params`、场景哈希合并和分镜转段的一次完整刷新。
- 从新 backend/watchdog 切点统计完整 24 小时端点请求量、成功/失败、累计/P50/P95/max、两类 SQL 指纹、
  Session hold、5xx 与 PgBouncer 六类错误，并在下一后续窗口再次确认。短窗“未出现”只能记为未捕获。
- 网络根因继续 `OPEN / REPEATED`；若 executor 仍低于 10ms 而应用超过 500ms，继续查 PgBouncer、网络、
  payload 与往返，禁止盲目新增索引。
