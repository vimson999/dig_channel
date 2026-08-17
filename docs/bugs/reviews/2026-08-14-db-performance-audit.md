# 2026-08-14 数据库性能与错误巡检

## 结论

本轮固定窗口为 **2026-08-13 06:02:19 至 2026-08-14 06:02:19（Asia/Shanghai）**。结论不是“已解决”：应用慢 SQL 数量与累计耗时分别较上一轮恶化 `35.0%`、`32.8%`，HTTP P95 恶化 `15.4%`，PgBouncer query timeout 从 `8` 增至 `14`。本轮定位并修复了两个代码级根因，但只有 finalization 单任务窄投影完成真实 API 验收；DeepSeek 跨 Session 修复虽已由新 worker 加载，仍缺新真实任务和后续完整窗口，保持 OPEN。

本轮没有执行 CREATE/DROP INDEX、ALTER TABLE、数据回填、生产配置或其他 DDL；原始 `params/result` JSON 完整保留。

## 与上一轮基线对比

| 指标 | 2026-08-13 基线 | 本轮 | 变化 | 判定 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL | 872 | 1,177 | +35.0% | 恶化 |
| 应用慢 SQL 累计耗时 | 2,180.829s | 2,895.935s | +32.8% | 恶化 |
| 应用慢 SQL P50 | 1.620s | 1.571s | -3.0% | 小幅改善 |
| 应用慢 SQL P95 | 7.477s | 6.934s | -7.3% | 改善但仍高 |
| 应用慢 SQL 最大 | 19.577s | 26.175s | +33.7% | 恶化 |
| HTTP 请求 | 2,031 | 3,085 | +51.9% | 流量增长 |
| HTTP 失败 | 30 / 1.48% | 30 / 0.97% | 失败数持平，失败率 -0.51pp | 改善 |
| HTTP 累计耗时 | 1,697.577s | 2,285.533s | +34.6% | 恶化 |
| HTTP P95 | 2.826s | 3.261s | +15.4% | 恶化 |
| HTTP 最大 | 88.103s | 28.549s | -67.6% | 改善 |
| Session 长持有 | 143 | 195 | +36.4% | 恶化 |
| Session P95 / 最大 | 20.954s / 23.006s（业务） | 21.288s / 147.667s（含诊断） | P95 恶化；业务最大 122.171s | 恶化 |
| PgBouncer query timeout | 8 | 14 | +75.0% | 反复/恶化 |
| PgBouncer connect timeout | 7 | 3 | -57.1% | 改善但未闭环 |
| server socket crash | 132 | 30（窗口）；keepalive 重载后 0 | 显著改善 | OBSERVING |
| idle in transaction / lock timeout / statement timeout / deadlock | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 持平 | 当前未捕获，不等于永久不存在 |

目标表慢查询计数：`publication_tasks 672`（上一轮 233，`+188.4%`）、`zsxq_sync_runs 4`（`-80.0%`）、`zsxq_topics 106`（`-20.3%`）、`zsxq_topic_comments 85`（`-40.1%`）、`zsxq_attachments 41`（`-28.1%`）、`external_documents 6`（`+100%`，绝对量低）。主要恶化集中在 `publication_tasks` 的父子等待、状态轮询和 report-audio 调用链。

## 主要问题排序

排序综合累计耗时、P95、最大耗时、失败率、受影响页面/任务和复发次数，不按出现次数单排。

