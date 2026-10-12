# 2026-09-21 每日数据库性能与错误巡检

## 根因闭环结论

固定窗口为 **2026-09-20 06:00:00–2026-09-21 06:00:00（Asia/Shanghai）**。本轮覆盖
`slow_query.log`、`app.log`、`backend.log`、`scheduler.log`、全部 `celery/*.log`、独立 B站/YouTube
调度器、只读生产数据库、PgBouncer、`pg_stat_user_tables/indexes`、`EXPLAIN (ANALYZE, BUFFERS)`、
持久任务终态与真实 HTTP request id。

本轮没有问题达到 CLOSED。新增并完成代码根治的是 **ZSXQ 发布状态回写把权威空数组误判为历史缺字段**：
7 条真实新任务都携带 `source_refs=[]`，旧代码仍对 clean task 读取 `params/result`；捕获 3 次慢 SQL、
累计 6.702 秒且全部 `matched=0 updated=0`。已按字段“是否存在”而非数组真假值修复两个消费路径，保留
历史缺字段兼容，不删除原始 JSON；定向联合 93/93 通过。完整测试与运行替换见下文，在真实后续任务和下一
完整窗口之前保持 OBSERVING。

数据库 executor、锁与索引不是本窗主因：应用慢 SQL 21 条/30.339 秒，代表同形 EXPLAIN 仅
0.021–35.147ms，PgBouncer 六类故障、waiting lock、应用长 hold 和 idle in transaction 均为 0。
Publisher 没有再发生 repair-limit 终态失败，但真实成功任务又写入当前源码已删除的旧可见消息换 ID 语义，
故仍为 OPEN/REPEATED；本轮补足下一次复发所需的 worker/queue/unacked 深度与截断现场。YouTube 两个失效
来源继续自然复发，人工 SQL 尚未执行，不能写成已解决。

## 与上一轮基线比较

| 指标 | 09-20 基线 | 本轮 | 变化 | 判断 |
|---|---:|---:|---:|---|
| 应用慢 SQL 数量 | 14 | 21 | +50.0% | 恶化 |
| 应用慢 SQL 累计耗时 | 17.058s | 30.339s | +77.9% | 恶化 |
| 应用慢 SQL P50 | 1.036s | 1.268s | +22.4% | 恶化 |
| 应用慢 SQL P95 | 1.732s | 1.993s | +15.1% | 恶化 |
| 应用慢 SQL最大值 | 1.993s | 3.508s | +76.0% | 恶化 |
| PgBouncer 六类故障 | 0 | 0 | 持平 | 本窗未复发，不代表历史物理断点已根治 |
| 应用长 hold / idle in transaction | 0 / 0 | 0 / 0 | 持平 | 无 Session 跨外部 I/O 证据 |
| HTTP 请求 | 26 | 167 | +542.3% | 工作负载不同，不做纯同比结论 |
| HTTP P95 / max | 390.60 / 441.20ms | 1109.51 / 6425.63ms | 恶化 | 主要为内容提交/轮询，无匹配慢 SQL |
| B站情报自然周期 | 24/24 成功 | 23/23 成功 | 持续改善 | 06 点被重启跳过；原网络断点仍未知 |
| YouTube channel/video | 各失败 2 个 | 各失败 2 个 | 反复 | 同 id=21/56，人工停用未执行 |
| Publisher repair-limit 失败 | 上一可用业务窗 5 | 0 | 暂时改善 | 仍见旧语义审计，不能关闭 |

分析器还读到 2 条 13.664/1.190 秒临时诊断 SQL，累计 14.854 秒；已单列为 adhoc，不混入应用指标。
唯一 14.107 秒 connection hold 也来自该诊断，SQL 本身占 13.911 秒、非 SQL 仅 196ms。

## 主要问题排序

排序综合累计耗时、P95/max、失败率、受影响入口和复发次数，不按出现次数单排。

