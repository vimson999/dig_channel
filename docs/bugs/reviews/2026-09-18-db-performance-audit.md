# 2026-09-18 每日数据库性能与错误巡检

## 窗口、基线和根因闭环结论

固定窗 `2026-09-17 06:00:00`–`2026-09-18 06:00:00`（Asia/Shanghai）。先读取自动化记忆、09-17 报告、Bug、数据库 Session 纪律与 recurrence registry。本窗经修正的日志分析器读取顶层独立 scheduler、web、Celery、慢 SQL 与 PgBouncer 轮转日志。**数据库网络根因再次复发，不能 CLOSED**；本轮另定位并修复数据库故障把已停用的 ZSXQ all-tabs 计划临时启用的 fail-open 代码。06:18 已安全替换运行进程，但真实故障及后续完整窗口仍需验收。

| 指标 | 09-17 基线窗 | 本窗 | 变化和解释 |
| --- | ---: | ---: | --- |
| 完成慢 SQL / 累计 | 10 / 18.974s | 16 / 30.106s | +60% / +58.7%；另有失败 SQL 15.208s 不在慢日志 |
| 完成慢 SQL P50 / P95 / 最大 | 1.209 / 5.042 / 5.042s | 1.188 / 4.772 / 6.118s | P95 -5.4%，最大 +21.3%；不能以分位数下降掩盖断连 |
| HTTP 完成 / 成功 / 失败 | 133 / 132 / 1 | 749 / 745 / 4 | 四个失败均为 `favicon.ico` 404，日志内业务 5xx=0 |
| HTTP P50 / P95 / 最大 / 累计 | 7.33ms / 751.11ms / 12.525s / 52.119s | 6.52ms / 1099.46ms / 5.244s / 226.034s | P95 +46.4%，业务流量和页面结构不同 |
| Celery 成功 / 失败 / retry | 151 / 0 / 0 | 208 / 1 / 0 | 唯一 Celery 失败为 B站定时发布时间被上游拒绝，非 DB SQL 错误 |
| PgBouncer 去重 query-wait / TLS 接收错误 / server crash | 恢复后 0 / 0 / 0 | 1 / 1 / 1 | 01:36 同一连接故障 episode；正确 Tailscale route 上仍有链路瞬断 |
| 连接持有 >5s / 最大 | 1 / 5.108s | 2 / 15.218s | 修复独立 scheduler 日志漏读后得出；非 SQL 合计仅 70ms |

前窗停机使 HTTP 流量低，不能用同比数量判断需求恶化；但 SQL 长尾、PgBouncer 断连、ZSXQ 配置误启是同一日确实复发或新增的问题。`server_closed` 分析器原始计数 11 中，10 条来自单元测试向 B站独立日志写入的 `for test`、`publish-task-1`、`sync-disconnect-1` 模拟错误；真实受管事件为 01:36 ZSXQ 一条。不能把原始 11 条当成生产失败风暴；测试日志污染本身是待消除的观测边界。

## 页面、API、任务与 SQL 关联

本窗 76 个 method/path、749 个已完成请求，按累计耗时、P95、最大、失败与用户影响排序如下。完整已观测端点在附表；它只覆盖进入 request logger 的流量，未观测页面不等于零故障。

