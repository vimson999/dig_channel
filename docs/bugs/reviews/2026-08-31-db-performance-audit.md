# 2026-08-31 数据库性能与错误巡检

## 根因闭环结论

本轮不能写成“已解决”。固定窗口（2026-08-30 06:02:19 至 2026-08-31 06:02:19）整体明显改善，
但窗口后真实页面复现 20 秒 PgBouncer `query timeout` 和用户可见 502，随后 PDF 清洗 commit 边界又
发生 `server closed`。精确执行计划只有 0.079–741.058ms，Tailscale 现场 20 包丢失 30%；因此全站
共同根因仍是 Tailscale/物理出口/远端网络长尾，而不是 PostgreSQL executor、锁或缺索引，状态保持
`OPEN / REPEATED`。

窗口后最终定位并落地八个可由应用根治的放大器：`data/table` 全范围读取后 Python 分页、清洗 DB retry
删除 durable workspace 后整批重做、MP3 素材生成跨浏览器 I/O 持有 Session/宽写/提交后回读、
`continue/retry/regenerate` 两次 commit 后重复整行回读、PDF merge 完整 ORM 列表与终态轮询、MP3 B站
提交父任务宽读与 broker 后二次事务、报告音频 B站提交父任务宽读并跨文件探测持有 Session，以及平台
retry/cancel/后台 revoke 的宽读、提交后回读与跨 control I/O 事务。另修复
本机 bridge 413 因未消费请求体退化为 ECONNRESET 的独立 HTTP 缺陷。

数据表已获真实成功 API 验收，清洗已获 worker/真实终态及一对一 child 对账，MP3 素材已获两次自然
renderer/CAS 验收，PDF merge 已获真实 API/终态浏览器验收；continue 与两类 B站提交均已通过定向/完整
测试和 fail-closed 安全重启加载，但尚缺各自自然成功入口或完整后窗。最终 backend PID `99802` healthy，
bootstrap 17/17、`errors=[]`，六个本项相关 worker 均 running/ready、queue 0。应用修复均为
`RUNTIME_REPLACED / OBSERVING` 或更强的短窗验收，仍不是 CLOSED。没有生产 DDL、回填、timeout 掩盖、
伪造数据、删除审计 JSON 或 FORCE 重启。

## 固定窗口与上一轮量化对比

| 指标 | 2026-08-30 基线 | 本轮固定窗 | 变化 | 判定 |
| --- | ---: | ---: | ---: | --- |
| 慢 SQL 数量 | 166 | 110 | -33.7% | 改善 |
| 慢 SQL 累计耗时 | 466.080s | 262.811s | -43.6% | 改善 |
| 慢 SQL P50 / P95 / 最大 | 1.602 / 10.486 / 19.576s | 1.582 / 5.501 / 16.284s | -1.2% / -47.5% / -16.8% | 改善 |
| HTTP 请求 / 失败 | 647 / 2 | 94 / 1 | 流量少 85.5% | 覆盖下降，不能作业务改善结论 |
| HTTP 累计 / P95 / 最大 | 504.712 / 3.500 / 28.182s | 43.869 / 1.415 / 22.668s | -91.3% / -59.6% / -19.6% | 在已捕获流量内改善 |
| 长 Session 数 | 26 | 7 | -73.1% | 改善 |
| 长 Session P95 / 最大 | 19.838 / 22.590s | 23.609 / 23.609s | +19.0% / +4.5% | 恶化、低频长尾仍在 |
| Session SQL / 非 SQL 持有 | 272.313 / 12.548s | 64.343 / 8.990s | -76.4% / -28.4% | 改善，主要时间仍在 SQL 往返 |
| PgBouncer connect/query timeout | 5 / 1 | 0 / 0 | 固定窗归零 | 改善但窗口后真实 query timeout 复发 |
| 新 server connection | 36.333/h | 12.000/h | -67.0% | 1800 秒 lifetime 降 churn 持续有效 |

固定窗中 statement timeout、lock timeout、deadlock、server closed、idle in transaction 和 SQL error
均为 0；这只说明该固定窗未捕获，不代表后续运行态未发生。

## 主要问题排序与调用链证据

排序综合累计耗时、P95/最大、失败率、用户/任务影响和复发轮次，而非只看次数。

| 顺位 | 问题 / 指纹 | 累计、P95、最大或失败 | 页面/任务影响 | 根因与复发 |
| --- | --- | --- | --- | --- |
| 1 | `DB-SESSION-LIFECYCLE` / Tailscale 网络尾部 | 固定窗慢 SQL 262.811s；运行态 `topics/local` 1/1 次 502、21.089s | 知识星球页面直接失败；MP3 首屏 18.386s | 至少 8/8、8/14、8/16、8/29、8/30、8/31 六轮反复；现场 30% 丢包 |
| 2 | ZSXQ scheduler config `60ff7467696400d3` | 24 次、42.516s、P95 3.865s、max 4.350s | all-tabs scheduler 周期读取 | 18 行小表/既有唯一索引，跨时段往返尾部；REPEATED |
| 3 | report-video stage stats `3e7b.../7d788...` | 25 次、57.766s；最高 16.284s | watchdog 后台状态聚合；一次 Session 23.609s | 三条不同 group query 同 Session，非 SQL 4.707s；同形前后 28–205ms，跨查询网络 spike |
| 4 | 平台凭据 `6462f646a19da33f` | 8 次、27.684s、P95/max 5.501s | B站/调度器凭据解析 | 低频远端往返，未见大字段或外部 I/O 交叉持有 |
| 5 | publisher/outbox/recovery 后台查询 | cleanup 17.643s、outbox 14.506s、recovery 9.618s | watchdog 修复与通知 drain | 01:39 不同表连续慢后马上恢复；同窗跨表 network spike，不是单表索引 |
| 6 | Insights 冷聚合 | 固定窗 22.668s；本轮五冷 key P95/max 9.475s | 生财洞察首屏/切主题 | Session 边界有效；executor 36.513ms，冷 miss 网络尾部仍 REPEATED |
| 7 | durable notification outbox | 37/37 task 成功，DB 23/23 sent | 后台邮件通知 | 本窗改善且未复发；仍缺 provider 故障恢复和邮箱回执，OBSERVING |

### 真实 query timeout 证据链

1. request `8c73fe09`：`POST /api/v1/zsxq/topics/local`，用户点查 927.08ms、主题预查 28.82ms；
   第三条 SQL 20,013.12ms，连接总持有 20,985.12ms，其中非 SQL 仅 16.10ms。
2. API 以 502/21,088.98ms 返回；PgBouncer 06:11:35 同秒关闭 server 与 client，原因明确为
   `query timeout`，不是应用猜测。
3. 同接口随后 request `bd3d49cf` 200/1,636.88ms，后续筛选加载本地 205 条并渲染 20 条完整主题。
4. 真实 SQL 执行计划：日期 count distinct 走
   `ix_zsxq_topics_group_create_synced_desc`，741.058ms；分页同索引 0.079ms。没有锁等待、
   idle-in-transaction 或稳定慢计划。
5. PgBouncer：transaction pool，`query_timeout=20`、`query_wait_timeout=15`、
   `server_lifetime=1800`、default/min pool 15/6；`cl_waiting=0`，6 条 server 为 used。
6. 目标 `100.92.99.39` 走 `utun5`，Tailscale 为 direct endpoint `121.4.126.31:41641`；
   20 包丢失 30%，RTT min/avg/max/stddev 27.098/67.172/184.053/59.952ms，5432 TCP 可连。

结论：PgBouncer 的 20 秒限制解释了失败阈值，但不是根因；共同根因是直连路径的当前丢包/抖动。
提高 timeout 或重试只会延迟失败/放大负载，不予实施。

## 页面、API 与后台任务全链路

固定窗日志只捕获 94 个完整请求、43 个端点，93 成功、1 个 `/` 404；没有业务 API 失败。累计耗时
43.869s、P50/P95/max 2.87ms/1.415s/22.668s。主要端点：

| 端点 | 请求/失败 | 累计 | P50 / P95 / 最大 | SQL 关联 |
| --- | ---: | ---: | ---: | --- |
| `GET /api/v1/zsxq-insights/overview` | 2/0 | 22.669s | 0.81ms / 22.668s / 22.668s | 3 条慢 SQL 共 19.597s |
| `GET /api/mp3-video-workflows/audio-assets` | 2/0 | 3.012s | 2.73ms / 3.009s / 3.009s | canonical SQL 1.908s |
| `GET /api/v1/zsxq/system-sync-scheduler/status` | 3/0 | 2.548s | 0.335 / 1.970 / 1.970s | 无匹配慢 SQL |
| `POST /api/v1/zsxq/topics/local` | 4/0 | 2.385s | 0.417 / 1.415 / 1.415s | 无匹配慢 SQL |
| `GET /api/v1/zsxq/groups/local` | 2/0 | 1.765s | 0.322 / 1.443 / 1.443s | 无匹配慢 SQL |
| `POST /api/v1/zsxq/sync/runs` | 2/0 | 1.475s | 0.498 / 0.977 / 0.977s | 无慢 SQL |
| `POST /api/v1/zsxq/topics/local/tabs` | 2/0 | 0.528s | 0.240 / 0.288 / 0.288s | 无慢 SQL |

