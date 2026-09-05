# 2026-09-05 每日数据库性能与错误巡检

## 根因闭环结论

固定窗口：2026-09-04 06:00:00–2026-09-05 06:00:00，Asia/Shanghai。

本窗口没有数据库错误、PgBouncer timeout、锁等待或 idle-in-transaction，但性能明显回退，不能写成根因已解决：

1. 应用慢 SQL 从 29 条/39.148 秒升至 103 条/181.964 秒，P95 从 1.932 秒升至 3.720 秒，
   最大值从 1.937 秒升至 9.386 秒。慢点横跨点查、后台修复扫描、通知 outbox 与 chart-data，
   对应生产 `EXPLAIN ANALYZE` 仅 0.02–3.14ms；共同成因仍是 PostgreSQL/PgBouncer/网络往返尾延迟，
   不是某一张表缺索引。
2. PgBouncer 六类错误均为 0，较上轮一次 `query_wait_timeout` 改善；但 stats wait/query P95 分别从
   56.41/112.74ms 升至 137.56/239.98ms。完整窗口没有错误只能作为恢复窗口，历史网络簇保持
   `OPEN / REPEATED / OBSERVING`。
3. 内容管线 Web 提交 5 次均成功，但 P50/max 为 10.201/37.742 秒。最慢 request `285c2245`
   约 13 条 SQL 累计 8.925 秒，剩余约 28.816 秒在 SQL 外；运行进程仍早于 `dec217e` 的最终
   事务边界、批量去重冲突和最小 app factory 加固，不能用测试结果冒充生产替换。
4. 父任务唤醒旧宽 JSON 指纹 `71b8b6d6cc87d1f3` 在固定窗为 0。06:10 运行时已包含核心窄候选形状，
   但窗口没有目标自然 Publisher completion；18:49 提交的失败恢复与事务边界补强也尚未加载，
   因此保持 `OBSERVING`。

本轮没有生产 DDL、索引变更、数据回填、超时放宽、缓存清空或业务任务补投。标准重启前安全门两次确认
无不可中断任务，全量测试通过；自动审批仍以实际服务中断风险拒绝重启。当前进程保持健康且未被替换，
最终代码处于 `CODE_VERIFIED / WAITING_RUNTIME_REPLACEMENT`。

## 前后量化

| 指标 | 09-04 窗口 | 09-05 窗口 | 变化与判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL 数 | 29 | 103 | +255.2%，明显回退 |
| 慢 SQL 累计 | 39.148s | 181.964s | +364.8% |
| 慢 SQL P50/P95/max | 1.285/1.932/1.937s | 1.286/3.720/9.386s | P50近似持平；P95 +92.5%；max +384.5% |
| HTTP 请求/失败 | 672/2 | 1661/2 | 流量 +147.2%；失败率 0.30%→0.12% |
| HTTP 累计 | 257.530s | 581.354s | +125.7% |
| HTTP P50/P95/max | 5.77/1592.42/10164.56ms | 7.03/1328.84/37741.54ms | P95 -16.6%；max +271.3% |
| 连接持有 >5 秒 | 1 | 6 | +500%；4 scheduler、2 web |
| Celery 完成/失败/retry | 205/0/0 | 227/3/0 | 失败为 upload-only 外部任务，不是 DB SQL 错误 |
| PgBouncer query/connect/wait timeout | 0/0/1 | 0/0/0 | 本窗改善，仍需后续窗 |

103 条慢 SQL 均为托管应用来源；`publication_tasks` 59 条、`zsxq_topics` 1 条、
`zsxq_attachments` 1 条，其他重点表没有超过 1 秒的 SQL。结构化 SQL error 与 traceback episode 均为 0；
这是全日志块与 PgBouncer 三源对账后的结果，不把专用 `publication_tasks` logger 的 0 当作全库 0。

## 主要瓶颈、调用链与根因

| 优先项 | 指纹 / origin | 次数 / 累计 / P95 / max | 根因与影响 |
| --- | --- | --- | --- |
| P0 最大尾部 | `eef0f08c4ecec7dd` / stick figure auto enqueue | 5 / 17.973s / 9.386s / 9.386s | 按 id+feature 的轻点查；executor 点查样本 0.032ms，后台自动续跑被网络尾延迟放大 |
| P0 通知恢复 | `c1e57cc097d3e051` / due_provider_drains | 9 / 17.081s / 3.307s / 3.307s | 窄 outbox 扫描；本窗无 timeout，仍承担每轮远端往返 |
| P0 stale repair | `c014bff91db5d2af` | 9 / 16.131s / 3.121s / 3.121s | pending repair 窄字段扫描，后台 watchdog 周期成本 |
| P0 business repair | `1e920b463e0710b7` | 5 / 12.177s / 6.552s / 6.552s | 多 feature processing 窄候选；不是 JSON 全行扫描 |
| P0 publisher repair | `93f39f2621976dbe` | 6 / 11.717s / 5.402s / 5.402s | stale processing 窄候选；一次 8.798s hold 含 SQL 5.402s |
| 前台 chart | `94bfcee9b29fff74` | 6 / 11.603s / 4.278s / 4.278s | 实际 chart executor 3.135ms，慢在端到端往返 |
| MP3 冷目录 | `be891b5c1af362da` | 4 / 8.643s / 2.647s / 2.647s | 有界轻投影目录；继续观察缓存与远端网络 |

