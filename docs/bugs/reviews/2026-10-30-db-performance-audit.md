# 2026-10-30 数据库性能与错误巡检

## 巡检边界与结论

- 固定窗口：2026-10-29 06:00 至 2026-10-30 06:00（Asia/Shanghai）；数据库只读快照对应 UTC
  `2026-10-28 22:00:00..2026-10-29 22:00:00`。
- 输入覆盖：`logs/slow_query.log`、`logs/app.log`、`logs/backend.log`、`logs/scheduler.log`、
  `logs/celery/*.log`、PgBouncer 1,440 个分钟样本、生产只读统计/EXPLAIN、durable task 快照，以及窗后
  health/重点 API/worker readiness。
- **本轮没有任何主要根因达到 CLOSED。** 慢 SQL 数量、累计和 P95 明显改善，但代表性 audio catalog
  尾延迟再次出现；远端旧运行体生产会话、YouTube 21/56、ZSXQ 1059 和 B站历史 DNS/TUN 都没有满足
  “根因修复 + 真实运行 + 后续完整窗不复发”。
- 本轮没有可在不改变字段完整性/页面语义、且无需外部或生产配置动作的安全业务代码修复。已落地的是只读
  A/B 诊断、防复发纪律、Bug 和 recurrence registry；没有重启常驻服务，没有执行 DDL/回填/生产配置。

## 与上一窗口的量化对比

| 指标 | 上一窗 | 本窗 | 变化与判定 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 29 / 67.720s | 15 / 27.651s | 数量 -48.3%，累计 -59.2%，改善 |
| 慢 SQL P50/P95/max | 1.222/11.133/12.993s | 1.256/2.018/9.288s | P50 +2.8%，P95 -81.9%，max -28.5% |
| audio catalog | 3 / 33.761s | 1 / 9.288s | 次数/累计改善，但单次复发，不能关闭 |
| HTTP 完成/端点 | 1,064 / 未完整列举 | 635 / 103 | 流量 -40.3%，覆盖下降需谨慎比较 |
| HTTP 成功/失败 | 1,054/10 | 629/6 | 失败率均约 0.94%；本窗无业务 5xx |
| HTTP 累计/P95/max | 357.256s / 1.027s / 50.275s | 260.767s / 1.477s / 49.271s | 累计 -27.0%，P95 +43.8%，max -2.0% |
| Celery completed/retry/failed | 171/0/0 | 181/0/0 | 完成数 +5.8%，无框架级失败 |
| 远端 MacBook DB 会话 | 7 | 6 | 改善但未归零，仍是 P0 隔离风险 |
| ZSXQ upload/retry/verify pending | 10/6/1 | 3/1/0 | 改善但 1059 同阶段复发 |
| YouTube channel/video | 0/51、0/51（SSLError） | 49/51、errors=2 | 全来源 TLS 故障暂未复发；21/56 第 27 窗复发 |
| B站情报自然周期 | 23/23、161/161 | 24/24、168/168 | 完整绿窗，历史网络根因继续观察 |

变化分类：

- **新增**：没有新增生产数据库根因；新建 ZSXQ publish/upload 1059 独立 Bug 记录，是既有复发的正式归档。
- **恶化**：HTTP P95 因本窗流量构成升至 1.477 秒；Publisher pipeline/upload-only 的日志累计壁钟分别
  1,959.485/1,926.583 秒，较上一窗约 +25.7%/+28.6%，但 P95 反而改善且 16/16 durable Publisher 成功，
  主要仍是外部上传/编排，不是数据库 executor。
- **改善**：慢 SQL 数量/累计/P95/max、HTTP 总耗时、ZSXQ 1059 事件、远端会话数、YouTube 全来源 SSL
  故障均改善。
- **反复**：audio 指纹 `be891b5c1af362da`、Insights 指纹 `674016d39deab3f5`、YouTube 21/56、
  ZSXQ 1059、远端旧运行体会话均复发或仍在。
- **仍未闭环**：Tailscale/psycopg2 结果接收、旧部署物理隔离、YouTube 永久失效源和间歇 TLS、ZSXQ 1059、
  B站 DNS/TUN 故障现场全部保持 OPEN/OBSERVING。

## 主要问题排序

排序综合累计壁钟、P95/max、失败率、受影响链路和复发次数；父子任务壁钟可能重叠，不相加冒充独立用户时间。

