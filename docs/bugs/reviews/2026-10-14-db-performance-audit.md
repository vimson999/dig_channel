# 2026-10-14 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 `2026-10-13 06:00:00`–`2026-10-14 06:00:00`（Asia/Shanghai）。本轮不能写成“已解决”：

- 本机于 10 月 13 日 09:12 启动，backend 于 09:25:55 启动，日志覆盖约 20 小时 34 分，而非完整 24 小时。
- 应用慢 SQL 从上一短窗 6 条/14.357 秒反弹到 **147 条/405.959 秒**，P50/P95/max 为
  **1.560/8.622/18.020 秒**。相比上一窗数量 +2350.0%、累计 +2727.6%、P95 +21.2%、max +153.3%；
  即使按覆盖时长归一仍明显恶化。
- 代表查询在 PostgreSQL 的 `EXPLAIN (ANALYZE, BUFFERS)` 只有 0.024–47.138ms；现场无 waiting lock、
  无 idle-in-transaction。PgBouncer 有 4 个 query timeout 和 1 个 query-wait timeout，分别命中
  Publisher 主键、audio-clean 主键、HTTP 通知去重、notification delivery 与 due-provider 查询。
  共同根因仍是 executor 外的 Tailscale/PgBouncer/网络传输尾延迟，不是缺索引。
- 旧运行时问题显著改善：本窗五类 durable task 的 old-root 为 0，当前快照也不再有旧 MacBook Pro
  数据库会话；但窗口不足 24 小时，也没有完成凭据轮换或 broker namespace 物理隔离，故只到
  **OBSERVING/REMOTE_SESSIONS_ZERO_OBSERVED**，不能 CLOSED。
- 全站最慢的两个 API 是 YouTube 手工同步：`channels/sync/all` 最大 190.591 秒，`videos/sync`
  最大 66.887 秒，均无匹配慢 SQL；日志明确是 Google API SSL EOF/30 秒读超时。B站情报调度器又出现
  3 个整点周期 0/7、全部 DNS 解析失败。它们是外部 I/O 根因，不应通过索引、DB 重试或放宽超时掩盖。
- 本轮没有发现兼容现有业务、可在仓库内根治的新代码缺陷；未做生产代码、配置或 DDL 变更。报告、Bug 与
  recurrence registry 已更新，所有根因继续按 OPEN/OBSERVING 管理。

## 与基线的量化对比

| 指标 | 10-13 上一窗 | 本窗 | 变化/判定 |
| --- | ---: | ---: | --- |
| 应用日志覆盖 | 约 2h38m | 约 20h34m | 改善，但仍缺约 3h26m |
| 慢 SQL | 6 | 147 | +2350.0%，显著反弹 |
| 慢 SQL 累计 | 14.357s | 405.959s | +2727.6% |
| P50 / P95 / max | 1.485 / 7.114 / 7.114s | 1.560 / 8.622 / 18.020s | +5.0% / +21.2% / +153.3% |
| 完整 HTTP | 182 | 1,051 | 1,039 成功/12 失败；12 个失败均为 favicon 或错误方法的巡检探针 |
| HTTP P50/P95/max | 4.65ms / 947.31ms / 7.473s | 4.19ms / 1.510s / 190.591s | P95/max 恶化，最大来自外部 YouTube API |
| PgBouncer query / query-wait timeout | 5 / 5 | 4 / 1 | 关机风暴消失，但业务窗口仍真实复发 |
| Publisher old-root | 25/25 | 0/17 | 明显改善；仅 20.55h，仍待完整窗/物理隔离 |
| Publisher 失败 | 9/25 | 1/17 | 降至 5.9%；当前根缺少视频文件，另行观察 |

最近一个完整有效基线 09-29 为 47 条/68.197 秒、P50/P95/max 1.101/2.531/6.851 秒。本窗相对该基线
数量 +212.8%、累计 +495.3%、P95 +240.6%、max +163.0%，因此不能用“timeout 计数下降”描述为整体改善。

