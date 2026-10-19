# 2026-10-15 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 `2026-10-14 06:00:00`–`2026-10-15 06:00:00`（Asia/Shanghai），覆盖完整 24 小时。
本轮不能写成“已解决”：

- 应用慢 SQL 从 147 条/405.959 秒改善到 **22 条/53.426 秒**，P50/P95/max 从
  1.560/8.622/18.020 秒降至 **1.312/7.374/7.602 秒**；数量、累计耗时分别下降 85.0%/86.8%。
  PgBouncer 六类错误、应用 SQL error、statement/lock timeout、deadlock 与持久 idle-in-transaction 均为 0。
  这是完整绿窗，但上一窗故障物理断点尚未根治，状态只到 `OBSERVING`。
- 最慢 SQL 仍是 MP3 音频目录冷缓存：4 次累计 29.444 秒、P95/max 7.602 秒；同形生产
  `EXPLAIN (ANALYZE, BUFFERS)` 仅 **4.691ms**。应用耗时为 executor 的约 1,524–1,621 倍，
  根因继续位于 PgBouncer/私网/驱动 fetch 边界，不是 PostgreSQL executor 或缺索引。
- 旧 MacBook Pro 运行时全面复发：当前 `100.117.177.98` 有 6 个空 `application_name` 会话，且 2/2
  `audio_transcription` durable task 携旧 checkout，当前日志集合中找不到 task id。一次断会只会重连，
  必须停远端旧栈，或轮换 PostgreSQL/Redis 凭据并隔离 broker namespace；状态为
  **OPEN/REPEATED/WAITING_DISTRIBUTED_ISOLATION**。
- B站情报调度器固定窗 **24/24 周期全部失败、168/168 competitor 请求 DNS 失败**，较上一窗 3 个失败周期
  明显恶化。06:06 窗后代理和直连均恢复 200，只能证明当前恢复，不能补齐故障时 DNS/TUN 断点。
- YouTube 手工同步的 3 个低频请求累计 169.665 秒，最大 89.596 秒；均无匹配慢 SQL，日志明确为
  Google API 30 秒 read timeout。固定不可用 `channels.id=21/56` 已连续第 12 个巡检窗口复发；受保护停用
  SQL 仍待人工执行。
- durable 后台任务暴露三类不同问题：ZSXQ 发布 3 条原始壁钟接近 4.1 小时，但 97.3% 是失败终态到人工/
  自动续跑之间的等待；PDF clean 原始 3,794.788 秒中约 2,521.048 秒是单 worker 队列等待；Publisher
  4/24 失败（16.7%），其中 3 条被缺素材 fail-closed 正确阻断、1 条缺视频。均未达到关闭标准。
- 本轮没有发现可以由仓库内兼容变更根治的新增代码缺陷，未改生产代码、未重启、未做 DDL；已更新报告、
  Bug 与 recurrence registry。所有主要根因保持 OPEN/OBSERVING，而非以一次绿窗或当前网络恢复冒充闭环。

## 与上一轮量化对比

| 指标 | 10-14 | 本窗 | 变化/判定 |
| --- | ---: | ---: | --- |
| 日志覆盖 | 约 20h34m | 24h | 覆盖完整 |
| 慢 SQL | 147 | 22 | -85.0%，改善 |
| 慢 SQL 累计 | 405.959s | 53.426s | -86.8% |
| P50 / P95 / max | 1.560 / 8.622 / 18.020s | 1.312 / 7.374 / 7.602s | -15.9% / -14.5% / -57.8% |
| HTTP 请求 / 失败 | 1,051 / 12 | 1,179 / 6 | 请求 +12.2%；6 个失败均 favicon 404 |
| HTTP 累计 / P95 / max | 734.345s / 1.510s / 190.591s | 524.169s / 1.288s / 89.596s | -28.6% / -14.7% / -53.0% |
| PgBouncer query/query-wait | 4 / 1 | 0 / 0 | 完整绿窗，保持 OBSERVING |
| 通知 sent / attempts / max attempt | 105 / 110 / 5 | 112 / 112 / 1 | 重试放大本窗未复发 |
| 旧根任务 / 远端会话 | 0 / 0（观察） | audio transcription 2/2 / 6 | **复发并恶化** |
| B站情报失败周期 | 3 个 0/7 | 24 个 0/7 | 168 次 DNS 失败，显著恶化 |

