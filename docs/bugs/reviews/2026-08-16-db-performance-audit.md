# 2026-08-16 数据库性能与错误巡检

## 巡检口径与结论

- 固定窗口：`2026-08-15 06:02:19` 至 `2026-08-16 06:02:19`（Asia/Shanghai）。
- 数据源：`logs/slow_query.log`、`logs/app.log`、`logs/backend.log`、`logs/scheduler.log`、`logs/celery/*.log`、本机 PgBouncer 日志；并读取真实 PostgreSQL 只读 catalog、`pg_stat_user_tables`、`pg_stat_user_indexes` 与代表性 `EXPLAIN (ANALYZE, BUFFERS)`。
- 上一轮基线：`docs/bugs/reviews/2026-08-15-db-performance-audit.md`；同时复核 Bug README、数据库 Session 生命周期纪律和 recurrence registry。

本轮不能结论为“已解决”。窗口内应用慢 SQL 数量和累计耗时显著恶化，页面状态轮询成为最大的累计端到端成本；PgBouncer 代理 TUN 回退、query-wait、server crash 和 TLS error 改善为 0，但 query/connect timeout 反而增加；SMTP 双层重试风暴以同一 event id 再次出现。已落地两项根因代码修复：三个状态页面统一 Page Visibility + in-flight single-flight；SMTP 默认服务内重试收敛为一次并由 Celery 使用指数退避加抖动。前者完成页面加载与确定性生命周期验收；后者经安全门后只重载 workflow_notify 并确认新 PID ready，但两者仍需后续真实窗口复核。

## 与上一轮基线对比

| 指标 | 2026-08-15 | 本轮 | 变化 | 判断 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL 数 | 245 | 688 | +180.8% | 恶化 |
| 应用慢 SQL P50 / P95 / 最大 | 1.542 / 4.924 / 15.285s | 1.578 / 7.079 / 18.179s | +2.3% / +43.8% / +18.9% | 长尾恶化 |
| 应用慢 SQL 累计 | 555.812s | 1658.905s | +198.5% | 恶化 |
| 临时诊断慢 SQL | 未作同口径基线 | 90，累计 356.675s | 新增单列 | 与托管应用隔离，不混入主指标 |
| HTTP 请求 | 1833 | 7569 | +313.0% | 流量放大 |
| HTTP 失败数 / 失败率 | 8 / 0.44% | 24 / 0.32% | 数量 +200%，比例 -0.12pp | 数量恶化、比例改善 |
| HTTP P95 / 最大 / 累计 | 2.887 / 16.619 / 987.443s | 2.884 / 26.202 / 4451.187s | -0.1% / +57.7% / +350.8% | 累计与最大恶化 |
| 连接生命周期事件 | 30 | 98 | +226.7% | 恶化，含诊断连接 |
| PgBouncer query / connect timeout | 5 / 7 | 7 / 11 | +40.0% / +57.1% | 反复且恶化 |
| proxy TUN / query-wait / crash / TLS | 16 / 9 / 18 / 3 | 0 / 0 / 0 / 0 | 全部归零 | 明确改善但不能关闭网络根因 |
| workflow_notify success / retry / failed | 8 / 0 / 0 | 161 / 3 / 1 | 高流量下复发 | 双层重试根因实证 |

“未出现 proxy TUN/crash”不等于网络已恢复；同一窗口仍有 7/11 个去重 query/connect timeout 和应用 `server closed`。同理，测试通过不能替代新 worker 与完整窗口证据。

## 页面、API 与 SQL 关联

### 端点排序

按累计耗时优先，再结合 P95、最大、失败率和受影响页面：

| 端点 | 请求 | 失败 | 累计 | P95 | 最大 | 匹配慢 SQL | 根因判断 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `GET /api/v1/celery/status` | 783 | 0 | 1835.735s | 10.117s | 26.202s | 118 / 261.746s | 后台页面持续轮询 + broker/control-plane/网络，SQL 不是全部耗时 |
| `GET /api/v1/zsxq/system-sync/status` | 369 | 0 | 414.577s | 5.232s | 12.846s | 有 | 同步页四端点扇出 |
| `POST /api/v1/zsxq/system-sync/batches` | 370 | 0 | 390.098s | 6.721s | 17.197s | 60 / 235.668s | 页面轮询 + 批次摘要 SQL/网络 |
| `GET /api/v1/zsxq/auto-publish/status` | 368 | 0 | 303.066s | 3.473s | 16.551s | 有 | 同步页轮询 |
| `GET /api/v1/zsxq/system-sync/config` | 368 | 0 | 262.269s | 3.318s | 8.023s | 有 | 配置轮询与网络往返 |
| `GET /api/mp3-video/jobs` | 405 | 1 | 188.966s | 2.137s | 20.874s | 有 | 高频列表；一次断连失败 |
| `GET /api/platform-credentials` | 219 | 0 | 152.377s | 未进前五 | 秒级 | 有 | 凭据页/鉴权与网络 |
| `GET /api/v1/celery/watchdog/status` | 778 | 0 | 134.887s | 0.344s | 7.578s | 少 | 与 Celery status 成对轮询 |
| `GET /api/mp3-video/jobs/<id>` | 65 | 0 | 105.061s | 6.366s | 14.552s | 有 | 低频高延迟任务详情 |
| Bilibili upload 端点 | 17 | 0 | 78.986s | 11.205s | 17.839s | 有 | 低频高延迟，需继续观察外部 I/O |
| workflow continue | 8 | 0 | 53.351s | 19.511s | 约 20s | 有/外部 | 低频高延迟 |