| 排名 | 问题 | 累计 / P95 / max | 失败与影响 | 根因与状态 |
|---:|---|---|---|---|
| 1 | 内容管线 Web 提交与列表 | 48.048s；提交 P95/max 6.426s，列表 P95 773ms/max 1.341s | 71/71 HTTP 成功；用户提交和页面轮询受影响 | 没有匹配慢 SQL，时间在文件/命令准备、broker 与请求外阶段；阶段级 telemetry 不足，OPEN/OBSERVING |
| 2 | ZSXQ publish/PDF clean 后台任务 | 成功事件累计 3110.472s；publish P95 467.608s，PDF clean max 542.325s | publish durable 6/7 完成；1 条因两个账号均“今日限额”失败 | 主耗时为清洗/上传外部 I/O；失败为外部配额，不是 DB；空 source_refs 的额外 DB 回退已 RUNTIME_REPLACED/OBSERVING |
| 3 | 应用数据库远程尾延迟 | 30.339s；P95 1.993s/max 3.508s | 覆盖 ZSXQ、outbox、内容资产、后台 repair | executor 最大代表值 35.147ms且无锁/连接故障，仍是应用回退放大叠加远程往返；网络簇 OPEN |
| 4 | Publisher 队列修复旧语义 | 本窗 31 条：28 completed、3 failed；无 repair-limit 失败 | `22f523…` 最终成功但 durable 审计仍“可见消息换 ID” | 写入者/拓扑未归因，CAS loser 防线生效；OPEN/REPEATED，已补现场快照 |
| 5 | YouTube 失效来源 | channel 16.22s、video 45.22s；各失败 2 | 每日同步与通知持续 partial | 远端 0 results 但本地仍启用；OPEN/REPEATED/WAITING_MANUAL_CONFIG |
| 6 | B站外部连接历史故障 | 23 周期累计 215.660s；P95/max 11.119/11.409s | 138/138 competitor 成功 | 本窗改善，最初 DNS/代理/TUN 断点未知；OPEN/OBSERVING |

## SQL 指纹与调用链

| 指纹 / SQL origin | 次数 | 累计 | P95/max | 关联与判断 |
|---|---:|---:|---:|---|
| `f8b9a0ddf265ae34` `zsxq_sync_service.py:4208` | 3 | 6.702s | 3.508s | publish 完成后附件回写；空 `source_refs` 误触发 clean task 大 JSON，已修 |
| `c1e57cc097d3e051` `workflow_notification_outbox.py:488` | 4 | 5.451s | 1.993s | provider drain 后台查询；128 条 durable delivery 全 sent，无失败风暴 |
| `95b0e3fcd9a308f7` `canonical_asset_service.py:193` | 2 | 2.519s | 1.278s | 清洗产物注册；无 N+1，仍受远程往返 |
| `7ef2198e1c9a11e0` `platform_credential_service.py:857` | 1 | 1.718s | 1.718s | ZSXQ 账号解析；外部发布前数据库阶段 |
| `674016d39deab3f5` `zsxq_insights_service.py:448` | 1 | 1.672s | 1.672s | overview 聚合；同请求详情 SQL 1.211s |
| `60ff7467696400d3` `zsxq_all_tabs_auto_sync_service.py:157` | 1 | 1.570s | 1.570s | 调度配置读取；无意外 all-tabs 投递 |
| 内容任务写回两指纹 | 2 | 2.789s | 1.510s | PDF clean 状态更新；executor/锁无异常 |
| ZSXQ publish 两指纹 | 2 | 2.271s | 1.268s | 任务写回与任务开始；外部 I/O 前后短事务 |
| Publisher cleanup/pending | 2 | 2.286s | 1.239s | 后台扫描；本窗有自然候选但无 repair-limit 失败 |
| 其余 B站同步/报告收口 | 2 | 2.151s | 1.083s | 后台任务，未见失败或长事务 |

`publication_tasks` 占 10 条慢 SQL，`zsxq_topics` 2 条，其余重点表慢 SQL 为 0。没有 statement timeout、
lock timeout、deadlock、query/connect timeout。分析器发现 16 个 runtime guard 短暂观测，最大连接 3、
最大事务龄 0.992 秒、最大 idle 0.809 秒，无准入 defer；这些不是 60 秒 idle-in-transaction 事故。

## 页面/API 端到端与未匹配慢 SQL

共捕获 167 个完整请求、49 个 method/path 组，159 成功、8 失败，总耗时 58.129 秒，
P50/P95/max 205.56/1109.51/6425.63ms。8 个失败是 `/channels`、`/index.html`、`/login.html`、
`/access/<redacted>` 的探针/登录状态，不是业务 5xx 风暴。主要动态端点如下；完整 49 组还包含 health、
静态资源与 CORS OPTIONS，均逐组由分析器统计，最大 69.71ms（health）或 11.22ms（静态页面）。