## 主要问题排序

按累计耗时、P95、最大耗时、失败率、用户/页面影响与复发次数综合排序：

| 排名 | 问题 | 量化影响 | 根因与状态 |
| --- | --- | --- | --- |
| 1 | 旧 MacBook Pro 分布式运行时 | 2/2 audio transcription 为 old-root；当前 6 个生产会话 | 共享 DB/Redis/broker 凭据，物理隔离未完成；OPEN/REPEATED |
| 2 | ZSXQ 发布失败后长等待续跑 | 6 条最终完成，原始壁钟 44,932.456s，P95 14,898.719s | 外部账号配额/401/1059 后约 4h 再续跑；97.3% 非活跃执行，OPEN |
| 3 | PDF clean 单 worker 排队 | 5 条最终完成，原始壁钟 3,794.788s；活跃执行 1,273.740s | 队列可见且串行，约 66.4% 为队列等待；OBSERVING |
| 4 | B站情报 DNS 风暴 | 24/24 周期失败，168/168 请求失败 | DNS/代理/TUN 外部链路；故障同秒证据缺失，OPEN/REPEATED |
| 5 | Publisher 失败 | 24 条中 4 failed，失败率 16.7%；失败壁钟 960.035s | 3 条缺素材 fail-closed，1 条缺视频；上游素材局部性未闭环 |
| 6 | YouTube 同步外部长尾 | 3 请求累计 169.665s，P95/max 89.596s | Google API 30s read timeout；无慢 SQL/Session 交叉持有 |
| 7 | MP3 音频目录冷链 | 4 个冷请求 SQL 29.444s，P95/max 7.602s；另 5 次热命中快速 | executor 4.691ms；驱动/私网/fetch 尾延迟，OPEN/OBSERVING |

## SQL、Session、PgBouncer 与页面关联

### 慢 SQL 与连接持有

| 指纹/来源 | 次数 | 累计 / P95 / max | 关联与根因 |
| --- | ---: | ---: | --- |
| `be891b5c1af362da` audio catalog | 4 | 29.444 / 7.602 / 7.602s | 4 个 cache miss；executor 4.691ms，不支持加索引 |
| `c1e57cc097d3e051` notification due-provider | 4 | 5.199 / 1.588 / 1.588s | Watchdog/通知扫描；无 timeout |
| `c014bff91db5d2af` Publisher pending scan | 3 | 3.749 / 1.433 / 1.433s | 既有索引/轻投影；无锁 |
| `25b2a425193817e3` Publisher stale scan | 2 | 3.708 / 2.281 / 2.281s | 既有扫描合同；无 executor 证据支持 DDL |
| data/chart/PDF clean/notification 等 | 9 | 11.326s | 单次 1.014–1.567s；分散在页面与后台，不构成单表扫描风暴 |

4 个长连接持有均来自 web，P50/P95/max 7.665/7.810/7.810 秒；SQL 合计 29.582 秒，非 SQL 仅
0.745 秒，4/4 都关联慢 SQL。三个是 audio catalog，一个包含鉴权 checkout；没有外部 I/O 跨 Session 证据。
idle 观测仅 40 个短暂 episode，最大 3 连接、事务龄 1.290 秒、idle 1.250 秒，没有持久
idle-in-transaction。

PgBouncer 1,440 个分钟样本中 query/query-wait/connect timeout、server crash、TLS、HBA 等八类错误均为 0；
288 个新 server connection（12/小时）全部来自当前 Mac mini `100.96.3.12`，无 proxy TUN fallback。
wait P50/P95/max 为 28.72/52.88/261.70ms，query 为 65.05/83.36/826.48ms。

### 页面/API

1,179 个完整请求覆盖 122 个端点，1,173 成功/6 失败，累计 524.169 秒，P50/P95/max
3.83ms/1.288s/89.596s；6 个失败均为 favicon 404，自然业务 HTTP 失败为 0。