巡检真实验收补充：

- Insights 五冷 key 全部 200，端到端 2.293–9.475s；最慢 `c51d2624` selected SQL 6.761s/2085
  行、detail 1.649s/48 行，hold 非 SQL 43.76ms；Session 不跨 CPU/缓存阶段。
- ZSXQ Lab 的 groups/local 226.62ms、tabs 210.80ms、sync/runs 541.58ms、topics/local 305.87ms；
  页面最终显示 205 条本地主题并渲染 20 条。中间一次 `8c73fe09` 发生上述 502，随后恢复。
- MP3 首次目录 `2ceea551` 200/18.386s、SQL 17.433s/280 行；同形 executor 0.626ms。
  再载 `add7d05f` 命中 279.622s 快照，7.93ms、0 目录 SQL；jobs 201.01ms、runtime-status
  230.22ms、workflow-stats 286.54ms，Worker 正常、队列 0/0。
- 本段页面证据采集时 backend PID 为 65497；窗口后代码修复已在安全门通过后执行标准整栈重启，
  最终运行态见文末 09:20 验收节。全程未使用 FORCE。

后台任务固定窗只有 workflow_notify 有完整 Celery 终态：37/37 成功、0 retry/0 fail，累计 87.150s，
P50/P95/max 2.639/2.857/3.173s。数据库结束对账为 23/23 delivery sent、attempt_count=1、
0 重复 dedupe key、0 pending/leased/dead；provider closed、连续失败 0。没有新的自然 MP3 pipeline
任务或 ZSXQ publish 任务，两个对应行为验收继续等待自然样本。

## 三类强制异常清单

- 低频高延迟：Insights overview 2 次中一次 22.668s；巡检五冷 key 最大 9.475s；MP3 冷目录
  18.386s；`topics/local` 一次 21.089s/502。
- 页面/API 很慢但无匹配慢 SQL：固定窗 scheduler status 1.970s、topics/local 1.415s、groups/local
  1.443s、system-sync-test/batches 1.153s。它们可能落在鉴权、连接获取、结果 fetch/回滚或非 SQL
  环节；未匹配不能解释成没有 DB 问题。
- 后台任务/连接长但无前台端点：watchdog report-video stats hold 23.609s；report-audio recovery
  hold 7.289s；它们没有 request id，已用 process、origin、时间和相邻 SQL 关联。

## 表规模、顺扫、索引与写放大

| 表 | 总大小 | live/dead | seq / idx scan | 索引使用率 | 观察 |
| --- | ---: | ---: | ---: | ---: | --- |
| `zsxq_topics` | 129MB | 12,637 / 1,584 | 3,878 / 889,989 | 99.57% | update 174,041，HOT 0.71%；宽行写放大仍需观察 |
| `publication_tasks` | 128MB | 12,973 / 571 | 6,929 / 3,056,672 | 99.77% | update 241,169，HOT 1.28%；结构化投影防线必要 |
| `zsxq_topic_comments` | 118MB | 41,333 / 5,644 | 345 / 357,069 | 99.90% | update 237,197，HOT 70.46% |
| `zsxq_attachments` | 78MB | 33,906 / 37 | 601 / 1,135,160 | 99.95% | 未出现本窗慢表指纹 |
| `zsxq_sync_runs` | 37MB | 1,062 / 46 | 242 / 337,321 | 99.93% | 代表列表 seq scan 1.768ms，小表合理 |
| `external_documents` | 7.0MB | 1,817 / 146 | 1,033 / 84,966 | 98.80% | 未出现本窗慢表指纹 |

补充 `content_pipeline_assets` 为 726 live/0 dead、3,792 seq/2,220 idx；目录 SQL 对 726 行顺扫并排序，
execution 0.626ms。此规模下新增 `(media_type,status,updated_at,created_at)` 索引不会解决 17.433 秒
应用等待。`zsxq_topics` 日期 count 已使用目标组合索引；49 个 index block read 对应 741ms 现场 I/O，
分页仅 0.079ms。

没有 exact duplicate index。低使用的 `ix_zsxq_topics_group_type_create_desc` 仅 1 scan/3.7MB，
但统计周期/重叠语义不足以安全 DROP；禁止无人值守执行。无 CREATE/DROP INDEX、ALTER、回填或锁表
动作，也没有生成 DBeaver SQL，因为当前证据明确支持“不做 DDL”。

## 已落地修复、测试与运行态状态

固定窗口统计阶段尚无生产代码变更；窗口后的四项放大器修复见文末。最终完整 unit 为
`1,533 passed, 3 skipped, 305 subtests passed`，`compileall`、`git diff --check`、registry JSON 和
Bug strict 均通过；正式回顾日期为 2026-08-31。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `DB-SESSION-LIFECYCLE` / PgBouncer timeout | Tailscale/出口/远端路径丢包抖动 | 1800s lifetime、keepalive、短事务和诊断归因已替换；本轮不改 timeout | 真实 502 + PgBouncer 同秒 query timeout；30% packet loss | 是，OPEN/REPEATED | 基础设施责任：稳定网络路径；应用继续保留失败可观测性 | 完整 24h 六类错误=0、丢包接近0、20s hold/502=0，且下一窗再确认 |
| Insights cold miss | 网络结果传输长尾；历史 Session 跨 CPU 已修 | 显式 read scope、轻摘要、当前页有界详情、缓存 | 五冷 key 均成功；非SQL hold 43.76ms；P95 9.475s | 网络复发；代码未复发 | 冷首屏仍超过 5s | 五冷 key P95<5s 且后续窗口无十秒级 SQL/hold |
| MP3 canonical catalog | 冷 miss 遇网络尾部；旧失效候选/N+1 已修 | authoritative snapshot、TTL/single-flight、DB 后文件校验 | 冷18.386s、executor0.626ms；暖7.93ms/0SQL | 网络复发；目录算法未复发 | 首次访问慢；自然 source identity 样本仍无 | 冷P95<5s、暖0SQL；首条自然 pipeline identity 四项通过 |
| notification provider storm | 旧每 event 重试预算；已由 provider outbox 替换 | durable outbox、熔断、租约、去重 | 37/37 task；23/23 DB sent/attempt1 | 本窗未复发 | 缺真实 provider 故障恢复和邮箱回执 | 故障期单探针、恢复全 sent、无重复 event、人工收件 |
| ZSXQ publish poisoned Session | timeout 后吞异常未清 Session；代码已修 | 有界 retry + rollback/remove | 新 worker 已加载；本窗无自然发布 | 未捕获，不等于未发生 | 缺真实任务行为验收 | 自然瞬断/发布下状态、通知、Session 四项一致 |
| scheduler/report stats 指纹 | 小查询远端往返尾部 | 现有窄投影、分段 origin；不盲目加索引 | 跨表同窗 spike、前后同形毫秒级 | 是，OBSERVING | 周期后台累计耗时仍高 | 按指纹累计/P95/max 降至验收线且网络无复发 |

## 未捕获边界与下一轮计划

- 94 个 HTTP 请求远低于上一轮 647；没有日志的页面、外部客户端、浏览器后台标签和未产生日志的任务
  都不能被宣称正常。请求日志也不能直接拆分 SQL execute、fetch、rollback/checkin 的全部驱动阶段。
- PostgreSQL `pg_stat_*` 是累计计数且可能经历 reset；低 scan 不等于无价值，不能凭单日删除索引。
- Tailscale 20 包只证明巡检时段有严重丢包，不足以区分本地 Wi-Fi、运营商出口、WireGuard path、远端
  主机或远端网络哪一段；下一轮需保留时间序列和两端网络指标，不能把责任武断归给单一节点。
- 后台只有 workflow_notify 在固定窗出现完整 Celery 终态；其他 worker 无任务不能当作任务成功率 100%。
- 下一轮优先级：先复核 `8c73fe09` 同指纹和 Tailscale 丢包，再跑 Insights 五冷 key 与 MP3 冷/暖；
  按 request/task id 对齐 PgBouncer socket；检查自然 MP3 pipeline、ZSXQ publish 与 provider 故障恢复；
  最后再依据真实 SQL + pg_stat + EXPLAIN 决定是否需要人工 DDL。未满足后续窗口前均保持 OPEN/OBSERVING。

## 08:36 恢复快照

