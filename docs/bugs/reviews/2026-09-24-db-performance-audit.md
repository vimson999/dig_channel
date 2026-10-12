# 2026-09-24 每日数据库性能与错误巡检

## 根因闭环结论

固定窗口为 `2026-09-23 06:00:00` 至 `2026-09-24 06:00:00`（Asia/Shanghai）。本轮不能标记“已解决”：

- 应用慢 SQL 由 8 条降至 2 条，累计耗时由 10.887s 降至 2.245s；两条生产同形 SQL 的 PostgreSQL executor
  仅 0.048/0.117ms，现有索引命中，无锁、无长事务、无 PgBouncer 错误。数据库端没有 DDL 根据。
- Publisher 分布式旧 actor 持续复发并恶化：26 条任务全部携带已不存在的旧 checkout 路径，5 条完成任务仍写入
  已删除的 repair reason，累计换 ID 8 次；另有 3 条被当前 Watchdog 重排后因旧根产物不可访问而渲染失败。
  该 P0 保持 **OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**。
- 原始 scheduler 日志中 5 条数据库断连全部为单元测试故障注入。根因是可导入 scheduler/API 在 import 期安装
  生产文件 handler；已以 TDD 移除副作用并安全重启，状态为 **RUNTIME_REPLACED (API) / REPLAY_ACCEPTED / OBSERVING**，
  通用 runner 显式启动与完整后窗仍待验收。
- YouTube id 21/56 在第五个窗口继续固定失败，人工停用脚本仍未执行，保持
  **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。

本轮未执行生产 DDL、索引变更、数据回填、任务重投、缺失文件路径替换或凭据轮换。

## 与上一轮量化对比

| 指标 | 09-23 窗口 | 09-24 窗口 | 变化 | 判断 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL | 8 | 2 | -75.0% | 改善，但本窗请求量也下降 |
| 应用慢 SQL 累计 | 10.887s | 2.245s | -79.4% | 改善 |
| 慢 SQL P50 | 1.200s | 1.007s | -16.1% | 改善 |
| 慢 SQL P95 / 最大 | 1.885s / 1.885s | 1.238s / 1.238s | -34.3% / -34.3% | 改善 |
| PgBouncer wait P95 | 50.88ms | 36.39ms | -28.5% | 改善 |
| PgBouncer query P95 | 78.06ms | 63.64ms | -18.5% | 改善 |
| HTTP 请求 | 66 | 16 | -75.8% | 流量不同，不能直接宣告延迟根治 |
| HTTP P95 / 最大 | 2.355s / 6.313s | 4.08ms / 9.74ms | 显著降低 | 仅代表本窗少量样本 |
| Publisher 旧 reason / 换 ID | 4/12 条 / 7 次 | 5/26 条 / 8 次 | 继续复发 | P0 未闭环 |
| Publisher 旧路径资产失败 | 0 | 3 | 新增放大 | 用户工作流受影响 |
| YouTube 固定失效源 | id 21/56 | id 21/56 | 第五窗 | 反复 |

## 主要问题排序

| 排名 | 问题 | 累计/P95/最大 | 失败率与影响 | 复发 |
| --- | --- | --- | --- | --- |
| 1 | Publisher 旧运行时共享 DB/broker | 单任务增加约 3–4 分钟 repair 等待 | 3 条资产不可达失败；5 条完成任务被旧语义改写；3 个父工作流受影响 | 是，已多窗 |
| 2 | YouTube id 21/56 仍启用 | channel 19.48s；video 42.45s | channel 49/51，video errors=2 | 是，第五窗 |
| 3 | 应用侧 DB 远程尾延迟 | 2.245s / 1.238s / 1.238s | 两条低频查询，未引发业务失败 | 历史反复，本窗改善 |
| 4 | 单测污染 scheduler 生产日志 | 原始 5 个断连事件 | 自然失败 0，但扭曲故障率和根因归因 | 是，与 09-19 同类 |

## 慢 SQL、Session、PgBouncer 与 executor

按累计耗时、P95 和最大耗时排序，两个应用指纹都只出现一次：

| 指纹 / SQL origin | 应用耗时 | `EXPLAIN (ANALYZE, BUFFERS)` | 根因判断 |
| --- | ---: | --- | --- |
| `1e920b463e0710b7` / `publisher_queue_repair.py:1783` processing scan | 1.238s | `idx_publication_tasks_status`，0 rows，executor 0.048ms | 不是 executor/索引；应用、driver 或远程调度尾延迟 |
| `c1e57cc097d3e051` / `workflow_notification_outbox.py:488` due aggregation | 1.007s | `ix_workflow_notification_deliveries_lease` bitmap，0 rows，executor 0.117ms | 不是 executor/索引；同上 |