- 低频高延迟：`POST /api/v1/videos/sync` 2 次 48.451/89.596 秒；
  `POST /api/v1/channels/sync/all` 1 次 31.618 秒。均为 200、无匹配慢 SQL，日志出现 Google API
  30 秒 read timeout；不能归咎数据库。
- 页面慢但无匹配慢 SQL：content web-submissions 97 次累计 44.032 秒、P95 0.736 秒/max 1.762 秒；
  web-submit 6 次累计 39.107 秒、P95/max 8.491 秒。最慢提交 `cb651634` 包含多次亚秒 DB、两次通知入队、
  文件 staging/hash/broker 边界，现有日志无法把剩余时间精确分段，保持 OPEN/OBSERVING。
- `audio-assets` 9 次累计 33.020 秒，4 个 cache miss 匹配 29.444 秒慢 SQL；另外 5 次热命中快速，说明
  TTL/single-flight 有效，但冷链仍有 executor 外长尾。
- B站上传 11 次累计 18.549 秒、P95 1.955 秒，MP3 detail/history、charging、rankings 等也有 1–3 秒
  无慢 SQL样本；下一轮继续拆 acquire/serialize/cache/外部 I/O，不能用“慢 SQL=0”误报无问题。
- `zsxq-insights overview`、`zsxq topics/local`、`topics/local/tabs`、`sync/status` 本窗没有请求行；这是
  **未捕获**，不是这些重点链路无问题。

### 后台任务和无前台端点异常

Celery 日志有 258 completed、0 retry、0 failed，成功执行累计 7,420.266 秒。按活跃执行排序：
Publisher pipeline 13 条/2,036.778 秒（P95/max 277.351/288.945 秒），B站 upload 11 条/1,735.746 秒
（max 379.086 秒），PDF clean 5 条/1,273.740 秒（max 442.090 秒），ZSXQ publish 9 次执行/
1,210.339 秒（max 421.419 秒），MP3 workflow 69 条/670.942 秒（P95 57.816 秒），通知 150 条/
419.653 秒（P95/max 4.118/7.488 秒）。

durable 记录揭示日志聚合不能直接说明的壁钟：

- ZSXQ publish 6 条最终 completed；其中 3 条先因每日文件配额 `13407`、401 与平台 `1059` 失败，约
  4 小时后换账号/投递继续并完成。原始 durable 壁钟 44,932.456 秒，减去 9 次真实 Celery 执行
  1,210.339 秒后有 **43,722.117 秒（97.3%）** 是终态等待/续跑间隔。最终成功不能抹掉初次失败；
  需新增可查询的 active/queue/retry-wait 阶段计时，状态 OPEN/OBSERVING。
- PDF clean 5 条全部完成；日志显示单 worker 串行，watchdog 队列深度由 3→2→1，未见 orphan。
  durable 3,794.788 秒减去活跃执行 1,273.740 秒，约 **2,521.048 秒（66.4%）** 是可见队列等待。
- Publisher durable 24 条，20 completed/4 failed。3 条 `required_material_unavailable` 是现有
  fail-closed 防线正确阻断，1 条为“找不到已生成的视频文件”；防线避免错误重投，但上游素材可达性仍未闭环。
- 2 条 audio transcription old-root task（277.283/915.122 秒）在当前所有日志中均无 task id，输出路径也指向
  旧 checkout；这是窗口外 actor 的正证据，不是日志漏分类。

## 表、索引与查询形态复核

| 表 | heap / TOAST / index / total | live/dead | seq/index scan | index scan share |
| --- | --- | ---: | ---: | ---: |
| `publication_tasks` | 18/116/14/148MB | 14,455/1,861 | 7,159/4,041,567 | 99.82% |
| `zsxq_topics` | 13/79/36/129MB | 12,637/1,584 | 4,272/892,661 | 99.52% |
| `zsxq_topic_comments` | 91/4.2/23/118MB | 41,333/5,644 | 530/359,308 | 99.85% |
| `zsxq_attachments` | 35/0.1/42/78MB | 33,906/37 | 648/1,135,952 | 99.94% |
| `zsxq_sync_runs` | 1.2/35/0.9/37MB | 1,062/46 | 311/338,025 | 99.91% |
| `external_documents` | 0.9/4.9/1.3/7.0MB | 1,817/146 | 1,080/84,997 | 98.75% |
| `content_pipeline_assets` | 3.4/0.04/1.2/4.7MB | 4,022/584 | 4,096/10,039 | 71.00% |

