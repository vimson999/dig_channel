# 后端进程 bootstrap 在凭据检查后跨进程启动持有数据库 Session

## 基本信息

- 状态：代码与全量回归已修复，新 PID 已加载并完成 bootstrap 长持有验收，待后续窗口持续观察
- 严重级别：P1
- 影响模块：后端进程 bootstrap / ZSXQ 配置状态 / 平台凭据 / SQLAlchemy Session
- 首次发现：2026-08-06
- 修复提交：待提交
- 根因分类：只读配置查询成功后未结束 Session / 重复状态查询 / Session 跨进程检查与 shell I/O
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：是
- 关联记录：`2026-07-21-watchdog-repair-idle-transaction-and-poll-overlap.md`、`2026-07-24-db-session-crossed-broker-and-remote-io.md`、`2026-08-06-idle-transaction-pressure-observability-gap.md`
- 防线层级：local_patch / contract_test / observability

## 现象

第一次安全重启加载新的连接生命周期归因后，bootstrap 线程立即记录一条 13,544.87ms 的连接
持有：`checkout_origin=services/platform_credential_service.py:618:has_active_credential`，其中 9 条
SQL 累计仅 487.47ms，`non_sql_hold_ms=13057.40`。线程名明确为
`backend-process-bootstrap`，不是前台请求。

## 影响

- 后端启动时一个只读凭据检查会跨越 Celery worker、watchdog、scheduler 的进程检查和启动等待。
- 连接池在最需要并发启动探针的阶段少一个可用连接，放大 PgBouncer/网络等待。
- 旧日志只能看到“连接持有过久”，无法区分 0.5 秒 SQL 和 13 秒非 SQL。

## 根因

`config_status()` 通过 `_runtime_cookie_configured()`、`_runtime_db_cookie_configured()` 和
`_runtime_cookie_source()` 三条路径重复检查同一个数据库凭据，单次状态快照最多重复三次查询。
`_runtime_db_cookie_configured()` 只在异常时 rollback/remove，成功时保留 scoped Session。
bootstrap 随后继续执行进程状态检查和 shell 启动，导致成功的只读 checkout 被跨 I/O 持有。

## 解决思路

1. 一次配置状态只采集一次凭据快照，所有派生字段复用。
2. 该检查是独立只读操作，无论成功失败都立即 rollback/remove。
3. 用连接 SQL/non-SQL 拆分做运行态验收，避免仅按函数入口猜测。

## 实际方案

- 新增 `_runtime_cookie_state()`，一次计算 configured/db/source。
- `config_status()` 和 `_snapshot_config()` 复用同一快照，不再做三次 DB 检查。
- `_runtime_db_cookie_configured()` 在 `finally` 中统一执行 `_reset_db_session`，保证成功路径也释放。
- 新增合同测试，断言一次状态只采集一个快照，凭据检查成功时同样清 Session。

## 验证

- 新增定向合同测试通过；相关定向回归 27 项通过。
- 完整 unit 1,028 项通过；`compileall`、严格 Bug 检查和 `git diff --check` 通过。
- 2026-08-06 09:59:39 门禁确认无不可中断任务后完成安全重启；新后端 PID `47594` 在
  `2026-08-06T02:01:48Z` 启动。新 PID 没有出现 `backend-process-bootstrap`/
  `has_active_credential` 的长连接持有，旧 PID `37909` 的 13.545s 样本仅保留为历史基线。

## 复发预防

- bootstrap、watchdog、scheduler 的配置探针必须返回纯快照并在进入进程、broker、网络或 shell I/O 前结束 Session。
- 同一 API/状态快照不得通过多个派生字段重复查询同一配置事实。
- 新连接日志以 `checkout_origin + sql_total_ms + non_sql_hold_ms + thread` 作为生命周期复发证据。

## 后续事项

- 继续观察新 PID 的 bootstrap origin；若同类长持有复发，沿新的 origin 排名继续横向排查。