PgBouncer 1,440 个样本中八类错误全为 0；wait P50/P95/max 为 19.30/36.39/106.54ms，query 为
51.70/63.64/144.01ms。12 小时连接 churn 288，全部来自私网 `100.96.3.12`，proxy fallback 为 0。

- statement/lock/deadlock/query/connect timeout：0。
- 应用结构化 connection hold：0；idle in transaction 事件：0；waiting lock：0。
- 任务准入共观测 13 个短暂 idle episode，最多 2 连接，最大 xact age 0.445s、idle 0.192s，deferral 0。
- 原始 `server_closed=5` 皆在 06:26:55–06:27:51，关键字为 `for test`、`publish-task-1`、
  `sync-disconnect-1`、`unittest.mock`；五个对应故障注入测试可精确回放，自然应用事件为 0。

## 页面、API、后台任务与 SQL 关联

HTTP 共 16 次、7 个端点，8 成功/8 失败，累计 22.78ms，P50/P95/max 0.41/4.08/9.74ms。
8 个失败为无认证、启动或 contact 探测，不是自然页面故障。

| 端点 | 请求/成功/失败 | 累计 / 最大 | SQL/调用链判断 |
| --- | --- | --- | --- |
| `POST /contact-messages` | 9/5/4 | 19.71ms / 9.74ms | 无慢 SQL；失败为探测输入 |
| browser-import history | 1/1/0 | 1.58ms | 无慢 SQL |
| `/health` | 1/1/0 | 0.28ms | 后端健康 |
| 其他 redacted/index/login 探测 | 5/1/4 | <1ms | 无慢 SQL |

特别复核的 `zsxq-insights/overview`、`zsxq/topics/local`、`topics/local/tabs`、`sync/status`
在本窗口没有自然 HTTP 样本；这是“未捕获”，不是“没问题”。

后台任务：

- workflow notification 日志 140 次成功，累计 357.777s，P50/P95/max 2.517/3.080/3.712s；
  durable 窗口创建 139 条，139/139 sent，attempt 总数 139，无 retry/dead/重复投递。
- PDF clean 4/4 成功，累计 7.924s，P95/max 2.117s；audio clean 1/1 成功，2.564s。
- ZSXQ publish 2/2 成功，业务执行合计 2.647s；每条因并发上限各有 2 次 180s 准入重试，后续有界成功，
  无 DB 错误或无限风暴。本窗没有自然 ZSXQ sync run。
- B站情报固定窗内 23 个周期全部 6/6，138 个 source success、0 fail，累计 194.884s，
  P50/P95/max 8.458/8.919/9.085s。因调度漂移为 23 个而非 24 个。
- YouTube channel sync 为 49/51，业务耗时 19.48s；video sync 处理 49 个频道、new 29、updated 60、
  errors 2，耗时 42.45s，失败仍是 id 21/56。

三类异常单列：低频高延迟是两条 1.0–1.24s 慢 SQL 和 42.45s YouTube 后台周期；本窗已捕获的
页面/API 中没有“很慢但无慢 SQL”样本，但目标页面无样本；没有前台端点的后台长任务为 YouTube、B站、
notification 与 ZSXQ publish，它们都没有跨外部 I/O 长持 Session 的证据。

## 根因、已落地修复与反复

### P0 Publisher 旧 actor 共享生产 DB/broker

26 条 Publisher 中 21 条 `completed/done`，5 条 failed；其中 2 条是外部 B站业务错误，2 条是
`synthesis`，1 条是 `v_synthesis`。所有 26 条都含旧 checkout 绝对路径。

5 条完成任务仍写入 `pending_message_not_active_but_queue_visible`，历史共换 ID 8 次。3 条失败任务各有一次
`pending_queued_not_visible_in_celery` repair；当前 render worker 消费后看到输入原有 1 段音频，但旧根目录已不存在，
文件存在性过滤后为 0。两条横屏任务报 `max() arg is an empty sequence`，一条竖屏报
`No valid audio clips.`。三个父 MP3 任务后续均有其他 Publisher 子任务成功而最终完成，但这不能消除
3 个真实失败 delivery 和延迟。