Celery status 和 watchdog 在约 12.8 小时内分别 783/778 次、成对出现，中位间隔约 60 秒；系统同步页最大连续段 278 次、约 4.3 小时，中位间隔同为 60 秒，另有可见页 15 秒节奏。源码对应的是 15 秒 `setInterval`，Chrome 后台节流只把周期放慢，没有停止。跨域预检还会为上述轮询产生同量级 OPTIONS，实际线上的浏览器请求数高于业务端点表。

### 重点 ZSXQ 页面真实验收

修复后用真实本地 API 验收：

- health：200 / 0.737s。
- Celery status：200 / 0.029s；页面真实加载新 `visibility-polling.js?v=20260816-db-polling-guard` 并正常渲染。
- system sync status：200 / 0.485s。
- `zsxq-insights overview`：冷请求 200 / 15.910s、热请求 200 / 0.026s，说明缓存有效，但冷路径仍受网络/数据库往返长尾影响。
- `topics/local`：冷请求 200 / 9.375s、热请求 200 / 0.779s，响应明确 `include_raw_payload=false`，未回传原始大 JSON。
- `topics/local/tabs`：200 / 0.527s。

### 没有匹配慢 SQL的异常

- 98 个连接生命周期事件中，托管 Web/worker/scheduler 共 80 个；有 18 个托管长持有未匹配已完成慢 SQL，P50/P95/最大为 7.901/13.225/22.710s。它们可能处于 broker、文件系统、网络、连接建立或失败 SQL 边界，不能报成“没有问题”。
- `POST zsxq browser-import` 14 次中 3 次失败，失败率 21.43%，累计 17.498s，没有匹配慢 SQL；属于新功能/外部浏览器导入链路，不归因 PostgreSQL executor。
- 单次 system-sync run-once 12.029s、没有匹配慢 SQL；应继续拆外部等待与任务投递。
- 低频高延迟：金融封面重生成 3 次 P95 15.796s、背景重生成 3 次 P95 11.361s；不能被高频端点平均值隐藏。

## SQL 指纹与根因排序

| 指纹 / origin | 次数 | 累计 | P95 | 最大 | 关联与判断 |
| --- | ---: | ---: | ---: | ---: | --- |
| `60ff7467696400d3` scheduler config | 128 | 294.165s | 5.402s | 18.179s | 累计第一；executor 0.023ms，网络/往返 + polling，较上轮次数 +42.2% |
| `ba0ef8e45dfe5c37` sync summaries | 40 | 184.420s | 10.893s | 11.507s | system sync 页面/批次摘要 |
| `5d42e2fe…` platform credential | 60 | 106.076s | 4.157s | 7.417s | 凭据/鉴权往返 |
| `3e7b5bb592aa0562` Celery report-video snapshot | 50 | 102.994s | 4.590s | 11.310s | 新进入前列，Celery status 业务快照 |
| `590220bb0dd49238` MP3 `load_task` | 23 | 82.600s | 11.732s | 12.558s | 任务详情/工作流低频高延迟 |
| `bc8e…` auth user | 32 | 71.441s | 4.519s | 秒级 | 请求鉴权往返 |
| `58f8…` business snapshot | 28 | 69.629s | 4.449s | 秒级 | Celery status 快照 |
| `ab3…` auto-publish count | 33 | 61.909s | 3.941s | 秒级 | 同步页状态轮询 |
| `f220…` attachment refs | 1 | 12.536s | 12.536s | 12.536s | 低频高延迟，连接/网络候选 |

