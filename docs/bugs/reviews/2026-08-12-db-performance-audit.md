# 2026-08-12 数据库性能与错误巡检

## 根因闭环结论

本轮不能结论为“全部已解决”。固定窗口 `2026-08-11 06:02:19` 至 `2026-08-12 06:02:19` 内，应用慢 SQL 408 条，P50/P95/最大为 `1.650/5.203/18.691s`，累计 `930.176s`；较上一轮数量下降 50.0%、累计下降 47.9%、P95 改善 5.8%，但 P50 恶化 6.5%、最大值恶化 15.3%。HTTP 1922 次，失败 13 次（0.68%），P95/最大/累计为 `1.963/24.369/833.855s`；流量下降 63.7%，因此不能把总量下降等同于根治。

本轮确认并修复 `ZSXQ-AUTO-PUBLISH-PREVIEW-SESSION`：内容流水线发布预览的账号计数查询没有结束只读 Session，随后跨七牛上传/重试持有连接 `41.059s/88.557s`，SQL 仅 `104.94/115.39ms`。修复已通过先红后绿测试、完整 unit、完整安全重启和 health；因尚无修复后真实发布批次，状态为 `RUNTIME_REPLACED/OBSERVING`，不是 `CLOSED`。

上轮 `CELERY-INSPECT-SERIAL-3X` 的并行 inspect 代码本轮已安全加载：重启后真实 Celery status 三次为 `1.503/0.030/0.109s`，均 200；但需下一完整窗口确认 PgBouncer timeout/server closed 不复发。`RUNTIME-TOPOLOGY-RELOAD` 本窗口未再出现 ImportError，25 条 MP3 workflow 均完成，仍保留观察状态。

仍未闭环：PgBouncer unique query timeout 仍为 6；topic batch update 与 scheduler config 两个指纹继续反复；MP3 audio-assets 单次请求 18.580 秒、应用侧 SQL 18.497 秒，但报告后只读复核已排除 topic 正文传输量和 executor 作为该样本主因；连接长持有最大升至 88.557 秒；评论 snapshot 出现 18.691 秒大 IN 长尾。没有 executor 证据支持新增索引。

## 范围、基线与未捕获边界

- 上一轮：`2026-08-10 06:02:19` 至 `2026-08-11 06:02:19`；本轮使用固定截止点，避免本次重启和烟测污染同比。
- 扫描 `logs/slow_query.log`、`logs/app.log`、`logs/backend.log`、`logs/scheduler.log`、`logs/celery/*.log`，并统计 PgBouncer 原始事件、HTTP、Celery 与连接生命周期。
- 数据库检查只读执行 `pg_stat_user_tables`、`pg_stat_user_indexes`、锁/会话、配置和代表 SQL 的 `EXPLAIN (ANALYZE, BUFFERS)`；未执行 DDL、回填、生产配置或破坏性操作。
- `pg_stat_statements` 未安装；没有 request id/SQL origin 的事件只能按时间、PID、task id 和调用来源近似关联。日志未匹配不代表没有发生。
- 固定窗口未捕获 `zsxq-insights overview`、`topics/local`、`topics/local/tabs` 前台请求；本轮只在重启后做真实冷/热烟测，不能据此形成固定窗口 P95。

## 与上一轮量化对比

| 指标 | 上一轮 | 本轮 | 变化 | 判断 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL | 816 | 408 | -50.0% | 改善，流量同时下降 |
| 慢 SQL P50/P95 | 1.550/5.525s | 1.650/5.203s | +6.5%/-5.8% | 中位恶化、长尾改善 |
| 慢 SQL 最大/累计 | 16.206/1784.288s | 18.691/930.176s | +15.3%/-47.9% | 最大恶化 |
| HTTP 请求/失败 | 5296/48 | 1922/13 | -63.7%/-72.9% | 流量与失败均下降 |
| HTTP 失败率 | 0.91% | 0.68% | -0.23pp | 改善 |
| HTTP P95/最大 | 2.398/25.865s | 1.963/24.369s | -18.1%/-5.8% | 改善，最大仍高 |
| HTTP 累计 | 2729.635s | 833.855s | -69.5% | 流量相关，不等同闭环 |
| 连接长持有 | 148 | 65 | -56.1% | 改善 |
| 长持有 P95/最大 | 18.826/58.939s | 20.846/88.557s | +10.7%/+50.3% | 恶化 |
| PgBouncer unique query timeout | 6 | 6 | 0 | 反复，未闭环 |