`publication_tasks`/topics/comments/attachments/sync-runs/external-documents 的 HOT update 比例分别约
1.18%/0.71%/70.46%/0.12%/0.33%/0%，大 JSON 表仍需继续执行窄投影、窄更新纪律，但本窗没有把原始 JSON
删除或用假数据规避。

audio catalog 对 `content_pipeline_assets` 的真实 EXPLAIN 是 Seq Scan：扫描 4,022 行、返回 1,268、
shared read 430，1.186MB quicksort，planning 0.415ms、execution 4.691ms。表仅 4.7MB，顺扫本身不是
7.1–7.6 秒应用耗时的根因，故不生成索引。精确定义重复索引为 0；低使用候选与上一轮一致，但缺 stats reset、
owner、低频恢复合同和写放大 A/B 证据，禁止自动 DROP。

角色设置仍为 statement timeout 60s、lock timeout 5s、idle-in-transaction 60s。本轮没有
CREATE/DROP INDEX、ALTER TABLE、回填或生产配置 SQL，也没有足够证据生成新的 DBeaver DDL。
唯一待人工 SQL 是既有受保护的 YouTube 不可用频道停用脚本；它不是索引整改，执行前仍须人工只读复核。

## 已落地变更、测试与运行态验证

- 已落地：本报告、4 条既有 Bug 记录、Bug README 和 recurrence registry；没有生产代码/配置/DDL 变更。
  原因是主要 SQL 的 executor 已排除，B站/YouTube/旧 Mac 均是仓库外运行态或外部链路根因。
- `/health` 返回 healthy；沙箱外 boot dependency gate 对 Tailscale、私网 route、PostgreSQL 与 Redis 全通过。
  受限沙箱首次门禁无法访问这些端点而 exit 3，真实环境同命令通过，属于诊断环境差异，不是产品故障。
- LaunchAgent `com.dig-ytb.dev-stack` 仍未安装。未修改运行代码，因此没有重启；也无需运行重启安全门或冒险
  打断当前任务。后续若要安装/重启，仍必须先运行 `check-restart-safety.py` 且不得 FORCE。
- 定向巡检器 **34 passed、2 subtests passed**；完整 unit 在允许 loopback 的标准环境中
  **1809 passed、4 skipped、363 subtests passed**，用时 112.92 秒。受限沙箱首跑的 34 个失败全部是
  本地 HTTP 测试绑定 `127.0.0.1` 被拒的 `PermissionError`；同一命令在标准环境全绿，没有业务断言失败。
  `compileall`、Bug strict 209/209、registry JSON 与 `git diff --check` 也通过。测试绿灯只证明代码/文档合同，
  不替代上述运行态闭环。
- 两次只读索引诊断最初因 shell 引号导致 SQL 语法错误，事务均自动回滚；更正后取得重复索引=0。
  该自错没有生产写入，不计作产品 SQL error。

## 人工动作、风险与下一轮验收

1. **P0 旧 Mac 隔离**：在 `100.117.177.98` 对应 MacBook Pro 停止 backend/Celery/Watchdog/scheduler/手工脚本；
   若不能证明全部停止，则协调轮换 PostgreSQL/Redis 凭据并隔离 broker namespace。仅断开 6 个会话会自动重连，
   不得作为完成。隔离后要求完整 24 小时远端会话=0、old-root=0，并取得当前根自然任务成功。
2. **YouTube 人工配置**：只读确认 `id=21/56` 仍返回 0 results 后，在 DBeaver 逐段审核并执行
   `docs/sql/disable_unavailable_youtube_channels_20260920.sql`；风险是误停恢复频道，回滚按脚本恢复 `is_active`。
   验收为 channel 49/49、video errors=0 连续两个自然周期，再观察一周期无复发。
3. **B站链路取证**：下一次真实失败同秒保存 DNS 结果、系统代理、TUN route、实际连接目标和 HTTP 分类；
   当前 200 只算恢复快照。验收为完整 24 小时所有自然周期 7/7，且无 Session 跨外部 I/O。