重点表慢 SQL 数：`publication_tasks=169`、`zsxq_sync_runs=75`、`zsxq_topics=16`、`zsxq_attachments=20`、`zsxq_topic_comments=0`、`external_documents=0`。出现次数为 0 只表示本窗口没有达到慢阈值，不代表表或路径永不发生问题。

### SQL 错误与连接事件

- SQL error 共 8 条：托管应用 1 条，临时诊断 7 条；二者分开报告。
- 托管应用错误：request `03cc1336`，`api/mp3_video_workflow.py:514:load_page`，SQL 指纹 `edc415…`，约 20.169s 后 `OperationalError: server closed the connection unexpectedly`；与 15:15:15 的 PgBouncer query timeout 同秒。
- PgBouncer 去重 query/connect timeout 为 7/11；pool wait、server crash、TLS receive error、proxy TUN fallback 均为 0。server source 保持 `100.96.3.12`。
- 应用语义层没有 statement timeout、lock timeout、deadlock、connection timeout 事件；但 PgBouncer 的 query/connect timeout 必须独立统计，不能因应用 parser 为 0 而漏报。
- 现场无 waiting lock、无 idle-in-transaction；数据库设置为 `statement_timeout=1min`、`lock_timeout=5s`、`idle_in_transaction_session_timeout=1min`。

## 后台任务与外部 I/O

| 任务簇 | 完成 | retry | failed | 累计 | P95 / 最大 | 判断 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 全部 Celery | 300 | 3 | 2 | 10393.421s | 依类型 | 后台占用远高于 HTTP，必须单列 |
| publisher pipeline | 17 | 0 | 0 | 3220.373s | 396.982 / 411.329s | 长运行属媒体/外部处理，非单条 SQL |
| ZSXQ publish | 4 | 0 | 0 | 1693.222s | 1039.265 / 1039.265s | 超长外部发布，需持续验证 Session 已释放 |
| MP3 workflow | 86 | 0 | 0 | 1671.197s | 119.034 / 220.319s | 任务量大，SQL/媒体/外部混合 |
| Bilibili upload | 16 | 0 | 0 | 1583.613s | 166.639 / 秒级 | 外部上传 |
| content PDF | 2 | 0 | 0 | 1547.660s | 最大 1526.650s | 单个超长清洗任务 |
| workflow_notify | 161 | 3 | 1 | 222.058s | 1.884 / 2.559s（成功） | 同一 event 双层重试风暴复发 |
| browser import | 8 | 0 | 1 | 49.845s | 20.944 / 秒级 | 新链路失败，非 SQL 慢 |

SMTP 失败 event `bc2ede13-2a5b-4fdd-98a2-59b18ea69d20` 执行 4 轮 Celery delivery；每轮服务内 3 次固定 SSL 重试再加一次 STARTTLS fallback，最多 16 次连接后仍终态失败。已将默认服务内 attempts 改为 1、任务层改为 30/60/120 秒指数退避加 0–30 秒抖动。新 workflow_notify PID 80463 已 ready，但未实现 durable pending/幂等补发，也没有新 worker 下的真实 SMTP 故障/最终送达，因此只能标 `RUNTIME_REPLACED/OPEN`。

DeepSeek Session 修复本窗口没有真实 report-audio 目标任务样本；“未捕获”不能作为未复发证据，保持 `RUNTIME_REPLACED/OPEN`。同理，旧 finalization JSON 轮询指纹未进入本轮前列，只能继续 OBSERVING。

## 数据库规模、扫描、索引与 EXPLAIN

只读现场 PostgreSQL 16.13，事务显式只读；`pg_stat_statements` 未安装，统计上限需注明。

| 表 | 总大小 | live / dead | update | HOT 比例 | seq / idx scan | 索引扫描占比 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `zsxq_topics` | 128MB（table 13MB / TOAST 79MB / index 36MB） | 12256 / 1559 | 174016 | 0.71% | 3240 / 869863 | 99.63% |
| `publication_tasks` | 123MB（17 / 94 / 12MB） | 12514 / 2344 | 232513 | 1.33% | 6831 / 2790663 | 99.76% |
| `zsxq_topic_comments` | 118MB（91MB / 4256kB / 23MB） | 41333 / 5644 | 237197 | 70.46% | 246 / 356803 | 99.93% |
| `zsxq_attachments` | 76MB（34MB / 48kB / 41MB） | 34115 / 6389 | 264018 | 0.12% | 568 / 1130972 | 99.95% |
| `zsxq_sync_runs` | 37MB（1264kB / 35MB / 944kB） | 1008 / 209 | 111623 | 0.33% | 213 / 331658 | 99.94% |
| `external_documents` | 7016kB | 1817 / 146 | 较低 | — | 以 index 为主 | 98.82% |