重点表慢 SQL：`publication_tasks` 85（-75.1%）、`zsxq_sync_runs` 8（+166.7%）、`zsxq_topics` 80（-22.3%）、`zsxq_topic_comments` 71（-24.5%）、`zsxq_attachments` 23（-37.8%）、`external_documents` 3（-40.0%）。`zsxq_sync_runs` 是唯一明显恶化项，需结合后台同步批次数而非仅看次数。

固定窗口中 app SQL error、statement timeout、lock timeout、connect timeout、deadlock、idle in transaction 均为 0；但 app 有 11 条 server closed，PgBouncer 18 条原始 query timeout 去重后为 6。未捕获 idle in transaction 不能替代数据库侧持续监控。

## 主要问题排序

排序综合累计、P95、最大、失败率、用户/页面影响和复发次数。

| 优先级 | 指纹/问题 | 次数 | 累计/P95/最大 | 影响与复发 | 状态 |
| --- | --- | ---: | --- | --- | --- |
| P0 | `0b4ccc892361a813` topic batch update | 36 | 119.760/6.188/7.187s | ZSXQ 同步；连续多轮反复 | OPEN/OBSERVING |
| P0 | `60ff7467696400d3` scheduler config | 64 | 113.138/3.009/7.585s | 调度器轮询；executor 0.027ms | OPEN/OBSERVING |
| P0 | `ZSXQ-AUTO-PUBLISH-PREVIEW-SESSION` | 2 | hold 129.616s；最大 88.557s | 内容流水线、七牛、连接池；DB Session 簇复发 | 修复已加载，待真实发布 |
| P0 | PgBouncer query timeout | 6 unique | wait P95 104ms、query P95 139ms | 全站网络链路 | OPEN |
| P1 | `548d10cff60cb3fd` | 14 | 36.255/4.274s | ZSXQ topic 更新 | OBSERVING |
| P1 | `9da4aded…` audio assets | 2 | 20.672/18.497/18.497s | MP3 音频素材页；executor 55.619ms，正文最大 92 字节 | OPEN/OBSERVING，网络长尾 |
| P1 | `06d65…` comment snapshot | 1 | 18.691s | 后台同步，大 IN 参数集 | OPEN，需下次批次归因 |
| P1 | `CELERY-INSPECT-SERIAL-3X` | 固定窗口 5 请求 | 29.945s；旧最大 24.369s | 状态页 | 新运行时已替换，观察 |

## 页面/API、SQL 与 Session 关联

| 端点 | 请求/失败 | 累计/P95/最大 | 匹配慢 SQL | 归因 |
| --- | ---: | --- | --- | --- |
| MP3 jobs detail | 358/0 | 232.848/2.276/5.868s | 31 条，54.821s | 轮询量最大，任务 JSON/网络往返仍需观察 |
| platform credentials | 157/0 | 99.235/2.389s | 有 | 远端窄查询网络长尾 |
| Bilibili upload POST | 12/0 | 42.982/6.626/8.515s | 少量 | 上传外部 I/O 主导 |
| report audio jobs | 87/0 | 36.688/1.922s | 有 | finalization 旧指纹未再主导 |
| MP3 audio-assets | 24/0 | 35.332/2.747/18.580s | 20.672s | 单次 SQL 18.497s，选择 `topic.content` 后只返回 180 字摘要 |
| Celery status | 5/0 | 29.945/24.369s | 最大样本无慢 SQL | 固定窗口起点旧实现；新 PID 已验收 |
| report audio detail | 17/0 | 26.451/3.061/5.379s | 有 | 低流量仍有网络长尾 |
| report audio status | 12/0 | 12.571/1.692/2.464s | 有 | 轮询较上一轮明显下降 |

低频高延迟：固定窗口唯一 `>10s` 前台样本是旧 Celery status 24.369 秒。页面很慢但没有慢 SQL：该样本由控制面 inspect 等待造成；新代码加载后的三次真实请求已降到 `1.503/0.030/0.109s`。后台/连接持有但没有前台端点：自动发布两次 checkout 与七牛上传同进程同时间线，分别持有 41.059/88.557 秒。

