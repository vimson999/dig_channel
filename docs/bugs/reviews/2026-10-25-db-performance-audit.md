# 2026-10-25 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 2026-10-24 06:00:00 至 2026-10-25 06:00:00（Asia/Shanghai；数据库 UTC-naive
边界为 2026-10-23 22:00:00 至 2026-10-24 22:00:00）。本轮不能写成“已解决”。应用慢 SQL 从
28 条/68.434 秒降至 14 条/19.638 秒，P95/max 从 8.878/9.314 秒降至 2.106/2.157 秒；但 HTTP 完成量同时
从 887 降到 23，且几乎全部是上一轮重启后的自检流量，自然页面、Publisher 和内容管线覆盖明显不足。

更重要的是，旧运行体再次自然写入生产：`mp3_video_workflow` task
`46fa834c-a887-4cdc-be3a-6a394f972388` 在本窗完成，壁钟 21.660 秒，输入、结果、封面、关键帧与背景路径全部
来自旧根 `/Users/v9/Documents/workspace/v9/code/dig_ytb_data_claude_v1/`；当前 MP3 worker 无该 task id，只有
当前 notification worker 消费了共享 outbox。Tailscale 显示旧 MacBook Pro 在任务完成后仍在线约 4 小时
38 分。当前会话只见本机与 `100.96.3.12` 是窗后快照，不能推翻 durable 复发证据。分布式旧运行体问题保持
**OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**。

本轮没有足够证据支持新的生产代码或索引变更，因此没有以 timeout、重试、缓存或 DDL 掩盖症状。已更新复发
登记与 Bug 证据链；旧运行体必须由人工物理停用，或轮换 PostgreSQL/Redis 凭据并隔离 broker namespace。

## 修复前后量化基线

| 指标 | 上一窗口 | 本窗口 | 变化与判断 |
| --- | ---: | ---: | --- |
| 慢 SQL | 28 / 68.434s | 14 / 19.638s | 数量 -50.0%，累计 -71.3%；样本量骤降，不能判闭环 |
| 慢 SQL P50 / P95 / max | 1.283 / 8.878 / 9.314s | 1.117 / 2.106 / 2.157s | -12.9% / -76.3% / -76.8% |
| HTTP | 887 完成，878 成功 | 23 完成，21 成功 | 流量 -97.4%；两失败均为诊断 400/404，无业务 5xx |
| HTTP P50 / P95 / max | 3.04ms / 1.362 / 49.238s | 224.15ms / 1.726 / 3.441s | 流量组成不同，不作直接性能归因 |
| Celery 日志 | 169 完成，2 失败 | 41 完成，0 失败 | 本窗仅通知任务；业务任务覆盖不足 |
| durable 旧根任务 | 0 | 1/1，21.660s | 新增自然复发，P0 |
| B站情报周期 | 24/24，168/168 | 23/23，161/161 | 重启边界少一周期；成功率保持，P95 16.122→14.260s |
| PgBouncer wait P95 / query P95 | 54.29 / 82.38ms | 42.62 / 74.14ms | 改善；八类错误仍为 0 |

## 按影响、累计耗时、尾延迟与复发次数排序

| 排名 | 问题/复发键 | 量化证据 | 影响 | 本轮状态 |
| ---: | --- | --- | --- | --- |
| 1 | 旧运行体 / `RUNTIME-TOPOLOGY-RELOAD` | 1/1 old-root，21.660s；任务后旧机仍在线约 4h38m | 旧代码可继续执行生产任务、绕过当前审计与修复 | OPEN/REPEATED/WAITING_DISTRIBUTED_ISOLATION |
| 2 | Insights / `674016d39deab3f5` | 2 次/4.101s，P95/max 2.059s；executor 40.705ms | overview 最慢请求 3.441s | OPEN/REPEATED/OBSERVING |
| 3 | notification due drain / `c1e57cc097d3e051` | 4 次/4.518s，P95/max 1.207s；上窗 3 次/3.645s | 后台通知扫描累计耗时 +24.0% | OPEN/OBSERVING |
| 4 | YouTube `id=21/56` | channel 2/51 失败，video errors=2，第 22 窗 | 每日固定失败与通知噪声 | OPEN/REPEATED/WAITING_MANUAL_CONFIG |
| 5 | audio catalog / `be891b5c1af362da` | 本窗无 audio-assets 请求、慢指纹 0；上窗 4 次/35.271s | 高影响问题缺自然样本 | OPEN/REPEATED/UNOBSERVED |