专用 publication 查询审计从 06:00 延伸到巡检时刻，不能当作固定 24 小时总数；它显示九个 watchdog
候选查询各约 136–148 次，常态 P50 约 560–661ms、P95 约 735–1100ms。各查询本身已是窄投影，
说明周期任务每轮都在支付远端往返。没有证据支持把它们合并为一条复杂 SQL 或新增索引；后续应在稳定网络
窗口比较同一组指纹，再决定是否调整 watchdog 周期或快照所有权。

6 次长连接持有累计 41.927 秒，其中 SQL 37.836 秒、非 SQL 4.091 秒；5 次可直接关联慢 SQL。
唯一未被算法匹配的 managed hold 位于 20:00:59，实际仍记录 1 条 SQL 5.402 秒与非 SQL 3.396 秒。
因此本窗 hold 主要由 SQL 驱动等待形成，不是 Session 跨浏览器、SMTP 或文件处理。

## 页面、API 与后台任务

日志覆盖 78 种归一化 method+path，包含知识星球、MP3、Publisher、数据页、凭据、B站、PDF 合并与
内容管线，没有只筛选一个页面。

| 路径 | 请求/失败 | 累计 | P50/P95/max | SQL 关联 |
| --- | ---: | ---: | --- | --- |
| GET `/api/v1/data/summary` | 145/0 | 94.240s | 426/1629/7273ms | 5 条慢 SQL，10.517s |
| GET `/api/v1/rankings/all` | 147/0 | 78.288s | 397/1073/6077ms | 无匹配慢 SQL |
| GET `/api/v1/data/chart-data` | 149/0 | 76.027s | 307/1598/5388ms | 9 条慢 SQL，16.111s |
| POST `/api/v1/content-pipeline/web-submit` | 5/0 | 70.104s | 10201/37742/37742ms | 1 条 >1s；最慢请求 SQL 总计约 8.925s |
| POST `.../bilibili-upload` | 12/0 | 29.051s | 2668/3716/4043ms | 无匹配慢 SQL，含外部/broker 路径 |
| GET `.../bilibili-uploads` | 39/0 | 28.816s | 755/1196/1478ms | 无匹配慢 SQL |
| GET `/api/mp3-video-workflows/audio-assets` | 8/0 | 14.381s | 2654/6238/6238ms | 5 条慢 SQL，9.682s |
| GET `/api/v1/celery/status` | 3/0 | 7.328s | 8/7318/7318ms | 无匹配慢 SQL；后续同端点毫秒级 |

两次 HTTP 失败分别为取消状态冲突 409 和 B站 charging-options 外部 502；均没有匹配数据库错误。
低频接口仍单列：素材再生成各 4 次、最大 3.84–4.23 秒；PDF merge 创建 1 次 2.369 秒；
continue-full-video 4 次最大 1.974 秒。接口慢而无慢 SQL 的部分不能误算成数据库 executor。

Celery 227 次完成累计 6292.682 秒：ZSXQ publish 7 次、Publisher 12 次、MP3 delivery 54 次、
upload-only 9 次、PDF clean 3 次、workflow notify 137 次、audio clean 4 次、PDF merge 1 次。
3 个 failure 都属于 upload-only；没有 SQL traceback。外部发布、OCR、媒体与浏览器耗时不纳入数据库执行时间。

自然 `DB_SESSION_BOUNDARY` 固定窗对账得到 3 个唯一 revoke 事件：MP3 superseded repair、watchdog
superseded repair、watchdog child cleanup；全部 `session_in_transaction=false`、durable=true、成功。
它只验收这些入口，不能推广到尚未自然触发的所有 cancel/revoke 分支。

## 物理层、索引与 PgBouncer

| 表 | 总大小 | live/dead | HOT% | 索引扫描占比 |
| --- | ---: | ---: | ---: | ---: |
| publication_tasks | 130MB（TOAST 101MB） | 13215/1607 | 1.26% | 99.78% |
| zsxq_topics | 129MB | 12637/1584 | 0.71% | 99.55% |
| zsxq_topic_comments | 118MB | 41333/5644 | 70.46% | 99.89% |
| zsxq_attachments | 78MB | 33906/37 | 0.12% | 99.95% |
| zsxq_sync_runs | 37MB | 1062/46 | 0.33% | 99.93% |
| external_documents | 7016kB | 1817/146 | 0% | 98.79% |

`publication_tasks` dead tuple 从 1033 升至 1607（+55.6%），总大小保持 130MB；继续观察 autovacuum 与
JSON 写放大，不执行 `VACUUM FULL`。精确重复索引为 0。低使用索引包括 attachments group-tab PDF、
topics group-type、external_documents tts/summary 与 publisher MP3 match；缺少完整业务周期、等价索引与
真实查询参数，不能删除。

