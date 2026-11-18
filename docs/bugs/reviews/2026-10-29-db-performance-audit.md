# 2026-10-29 数据库性能与错误巡检

## 根因闭环结论

- 窗口：2026-10-28 06:00:00 至 2026-10-29 06:00:00（Asia/Shanghai）。
- **整体不能标记已解决。** 应用慢 SQL 29 条/67.720 秒，较上一窗 18 条/58.064 秒增加
  61.1%/16.6%；P50 由 1.560 秒改善到 1.222 秒，P95/max 由 8.938/11.133 秒恶化到
  11.133/12.993 秒。
- 音频资产目录 3 次冷建累计 33.761 秒。同形生产 executor 仅 6.527ms；同一连接的 execute/fetch
  需 8.967～9.411 秒，Python materialize 仅 7.48～9.17ms。根因已收窄到驱动/PgBouncer/私网结果接收，
  但缺自然尖峰同秒 socket/route 证据，保持 **OPEN / REPEATED / OBSERVING**。
- YouTube channel/video 两条自然任务均 0/51，安全错误合同首次给出
  `endpoint=channels type=SSLError`；诊断修复已运行替换，业务 TLS/代理/网络根因仍 OPEN。
- 23 个目标 durable task 均 current-root，Publisher 16/16 completed；但远端 MacBook 生产 DB 会话由
  5 个增至 7 个，分布式运行体隔离仍未闭环。
- 本轮没有支持安全业务修复或生产 DDL 的证据，没有用 timeout、重试、TTL 或盲目索引掩盖根因；只更新
  报告、Bug 与复发登记，运行代码不变。

## 修复前后量化基线

| 指标 | 上一窗 | 本窗 | 判断 |
|---|---:|---:|---|
| 应用慢 SQL | 18 | 29 | 恶化 +61.1% |
| 累计耗时 | 58.064s | 67.720s | 恶化 +16.6% |
| P50/P95/max | 1.560/8.938/11.133s | 1.222/11.133/12.993s | 中位改善，尾部恶化 |
| HTTP 请求/失败 | — | 1,064/10 | 失败均为 `/favicon.ico` 404 |
| HTTP 累计/P95/max | — | 357.256/1.027/50.275s | 含外部同步 |
| SQL error/statement/lock/connection timeout | 0/0/0/0 | 0/0/0/0 | 无新增错误，不代表根治 |
| PgBouncer 八类错误/fallback | 0/0 | 0/0 | 无新增错误 |
| PgBouncer wait P50/P95/max | 24.15/49.25/103.31ms | 23.90/45.61/176.57ms | P95 改善，max 恶化 |
| 远端 MacBook DB 会话 | 5 | 7 | 隔离恶化 |

日志另有 1 条巡检自身 ad-hoc SQL，已从应用口径剔除。20 条短暂 `idle in transaction` 观察的最大
transaction/idle 仅 0.770/0.740 秒、峰值 2 连接，没有持久压力；单次快照未见等待不能证明全窗从未发生。

## 主要问题排序

排序综合累计耗时、P95/max、失败率、用户影响和复发次数，不只按出现次数。

1. **YouTube 全量同步：** channel 0/51、57.16 秒；video 0/51、96.63 秒，失败率 100%，影响全部来源。
2. **音频资产目录：** 3 次/33.761 秒，P50/P95/max 11.133/12.993/12.993 秒；8 个页面请求累计
   36.317 秒，P95/max 14.269 秒，同指纹连续复发。
3. **ZSXQ 发布：** 1 个 durable task 最终完成但耗时 1,005.931 秒；10 个 upload failure、6 个 publish
   retry、1 个 verify pending，较上一窗 5 次 `1059` 失败恶化。
4. **远端旧运行体隔离：** 自然任务全绿，但远端仍有 7 个空 `application_name` 连接；失败率为零不消除
   共享凭据和竞态风险。
5. **长后台任务：** Publisher pipeline 8 次/1,558.531 秒，P95/max 440.480 秒；upload-only
   8 次/1,498.716 秒，P95/max 494.751 秒；MP3 process 40 次/427.067 秒，P95/max 47.063/57.138 秒。
   171 个 Celery task 全完成、retry/failed 为 0，当前主要是外部 I/O/媒体处理而非 executor。

## 页面/API/任务与 SQL 关联

| 页面/API/任务 | 结果 | 累计/P95/max | SQL/Session 关联 | 结论 |
|---|---|---|---|---|
| `GET .../audio-assets` | 8/8 | 36.317/14.269/14.269s | 3 条 SQL 33.761s；3 hold 的 SQL 33.882s、非 SQL 0.675s | 冷缓存 fetch 尾部复发 |
| `POST /api/v1/videos/sync` | 1/1 | 50.275s | 无慢 SQL | 外部调用；API 慢但无慢 SQL |
| `POST /api/v1/channels/sync/all` | 1/1 | 18.763s | 无慢 SQL | 外部调用；内部 0/51 |
| content pipeline submit | 1/1 | 10.446s | 无慢 SQL | 后台投递/外部链路 |
| web submissions | 78/78 | 34.484/0.881/2.106s | 无慢 SQL | 高频但无 DB 尖峰 |
| MP3 jobs | 125/125 | 24.964/—/1.442s | 无慢 SQL | 状态查询稳定 |
| insights overview | 1/1 | 0.842s | 固定窗无 >=1s SQL；代表 executor 40.371ms | 本窗未复发，不等于关闭 |
| topics/local、tabs、sync/runs、scheduler | 均成功 | 0.528/0.195/0.236/0.350s | 无长 hold | 自然样本稳定 |