同一 Tailscale 目标的 20 包探测已恢复到 0% 丢包，但 RTT min/avg/max/stddev 仍为
26.731/78.135/177.386/50.807ms。该快照仅说明链路在 06:11 真实失败后恢复，不否定此前 30% 丢包、
PgBouncer query timeout 与用户可见 502；网络复发键仍为 `OPEN / REPEATED / OBSERVING`。

## 08:35 后续运行态新增根因与修复（覆盖本报告早先“无代码变更”结论）

固定窗报告完成后，真实页面和后台任务继续产生了两条可由应用根治的新证据，因此本节覆盖上文
“本轮没有生产代码变更”的时点性描述。两项修复均已落地；09:10 后续自动发布自然结束、安全门
通过并完成标准重启。文中保留 09:04 的中间态用于证明门禁确实 fail closed，最终状态以 09:20
验收节为准，仍不能写成已解决。

### 新增 1：`data/table` 全范围物化后分页

- request `16574fad`/`332c606f` 都只请求 page 1/per_page 10，却各返回 1,515 条 ORM 数据；SQL
  2.126/3.226 秒，端点 2.910/4.458 秒。旧代码随后才 Python slice，且日期×频道组装用线性
  `next(...)`，形成 O(n²) 扫描。
- 旧/新生产 `EXPLAIN` execution 仅 2.008/1.473ms；新查询先分页日期并只返回 490 行，证明根因是
  查询形状、结果传输、ORM 物化和 Python CPU，不是缺索引。
- 已改为 distinct-date CTE + window total + SQL limit/offset，并用 `(channel,date)` 字典 O(1) 组装；
  export 保持完整读取。只读生产 service 两次为 562.144/443.851ms，返回 10/31 日期。
- Bug：`2026-08-31-data-table-python-pagination-overfetch.md`；最终状态 `RUNTIME_REPLACED / OBSERVING`。

### 新增 2：清洗 DB retry 删除 durable workspace

- PDF 任务 `5341ebed-3ef5-4ce0-aa63-8cb1fe99ca1b` 首轮已完成 31 份文件；08:51:05 在 31 行
  canonical 更新边界遇到 `server closed`。旧 retry 固定 `reset_workspace=True`，删除 manifest/output，
  从头重做 31 份 PDF；第二轮 532.224 秒，09:00:58 才完成 31/31 并提交发布。
- 已新增严格 manifest adoption（batch、输入多重集、输出根、逐项 source-output、非空/类型/PDF 头），
  PDF/音频 retry 保留 workspace；retryable 异常不先写 failed，cleaning/failed 都不得降级 cleaned。
- 定向测试 20/20；新对账器对真实 151KB manifest 验证 `31 source/31 cleaned/0 failed` 并成功采用。
  但该任务由旧 worker 完成，真实新代码 redelivery 尚未发生，仍需自然 transient 验收。
- Bug：`2026-08-31-content-pipeline-clean-retry-workspace-reset.md`；最终状态
  `RUNTIME_REPLACED / OBSERVING`。

### 新增根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 当前运行态证据 | 是否复发 | 剩余风险 / 下一轮验收 |
| --- | --- | --- | --- | --- | --- |
| `GET data/table` 1,515-row fingerprint | SQL 未分页、Python slice 与 O(n²) 组装 | 日期 CTE/window 分页、O(1) map、导出合同 | 新 PID 冷/热真实 API 均 490 SQL 行、10/31 日期；热态 1.020s | 是；新代码短窗未复发 | 完整 24h 旧 1,515 行指纹为 0，P95/最大与失败率达标 |
| content clean retry workspace reset | commit ACK 不确定后无 manifest adoption，状态可倒退 | 严格 durable checkpoint、retry 保留 workspace、cleaned 单调 | 新 worker ready/队列0；真实 manifest 可采用；旧任务及发布31/31完成 | 是；新代码自然 retry 尚未捕获 | 下一自然 retry 直接 `resume_completed`、无第二轮 31 个 START、只投递一次 |
| `DB-SESSION-LIFECYCLE` | Tailscale/远端链路间歇中断 | 既有 lifetime/短事务 + 新 checkpoint 降低放大 | 08:36 0% 丢包后 08:51 又 `server closed` | 是，OPEN | 完整 24h 六类错误/server closed/失败 SQL/20s hold 均为 0，后续窗口再确认 |

索引结论不变：两项新增证据都不支持 CREATE/DROP INDEX 或 ALTER；没有待人工 SQL。

## 09:20 安全重启与最终运行态验收

- 发布任务 `15d73154-7e61-44b8-bc65-f0544a49e90b` 自然结束；结果为 4 轮、31/31 selected、
  uploaded、verified，0 failed/0 pending。个别 1059/日额度提示是 attempt 级瞬态，最终均被有界重试
  后回查确认，未把 task `completed` 误判为“有文件失败”。
- 09:10:24 `check-restart-safety.py` 读取 47 条任务记录、0 条活动 sync，确认 0 个不可中断任务；未用
  FORCE 执行 `restart-dev-stack.sh`。新 backend PID 58871 healthy；bootstrap 17/17 Celery service
  `ok=true`、`errors=[]`。content-pipeline starter PID 59411、worker PID 59416 于 09:13:41 ready；
  `/api/v1/celery/runtime-status?services=content_pipeline` 返回 running、queue length 0。
- 新 backend 真实 `data/table` request `a1c0d10e` 冷态 200/5.394s，分页 SQL 3.209s/490 行；热态
  request `35876fbb` 200/1.020s，分页 SQL 0.518s/490 行。响应为 10 行、total/total_days=31、pages=4、
  channels=50；旧 request `332c606f` 为 200/4.458s、SQL 3.226s/1,515 行。返回行减少 67.7%，热态
  端到端改善 77.1%，且字段/导出合同未删减。
- 重启后短窗 PgBouncer 未出现 `query_wait_timeout`、`query timeout`、`server closed` 或 connect
  failure；第一次 API 冷态仍有两条 1.111/3.209 秒慢 SQL，说明网络长尾并未闭环。该短窗不能替代
  完整 24 小时，也不能把未捕获解释成未发生。
- in-app Browser 对 loopback URL 返回 `ERR_BLOCKED_BY_CLIENT`，没有取得浏览器 DOM/视觉证据；未切换
  或干扰用户正在使用的 Chrome。因此 API 与 worker 已验收，UI 视觉仍为明确边界。

最终状态：数据表与清洗 retry 两个应用根因均为 `RUNTIME_REPLACED / OBSERVING`；网络为
`OPEN / REPEATED`。下一轮必须优先检查旧 1,515-row 指纹、自然清洗 redelivery、PgBouncer 六类错误、
20 秒 hold/502 和同一 Tailscale 路径丢包；任何一项未满足都不得 CLOSED。

## 09:37 后续真实流量发现第三个应用放大器

第二次标准重启后的真实 request `daab9c3a` 调用 MP3 `xiaojunjun_background` 素材重生成，返回 200，
但端到端为 `21.702s`。调用链为：

1. `load_task` 整行 SELECT `12.559s`，SQL 指纹 `590220bb0dd49238`；
2. Playwright/文件生成发生在同一读 Session 生命周期内；
3. ORM 写完整 `result` 和无关投影列 `1.032s`；
4. commit 后 `_job_payload` 访问过期 ORM，又以指纹 `773ba32db4924cf2` 整行 SELECT `2.608s`；
5. 连接总持有 `16.793s`，SQL `13.591s`、非 SQL `3.202s`。

只读生产量化显示该任务 `params=4,300B`、`result` 文本约 `32,401B`；链路恢复后同一行体积查询
`53.76ms`、JSON 字段分布查询 `28.74ms`。因此首条 12.6 秒仍是网络/PgBouncer 尾延迟，不是 executor
或 JSON 大小；应用根因是把该尾延迟放大成跨浏览器持有和第二次往返。

已改为读取后深拷贝 detached DTO 并立即 rollback/remove；浏览器生成后只以 ID + 原 `status` + 原
`updated_at` 做 `result/updated_at` 窄 CAS，commit 后用内存快照响应，不再重读任务。并发状态改变时
返回 409 且保留新结果，避免旧素材请求覆盖视频重生成状态。完整原始 JSON、素材历史与日志均保留。

TDD 先证明旧实现进入 renderer 时仍 `in_transaction=True`；修复后验证 renderer 无事务、仅一次窄
UPDATE、UPDATE 后无 `publication_tasks` SELECT，并发写入不被覆盖。MP3 定向 `189 passed`，完整 unit
`1,533 passed, 3 skipped, 305 subtests passed`；`compileall`、diff、registry JSON 与最终 Bug strict
通过。Bug 记录为 `2026-08-31-mp3-artifact-regeneration-session-hold.md`。

