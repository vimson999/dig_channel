# 2026-10-24 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 2026-10-23 06:00:00 至 2026-10-24 06:00:00（Asia/Shanghai；数据库 UTC-naive
边界为 2026-10-22 22:00:00 至 2026-10-23 22:00:00）。本轮不能写成“已解决”：PostgreSQL executor、锁、
事务泄漏和 PgBouncer 错误面保持绿色，但应用慢 SQL 累计耗时由 51.023 秒恶化至 68.434 秒；音频资产目录
指纹由 2 次/16.652 秒恶化为 4 次/35.271 秒，仍是 executor 外 driver/fetch/私网尾部。

Publisher 的日志外 writer 证据从 12/30 改善为 2/20，但同一复发键未归零，物理隔离仍是 P0 人工阻塞。
本轮唯一直接落地的根因代码修复是 B站失效充电档位：原链路在完整媒体上传后才得到 21523；现在于数据库
Session 释放后、媒体上传前重新拉取权威档位，失效项进入 `waiting_user_reselect`，不静默替档、不上传媒体、
不重试确定性错误。代码已安全运行替换，但尚无修复后的自然失效样本和完整后窗，故保持 OPEN/OBSERVING。

## 修复前后量化基线

| 指标 | 上一窗口 | 本窗口 | 变化与判断 |
| --- | ---: | ---: | --- |
| 慢 SQL | 26 / 51.023s | 28 / 68.434s | 数量 +7.7%，累计 +34.1%，恶化 |
| 慢 SQL P50 / P95 / max | 1.151 / 8.271 / 8.380s | 1.283 / 8.878 / 9.314s | +11.5% / +7.3% / +11.1% |
| audio catalog 指纹 | 2 / 16.652s | 4 / 35.271s | 次数 +100%，累计 +111.8%，P95 8.878s |
| HTTP | 726 完成，717 成功 | 887 完成，878 成功 | 流量 +22.2%，失败率 1.24%→1.01% |
| HTTP P50 / P95 / max | 2.94ms / 1.618 / 48.920s | 3.04ms / 1.362 / 49.238s | P95 -15.8%，max +0.6% |
| Publisher durable | 15 成功/14 失败/1取消 | 16 成功/4 失败 | 失败率 46.7%→20.0%，改善未闭环 |
| foreign actor | 12/30 | 2/20 | 40.0%→10.0%，仍自然复发 |
| PgBouncer wait P95 / query P95 | 52.05 / 80.96ms | 54.29 / 82.38ms | +4.3% / +1.8%，八类错误仍为 0 |

## 按业务影响排序的主要问题

| 排名 | 问题/复发键 | 累计/P95/max 或失败率 | 影响 | 本轮状态 |
| ---: | --- | --- | --- | --- |
| 1 | `be891b5c1af362da` audio catalog | 35.271s；P95 8.878s；max 9.314s | audio-assets 10/10 请求，HTTP P95/max 10.961s | OPEN/REPEATED/OBSERVING |
| 2 | 日志外 writer / `foreign_actor_evidence` | 2/20 Publisher；两任务 453.222s；总体失败率 20% | 两个 MP3→Publisher 子任务被错误终结 | OPEN/REPEATED/WAITING_DISTRIBUTED_ISOLATION |
| 3 | `BILIBILI-CHUNK-RETRY-EXHAUSTED` | 1 次失败，253.799s，第 42/69 分片 4 次失败 | 一次上传失败，用户需重新投稿 | OPEN/REPEATED/OBSERVING |
| 4 | `BILIBILI-STALE-UPOWER-TIER` | 1 次失败，127.235s；21523 | 完整上传后拒绝，浪费媒体传输 | RUNTIME_REPLACED/OBSERVING |
| 5 | YouTube `id=21/56` | channel 2/51 失败；video errors=2；第 21 窗 | 每日固定失败与通知噪声 | OPEN/REPEATED/WAITING_MANUAL_CONFIG |

## 页面/API、后台任务与 SQL 关联

### SQL、Session 与连接