| method/path | 请求 | 成功/失败 | 累计 | P50/P95/max | 慢 SQL 关联 |
|---|---:|---:|---:|---:|---|
| GET `/content-pipeline/web-submissions` | 67 | 67/0 | 26.632s | 389/773/1341ms | 0；频繁状态轮询，低频高延迟并存 |
| POST `/content-pipeline/web-submit` | 4 | 4/0 | 21.416s | 5664/6426/6426ms | 0；文件/命令/broker 阶段待细分 |
| GET `/zsxq-insights/overview` | 1 | 1/0 | 1.797s | 1797ms | 1 条详情 SQL 1.211s；聚合另有后台 SQL |
| GET `/data/summary` | 1 | 1/0 | 1.340s | 1340ms | 0；页面慢但无慢 SQL |
| GET `/rankings/all` | 2 | 2/0 | 1.302s | 517/785/785ms | 0 |
| GET `/zsxq/system-sync-scheduler/status` | 2 | 2/0 | 1.287s | 177/1110/1110ms | 0；状态端点长尾 |
| GET `/platform-credentials` | 3 | 3/0 | 807ms | 252/349/349ms | 0 |
| PUT `/platform-credentials/:id` | 1 | 1/0 | 677ms | 677ms | 0 |
| GET `/data/chart-data` | 2 | 2/0 | 648ms | 254/394/394ms | 0 |
| GET `/bilibili-intelligence/charging-dashboard` | 1 | 1/0 | 615ms | 615ms | 0 |
| GET `/data/table` | 1 | 1/0 | 596ms | 596ms | 0；旧 Python 全量分页指纹未见 |
| GET `/channels` | 3 | 1/2 | 312ms | 1/309/309ms | 0；两次为探针失败 |
| GET `/channels/categories` | 1 | 1/0 | 132ms | 132ms | 0 |
| GET `/channels/defaults` | 1 | 1/0 | 2ms | 2ms | 0 |
| GET `/platform-credentials/options` | 1 | 1/0 | 8ms | 8ms | 0 |
| POST `/zsxq/browser-import/history` | 2 | 2/0 | 8ms | 4/4/4ms | 0；测试入口 |

重点页面 `topics/local` 与 `topics/local/tabs` 本固定窗没有自然请求，不能报告“无问题”；overview 与
sync/status 有样本如上。单列三类异常：

- **低频高延迟**：4 次 web-submit P95 6.426s；overview 1.797s；data summary 1.340s。
- **页面/API 慢但无匹配慢 SQL**：web submissions、web submit、data summary、sync status，共 74 次，
  合计 50.675s。现有日志不能把其余时间精确拆成文件 I/O、序列化、broker 或连接排队，保持 OPEN。
- **后台任务/连接长但无前台端点**：ZSXQ publish 6 次成功累计 1579.559s、PDF clean 4 次累计
  1530.913s；这些是外部上传/清洗任务。应用 connection hold 为 0，不能误报为 DB Session 持有。

## 后台任务、失败风暴与 durable 对账

- Celery：160 个完成事件、1 个 retry、2 个失败 marker。ZSXQ publish 6 成功、1 admission retry，
  P95/max 467.608s；PDF clean 4/4 成功，max 542.325s；workflow notification 149/149 成功，
  P95 3.597s；Publisher 的 1 个 Celery success 是 stale message no-op，不能代替业务终态。
- 数据库：本窗新建 Publisher 31 条，28 completed、3 failed。`364b86…` 的失败是
  `No valid audio clips`，不是 DB 或 queue-repair-limit；其余失败同样不归入数据库连接故障。
- ZSXQ publish durable 7 条，6 completed、1 failed。失败 `74f949…` 的两个账号均命中“今日限额”，
  是外部配额业务失败；`e355…` 曾遇 code 13407/1059 后最终 completed，说明有界恢复生效。
- workflow notification durable outbox：128 条全部 sent、累计 attempts 132，无 failed/pending 积压，
  没有 SMTP/broker 失败风暴。
- B站 charging：09-21 00:00 同步 6/6、失败 0、3.12s。

## 反复问题与本轮修复

### ZSXQ present-empty 回退：已运行替换，待自然任务验收

修复前真实证据、实现、TDD 与验收条件见
`2026-09-21-zsxq-explicit-empty-source-refs-legacy-fallback.md`。修复后新任务的 `source_refs=[]` 不再读取
source clean task；字段缺失的历史任务仍兼容。06:27 已通过标准整栈重启加载新代码，当前状态为
RUNTIME_REPLACED/OBSERVING；尚无重启后的自然发布任务，不以 API 成功或单测替代该业务证据。

### Publisher：旧语义再现，未闭环