另有 report-audio finalization 2 次/3.273 秒、recovery 2 次/2.271 秒、Publisher stale repair
1 次/2.106 秒、all-tabs config 1 次/1.233 秒、notification acquire 1 次/1.106 秒和 ZSXQ detail
1 次/1.031 秒。它们均无错误、锁或长 Session 证据，本窗也没有足够重复样本支持专项代码修改。

## 页面、API、后台任务与 SQL 关联

### SQL、Session、PgBouncer 与 executor

- 14 条慢 SQL 累计 19.638 秒；`publication_tasks` 5 条、`zsxq_topics` 3 条。SQL error 仅 1 条自动诊断
  `SyntaxError`，应用 SQL error 为 0；statement/lock/connection timeout、deadlock、waiting lock、持续
  idle-in-transaction 与 connection-held 均为 0。
- PgBouncer 1,440 个样本、288 次连接（12/h），来源为 `100.96.3.12`；proxy fallback 与八类错误为 0。
  wait P50/P95/max 为 30.40/42.62/260.58ms，query 为 65.07/74.14/423.50ms。
- 生产只读 `EXPLAIN (ANALYZE, BUFFERS)`：publication PK 0.048ms、chart 3.045ms、all-tabs 0.021ms、
  publisher list 0.058ms、content list 0.251ms、recent wide/narrow 0.031/0.034ms、sync runs 1.557ms、
  Insights 40.705ms。Insights 应用 2.059 秒约为 executor 的 51 倍，继续指向 driver/PgBouncer/私网 fetch，
  不支持加索引。

### 页面与 API

固定窗只有 23 个完成请求、10 个端点，成功 21、失败 2，累计 10.222 秒，P50/P95/max 为
224.15ms/1.726s/3.441s。失败是巡检自检造成的 charging-options 缺 credential 400 与错误方法
`GET topics/local` 404，均有 request id 且不是业务 5xx。

- `zsxq-insights overview` 3/3 成功，累计 3.553 秒；最慢 3.441 秒，匹配 Insights SQL 2.059 秒。
- `GET /api/v1/celery/status` 2/2 成功，P95/max 1.726 秒，无匹配慢 SQL，属于进程/队列探测等待。
- `topics/local` POST 2/2 成功，P95 0.738 秒；charging-options 成功样本最大 0.700 秒。
- 窗后真实只读 E2E：health 200/0.041s、overview 200/3.649s、topics/local POST 200/1.005s、tabs POST
  200/0.288s、sync/runs POST 200/0.397s、system scheduler status 200/0.637s、scheduler status
  200/0.036s、Celery status 200/1.964s。Celery 状态显示 17/17 service running、paused=0、queue=0、
  topology conflict=0、broker ok。

低频高延迟为 overview 3.441 秒；“页面慢但无匹配慢 SQL”为 Celery status 1.726 秒；“后台任务很久但无
前台端点”为旧根 MP3 workflow 21.660 秒、B站情报单周期最大 14.295 秒、YouTube channel 21.940 秒和
video sync 的独立调度执行。不能因无匹配 >=1 秒 SQL 就解释成没有数据库或网络成本。

### 后台任务与外部调用

- Celery 日志只识别 41 个 completed、0 retry、0 failed，且均为 `workflow_notify`，累计 114.119 秒，
  P50/P95/max 2.898/4.128/8.879 秒；这不是 Publisher/内容管线全绿证明。
- 生产快照显示本窗唯一目标 durable 任务为旧根 MP3 workflow，已完成但 `current_step=keyframes_ready`；
  所有素材和结果均指向旧 checkout。本机 MP3 worker 日志无 task id，本机 notification worker 只消费共享
  outbox，形成“durable 写入 + 本地无执行日志 + 旧机同窗在线”的完整 actor 证据链。
- B站情报 23/23 周期、161/161 competitor 成功，累计 302.132 秒，P50/P95/max
  13.100/14.260/14.295 秒；没有慢 SQL 或 Session 长持有。历史 DNS/TUN 物理断点仍未捕获，保持观察。