- 28 条慢 SQL 累计 68.434 秒；`publication_tasks` 14 条、`zsxq_topics` 2 条、`zsxq_attachments` 1 条。
  首位 audio catalog 4 条累计 35.271 秒，占全部慢 SQL 时间 51.5%。4 个匹配 Session hold 的
  P50/P95/max 为 8.944/9.375/9.375 秒；SQL 占 35.271 秒，非 SQL 仅 270ms，排除 Session 跨文件 I/O。
- 后续指纹包括 workflow notification due drains 3 条/3.645 秒、credential resolve 2 条/3.322 秒、
  canonical cleaned outputs 2 条/3.121 秒、MP3 operation 2 条/2.814 秒、job load 2 条/2.754 秒、Publisher
  child cleanup 2 条/2.601 秒、report-audio recovery 2 条/2.263 秒、Insights aggregate 1 条/1.912 秒。
- SQL error、statement timeout、lock timeout、connection timeout、deadlock、持续 idle-in-transaction 均为 0。
  runtime guard 25 个短观测，最大连接 2、事务龄 0.338 秒、idle 0.303 秒，没有 pressure defer。
- PgBouncer 1,440 个样本、288 次建连（12/h），来源均为 `100.96.3.12`；八类错误与 proxy fallback 为 0。
  `wait` P50/P95/max 30.38/54.29/184.19ms，`query` 66.17/82.38/478.78ms。错误为 0 不代表远程
  fetch 尾部已消失。

### 页面与 API

固定窗 887 个完成请求、117 个端点、0 个不完整请求；878 成功、9 个失败均为 favicon/静态资源 404，
没有业务 5xx。累计 324.483 秒，P50/P95/max 为 3.04ms/1.362s/49.238s。

- `GET /api/mp3-video-workflows/audio-assets` 10/10 成功，累计 39.786 秒，P95/max 10.961 秒；匹配
  5 条慢 SQL/36.529 秒，其中 4 条为主 catalog 指纹。页面尾延迟与远程 fetch 一一对应。
- jobs detail 29 次累计 20.069 秒；uploads 46 次累计 18.460 秒；上传创建 10 次累计 15.906 秒。它们没有
  N+1、超大分页或大字段爆炸的新证据。
- 低频高延迟且无匹配慢 SQL：`POST /api/v1/videos/sync` 49.238 秒、
  `POST /api/v1/channels/sync/all` 20.040 秒、content web-submit 2 次累计 15.457 秒/P95 8.756 秒。
  这些属于外部 API、文件或编排等待，不是 PostgreSQL executor 慢。
- 重点 ZSXQ 固定窗只有 overview 1 个自然样本（3.619 秒，其中 Insights SQL 1.912 秒），其余入口缺足够
  自然流量，按“未捕获”而不是“无问题”报告。重启后只读 E2E：health 200/0.040s、overview
  200/0.037s、topics/local 200/0.789s、tabs 200/0.301s、sync/runs 200/0.414s、scheduler status
  200/0.369s；只证明当前可用性。

### 后台任务与无前台端点异常

- Celery 日志识别 169 completed、0 retry、2 failed。Publisher pipeline 10 成功累计 1,410.675 秒，
  P95/max 245.236 秒；upload-only 8 成功/2 失败，累计 1,146.402 秒，P95/max 250.573 秒。
- durable Publisher 口径更完整：20 条中 16 completed、4 failed，累计 4,347.450 秒，P50/P95/max
  218.780/318.940/338.092 秒。两条 `required_material_unavailable` 缺 actor 且素材在当前节点存在；本地日志
  没有失败写入，是本轮“后台任务很久但无前台端点”的最高优先级异常。
- 另外两条失败分别为 B站分片 4 次失败和失效档位 21523。同父视频在用户重选当前档位后成功，返回
  `aid=117489484700356`、`bvid=BV16Dnt6eE46`，证明旧档位是独立确定性根因。
- ZSXQ publish 2/2 完成：PDF 40/40、音频 23/23，共 63/63 verified、8 个 topics。过程中有有界 1059 与
  一次 13407 配额切换，但最终无失败/重复投递，保持 OBSERVING，不能把中间错误删除或伪装成未发生。