只读执行计划：publication 点查 0.032ms、chart 3.135ms、system config 0.023ms、publisher list
0.070ms、content list 0.195ms、sync runs 1.727ms、Insights canonical 35.214ms。当前无 waiting lock，
7 个远端 idle/ClientRead 会话，无 idle-in-transaction；role/db 保险仍为 statement 60s、lock 5s、idle txn 60s。

固定窗 PgBouncer 1440 个 stats 样本，wait P50/P95/max 23.51/137.56/458.06ms，query
53.26/239.98/691.57ms；288 次建连，正好 12/h，source 均为 `100.96.3.12`，proxy TUN fallback 0。
巡检时 `SHOW POOLS` 为 transaction mode、`cl_waiting=0`、`sv_login=0`、`maxwait=0`，6 条 server
均为 `used`。这是一张恢复快照，不能反推 18–20 时性能尖峰时池状态。

## 代码、测试、部署与状态

- 当前 HEAD `dec217e`（2026-09-04 18:49:39+08:00）包含日志块解析、内容提交事务边界、批量冲突回退与
  父任务失败恢复补强。长期 backend PID 38129 于 09-04 06:09:56 启动，Celery/watchdog 于 06:10
  启动，明确早于该提交；没有把磁盘代码等同于运行代码。
- 定向内容提交、父唤醒、日志分析与 queue repair 共 114 项通过；完整 unit 1607 项/74.100s/OK。
  首轮沙箱中 34 个 loopback server bind 错误在允许本机端口后全部通过，不属于代码失败。
- worker app factory 27 文件、revoke 314 文件、Bug strict 195/195、compileall、`git diff --check` 均通过。
- 15:02 与 15:07 两次重启安全门都读取 47 条近期 publication tasks、0 个活动 ZSXQ sync、
  0 个不可中断任务。未使用 FORCE。
- 标准 `scripts/dev/restart-dev-stack.sh` 被自动审批拒绝，原因是停止 backend、Celery 与 scheduler
  存在实际服务中断风险，且审批器要求用户对这次具体重启单独授权。没有绕过拒绝执行间接停止或重启。
- 拒绝后进程 PID/启动时间未变化；15:07 的 summary、rankings、chart-data 真实 GET 均为 200，
  分别 486/450/374ms，证明现有服务仍在正常服务，但不是 `dec217e` 运行验收。无认证的直接
  `/health` 探针返回 403，受当前访问控制保护，未把它误报成进程故障或健康成功。

## 根因闭环状态表

| 复发键/问题 | 本轮证据 | 状态 | 下一责任动作与验收 |
| --- | --- | --- | --- |
| `DB-SESSION-LIFECYCLE` 网络分支 | 六类错误0，但慢SQL181.964s、wait/query P95翻倍；executor毫秒级 | OPEN / REPEATED / OBSERVING | 后续完整窗继续对账；若再尖峰，必须同时抓 SHOW POOLS/SERVERS、socket、路由与远端 activity |
| 内容提交事务/往返 | Web max37.742s，最慢约28.816s非SQL；最终补强只在HEAD | CODE_VERIFIED / WAITING_RUNTIME | 获得具体重启授权后标准重启；首个自然多文件批次核对哈希事务0、O(1) canonical、hold与终态 |
| `71b8…` 父唤醒宽读 | 固定窗0，核心窄查询已在旧运行时；无目标自然完成，最终恢复补强未加载 | OBSERVING | 重启后对账首条 Publisher completion、有效唤醒/失败恢复、旧指纹0及完整父产物 |
| `REGRESSION-ACCEPTANCE-INTEGRITY` | SQL/logger/traceback/PgBouncer 三源均0；回放门禁与1607单测通过 | OBSERVING | 下一错误 episode 继续验证跨文件、无时间戳与边界去重；不能相加重复流 |
| 索引/表膨胀 | 无重复索引，executor均快；publication dead +574 | OBSERVING | 继续 autovacuum/HOT/写放大趋势；没有 DDL 责任动作 |

## 下一轮必须复核

1. 先确认 `dec217e` 是否已通过标准脚本替换到新的 backend/worker/watchdog PID；若未替换，不得开始运行验收。
2. 从新进程切点捕获首个自然内容多文件提交与 Publisher completion，分别验收批量去重、事务外哈希、
   最小 app factory、父任务窄唤醒、CAS 失败恢复和最终业务产物；不制造测试任务冒充自然流量。
3. 比较 103 条/181.964 秒、6 次 hold 与 PgBouncer wait/query P95；完整窗口错误为 0 后仍需后续窗口无复发
   才允许关闭网络簇。
4. 覆盖当前未观测的 Insights overview、topics/local/tabs、sync/status 真实页面/API；数据库 EXPLAIN
   不能替代浏览器、鉴权、序列化与客户端渲染。
5. 继续观察 publication dead tuple、watchdog 九个周期查询和内容提交 SQL 外耗时。任何 DDL 必须等真实 SQL、
   pg_stat 与 EXPLAIN 共同支持，并只输出 DBeaver 脚本、超时、验证与回滚，不自动执行。

原始证据保留在本地 `artifacts/db-performance/` 目录；该目录属于运行生成物，按仓库规则不提交远端。