09:50 首次安全门明确阻断真实 PDF 清洗任务 `a96435a4…`（80%）；清洗自然完成后又正确阻断其发布子任务
`c186c4fa…`（5%）。没有 FORCE，也没有中断用户工作。当时状态确为 `CODE_CHANGED /
AWAITING_SAFE_RESTART`；后续任务终态、标准重启和真实 API/SQL 验收见下节。下一轮仍必须要求上述两个
旧 SELECT 指纹和跨 I/O hold 指纹在完整窗口归零，不能因测试或单次探针通过写成已解决。

## 10:05 第二次安全重启与 MP3 新代码运行态验收

- `a96435a4-e015-461f-b15e-c1bfc7976687`（20 份 PDF）与
  `693ee226-7e98-4b61-9533-fce46cf8d4c3`（8 份音频）自然完成；其自动发布子任务
  `c186c4fa-22e1-4ce1-9e2a-d42a0a0a34a4`、`08544642-48dd-4e8e-a4ad-a65edab98006` 也分别
  `completed/done/100%`。只读生产对账连同早先 31 份 PDF 来源 `5341ebed…` 证明三个来源各恰好一个
  发布 child，均 completed；没有重复 child。
- 10:01 安全门最终读取 47 条任务、0 条活动 sync 并放行；未用 FORCE 执行标准整栈重启。最终 backend
  PID `70045`，`/health` healthy；bootstrap 17/17 Celery service `ok=true`、`errors=[]`，MP3、
  ZSXQ publish 与 content-pipeline worker 均 ready。
- 对真实任务 `15dd7a8f-76ad-49fd-86ac-21b3acf913e9` 发送空标题的无副作用请求；request
  `48d60e3b` 在新 PID 上按预期返回 400/5,009.71ms。首次任务快照 SELECT 指纹
  `590220bb0dd49238` 为 3,860.18ms，B站子任务预取为 120.59ms；日志没有 UPDATE、没有浏览器/文件
  写入，也没有 commit 后指纹 `773ba32db4924cf2` 的整行 SELECT。
- 该证据证明新路由已加载，外部 I/O 前 Session 释放与提交后不回读的代码路径已替换；但首次快照仍被
  远端网络拖至 3.860 秒，基础设施根因继续 `OPEN / REPEATED`。后续自然成功 renderer + 窄 CAS 证据
  见 10:28 节；MP3 状态为 `RUNTIME_REPLACED / OBSERVING`，不是 CLOSED。

### 最终根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `DB-SESSION-LIFECYCLE` / PgBouncer timeout | Tailscale/出口/远端路径丢包抖动 | 既有短事务、lifetime/keepalive、错误归因；应用放大器分别收窄 | `8c73fe09` 502/query timeout、30% 丢包；`48d60e3b` 首读仍3.860s | 是，OPEN/REPEATED | 基础设施需稳定链路；应用不得靠加 timeout 掩盖 | 完整24h六类错误/20s hold/502为0、丢包近0，且后续窗口再确认 |
| `data/table` 1,515-row 指纹 | SQL 未分页、Python slice/O(n²) | 日期 CTE/window、SQL分页、O(1) map | 新API 490 SQL行、10/31日期；热态1.020s | 新代码短窗未复发 | UI DOM被客户端阻断；网络冷态仍5.394s | 24h旧1,515行指纹为0，端点失败0且P95/max达标 |
| clean retry workspace reset | redelivery 删除 durable workspace、状态倒退 | 严格checkpoint、单调状态、retry保留workspace；child advisory lock复用 | 三个真实来源/三个唯一completed child；新worker ready | 自然DB redelivery未捕获 | commit ACK不确定场景仍待真实样本 | 直接`resume_completed`、无重复START/child/dispatch、状态不倒退 |
| MP3 artifact `590.../773...` + 16.793s hold | 读Session跨浏览器I/O、宽写、commit后回读、无CAS | detached DTO、I/O前release、窄CAS、预取child、内存响应 | `86e70f97`/`ffa15b05` 两次自然200，仅一次UPDATE且之后无任务SELECT | 新代码短窗未复发 | 网络首读仍可能慢；完整后窗未到 | artifact 的 post-commit `773...`/跨I/O hold为0；初始`590...`每请求至多一次 |
| MP3 continue `8b097844` post-commit pair | commit后两次访问过期ORM，broker ID走ORM宽写 | commit前DTO/child快照、broker只传ID、celery/failure窄写；retry/regenerate共用 | 旧请求18.562s、3条慢SQL13.500s；192项定向通过；新PID78053/worker ready | 新代码后窗尚无自然样本 | 运行进程已替换；仍缺真实目标入口与24h后窗 | 新PID真实入口无post-commit宽SELECT、broker无Session、celery id一次窄写 |

未捕获边界保持不变：in-app Browser 对 loopback 返回 `ERR_BLOCKED_BY_CLIENT`，未取得页面 DOM；自然
清洗 DB redelivery、自然重复投递、continue/retry/regenerate 新进程上的真实成功调用和下一完整 24 小时
均尚未发生。测试通过、代码进程替换与素材成功请求都不能替代这些不同链路的运行态证据。

## 10:28 两次素材自然成功与第四个应用放大器

- 用户自然操作产生 `xiaojunjun_background` 成功 request `86e70f97`（200/3,416.01ms）和
  `ffa15b05`（200/4,150.20ms）。两者 SQL 顺序均为任务快照 SELECT（364.90/244.02ms）、B站 child
  预取（131.34/251.50ms）、单次 UPDATE（155.65/160.09ms）；UPDATE 后无任务 SELECT，随后素材下载
  均 200。旧 `daab9c3a` 为 21,702.34ms，新最大值下降 80.9%，真实 renderer/CAS 路径已运行验收。
- 同一用户流程的 `continue-full-video` request `8b097844` 暴露第四个放大器：200/18,561.90ms，初始
  `590...` 7,475.88ms；第一次 commit 后 `773...` 3,884.66ms，写 celery id 并第二次 commit 后又
  `773...` 2,139.90ms，三条慢 SQL 合计 13,500.44ms。首个 hold 8,156.80ms 中非 SQL仅298.65ms，
  根因是网络尾延迟叠加两次过期 ORM 回读，而非 executor/外部 I/O。
- 已新增共享 commit/enqueue helper，在 commit 前冻结 DTO/child，broker 只传字符串 ID；celery id 与
  enqueue failure 均窄写并立即 release。`continue-full-video`、retry、regenerate-videos 三个同构入口
  共用，成功/失败 SQL 顺序回归均通过；定向 `192 passed, 3 subtests passed`。
- 10:28 重启门读取 51 条任务、0 条 sync，但 publisher `392799b4…/upload_bilibili/10%` 与
  `c1061e9a…/synthesis/10%` 正在执行，因此 fail closed。没有 FORCE；Bug
  `2026-08-31-mp3-continue-full-video-post-commit-reload.md` 状态为
  当时为 `CODE_CHANGED / AWAITING_SAFE_RESTART`。它们自然终态后的标准重启与运行态结果见下节。

## 10:43 第三次安全重启与第四项修复加载验收

- 10:41 安全门读取 47 条近期任务、0 条活动 sync，确认无不可中断任务；标准重启脚本随后冻结 17 个
  队列消费、关闭 API 入队，并在停 worker 前执行第二次门禁，仍为安全。全过程未用 FORCE。
- 新 backend PID `78053`，`/health` 返回 `healthy`；bootstrap 17/17 Celery service、`errors=[]`。
  MP3、content-pipeline、ZSXQ publish worker 分别于 10:42:26、10:42:33、10:42:28 报告 ready。
- 最终回归为 MP3 API/workflow/DB projection `192 passed, 3 subtests passed`；完整 unit
  `1,533 passed, 3 skipped, 305 subtests passed`。compileall、`git diff --check`、recurrence JSON 与
  Bug strict 见本轮最终门禁。
- 10:42 后窗未出现 SQL error、PgBouncer timeout、长 Session 或目标入口自然请求；仅捕获
  B站情报 scheduler 读取两行 `system_configs` 的一次 2,070.26ms 慢 SQL。该小表读取延迟继续符合
  网络往返长尾，而没有 CREATE/DROP INDEX 证据。
- 因此第四项从 `AWAITING_SAFE_RESTART` 提升为 `RUNTIME_REPLACED / OBSERVING`：代码与 worker 已被
  新进程替换，但没有制造生产任务来冒充真实验收。下一次自然 continue/retry/regenerate 必须证明首次
  UPDATE 后 `773...` 宽 SELECT 为 0、broker 阶段 Session 为 0、celery id 仅一次窄写；完整 24 小时
  再确认不复发前仍不得 CLOSED。

## 11:15 真实 PDF 合并页面发现第五个应用放大器