当前生产库中无 old-root pending/processing Publisher，现场 `ps` 也只见当前项目根进程。这只是采样时快照，
不能推翻本窗的 durable path/reason 证据。已有 CAS loser 撤销、current-id guard 和当前代码“可见即等待”只能减少放大，
无法阻止持有共享凭据的旧部署写 DB。本轮不伪造或改写缺失资产，也不盲目重试。

责任动作是人工盘点所有持有生产 PostgreSQL/Redis 凭据的主机、容器和启动器，停止旧栈；若不能证明全量停止，
轮换 DB/Redis 凭据并为当前部署分配独占 broker namespace。

### P1 scheduler API import 日志污染

`scheduler/task_scheduler.py` 在模块顶层安装 `RotatingFileHandler(logs/scheduler.log)`；Web 应用只是导入
`apis.v1.scheduler` 就改变了整个测试进程的 root logger。`run_scheduler.py` 也有同型 import-time 配置与目录创建。

已落地：可导入调度库不再安装全局 handler；runner 的目录创建与生产 handler 安装移入 `_configure_logging()`，
只由 `main()` 显式调用。新解释器导入 API/runner 和显式配置三条合同 3/3 通过；重放五个原始故障注入测试
5/5 通过，`scheduler.log` 中稳定测试 marker 计数前后都是 53。详见
[Scheduler API 导入污染生产日志](../2026-09-24-scheduler-api-import-production-log-contamination.md)。

### P1 YouTube 无效源重复失败

