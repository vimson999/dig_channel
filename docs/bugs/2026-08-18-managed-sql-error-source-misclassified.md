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

### 2026-09-04 提交前回放补强

只读审查进一步复现：同秒独立故障被合并、测试/临时诊断混入、无毫秒的 ColoredFormatter 头被吞入前一块，
甚至造成 `float('5000\\n❌')` 崩溃与跨窗口错归因。现保留完整时间，识别普通/Celery/彩色/UI START 头，
结构化字段仅从本事件头提取。仅合并相邻且正文一致的彩色/普通 handler 副本；跨文件按事件正文和发生序号
去重，同一来源的重复发生保留。明确 test/SQLite 记录排除，application/adhoc 分列并保留来源路径，不打印参数。
29 项日志回归通过。固定历史窗再次对账仍为 hold=1、server_closed=1、traceback=1，traceback 同时关联
scheduler.log 与 celery_watchdog.log。PgBouncer `pgbouncer_events` 仍是旧的秒桶统计，不冒充精确独立故障数。

### 2026-09-04 多行 traceback 漏报复发

09-03 08:54:10 watchdog PID22426 在 `due_provider_drains` 发生真实断连：连接持有
15023.91ms，其中失败SQL15016.79ms、非SQL仅7.12ms；PgBouncer同刻query_wait_timeout。
异常藏在时间戳头之后的 traceback，旧分析器跳过所有无时间戳行，错误地给出server_closed=0、
结构化SQL错误=0。后者只覆盖publication_tasks专用logger，本就不能代表全库。

现以完整时间戳日志块解析，链式DBAPI/SQLAlchemy异常与跨文件复制按episode去重；新增独立
`sql_error_traceback_episodes`证据流及SQL指纹，禁止与结构化错误不经关联直接相加。
connection-held只认日志头的lifecycle logger，避免traceback栈中的同名文件被误当持有事件。
两项回归覆盖多行、链式异常、跨文件去重、窗口边界与非lifecycle栈帧；分析器23项通过。
真实固定窗回放server_closed=1、traceback episode=1、指纹c1e57cc097d3e051，结构化SQL错误仍为0。
完整unit1593项通过。状态OBSERVING：下一窗继续对账PgBouncer/traceback/Session三源。

本窗口两条 `publication_tasks` OperationalError（server closed）来自 `logs/scheduler.log`，但日志字段为 role unknown、process `-`、origin unknown。分析器只依赖字段，将其计入 ad-hoc diagnostic，导致应用 SQL 错误被压成 0；slow-query 与错误日志跨源去重时也没有把托管来源升级回来。

## 实际修复

分类器新增 `source_path`：app/backend/scheduler/celery 日志优先视为托管应用来源；同一 SQL 错误从其他日志获得托管证据时升级分类。生命周期样本保留来源，避免缺 role/process 时再次丢失。

## 验证与下一轮验收

回放测试在旧签名先失败，修复后分析器 20 个测试通过；重跑本窗口后应用 SQL 错误正确为 2、ad-hoc 为 0，托管连接持有中的 ad-hoc 也为 0。`slow_query.log` 单独缺失全部来源字段时仍无法反推出调用者，这是明确捕获边界；下一轮要求 SQL error、lifecycle、PgBouncer 三源保持一致并继续补齐生产 role/process/origin。

## 2026-08-19 来源文件优先级过度修正

本轮唯一 server-closed SQL 明确带 `role=unknown process=- origin=unknown`，是 stdin 诊断进程，但因共享 logger 落入 `scheduler.log`，旧规则又把它升级为 application error。回归现规定：显式 shell 进程 `-/-c/python/python3` 且 role/origin 全未知时优先归 ad-hoc；只有缺 process 元数据且来自 app/backend/scheduler/celery 的记录才由来源文件补全为托管。测试先红后绿，分析器 20 tests 通过；重跑窗口由 application error 1 / ad-hoc 0 修正为 application 0 / ad-hoc 1。状态继续 OBSERVING，因为共享文件仍无法替代生产 role/process/origin。
