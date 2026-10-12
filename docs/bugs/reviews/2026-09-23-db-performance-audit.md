# 2026-09-23 每日数据库性能与错误巡检

## 结论

固定窗口为 `2026-09-22 06:00:00` 至 `2026-09-23 06:00:00`（Asia/Shanghai）。本轮不能标记“已解决”：数据库 executor、锁、事务与 PgBouncer 故障均无根因级异常，应用慢 SQL 较上一窗口继续改善；但 Publisher 的旧版本队列修复语义在 12 条真实任务中再次污染 4 条、累计替换 7 个 Celery ID。上一轮只扫描本机绝对路径的运行拓扑门禁因此被证实存在假绿，问题保持 **OPEN / REPEATED / OBSERVING / WAITING_DISTRIBUTED_ISOLATION**。

本轮没有执行生产 DDL、索引变更、数据回填、任务重投、凭据轮换或服务重启。当前本机 27 个 Celery 控制面回包均来自当前主机和现行进程，旧 checkout 已不存在，本机 LaunchAgent/LaunchDaemon/crontab 也无旧路径；这只能证明巡检时旧 actor 已离线，不能否定窗口内持久化证据。根治需要在所有可能主机停止旧 actor，或协调轮换 PostgreSQL/Redis 凭据与 broker namespace，使旧部署物理失去共享 DB/broker 的写入能力；这属于生产配置和跨主机动作，不能无人值守执行。

## 与上一轮量化对比

| 指标 | 09-22 窗口 | 09-23 窗口 | 变化 | 判断 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL | 9 | 8 | -11.1% | 改善 |
| 应用慢 SQL 累计 | 13.349s | 10.887s | -18.4% | 改善 |
| P50 | 1.319s | 1.200s | -9.0% | 改善 |
| P95 / 最大 | 1.964s / 1.964s | 1.885s / 1.885s | -4.0% / -4.0% | 改善 |
| PgBouncer 八类故障 | 0 | 0 | 持平 | 本窗未复发，但网络簇仍观察 |
| statement/lock/deadlock/query/connect timeout | 0 | 0 | 持平 | 未复发 |
| 长 Session hold / idle in transaction | 0 / 0 | 0 / 0 | 持平 | 未复发 |
| Publisher 业务终态 | 20/24 completed，4 failed | 12/12 completed | 改善 | 终态改善不等于旧 actor 根治 |
| Publisher 旧 reason | 4 条完成任务，另 4 条 repair-limit 失败 | 4/12 条、7 次换 ID | 反复 | P0，仍未闭环 |
| YouTube 固定失效源 | id 21/56 各失败 | id 21/56 各失败 | 第四窗反复 | 人工配置仍阻塞 |

日志另有 1 条 1.256 秒的只读巡检 SQL，已从应用口径剔除。原始 `server_closed=10` 与 2 个 SQL traceback 均发生在 06:37–06:40 的单元测试故障注入，带 `unittest.mock`、测试 fixture 建删表和固定测试异常，不是自然生产数据库事故；结构化 SQL error 为 0。

## 慢 SQL、真实 executor 与索引证据

按累计耗时、P95、最大耗时排序的 8 个应用指纹均只出现一次：

| 指纹 / origin | 应用耗时 | 真实执行计划证据 | 结论 |
| --- | ---: | --- | --- |
| `e3a01e69bc4b3814` notification acquire | 1.885s | 同形 SQL executor 0.115ms，124 shared hits | 远程连接/驱动尾延迟，不是 executor |
| `cb4c8a59ef032480` web publish history | 1.837s | `ix_publication_tasks_feature_source_created_desc`，20 rows，0.210ms | 索引命中；不能加索引掩盖网络尾延迟 |
| `674016d39deab3f5` ZSXQ insights aggregate | 1.674s | 单次；无锁/timeout/长事务 | 继续观察自然样本 |
| `c1e57cc097d3e051` due provider drains | 1.200s | bitmap index，0.144ms，121 hits | executor 正常 |
| `60ff7467696400d3` ZSXQ scheduler config | 1.110s | 18 行小表顺扫，0.078ms | 小表顺扫合理，无索引收益 |
| `4a1f114c4802c3a0` provider lookup | 1.081s | 单次；通知 117/117 最终 sent | 无失败风暴 |
| `c014bff91db5d2af` Publisher pending scan | 1.053s | 目标索引命中，0.135ms | executor 正常 |
| `93f39f2621976dbe` Publisher processing scan | 1.047s | bitmap index，0.084ms | executor 正常 |