10:53:52–10:57:45 的自然 PDF 合并工作台流量共 27 次
`GET /api/pdf-merge-workflows/jobs`，成功 27/27，累计 42.235 秒、P50 0.396 秒、P95 4.728 秒、
最大 18.024 秒。空列表 request `112cdfd9` 为 200/18.024 秒；任务表宽读指纹
`358df92aaae36a2d` 读取完整 `PublicationTask.params/result` 与全部媒体投影，耗时 17.328 秒但返回
0 行。连接租约持有 17.644 秒，其中两条 SQL 17.602 秒、非 SQL 仅 41.75 毫秒。非空 request
`c8a7d2f7`、`a3d9164e` 也分别为 4.402/2.863 秒，任务宽读 2.923/2.624 秒且仅返回 2 行。

同页 10:54:52–10:56:42 还产生 8 次 PDF 资产目录 GET，累计 5.767 秒、P50 0.452 秒、P95/最大
2.073 秒，其中 6 次集中在 8 秒内；这与构造自动加载和输入 debounce 的隐式回源形状一致。修复因此
同时将资产目录改为用户点击“刷新资产”后的显式意图请求，空关键词仍保留加载全部的能力。

生产只读 `EXPLAIN (ANALYZE, BUFFERS)` 对新等价过滤形状的 PostgreSQL execution 仅 0.201 毫秒、
planning 0.289 毫秒，3 行、48 shared hit/0 read；同次驱动记录等待 867.57 毫秒，连
`SET TRANSACTION READ ONLY` 都等待 1.669 秒。现有
`ix_publication_tasks_feature_user_created_desc(feature,user_id,created_at DESC)` 已覆盖目标条件。故底层
根因仍是 PgBouncer/网络长尾，不是 executor 或缺索引；应用又用全实体 + `paginate()` 列表/count 双
往返、响应前不显式 release，以及空/终态仍 12 秒轮询且无 single-flight 放大该长尾。

已将列表改为标量和四个必要 JSON 子树的轻投影，以 `count(id) OVER()` 让正常页只执行一条任务查询，
冻结行后立即 release/remove Session；公开 title/sources/output/logs/download 字段完整保留，原始
`params/result` 不删除、不改写。前端改为 Page Visibility + active-only + single-flight + terminal stop，
所有任务加载入口共享同一 Promise。PDF 资产目录改为显式意图加载：构造与输入变化不自动回源，用户
点击“刷新资产”后按关键词加载，空关键词仍可加载全部；资产选择和字段合同没有删除。

TDD 红灯分别证明旧后端执行两条宽任务 SELECT、旧前端空列表仍保留 timer；修复后 PDF merge API、
service、task、frontend 定向 16/16，通过真实脚本 VM 锁定空/终态 0 timer、活动态 1 timer、并发刷新
1 HTTP、终态立即停止。前端页面合同、轮询合同和共享 helper 合同均通过。11:11 安全门读取 47 条近期
任务、0 条活动 sync，确认当时无不可中断任务；本节初稿时状态为
`CODE_CHANGED / AWAITING_RUNTIME_ACCEPTANCE`，后续重启与真实验收见 11:27 节。

Bug：`2026-08-31-pdf-merge-list-wide-polling.md`。没有 CREATE/DROP INDEX、ALTER 或回填；当前证据
明确反对盲目 DDL。

### 第五项根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 当前运行态证据 | 是否复发 | 剩余风险 / 下一轮验收 |
| --- | --- | --- | --- | --- | --- |
| PDF merge `358df92aaae36a2d` + 17.644s hold | 网络长尾被全实体、page+count、终态轮询与无 single-flight 放大 | 轻投影、窗口计数、查询后 release、active-only/single-flight | 新 PID 两次真实 API 仅一条轻查询；终态浏览器 26 秒只请求一次，资产0次 | 是；新代码短窗未复发 | 下一真实活动任务验收 single-flight/终态停止；24h 旧指纹为0 |
| `DB-SESSION-LIFECYCLE` / 网络尾延迟 | Tailscale/出口/远端/PgBouncer 路径抖动 | 应用减少往返和持有；不加 timeout、不盲加索引 | executor 0.201ms 对比驱动 867.57ms；旧空列表17.328s | 是，OPEN/REPEATED | 基础设施稳定路径；24h timeout/server closed/20s hold/5xx为0并后续再确认 |

## 11:27 第四次安全重启与 PDF 合并真实页面/API 验收

- 完整 unit 最终为 `1,535 passed, 3 skipped, 305 subtests passed`；PDF merge 定向 16/16，
  compileall、三个前端合同、recurrence JSON、Bug strict 与 `git diff --check` 全部通过。
- 11:21 实时门禁和标准脚本冻结 17 个队列后的二次门禁都读取 47 条近期任务、0 条活动 sync，确认没有
  不可中断 processing。未用 FORCE 完成标准重启；新 backend PID `82858`，health 为 healthy，
  bootstrap 17/17、`errors=[]`。content-pipeline、MP3、ZSXQ publish 及其余托管 worker 均 ready。
- 新进程真实 API request `54fde6f1` 为 200/459.39ms，仅一条任务 SELECT 318.51ms/3 行；三条历史任务
  的 title、sources、output、logs、download URL 与分页合同完整。独立 Chromium 访问真实页面产生
  request `7571d127`，200/370.30ms，唯一任务 SELECT 119.96ms/3 行。26 秒观察中任务 API 仅 1 次、
  PDF 资产 API 0 次；三个任务均 completed 后没有第二个 12 秒 tick。新两次最大 459.39ms，较旧
  18,024.02ms 最大下降 97.5%。
- 两次新请求都没有旧 `358df92aaae36a2d`、常规 count 第二次往返、`params/result` 全实体警告或长
  Session。静态页面实际加载共享 visibility helper 和 `20260831-003` 控制器，并显示显式资产刷新说明。
- 这是终态页面与真实 API 的运行替换证据，不是完整闭环：没有制造新的用户任务来测试活动态周期，且
  基础网络仍 `OPEN / REPEATED`。PDF merge 应用根因为 `RUNTIME_REPLACED / OBSERVING`；下一真实活动
  任务和完整 24 小时未复发窗口之前不得 CLOSED。

## 11:58 自然 B站上传提交发现第六个应用放大器

10:42–11:22 的自然 MP3 B站上传提交共 4 次，成功 4/4，端到端累计 `25.910s`、P50
`5.766s`、P95/最大 `11.115s`。最慢 request `2aad47a5` 首条父任务宽读指纹
`590220bb0dd49238` 为 `8.915s`，包含完整 `params/result`；首段 Session hold `9.046s`，SQL
`9.001s`、非 SQL仅 `45ms`。child/projection 首次 commit 和 broker 之后，旧代码又以指纹
`ef9898665a613dd7` 窄写 celery ID `505.81ms` 并第二次 commit。四次请求 SQL 累计约 `11.81s`。

生产只读对账排除了“重复点击/重复投递”误诊：父任务 `35a5f9d7…` 与 `7bb3ec64…` 各有横、竖屏
两项，分别使用尾号 `…ca981800`/`…903aec46` 的不同账号；四个上传 intent hash 均不同且全部完成。
它们是合法的 4 个业务意图，不能用父任务级去重吞掉。两条父任务的 `params/result` 分别为
`6,848/40,190B` 与 `7,876/41,042B`，而提交只需少量路径、标题与凭据 scalar。

生产只读 `EXPLAIN (ANALYZE, BUFFERS)` 显示旧宽点查 executor `0.035ms`、3 shared hit/0 read；
新 JOIN 轻投影 executor `0.917ms`、77 shared hit/0 read。旧完整序列化样本 `47,436B`，新快照
`2,406B`，减少 `94.9%`；最终新查询 5 次驱动 P50/P95/最大仍为
`34.31/174.32/174.32ms`。这再次证明底层是 PgBouncer/网络尾延迟且仍有抖动，不是 executor、锁、
磁盘或索引缺失。

已将提交路由改为一条父任务+凭据窄 JOIN，读取后冻结 detached snapshot 并在文件探测前立即释放
Session；只有结构化标题均缺失才读取 7 个 response scalar，随后再次释放。child 在首事务预分配并
持久化 broker UUID，broker 使用同一 ID；正常成功路径不再查询/更新任务或第二次 commit，异常 ID 与
发布失败仍以窄写持久化。完整 `params/result`、标题、封面、凭据和原始审计数据均保留。

TDD 两个红灯分别锁定“旧成功路径无预分配 broker ID”和“旧 route 宽读且文件探测时 Session 未释放”；
修复后新增 2 项、相关 API/service 89 项、DB projection 81 项均通过。完整 unit 首次只因沙箱 loopback
限制出现 34 个 `PermissionError`。最终门禁又发现本机 bridge 超限表单会因未读请求体让 413 退化为
ECONNRESET；有界消费修复前稳定复现两次，修复后原症状 1/1、bridge 20/20，最终完整 unit
`Ran 1496 tests in 75.952s`、`OK`。该独立问题记录为
`2026-08-31-frontend-oversized-form-connection-reset.md`；compileall、py_compile、diff check、registry JSON
与 Bug strict 通过。