| 入口 / 来源 | 请求或任务；成败 | 累计 / P95 / 最大 | SQL、Session 和用户影响 |
| --- | --- | --- | --- |
| `GET /api/v1/content-pipeline/web-submissions` | 105；105/0 | 33.781s / 435ms / 596ms | 无匹配 >=1s SQL；较前窗 30 次、12.449s，轮询量 +250%、累计 +171%；单次 P95 改善，整体往返成本恶化。`94e2385b` 最慢。 |
| `POST /api/v1/content-pipeline/web-submit` | 6；6/0 | 26.091s / 5.244s / 5.244s | `6550f6e3` 的 8 行 canonical admission 仍为一次 SELECT、一次批量 INSERT；任务和两次通知 outbox 串行。无 >=1s SQL/长 Web hold，但缺各阶段 marker，端到端仍 OPEN。前窗 3 次、P95 12.525s；本窗低于前窗但并非根因关闭。 |
| `POST /api/mp3-video-workflows/jobs/:id/bilibili-upload` | 9；9/0 | 24.265s / 4.310s / 4.310s | `baf52061` 有一条 1.134s 的 `load_projected_snapshot` SQL，其余耗时含任务准备、broker/通知；请求成功不代表后续上传成功。 |
| `GET /api/mp3-video-workflows/jobs/:id/bilibili-uploads` | 30；30/0 | 21.464s / 1.099s / 1.166s | 状态轮询，无匹配 >=1s SQL；`5c4e30db` 最慢，其他 500ms guard 样本仍有点查，需对照页面可见/active 状态。 |
| `GET /api/mp3-video-workflows/jobs/:id` | 27；27/0 | 13.937s / 1.214s / 1.313s | 详情端点，无匹配 >=1s SQL，不能据此认为无小 SQL/JSON 成本。 |
| `GET /api/mp3-video-workflows/audio-assets` | 3；3/0 | 9.547s / 5.173s / 5.173s | `84fe5b99`、`059fec85` 的同一 canonical SQL 为 4.772/4.161s、合计 8.933s；旧逐项附件 N+1 未见，但网络/驱动长尾反复。 |
| B站情报独立 scheduler | 24 个整点周期失败，另 2 个单元测试污染的 partial 行 | 01:36 该轮 9.818s | 6.118s 配置点查 `fd28c66e902e8061` 与 PgBouncer 故障同窗；随后六个外部 B站 competitor 全部 `nodename nor servname provided`。其它 23 个整点也均外部连接失败；外部 DNS/代理链路另列 OPEN。 |
| ZSXQ all-tabs 独立 scheduler | 配置读取失败 1 次，恢复后 1 次 | 失败 SQL 15.208s、hold 15.218s | `system_configs` 两行配置查询来自 `_db_config_rows`；01:36:30 错误启用两个已停用计划，01:37:00 读库恢复移除，无意外投递。失败 SQL 不进入慢查询完成日志。 |
| Celery Publisher / PDF clean / upload-only / ZSXQ publish / MP3 / notification | 分别成功 6/6/7/6/47/136；upload-only 失败 1 | 成功累计 1326.037/1074.650/870.722/631.117/380.695/328.751s | 后台长任务不挂在前台 HTTP；无证据把外部处理累计秒数等同数据库 Session 持有。唯一失败 task `60261630-...` 的业务 task `87871f82-...` 已 `failed/failed`，上游错误码 21173：定时发布时间已不满足至少提前 5 分钟。 |

三类异常单列：低频高延迟方面，本窗没有 >10s 的已记录 HTTP；低频最慢为 6 次 Web submit 最大 5.244s，3 次素材重生成背景 P95 3.641s、封面 2.515s/3.202s。页面/API 很慢而无匹配慢 SQL 的包括 Web submit、MP3 详情与上传状态、素材重生成及 B站选项；部分小 SQL、文件和 broker/外部 I/O 尚未分段。无前台端点的后台长任务为 PDF clean、Publisher、ZSXQ publish、B站情报调度及 ZSXQ 配置读取失败；后两者分别有外部连接故障和数据库断连证据。ZSXQ insights overview、topics/local、topics/local/tabs、sync/status 均无自然前台请求，继续列为未验收；本轮不制造用户数据或依赖未授权会话假造页面样本。

## SQL、连接和根因定位

16 条完成慢 SQL 合计 30.106 秒。按累计：音频目录 `be891b5c1af362da` 2 条/8.933s（29.7%）、B站情报配置 `fd28c66e902e8061` 1 条/6.118s（20.3%）、Publisher stale repair `25b2a425193817e3` 3 条/3.313s（11.0%）；其余包括 Publisher cleanup/processing、内容清洗状态和通知 provider 扫描。目标表慢样本只有 `publication_tasks` 11 条，其余五目标表均 0 条；这是 >=1s 阈值口径，并不代表没执行。

01:36:15 ZSXQ scheduler 发出 `SELECT system_configs.key,value,status,updated_at ... WHERE type/scope/key LIKE ...`；01:36:30 PgBouncer 记一次 `query_wait_timeout`，同秒应用报 `server closed the connection unexpectedly`，失败 SQL 用去 15.208s，连接持有 15.218s/非 SQL 10ms。01:36:35 池端 `tls_sbufio_recv read failed: Operation timed out` 和 server crash，01:36:46 一次重连失败，01:36:51 新服务连接从 Tailscale `100.96.3.12` 建立 TLS。B站情报配置 `SELECT system_configs.value,status WHERE key/scope` 在 01:36:50 完成 6.118s，连接持有 6.205s/非 SQL 60ms。两条小配置 SELECT 的同期长尾及断连指向 PgBouncer/远端私网链路，不支持“PostgreSQL executor 慢”或索引缺失的判定；但缺同秒抓包、PgBouncer pool waiting/远端 PostgreSQL wait 现场，无法把瞬断再精确归因 Tailscale、代理或远端主机。