代表性 executor：publication point lookup 0.038ms、channel chart 2.610ms、scheduler config 0.023ms、publisher snapshot 0.063ms、content snapshot 0.172ms、recent JSON 0.055ms、narrow projection 0.040ms、sync-runs 小表顺扫 1.518ms、Insights canonical 35.916ms。与应用 P95 7.079s 相比，主要长尾不在 executor。

索引检查：

- 没有 exact duplicate index。
- 低使用索引包括 attachment `group-tab` 约 4968kB / 1 scan、topics `group-type` 约 3672kB / 1 scan、publication status 约 488kB / 5 scan；低 scan 不等于可以删除，可能是低频关键路径或统计重置后数据。
- Insights canonical index 约 1592kB，累计 3327 scans，最近扫描 2026-08-15 06:10，仍有明确价值。
- 重点表大量 update 且 `publication_tasks/topics/attachments/sync_runs` HOT 比例低，写放大是真实风险；但本轮没有真实 SQL + catalog + EXPLAIN 证据支持新增索引，也没有足够业务覆盖证据支持删索引。

因此本轮不生成新的 CREATE/DROP INDEX，不执行 DDL、ALTER、回填或生产配置。已有人工候选 `docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql` 继续保持待人工状态：只能在 DBeaver 逐条执行其 lock/statement timeout、验证和回滚步骤；本轮 comments 路径无慢 SQL，不能据此提高优先级。

## 已落地修复与验证

### 1. 状态页面轮询根因

- 代码：新增共享 `visibility-polling.js`；改造 Celery Monitor、ZSXQ system sync、ZSXQ Lab；HTML 加 cache-bust。
- 契约：hidden 不建 timer；恢复 visible 立即刷新；in-flight 合并；隐藏/stop 清理。
- 定向：Node 生命周期测试通过；四个 JS `node --check` 通过；数据库投影/前端契约 `99 passed, 3 subtests passed`。
- 运行态：真实 Chrome 页面已加载新静态资源并完成 200 状态刷新。静态前端无需重启服务。
- 状态：`CODE_CHANGED/OBSERVING`，待完整 24 小时后台请求归零验收。

### 2. SMTP 双层固定重试

- 修复前：同 event 最多 4×(3 SSL + 1 STARTTLS) 连接，固定 30 秒重新聚集。
- 代码：默认服务内 attempts 3→1；Celery 任务改指数退避 + 抖动，保留 3 次最终预算与真实失败。
- TDD：旧实现 `3 failed`，修复后 notification policy/service `14 passed`。
- 运行替换：安全门确认无不可中断任务；仅窄重启 workflow_notify，旧 PID 67092/67097 warm shutdown，新 PID 80463 于 06:25:25 ready，队列/active/reserved/scheduled 均为 0；其他服务未重启。
- 状态：`RUNTIME_REPLACED/OPEN`。没有新 worker 下的真实故障、durable pending 或最终送达证据，不标解决。

### 完整门禁

- 完整 unit：`1230 passed, 3 skipped, 76 subtests passed`。
- frontend Node：2 个脚本全部通过（browser-human-intervention 5 scenarios；visibility polling 生命周期）。
- `compileall`：`src/backend`、`scripts/diagnostics`、`scripts/dev` 通过。
- JavaScript syntax：四个受影响脚本全部通过。
- Bug strict：164 条 Bug 全部归类、9 个复发簇、检查通过；recurrence JSON 结构通过；`git diff --check` 通过。