- B站情报 24/24 自然周期、168/168 competitor 成功，累计 329.944 秒，P50/P95/max
  13.399/16.122/17.000 秒。YouTube 自然 channel 49/51、video errors=2，仍为固定 21/56。

## 根因修复与验证链

### B站失效充电档位

修复前证据：durable 任务携旧 `upower_level_id=1424170587911438656`，当前权威接口只返回
`1357406026810593731`、`1381310463006055021`、`1431031586035418859`；旧 ID 完成媒体上传后才被 21523
拒绝。调用链为 `run_bilibili_upload_only` → `BiliUploader.upload_single` → `_charging_pay_fields`，后者直接
复用 durable ID，没有执行前复核。

已落地：`BiliUploader.validate_charging_pay_selection()` 拉取权威集合；worker 在
`db.session.remove()` 后、构造媒体上传协程前调用。失效时写 `failed/waiting_user_reselect`、
`bilibili_error_code=stale_upower_level`、`bilibili_reselect_required=true`，不调用 `upload_single`。

验证链：先观察新增测试因“未抛错、仍上传”而 RED；实现后 3 项焦点与 98 项相关测试通过；完整 unit
1,741/1,741 通过；compileall、双 AST 边界、Bug strict、JSON 与 diff-check 通过。安全门两次均确认 46 条近期
任务、0 活动 sync 中无不可中断任务，标准重启到 backend PID 80782；bootstrap 17/17 worker、Watchdog、
6/6 scheduler 全部 `ok=true`、`errors=[]`，platform_upload PID 80869、Celery status 17/17 running 且
conflict=0。状态仅 **RUNTIME_REPLACED/OBSERVING**：没有制造真实外部投稿来验收，下一完整自然窗必须复核。

### 其他重复根因

- audio catalog：一次有界投影、Session 外文件校验和缓存合同仍有效，但 4 次远程 fetch 尾部恶化；当前缺
  pool acquire、fetch bytes/deserialize 与 socket/route 分段，不能用延长 TTL 或索引掩盖。
- 日志外 writer：2/20 是改善，不是闭环。代码审计无法隔离共享 PostgreSQL/Redis 凭据的旧 actor；需人工停旧
  Mac 全栈，或轮换凭据并隔离 broker namespace。
- B站分片：既有 4 次上限和总时限已阻止无限占队列；缺幂等续传/重投证明，不能盲目提高 retry 或自动重投。
- YouTube：固定两个远端 0-result 来源第 21 窗复发；观测已充分，根治仍是人工执行受保护停用脚本。

## 表规模、索引与 DDL 判断

| 表 | 总大小 | live/dead | idx scan share | 结论 |
| --- | ---: | ---: | ---: | --- |
| `publication_tasks` | 152MB（table 18MB/toast 120MB/index 14MB） | 14,717 / 556 | 99.83% | 大 JSON/toast 是负载风险，但代表点查 executor 0.044ms |
| `zsxq_topics` | 133MB | 13,974 / 1,644 | 99.52% | Insights 顺扫/window executor 40.869ms，应用秒级主要不在 executor |
| `zsxq_topic_comments` | 119MB | 45,853 / 5,914 | 99.85% | 无本窗慢 SQL 热点 |
| `zsxq_attachments` | 79MB | 35,981 / 72 | 99.94% | 低使用索引需更长统计窗，不立即删 |
| `zsxq_sync_runs` | 37MB | 1,076 / 60 | 99.90% | 代表顺扫 1.579ms |
| `external_documents` | 7.0MB | 1,817 / 146 | 98.74% | 无热查询证据 |

没有 exact duplicate index。低使用候选包括 attachments PDF group-tab 约 5MB（scan=1）和 topics group/type
约 3.8MB（scan=1），但统计自 8 月累积，缺 reset-window 写放大和反事实计划证据；不生成 DROP DDL。
代表 EXPLAIN：publication PK 0.044ms、chart 3.032ms、all-tabs 0.026ms、publisher list 0.059ms、content list
0.218ms、recent wide/narrow 0.032/0.034ms、sync runs 1.579ms、Insights 40.869ms。无 waiting lock，
`pg_stat_statements` 未安装。**本轮没有 CREATE/DROP INDEX、ALTER、回填或生产配置变更。**