本窗 PgBouncer 289 条新服务连接，全部 `100.96.3.12`，代理 TUN fallback 为 0；分钟 wait/query P95 为 44.56/77.52ms、峰值 170.82/206.76ms，建连 12.04/h。应用完成慢 SQL 的 statement/lock/query/connect timeout、deadlock、SQL error=0，但 ZSXQ **失败 SQL** 是单独的 OperationalError，不能写成“全库无错误”。短暂 idle-in-transaction offender 28 episode、最多 3 连接、最大事务龄 1.278s/idle 龄 1.120s、准入 defer 0；现场只读 `pg_stat_activity` 为 active 1/idle 9/idle-in-transaction 0、waiting lock 0。没有发现 >5s 非 SQL Session 跨外部 I/O；更短的外部交叉持有未能用本统计排除。

程序模式：音频目录仍是有界 canonical 单次读取，前窗真实 773 行 executor 2.508ms，旧附件逐项查询未见；本窗缺 request 级 pool wait/fetch bytes，继续观察。Web submit 8 行正常批量 1 查 1 插，无逐文件 N+1；阶段 marker 缺失使哈希、任务 commit、broker 和两次 outbox 的精确份额仍未闭环。Web history 105 次及 MP3 上传记录 30 次属于重复轮询，需要用页面 visible/active/single-flight 状态与真实 SQL 数复核。独立 B站情报 24 次整点失败属于外部解析/连接风暴，当前系统 DNS 返回 `198.18.0.23` fake IP、系统 HTTP(S) 代理指向 `127.0.0.1:7890`，巡检时该端口无 listener；因 aiohttp 是否使用系统代理和 TUN 路由缺现场链路跟踪，不能直接认定 7890 是唯一根因。生产网络/代理配置未无人值守改动。

## 目标表、索引与执行器

只读当前总大小：`publication_tasks` 135MB（TOAST 105MB）、`zsxq_topics` 129MB（TOAST 79MB）、`zsxq_topic_comments` 118MB、`zsxq_attachments` 78MB、`zsxq_sync_runs` 37MB、`external_documents` 7.0MB，与前窗近似不变。对应累计索引扫描占比 99.80%、99.54%、99.87%、99.95%、99.92%、98.78%；统计自 08-08 起累计，不能当单日增量。`publication_tasks/topics/sync_runs/attachments` 的 HOT update 比 1.25%/0.71%/0.33%/0.12%，继续观察写放大；comments dead tuples 5,644。精确重复索引 0，waiting lock 0；`pg_stat_statements` 未安装。

只读 `EXPLAIN (ANALYZE, BUFFERS)` 的代表 executor：all-tabs 配置 0.022ms、Publisher 点查 0.036ms/列表 0.083ms、内容列表 0.184ms、ZSXQ system sync 1.580ms、channel chart 3.107ms、Insights canonical 聚合 34.537ms。all-tabs 配置顺扫 18 条配置只返回 2 行，顺扫符合当前表规模。`ix_zsxq_attachments_pdf_assets_group_tab` 5.0MB/累计 scan 1 与 `ix_zsxq_topics_group_type_create_desc` 3.7MB/scan 1 仍需真实参数 SQL 与对应 EXPLAIN 才能评估删除；没有证据支持新增或移除索引。**本轮无待人工执行的新 SQL**；已有维护脚本需独立审批，不据此次秒级应用等待执行 DDL。未无人值守 CREATE/DROP INDEX、ALTER、回填或生产配置修改。

## 根因闭环状态表