4. **数据库链路后窗**：继续要求 PgBouncer 六类错误、SQL error、timeout、长 idle transaction 为 0，
   audio catalog 冷链 P95 显著回落；新尖峰必须保存同秒 pool/socket/route/activity。
5. **后台阶段指标**：ZSXQ durable 壁钟拆分 active/queue/retry-wait，PDF clean 拆分 queue/execute；下一窗按阶段
   而不是原始 `created_at→completed_at` 排序，避免把等待冒充 executor 或 worker 执行。
6. **未捕获边界**：本窗没有 ZSXQ 四个重点页面请求；慢 SQL 阈值也不覆盖亚秒 N+1、acquire/fetch/serialize、
   缓存失效与外部 I/O。下一轮必须获取自然页面样本，未捕获时继续明确写“未捕获”。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `old-root audio transcription + 100.117.177.98` | 旧 Mac 共享 DB/Redis/broker 权限 | root 快照、素材 fail-closed、启动门禁 | 2/2 old-root；6 个空 app 会话 | **是，反复** | **OPEN/WAITING_DISTRIBUTED_ISOLATION**；人工停旧栈或轮换凭据/namespace | 完整 24h 远端会话=0、old-root=0、当前根自然成功 |
| `be891b5c1af362da` | driver/PgBouncer/私网 fetch 尾延迟 | 有界投影、Session 释放、TTL/single-flight | 应用 7.149–7.602s；executor 4.691ms；热命中快速 | 是 | **OPEN/OBSERVING**；无 DDL | 冷请求 P95 回落、暖请求 SQL=0、六类网络错误继续为0 |
| `BILI-INTEL DNS nodename` | DNS/代理/TUN 间歇故障 | 逐 competitor 隔离、独立周期日志 | 24/24 周期0/7，168失败；窗后恢复200 | **是，恶化** | **OPEN/WAITING_NETWORK_TRACE** | 同秒五源证据；完整窗全周期7/7 |
| `YouTube id=21/56` | 永久不可用来源仍启用 | 受保护停用 SQL 已生成 | 第12窗仍失败；脚本未执行 | **是，反复** | **OPEN/WAITING_MANUAL_CONFIG** | 人工停用后 channel49/49、video errors0 连续两周期 |
| `YouTube sync 30s read timeout` | Google API 瞬时网络/服务长尾 | 分频道隔离、失败详情 | 3请求169.665s、max89.596s，无慢SQL | 是 | **OPEN/OBSERVING**；禁止跨DB Session | 外部错误归零、P95回落、无DB hold |
| `ZSXQ publish raw wall >4h` | 外部配额/认证/平台错误后的等待续跑，且阶段指标缺失 | 账号轮换/续跑历史审计已存在 | 6最终完成；97.3%为非活跃等待 | 是 | **OPEN/OBSERVING**；最终成功不抹初次失败 | 分段指标落地；自然任务无13407/401/1059且不长等 |
| `PDF clean queue wait` | 单 worker 串行积压 | 队列可见性/Watchdog 现有防线 | 5完成；66.4%原始壁钟为排队 | 本窗出现 | **OBSERVING**；评估容量而非误判 orphan | queue P95预算明确，无长期积压/重复投递 |
| `Publisher required_material_unavailable` | 跨节点本地素材不可达/上游缺视频 | repair fail-closed 已运行 | 4/24失败，3正确阻断、1缺视频 | 是 | **OPEN**；防线有效但根因未消失 | 当前根素材可达，失败率0，旧 render 错误不复发 |
| `PgBouncer timeout cluster` | 上窗私网/池传输抖动，物理断点未闭环 | Session纪律、timeout与分段telemetry | 本窗八类错误0，22慢SQL/53.426s | 本窗未复发 | **OBSERVING**；一次绿窗不足关闭 | 后续完整窗继续0；尖峰同秒四源证据 |
| `ZSXQ重点页面无请求` | 覆盖空白 | HTTP/SQL关联分析 | overview/topics/tabs/status 均无样本 | 未捕获 | **OBSERVING**；不得报健康 | 下一窗每条链有自然样本并关联SQL/Session |