| 顺位 | 问题 | 累计/P95/max 或失败率 | 影响与复发判断 |
| ---: | --- | --- | --- |
| 1 | Publisher 外部上传/编排 | 日志两类累计 3,886.067s；P95 313.004/298.811s | 16/16 成功，无框架重试；耗时高但数据库无对应长 hold，外部 I/O/编排观察 |
| 2 | audio catalog `be891b5c1af362da` | SQL 9.288s；HTTP 10.075s；1/8 冷建 | 多窗复发，直接影响 MP3 页面冷启动；根因已收窄但未物理修复 |
| 3 | YouTube 21/56 | channel 2/51 失败、video errors=2 | 第 27 窗复发；影响两个来源，等待人工配置 |
| 4 | ZSXQ publish/upload 1059 | 3 upload failure、1 publish retry；任务 395.255s | 最终成功不抹中间失败；同错误码复发 |
| 5 | 远端旧运行体 | 当前 6 个生产 DB 会话 | 本窗任务全绿但仍有共享写权限风险，历史曾造成用户任务误关/竞态 |
| 6 | Insights `674016d39deab3f5` | SQL 2.018s；HTTP 4.170s；executor 40.910ms | 低频页面高延迟；旧全量文本 overfetch 未复发，公共远端接收仍 OPEN |

## SQL、Session、PgBouncer 与 executor 根因链

应用慢 SQL 共 15 条、27.651 秒；`publication_tasks` 5 条、`zsxq_topics` 1 条，其余重点表本窗 0 条。
SQL error、traceback、statement timeout、lock timeout、connection timeout、server closed、deadlock、query
timeout 全为 0。PgBouncer 八类错误全为 0，288 次连接均由 `100.96.3.12` 发起、代理 TUN 回退 0；wait
P50/P95/max 23.77/45.81/255.59ms，query 58.67/72.31/3,166.89ms。

唯一长 Session hold 为 request `791c4bd2`：9.351 秒，其中匹配 audio SQL 9.288 秒、非 SQL 63.42ms；
没有 Session 跨文件 I/O。21 个 idle 观测 episode 的最大连接 2、最大事务龄/idle 龄 0.375/0.259 秒，
无 pressure deferral，不构成持久 `idle in transaction`。

### audio catalog 新的 A/B 证据

- 自然请求：1,558 行，SQL 9.288 秒，HTTP 10.075 秒；同秒 PgBouncer 发生 1,800 秒 server lifetime
  rollover，但没有 cl_waiting/query timeout。
- PgBouncer 探针：connect 9.12ms，execute/fetch 9.105 秒，Python materialize 4.61ms，executor
  6.029ms，891,354 bytes。
- `DIRECT_DATABASE_URL`：connect 236.73ms，execute/fetch 9.067 秒，materialize 6.52ms，executor
  5.139ms，返回行数/字节数完全相同。
- 两条路径共同经 Tailscale `100.96.3.12 -> 100.92.99.39`，MTU 1280。由此排除 PostgreSQL executor、
  缺索引、PgBouncer 排队/转发和 Python 结果物化为本样本主因，公共断点收窄到 psycopg2 经 Tailscale 的
  结果接收链路。直连同慢不等于网络已定责，仍需 socket 分段或同机房/非隧道 A/B。

现有 2,500 行上限、字段投影、300 秒缓存、single-flight 和 Session 外文件校验有效；8 个请求只有 1 个
目录 SQL。字段服务于目录合并、解析和响应完整性，删除字段或服务端分页会改变业务语义，因此本轮不做。

## 页面/API、后台任务与 SQL 关联

635 个完整请求覆盖 103 个端点，629 成功、6 失败。失败为 4 个 `favicon` 404 和上一轮巡检产生的两个无效
`celery/runtime-status` 400（request `2ec9a419`、`075aea1d`，missing/invalid services，1.12/2.45ms）；
没有自然业务 5xx。这两条是巡检自污染，不能算运行故障，也不能从原始失败数删除。

