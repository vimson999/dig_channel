# DB 巡检漏报任务门禁的短暂 idle-in-transaction 观测

## 基本信息

- 日期：2026-09-10
- 状态：CODE_CHANGED / REPLAY_ACCEPTED / OBSERVING
- 严重级别：P1
- 影响模块：DB 日志巡检 / runtime guard / PostgreSQL Session
- 首次发现：2026-09-10 每日数据库性能巡检
- 修复提交：当前工作区
- 根因分类：日志格式演进未进入统一观测口径
- 复发键：`REGRESSION-ACCEPTANCE-INTEGRITY`、`DB-SESSION-LIFECYCLE`
- 是否复发：是；2026-08-06 已发生过门禁压力与数据库事件口径未汇合
- 关联记录：`2026-08-06-idle-transaction-pressure-observability-gap.md`、`2026-08-18-managed-sql-error-source-misclassified.md`
- 防线层级：observability / contract_test / replay_acceptance

## 现象

固定窗口 `2026-09-09 06:00:00` 至 `2026-09-10 06:00:00` 中，runtime guard 输出 31 个
`[TASK_GUARD] idle-in-transaction offenders ...` 观测；旧分析器却报告
`events ... idle_in_transaction=0` 且 `idle_in_transaction_pressure observations=0`。

这些样本的最大连接数为 3，最大事务年龄 `0.779s`、最大空闲年龄 `0.562s`，没有任务准入 defer，
远低于 60 秒 `idle_in_transaction_session_timeout`。它们不是长事务事故，但也不能被巡检写成“没有观测”。

## 影响

- 每日报告会把“没有长事务异常事件”错误扩写为“没有任何 idle-in-transaction 观测”。
- 后续无法区分短暂事务边界、准入压力和真正超时事故，复发趋势会出现假绿。
- 通过提高超时或忽略日志都无法解决口径缺失，反而会掩盖 Session 生命周期回归。

## 根因

分析器只识别空格形式 `idle in transaction`、旧字段
`idle_in_transaction_connections` / `idle_tx` 与准入 defer；runtime guard 后续新增的连字符格式
`idle-in-transaction offenders count=N details=[...]` 没有进入解析器。该格式还携带事务与 idle 年龄，
旧汇总既没有计数，也没有年龄边界。

## 解决思路

保留三种独立语义：数据库异常事件、任务准入压力、短暂 offender 观测。只为新格式新增结构化解析和
去重聚合，不把亚秒观测升级成事故，也不改 runtime guard、数据库超时或业务行为。

## 实际方案

- `analyze_db_performance_logs.py` 解析结构化 offender JSON，按秒与完整 details 签名去重。
- 新增独立输出 `idle_in_transaction_observations`，报告 episode 数、最大连接数、最大事务年龄和最大 idle 年龄。
- 负年龄按 0 处理；格式损坏时仍保留 header count，避免解析异常中断整轮巡检。
- 未修改数据库、PgBouncer、网络、索引或运行中的应用进程。

## 验证

- TDD RED：新增日志回放测试在旧分析器上找不到目标输出并失败。
- GREEN：定向测试通过，分析器完整套件 30/30 通过。
- 生产日志回放得到 `episodes=31 max_connections=3 max_xact_age_seconds=0.779 max_idle_age_seconds=0.562`。
- 同窗准入 defer 为 0、数据库 idle-in-transaction 异常事件为 0；现场只读快照也无 idle-in-transaction。

## 复发预防

- 日志生产者新增或修改关键格式时，必须用生产样本回放更新巡检合同。
- 每日 DB 巡检分别报告事件、准入压力与短暂观测，不允许用其中任一项的 0 替代另外两项。
- offender 汇总必须保留最大年龄，避免把亚秒边界与接近 60 秒超时的事故混为一谈。

## 后续事项

- 下一完整窗口继续观察 episode、最大年龄和准入 defer；只有年龄逼近阈值或出现 defer 才升级为业务修复。
- 当前修复仅改变离线分析器，无需重启在线服务。