PgBouncer 共 1,440 个样本，wait P50/P95/max 为 24.14/50.88/1017.91ms，query 为 52.36/78.06/426.72ms；289 次连接全部来自私网地址，12.042 次/小时，无 proxy-TUN fallback。当前活动快照为 active 1、idle 9、idle-in-transaction 0、waiting lock 0。11 次任务准入短观测最大仅 3 连接、事务龄 0.269 秒、idle 龄 0.241 秒，未触发 deferral。

## 页面、API、后台任务与 SQL 关联

HTTP 共 66 次、19 个端点，48 成功/18 失败，累计 28.237 秒，P50/P95/max 为 4.82ms/2.355s/6.313s。18 个失败主要是启动探针、无认证页面探测、contact 探测及两次刻意空参数 ZSXQ 请求，不能当成自然业务失败。

| 端点 | 请求/成功/失败 | 累计、P95、最大 | SQL / 根因关联 | 状态 |
| --- | --- | --- | --- | --- |
| `POST /content-pipeline/web-submit` | 3/3/0 | 15.635s；6.313s；6.313s | 无 >=1s SQL；最慢 request `07cdc9c2` 的可见 DB 阶段均低于 0.55s，剩余在串行 durable outbox/broker/应用阶段 | OPEN/OBSERVING，需阶段 telemetry |
| `GET /content-pipeline/web-submissions` | 17/17/0 | 9.621s；1.169s；2.355s | request `14d557ad` 匹配 1.837s history SQL；executor 0.210ms 且索引命中 | 网络/驱动尾延迟 |
| `POST /zsxq/topics/local` | 2/1/1 | 1.272s；1.103s；1.103s | 成功 request `b5faa4d8` 的 7 条 SQL 合计约 0.950s，单条均 <1s；失败是空参数探针 | 低频多往返，不等于慢 executor |
| `GET /zsxq-insights/overview` | 1/1/0 | 0.813s | 无匹配慢 SQL | 本窗低频，继续观察 |
| `POST /zsxq/topics/local/tabs` | 2/1/1 | 0.173s max | 成功 0.172s；失败是空参数探针 | 正常 |
| `GET /zsxq/system-sync-scheduler/status` | 1/1/0 | 0.267s | 无慢 SQL | 正常 |

慢页面但无匹配慢 SQL 单列：`web-submit` 3 次全部 4.777–6.313 秒；`topics/local` 的成功样本 1.103 秒由多次远程 SQL 往返构成。没有前台端点的后台长任务单列：PDF clean 8 次累计 649.524 秒、P95/max 267.411 秒；ZSXQ publish 3 次累计 494.395 秒、P95/max 276.261 秒；notification 141 次累计 350.429 秒、P95 3.538 秒；它们没有 Session hold 证据，长耗时主要位于外部 I/O/业务执行。Celery 记录的 1 次 Publisher success 是 stale/no-op delivery，不能替代 durable 业务验收。

## 主要根因与复发

### P0 Publisher 旧 actor 再次改写可见消息

12 条 Publisher 均最终 `completed/done`，但 `4c6a3d87…`、`2da2b7d2…`、`46528e4e…`、`ce7bcee9…` 持久化写入当前源码已删除的 `pending_message_not_active_but_queue_visible`，repair count 分别为 1/2/3/1，累计 7 次把仍可见消息换成新 ID。所有 12 条 Publisher 及 3 个 MP3 父任务的 params/result 均含旧 checkout 路径，父任务和通知产物来自旧运行根目录；当前本地 Watchdog 在同一时间线上发布替代消息后 CAS=0 并撤销，render worker又按 current-id guard 跳过，说明现行防线只避免了进一步放大，旧 actor 仍先一步改写 durable row。