## 待人工 SQL、风险与阻塞

- 仅保留既有 [`disable_unavailable_youtube_channels_20260920.sql`](../../sql/disable_unavailable_youtube_channels_20260920.sql)
  供 DBeaver 人工逐段复核执行。风险是误停仍可恢复来源，所以只允许处理已连续 21 窗固定失败的 21/56；执行后
  要求两个自然周期 channel 49/49、video errors=0。
- 低使用索引没有足够证据，不生成删除脚本。若后续要整改，必须先固定 `pg_stat_*` reset 时间、写速率、真实 SQL
  与 `EXPLAIN (ANALYZE, BUFFERS)`，再提供逐条 lock/statement timeout、验证和回滚。
- 旧运行时物理隔离不是 SQL 索引问题；责任动作是停旧 backend/Celery/Watchdog/scheduler/脚本，无法证明时轮换
  DB/Redis 凭据与 broker namespace。

## 未捕获边界

- 固定窗不足以覆盖所有 117 个端点的自然业务，重点 ZSXQ 多入口只有窗后 E2E；测试通过不替代自然流量。
- `pg_stat_statements` 不可用，无法用服务端累计指纹交叉验证低于 1 秒阈值的 SQL；当前按应用结构化日志、
  pg_stat 表/索引与代表 EXPLAIN 三角验证。
- 06:02/06:03 两次只读诊断因引号和不存在字段失败，发生在固定窗外且无数据变更，已从窗口统计排除。
- 没有为验收而触发真实外部 B站投稿；新档位校验只有代码、测试、运行替换与当前 options 证据，必须等自然任务。
- “未匹配慢 SQL”只说明未达到当前日志阈值/关联窗口，不代表无数据库开销；下一轮继续按 request id 与 origin 对账。

## 根因闭环状态表与下一轮验收

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `be891b5c1af362da` | executor 外 driver/fetch/私网尾部 | 既有 O(1) 投影、single-flight、Session 外文件校验 | 4 次/35.271s，executor 毫秒级 | 是，恶化 | 补 pool/fetch/socket 分段；应用维护者 | 冷/暖请求 SQL 数、bytes、分段与同秒网络证据 |
| foreign actor | 共享凭据旧 writer 日志外终结任务 | actor/mtime/全 feature 日快照门禁 | 2/20，old-root=0 但本地无失败写入 | 是，改善未归零 | 人工停旧栈或轮换 DB/Redis、隔离 broker | 完整 24h old/foreign/remote actor 全 0 + 自然任务成功 |
| stale upower tier | durable 外部 ID 执行前不复核 | Session 外预校验 + 可重选终态 | 17/17 worker running；platform PID 80869 | 修复前复发；修复后未捕获 | 等自然失效/重选，不制造外部投稿 | 失效项媒体上传=0、明确可重选、自然成功、后窗 21523=0 |
| chunk retry exhausted | 外部媒体分片连续失败，缺幂等恢复 | 既有 4 次上限/总时限；新增 Bug 门禁 | 1 次 253.799s 后终止，后续人工重投成功 | 是 | 捕获目标/响应/会话；证明幂等后再自动化 | 无重复稿件的安全恢复 + 后窗无同形失败 |
| YouTube 21/56 | 永久不可用来源仍启用 | 观测与受保护人工 SQL | channel 49/51、video errors=2 | 是，第 21 窗 | 人工 DBeaver 停用两源 | 连续两自然周期 49/49、errors=0 |
| ZSXQ 1059/13407 | 外部配额/并发限制 | 有界轮次与账号回退 | 63/63 verified、2/2 durable completed | 中间错误出现，未成最终失败 | 继续保留中间错误与次数 | 下一自然批次无无界重投、无重复 topic/附件 |

下一轮必须优先读取本报告和复发登记表，固定 24 小时后窗复核：新 B站预校验是否有自然样本、21523 是否归零；
audio catalog 分段是否补齐且 P95 是否改善；foreign actor 是否真正归零；YouTube 人工脚本是否执行并通过双周期；
B站分片失败是否再次出现且能否取得网络分类。上述验收未完成前，各项不得写成“已解决”。