| 排名 | 问题/指纹 | 次数与累计耗时 | P95 / 最大 | 失败/影响 | 根因与状态 |
| ---: | --- | ---: | ---: | --- | --- |
| 1 | report-audio 父任务等待子任务 `40b27e0226933f0c` | 264 次 / 541.912s | 各 origin P95 4.291–9.020s / 最大 17.691s | report-audio 状态与详情页；较上轮 41 次 / 92.641s 大幅恶化 | 频繁等待轮询叠加网络长尾，OPEN |
| 2 | DeepSeek 外部调用跨 Session | 3 个关键 hold，非 SQL 累计约 299.913s | hold 83.161–122.171s | 3 个真实 report-audio worker 任务 | 应用事务边界，已修并加载；缺真实新任务，RUNTIME_REPLACED/OPEN |
| 3 | scheduler 配置轮询 `60ff7467696400d3` | 106 次 / 222.071s | 4.725s / 9.975s | 后台 scheduler；累计 +6.0%、P95 +15.0% | 网络往返/高频轮询仍复发，OPEN |
| 4 | report-audio 完成/等待 `fd910014d5877aa5` | 89 次 / 199.861s | 多个 origin；最大约 20.5s | 2 个 SQL 失败，父任务受阻 | PgBouncer query timeout 同秒，executor 外网络等待，OPEN |
| 5 | finalization 单任务整列 JSON `af15f9b3d2324c80` | 365 次；另 7 次慢样本 | 264.20ms / 4.327s | status/detail 热路径 | 上轮只修列表、漏掉单任务 helper；本轮修复并真实 API 验收，OBSERVING |
| 6 | ZSXQ topic update `0b4ccc892361a813` | 43 次 / 123.896s | 7.848s / 10.610s | ZSXQ 同步 | 次数 -25.9%、累计 -37.5%，改善但仍复发 |
| 7 | watchdog 不可中断任务扫描 `ec2178...` | 30 次 / 72.064s | 8.298s / 8.597s | 重启门禁/后台 | 新增高延迟；executor 证据不足，OPEN |
| 8 | workflow_notify SMTP 风暴 | 133 success / 53 retry / 12 failed | 固定 2s + 30s 重试簇 | 终态失败率 12/(133+12)=8.28% | 双层固定重试放大外部断连，反复/OPEN |

评论更新 `19cc392c5b5cbe76` 为 26 次 / 39.923s，较上一轮次数 `-35.0%`、累计 `-58.3%`、P95 `-65.9%`，属于改善但仍需继续观察。

## SQL、请求与调用链关联

### 1. DeepSeek Session 长持有：已定位并修代码

真实 task `e28724cb-...`、`0a6e0be0-...`、`829bcdb2-...` 的 checkout origin 均为 `tasks/report_audio_workflow.py:1007:_process_job`。三次 hold 分别为 `122.171s / 110.381s / 83.161s`，对应 SQL 仅 `6.249s / 8.288s / 1.263s`，非 SQL 为 `115.922s / 102.093s / 81.898s`。

调用链为：

`_load_task(gemini_task_id, *TASK_CHILD_COLUMNS)` → 读取 Gemini 结果并开启事务 → `_structure_gemini_response` → `normalize_for_tts` DeepSeek 网络调用 → 后续数据库更新才归还 Session。

这不是 PostgreSQL executor 慢、锁等待或缺索引。修复在调用 DeepSeek 前显式 `release_db_session_quietly()`，只把已经物化的文本和 metadata 传出事务。真实 SQLite 事务测试先 RED、后 GREEN；完整开发栈安全重启后 report-audio worker PID `5875` 于 06:19:04 ready。尚无新真实任务走到 DeepSeek 阶段，因此不能写“已解决”。

### 2. finalization 单任务 JSON 复发：已修并完成真实 API

上轮只把列表改为 `finalization_summaries_for_audio_ids` 批量投影，`finalization_summary_for_audio` 仍调用 `latest_finalization_for_audio`，造成 365 次完整 `params/result` 读取。这是相同复发键的遗漏调用点，不是新索引问题。

本轮统一让单任务 helper 走 JSON 子字段投影。SQL 形态测试证明旧代码包含整列 JSON 并先失败；修复后测试拒绝整列回归。新 backend PID `5599` 上真实任务 `38414dee-...` 验收：

- `GET /api/report-audio-workflows/jobs?per_page=1&page=1`：HTTP 200，0.477s。
- `GET /api/report-audio-workflows/jobs/38414dee-.../status`：HTTP 200，0.677s，字段完整。
- 同秒 finalization 查询 29.14ms、0 行，没有 `multirow_payload` 或完整 `params,result` 告警。

`report_audio_parent_wakeup.py:83` 仍有 10 次 multirow payload。该 CAS/回滚路径需要精确 JSON 状态，本轮没有在缺少原子 JSONB patch 与回滚测试时机械删字段，保持 OPEN。

### 3. PgBouncer/网络与 executor 的区分

本轮应用 SQL 错误 6 个，全部为 server connection closed：父任务更新 `b7ead0...` 3 次、`_process_job` 全载荷 `85e96...` 1 次（20.236s）、完成/等待 `fd910...` 2 次（20.168–20.499s）。其中多个失败与 PgBouncer query timeout 同秒。