本轮原始 Celery `broadcast` 得到 27 个独立回包，PID、host、broker/vhost 与当前本机进程一致，没有当前在线的重复 nodename 或旧 PID。本机进程、启动项和旧路径均为空，因此旧 actor 是窗口内短暂存在、随后退出的进程，或在未纳入本机进程扫描的其他主机/调度入口。上一轮 `foreign_runtime=0` 只能覆盖采样时刻与本机绝对路径，不能作为分布式唯一性证明。

已落地且本窗证明有效的防线是 CAS loser 撤销、current-id stale skip 和当前代码“消息可见即等待”；它们让本窗 12/12 最终完成、未再出现 repair-limit 终态失败。它们不能阻止旧代码使用共享 DB 凭据直接更新任务，因此根因不闭环。责任动作是人工盘点所有持有生产 DB/Redis 凭据的主机与启动器，停掉旧版本并轮换凭据/namespace；必要时给当前部署发放独占身份。完成后需安全门、协调重启、自然 MP3→Publisher 和后续完整 24 小时窗口验收。

### P1 页面长尾与数据库网络尾延迟

慢 SQL 总量与累计耗时下降，代表查询在 PostgreSQL 内均为 0.08–0.21ms，而应用侧为 1.0–1.9s；没有锁、长事务、pool timeout 或高成本 plan。根因方向仍是远程连接/驱动/调度尾延迟，`web-submit` 另有串行 outbox/broker 阶段缺少细粒度 marker。当前无可证明有效的索引或查询改写，不做盲修。

### P1 外部调用与重复失败

B站情报 24 个自然周期全部 6/6 成功，累计 226.844 秒，P50/P95/max 8.977/10.670/13.328 秒，本窗未复发。YouTube channel 同步仍为 49/51，video 同步仍有 2 个 error，失败继续固定为 id 21/56，已连续第四窗口复发；受保护停用脚本尚未人工执行，保持 **OPEN/REPEATED/WAITING_MANUAL_CONFIG**。

通知 outbox 窗口创建 117 条且 117 条最终 sent，125 次总 attempt；其中 2 条各 attempt 5 次，在约 16 分钟后成功，日志显示指数退避与 provider circuit 生效，没有无限重投。它是一次外部 provider 短暂故障，保持 OBSERVING，不归为 DB executor 或失败风暴。

## 表规模、扫描与索引复核

| 表 | total / heap / index / toast | live/dead | seq / idx scan | 判断 |
| --- | --- | ---: | ---: | --- |
| `publication_tasks` | 145.3MB / 17.0MB / 13.1MB / 108.5MB | 13,825 / 1,931 | 7,097 / 3,635,436 | toast 大但热点计划命中索引；无新 DDL 依据 |
| `zsxq_topics` | 134.8MB total | 12,637 / 1,584 | 4,176 / 891,879 | 本窗仅 1 条慢指纹，无计划证据支持加索引 |
| `zsxq_topic_comments` | 123.7MB total | 41,333 / 5,644 | 475 / 358,605 | 无本窗慢 SQL |
| `zsxq_attachments` | 81.5MB total | 33,906 / 37 | 626 / 1,135,747 | 无本窗慢 SQL |
| `zsxq_sync_runs` | 38.8MB total | 1,062 / 46 | 285 / 337,693 | 无本窗慢 SQL |
| `external_documents` | 7.2MB total | 1,817 / 146 | 1,058 / 84,987 | 无本窗慢 SQL |

精确重复索引为 0。存在低扫描索引，但没有“真实热点 SQL + pg_stat + EXPLAIN ANALYZE/BUFFERS”三项同时成立的删除或新增证据；本轮不生成索引 DDL。`system_configs` 只有 18 行，顺扫 0.017ms，新增索引只会增加写放大。

## 变更、测试与运行态验证

本轮没有修改应用代码或生产配置，也没有重启服务；原因是已证根因位于当前进程边界之外，继续增加本机路径扫描、超时或 repair 次数只会掩盖复发。已更新本报告、Publisher Bug 记录、recurrence registry、Bug 索引与数据库 Session/运行验收纪律，把上一轮假绿重新打开并明确分布式隔离条件。