| 页面/API | 请求/成功 | HTTP P95/max | 匹配慢 SQL/Session | 根因分类 |
| --- | ---: | ---: | --- | --- |
| `/api/mp3-video-workflows/audio-assets` | 8/8 | 10.075/10.075s | 1 条 9.288s、hold 9.351s | 远端结果接收；缓存 7/8 命中 |
| `/api/v1/zsxq-insights/overview` | 1/1 | 4.170/4.170s | aggregate 2.018s；executor 40.910ms | executor 外远端接收；旧 overfetch 未复发 |
| `/api/v1/zsxq/topics/local` | 1/1 | 1.097/1.097s | 无 >=1s 慢 SQL | 多条亚秒 SQL/远端往返，低频观察 |
| topics tabs / sync runs / scheduler | 各 1/1 | 0.341/0.415/0.654s | 无 >=1s 慢 SQL | 当前可用，样本低 |
| `/api/v1/videos/sync` | 1/1 | 49.271s | 无慢 SQL/长 hold | 外部 YouTube I/O |
| `/api/v1/channels/sync/all` | 1/1 | 28.575s | 无慢 SQL/长 hold | 外部 YouTube I/O |
| content web-submit | 1/1 | 8.647s | 无慢 SQL/长 hold | 文件/通知/broker 编排，缺完整阶段埋点 |
| MP3 job status | 24/24 | 0.964/1.670s | 无慢 SQL | 频繁轮询，累计 12.977s；需按请求量继续观察 |
| artifact regenerate 三类 | 11/11 | 约 2.84–3.07s | 无慢 SQL | 图像生成/文件 I/O |
| B站 charging dashboard | 5/5 | 2.014/2.014s | 无慢 SQL | 外部凭据/平台 I/O |

单独异常分类：

- **低频高延迟**：videos sync 49.271 秒、channels sync 28.575 秒、audio cold 10.075 秒、web-submit
  8.647 秒、Insights 4.170 秒。
- **页面/API 慢但无匹配慢 SQL**：两个 YouTube sync、web-submit、MP3 status/regenerate/upload、B站
  dashboard；按外部 I/O、文件 I/O、broker 或亚秒多次往返处理，不能误加数据库索引。
- **后台任务或连接很久但无前台端点**：181 个 Celery task 全完成；Publisher pipeline 8 次累计
  1,959.485 秒、upload-only 8 次 1,926.583 秒、MP3 process 70 次 596.943 秒、ZSXQ publish 1 次
  395.255 秒、notification 93 次 223.726 秒、audio clean 1 次 148.927 秒。无框架 retry/failed，长壁钟
  主要位于外部上传/处理链，不能由前台请求统计覆盖。

## 程序模式复核

- **N+1/超大分页/大字段**：audio catalog 仍是一条有界查询，无附件 N+1；1,558 行/891KB 是较大结果，但
  现有字段均被消费。Insights 只返回 12 个主题聚合，既有数据库聚合与缓存有效。未发现新的无限分页或整表
  ORM lazy-load 证据。
- **重复去重/聚合**：Publisher repair 指纹 `9abfe319e18a363f` 2 次/2.499 秒，是本窗唯一重复慢指纹；
  代表 EXPLAIN 仍命中现有索引。Insights 旧全文回传指纹未出现。
- **缓存失效**：audio 7/8 热命中，single-flight 正常；Insights 本窗只有一个自然样本，不能凭窗后 0.405 秒
  暖态关闭。
- **频繁轮询**：MP3 job status 24 次累计 12.977 秒，uploads 33 次累计 11.408 秒；单次总体可控，但必须按
  请求量继续追踪，避免用单次 P95 隐藏累计成本。
- **Session 与外部 I/O 交叉持有**：唯一长 hold 的非 SQL 仅 63.42ms；本窗未发现跨外部 I/O 持有数据库
  Session 的新证据。
- **失败风暴/重复投递**：Celery 框架 retry/failed 为 0；ZSXQ 内部 1059 有 3 次上传失败、1 次发布级有界
  重试，单独记录，不能因最终成功忽略。

## 重点表、索引与 DDL 裁决

| 表 | 总大小 | live/dead rows | 主要结论 |
| --- | ---: | ---: | --- |
| `publication_tasks` | 153MB（TOAST 120MB） | 14,824 / 2,723 | dead 较上窗约 +477；需观察 autovacuum，不足以人工 VACUUM/DDL |
| `zsxq_topics` | 133MB（TOAST 81MB） | 13,981 / 1,665 | canonical index 累计 scan 9,905，Insights 40.910ms executor |
| `zsxq_topic_comments` | 119MB | 45,884 / 6,093 | 本窗无目标慢 SQL，不能凭大小加索引 |
| `zsxq_attachments` | 79MB | 35,988 / 75 | 本窗无目标慢 SQL |
| `zsxq_sync_runs` | 37MB（TOAST 35MB） | 1,077 / 61 | system runs EXPLAIN 1.650ms |
| `external_documents` | 7,016KB | 1,817 / 146 | 本窗无目标慢 SQL |