PgBouncer 固定窗口去重事件为 query timeout 14、connect timeout 3。2026-08-13 10:03:54 keepalive 配置重载后至窗口末约 20 小时，TLS receive timeout 和 `server conn crashed?` 均为 0，但仍有 query timeout 10、connect timeout 2。故 keepalive 修复对 socket crash 有持续改善，网络/query timeout 根因仍开放。

PostgreSQL 现场无 waiting lock、无 idle-in-transaction；代表性 EXPLAIN 执行为：publication point 0.040ms、chart 2.214ms、config 0.042ms、publisher 0.077ms、content 0.153ms、recent JSON 0.030ms、narrow 0.026ms、sync runs 1.241ms、Insights canonical 37.504ms。应用秒级/20 秒级等待远高于 executor，禁止用新增索引掩盖网络或 Session 生命周期问题。

## 页面/API 全链路

本轮 HTTP 3,085 请求，成功 3,055、失败 30，失败率 0.97%；P50/P95/最大为 `12.26ms / 3.261s / 28.549s`，累计 2,285.533s。

| 端点 | 请求 | P95 / 最大 | 累计 | SQL 关联与判定 |
| --- | ---: | ---: | ---: | --- |
| report-audio status | 344 | 6.472s / 23.260s | 538.691s | 62 条慢 SQL / 180.360s；父子轮询与网络共同放大 |
| report-audio list | 380 | 3.399s / 8.220s | 230.604s | 列表流量高，需继续压缩父任务全载荷 |
| Celery status | 19 | 17.415s / 28.549s | 200.474s | 匹配慢 SQL仅 7.797s，主要为 broker inspect/Redis；单飞后并发改善，单次仍 OPEN |
| platform credentials | 222 | 2.419s / 6.665s | 147.489s | 数据库/网络往返 |
| report-audio detail | 35 | 11.380s / 21.817s | 120.281s | 子任务和媒体投影较重 |
| MP3 list | 207 | 2.458s / 6.529s | 98.600s | 高频列表 |
| MP3 detail | 24 | 11.486s / 12.237s | 66.255s | 低频高延迟 |
| Bilibili seasons | 70 | 2.689s / 6.637s | 64.315s | 外部/数据库混合 |
| report-audio retry | 6 | 最大 27.144s | 50.057s | 3/6 失败，50% 失败率；SQL 27.916s |
| Bilibili intelligence sync | 2 | 23.196s / 23.196s | 40.582s | 低频高延迟；SQL 18.245s |
| report-audio regenerate cover | 3 | 最大 20.619s | — | SQL 13.804s |
| report-audio B站上传 | 2 | 最大 19.474s | — | SQL 15.587s |
| data summary | 4 | 最大 16.151s | — | SQL 17.219s |

三类必须单列的异常：

1. **低频高延迟**：Bilibili intelligence sync、report-audio retry/regenerate/upload、data summary。
2. **页面/API 很慢但无匹配慢 SQL**：MP3 vertical download 最大 12.428s、PDF evidence image 最大 3.175s、若干 restart 端点 10–14.5s、artifact download 最大 10.8s；优先排查磁盘/媒体、broker、外部 I/O 与网络。
3. **后台/连接持有很久但无前台端点**：DeepSeek 三个 83–122s hold；另有 ad-hoc 诊断最大 147.667s，已与托管进程分栏。

特别链路的捕获边界：本窗口分析器没有捕获 `zsxq-insights overview`、`zsxq topics/local`、`topics/local/tabs`、`sync/status` 的真实请求，不能据此表述为“没有问题”。只捕获到 `POST /api/v1/zsxq/system-sync-scheduler/run-once` 1 次、6.693s、无匹配慢 SQL。下一轮需在有真实流量时继续验收这些页面。

## 后台任务

| 任务族 | success / retry / failed | P95 / 最大 | 累计与判定 |
| --- | ---: | ---: | --- |
| report audio | 102 / 3 / 0 | 约 1,802.7s / 1,820.37s | 累计约 18,770.9s；工作流本身长，Session 必须保持短事务 |
| ZSXQ sync | 14 / 4 / 0 | 467.7s / 616.7s | 累计约 3,742.8s；外部下载/同步 |
| publisher | 15 / 0 / 0 | — | 累计约 2,065s |
| ZSXQ publish | 8 / 0 / 0 | — | 累计约 1,443s |
| workflow_notify | 133 / 53 / 12 | 固定重试簇 | SMTP 08:58–09:08 再次 `Connection unexpectedly closed` |