重启后真实 ZSXQ 验收：Insights 冷请求 2.832 秒（主题聚合 SQL 1.681 秒、轻量摘要 0.322 秒、详情 0.303 秒）；topics/local 0.832 秒；tabs 0.232 秒。sync scheduler status 首个冷连接请求 8.210 秒，其中凭据 SQL 1.508 秒、配置 SQL 2.089 秒、rollback/check-in 网络等待约 3.676 秒；同进程随后三次为 `0.441/0.294/0.195s`，无长持有日志，判定为冷连接/PgBouncer 网络抖动而非漏释放，不盲目加索引。

## 后台任务

| 任务族 | 完成/重试/失败 | 成功累计/P95/最大 | 判断 |
| --- | ---: | --- | --- |
| 全部 Celery | 373/3/0 | 14192s | 无失败风暴 |
| ZSXQ sync | 22/3/0 | 6589/609/801s | 外部页面、下载与数据库混合长任务 |
| report audio | 27/0/0 | 最大 554s | 父子等待仍需窗口复核 |
| MP3 workflow | 25/0/0 | — | 上轮 ImportError 本窗口未复发 |
| PDF clean | 5/0/0 | — | 本地 CPU/I/O，无前台端点 |
| report video | 89/0/0 | — | 无失败风暴 |
| publisher | 全部成功 | — | 外部上传耗时不应持有 DB Session |

连接生命周期共 65 条，P50/P95/最大 `7.191/20.846/88.557s`，SQL 累计 535.130 秒、非 SQL 累计 153.472 秒。3 条 managed checkout 无 SQL 关联，另有 8 条 adhoc role unknown；不能把 role unknown 自动归为业务问题或无问题。

## 修复证据链

### ZSXQ 自动发布预览 Session

- 修复前：`logs/scheduler.log` 的进程 PID 19869，checkout origin `_db_publish_cookie_account_count`；09:00:23 持有 41.059 秒、SQL 104.94ms、非 SQL 40.954 秒，09:01:55 持有 88.557 秒、SQL 115.39ms、非 SQL 88.441 秒。中间为七牛上传/重试，09:00:03 与 09:00:47 出现 PgBouncer timeout。
- 代码：账号数 helper 在 `finally` 归还 scoped Session；仅传递整数，不改字段、审计数据或发布语义。
- 定向 TDD：旧实现上 `remove` 期望 1 次、实际 0 次；修复后通过。相关两个测试模块 11 项通过。
- 完整验证：标准 `PYTHONPATH` 环境完整 unit 1116 项通过，compileall、diff、Bug 严格门禁通过。
- 运行态：重启安全门通过，backend PID 16597、全部 worker/scheduler 重载，health 为 `healthy`。
- 未闭环：没有修复后真实自动发布批次；下一轮必须捕获 count check-in 早于七牛上传。

### 上轮 Celery inspect 与运行时混版

- 第二层并行 inspect 已由本次完整重启加载；真实 status 三次 200，最大 1.503 秒，字段完整，无长 checkout。
- 本窗口 25 条 MP3 workflow 全部完成且 `DEFAULT_INITIAL_SCRIPT_MIN_CHARS` ImportError 为 0。
- 两项均需新 PID 后完整 24 小时无 timeout/ImportError 才能从 `OBSERVING` 关闭。

## 数据库与索引证据

| 表 | 总大小 | live/dead | HOT | index hit | 判断 |
| --- | ---: | ---: | ---: | ---: | --- |
| `zsxq_topics` | 126MB（13/78/34MB） | 11883/568 | 0.76% | 99.67% | TOAST/宽字段与低 HOT，非缺索引 |
| `publication_tasks` | 120MB（17/91/12MB） | 12905/503 | 1.37% | 99.81% | JSON/宽行写放大 |
| `zsxq_topic_comments` | 118MB | 41254/5149 | 69.10% | 99.93% | 命中良好，单次大 IN 长尾待复核 |
| `zsxq_attachments` | 72MB | 33310/2326 | 0.12% | 99.95% | 写放大，已 autovacuum |
| `zsxq_sync_runs` | 35MB | 936/58 | — | 99.94% | 本轮慢数恶化，需按批次归因 |
| `external_documents` | 6.944MB | 1786/146 | 0% | 98.94% | 表小，不支持盲目索引 |