`22f523…` 最终 completed，但其 `result.queue_repair` 在 09:33:34 写入
`pending_message_not_active_but_queue_visible`、`previous_message_visible_in_queue=true` 并把 `2be4…` 换为
`78c3…`。09:33:36 当前 Watchdog 对旧快照发布 `54040…`，CAS UPDATE 0 后撤销；render 随后按 current-id
guard stale-skip，未造成最终失败。当前源码和 pyc 都不含旧 reason，唯一现存 Watchdog PID 23976 启于
06:22；现有日志缺 09:33:34 写入者 PID/origin，因此不能臆断单一 actor。

已新增 worker active/reserved/scheduled 数量与命中来源、五个 priority 队列深度/扫描/截断、Redis unacked
深度/扫描/截断日志；不改变“任一可见即等待、检查失败即 fail closed、CAS loser 撤销”的状态机。状态继续
OPEN/REPEATED，下一自然候选必须用完整快照定位。

### YouTube 与 B站

YouTube 新观测代码已通过自然周期，明确复发仍为 id 21/56；受保护脚本
`docs/sql/disable_unavailable_youtube_channels_20260920.sql` 默认 ROLLBACK，未无人值守执行。B站情报 23/23
自然周期全部 6/6，且独立日志未再被测试污染；原外部链路断点仍未知，保持 OBSERVING。

## 表规模、扫描、索引与膨胀

| 表 | 总大小 | live/dead | idx scan 比 | 结论 |
|---|---:|---:|---:|---|
| `publication_tasks` | 137MB（table 17 / TOAST 107 / index 13） | 13,478 / 519 | 99.80% | dead 从 2,273 改善，09-20 autovacuum 生效；无需 DDL |
| `zsxq_topics` | 129MB（13 / 79 / 36） | 12,637 / 1,584 | 99.54% | 大小主要为 TOAST；无顺扫风暴证据 |
| `zsxq_topic_comments` | 118MB | 41,333 / 5,644 | 99.87% | dead 较高但已有 vacuum/autovacuum；不凭比例盲改 |
| `zsxq_attachments` | 78MB | 33,906 / 37 | 99.95% | 健康 |
| `zsxq_sync_runs` | 37MB | 1,062 / 46 | 99.92% | 健康 |
| `external_documents` | 7MB | 1,817 / 146 | 98.77% | 低样本，无 DDL 依据 |

没有完全重复索引；低使用索引与上一轮一致，未出现能用真实慢 SQL + scan stats + EXPLAIN 同时证明应删除或
新增的候选。代表计划：publication task 点查 0.041ms、chart-data 3.170ms、all-tabs config 0.021ms、
Publisher list 0.100ms、content pipeline list 0.202ms、sync runs 1.774ms、Insights canonical 35.147ms。
因此本轮 **不生成也不执行 CREATE/DROP INDEX 或 ALTER TABLE**；没有待人工索引 SQL。

## PgBouncer、网络与错误归因

PgBouncer 1440 个统计点：wait P50/P95/max 22.00/45.48/165.96ms，query 为
49.53/72.79/539.99ms；288 次连接，12.0/h，全部来源 `100.96.3.12`，proxy TUN 回退 0。
connect/query wait timeout、server crash、TLS receive、login failure、HBA reject 均为 0。

日志原始计数中的 10 条 server-closed 与 2 个 traceback episode 都发生在 06:18–06:21 的测试故障注入，
含 `for test`、假 task id 与 `sync-disconnect-1`；06:22 正常进程启动后未见同类自然事件。结构化应用 SQL
error 为 0，另有 2 个 adhoc SyntaxError。测试污染不得混作生产断连，也不能因本窗自然错误为 0 就关闭历史
网络问题。

## 测试、运行态验证与人工动作

已完成：

- 新增 present-empty 两路径查询预算测试与 Publisher 可见性现场日志测试；RED 证据已保存于 Bug 记录；
- `test_zsxq_sync_query_discipline` + `test_publication_task_queue_repair`：93/93 通过；
- 完整 unit：1698/1698 通过；沙箱内初跑的 34 个错误全部是 loopback bind 被拒，授权环境用完全相同命令复跑
  后为 `Ran 1698 tests in 82.130s / OK`；
- `compileall`、`git diff --check`、registry JSON、Bug strict（206/206 已归类）与 Celery revoke boundary
  （321 个 backend Python 文件）全部通过；
- 生产数据库只读任务终态、表统计、锁、活动 Session 与 EXPLAIN 对账；
- B站/YouTube 自然周期、Publisher delivery、outbox durable 终态交叉核对；
- 06:26 首次及冻结消费/关闭 API 后的二次安全门均确认无不可中断任务；未用 FORCE 完成标准整栈重启。
  新 backend PID 6472，bootstrap 17/17 Celery service、Watchdog、6/6 scheduler 均 `ok=true` 且
  `errors=[]`，目标 worker 日志均出现 06:27 `ready`；health 为 200/125ms；