重点表累计 index scan ratio 为 98.73%–99.94%，exact duplicate indexes 为 0，waiting locks 为 0。
代表 EXPLAIN：watchdog 点查 0.040ms、chart 3.028ms、all-tabs config 0.034ms、Publisher list 0.080ms、
content list 0.256ms、wide JSON recent 0.067ms 对 narrow 0.049ms、system runs 1.650ms、Insights 40.910ms。

`pg_stat_statements` 未安装，索引统计自 2026-08-08 server start 后未 reset；少扫描索引的累计值不是 DROP
证据。本轮真实 SQL、表统计与 EXPLAIN 都不支持 CREATE/DROP INDEX、ALTER TABLE、回填或人工 VACUUM，故
**不生成新 DDL 脚本，也不执行任何 DDL**。

## 反复问题、已落地动作与运行态验证

- audio/隧道：已完成相同 payload 的 PgBouncer/直连只读 A/B，并把新合同写入数据库 Session 纪律；这只是
  根因收窄，不是运行修复。
- ZSXQ 1059：新增独立 Bug，明确 upload failure/retry/verify pending 必须分开统计；既有有界重试和幂等是
  containment，不是 root fix。
- YouTube：上一窗全来源 SSLError 本窗恢复，但 21/56 第 27 窗复发；诊断合同仍为 RUNTIME_REPLACED，业务
  根因保持 OPEN。
- 远端旧运行体：23/23 durable task current-root，Publisher 16/16 成功；但远端 DB 会话 6 个，不能关闭。
- B站情报：24/24 周期、168/168 competitor 成功，累计 305.007 秒，P50/P95/max 12.481/14.196/
  15.433 秒；历史 DNS/TUN 断点未捕获，保持 OBSERVING。
- 通知：71 条 durable notification sent、attempts 71、max attempts 1，无重试风暴。

本轮只改诊断文档/纪律/复发登记，没有常驻代码变更，因此无需重启，也不触发重启安全门。窗后真实验收：

- `/health` 200/healthy，0.054 秒。
- overview/topics/tabs/sync-runs/system-scheduler 均 200，耗时 0.405/0.512/0.204/0.296/0.510 秒。
- `celery/runtime-status?services=mp3_video_workflow,render` 200；两个 worker 均 running、paused=false、queue=0。
- 定向 45/45 通过；完整 unit 首次在受限沙箱内仅有 34 个 loopback bind `PermissionError`，其余
  1,805 项通过；允许本机回环后 **1,831 passed、4 skipped、374 subtests passed**。
- `compileall`、worker app-factory 边界、Celery revoke 边界、registry JSON、Bug strict（217/217 已归类）
  均通过；最终 `git diff --check` 见报告收尾验证。

## 待人工动作、风险与未捕获边界

1. **YouTube 21/56**：只可在 DBeaver 逐条复核并执行
   `docs/sql/disable_unavailable_youtube_channels_20260920.sql`。脚本已有 `lock_timeout`/`statement_timeout`、
   验证与回滚说明；风险是停错仍可恢复的来源，所以范围不得扩大到上一窗 SSLError 的 51 个来源。
2. **远端旧运行体**：人工停止/卸载 MacBook 的 backend/Celery/Watchdog/scheduler；若无法证明全停，轮换
   PostgreSQL/Redis 凭据、隔离 broker namespace，并强制 deployment identity/`application_name`。凭据轮换
   会造成服务中断，必须在维护窗协调，不能由本自动化执行。
3. **数据库网络**：取得同等 891KB payload 的同机房或非 Tailscale A/B，或 client/server socket 分段；
   直接修改 MTU/路由/生产部署有断连风险，本轮不执行。

未捕获边界：没有 `pg_stat_statements`；应用日志只记录 >=1 秒 SQL，页面可由多条亚秒远端往返累加；103 个
端点是本窗自然覆盖，不代表所有路由都有流量；单次 activity 不能证明全窗无瞬时锁；audio 只有一个自然冷建；
YouTube SSLError 和 B站 DNS/TUN 本窗未在故障现场复现；ZSXQ 1059 缺 provider 级分类；后台任务不都具有前台
request id。未捕获均不写成未发生。

## 根因闭环状态表