无锁等待、无 active/waiting 查询、无 idle in transaction；7 个会话均 idle。超时配置为 statement 60 秒、lock 5 秒、idle-in-transaction 60 秒。

代表 EXPLAIN executor：publication point `0.044ms`、chart `2.207ms`、scheduler config 2 行 `0.027ms`、publisher list `0.059ms`、content list `0.278ms`、完整 JSON 20 行 `0.027ms`、窄投影 `0.050ms`、sync runs 120 行 `1.782ms`、Insights 6605 行 `37.673ms`。Insights 专用索引 valid/ready/live，累计 scan 3216。应用侧秒级与 executor 毫秒级差异继续指向 PgBouncer/网络、连接冷启动、数据搬运和往返。

未发现重点表定义完全相同的新重复索引。低使用索引没有足够写放大/查询证据可无人值守删除。唯一待人工动作仍是 `docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql`；脚本含 lock/statement timeout、验证和回滚，必须在 DBeaver 维护窗口逐条执行。本轮未执行 CREATE/DROP INDEX、ALTER、回填或生产配置。

MP3 audio-assets 18.497 秒查询确实选择 `zsxq_topics.content`。报告后同范围只读复核确认 3,080 条关联正文平均/P50/P95/最大均为 92 字节，首批 40 条正文仅 3,680 字节；同形查询 `EXPLAIN (ANALYZE, BUFFERS)` execution 为 55.619ms。因此该样本不能归因于大字段搬运或缺索引，仍应归入 PgBouncer/网络长尾。代码仍将正文来源投影限制为 720 字符，作为接口只返回 180 字摘要的有界防线，而不是根因闭环声明。

## 测试与门禁

- 定向 TDD：失败一次后修复；相关 11 项通过。
- 首次完整发现命令未带项目 `PYTHONPATH`，1113 项运行、2 个模块收集失败（`No module named services`）；如实保留该环境错误。
- 标准命令 `PYTHONPATH=src/backend ...`：1116 项，43.785 秒，全部通过。
- `compileall -q src/backend tests`：通过。
- `git diff --check`：通过。
- `review_bug_records.py --strict`：160/160 条归类，9 个复发簇，通过。
- recurrence registry JSON：通过解析。
- restart safety：无不可中断任务；完整 dev stack 重启成功；health 200/healthy；真实 Celery 与四条 ZSXQ API 验收见上。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `ZSXQ-AUTO-PUBLISH-PREVIEW-SESSION` | count helper 遗留只读 Session，跨七牛 I/O | finally remove + SQLite 顺序契约 | 新 PID/worker 已加载，尚无真实发布 | DB Session 同类复发 | 真实批次证据缺失 | count check-in 早于上传；hold/timeout=0 |
| `CELERY-INSPECT-SERIAL-3X` | 三类 inspect 串行广播 | 独立连接并行 | 真实三次最大 1.503s | 本轮旧样本出现 | 缺完整新 PID 窗口 | 24h status P95<10s、timeout=0 |
| `CELERY-STATUS-AUTH-SESSION` | 鉴权 Session 跨 broker I/O | inspect 前 release | 新 PID 无跨 I/O checkout | 未见新复发 | 仍需窗口 | server closed/SQL error/hold=0 |
| `RUNTIME-TOPOLOGY-RELOAD` | 共享模块与旧 worker 混版 | 完整安全重启 | MP3 25/25 完成，ImportError=0 | 本轮未见 | 缺代码版本 fail-closed | 再一窗口为 0 |
| `0b4ccc892361a813` | 页级批写叠加网络长尾 | 既有批量写入 | 36 次、累计119.760s | 反复 | P95 6.188s | P95<3s、无重试风暴 |
| `60ff7467696400d3` | 窄配置轮询受网络往返放大 | 既有投影/缓存 | executor 0.027ms | 反复 | app P95 3.009s | P95<1s、timeout=0 |
| `9da4aded…` | PgBouncer/网络长尾；非 executor、索引或大字段 | 正文投影限制 720 字符 | executor 55.619ms，正文最大 92 字节 | 单次样本 | 网络根因未闭环 | 后续窗口 timeout/crash=0 |
| `06d65…` | 660 comment ids 大 IN + 网络长尾 | 暂无盲目 DDL | 单次18.691s | 新增 | 单样本 | 同批次 EXPLAIN、参数规模与下一批 P95 |
| PgBouncer timeout | 隧道/冷连接/网络抖动 | 既有路由与池防线 | unique 仍为6；冷后热请求恢复 | 反复 | 全站风险 | unique=0，冷请求与executor分段稳定 |
| comment hash cleanup | 等价/低价值索引治理 | DBeaver 脚本已备 | schema preflight 仍提示 | 持续 | 人工锁/所有权风险 | 人工执行、验证和回滚证据 |

