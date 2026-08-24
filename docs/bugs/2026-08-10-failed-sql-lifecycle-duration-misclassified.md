# 失败 SQL 耗时被错记为非 SQL Session 持有

## 基本信息

- 状态：新代码已由本地 backend/worker 加载；等待下一条真实失败 SQL 样本校准，`RUNTIME_REPLACED/OBSERVING`
- 严重级别：P0
- 影响模块：SQLAlchemy database logger / Session 生命周期审计 / PgBouncer timeout 根因归类
- 首次发现：2026-08-10 日巡检
- 修复提交：当前工作区
- 根因分类：observability regression / failed SQL timing / Session attribution
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：是；连接长持有与 query timeout 已多轮复发，本轮发现其归因数据被埋点缺陷污染
- 关联记录：`2026-08-06-idle-transaction-pressure-observability-gap.md`、`2026-08-08-postgres-tunneled-route-query-timeout.md`
- 防线层级：observability / contract_test / runtime_acceptance

## 现象

真实 request `3b1274e9` 在 2026-08-10 03:03:14 请求
`GET /api/v1/celery/status`。PgBouncer 于 03:03:34.986 取消等待中的查询，应用在
03:03:44.500 记录 `_business_tasks_snapshot` SQL 失败，最终 HTTP 200/30.205s。

同一 checkout 生命周期却记录 `duration_ms=29861.65`、`sql_count=2`、
`sql_total_ms=190.27`、`non_sql_hold_ms=29671.38`。失败 SQL 的诊断日志显示
`duration_ms=0`，把约 20 秒数据库等待错误归到“应用在 Session 上做外部 I/O”。

## 影响

- PgBouncer/网络等待会被误判为应用 sleep、Celery inspect 或外部 I/O 持有。
- 本窗口 154 个长持有样本中的 `non_sql_hold_ms` 累计 2,032.406s 不能直接用于根因排序。
- 按错误归因继续修改事务边界不能消除真实 query timeout，还会掩盖网络复发。

## 根因

`DatabaseLoggerMiddleware._query_start_times_quietly()` 只保留了 `connection is None`
分支，正常连接的 `connection.info.setdefault("query_start_time", [])` 被误缩进到
`_record_checkout_sql_duration()` 的 `except` 之后，成为不可达代码。

成功 SQL 由 `after_cursor_execute` 计算耗时；失败 SQL 只能经 `handle_error` 取得开始时间。
accessor 返回 `None` 后，失败 SQL 只增加次数而耗时恒为 0，checkout 总时长的剩余部分遂被
错误标成 non-SQL。

## 解决思路

保持现有 SQLAlchemy 事件模型，只恢复 accessor 的容错返回，不改变事务、重试、超时或业务
数据；先用 20 秒失败 SQL 定向测试证明红灯，再实施最小修复。

## 实际方案

- 恢复对 `connection.info["query_start_time"]` 的安全访问。
- 删除 `_record_checkout_sql_duration()` 中不可达的错误缩进代码。
- 新增失败 SQL 时长必须同时进入 checkout `count/total/max` 的契约测试。

## 验证

- 修复前定向测试：`sql_total_ms=0`，预期 20,000ms，失败；修复后通过。
- 相关生命周期、finalization、API 与投影模块 177 tests passed。
- 安全重启门禁确认无不可中断任务；新 backend PID `64496`、worker/scheduler ready、health 200。
- 当前没有人为制造数据库故障，尚无真实失败 SQL 验证非零失败耗时，因此不能标记 CLOSED。

## 复发预防

- 长 checkout 被归类为外部 I/O 前，必须确认其中失败 SQL 也有非零耗时。
- database logger 的成功与失败事件必须分别有时长聚合测试。
- 下一条真实 query timeout 必须满足 `sql_max_ms` 覆盖失败等待，且剩余 non-SQL 与调用链相符。

## 后续事项

- 下一完整 24 小时优先寻找新 PID 后的失败 SQL 样本；没有样本只能继续 OBSERVING。
- 网络根因仍由 `2026-08-08-postgres-tunneled-route-query-timeout.md` 跟踪；本修复只校准证据。