## 主要问题排序

排序综合累计耗时、P95、最大耗时、失败率、受影响页面/任务与复发次数。

| 排名 | 问题 | 量化影响 | 根因与状态 |
| --- | --- | --- | --- |
| 1 | 内容后台长工作流 | PDF clean 5 条累计 12,233.322s、P95 4,513.879s；ZSXQ publish 8 条累计 10,044.700s、P95 2,601.775s；MP3 4 条累计 3,600.898s | durable 壁钟包含外部 I/O/排队；无 old-root，但未捕获细分阶段，OPEN/OBSERVING |
| 2 | 数据库链路尾延迟与 timeout | 147 条累计 405.959s、P95 8.622s、max 18.020s；4 query timeout + 1 query-wait | executor 0.024–47.138ms，无锁/idle tx；Tailscale/PgBouncer/网络边界 OPEN/REPEATED |
| 3 | YouTube 同步外部 I/O | 4 个请求累计 337.567s；max 190.591s；任务仍返回 200 | Google API SSL EOF/30s read timeout，无慢 SQL/DB Session 交叉持有，OPEN |
| 4 | 内容提交/历史页面 | web-submit 3 次累计 64.230s、max 48.844s；web-submissions 28 次累计 50.879s、max 29.999s | request `b0f9642a` 命中通知去重 query timeout；`771700fb` 三条 SQL 合计 25.259s，OPEN/REPEATED |
| 5 | MP3 音频目录冷缓存 | 4 个冷请求 SQL 累计 32.611s、max 9.863s；7 个缓存命中约 5ms | 单次有界查询/缓存已生效，executor 4.564ms；剩余为网络/fetch 尾延迟，OPEN/OBSERVING |
| 6 | B站情报 DNS 失败风暴 | 03:32–05:32 三个自然周期均 0/7，21 次请求失败 | `space.bilibili.com` DNS `nodename nor servname`，非 DB 根因，OPEN/REPEATED |

## SQL、页面/API 与后台任务关联

### SQL、Session 与 PgBouncer

| 指纹/来源 | 次数 | 累计 / P95 / max | 关联与判定 |
| --- | ---: | ---: | --- |
| `c1e57cc097d3e051` notification due-provider | 13 | 39.267 / 4.709 / 16.492s | Watchdog/通知扫描；1 次 query-wait，executor 毫秒级 |
| `be891b5c1af362da` MP3 audio catalog | 4 | 32.611 / 9.863 / 9.863s | 4 个冷缓存请求；executor 4.564ms，7 个热缓存请求约 5ms |
| `6f60d95efb7f7eb3` ZSXQ publish task load | 7 | 29.590 / 8.622 / 8.622s | 后台任务主键/窄读，executor 非瓶颈 |
| `9583c...` notification delivery update lookup | 9 | 26.482 / 8.413 / 8.413s | 通知 worker；13:18 query timeout |
| `050372...` provider state | 6 | 23.455 / 17.154 / 17.154s | 通知 provider 状态；长尾不是表扫描 |
| `1117...` notification enqueue lookup | 2 | 21.111 / 18.020 / 18.020s | request `b0f9642a` 的 web-submit，13:04 query timeout |
| `f5ee...` Publisher cleanup scan | 11 | 19.316 / 3.158 / 3.158s | Watchdog；既有轻投影，代表 executor 0.024ms |
| `c014...` Publisher pending scan | 3 | 15.511 / 12.998 / 12.998s | Watchdog；现有索引命中，无 DDL 依据 |