12:13 将该 bridge 从已核实的旧常驻 PID `30654` 精确 `TERM`，确认 8888 端口释放后通过项目 ensure
脚本启动 PID `88329`；未强杀、未触碰 backend/worker。新进程真实健康页为 200，真实 `512KiB + 1`
表单得到 413/`request_body_too_large`，不再连接重置。该独立 HTTP 问题为
`RUNTIME_E2E / OBSERVING`，仍需后续窗口复核，不能因一次验收写成 CLOSED。

11:55 安全门读取 47 条近期任务、0 条活动 sync；标准重启冻结后的二次门禁仍通过，全程未用 FORCE。
新 backend PID `86809` healthy，bootstrap 17/17 Celery service `ok=true`、`errors=[]`；
platform_upload/mp3_video_workflow worker 分别于 11:56:45/11:56:56 ready，watchdog 已启动。重启后尚无
自然 B站上传 POST；没有为了验收制造外部上传或重复 child。因此本项为
`RUNTIME_REPLACED / OBSERVING`，不能写成已解决。

11:56 后短窗没有 SQL error、PgBouncer timeout、server closed、5xx、目标旧指纹或上传 POST；仅
B站充电/情报 scheduler 读取极小 `system_configs` 时出现 `1,542.74/1,414.07ms` 慢 SQL。既有同形
executor 约 `0.043ms`，该新证据继续指向网络往返长尾，不能用短窗“未捕获错误”宣告基础设施恢复。

Bug：`2026-08-31-mp3-bilibili-upload-wide-parent-double-commit.md`。索引结论仍是不执行 DDL：真实 SQL、
buffer 和 executor 证据均明确反对新增索引，也没有待人工 SQL。

### 第六项根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 当前运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| B站提交 `590220bb0dd49238` + `ef9898665a613dd7` | 网络长尾被父任务完整 JSON、独立凭据查询、broker 后二次事务放大 | 父任务/凭据窄 JOIN、I/O 前 release、首事务 broker ID、成功路径零二次写 | 新 PID 86809 healthy；17/17 service ok；两个目标 worker ready；生产只读快照 47,436B→2,406B | 与大 JSON/跨 broker 生命周期簇反复；新代码尚无自然 POST | 真实成功 SQL 顺序和端到端改善未捕获；不得制造外部上传冒充验收 | 下一自然提交仅一条轻快照，文件/broker hold=0，ID 首事务一致，旧两指纹和第二 commit=0；24h 后窗再确认 |
| B站同父任务 2 child 形状 | 横/竖屏与账号是不同业务 intent，不是重复 delivery | 保留合法多意图；仅记录稳定 intent 对账，不加父级粗去重 | 四个 child 的 video/account/intent hash 均不同且 completed | 否；已排除重复投递误诊 | 后续仍需区分合法多意图与真正相同 intent redelivery | 下一窗口相同 intent 不重复、不同 intent 不丢失，child/broker/终态一一对应 |
| `DB-SESSION-LIFECYCLE` / PgBouncer 网络尾延迟 | Tailscale/出口/远端/PgBouncer 路径抖动 | 应用减少字节、往返和持有；不加 timeout、不盲加索引 | executor 0.035/0.917ms，驱动最大 174–204ms；历史最慢请求11.115s | 是，OPEN/REPEATED | 基础设施稳定路径仍是责任动作 | 完整24h timeout/server closed/20s hold/5xx为0、路径丢包近0并在后续窗口再确认 |

未捕获边界：重启后没有自然成功 B站提交，故不能量化新端点 P50/P95/max，也不能以“日志未出现”证明
旧指纹永不复发；UI DOM、外部 B站实际上传和完整 24 小时窗口均未在本节捕获。下一轮必须从 PID
`86809` 的 11:56:40 切点继续，而不是重新从零扫描。

## 12:50 全调用点复核发现第七个应用放大器

第六项 MP3 B站提交完成根因修复后，对所有同能力调用点继续复核，发现报告音频入口
`POST /api/report-audio-workflows/jobs/<id>/bilibili-upload` 仍使用 `_get_owned_task` 加载完整
`PublicationTask.params/result` 与全部媒体投影，并在同一读事务内解析路径、查 child、探测视频/封面文件，
最后另查凭据显示名。这不是新的底层数据库故障，而是同一 `DB-SESSION-LIFECYCLE` / 大 JSON 热路径根因
在另一页面复发。

TDD 先用真实 route、100KB 审计载荷和实际文件探测锁定旧行为：接口虽返回业务成功，但 11 次
`_path_exists` 回调的 `db.session().in_transaction()` 全为 `True`。生产只读样本任务
`38414dee-1ab9-43f0-966e-2c8a7ce5d4e1` 为 `params=2,409B`、`result=18,644B`，合计
`21,053B`。旧完整 ORM 驱动 `606.98ms`，planning/executor 仅 `0.159/0.041ms`、3 shared hit/0 read；
新父任务+凭据轻投影驱动 `105.85ms`，planning/executor `0.300/1.684ms`、118 shared hit/0 read。
新 detached 快照序列化 `2,517B`，字节减少约 `88.0%`，驱动样本改善约 `82.6%`。同轮
`SET TRANSACTION READ ONLY` 本身等待 `98–420ms`，child fallback 驱动 `69.42ms`，继续证明秒级风险
来自 PgBouncer/网络往返，executor、锁、磁盘和缺索引均不是根因。

已把报告音频提交改为一次父任务+凭据 LEFT JOIN 的 scalar/JSON-scalar 快照；正常路径不加载完整 JSON。
所有 standard/visual/child helper 只收集字符串路径候选，每个查询阶段后立即 rollback/remove，再做文件
探测。兼容 `params.report_audio_workflow`、result-only PDF filename、历史视频位置和原专用封面优先级；
completed publisher child 的 B站专用封面仍优先于父 generic cover，用户显式选择不变。JOIN 得到的凭据名
直接传给共享 child helper；共享 enqueue 已首事务预存 broker UUID，成功路径没有发布后二次写。
原始 JSON、标题、机构/日期、横竖屏视频、封面与凭据字段均保留。

三个新增回归分别锁定：父任务/凭据只有一条窄 JOIN 且所有文件探测事务为 0；仅 result PDF metadata 时
标题仍含“广发证券”和 `2026-08-30`；completed child 专用 B站封面优先级不回归且 fallback 后探测事务
仍为 0。报告音频 API `79 tests OK`，报告音频/MP3/DB projection/task projection 交叉定向
`189 tests OK`。完整 unit 在沙箱内仅因 34 个 loopback bind 权限错误失败；允许纯测试回环后
`Ran 1499 tests in 73.792s`、`OK`。完整测试发现的两处 PDF merge `send_file` 测试响应句柄已在测试中
显式关闭，`-W error::ResourceWarning` 6/6 通过；这两条是测试消费端告警，不是生产接口泄漏。

12:37 的只读旧形状基线探针产生指纹 `fffe03373e38e2e6`，process=`-c`、origin=`unknown`；该指纹已
标记为人工诊断证据，排除于自然复发计数。没有生产写入、DDL、回填、配置或 timeout/retry 改动。

12:47 首次安全门读取 47 条近期任务、0 条活动 sync 并放行；标准重启冻结 17 个队列、关闭 API 后的
第二次门禁仍放行，全程未用 FORCE。新 backend PID `92009`，`/health` healthy；bootstrap 17/17
Celery service `ok=true`、`errors=[]`。platform_upload、report_audio_workflow、mp3_video_workflow 分别于
12:48:50、12:49:00、12:49:04 ready，watchdog 和调度器按配置恢复。12:48 后短窗无 SQL error、
PgBouncer timeout、server closed、长 Session、目标旧指纹或报告音频 B站提交 POST。

短窗“未出现”不能写成“未发生”：没有为验收制造外部上传或重复生产 child，也没有自然报告音频 B站
提交，所以只能确认新代码和 worker 已运行替换，不能量化新端点请求量、失败率、累计、P50/P95/max，
状态为 `RUNTIME_REPLACED / OBSERVING`，不是 CLOSED。

Bug：`2026-08-31-report-audio-bilibili-upload-wide-session-hold.md`。索引结论仍为不执行 DDL：旧/新
executor 都在毫秒级且 buffer 无磁盘读，真实 SQL、表统计与 EXPLAIN 三证不支持新增或删除索引；没有
待人工 SQL。