| 指纹 / 复发键 | 根因、变化和修复 | 运行态证据 / 是否复发 | 剩余风险、责任动作和下一轮验收条件 |
| --- | --- | --- | --- |
| 01:36 ZSXQ 失败配置 SQL + PgBouncer query-wait；`DB-SESSION-LIFECYCLE` | 正确 route 上私网/远端连接瞬断；本轮补齐独立 scheduler 日志发现。失败 SQL 15.208s，executor 代表 0.022ms。 | **再次复发，OPEN/REPEATED**；01:36 TLS 超时/server crash，01:36:51 TLS 恢复；当前 health=200、Tailscale Running/utun6、289/289 正确 source，但未闭环。 | 运行拓扑维护者采下次事故的 Tailscale/代理/远端 DB/PgBouncer 同秒快照；下一完整窗需 query-wait、crash、TLS error、失败 SQL 全 0，并有 API/任务连续运行证据。 |
| `system_configs` fail-open all-tabs；`RUNTIME-TOPOLOGY-RELOAD` | 读库异常把两个 `status=0` 计划当 enabled；已改为 rollback/remove 后返回空计划，TDD 回归红转绿。 | 旧进程 01:36 误注册 30s，未投递；06:18 安全重启后新 scheduler 读到两条停用配置，`targets=[] queued=False`，**RUNTIME_REPLACED / OBSERVING**，尚无新进程真实 DB 故障样本。 | 调度器负责人在下一真实/受控 DB 故障核对 0 计划/0 投递，恢复后只按 DB `status` 注册；完整后窗无复发后再关闭。 |
| 音频 `be891b5c1af362da`；`HOT-PATH-PAYLOAD-POLLING` | 两次 4.161/4.772s，旧 N+1 未见；canonical 缓存/Session 释放既有修复在运行。 | **同指纹反复，OPEN/OBSERVING**；3 个 API 200，P95 5.173s，不能以成功响应关闭延迟。 | 数据访问维护者采冷/暖 request 的 pool wait、driver fetch、bytes、行数；冷 <=2 SQL，暖 0 SQL，完整窗无 >5s hold 与长尾。 |
| Web submit `6550f6e3` + history polling；`HOT-PATH-PAYLOAD-POLLING` | 批量 1 查 1 插维持，端到端 P95 从 12.525s 降至 5.244s；history 次数 30→105。 | 6/6 请求 202，但阶段耗时缺口和轮询累计恶化，**OPEN/OBSERVING**。 | 内容管线维护者记录六阶段、Session 与 canonical/outbox 最终状态；验证单飞、visible/active 停轮询，下一窗 P95/累计耗时按流量归一比较。 |
| B站情报独立 scheduler 外部 DNS/连接；`EXTERNAL-CALL-RESILIENCE` | 24 个整点失败/6 competitor 均失败，01:36 同时叠加 DB 长尾；本轮未改系统网络，已新增 Bug 及复发登记。 | **OPEN/REPEATED**；当前 fake-IP 可解析但 7890 无 listener，尚未证明具体断点。 | 网络/情报维护者在下一周期采 DNS、proxy/TUN、aiohttp 目标、HTTP 状态与重试/通知；真实周期至少一次 6/6 成功，后续窗无失败风暴。 |
| B站定时上传 task `87871f82-...`；`SCHEDULED-UPLOAD-TIME-MARGIN` | 入口时余 5m28s、外部调用时余 4m22s，上游码 21173 拒绝；09-12 有同码失败。已新增 Bug 及复发观察。 | **OPEN/REPEATED**，durable task 已 failed/failed 且保留 error；非 SQL/索引问题。 | 上传维护者量化排队/上传并修订时序契约，不静默改用户定时点；定向回归和自然定时上传成功后再观察完整窗。 |

## 变更、验证与未捕获边界