SMTP 较上一轮 `172 / 71 / 18` 数量下降，但 12 个终态失败与相同固定重试节奏仍存在，不能按数量下降关闭。需要共享 retry budget、指数退避加抖动、provider 级背压/短熔断、durable pending 与 event-id 幂等补发；本轮没有用增加 timeout 或吞异常止血。

运行时拓扑复发：固定窗口 10:32、10:34 两个 report-audio 父任务传播历史豆包子任务保存的 `public_browser_human_intervention_notification` ImportError，耗时 20.163s、23.111s。这不能证明新 worker 再次首次导入失败，但用户可见链路确实失败；新进程已完整替换，仍需区分“新 ImportError”与“旧 failed child 重放”。

## 表规模、扫描与索引

2026-08-14 06:06 只读现场：

| 表 | 总大小 | live / dead | updates / HOT% | 索引扫描占比 |
| --- | ---: | ---: | ---: | ---: |
| zsxq_topics | 128MB（toast 79MB，index 36MB） | 12,111 / 1,419 | 173,876 / 0.71% | 99.63% |
| publication_tasks | 123MB（toast 94MB，index 12MB） | 12,465 / 1,331 | 231,500 / 1.34% | 99.81% |
| zsxq_topic_comments | 118MB（table 91MB，index 23MB） | 41,333 / 5,644 | 237,197 / 70.46% | 99.94% |
| zsxq_attachments | 74MB（table 33MB，index 41MB） | 33,970 / 5,824 | 263,453 / 0.12% | 99.95% |
| zsxq_sync_runs | 37MB（index 35MB） | 996 / 191 | 111,605 / 0.33% | 99.94% |
| external_documents | 7.016MB | 1,817 / 146 | — | 98.82% |

未发现需要立即新增索引的 executor 证据。低扫描索引包括 attachment PDF assets group tab 4.872MB/scan 1、topics group type create desc 3.648MB/scan 1、publish status 480KB/scan 5，以及若干 external_documents 和 publisher MP3 88KB/scan 0；仅凭低扫描不能无人值守 DROP。

启动 schema 检查仍提示两个等价重叠索引 `ix_zsxq_topics_group_tab_type_create_desc` 与 `ix_zsxq_topics_group_type_create_desc`，对应 Alembic `20260724_comment_hash_indexes` 尚未登记应用。人工候选脚本仍是：

[`docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql`](../../sql/comment_hash_index_cleanup_dbeaver_20260724.sql)

必须停栈后在 DBeaver 逐条执行脚本自带的 `lock_timeout`、`statement_timeout`、验证和回滚步骤；本轮不执行。风险是索引 DDL 锁等待、写放大变化及所有者权限，需 DBA/人工窗口确认。

## 已落地变更与测试

已落地：

- `src/backend/tasks/report_audio_workflow.py`：DeepSeek 结构化前归还数据库 Session。
- `src/backend/services/report_audio_finalization.py`：单任务 finalization 摘要复用有界 JSON 子字段投影。
- 两组契约测试：真实事务内观察 release-before-I/O；捕获实际 SQL 并拒绝完整 `params/result`。
- 方案三测试补充 worker-readiness mock，使测试聚焦其视觉依赖契约；没有绕过生产门禁。
- 新 Bug 记录、既有复发记录、recurrence registry 与 README 已更新。

验证结果：