- YouTube channel 49/51，耗时 21.940 秒；video processed 49、new 31、updated 54、errors=2，失败仍精确为
  `id=21 Smartpet Video AI` 和 `id=56 数字指南针`，进入第 22 个连续窗口。
- 本窗没有 B站 21523、21173 或分片重试耗尽，但没有 upload-only 自然任务；失效档位预校验与分片恢复均只能
  标为未捕获，不能把 0 次视为未复发。ZSXQ scheduler 为 disabled/targets empty，仅 1 个 json_import 完成，
  没有完整自然 ZSXQ 同步链路。

## 程序模式复核

- 本窗真实 SQL 与 EXPLAIN 未发现新的超大分页或 N+1；重点列表继续使用有界查询/投影。但自然流量过少，结论仅为
  “本窗未捕获”，不能外推全部页面。
- `publication_tasks` 大 JSON/TOAST 仍是宽行风险；代表 wide/narrow 查询 executor 均约 0.03ms，本窗慢指纹
  来自远程 fetch 而非扫描。禁止删除原始 JSON 审计字段或用假数据规避。
- notification due drain 次数 3→4、累计 3.645→4.518 秒，是小幅反复；当前 executor/锁证据不足，不以缓存、
  timeout 或索引掩盖。下一轮需继续按 outbox 数量、provider 分组与发送吞吐关联。
- 没有 Session 与外部 I/O 交叉长持有、失败重试风暴或重复投递证据；但旧运行体仍可绕过当前生命周期纪律，故
  物理隔离之前 Session/actor 防线不能判整体闭环。

## 表规模、索引与 DDL 判断

| 表 | 总大小 | live/dead | idx scan share | 结论 |
| --- | ---: | ---: | ---: | --- |
| `publication_tasks` | 152MB（table 18MB/toast 120MB/index 14MB） | 14,718 / 575 | 99.83% | 宽 JSON 风险存在，热点点查 executor 0.048ms |
| `zsxq_topics` | 133MB | 13,981 / 1,665 | 99.52% | Insights executor 40.705ms，应用尾部不在 executor |
| `zsxq_topic_comments` | 119MB | 45,884 / 6,093 | 99.85% | 无本窗慢 SQL 热点 |
| `zsxq_attachments` | 79MB | 35,988 / 75 | 99.94% | 低扫描索引需更长重置窗口，不立即删 |
| `zsxq_sync_runs` | 37MB | 1,077 / 61 | 99.90% | 代表查询 executor 1.557ms |
| `external_documents` | 7.0MB | 1,817 / 146 | 98.73% | 无热点证据 |

没有 exact duplicate index。低扫描候选仍包括 attachments PDF group-tab 约 5MB（scan=1）和 topics
group/type 约 3.8MB（scan=1），但统计自 8 月服务器启动累积，缺独立 reset-window 写放大与反事实计划证据；
不生成 DROP DDL。`pg_stat_statements` 未安装，当前无 waiting lock。**本轮没有 CREATE/DROP INDEX、ALTER、
数据回填或生产配置变更。**

## 已落地处置、测试与运行态验证

- 已把旧根 MP3 自然复发、YouTube 第 22 窗、B站情报本窗和未捕获边界写入对应 Bug、README 与 recurrence
  registry；这属于防复发证据门禁，不冒充根因修复。
- 没有修改生产代码，因此无需重启，也不触发重启安全门。现有 backend、17/17 Celery service、Watchdog 和
  6 个 scheduler 进程持续运行；API/worker ready、broker 与队列状态通过真实接口复核。
- 定向诊断 37/37、完整 unit 1,741/1,741 通过；compileall、Celery revoke 边界（326 个 Python 文件）、
  worker app-factory 边界（28 个文件）、Bug strict（213/213 已归类）、JSON 校验和 `git diff --check` 均通过。
  测试只证明代码库一致性，不替代下一自然运行窗口。

## 待人工动作、风险与阻塞

- **P0：物理隔离旧运行体。** 在旧 MacBook Pro 停止旧 backend/Celery/Watchdog/scheduler/脚本；若不能证明
  全停，轮换 PostgreSQL/Redis 凭据并隔离 Celery broker namespace。直接杀当前数据库会话不能阻止旧进程重连。