失败身份已结构化观测，但业务根因仍是远端已无内容、本地配置仍启用。本窗 id 21/56 在 channel 和 video
两个自然周期再次失败。受保护 DBeaver 脚本仍是
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`，本轮未无人值守执行。

## 表规模、顺序扫描与索引复核

| 表 | total / heap / index / toast | live/dead | seq / idx scan | 判断 |
| --- | --- | ---: | ---: | --- |
| `publication_tasks` | 139MB / 17MB / 13MB / 109MB | 13,302 / 17 | 7,099 / 3,663,258 | autovacuum 后 dead 从 1,931 降至 17；热点计划命中索引 |
| `zsxq_topics` | 129MB total | 12,637 / 1,584 | 4,177 / 891,883 | 无本窗慢 SQL 证据 |
| `zsxq_topic_comments` | 118MB total | 41,333 / 5,644 | 476 / 358,605 | 无本窗慢 SQL 证据 |
| `zsxq_attachments` | 78MB total | 33,906 / 37 | 627 / 1,135,753 | 无本窗慢 SQL 证据 |
| `zsxq_sync_runs` | 37MB total | 1,062 / 46 | 286 / 337,717 | 代表计划 1.576ms |
| `external_documents` | 7,016kB total | 1,817 / 146 | 1,059 / 84,987 | 无本窗慢 SQL 证据 |

精确重复索引为 0。代表计划中 task PK 0.033ms、channel chart 3.204ms、system config 0.022ms、
Publisher list 0.052ms、content task list 0.089ms、recent wide/narrow 0.024/0.023ms、Insights canonical 35.234ms。
Insights 当前对 8,229 行使用 seq scan，但本窗无对应慢 SQL，已有候选索引累计 5,220 次扫描，仍不具备
“真实慢 SQL + pg_stat + 高成本计划”三项证据。低扫描索引也没有足够删除证据。因此本轮不生成、不执行索引 DDL。

## 测试与运行态验证

以下为本轮新结果，最终数字在报告完成前回填：

- scheduler logging 导入/显式配置合同：3/3。
- 五个原始断连故障注入定向回放：5/5，日志 marker 53 → 53。
- 完整 unit `Ran 1717 tests in 103.326s / OK`；首次受限运行的 34 个 error 均为 loopback bind `PermissionError`，
  同一命令在允许 loopback 的环境全绿。
- `compileall`、worker app-factory boundary 28 个文件、Celery revoke boundary 323 个文件、
  Bug strict 207/207、registry JSON 与 `git diff --check` 均通过。
- 生产只读验收：16 个 HTTP 样本、155 个 Celery 成功/重试/失败日志、23 个 B站自然周期、
  YouTube 双自然周期、26 条 Publisher durable rows、139 条 notification deliveries、PgBouncer 1,440 样本、
  表统计与代表 `EXPLAIN (ANALYZE, BUFFERS)`。

06:40 首次和冻结后二次重启安全门都读取 46 条近期 publication task、0 个活动 ZSXQ sync，确认无不可中断任务。
标准脚本未用 FORCE 重启到 backend PID `91625`；health 200，bootstrap 17/17 Celery service、Watchdog 和 6/6 scheduler
均 `ok=true`、`errors=[]`，普通 worker 及 5+6 个浏览器 pool worker 都有新 ready 记录。backend 打开文件中无
`scheduler.log`。真实只读 health、ZSXQ scheduler status 与 Insights overview 均为 200，耗时 29/387/652ms。
启动缓存刷新在固定窗之后又记录一次 Insights aggregate 应用侧 1.685s，它继续支持 DB 远程尾延迟
保持 OPEN/OBSERVING，不得倒灌进本固定窗指标。

日志修复的 API import 路径已 **RUNTIME_REPLACED / OBSERVING**；通用 runner 本机未配置托管，显式启动只有代码合同证据。
这些运行态证据不能宣告 Publisher 分布式根因关闭。

## 待人工 SQL、风险与未捕获边界

1. 本轮无待执行索引/表结构 SQL。唯一既有人工脚本是 YouTube 源停用脚本，需在 DBeaver 按其内置
   lock/statement timeout、验证与回滚说明逐条执行。
2. Publisher 需人工跨主机盘点与凭据/namespace 隔离；旧凭据未失效前，本机 `ps` 干净、worker ready 或单次任务成功
   都不是分布式唯一性证明。
3. HTTP 样本少且目标 ZSXQ 页面无样本；`pg_stat_statements` 不可用；运行时日志仍缺少旧 actor 的
   deployment/host/PID/code identity。“未捕获”不得解读为“未发生”。
4. 慢 SQL 阈值以下的 N+1、轮询或多次远程往返可能未进入慢日志；本窗没有新的超大分页、大字段宽读、
   重复去重聚合、缓存失效或跨外部 I/O 持有 Session 的新证据，但不因此把历史观察项关闭。

## 下一轮明确验收计划

1. 首先复核 Publisher 隔离动作：按 old-root path、旧 reason、ID replacement、repair-limit、缺失资产四类指纹扫描。
   只有隔离后自然 MP3→Publisher 成功且随后完整 24h 指纹为 0，才能进入 CLOSED 评估。
2. 复核 scheduler 日志修复已被新进程加载，全量测试期间 marker 不再增长，新自然错误能关联真实进程。
3. 复核 YouTube 人工配置是否执行；执行后要求 channel/video 双自然周期无 id 21/56 失败。
4. 按同一 06:00–06:00 窗口重算全站 endpoint/task/SQL/PgBouncer，为 ZSXQ 目标页面保留“未观测”，直到有真实样本。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| old-root + `pending_message_not_active_but_queue_visible` | 旧 actor 仍共享 DB/broker，本机路径检查不是物理围栏 | CAS loser 撤销、current-id guard、当前代码可见即等待 | 26 条皆 old-root，5 条旧 reason/8 次换 ID，3 条产物不可达失败 | **是** | 跨主机停旧栈，或轮换 DB/Redis 凭据与 broker namespace；OPEN | 隔离后自然任务成功，后续完整窗四类指纹均 0 |
| scheduler test marker 写入生产日志 | scheduler/API/runner import 期安装全局文件 handler | 移除库副作用，runner 显式入口配置，3 条导入合同 | marker 53→53；PID91625 healthy、17/17 ready、backend 无 scheduler.log handler | 同类复发；API 修复已运行 | API RUNTIME_REPLACED；runner 未托管；OBSERVING | 下一完整窗无测试 marker，真实错误有进程身份 |
| `1e920b...` / `c1e57c...` | executor 快，应用/driver/网络/调度尾延迟 | SQL origin、PgBouncer、Session 与 plan 观测 | 应用 1.238/1.007s，executor 0.048/0.117ms | 历史反复，本窗改善 | 下次 episode 采集同秒四源；OPEN/OBSERVING | 连续窗稳定；复发时完成 request/pool/network/executor 归因 |
| YouTube id 21/56 | 远端无内容但本地仍启用 | 失败身份观测与受保护脚本 | channel 49/51，video errors=2 | **第五窗** | 人工复核执行脚本；OPEN | 配置后双自然周期无该两源失败 |
| ZSXQ 目标页面/同步 | 本窗无自然样本 | 既有查询纪律与观测 | 未捕获 | 未知 | 不得写成未发生 | 出现自然请求/任务后关联 endpoint→SQL→Session→PgBouncer→executor |