- 分析器现纳入所有顶层独立 scheduler 及轮转日志，并识别实际 `database connection held too long` 格式；故障样本回归先 0 后 1，分析器定向 32/32。真实重放使长 hold 从误报 0 变成 2，发现失败 SQL 不在 slow log。
- ZSXQ fail-open 修复先由已停用配置故障注入验证旧实现返回 2 条 enabled，再改为返回 0；最终 ZSXQ/调度器/分析器/Bug 相关定向 58/58。没有删除原始 JSON、字段或审计数据。
- 完整 unit 正确以 `PYTHONPATH=src/backend` 运行 1691 项，1690 通过、1 失败：工作区既有未提交的排行榜日期断言硬编码 `2026-09-15`，实现返回运行当天 `2026-09-18`；前窗同一断言也失败，与本轮变更无关，未覆盖用户工作区。`compileall -q src/backend scripts tests/unit`、app-factory 28 文件、revoke 321 文件、Bug strict 203/203、registry JSON 和 `git diff --check` 通过。
- 06:08 与 06:17 两次重启安全门均确认 46 条近期 PublicationTasks、0 个活动同步、0 个不可中断任务；标准 `restart-dev-stack.sh` 06:18 完成。新后端引导 17/17 Celery starter、6/6 scheduler、errors=[]；各普通 worker 及浏览器/PDF worker 日志有 06:18 ready。真实 `/health`=200；新 ZSXQ scheduler 06:18:32 DB 配置读取 2 行，两条目标 disabled，`startup_trigger queued=False targets=[]`，无启用 all-tabs job。尚无新进程真实数据库故障注入，也无故障后的完整 24h 窗口，故只到 RUNTIME_REPLACED/OBSERVING。
- Tailscale `Running`、DB route `utun6`；现场两条 all-tabs 数据库配置 `status=0`，DB active1/idle9/idle-in-transaction0。独立页面 ZSXQ 四个关键入口无自然请求；`pg_stat_statements` 不可用，PgBouncer 分钟平均无法对齐单个 request。HTTP 日志、Celery 成功日志和数据库只读快照为不同证据流，未捕获不等于未发生。

## 下一轮优先验收

1. 先核对 06:18 新进程后的完整运行窗：PgBouncer `query_wait_timeout`、TLS/server crash、失败 SQL、错误 all-tabs 注册和意外投递均须为零；若再断连，同秒保留 Tailscale 路由、proxy/TUN、PgBouncer pool 与 PostgreSQL wait 现场。仅一次零错误窗口不替代基础网络根因定位。
2. 在自然 ZSXQ overview、topics/local、tabs、sync/status 请求及调度周期中关联 request id、SQL origin、pool wait/driver、Session 持有和结果；若无真实请求继续标注未捕获。下一次数据库配置读取故障验证停用计划始终 0 job/0 投递，恢复后按 `status=0` 保持停用。
3. 对音频目录采冷/暖 SQL 数、行数与 bytes，对 Web submit 补接收/哈希/commit/broker/outbox 阶段耗时，对两类轮询按页面 visible/active 统计请求和累计耗时；P95/最大和累计按流量归一与本窗对照。
4. 对 B站情报下一真实整点周期抓 DNS/代理/TUN/aiohttp 实际目标并确认 6/6 成功；对定时上传量化入口、队列、媒体传输与外部投稿时余量，修订时序合同并通过真实自然定时任务验收。仍失败的业务任务保留失败终态与原始错误。
5. 只读复核目标表扫描、HOT、dead tuples 和低 scan 索引；获得真实参数 SQL 与 `EXPLAIN (ANALYZE, BUFFERS)` 前不提出执行 DDL。

## 已观测端点全表

下表覆盖日志中的全部 76 个 method/path，按累计耗时排列；毫秒，慢 SQL 是同 request id 的 >=1s 完成查询，500ms `publication_tasks` guard 和失败 SQL 不计入该列。

