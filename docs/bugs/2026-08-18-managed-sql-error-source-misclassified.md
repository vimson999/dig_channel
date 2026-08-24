# DB 巡检把托管日志中的 SQL 失败误分为临时诊断

## 基本信息

- 状态：CODE_CHANGED / OBSERVING
- 严重级别：P1
- 影响模块：DB 日志分析器 / scheduler / PgBouncer / 复发归因
- 首次发现：2026-08-18 日常数据库巡检
- 根因分类：来源元数据缺失、跨日志去重、分类优先级错误
- 复发键：`REGRESSION-ACCEPTANCE-INTEGRITY`
- 是否复发：是；与历史巡检漏报/误归因属于同一验收完整性簇
- 防线层级：source_aware_classifier / replay_contract

## 现象与根因

本窗口两条 `publication_tasks` OperationalError（server closed）来自 `logs/scheduler.log`，但日志字段为 role unknown、process `-`、origin unknown。分析器只依赖字段，将其计入 ad-hoc diagnostic，导致应用 SQL 错误被压成 0；slow-query 与错误日志跨源去重时也没有把托管来源升级回来。

## 实际修复

分类器新增 `source_path`：app/backend/scheduler/celery 日志优先视为托管应用来源；同一 SQL 错误从其他日志获得托管证据时升级分类。生命周期样本保留来源，避免缺 role/process 时再次丢失。

## 验证与下一轮验收

回放测试在旧签名先失败，修复后分析器 20 个测试通过；重跑本窗口后应用 SQL 错误正确为 2、ad-hoc 为 0，托管连接持有中的 ad-hoc 也为 0。`slow_query.log` 单独缺失全部来源字段时仍无法反推出调用者，这是明确捕获边界；下一轮要求 SQL error、lifecycle、PgBouncer 三源保持一致并继续补齐生产 role/process/origin。

## 2026-08-19 来源文件优先级过度修正

本轮唯一 server-closed SQL 明确带 `role=unknown process=- origin=unknown`，是 stdin 诊断进程，但因共享 logger 落入 `scheduler.log`，旧规则又把它升级为 application error。回归现规定：显式 shell 进程 `-/-c/python/python3` 且 role/origin 全未知时优先归 ad-hoc；只有缺 process 元数据且来自 app/backend/scheduler/celery 的记录才由来源文件补全为托管。测试先红后绿，分析器 20 tests 通过；重跑窗口由 application error 1 / ad-hoc 0 修正为 application 0 / ad-hoc 1。状态继续 OBSERVING，因为共享文件仍无法替代生产 role/process/origin。
