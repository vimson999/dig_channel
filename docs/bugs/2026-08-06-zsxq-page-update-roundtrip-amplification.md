# ZSXQ 页内主题更新与同步心跳逐条往返放大

## 基本信息

- 状态：主题/评论页级批量根因已在新 worker 验收；新增附件 INSERT 已有正向证据，继续观察长尾
- 严重级别：P1
- 影响模块：ZSXQ 全 tabs 同步 / `zsxq_topics` / `zsxq_sync_runs` / SQLAlchemy Session
- 首次发现：2026-08-06
- 修复提交：待提交
- 根因分类：页内逐条宽 UPDATE / 高频同步心跳提交 / 远端往返放大
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：是
- 关联记录：`2026-07-21-zsxq-sync-point-query-amplification.md`、`2026-07-22-zsxq-topic-unchanged-payload-rewrite.md`、`2026-07-24-zsxq-comment-rewrite-stale-db-pool-index-sprawl.md`
- 防线层级：local_patch / contract_test / runtime_acceptance

## 现象

2026-08-06 09:46–09:48 的活动全 tabs 同步在同一页对 `zsxq_topics` 逐主题执行宽 UPDATE，
来源为 `services/zsxq_sync_service.py:5792:_update_existing_topic`，实测单条 1,748.18–2,534.62ms；
同一任务还约每个主题执行一次 `UPDATE zsxq_sync_runs`，单条约 0.67–1.02s。当前活动任务
`4a1414c6-60e4-4fbe-b61f-0fc167dcfef7` 的 payload 包含 `comments_count`、`likes_count`、
`reading_count`、`user_specific` 等平台动态字段，完整 payload hash 变化会保留原始 JSON 并触发
结构化投影更新，逐行发送到远端 PostgreSQL。

## 根因

页快照已经一次性加载，但 existing topic 仍在每条主题上单独发 UPDATE；同步心跳虽节流了 result
JSON，仍在每条主题上更新投影列和 `updated_at` 并 commit。远端连接/PgBouncer 的分钟级等待把每个
往返放大为秒级慢查询，不能用缺索引解释；现有 topic 唯一条件和 EXPLAIN 证据也不支持盲加索引。

## 实际方案

- 将 existing topic 更新拆成“准备投影”和“执行更新”两步，按相同字段集合聚合，在页内通过 Core
  `executemany` 一次发送一组 UPDATE；payload hash、结构化字段和原始 JSON 仍按原规则完整保存。
- 同步心跳提交按最多 30 秒一次节流，页末强制提交最终进度/result；带附件下载的分支在外部 I/O 前
  先刷掉待提交的主题更新，避免内部 commit 重复执行批次。
- 不改表、不改索引、不删除或截断任何原始 JSON，不改变旧 `_update_existing_topic` 单条兼容入口。

## 验证

- 新增批量更新字段形状和 30 秒心跳上限测试；ZSXQ 查询纪律与投影纪律定向 115 tests 通过。
- 完整 unit `1,030 tests` 通过（2026-08-06 09:56:12），随后新增附件批量路径后的完整 unit `1,031 tests`
  通过（2026-08-06 10:37:38），页边界收紧后再次通过 1,031 tests（10:51:12），下载边界修复后
  再次通过 1,033 tests（11:52:34）；`compileall`、
  `git diff --check`、Bug 严格检查 148/148、recurrence registry JSON 均通过。
- 11:07 门禁确认无不可中断任务后完成安全重启；新后端 PID `55059` 健康，
  `zsxq_sync@Mac-mini-3.local ready` 于 11:08:36 出现，页级批量路径已加载。11:10–11:24 的
  正向系统同步完成 4 页/78 topics、更新 68，但该 worker 尚未包含后续显式
  `flush/no_autoflush` 追补，故仍不能把这轮的隐式 flush 长尾当作最新代码结果。
- 11:32–11:34 的最新旧边界 worker 任务完成 4 页/78 topics、更新 67；日志仍出现大量单行
  `UPDATE ZSXQ_TOPICS`。复盘发现原因不是批量方法失效，而是 `download_attachments=True` 时
  下一主题前主动刷掉 `pending_topic_updates`，把每个 existing topic 分裂成一条往返。该分支已移除，
  新增回归验证两条 existing topic 在下载提交边界仍只做一次批量应用。