| 方法与路径 | 请求 | 成功/失败 | 累计 ms | P50 / P95 / max ms | 慢 SQL | 最慢 request id |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `GET /api/v1/content-pipeline/web-submissions` | 105 | 105/0 | 33781.19 | 331.71 / 434.84 / 595.75 | 0 | `94e2385b` |
| `POST /api/v1/content-pipeline/web-submit` | 6 | 6/0 | 26091.11 | 4126.65 / 5244.33 / 5244.33 | 0 | `6550f6e3` |
| `POST /api/mp3-video-workflows/jobs/:id/bilibili-upload` | 9 | 9/0 | 24264.50 | 2865.10 / 4310.36 / 4310.36 | 1 | `baf52061` |
| `GET /api/mp3-video-workflows/jobs/:id/bilibili-uploads` | 30 | 30/0 | 21463.81 | 754.46 / 1099.46 / 1165.98 | 0 | `5c4e30db` |
| `GET /api/mp3-video-workflows/jobs/:id` | 27 | 27/0 | 13937.35 | 472.05 / 1214.19 / 1313.40 | 0 | `594ce36f` |
| `POST /api/mp3-video-workflows/jobs/:id/artifacts/xiaojunjun_background/regenerate` | 3 | 3/0 | 10373.03 | 3479.80 / 3641.01 / 3641.01 | 0 | `035e1e64` |
| `GET /api/mp3-video-workflows/jobs` | 47 | 47/0 | 9813.01 | 262.11 / 488.97 / 786.18 | 0 | `2afef253` |
| `GET /api/mp3-video-workflows/audio-assets` | 3 | 3/0 | 9546.69 | 4371.56 / 5173.46 / 5173.46 | 2 | `84fe5b99` |
| `GET /api/v1/platform-credentials` | 27 | 27/0 | 9376.35 | 308.22 / 642.74 / 733.63 | 0 | `04fff7a1` |
| `POST /api/mp3-video-workflows/jobs` | 3 | 3/0 | 8774.51 | 3031.62 / 3546.96 / 3546.96 | 0 | `cab37f02` |
| `POST /api/mp3-video-workflows/jobs/:id/artifacts/xiaojunjun_cover/regenerate` | 3 | 3/0 | 7272.08 | 2452.46 / 2515.22 / 2515.22 | 0 | `0ddce6ef` |
| `POST /api/mp3-video-workflows/jobs/:id/artifacts/finance_cover/regenerate` | 2 | 2/0 | 6381.72 | 3179.43 / 3202.29 / 3202.29 | 0 | `985d9a30` |
| `GET /api/bilibili-publish/charging-options` | 6 | 6/0 | 4857.11 | 627.25 / 1549.20 / 1549.20 | 0 | `c9121245` |
| `POST /api/mp3-video-workflows/jobs/:id/continue-full-video` | 3 | 3/0 | 4376.00 | 1458.05 / 1474.14 / 1474.14 | 0 | `bc2bf4e3` |
| `POST /api/mp3-video-workflows/jobs/:id/bilibili-upload/:id/cancel` | 2 | 2/0 | 4302.47 | 622.79 / 3679.68 / 3679.68 | 0 | `495b7179` |
| `GET /api/bilibili-publish/seasons` | 4 | 4/0 | 3487.44 | 836.28 / 1475.21 / 1475.21 | 0 | `043b63ba` |
| `GET /api/mp3-video-workflows/jobs/:id/download-artifact/keyframe_landscape_01` | 7 | 7/0 | 2704.98 | 352.21 / 1031.52 / 1031.52 | 0 | `4be4a3a0` |
| `GET /api/mp3-video-workflows/jobs/:id/download-artifact/keyframe_landscape_02` | 7 | 7/0 | 2690.31 | 351.39 / 1033.71 / 1033.71 | 0 | `a0c44f30` |
| `GET /api/mp3-video-workflows/jobs/:id/download-artifact/keyframe_landscape_03` | 7 | 7/0 | 2666.14 | 324.68 / 1032.07 / 1032.07 | 0 | `fb39d7ce` |
| `GET /api/mp3-video-workflows/jobs/:id/download-artifact/keyframe_vertical_01` | 7 | 7/0 | 2643.29 | 325.71 / 1029.13 / 1029.13 | 0 | `03928b49` |
| `GET /api/mp3-video-workflows/jobs/:id/download-artifact/keyframe_vertical_02` | 7 | 7/0 | 2633.48 | 323.71 / 1030.45 / 1030.45 | 0 | `8c36e8ed` |
| `GET /api/mp3-video-workflows/jobs/:id/download-artifact/keyframe_vertical_03` | 7 | 7/0 | 2486.16 | 323.60 / 1032.63 / 1032.63 | 0 | `52997728` |
| `GET /api/mp3-video-workflows/jobs/:id/download-video/landscape` | 60 | 60/0 | 1888.91 | 1.20 / 319.21 / 503.65 | 0 | `12673cf7` |
| `GET /api/mp3-video-workflows/jobs/:id/download-artifact/xiaojunjun_background` | 10 | 10/0 | 1318.13 | 4.10 / 536.59 / 536.59 | 0 | `f40b066e` |
| `GET /api/v1/rankings/all` | 2 | 2/0 | 1289.40 | 501.08 / 788.32 / 788.32 | 0 | `26aada4c` |
| `GET /api/v1/celery/runtime-status` | 3 | 3/0 | 1149.91 | 376.90 / 400.20 / 400.20 | 0 | `7e415b87` |
| `GET /api/mp3-video-workflows/jobs/:id/download-artifact/xiaojunjun_cover` | 10 | 10/0 | 1033.06 | 4.92 / 368.43 / 368.43 | 0 | `8ac89584` |
| `GET /api/mp3-video-workflows/jobs/:id/download-video/vertical` | 39 | 39/0 | 932.57 | 1.91 / 20.38 / 510.09 | 0 | `9312c2d9` |
| `GET /api/mp3-video-workflows/jobs/:id/download-artifact/finance_cover` | 9 | 9/0 | 855.65 | 3.50 / 448.21 / 448.21 | 0 | `bc81e269` |
| `GET /api/v1/data/summary` | 1 | 1/0 | 718.65 | 718.65 / 718.65 / 718.65 | 0 | `a5252ea1` |
| `GET /api/v1/data/chart-data` | 2 | 2/0 | 544.68 | 252.96 / 291.72 / 291.72 | 0 | `a026e02e` |
| `GET /api/v1/data/table` | 1 | 1/0 | 473.61 | 473.61 / 473.61 / 473.61 | 0 | `49dc3287` |
| `GET /mp3-video-workflow-detail.html` | 7 | 7/0 | 260.35 | 2.13 / 146.04 / 146.04 | 0 | `ee963cd1` |
| `GET /api/v1/channels` | 1 | 1/0 | 197.34 | 197.34 / 197.34 / 197.34 | 0 | `d3e19cfa` |
| `GET /mp3-video-workflow.html` | 3 | 3/0 | 193.59 | 2.07 / 189.90 / 189.90 | 0 | `812e6e67` |
| `GET /assets/images/apple-touch-icon.png` | 1 | 1/0 | 164.97 | 164.97 / 164.97 / 164.97 | 0 | `e3d1f3ec` |
| `GET /js/bilibili-publish-panel.js` | 7 | 7/0 | 99.80 | 7.83 / 47.11 / 47.11 | 0 | `54d7d5c3` |
| `GET /js/shared-navigation.js` | 11 | 11/0 | 89.34 | 5.76 / 29.97 / 29.97 | 0 | `e1379b43` |
| `GET /api/mp3-video-workflows/status` | 3 | 3/0 | 85.81 | 14.62 / 59.26 / 59.26 | 0 | `798da399` |
| `GET /js/platform-browser-publish-panel.js` | 7 | 7/0 | 82.82 | 6.20 / 48.82 / 48.82 | 0 | `e20d98b8` |
| `GET /api/v1/auth/verify` | 31 | 31/0 | 78.75 | 1.02 / 9.83 / 17.30 | 0 | `fde3343d` |
| `GET /js/mp3-video-workflow-detail.js` | 7 | 7/0 | 76.27 | 5.73 / 43.56 / 43.56 | 0 | `0510732c` |
| `GET /js/env.js` | 11 | 11/0 | 69.31 | 5.49 / 11.22 / 11.22 | 0 | `3611ea4b` |
| `GET /js/upload-success-sound.js` | 7 | 7/0 | 66.78 | 6.80 / 23.15 / 23.15 | 0 | `a666046b` |
| `OPTIONS /api/mp3-video-workflows/jobs/:id/bilibili-uploads` | 29 | 29/0 | 66.32 | 2.14 / 4.44 / 6.14 | 0 | `021ffb48` |
| `OPTIONS /api/mp3-video-workflows/jobs` | 29 | 29/0 | 62.23 | 1.49 / 6.52 / 7.26 | 0 | `4d113d67` |
| `OPTIONS /api/mp3-video-workflows/jobs/:id` | 27 | 27/0 | 49.02 | 1.49 / 3.44 / 5.42 | 0 | `c764eb53` |
| `OPTIONS /api/v1/platform-credentials` | 24 | 24/0 | 47.66 | 1.55 / 4.20 / 9.03 | 0 | `de044a39` |
| `OPTIONS /api/bilibili-publish/charging-options` | 6 | 6/0 | 23.70 | 1.70 / 14.91 / 14.91 | 0 | `d9cfc2da` |
| `OPTIONS /api/mp3-video-workflows/jobs/:id/bilibili-upload` | 9 | 9/0 | 23.23 | 2.20 / 5.49 / 5.49 | 0 | `a003fa1d` |
| `OPTIONS /api/v1/auth/verify` | 11 | 11/0 | 22.93 | 1.98 / 4.35 / 4.35 | 0 | `1782a3a2` |
| `GET /api/mp3-video-workflows/jobs/:id/download-artifact/audio_background` | 7 | 7/0 | 20.03 | 3.19 / 5.51 / 5.51 | 0 | `200933e5` |
| `GET /js/mp3-video-workflow.js` | 3 | 3/0 | 17.67 | 4.50 / 9.47 / 9.47 | 0 | `6717ee60` |
| `GET /js/download-count-highlight.js` | 3 | 3/0 | 17.25 | 4.26 / 9.86 / 9.86 | 0 | `8d94fa7f` |
| `GET /favicon.ico` | 4 | 0/4 | 12.88 | 4.20 / 5.50 / 5.50 | 0 | `6a0d1095` |
| `OPTIONS /api/bilibili-publish/seasons` | 4 | 4/0 | 8.28 | 2.57 / 3.36 / 3.36 | 0 | `5ca92b47` |
| `GET /assets/sounds/xm4012.mp3` | 4 | 4/0 | 7.34 | 1.99 / 2.20 / 2.20 | 0 | `cc0be008` |
| `OPTIONS /api/v1/celery/runtime-status` | 3 | 3/0 | 5.89 | 2.10 / 2.58 / 2.58 | 0 | `f4c85519` |
| `OPTIONS /api/v1/bilibili-intelligence/charging-dashboard` | 1 | 1/0 | 5.73 | 5.73 / 5.73 / 5.73 | 0 | `39cd5735` |
| `OPTIONS /api/mp3-video-workflows/jobs/:id/artifacts/xiaojunjun_background/regenerate` | 3 | 3/0 | 5.64 | 1.98 / 2.03 / 2.03 | 0 | `83e879db` |
| `OPTIONS /api/mp3-video-workflows/status` | 3 | 3/0 | 4.82 | 1.63 / 2.11 / 2.11 | 0 | `ba79045c` |
| `OPTIONS /api/v1/channels/categories` | 1 | 1/0 | 4.03 | 4.03 / 4.03 / 4.03 | 0 | `7c65cdf9` |
| `GET /health` | 1 | 1/0 | 3.96 | 3.96 / 3.96 / 3.96 | 0 | `ba61d10c` |
| `GET /js/content-pipeline-submit.js` | 1 | 1/0 | 3.88 | 3.88 / 3.88 / 3.88 | 0 | `c095ca3f` |
| `OPTIONS /api/mp3-video-workflows/audio-assets` | 3 | 3/0 | 3.84 | 1.57 / 1.85 / 1.85 | 0 | `3cc9e262` |
| `OPTIONS /api/v1/rankings/all` | 2 | 2/0 | 3.79 | 1.35 / 2.44 / 2.44 | 0 | `e5adcd52` |
| `OPTIONS /api/mp3-video-workflows/jobs/:id/artifacts/finance_cover/regenerate` | 2 | 2/0 | 3.41 | 1.45 / 1.96 / 1.96 | 0 | `b35663fe` |
| `OPTIONS /api/mp3-video-workflows/jobs/:id/continue-full-video` | 3 | 3/0 | 3.21 | 1.22 / 1.51 / 1.51 | 0 | `dfde6eee` |
| `OPTIONS /api/v1/channels/defaults` | 1 | 1/0 | 2.94 | 2.94 / 2.94 / 2.94 | 0 | `a003364a` |
| `OPTIONS /api/mp3-video-workflows/jobs/:id/artifacts/xiaojunjun_cover/regenerate` | 3 | 3/0 | 2.58 | 0.82 / 1.13 / 1.13 | 0 | `9142e086` |
| `OPTIONS /api/v1/data/chart-data` | 2 | 2/0 | 2.31 | 0.83 / 1.48 / 1.48 | 0 | `a1a2c60b` |
| `OPTIONS /api/mp3-video-workflows/jobs/:id/bilibili-upload/:id/cancel` | 1 | 1/0 | 2.18 | 2.18 / 2.18 / 2.18 | 0 | `ec944dff` |
| `OPTIONS /api/v1/channels` | 1 | 1/0 | 1.47 | 1.47 / 1.47 / 1.47 | 0 | `8bef941c` |
| `GET /content-pipeline-submit.html` | 1 | 1/0 | 1.45 | 1.45 / 1.45 / 1.45 | 0 | `c323c257` |
| `OPTIONS /api/v1/data/table` | 1 | 1/0 | 1.33 | 1.33 / 1.33 / 1.33 | 0 | `dafe5f60` |
| `OPTIONS /api/v1/data/summary` | 1 | 1/0 | 1.05 | 1.05 / 1.05 / 1.05 | 0 | `9e8bb677` |