- YouTube 仅保留既有 [`disable_unavailable_youtube_channels_20260920.sql`](../../sql/disable_unavailable_youtube_channels_20260920.sql)
  供 DBeaver 人工逐段复核执行，只处理连续 22 窗固定失败的 21/56。执行后要求两个自然周期 channel 49/49、
  video errors=0。
- 低使用索引证据不足，不生成删除脚本。未来若整改，必须先固定统计 reset 时间、写速率、真实 SQL 与
  `EXPLAIN (ANALYZE, BUFFERS)`，再提供 lock/statement timeout、验证和回滚。

## 未捕获边界

- 固定窗只有 23 个请求，无法覆盖全部业务页面/API；尤其 audio-assets、Publisher upload-only、内容管线与完整
  ZSXQ 同步没有自然样本。慢 SQL 数量下降主要受覆盖量影响。
- `pg_stat_statements` 不可用，低于应用日志阈值的服务端累计指纹无法交叉验证；当前依靠应用结构化日志、
  request id、Session hold、PgBouncer、pg_stat 表/索引与代表 EXPLAIN 三角验证。
- 窗后错误路径探测新增的两个 404 不属于固定窗口自然业务；自检本身也说明错误方法不能拿来验收 POST API。
- 当前无远端旧地址会话仅是瞬时快照；旧根 durable task 和旧机同窗在线才是复发主证据。

## 根因闭环状态表与下一轮验收

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| old-root MP3 / runtime topology | 旧主机共享 DB/Redis/broker，物理隔离未完成 | actor/root 快照、终态审计与进程拓扑门禁 | 1/1 old-root completed；旧机任务后在线约 4h38m | 是 | 人工停旧栈或轮换凭据并隔离 namespace | 完整 24h old/foreign/remote actor 全 0，且当前根自然 PDF→ZSXQ、MP3→Publisher 成功 |
| Insights `674016d39deab3f5` | executor 外 driver/PgBouncer/私网 fetch 尾部 | 既有投影/有界聚合 | 应用 2.059s，executor 40.705ms | 是 | 补 pool acquire/fetch bytes/deserialize 同秒证据 | 自然 overview 冷暖样本分段，P95 改善且无额外 SQL |
| notification due `c1e57cc097d3e051` | 远程 fetch 与周期 drain 累计 | provider drain 与 outbox 状态机 | 4 次/4.518s，通知 34/34 sent、attempt max=1 | 轻微反复 | 继续关联队列深度/provider 数 | 完整窗无失败风暴，单次与累计不再恶化 |
| audio catalog `be891b5c1af362da` | executor 外 driver/fetch/私网尾部 | O(1) 投影、single-flight、Session 外文件校验 | 本窗 0 自然请求，无法验收 | 未捕获 | 不得据 0 次关闭 | 至少冷/暖自然请求，记录 SQL 数、bytes、分段与网络现场 |
| stale upower tier | durable 外部 ID 执行前未复核 | Session 外权威预校验、可重选终态 | 本窗无 upload-only | 修复后未捕获 | 等自然样本，不制造投稿 | 失效项上传=0、明确可重选；自然成功且后窗 21523=0 |
| chunk retry exhausted | 外部分片连续失败且缺幂等续传 | 4 次上限/总时限与 durable 错误 | 本窗无 upload-only | 未捕获 | 捕获响应/CDN/session，证明幂等后再自动化 | 安全恢复且无重复稿件，完整后窗无同形失败 |
| YouTube 21/56 | 永久不可用来源仍启用 | 受保护人工 SQL 已备 | channel 49/51、video errors=2 | 是，第 22 窗 | 人工 DBeaver 停用两源 | 连续两个自然周期 49/49、errors=0 |
| B站情报 DNS/TUN | 历史外部链路断点未取证 | 有界周期与失败保留 | 23/23、161/161，P95 14.260s | 本窗否 | 下次失败同秒采 DNS/代理/TUN/HTTP 阶段 | 完整自然窗成功且尾延迟稳定；失败时有物理断点证据 |

下一轮必须先验证旧 Mac 物理隔离结果，再复核完整 24 小时 old-root/foreign actor/远端连接；其次补足自然
audio-assets、overview、Publisher upload-only 和 ZSXQ 全链路样本，并复查 YouTube 21/56 与 notification drain。
未满足表中自然运行态与后窗条件前，任何项目都不得写成 CLOSED。
