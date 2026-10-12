# 导入独立调度器时单元测试污染生产日志

## 基本信息

- 日期：2026-09-19
- 状态：CLOSED / RUNTIME_ACCEPTED（2026-09-22：连续自然周期正常；本窗仅见运行前历史测试污染）
- 严重级别：P1
- 影响模块：B站情报独立调度器、单元测试、数据库巡检观测
- 首次发现：09-18/19 固定窗口巡检
- 修复提交：当前工作区
- 根因分类：模块导入时配置全局 logging.FileHandler 的副作用
- 复发键：`REGRESSION-ACCEPTANCE-INTEGRITY`
- 是否复发：09-18 和 09-19 两轮均有测试模拟异常混入独立生产日志
- 关联记录：`2026-09-18-bilibili-intelligence-hourly-connect-failure.md`、`2026-08-18-managed-sql-error-source-misclassified.md`
- 防线层级：contract_test / observability / runtime_acceptance_pending

## 现象与影响

09-18 06:09–06:16 的三轮全量测试向 `logs/bilibili_intelligence_scheduler.log`
写入 `for test`、`publish-task-1`、`sync-disconnect-1` 等 15 条模拟断连文本、
3 条 SQL traceback，以及 `status=partial total=2` 的假同步。09-19 固定窗 PgBouncer
六类故障均 0，不能把这 15 条当作应用断连或把假 partial 当作真实整点失败。
这种混写也使真实调度器的失败率、SQL 归因和复发判断不可直接从原始计数得到。

## 根因与方案

`tests/unit/test_bilibili_intelligence_scheduler.py` 导入 runner；runner 在模块顶层
执行 `logging.basicConfig(handlers=[logging.FileHandler(LOG_PATH), ...])`，把全局 root
logger 连接到真实调度器文件。测试的其他模块异常随即沿 root handler 写入该文件。
将目录创建和 handler 安装移到 runner 的 `main()` 显式入口，保留独立进程启动时
原有格式、级别和文件路径；普通模块导入不改变 logging 拓扑。

## 验证与复发预防

- 测试先复现：新解释器仅 import runner 就发现真实生产文件 handler；另一个
  启动合同在修复前因无 `_configure_logging()` 失败。修复后 15 项调度器单测通过。
- 新解释器调用显式配置后，临时 scheduler 文件收到 marker；仅 import 不挂载
  生产文件。09-19 06:05 的定向回归没有向独立生产日志写入 `partial` 或 `for test`。
- 原有 PID 72430 已在 09-18 06:18 启动且真实整点继续写日志；不为纯导入副作用
  冒险重启在运行服务。下一次正常重启须核对文件日志、单实例锁和真实 6/6 周期。
- 巡检必须按来源/进程/request/task 关联结构化 SQL、traceback、PgBouncer；
  `for test` 和 `sync-disconnect-1` 不得计作生产事故，亦不能掩盖真正错误。

## 2026-09-20 后窗复核

固定窗原始分析器仍数到 14 条 `server_closed` 和 2 个 SQL traceback，但逐条时间线显示均集中在
09-19 06:07–06:12，包含 `for test`、`publish-task-1`、`sync-disconnect-1`、SQLite 建删表和
fake timeout，属于上轮修复前同一次单测污染。其后至固定窗结束没有新污染文本；24 个真实 B站
整点周期均为 6/6 completed。由于当前独立进程仍早于代码修复启动，本轮安全重启后还须验证新 PID
的文件 handler、单实例锁及下一次自然周期；重启前保持 CODE_VERIFIED/OBSERVING。

06:21 双安全门通过后标准整栈重启；新 B站情报 PID 24032 于 06:22:47 启动，启动日志只含
自身初始化与 schedule 配置。06:17–06:21 的两轮全量测试没有再向该独立文件写入测试 partial、
fake timeout 或 SQLite 建删表文本，证明导入期 handler 污染已在测试侧消失。代码与进程均已替换，
但仍须下一次真实整点周期和完整后窗确认，因此状态提升为 RUNTIME_REPLACED/OBSERVING，不关闭。

## 2026-09-22 后续运行验收

修复进程后的固定窗口中，22 个 B站情报自然周期全部 `6/6 completed`、失败 0；没有新的测试 marker 写入
独立调度器日志。分析器仍读到 10 条 server-closed 与 2 个 traceback episode，但全部集中在 06:22–06:24
的单元测试故障注入，带 `unittest.mock`、`for test` 或 `sync-disconnect-1`，不属于生产调度器自然进程。
这说明“导入调度器模块就把 root logger 挂到生产文件”的根因已在新进程和后续自然周期中消失。

记录标记 CLOSED/RUNTIME_ACCEPTED；巡检仍须按 PID、来源与测试 marker 隔离原始计数，避免把历史污染重新归因
为生产数据库断连。任何新自然进程写入测试 marker 都应立即按同一复发键重开。