低频高延迟是两条 YouTube POST、content submit 和长 Publisher/ZSXQ task；API 很慢但无慢 SQL 是前三条
HTTP；无前台端点的长任务/连接包括 ZSXQ、Publisher 和远端 7 个 DB 会话。3 个前台长 hold 均匹配音频 SQL，
未发现长 hold 无 SQL 的新增样本。

## 音频目录根因证据链

- 指纹 `be891b5c1af362da`，origin `services/mp3_video_workflow.py:495`；三次返回 1,511/1,541/1,541 行。
  request `1e147647`、`af84306f`、`9d7c3f48` 的请求耗时为 14.269/10.283/11.735 秒。
- Session hold 13.616/9.731/11.210 秒；非 SQL 仅 502.02/96.07/76.95ms，文件检查没有跨 Session。
- 当前同形查询为 1,541 行、约 881,038 bytes，Seq Scan 4,599 行、516 shared read、sort 1,461KB；
  `EXPLAIN (ANALYZE, BUFFERS)` executor 6.527ms。
- 同一 psycopg2 连接：connect 354.84ms；三次 execute/fetch 8,966.82/9,365.19/9,410.90ms；Python
  materialize 8.95/7.48/9.17ms。慢点位于 executor 后、Python 对象物化前。
- 既有字段投影、2,500 行上限、single-flight、300 秒缓存、Session 外文件校验仍生效：8 个请求只产生
  3 次目录 SQL。投影字段均由合并/解析/响应使用，删字段会损害完整性；服务端分页会改变全局搜索语义。

因此不加索引、不延长 TTL/timeout、不删原始数据。下一自然冷建必须同秒采客户端 socket、PgBouncer server
socket、route、server activity 与 response bytes，完成 driver/PgBouncer/私网三者的物理归因。

## 程序模式复核

- 大批量：音频目录 1,541 行/881KB 最显著，但已有上限和最小业务投影；`publication_tasks` 未见应用多行
  整行 payload。巡检 ad-hoc 造成的一条 multirow payload 已剔除。
- N+1：未见音频附件 lazy-load、旧附件逐项查询或页面级新增 N+1。
- JSONB 写：两条慢写为 `persist_notification` 701.52ms、`_update_result` 967.58ms，均为目标 patch/终态，
  不是进度 `_emit` 宽写；仍反映远程写尾部，保持观察。
- 聚合：未发现新增重复大聚合；Insights 代表查询 executor 40.371ms。
- 缓存：8 次音频请求仅 3 次冷建，cache/single-flight 有效；冷建未根治。
- 轮询：web-submissions 78 次、MP3 jobs 125 次，累计和尾部未形成 SQL 尖峰。
- Session 跨 I/O：YouTube、ZSXQ、Bilibili 与媒体任务未见 Session 跨外部 I/O；音频长 hold 是结果 fetch，
  Session 外文件校验合同未复发。

## 重点表、索引与 executor

| 表 | 总大小 | live/dead | index scan | 结论 |
|---|---:|---:|---:|---|
| publication_tasks | 153MB | 14,801/2,246 | 99.84% | 7 条慢 SQL，但代表 executor 0.233ms |
| zsxq_topics | 133MB | 13,981/1,665 | 99.51% | 1 条慢 SQL；Insights executor 40.371ms |
| zsxq_topic_comments | 119MB | 45,884/6,093 | 99.84% | 0 条慢 SQL |
| zsxq_attachments | 79MB | 35,988/75 | 99.94% | 0 条慢 SQL |
| zsxq_sync_runs | 37MB | 1,077/61 | 99.90% | executor 1.547ms |
| external_documents | 7,016KB | 1,817/146 | 98.73% | 0 条慢 SQL |

目标表无完全重复索引，当前无 waiting lock；`pg_stat_statements` 未安装，rare-index 计数自 2026-08-08
server start 累积，不能作为 DROP 依据。PK、channel chart、tabs config、Publisher list、JSON 窄/宽代表查询均
为 0.024～2.935ms。**本轮不生成新的 CREATE/DROP INDEX 脚本**，因为缺少“真实慢 SQL + 独立 stats 窗口
+ 反事实 EXPLAIN”三件套。

## 外部链路和反复问题

- YouTube：两自然任务均精确为 `API network failure endpoint=channels type=SSLError`，且无 key 泄漏。
  窗后代理/直连 curl 都完成 TLS 并得到预期无 key 403，真实凭据探测成功；只证明当前恢复。永久失效 21/56
  的人工 DBeaver 脚本仍待执行，不能因全量 SSL 故障扩大停用范围。