运行态只读验收包括：66 次真实 HTTP、153 次 Celery 日志、24 个 B站自然周期、YouTube 双自然周期、12 条 Publisher durable rows、117 条 notification deliveries、PgBouncer 1,440 样本、生产表统计和六条代表 SQL 的 `EXPLAIN (ANALYZE, BUFFERS)`。这些证据证明数据库 executor 当前正常，但不能替代旧 actor 停止后的后续窗口。

验证门禁全部使用本轮新结果：完整 unit `Ran 1714 tests in 102.136s / OK`；`compileall` 通过；worker app factory 28 个文件通过；Celery revoke boundary 检查 323 个 Python 文件通过；recurrence registry JSON 校验、Bug strict 206/206 与 `git diff --check` 均通过。测试通过只证明当前工作区合同未破坏，不作为旧 actor 已隔离的运行态证据。

## 待人工动作、风险与未捕获边界

1. 盘点所有持有生产 PostgreSQL 与 Redis/Celery 凭据的主机、容器、终端会话、launchd/cron 和远端编排；按旧 reason 时间 `08:59:33–09:16:10`（Asia/Shanghai）反查进程审计。
2. 在确认当前队列与不可中断任务安全后，停止旧部署；若不能证明全量停止，协调轮换 DB/Redis 凭据并将当前部署迁到独占 broker namespace。旧凭据必须失效，否则本机门禁无法构成写入围栏。
3. 配置变更前后均运行 `scripts/dev/check-restart-safety.py`；只有安全门通过才可标准重启，禁止 FORCE。变更后核对 raw Celery reply 数、PID/host/nodename、broker/vhost 和所有 worker ready。
4. 本轮没有待执行 DBeaver SQL。索引和表结构没有证据支持变更；凭据轮换也不应通过报告内硬编码 SQL 执行。

未捕获边界：日志只能关联带 request/task origin 的事件；旧 actor 未记录 PID/host/deployment identity，当前控制面快照也不能回看已退出进程。`web-submit` 缺少完整阶段 marker，外部 I/O 与 broker 时间只能由排除法界定；窗口内无自然 ZSXQ sync run，因此不能把同步链路写成已覆盖。无慢 SQL 也不等于无多次低于阈值的远程往返。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `pending_message_not_active_but_queue_visible` | 窗口内旧版本 actor 仍共享 DB/broker；本机绝对路径扫描不是分布式围栏 | 可见即等待、CAS loser 撤销、current-id guard、本机冲突 fail-closed | 12/12 完成，但 4 条累计 7 次旧语义；当前 27 worker 回包干净 | **是** | 人工跨主机停旧栈或轮换凭据/namespace；OPEN | 自然 MP3→Publisher 无旧 reason/换 ID/repair-limit，且后续完整窗无复发 |
| DB 远程尾延迟 | executor 快，应用侧 SQL 1–2s；网络/driver/调度尾部 | PgBouncer/SQL origin/plan 观测 | executor 0.08–0.21ms，8 条应用慢 SQL / 10.887s | 本窗改善，历史反复 | 捕获下一 episode 的同秒网络/driver/pool 证据 | 后续窗慢 SQL 与 pool 指标稳定，出现 episode 时完成四源归因 |
| `web-submit` 4.8–6.3s | 串行 DB/outbox/broker/应用阶段，缺完整 marker | durable outbox、批量去重既有防线 | 3/3 成功，无 >=1s SQL | 持续 | 增加分阶段 telemetry 后再决定优化 | 自然多文件请求能把总时长完整分摊，不跨 Session 做外部 I/O |
| YouTube id 21/56 | 远端已无内容但本地仍启用 | 结构化失败身份、受保护停用脚本 | channel 49/51、video errors=2 | **第四窗** | 人工执行既有配置脚本 | 双自然周期 51/51 且 error=0 |
| notification provider 短暂失败 | 外部 provider 暂时不可用 | durable lease、退避、熔断 | 117/117 sent，2 条各 5 attempts 后成功 | 新增短 episode | 观察是否跨窗反复/积压 | 下一窗无持续 retry_wait/dead-letter/重复投递 |