### 第七项根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 当前运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| 报告音频 B站提交完整 ORM + `file_probe_tx=11/11` | 网络长尾被父任务完整 JSON、独立凭据查询和 Session 跨文件 I/O 放大 | 父/凭据窄 JOIN、detached 候选、每段查询后 release、保留历史字段与封面优先级 | 新 PID 92009 healthy；17/17 service ok；目标 worker ready；21,053B→2,517B | 是；与 MP3/历史 B站 Session 根因簇反复；新代码尚无自然 POST | 真实成功 SQL/Session 顺序、字段完整性和端到端分位尚未捕获 | 下一自然提交仅一条父 JOIN，所有文件/broker hold=0，无完整 JSON/独立凭据查询/第二 commit；标题、视频、封面、凭据与 child 终态完整；24h 后窗再确认 |
| 人工诊断 `fffe03373e38e2e6` | 为旧形状 EXPLAIN/驱动基线主动执行，不是自然业务流量 | process/origin/时间登记并从自然复发统计排除 | 12:37 `-c` 探针；12:48 新 PID 后短窗未出现 | 不适用 | 若只按指纹不看来源会误报复发 | 下一轮按 request id、process、origin 区分诊断与自然流量；自然旧形状必须为0 |
| `DB-SESSION-LIFECYCLE` / PgBouncer 网络尾延迟 | Tailscale/出口/远端/PgBouncer 路径抖动 | 应用减少字节、往返和持有；不加 timeout、不盲加索引 | old/new executor 0.041/1.684ms，驱动606.98/105.85ms；SET只读仍98–420ms | 是，OPEN/REPEATED | 基础设施稳定路径；应用继续全调用点审计 | 完整24h timeout/server closed/20s hold/5xx为0、路径丢包近0并在后续窗口再确认 |

下一轮必须从 PID `92009` 的 12:48:44 切点优先继续：同时等待 MP3 与报告音频两类自然 B站提交，按
request id、SQL origin、Session hold、broker ID 和 child 终态一一对账；任何一个入口仍出现完整父 JSON、
文件/broker 交叉持有、broker 后二次写或字段缺失，都保持 OPEN 并继续追到共享调用链，不能再次只修单页。

## 13:26 全动作复核发现第八个系统性应用放大器

创建/提交链完成窄化后，本轮没有停在单页修复，而是继续审计 B站、抖音、视频号以及所有直接
`celery_app.control.revoke` 调用。结果确认相同根因仍存在于动作后半段：B站 retry 完整 JOIN
`PublicationTask + upload projection` 并在 commit 后回读过期 ORM；B站与 browser publish cancel、六个
主工作流 cancel、三个后台 child revoke helper、重复 Publisher cleanup 和 processing orphan 收口都曾在
活动读事务或未持久化终态内调用 Celery control。control failure 还会传播到 API，无法保证用户看到的取消
结果与数据库终态一致。这是 `DB-SESSION-LIFECYCLE` / `HOT-PATH-PAYLOAD-POLLING` 的再次横向复发，
不是新的数据库 executor 问题。

TDD 红灯逐层锁定旧行为：六个主工作流 cancel 在 revoke callback 中全部得到
`in_transaction=True`；B站 retry SQL 包含 `publication_tasks.params`；B站、抖音/视频号与主工作流的
control failure 都会传播；后台 child/duplicate/orphan revoke 同样在活动事务内。修复将 cancel 固定为
“响应 DTO 与 `canceled/failed` 终态 → durable commit → release → best-effort revoke”，broker control
失败只告警并由 terminal/current-id guard 拦截旧消息。B站 retry 和 browser publish create/enqueue 在首
事务预存稳定 broker UUID，commit 前冻结响应；正常 broker 成功路径不再回读完整任务或第二次宽写。

生产只读样本进一步量化：失败 B站 child 的旧全行 JOIN 序列化 `8,252B`，新 retry/cancel 分别为
`2,637/1,435B`，减少 `68.0%/82.6%`。旧/new retry/new cancel executor 分别只有
`0.045/0.029/0.056ms`，shared hit 6/read 0；旧三次驱动为 `31.35/31.21/123.94ms`，而同轮仅执行
`SET TRANSACTION READ ONLY` 也等待 `107.78–115.65ms`。因此索引、锁和磁盘均被排除，根因仍是
PgBouncer/网络尾延迟被应用宽行、过期 ORM、跨 control I/O 持有放大。没有 CREATE/DROP INDEX、
ALTER、回填或生产配置动作，也没有待人工 DBeaver SQL。

新增行为契约位于 `test_cancel_session_lifecycle.py`、`test_background_revoke_session_lifecycle.py`，并扩展
B站、浏览器发布与 queue repair 回归。相关交叉套件 376 项通过；最终项目标准入口运行
`1517 tests in 84.757s`、`OK`。Bug strict 当前为 187/187、两个目标复发簇均已登记。完整测试不能替代
运行态证据；本节后续按 fail-closed 门禁完成标准重启，但没有制造外部上传、取消或重复 delivery，状态为
`RUNTIME_REPLACED / OBSERVING`，不是 CLOSED。

Bug：`2026-08-31-platform-publish-retry-cancel-session-lifecycle.md`。

### 第八项根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 当前运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| platform retry/cancel 宽读 + revoke transaction hold | 详情 ORM 被动作复用；commit 后回读；取消在终态提交前同步 control I/O | 窄 action projection、commit 前 DTO、首事务 broker ID、durable terminal before revoke、共享单条/批量 revoke 门禁、结构化边界事件与 304 文件 AST 契约 | 旧/new SQL 8,252B→2,637/1,435B；executor 均<0.06ms；1517 项 unit OK；新 PID 99802 healthy，六个相关服务 running、queue 0 | 是；同根因已跨 create/retry/cancel/repair 多次出现；新进程尚无自然动作 | 等待自然动作；control failure 的生产兜底尚未捕获 | 新 PID 上 revoke/publish 阶段 Session=0；终态先可见；before/after 事件可关联；无 post-broker 全行 SELECT/二次写；自然动作字段与终态完整；24h 再确认 |
| `DB-SESSION-LIFECYCLE` / PgBouncer 网络尾延迟 | Tailscale/出口/远端/PgBouncer 路径抖动 | 应用减少字节、往返和连接租约；不加 timeout、不盲加索引 | executor<0.06ms 对比旧驱动最大123.94ms；SET只读仍107–116ms | 是，OPEN/REPEATED | 基础设施继续稳定路径；应用不得用短窗未捕获宣告恢复 | 完整24h timeout/server closed/20s hold/5xx为0、路径丢包近0，后续窗口再确认 |

13:31 重启安全门读取 47 条近期任务、0 条活动 sync，明确放行；标准脚本冻结 17 队列并在停止旧 API 后
再次门禁通过，全程未 FORCE。新 backend PID `96754` 的 `/health` 为 healthy，bootstrap 17/17 service
`ok=true`、`errors=[]`。13:35 runtime-only API 再确认 platform-upload、report-audio、MP3、
report-script-material、report-top10 五个相关 worker 全部 `running=true`、`paused=false`、queue 0。

13:32 后目标日志没有 SQL error、statement/lock/connect/query timeout、server closed、5xx 或目标旧宽读；
只读活动视图为 active 1、idle 7、`idle in transaction=0`。唯一慢 SQL 是 B站情报 scheduler 读取一条
`system_configs` 的 1,248.67ms，继续归入网络长尾复发键。按数据库 `timestamp without time zone` 的本地
切点 13:31:59 复核，重启后目标任务为 0；此前使用 UTC 字符串得到的 3 个 completed MP3 任务是时区口径
歧义，已纠正，不计作新流量。

因此第八项只能提升到 `RUNTIME_REPLACED / OBSERVING`。下一次自然 B站 retry/cancel、抖音/视频号动作、
六类工作流取消或后台 duplicate/orphan cleanup 必须按 request/task/broker id 与 SQL origin 对账；完整
24 小时并在后续窗口确认前不得写成“已解决”。

## 14:00 第八项防线全量收口与第二次运行替换

在 13:31 首次运行替换后的全源码复核中，仍发现四类业务调用直接绕过共享守卫：B站 cancel、browser
publish cancel、Publisher superseded repair，以及简笔画图片配额的批量 revoke。前三类已在 durable commit
之后执行，因此未重新引入已知 Session hold；但绕过意味着成功路径没有统一 request/task/broker 关联事件，
未来改动也可能重新跨事务。简笔画批量分支还会在 Celery control 故障时传播异常，尽管数据库终态已经提交，
仍可能诱导上层把 durable `failed/canceled` 误当成操作失败。这是第八项根因防线未完全收口，不另造新根因。

新增 TDD 依次得到三个旧实现红灯：shared helper 没有 `[DB_SESSION_BOUNDARY]` INFO；不存在保留单次
control 调用与 `SIGTERM` 的批量 helper；配额批量 revoke 故障会传播。修复后单条/批量 helper 在 I/O
前后统一记录 request id、Celery request/task、business task、broker task/count、terminate、
`session_in_transaction=false`、durable commit、outcome 和 duration；批量 ID 有序去重且只发一次 control。
简笔画 broker 故障只记录失败事件，数据库任务与资产终态保持 durable。业务源码直接 `.control.revoke`
审计只剩 `runtime_guard.py` 内部两处。