- ZSXQ：1/1 durable publish 成功，但 10 failure/6 retry/1 verify pending；有界重试避免终态失败，不等于
  `1059` 根因消失，保持 OPEN/REPEATED。
- Bilibili：23/23 周期、161/161 competitor 成功，累计 290.868 秒，P50/P95/max
  12.420/14.422/15.784 秒；总耗时 -1.8%、P95 -5.7%，max +2.1%。历史 DNS/TUN 根因无现场证据，
  保持 OBSERVING。
- 远端运行体：23/23 durable 自然任务 current-root，但远端会话增至 7，不能把绿窗等同于旧栈失权。

## 测试与运行态验证

- 固定窗：Celery 171 completed、0 retry、0 failed；目标 durable task 23/23 current-root，Publisher 16/16；
  75 条通知 sent、max attempts=1。
- 巡检时 `/health` healthy；mp3、content_pipeline、zsxq_publish、platform_upload、zsxq_sync worker 均
  running、queue=0；YouTube scheduler PID 74825/74950 存活。
- 窗后 API：overview/topics/tabs/sync-runs/scheduler 均 200，耗时 4.204/1.165/0.406/0.485/0.689 秒。
  这只证明当前可用，不能替代固定窗或下一自然周期。
- 本轮只改文档/登记，无运行代码变更，故无需重启，也不触发重启安全门。
- 定向回归 57/57 通过；完整 unit tests 首次在受限沙箱内因禁止绑定 `127.0.0.1`/连接测试 Redis 出现
  34 个 `PermissionError`，在允许本机回环的同一环境重跑 **1,747/1,747 通过**。`compileall`、registry JSON
  解析、Bug 严格检查（216/216 已归类）和 `git diff --check` 均通过。

## 待人工 SQL、风险和未捕获边界

唯一待人工 SQL 是 `docs/sql/disable_unavailable_youtube_channels_20260920.sql`，仅可在 DBeaver 逐条复核后停用
永久失效 21/56；脚本已有超时、验证与回滚。本轮无新索引、表结构、回填或生产配置脚本。

未捕获边界：日志不能直接拆开 psycopg2 execute/fetch；没有自然音频尖峰同秒的 socket/route；没有
`pg_stat_statements`；后台任务不全有前台 request id；YouTube 故障时没有同秒 proxy/direct A/B；Bilibili
本窗未触发历史故障；单次 activity 快照不能证明全窗无短锁/瞬时等待。

## 根因闭环状态表

| 指纹 | 根因 | 已落地修复 | 运行态证据 | 复发 | 风险/责任动作 | 下一轮验收 |
|---|---|---|---|---|---|---|
| `YOUTUBE-ALL-SOURCE-SSL` | 间歇 TLS/代理/网络；物理层待定 | safe reason + key 脱敏 | 自然任务输出 `channels/SSLError` | 是，0/51×2 | 运维同秒采 proxy/direct TLS；不盲重试 | 两自然周期成功；再失败有 TLS/route 断点 |
| `be891b5c1af362da` | executor 后、materialize 前的 fetch | 投影/上限/cache/single-flight/Session 外 I/O | 3 次 33.761s；executor 6.527ms；fetch 约 9s | 是 | 采 socket/route/activity | 冷暖配对、物理分段完备、后窗不复发 |
| `ZSXQ-UPLOAD-1059` | 外部上传不稳，物理断点待定 | 有界 retry/verify | 1/1 完成但 10/6/1 失败事件 | 是且恶化 | 采 provider/upload 阶段 | 自然任务零 1059/pending，连续稳定 |
| `REMOTE-OLD-RUNTIME` | 共享凭据/broker 与身份未隔离 | actor/root 诊断和 fail-closed | 23/23 current；远端 7 会话 | 风险复发 | 停旧栈或轮换凭据、隔离 namespace | 24h remote/old/foreign 全 0 |
| `BILI-DNS-TUN` | 历史 DNS/TUN 故障未捕获 | 分片恢复/上传校验 | 23/23、161/161 | 本窗未复发 | 失败现场采 DNS/TUN/aiohttp | 后窗全绿；再失败有物理证据 |
| `PUBLICATION-TASK-JSONB-WRITE-TAIL` | 远程 JSONB patch 尾部 | scalar progress/target patch | 2 条 0.702/0.968s | 观察到 | 按 origin/phase 跟踪 | 零慢 payload write，保留审计 JSON |

## 下一轮验收计划

1. 优先关联下一 audio cold request 的 request id、hold、executor、client/server socket、route 与 response
   bytes；暖请求目录 SQL 必须为 0。
2. 复核 00:03/01:00 YouTube；SSL 再现时同秒执行 proxy/direct TLS A/B，记录 PID、代理环境与证书阶段，
   禁止仅加 retry。
3. 复核 ZSXQ `1059`/verify pending 事件数、成功率和壁钟；最终成功不能消除失败事件。
4. 对远端 `100.119.238.48`、application_name、old/foreign actor 与 broker namespace 做完整 24h 复核；
   物理隔离前保持 OPEN。
5. 继续同窗输出全页面/API/Celery/SQL；缺样本明确写 UNOBSERVED，不以零日志判绿。