- 11:47 门禁提升权限复核全绿，随后重启完成：后端 PID `60726`，11:49:09 health healthy，
  ZSXQ worker 11:49:15 ready。最新 worker 已载入该修复，但尚未产生一轮“下载开启且主题有变化”的
  真实数据，因此目前只报告代码/测试/重载完成，不宣称运行态 UPDATE 次数已下降。
- 11:50:43–11:52:21 的新 worker 任务 `e5ce5e4f-38f3-456b-987f-b0b763c1c55d` 提供了正向证据：
  4 页/78 topics，`topics_updated=62`，下载开启但本轮无新增下载；`UPDATE ZSXQ_TOPICS` 从旧轮次
  78 次单行操作（累计 13,379.76ms）降为 7 次批量操作、合计影响 78 行（累计 5,013.02ms），
  round-trip 次数下降 91.0%，主题更新耗时下降 62.5%。7 次批量分别影响 18、2、5、14、20、10、9 行，
  未再出现单行 UPDATE。该收益是主题页批处理本身的真实验收，不把慢的附件快照 SELECT 归因于缺索引。
- 同窗口同步任务完成 96.563s；分析器捕获 5 条慢 SQL、累计 8,127.62ms，P50/P95/最大
  1,632.33/1,953.34/1,953.34ms，慢指纹为 `_apply_existing_topic_updates`、
  `_load_existing_attachment_snapshots`、`_find_existing_downloaded_attachment`，均为窄投影/点查叠加
  PgBouncer 等待；没有 connection hold、SQL 错误、statement/lock/query/connect timeout 或 idle-tx。
- 12:32–12:33 新 worker `67018` 消费自动全 tabs 任务 `8d8d1ba6-e0ef-4f31-ac17-d3b0141baff1`，
  完成 7 页/115 topics、58.521s。评论对账已表现为页级
  `_load_existing_topic_comment_snapshots`：两次页读影响 258/418 行，慢度 1,128.76/924.55ms，
  没有再出现 `_upsert_topic_comments` 逐主题慢查询或连接持有；当前低频 1 秒级读仍归因远端等待，
  不是索引缺失。
- 12:45:54 重载到最终 worker 后，带附件任务 `a4388ac3-405e-47bf-9055-3eed019b7c45` 于
  12:46:17–12:46:55 完成 4 页/78 topics、更新 77；`UPDATE ZSXQ_TOPICS` 仍按页批量执行，
  仅两次批量指纹（1,043.09/1,537.22ms），未退化为逐主题 UPDATE。该窗口 3 条慢 SQL 累计
  6,011.45ms、无 connection hold/SQL 错误/timeout/idle-tx；附件快照 3,431.14ms 仍是远端
  窄投影长尾，不作为索引缺失证据。

## 复发预防

- 热循环必须按页/批次预算数据库 round trip，不能因 JSON result 节流而保留逐条心跳 commit。
- 任何批量更新必须按字段形状分组，兼容 payload hash 列缺失的历史 schema，并保留原始 JSON 审计载荷。
- 运行态验收需比较同类同步页的 `zsxq_topics` UPDATE 次数、`zsxq_sync_runs` 心跳次数和连接
  `sql_total_ms/non_sql_hold_ms`，不能只看应用总耗时。

## 后续事项

- 主题更新、评论页级批次和最终外链 DTO 边界已经由新 worker 真实验收；新增下载样本仍需继续观察
  新行 `INSERT`、附件状态批量 UPDATE 与外部下载之间是否保持无隐式 flush 错位。
- 保持 heartbeat 不超过 30 秒，并继续把 `_load_existing_attachment_snapshots` 的远端等待与
  PostgreSQL executor 分开观测；没有新的 EXPLAIN/索引使用证据前不改索引。
- 连续终态通知的过期 ORM 根因另见 `2026-08-06-zsxq-workflow-notification-expired-session.md`，
  需在新 worker 加载后验证。