| 问题指纹/复发键 | 根因 | 已落地修复/防线 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `be891b5c1af362da` / `DB-SESSION-LIFECYCLE` | psycopg2/Tailscale 公共结果接收链路；executor/pooler 已排除 | 有界投影、缓存、single-flight、Session 外文件检查；新增直连 A/B 纪律 | 自然 9.288s；PgBouncer/直连约 9.1s，executor 5–6ms | 是 | 基础设施负责人做 socket 或同机房/非隧道 A/B | 物理修复后自然冷/暖配对，完整后窗无同指纹高尾 | OPEN/REPEATED/WAITING_INFRASTRUCTURE |
| `674016d39deab3f5` / Insights | 当前为同一远端接收高尾；旧全文 overfetch 已替换 | DB 聚合、canonical index、缓存 | SQL 2.018s/HTTP4.170s，executor40.910ms；窗后暖态0.405s | 当前高尾是 | 同 audio 网络动作；保持完整聚合字段 | 修复后自然冷请求与后窗，旧 overfetch 指纹继续 0 | RUNTIME_REPLACED/OPEN/OBSERVING |
| `YOUTUBE-21-56` | 两个来源永久不可枚举 | 受保护 DBeaver 停用脚本 | channel49/51、video errors2，第27窗 | 是 | 人工执行限定范围 SQL | 连续两个自然周期 channel49/49、video errors0 | OPEN/REPEATED/WAITING_MANUAL_CONFIG |
| YouTube all-source SSL | 调度时 TLS/代理/网络断点尚未现场 A/B | 安全 reason、key 脱敏已运行替换 | 上窗0/51×2；本窗恢复49/51与errors2 | 本窗未复发 | 下次同秒 proxy/direct TLS + PID/环境 | 至少后续完整窗无全量失败，故障现场根因修复 | OPEN/OBSERVING/WAITING_NETWORK_TRACE |
| `ZSXQ-PUBLISH-UPLOAD-1059` | 外部上传/发布返回1059，provider 细因未知 | 有界重试、幂等、verify；新增独立 Bug/指标合同 | 最终1/1成功，但3 failure/1 retry/0 pending | 是 | 下次保留阶段/文件/attempt/provider/idempotency | 根因或调用合同修复，真实发布成功，后续完整窗3项均0 | OPEN/REPEATED/OBSERVING |
| `RUNTIME-TOPOLOGY-RELOAD` | 远端旧部署仍共享 DB/Redis/broker 权限且无身份 | durable actor/root 审计、fail-closed、current-root快照 | 23/23 current-root，Publisher16/16；远端会话6 | 风险仍在 | 人工停旧栈或轮换凭据/隔离 namespace | 完整24h remote/old/foreign actor全0，两条自然链成功 | OPEN/REPEATED/WAITING_DISTRIBUTED_ISOLATION |
| B站 `EXTERNAL-CALL-RESILIENCE` | 历史 DNS/TUN 断点未捕获 | 阶段日志与有界执行 | 24/24、168/168，P9514.196s | 本窗未复发 | 下次失败保存 DNS/TUN/HTTP 阶段 | 故障现场根因修复并后续完整窗全绿 | OPEN/OBSERVING |

## 下一轮明确验收计划

1. 固定同一 24 小时边界，优先复核 audio 自然冷建、直连/池化同 payload、route/source/MTU；若仍约 9 秒，
   补 client/server socket 或非 Tailscale A/B，不重复只做 EXPLAIN。
2. 首先检查远端 `100.119.238.48` 会话、durable root/actor 和本地日志；只有完整 24 小时三者都为 0 且
   当前根自然 PDF→ZSXQ、MP3→Publisher 成功，才推进隔离验收。
3. 分别统计 ZSXQ upload failure/publish retry/verify pending；再次 1059 时保存 provider 分类、阶段、文件、
   attempt、请求关联键和幂等结果。
4. 验证 YouTube 21/56 人工 SQL 是否执行；若执行，要求连续两个自然周期 49/49、errors=0。若全来源 SSL
   复发，必须同秒采 proxy/direct TLS 和运行体身份。
5. 继续收集 B站完整自然周期；只有故障现场断点与修复后完整绿窗同时具备，才关闭历史 DNS/TUN。
6. 继续检查 `publication_tasks` dead rows、autovacuum 和索引累计统计；没有真实慢 SQL + reset 窗口 + EXPLAIN
   证据，不生成 CREATE/DROP INDEX。