新增 3 个回归均按 RED→GREEN 执行；含 B站/浏览器/后台/MP3/报告音频/queue repair/简笔画的交叉套件
`170 tests`、`OK`。完整入口首次在沙箱内只有 34 个 loopback bind `PermissionError`，同一命令解除纯测试
端口限制后为 `Ran 1517 tests in 84.757s`、`OK`。`compileall`、diff check、registry JSON、Bug strict
187/187 与直接 revoke 审计均通过。

随后把人工源码审计升级为可执行 AST 门禁 `check-celery-revoke-boundary.py`。三个测试先因门禁不存在全部
RED；最小实现能对受控 fixture 拒绝 shared guard 外的 direct revoke、拒绝缺 `business_task_id` 的 helper
调用，并要求所有调用显式 `operation`。首次 GREEN 又暴露仓库初始提交中的一个 `.py` 首行含非 UTF-8
注释；Python 编译器与 `ast.parse(bytes)` 均可解析，只有门禁预先强制 UTF-8 解码会崩溃，因此门禁改为与
解释器一致的 bytes 解析，而不是跳过文件或改动无关历史源码。最终门禁实际扫描 304 个 backend Python
文件，定向 3/3、生命周期交叉 10/10，完整 1517/1517 通过。该变更只有 dev gate 与测试，不需要再次重启
PID `99802`。

13:55 安全门读取 47 条近期任务、0 条活动 sync；标准脚本冻结 17 队列、停止旧 API 后的第二次门禁仍
通过，全程未使用 FORCE。新 backend PID `99802` healthy，bootstrap 17/17、`errors=[]`。runtime-only
接口确认 platform upload、报告音频、MP3、基础文案、Top10、report visual plan 六服务全部
`running=true`、`paused=false`、queue 0。切点 `13:56:44` 后没有自然目标动作，因此生产结构化边界事件
为未捕获，不能解释为未发生。只读活动视图 active 1、idle 7、idle-in-transaction 0；探针自身的
`SET TRANSACTION READ ONLY` 为 1,576.42ms，网络根因继续 `OPEN / REPEATED`，也继续反对盲目索引。

当前应用状态仍为 `RUNTIME_REPLACED / OBSERVING`。下一轮从 PID `99802` / 13:56:44 优先捕获自然
retry/cancel/duplicate/orphan/quota 动作，要求 before/after 成对、Session=0、终态先 durable、broker
故障不改变终态，且按 request/task/broker id 与 SQL origin 可关联；完整 24 小时和后续窗口确认前不得 CLOSED。

## 14:43 第九项：watchdog 自愈循环误加载 report-video 展示聚合

13:56:44 后窗的唯一自然慢 SQL 来自
`celery_monitor.py:_report_video_stage_stats`：`report_video_tasks GROUP BY status` 一次
`1,083.89ms`，返回 11 组。继续按 process/origin 还原后确认，这不是单次偶发 SQL：旧 watchdog PID
`609` 每轮用 `force_refresh=True` 调用通用 `_service_status()`，即使展示统计不参与 worker 卡死、告警或
重启判断，也固定执行 task status、report parse status、report AI status 三条聚合。

旧运行态 `13:56:44–14:38:18` 共 28 轮、84 条无决策价值的展示查询：task 聚合累计
`7,431.62ms`，P50/P95/max `227.28/508.42/1,083.89ms`；两个 report 聚合共 56 条、累计
`2,631.91ms`，P50/P95/max `31.32/109.13/430.97ms`；总累计 `10,063.53ms`。这是低频单条高延迟
与频繁后台轮询的交集，没有前台端点；不能因只有一条跨过 1 秒阈值而忽略其累计成本。

数据库证据反对索引：`report_video_tasks` 345 行、总大小 999,424B、seq scan 86,112；
`report_video_reports` 4,752 行、总大小 12,099,584B、seq scan 167,541。三条真实
`EXPLAIN (ANALYZE, BUFFERS)` 的 planning/execution 分别为 `0.202/0.302ms`、`0.167/4.205ms`、
`0.074/2.429ms`，全部 shared hit、0 read、0 temp；全表分组采用 seq scan 正确。现有 status/parse/AI
索引累计使用仅 `6/1/0` 也不能成为再加索引的理由。本轮没有 CREATE/DROP INDEX、ALTER、回填、生产配置
或待人工 DBeaver SQL。

根因修复不是抬 timeout 或延长缓存，而是让 `_service_status()` 增加默认开启的
`include_dashboard_stats`，watchdog 的首次检查与心跳复核显式关闭；完整 `/status` 继续按需返回原字段。
TDD 先得到缺少显式关闭参数的红灯，再转绿；新契约 3/3、watchdog 模块 40/40 通过。完整确定性入口为
`1565 passed, 5 skipped, 314 subtests passed in 82.57s`；compileall、Remotion TypeScript、109 项
DB/重启专项、Alembic 单 head、diff check、Bug strict 187/187 均通过。

14:37 双安全门各读取 47 条近期任务、0 条活动 sync，标准重启未 FORCE；新 backend PID `4274` healthy，
bootstrap 17/17、`errors=[]`，七个相关 worker 均 running、paused=false、queue 0。新 watchdog PID
`4810` 在 `14:38:21`、`14:39:44`、`14:41:09` 三轮真实周期中仍完成通知/repair/inspect，但 watchdog
日志的 `REPORT_VIDEO_*` 展示查询为 0。一次完整 status E2E `e6314206` 仍 200/`1,670.44ms` 并返回
345 个 task、4,752 个 report 的三组统计；runtime-only `29781f07` 为 200/`533.38ms`。前者属于
“API 较慢但无匹配慢 SQL”：耗时由 control-plane I/O 和多条低于慢阈值的按需查询组成，不应误报为数据库
executor 慢。

旧进程高频活动视图采样只在查询边界捕获 `28.3ms` 最大 idle 空隙，外部 I/O 阶段连续 25 秒为 0；新进程
5 秒高频采样及独立只读快照均为 `idle in transaction=0`。新切点 `14:37:55` 后任务更新 0 条、SQL error
0、statement/lock/connect/query timeout 0、server closed 0、长 Session 0；唯一慢 SQL 是 YouTube scheduler
读取极小配置的 `1,077.59ms`，继续属于网络长尾复发键。14:19 的人工坏 SQL 指纹
`70f997d06541ab2f`（process `-c`、`UndefinedColumn`）已明确从自然复发统计排除；成功探针
`822bddd900fae755` 返回目标任务 0 条。

### 第九项根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 当前运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| watchdog `report_video_* GROUP BY status` 展示轮询 | 自愈决策复用通用状态 DTO，`force_refresh` 把不参与决策的三条展示聚合带入 minute loop | `include_dashboard_stats=False` 覆盖首次与 heartbeat 复核；完整 status 默认字段不变；3 项合同 | 28 轮 84 条/10.064s → 新 PID4810 连续 3 轮 0 条；executor 0.302/4.205/2.429ms；完整 status 200 且字段完整 | 是，第 5 次 watchdog/polling 根因复发 | 短窗只有 3 轮；完整 status 本身仍 1.670s，但这是显式按需控制面 | 24h watchdog 展示 SQL=0；后续一轮仍为0；完整 status 字段/数值存在；无长 Session、5xx、timeout |
| platform retry/cancel/revoke 根因簇 | 宽 ORM、commit 后回读和 control I/O 旧边界 | 窄 DTO、稳定 broker ID、durable terminal、共享 revoke 守卫与 AST 门禁 | 全部代码已加载 PID4274；1565 项回归；切点后任务0、自然边界事件未捕获 | 是，继续 `RUNTIME_REPLACED/OBSERVING` | 无自然 retry/cancel/cleanup/quota 动作 | 下一自然动作 before/after 成对、Session=0、终态先 durable、无 post-broker 宽读；24h+后续窗 |
| PgBouncer/网络尾延迟 | 远端/Tailscale/PgBouncer RTT 与连接尾延迟 | 应用减少查询、字节、往返和持有；不抬 timeout、不盲加索引 | 展示 executor≤4.205ms，驱动/自然最大1.084s；新窗 YouTube 配置1.078s | 是，`OPEN/REPEATED` | 继续稳定网络路径并保留分层观测 | 24h 六类 PgBouncer 错误/5xx/20s hold为0，路径丢包近0，后续窗再确认 |

因此第九项只能标记 `RUNTIME_REPLACED / OBSERVING`，不是“已解决”。下一轮首先按 PID `4810` 和
切点 `14:38:18` 统计 watchdog 三个 SQL origin 是否保持 0；同时从 PID `4274` / `14:37:55` 继续等待
自然平台动作。任何重新出现的展示聚合、长 idle transaction、自然旧宽读或 control failure 终态回滚都必须
重新打开到上游调用链，不能用缓存、超时或短窗数量下降结案。