39 个 connection hold 的 P50/P95/max 为 9.727/21.720/25.317 秒；worker/scheduler/web 为 24/8/7。
SQL 合计 462.194 秒，非 SQL 仅 18.297 秒，37/39 可匹配慢 SQL。两条无匹配慢 SQL 的 hold 分别是
`repair_stale_publication_tasks_once` 8.076 秒（SQL 1.116、尾部 6.960 秒）和 Publisher cleanup
5.995 秒（SQL 1.279、尾部 4.716 秒）；代码已在 broker/外部 I/O 前释放 Session，现场更符合
rollback/checkin 网络尾部，不能据此臆断跨外部 I/O 持有。

PgBouncer 原始 timeout 12 行去重为 4 个 query timeout，query-wait 2 行去重为 1 个 episode；没有
connect timeout、server crash、TLS 或 HBA 错误。252 个 server connection 全部来自 `100.96.3.12`，
没有代理 TUN source。wait P50/P95/max 28.60/55.29/565.51ms，query 为 65.68/117.26/1595.02ms。

### 页面/API

完整请求 1,051 个、120 个端点，1,039 成功/12 失败，累计 734.345 秒，P50/P95/max
4.19ms/1.510s/190.591s。12 个失败是 9 个 favicon 404 与 3 个对 POST-only ZSXQ 接口的错误 GET
巡检探针，属于自污染，不是自然产品失败。

- 低频高延迟：`POST /api/v1/channels/sync/all` 2 次 22.034/190.591 秒；
  `POST /api/v1/videos/sync` 2 次 58.056/66.887 秒。均没有匹配慢 SQL，日志为 Google API SSL EOF/读超时。
- 页面慢且有慢 SQL：content web-submit 3 次 P95/max 48.844 秒；history 28 次 P95 6.400 秒、max
  29.999 秒。最慢 history request `771700fb` 的 3 条 SQL 为 11.354+6.811+7.094 秒。
- 页面慢但无慢 SQL：charging dashboard 18 次 P95 1.743 秒、data/table 18 次 P95 1.511 秒、
  chart-data 18 次 P95 1.402 秒、rankings 18 次 P95 1.173 秒；应继续按亚秒 SQL/序列化/缓存拆分。
- MP3 audio-assets 11 次累计 36.224 秒，4 次 cache miss 匹配 32.611 秒 SQL，7 次 cache hit 约 5ms，
  说明缓存有效但冷链网络尾延迟仍复发。
- ZSXQ 重点链路只有巡检样本：overview 2 次、topics/local POST 1 次、tabs POST 1 次、sync status
  1 次，均成功但不足以证明自然业务不复发。

### 后台任务与无前台端点异常

Celery 日志有 275 completed、13 retry、1 failed，成功任务累计 5,391.391 秒。Publisher pipeline 8 条
P95/max 301.544 秒；B站 upload-only 9 条 P95/max 336.553 秒；ZSXQ publish 6 条成功、12 retry，
max 544.513 秒；workflow notification 199 成功/1 失败，P95 37.418 秒；MP3 workflow 48 条
P95 48.935 秒；audio clean 3 条成功/1 retry，P95 157.284 秒。

durable 记录补足了无本地日志/无前台端点的壁钟：PDF clean 5、ZSXQ publish 8、Publisher 17、MP3 4、
audio clean 3。所有可分类记录 old-root=0；Publisher 16 completed/1 failed，唯一失败为当前根找不到已生成视频。
通知 outbox 105 sent、110 attempts、max attempts=5，说明通知链确有重试放大，仍需结合下一窗确认 4 个
timeout 指纹是否归零。

YouTube 自然周期继续复发已知配置问题：channel sync 51 个中 45 成功/6 失败；video sync 49 个来源、
errors=2，其中 `id=21/56` 仍是固定失败源。额外频道失败与 Google API SSL EOF 同窗，不能和两个永久不可用源
混成单一根因。

## 表、索引与查询形态复核

