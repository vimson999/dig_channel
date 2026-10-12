# Scheduler API 导入污染生产日志

## 基本信息

- 日期：2026-09-24
- 状态：RUNTIME_ACCEPTED (API) / RUNNER_CODE_VERIFIED / OBSERVING
- 严重级：P1
- 影响模块：`scheduler/task_scheduler.py`、`run_scheduler.py`、`/scheduler` API、单元测试、DB 巡检观测
- 根因分类：可导入调度库在 import 期给全局 root logger 安装生产 `FileHandler`
- 复发键：`REGRESSION-ACCEPTANCE-INTEGRITY`
- 是否复发：是；09-19 已修 B 站独立 runner，本轮发现通用 scheduler 包仍有同型副作用
- 防线层级：import_contract / logging_isolation / runtime_replay

## 现象与影响

`2026-09-23 06:26:55–06:27:51` 的固定窗口原始日志记录 5 个
`server closed the connection unexpectedly` 事件和 1 个 SQL traceback。逐条回放发现其中包含
`for test`、`publish-task-1`、`sync-disconnect-1` 和 `unittest/mock.py`，全部来自单元测试故障注入；
同窗 PgBouncer 八类故障、结构化 SQL error、timeout 和长 Session hold 均为 0。

这些记录却被写入 `logs/scheduler.log`，会把回归测试误报为生产数据库断连，污染失败率、
SQL 根因和复发判定。与 09-19 的 B 站独立 runner 问题不同，本次入口是 Web 应用注册
`apis.v1.scheduler` 时导入通用 `scheduler` 包，因此任何 `create_app()` 的测试进程都可受影响。

## 根因

`scheduler/task_scheduler.py` 在模块顶层调用 `logging.basicConfig()`，并立即创建
`RotatingFileHandler(logs/scheduler.log)`。`apis/v1/scheduler.py` 只是为了复用调度状态和手动触发函数，
但 import 动作已改变整个进程的 logging 拓扑。`run_scheduler.py` 自身也有同型顶层配置，
所以仅删除库模块一处仍会在导入 runner 时复发。

## 已落地修复

- 删除 `scheduler/task_scheduler.py` 的 import-time 目录创建和 root handler 安装；可导入调度库只保留
  模块 logger，由宿主应用决定日志去向。
- 将 `run_scheduler.py` 的生产 handler 配置移入 `_configure_logging()`，只在 `main()` 显式入口调用；
  命令行启动仍保留原有轮转文件和 stderr 日志。
- 新增新解释器合同，分别导入 API 和 runner，两者都不得出现 `scheduler.log` root handler。

## 验证

- TDD RED：修复前单独 import `apis.v1.scheduler` 和 `run_scheduler` 都返回
  `logs/scheduler.log` handler，两条合同分别因真实副作用失败。
- GREEN：`tests.unit.test_scheduler_logging` 2/2 通过。
- 现场回放：重跑本窗 5 个原始故障注入测试，全部通过；
  `scheduler.log` 中四类稳定测试 marker 计数在前后均为 53，没有新污染。
- 完整 unit `Ran 1717 tests in 103.326s / OK`；测试后 `scheduler.log` 的四类稳定 marker 仍为 53。
- 06:40 双重安全门读取 46 条近期任务/0 个活动 ZSXQ sync，未用 FORCE 完成标准整栈重启。
  新 backend PID `91625` health 200，bootstrap 17/17 Celery service、Watchdog 和 6/6 scheduler 均 `ok=true`、
  `errors=[]`；新 worker/pool 均有 ready 记录。
- `lsof -p 91625` 确认 backend 未打开 `scheduler.log`，真实只读 health、ZSXQ status 与 Insights overview 均 200。
  API import 路径已运行替换；通用 runner 本机未配置为托管 scheduler，因此其显式启动仍只有代码合同证据。
  不能仅凭重启标记 CLOSED。

## 复发预防与后续验收

- 任何 scheduler/worker/watchdog 的可导入库、API 依赖和 runner 模块都不得在 import 期安装全局
  `FileHandler`、创建 PID/锁文件或启动线程；生产副作用只允许在显式入口。
- API import 路径已在安全重启后核对新 PID、health 与打开文件；下一轮全量测试期间
  `scheduler.log` 的测试 marker 必须继续保持不增。
- 下一完整 24 小时窗口中，只有可追溯至真实 scheduler 进程的自然记录才能进入生产故障口径；
  无新测试 marker 后才可标记 CLOSED/RUNTIME_ACCEPTED。

## 2026-09-25 完整后窗复核

固定窗 scheduler 日志没有新增 `for test`、`publish-task-1`、`sync-disconnect-1` 或
`unittest.mock` marker；历史总数仍为 53。应用、traceback 和 PgBouncer 三路的自然 server-closed、
statement/lock/query/connect timeout 均为 0。API import 路径获得完整后窗证据，可升级为
**RUNTIME_ACCEPTED**；通用 runner 本机仍无受管实例，显式入口只具代码合同，整体记录保持
`RUNTIME_ACCEPTED (API) / RUNNER_CODE_VERIFIED / OBSERVING`，不把未运行入口误写为闭环。

## 2026-09-26 完整后窗复核

`scheduler.log` 修改时间仍停在 09-24 06:40，本窗没有新增测试 marker，历史基线 53 未增加；固定窗应用、
traceback 与 PgBouncer 也没有自然连接错误。API import 路径继续保持 **RUNTIME_ACCEPTED**。通用 runner 本机
仍无受管实例，因此整体状态仍为 **RUNTIME_ACCEPTED (API) / RUNNER_CODE_VERIFIED / OBSERVING**。
