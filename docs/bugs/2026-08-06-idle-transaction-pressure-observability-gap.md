# DB 巡检漏报任务门禁捕获的 idle-in-transaction 压力

## 基本信息

- 状态：offender 与连接来源归因已落地，待新进程和下一完整窗口验收
- 严重级别：P1
- 影响模块：DB 日志巡检 / runtime guard / Celery 任务准入 / PostgreSQL Session
- 首次发现：2026-08-06
- 修复提交：待提交
- 根因分类：跨观测面漏报 / Session 生命周期证据未汇合
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：是
- 关联记录：`2026-06-24-watchdog-idle-in-transaction.md`、`2026-07-21-watchdog-repair-idle-transaction-and-poll-overlap.md`、`2026-07-24-db-session-crossed-broker-and-remote-io.md`
- 防线层级：observability / contract_test / read_only_diagnostics

## 现象

2026-08-05 06:02:19 至 2026-08-06 06:02:19 的日志分析最初输出
`idle_in_transaction=0`，但 Celery 任务准入日志在同一窗口内记录了 12 次
`reason=db_idle_in_transaction_pressure`，影响 10 个任务。对应 retry 日志明确记录
`idle_tx=6/8/6/7`，另一个准入快照记录 5 条 idle-in-transaction 连接。

## 影响

- 巡检可能把“PostgreSQL 错误日志没有匹配行”误写成“窗口内没有发生 idle-in-transaction”。
- 任务实际因数据库事务压力被延迟，却无法进入每日基线和复发排名。
- 历史上反复出现的 Session 跨外部 I/O 问题会失去跨轮验收证据。

## 根因

日志分析器只识别 PostgreSQL/应用异常文本中的 `idle in transaction`，并为了避免把
`SELECT ... WHERE state = 'idle in transaction'` 这类诊断 SQL 当成事件而主动过滤查询文本。
这一防误报是必要的，但分析器没有补充读取 runtime guard 的结构化压力证据，造成另一个
方向的漏报。

实时诊断脚本还没有强制事务只读，且代表性的 `publication_tasks` 查询仍使用已经被业务
投影列替代的旧 `params ->> 'feature'` 条件，可能让后续索引判断偏离真实 SQL。

## 解决思路

1. 保留 PostgreSQL 错误事件与任务门禁证据两个独立口径，禁止相互冒充。
2. 对门禁 defer 和 `idle_in_transaction_connections` / `idle_tx` 数值去重统计。
3. 实时统计与 EXPLAIN 全程强制只读事务。
4. 诊断样本使用业务真实的 `feature` 投影条件。

## 实际方案

- `analyze_db_performance_logs.py` 新增 `idle_in_transaction_pressure` 汇总：defer 次数、受影响任务数、数值观测数和最大连接数。
- 继续单独输出 `events ... idle_in_transaction=0`，明确它只代表异常文本未捕获，不覆盖门禁观测。
- `db_bottleneck_report.py` 在任何统计或 EXPLAIN 前执行 `SET TRANSACTION READ ONLY`，并将 statement timeout 设为事务局部值。
- Publisher 与 Content Pipeline 的代表 SQL 改用 `PublicationTask.feature`，不再用旧 JSON 条件评估执行计划。
- runtime guard 只读压力 SQL 现在受限采样至多 5 个 offender，记录 application、client、事务/idle/query 年龄、wait event 和查询 MD5 指纹，不记录原始 SQL；相同来源 60 秒限频。
- 连接 checkout/checkin 日志新增 request id、Celery task id/name、checkout origin、SQL 次数/累计/最大耗时及 non-SQL 持有；分析器按 origin 排名连接持有。
- runtime guard 默认快照缓存从 5 秒提高到 15 秒，减少多进程重复压力探针，同时保留任务 feature 并发门禁。
- 新增解析和源代码契约测试，覆盖两种门禁数值格式、只读事务和真实投影 SQL。

## 验证

- 定向单元测试通过；完整验证结果记录在 `reviews/2026-08-06-db-performance-audit.md`。
- 修复后同一窗口识别 12 次 defer、10 个受影响任务，最大已记录 idle-in-transaction 数为 8。
- 只读诊断回显 `transaction_read_only=on`。
- 真实 Publisher feature 查询使用 `ix_publication_tasks_feature_created_desc_v2`，executor 0.072ms；Content Pipeline 代表查询 1.532ms。
- 新 offender SQL 已在真实 PostgreSQL 只读执行并返回当前 `idle_tx=0`；新进程连接日志已验证输出 `checkout_origin`、`sql_total_ms` 和 `non_sql_hold_ms`。

## 复发预防

- 每日 DB 巡检同时报告数据库异常文本、PgBouncer、Session 持有和 runtime guard 压力，任何一个口径为零都不得替代其他口径。
- 诊断脚本必须 fail-safe 为只读，并用当前业务 SQL/投影列采集 EXPLAIN。
- idle-in-transaction 再次触发时直接按受限 offender 指纹与连接 checkout origin 关联，不凭连接数猜来源，也不在日志泄露原始 SQL。

## 后续事项

- 下一完整窗口确认门禁压力是否消失；若复发，采集具体 offender 而不是继续只看聚合数。
- 需要运行时加载的业务修复仍必须通过重启安全门禁，不能因巡检自动强制重启。