| 表 | heap / TOAST / index / total | live/dead | seq/index scan | 判定 |
| --- | --- | ---: | ---: | --- |
| `publication_tasks` | 18/115/14/146MB | 14,412/1,109 | 7,158/4,005,529 | index 99.82%；HOT 1.18%，大 JSON 写放大仍需投影纪律 |
| `zsxq_topics` | 13/79/36/129MB | 12,637/1,584 | 4,270/892,653 | index 99.52%；宽 2,500 行查询 executor 47.138ms |
| `zsxq_topic_comments` | 91/4.2/23/118MB | 41,333/5,644 | 529/359,308 | index 99.85%；HOT 70.46% |
| `zsxq_attachments` | 35/0.1/42/78MB | 33,906/37 | 647/1,135,940 | index 99.94% |
| `zsxq_sync_runs` | 1.2/35/0.9/37MB | 1,062/46 | 310/337,994 | index 99.91% |
| `external_documents` | 0.9/4.8/1.2/6.9MB | 1,817/146 | 1,079/84,997 | index 98.75% |

精确定义重复索引为 0。低扫描候选仍包括 attachments PDF group/tab 5,088KB/1、topics group/type/create
3,720KB/1、external TTS 232KB/0、summary 184KB/0、Publisher MP3 match 184KB/0。统计未给出 reset 时间，
且这些索引可能承载低频恢复/唯一性合同；没有 owner、写放大 A/B 与完整自然查询证据，不生成 DROP INDEX 脚本。

代表 EXPLAIN：notification dedupe 4.695ms、notification PK 1.772ms、provider state 1.265ms、audio catalog
4.564ms（3,948 扫描/1,260 返回、1.2MB sort）、Publisher pending 0.024ms、publication PK wide 0.048ms、
Insights topics broad 47.138ms。角色设置仍为 statement timeout 60s、lock timeout 5s、idle-in-transaction
60s。无 CREATE/DROP INDEX、ALTER、回填或生产配置 SQL，也无可合理提交的 DBeaver DDL。

## 已落地变更、测试与运行态验证

- 本轮没有生产代码/配置变更；原因是所有主要 SQL 的 executor 已证实为毫秒级，外部 API/DNS 与物理网络
  根因不应以索引、超时、重试或应用假数据处理。
- 已更新本报告、四条既有 Bug 记录、Bug README 与 recurrence registry，保留问题复发键和下一轮验收条件。
- `/health` 当前 healthy；boot runtime dependency gate 对 Tailscale、私网 route、PostgreSQL 与 Redis 全部通过。
  backend PID 10371 自 10 月 13 日 09:25:55 运行。LaunchAgent 实际仍未安装，真实 reboot 自动恢复保持
  `WAITING_MANUAL_INSTALL`。
- 无需加载新代码，因此没有重启服务，也没有触发 restart safety gate。真实运行栈已经产生本窗 1,051 个 HTTP、
  275 个 Celery 完成样本和数据库/PgBouncer 现场证据。
- 巡检器定向回归 **34 passed、2 subtests passed**；完整 unit/integration 在允许本机测试端口的标准环境中
  **1817 passed、7 skipped、363 subtests passed**，用时 118.95 秒。受限沙箱首跑的 24 个失败全部是
  `127.0.0.1` 临时端口 bind 被拒，授权环境同命令全绿；不是业务断言失败。Bug strict 209/209、
  `compileall`、Remotion TypeScript、registry JSON 和 `git diff --check` 均通过。
- 一次只读诊断最初错误引用不存在的 `pg_stat_user_tables.stats_reset`，事务自动回滚；随后改用合法视图完成
  只读复核。该诊断自错未修改生产数据，不计作产品 SQL 错误。

## 未捕获边界与下一轮明确验收计划

- 缺失开机前约 3h26m 应用日志；未捕获不等于未发生。关键 ZSXQ 页面仅有烟测样本，不能判不复发。
- 慢 SQL 阈值不会捕获多条亚秒 SQL、acquire/fetch/serialize、DNS、外部 API 或缓存阶段；因此单列了
  低频高延迟、页面慢无慢 SQL、后台长任务三类异常。