## 下一轮明确验收计划

1. 第一优先捕获真实自动发布：按 PID/任务/时间串联 account count、Session check-in、七牛上传和重试，确认无 `ZSXQ-AUTO-PUBLISH-PREVIEW-SESSION` 长持有。
2. 固定新 PID 后完整 24 小时复核 Celery status：P95 `<10s`，PgBouncer timeout、server closed、业务快照 SQL error、跨 inspect checkout 均为 0。
3. 对 `0b4ccc…`、`60ff…` 连续反复指纹按累计与 P95复核；若再恶化，继续追调用频率、批次大小、连接冷启动和网络路由，不以索引掩盖。
4. 持续验收 audio-assets 的 720 字符有界投影；已完成同参数 EXPLAIN 和字节量复核，不再以大字段或缺索引解释 18.497 秒样本。
5. 对 comment snapshot 记录 topic/comment id 数、SQL 文本长度、executor 与传输耗时；再次达到 10 秒才考虑临时表/分块/索引方案。
6. 累积至少 5 个 Insights 冷样本，目标 P95 `<5s`、最大 `<10s`；同时复核 topics/local、tabs、sync/status，而非只看已知端点。
7. 人工索引脚本执行前重新采集 pg stats 和 EXPLAIN；无人值守巡检继续禁止生产 DDL。

在真实发布批次和后续完整窗口满足上述条件前，主要根因保持 `OPEN/OBSERVING` 或 `RUNTIME_REPLACED/OBSERVING`，不标记 `CLOSED`。

## 报告后修复与回滚验收（2026-08-12 09:35）

- 先以临时 SSH 隧道做隔离 A/B：候选 PgBouncer 30/30 成功，P95 301.8ms；现有 Tailscale 路径 30/30，P95 470.5ms。完整本机栈切换后，真实 PostgreSQL 直连出现 5 秒 timeout，故立即自动回滚，未继续承载业务。
- 回滚后 `DIRECT_DATABASE_URL/Redis/Celery` 恢复 `100.92.99.39`，PgBouncer server 也恢复 Tailscale；数据库两条路径、三个 Redis 用途和本机 `/health` 全部成功。试验 LaunchAgent 已停止，服务器受限隧道公钥已删除。
- 最终采用较小变更：保持 `min_pool_size=6` 与 `server_idle_timeout=600`，把 `server_lifetime` 从 21,600 秒降为 300 秒，定期淘汰跨 Tailscale 的陈旧后端 socket，同时不牺牲热池容量。
- 配置生效后 100 次、10 并发只读请求 100/100 成功，P50/P95/最大 156.6/338.6/371.3ms；`cl_waiting=0`，业务池 6 条 idle server 均为 `100.92.99.39 <- 100.96.3.12`。
- MP3 真实列表调用返回 10 项、扫描 40 行，SQL 420.09ms；`topic_content_excerpt` 全部存在且最长 92 字。最终相关定向测试 136 项全部通过；完整 unit 在 659 passed、2 skipped 后卡入既有 SSL 外部等待，人工中止并如实保留，未把它写成全量通过。
- 300 秒轮换生效后的后续窗口仍出现 4 次 `server conn crashed?`：两次 TLS receive timeout 对应 socket 年龄约 196 秒，另两次对应约 46/426 秒。这证明轮换只缩短故障 socket 寿命，未修复 Tailscale/TLS 路径根因。
- 隔离的无 TLS PgBouncer 候选池 100 次请求均未建立服务端连接；PostgreSQL 明确返回 `no pg_hba.conf entry ... no encryption`。候选池已停止，未改动正式 6432 链路，也未放宽 HBA 的加密要求。
- 本轮仍未执行生产 DDL。网络问题状态为 `RUNTIME_MITIGATED/OPEN`，必须由下一完整 24 小时窗口确认 query timeout/server crash 为 0 后才能转为观察闭环。