- 新进程真实只读 E2E：`topics/local` 200/692ms（服务端 538ms）、`topics/local/tabs` 200/307ms
  （服务端 175ms）、sync status 200/314ms（服务端 286ms）、Insights overview 200/770ms
  （服务端 741ms），响应 `success=true`，无新 ERROR/Traceback。

即使完成运行替换，ZSXQ 修复仍需真实新发布任务与后续完整窗口，Publisher 仍需自然候选现场，不能提前 CLOSED。

人工动作只有既有 YouTube 来源停用脚本：在 DBeaver 重新验证两个外部来源仍为 0 results 后逐段审核执行；
脚本带精确行保护、5 秒 `lock_timeout`、30 秒 `statement_timeout`、验证与回滚，默认 ROLLBACK。风险是把暂时
私有/短时不可见的频道停用，故继续由人确认。本轮无索引或表结构人工 SQL。

## 未捕获边界与下一轮验收计划

1. 固定窗未自然访问 `topics/local` 与 `topics/local/tabs`，未捕获不等于无问题；下一轮优先做真实冷/热请求，
   关联 request id、SQL origin、PgBouncer 与 executor。
2. web-submit 6.4s 与 web-submissions 67 次轮询都没有阶段级慢 SQL；下一轮补/读文件 hash、payload prepare、
   broker publish、serialization 分段耗时，并核对 Page Visibility、single-flight 与终态停止。
3. ZSXQ present-empty 修复：新进程加载后跑真实 web content pipeline，要求业务终态正确、source task 大 JSON
   SELECT 为 0；下一完整窗口指纹 `f8b9a0ddf265ae34` 归零或只剩可逐条解释的历史缺字段任务。
4. Publisher：下一自然 pending repair 必须同时得到 worker 三态、各 priority queue depth/scan/truncated、
   unacked depth/scan/truncated、写入进程与 delivery 时间；再现旧 reason 即继续向所有调用者与进程拓扑追踪。
5. YouTube：人工执行停用脚本后，两个自然任务分别达到 channel 49/49、video errors=0，并再观察一个周期。
6. 数据库网络：继续完整窗口四源对账；任一 server_closed/query-wait 必须同秒捕获路由、source address、
   PgBouncer pool 与应用 request/task，不因当前绿窗关闭。
7. 所有状态只在“代码落地 + 完整测试 + 新进程 + 真实业务 + 后续窗口无复发”同时满足后进入 CLOSED。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
|---|---|---|---|---|---|---|
| `f8b9a0ddf265ae34` | 空数组与缺字段混淆，误读 clean task JSON | 字段存在性判据 + 两路径 SQL 合同 | 修复前 7 条真实任务/3 慢查；新进程、健康与只读 E2E 已验收，待自然发布 | 本窗是 | 自动化：下一自然任务核对 SQL origin | 同指纹归零、终态正确、完整后窗 |
| `ORCH-PARENT-CHILD-STATE` | 可见性或运行 actor 归因缺口 | CAS/current-id 防线；新增完整 visibility snapshot | `22f523…` 最终成功，旧 reason 又写入 | 是 | 自动化：追 actor/queue；不得按成功关闭 | 自然候选完整现场且不再换可见 ID |
| YouTube id 21/56 | 远端不可用但本地启用 | 失败身份观测；受保护人工 SQL | 09-21 两自然任务仍各失败 2 | 是 | 人工：复核并执行脚本 | 49/49、errors=0，后续再一周期 |
| DB remote tail | 远程往返/驱动长尾；无 executor/锁/索引证据 | 既有短事务与指标防线 | 六类 PgBouncer 错误 0，executor ≤35.147ms | 未见物理故障，慢 SQL恶化 | 自动化：继续四源同秒观测 | 完整窗无故障且 tail 持续改善，历史断点需现场 |
| Web submit/status | 非 SQL 阶段占主导，分段观测不足 | 暂无掩盖式超时/重试 | 71/71 成功但累计 48.048s | 新增显著 | 自动化：补阶段归因后再改根因 | P95/阶段预算、轮询单飞/终态停止 |
| B站情报连接 | 历史 DNS/代理/TUN 断点未定位 | 日志隔离已运行替换 | 23/23 周期、138/138 成功 | 本窗否 | 自动化：异常时现场取证 | 后窗持续绿；新异常必须定位物理断点 |