本轮未重启 backend、其他 Celery worker 或 scheduler：页面修复是静态资源。SMTP 修复先运行 `scripts/dev/check-restart-safety.py`，沙箱内首次因网络权限 fail-closed，沙箱外只读复核通过；随后仅窄重启 workflow_notify 并验收 ready，没有以测试通过替代 worker 运行态。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `UI-HIDDEN-POLL-CELERY-ZSXQ-20260816` | 三个状态页无 Visibility 与 overlap 门禁 | 共享 controller、三页接入、cache-bust、契约测试 | Chrome 已加载；页面/API 200 | 是，同类 Page Visibility 纪律复发 | 完整后续窗口未覆盖；前端/巡检继续负责 | hidden 期间业务请求与 OPTIONS 不增长，visible 后单飞且间隔 ≥15s |
| `60ff7467696400d3` | scheduler/Web 稳定配置反复远端读取，叠加网络长尾 | 既有 TTL + 本轮页面轮询降载 | executor 0.023ms；本轮 128/294.165s | 是，恶化 | 跨进程缓存/失效仍待查 | proxy TUN=0 窗口 origin 分拆，P95 <500ms，次数/累计明显下降 |
| `PGBOUNCER-NETWORK-QUERY-CONNECT` | Tailscale/PgBouncer/网络传输不稳定；本轮非 executor/锁/索引 | 既有 route/keepalive/诊断防线 | TUN/crash/TLS=0，但 query/connect=7/11；真实 request 03cc1336 失败 | 是 | 网络边界仍 OPEN；运维继续关联 socket/path | 完整 24h 所有 timeout/crash/TLS/source-fallback/应用 SQL error=0 |
| `SMTP-DOUBLE-FIXED-RETRY` | 服务内与 Celery 两层固定重试相乘 | 默认单 SMTP attempt + 任务指数抖动 | 新 PID 80463 ready、队列为空；无新失败样本 | 是，旧窗口 1 terminal failed | 缺 durable pending、真实失败与最终送达 | 新日志单预算、故障注入、terminal=0、event 最终送达 |
| `CELERY-STATUS-CONTROL-PLANE` | 后台轮询放大；单次 broker/业务快照仍可能 >10s | 页面降载；既有 Session release/并发 inspect/强刷单飞 | 本轮 P95 10.117s、max 26.202s；118 条慢 SQL仅占 261.746/1835.735s | 量级反复 | 单次耗时尚未闭环 | 可见页 P95 <10s、无 session 跨 I/O、无 timeout/server closed |
| `REPORT-AUDIO-DEEPSEEK-SESSION-HOLD` | 旧 worker 外部 DS 调用前未结束 Session | 上轮代码与运行替换 | 本窗口无真实目标任务 | 未捕获，不等于未复发 | 缺真实任务 | 新 report-audio 任务证明 DS 期间 0 checkout/idle transaction |
| `INDEX-GOVERNANCE-20260816` | 慢耗时主要在往返/连接；写放大存在但无 DDL 收益证据 | 无 DDL，保留人工脚本纪律 | catalog + EXPLAIN + 无锁/idle 证据 | 无新增指纹 | 低使用索引删除仍高风险 | 真实 SQL、stats、EXPLAIN、写成本与回滚证据齐全后再提 DDL |

## 未捕获边界

- `pg_stat_statements` 未安装，无法获得服务器端跨应用累计 query stats；本轮以应用 SQL、PgBouncer 和现场 EXPLAIN 三角验证。
- 日志中缺 request id 的 worker/scheduler 与临时诊断连接只能按时间、PID、role、origin 关联，无法保证一一配对。
- 浏览器自动化接管时 `document.hidden` 不可靠，hidden 分支采用确定性 Node 生命周期测试；仍需真实后台标签页的后续日志差分。
- SMTP 没有受控断连注入和 durable pending 台账；一次成功或恢复窗口不能证明最终送达。
- 本窗口没有真实 DeepSeek report-audio 样本、comments 慢 SQL或 external_documents 慢 SQL；均保持观察，不能写成“没有问题”。

## 下一轮明确计划

1. 先读本报告/registry，以 `UI-HIDDEN-POLL-CELERY-ZSXQ-20260816`、`SMTP-DOUBLE-FIXED-RETRY`、`PGBOUNCER-NETWORK-QUERY-CONNECT` 为前三优先级。
2. 按新静态资源加载时点切分 Celery/ZXSQ 端点与 OPTIONS；在真实标签 hidden/visible 之间验证计数停止、一次恢复刷新和无 overlap。
3. workflow_notify 已安全窄重载；下一轮验证新 PID 80463 后的单预算 retry 日志和真实通知最终送达，缺失败样本时继续保持 OPEN。
4. 对 PgBouncer 按每个 timeout 关联 server socket source/age、Tailscale path、request id 和 SQL origin；完整窗口要求 query/connect/query-wait/crash/TLS/TUN fallback/应用失败 SQL 全部为 0。
5. 对 scheduler config 分 scheduler/Web origin 验收 P95 <500ms；若网络稳定仍有 128 次量级，继续追 TTL miss、写入失效和跨进程调用模式。
6. 捕获下一条真实 report-audio DeepSeek 任务，验证外部 I/O 期间 Session 已归还；没有样本则继续 OPEN。
7. 继续只读复核重点表。没有真实 SQL + stats + EXPLAIN + 锁风险/回滚证据，不提交任何 DDL 建议。

## 报告后门禁补记

- `venv/bin/python scripts/dev/review_bug_records.py --strict`：通过，164/164 条已归类，回顾周期未过期。
- `jq empty docs/bugs/recurrence-registry.json`：通过。
- `git diff --check`：通过。