- 定向 report-audio/finalization/database lifecycle：80 tests passed。
- 完整 unit：首次 1166 项发现 1 个测试隔离错误；修正 readiness mock 后重新完整运行，`1166 tests / OK`。
- `compileall`：通过。
- `git diff --check`：通过。
- recurrence registry `jq empty`：通过。
- Bug 严格检查：162 条全部归类，9 个复发簇，OK。
- 重启安全门禁：首次沙箱网络拒绝并 fail-closed；授权只读复核后两次均确认无不可中断任务。
- 完整开发栈重启：backend PID `5599` healthy；report-audio PID `5875`、workflow_notify PID `6145` 及其余 worker 均 ready；真实 Celery status API `success=true`。
- 运行态残余：Celery status 的若干 Redis queue length 仍返回 `Timeout reading from socket`，且该端点固定窗口 P95 17.415s，继续 OPEN。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| report-audio DeepSeek hold / `tasks/report_audio_workflow.py:1007` | 查询 Gemini 后跨 DeepSeek 外部 I/O 持有 Session | DeepSeek 前显式 release + 真实事务契约测试 | 新 worker PID 5875 ready；无新 DeepSeek 任务 | 本轮新定位，属 Session 簇再次复发 | OPEN；等待真实任务 | 同 origin 非 SQL hold <5s，完整 24h 无 >5s hold |
| `af15f9b3d2324c80` single finalization payload | 上轮列表修复遗漏单任务 helper | 统一 JSON 子字段批量投影 | 真实 status 200/0.677s，查询 29.14ms，无 payload 告警 | 是 | OBSERVING；下一窗口确认 | 旧 origin/multirow 指纹完整 24h 为 0 |
| `report_audio_parent_wakeup.py:83` | CAS/回滚读取完整 JSON | 未改 | 10 次 multirow payload | 是 | OPEN；设计原子 JSONB patch 与回滚测试 | 实际 SQL 不读整列且 CAS/回滚全通过 |
| PgBouncer query/connect timeout | Tailscale/传输路径与连接等待；非 executor | keepalive tunable 已移除，仅 OS keepalive | 重载后约 20h TLS crash 0；query/connect 10/2 | 是 | OPEN；网络/连接 owner 继续 A/B | 完整 24h query/connect/crash/失败 SQL 全 0 |
| `40b27e...` report-audio wait child | 高频状态轮询叠加网络长尾 | 既有投影/单飞不足以闭环 | 264 次、541.912s | 是且恶化 | OPEN；追上游等待模型和轮询节奏 | 累计/P95 显著下降且无失败，真实页面通过 |
| `60ff...` scheduler config | 周期远端读取/网络往返 | 既有缓存仍不足 | 106 次、222.071s | 是 | OPEN；核对缓存 owner/失效 | 次数与累计按计划频率收敛，P95 <500ms |
| SMTP retry storm | 服务内和 Celery 双层固定重试同步放大 | 本轮无代码掩盖 | 133/53/12，08:58–09:08 重现 | 是 | OPEN；实现共享预算、抖动、背压、durable pending | 故障注入 20+ 并发通过，真实完整窗口 terminal fail 0 |
| stale worker ImportError 传播 | 历史失败子任务/运行时模块缓存混版 | 完整重启，新 PID | 父任务仍传播 2 次旧错误；新 worker ready | 是/边界待区分 | OPEN/OBSERVING；失败 child 恢复策略 | 新 ImportError 0，真实相关任务成功，旧失败不再反复传播 |
| ZSXQ topic/comment update | 更新往返与网络长尾 | 既有批量/指纹机制 | topic/comment 指标均改善 | 仍出现 | OBSERVING | 下一窗口继续下降且真实 all-tabs 成功 |

## 未捕获边界与下一轮计划

- 当前日志只能证明“已捕获”的请求、SQL 和任务；没有请求或没有匹配慢 SQL不能证明没有问题。
- `pg_stat_statements` 未安装，跨重启的服务端累计语句画像缺失；本轮以应用 SQL origin、PgBouncer、EXPLAIN 和表/索引统计交叉归因。
- ad-hoc 诊断与托管 web/worker/scheduler 已分栏，不能把诊断最大 147.667s误归为用户页面。
- 当前完整 24 小时窗口发生在本轮代码发布前；新代码的“不复发”必须从 2026-08-14 06:18:45 新 backend 启动后重新计时。

下一轮按以下顺序验收：

1. 优先复核 DeepSeek origin 的 Session hold，并要求至少一条真实新 report-audio 任务走完结构化阶段。
2. 复核 `af15...` 单任务指纹与 `report_audio_parent_wakeup.py:83`，分别确认 0 与继续设计原子 patch。
3. 从 06:18:45 后统计 PgBouncer query/connect/TLS crash、应用 SQL 错误和 Celery queue timeout；任何一项非 0 都不得关闭网络簇。
4. 按累计耗时复核 `40b27e...`、`60ff...`、report-audio status/detail 和 Celery status，不只看次数。
5. 复核 SMTP terminal fail、retry cadence、event id 最终送达；没有背压/补发故障注入前保持 OPEN。
6. 在真实流量下覆盖 zsxq-insights overview、topics/local、topics/local/tabs、sync/status；无流量则继续明确“未捕获”。
7. 人工维护窗口若执行 comment hash/index cleanup，必须保存执行前后 `pg_stat_user_indexes/tables`、EXPLAIN、锁等待、验证与回滚证据。