- 下一窗必须从 10 月 14 日 06:00 起覆盖完整 24 小时：query/query-wait/connect timeout、server crash、
  SQL error、idle-in-transaction 均为 0，且慢 SQL P95/max 回落；否则继续保存同秒 pool/socket/route/activity。
- old-root 与远端空 application_name 会话必须继续为 0，并各获得至少一条 PDF、ZSXQ publish、MP3→Publisher
  当前根自然成功；未完成凭据/namespace 物理隔离前保持 OPEN/OBSERVING。
- `channels.id=21/56` 仍需人工复核并执行受保护停用 SQL；随后要求 channel 49/49、video errors=0 连续两周期。
- B站情报下一次失败须同秒保存 DNS 结果、系统代理/TUN、实际连接目标与 HTTP 分类；成功则要求完整 24 小时
  自然周期无 0/7 风暴。
- LaunchAgent 需人工审阅安装，并用一次真实 reboot 证明 Tailscale 未 ready 时 fail closed、ready 后单实例恢复。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `query timeout + executor<50ms` | Tailscale/PgBouncer/网络传输尾延迟 | 既有 Session 纪律、分段 telemetry、query timeout 保护 | 147 条/405.959s；4 query +1 wait timeout；无锁/idle tx | 是，恶化 | **OPEN/REPEATED**；不得加索引或放宽 timeout | 完整 24h 六类错误 0、P95/max 回落并保留峰时四源证据 |
| `old-root + foreign DB client` | 旧 MacBook Pro 共享 DB/broker 凭据 | 素材 fail-closed、root 归属快照、boot guard | 本窗 old-root=0、旧源会话=0 | 本窗未复发，但窗口不足 | **OBSERVING/WAITING_DISTRIBUTED_ISOLATION** | 完整窗为 0，三类当前根自然成功；物理停旧栈或轮换凭据/namespace |
| `YouTube manual sync >60s` | Google API SSL EOF/读超时 | 既有分频道隔离与失败详情 | 4 请求累计337.567s，max190.591s，无慢 SQL | 是 | **OPEN**；按频道分段，禁止跨 DB Session | 外部失败分类归零，端到端 P95 回落且无 DB hold |
| `content history 771700fb / submit b0f9642a` | 多条 DB 网络尾延迟；通知去重 query timeout | 批量历史查询、request→SQL 关联已存在 | history max29.999s；submit max48.844s | 是 | **OPEN/OBSERVING** | 两端点自然样本各≥5，query timeout 0、P95 回落 |
| `be891b5c1af362da` | 冷缓存查询的 driver/fetch/网络尾延迟 | 有界投影、Session 释放、TTL cache | 冷4条32.611s；热7次约5ms；executor4.564ms | 是 | **OPEN/OBSERVING** | 冷/热分栏，冷请求 P95 回落，暖缓存 SQL=0 |
| `BILI-INTEL space.bilibili.com DNS` | DNS/代理/TUN 外部链路不可用 | 逐 competitor 隔离与独立日志 | 三周期均0/7，21次DNS失败，无DB hold | 是，明显反复 | **OPEN/WAITING_NETWORK_TRACE** | 同秒网络四源证据；完整窗所有自然周期成功 |
| `YouTube id=21/56` | 永久不可用频道仍启用 | 受保护停用 SQL 已生成、尚未执行 | channel 45/51，video errors=2；固定源继续出现 | 是，第11窗 | **OPEN/WAITING_MANUAL_CONFIG** | 人工停用后 channel49/49、video errors0 连续两周期 |
| `boot guard LaunchAgent` | 启动编排尚未安装 | 协议门禁、启动脚本与 plist 模板已代码验证 | 当前门禁全通过；LaunchAgent=missing | 未经历新 reboot | **WAITING_MANUAL_INSTALL/OBSERVING** | 人工安装后真实 reboot fail-closed→单实例全栈 ready |
