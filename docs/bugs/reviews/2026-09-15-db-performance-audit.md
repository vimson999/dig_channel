# 2026-09-15 每日数据库性能与错误巡检

## 结论

固定窗口 `2026-09-14 06:00:00` 至 `2026-09-15 06:00:00`（Asia/Shanghai）内，应用慢 SQL
从上一窗 24 条/35.476 秒升至 132 条/223.626 秒，数量增加 450.0%、总耗时增加 530.4%；P95 从
3.198 秒降至 2.933 秒，但最大值由 3.562 秒升至 19.799 秒。PgBouncer 同窗出现 4 个去重
`query_wait_timeout` episode，应用侧对应 4 个 `server closed the connection unexpectedly` 事件；它们集中在
13:01:38–13:02:40，并命中通知 drain、研报音频恢复、Publisher 终态子任务清理和 pending scanner。

这是既有 `DB-SESSION-LIFECYCLE` 网络/连接池尾延迟簇的明确复发，不是索引或 Session 泄漏：8 次长连接持有
全部匹配慢 SQL，SQL 合计 100.503 秒而非 SQL 仅 1.597 秒；事故后代表查询 PostgreSQL executor 仅
0.025–35.0ms、无 waiting lock、无 idle-in-transaction，精确重复索引为 0。未新增/删除索引，未提高 timeout，
未自动修改 PgBouncer、Tailscale 或生产数据库配置。

HTTP 完成 350 次、342 次成功；8 次失败全部为 404，无 5xx。Celery 110 次成功、1 次失败；唯一失败为
Publisher 子任务引用的本机音频路径不存在，父 MP3 流程和同批其他 Publisher 子任务均已完成，和数据库事故
不在同一时间或根因链。本轮没有足够证据支持新的产品代码修复。

## 与上一窗口对比

| 指标 | 2026-09-14 | 本窗口 | 变化 |
| --- | ---: | ---: | ---: |
| 应用慢 SQL | 24 | 132 | +450.0% |
| 应用慢 SQL 总耗时 | 35.476s | 223.626s | +530.4% |
| SQL P50 / P95 / max | 1.090 / 3.198 / 3.562s | 1.271 / 2.933 / 19.799s | P95 -8.3%，max +455.8% |
| HTTP 完成 / 成功 / 失败 | 413 / 410 / 3 | 350 / 342 / 8 | 请求 -15.3%，失败率 0.73%→2.29% |
| HTTP P50 / P95 / max | 3.32ms / 1.378s / 5.672s | 50.16ms / 1.890s / 13.900s | P95 +37.2%，max +145.1% |
| Celery 成功 / 失败 | 148 / 0 | 110 / 1 | 成功 -25.7%，新增 1 个业务失败 |
| PgBouncer wait P95 | 43.65ms | 101.64ms | +132.9% |
| PgBouncer query P95 | 72.77ms | 179.04ms | +146.0% |

另有 1 条 1.206 秒慢 SQL 来自 ad-hoc 诊断，已与 132 条托管应用样本分栏；全口径共 133 条/
224.832 秒。

## 慢 SQL、Session 与页面链路

- 通知 provider drain 30 次/45.953 秒，Publisher 终态子任务清理 8 次/28.003 秒，Publisher processing
  scanner 14 次/26.198 秒，研报音频失败恢复 10 次/20.994 秒，Publisher pending scanner 11 次/
  16.037 秒。其次为火柴人 repair 8 次/10.447 秒、业务 processing repair 7 次/9.947 秒、ZSXQ
  all-tabs 配置读 6 次/8.381 秒和音频转写详情 5 次/7.743 秒。
- 最大 SQL 为 Publisher 终态子任务清理 19.799 秒；研报音频失败恢复最大 9.291 秒，processing scanner
  最大 4.970 秒。慢查询同时跨越点查、扫描器和通知表，不能由单表缺索引统一解释。
- 8 个 connection hold 的 P50/P95/max 为 15.294/19.854/19.854 秒；scheduler 7 个、worker 1 个。
  8/8 都有同连接慢 SQL，SQL 占 100.503 秒，非 SQL 仅 1.597 秒，没有“外部 I/O 持有 Session”样本。
- runtime guard 只记录 4 个短 episode，最大连接 2、事务龄 0.820 秒、idle 龄 0.509 秒，pressure defer 为
  0；现场 activity 为 9 个 idle ClientRead、0 个 idle-in-transaction。

## 13:01 连接事故证据链

1. PgBouncer 在 13:01:38、13:01:54、13:02:09、13:02:24 先后接受客户端，约 15 秒后连续以
   `query_wait_timeout` 关闭；日志有 8 行 client/server 镜像，去重后为 4 个 episode。
2. 同时段应用的通知 drain、研报音频恢复、Publisher 终态子任务清理和 pending scanner 均出现约 15 秒 SQL
   等待并收到 `server closed the connection unexpectedly`；分析器归并出 3 个显式 SQL error 指纹和 1 个
   traceback SQL episode，而不是把同一 traceback 重复计数。
3. PgBouncer 13:02 的一分钟吞吐归零；13:03 恢复时 wait/query 峰值为 961/1,313ms，应用第一条恢复查询为
   4.970 秒。全窗 wait/query P95 分别升到 101.64/179.04ms。
4. PostgreSQL 现场无等待锁，代表 SQL executor 只有毫秒级；PgBouncer 当前 `cl_waiting=0`、6 条 Tailscale
   server socket 已建立。当前恢复快照不能覆盖事故分钟，但排除了持续锁队列、池容量长期耗尽和稳定慢计划。
5. 目标 `100.92.99.39:5432` 当前路由仍为 `utun5`、MTU 1280，本机地址 `100.96.3.12`；本地 6432 和远端
   5432 TCP 均已恢复。结合历史 30% 丢包与 query timeout，根因边界继续落在 Tailscale/物理出口/远端网络
   与连接池可用性，而不是 PostgreSQL executor。

状态保持 `OPEN / REPEATED / OBSERVING`。不以事后 socket 可连、一次健康查询或增加 timeout 作为修复。

## HTTP、Celery 与业务状态

- 350 个 HTTP 请求的 P50/P95/max 为 50.16ms/1.890s/13.900s，总耗时 160.342 秒。最重路径为音频转写
  job 详情 43 次/42.059 秒，其中 5 条慢 SQL 共 7.743 秒；`report_audio_workflow` 重启 4 次/
  34.096 秒、P50 9.623 秒但没有匹配慢 SQL，属于进程控制时延。
- 8 个失败全部为 404：favicon 3 次，音频转写 job 详情 2 次，音频转写 status/list 各 1 次，
  `report_audio_workflow` worker status 1 次；无 5xx、无数据库错误响应。
- 固定窗未观察到 ZSXQ 页面 API 请求；后台有 1 个 `tasks.zsxq_publish.run_publish_clean_output_task` 自然成功，
  耗时 366.251 秒。此覆盖不足以替代 ZSXQ 页面冷链验收。
- Celery 自然成功 110 次：通知 100、音频转写 5、PDF 清洗 1、音频清洗 1、ZSXQ 发布 1，另有 Publisher
  任务日志。PDF 清洗耗时 710.358 秒，但没有数据库失败重试。
- 唯一 Publisher pipeline 失败 `b52f7175…` 在 10:44:37 因音频路径不存在得到
  `No valid audio clips`。其父任务 `2f5eee8b…` 为 completed/done；同父的 `82cbe399…`、`9af14465…`
  等子任务完成。该失败发生在数据库事故前约 2 小时 17 分钟，SQL 均为 30–362ms，故单列为素材/运行拓扑
  异常，不归入数据库根因，也不在证据不足时自动重投。
- 分析器把 `run_bilibili_upload_only` 的 1 次 1.781 秒 stale-guard skip 记为 Celery success；该 delivery 是
  queue repair CAS loser 被撤销后到达，并非真实投稿。业务验收必须以数据库 BVID/终态和 worker 真实上传链为准。

## Executor、索引与膨胀

- `publication_tasks` 135MB，live/dead 13,548/2,262，index scan 99.79%；dead 较上一现场增加 631，尚无
  autovacuum 落后、读计划退化或写放大证据，继续观察而不手工 VACUUM。
- `zsxq_topics` 129MB，live/dead 12,637/1,584；`zsxq_topic_comments` 118MB，live/dead
  41,333/5,644；两者 index scan 分别 99.54%/99.88%。附件、sync run、external document 的 index
  scan 也为 98.78%–99.95%。
- 生产只读 `EXPLAIN (ANALYZE, BUFFERS)`：publication point 0.034ms、chart 3.237ms、配置点查
  0.027ms、Publisher 列表 0.076ms、内容列表 0.193ms；sync run 小表顺扫 1.555ms，Insights 聚合顺扫
  12,637 行为 34.993ms。均远低于应用秒级长尾。
- 精确重复索引为 0。低扫描索引统计自 2026-08-08 数据库启动以来累积，缺少完整查询收益、写放大和长期
  使用证据；本轮不删除。`pg_stat_statements` 未启用，不能把日志指纹次数冒充服务端全量调用频率。
- 角色/数据库保险仍为 statement 60 秒、lock 5 秒、idle transaction 60 秒；诊断连接只读并使用 15 秒
  statement timeout。未执行生产 DDL 或写 SQL。

## Publisher 后窗与既有问题

- 固定窗新增 8 条带结构化 Bilibili projection 的 Publisher 任务，全部 `completed/done`、有效 BVID、
  `error_message=NULL`、queue repair count=0；但它们是完整 Publisher pipeline，不是可证实的自然
  upload-only delivery。
- 本机 `platform_upload` 日志只看到一个 CAS-loser stale skip；共享 nodename/多消费者日志盲区仍存在。
  因而 2026-09-14 的上传 orphan grace P0 和 2026-09-13 的 upload-only 成功清错 P1 均保持
  `RUNTIME_REPLACED/OBSERVING`，不能用完整 pipeline 样本提前关闭。
- 两次 pending repair 在发布替代消息后发生 CAS loser，替代 delivery 被撤销或由 current-id guard 跳过，
  对应任务最终由当前 ID 完成且 repair count 保持 0；这支持 CAS 防线生效，但没有直接覆盖“原消息 queued/
  unacked 可见时不得换 ID”的自然分支，2026-09-11 记录继续观察。

## 验证状态

- 固定窗分析器回放完成，并单独核对 404 状态、PgBouncer episode 去重、Publisher durable 终态和 Bilibili
  projection；06:07 之后的巡检只读查询均在固定窗之外，不污染本轮比较。
- PostgreSQL 现场报告、`SHOW POOLS/SERVERS/STATS`、路由和 TCP 可达性均为只读；没有锁、idle transaction、
  duplicate index 或持续 pool wait。
- 分析器定向回归 30/30 通过；完整 unit 首次在沙箱内仅因禁止绑定 loopback 得到 34 个
  `PermissionError`，允许本地回环后原命令复跑为 `Ran 1675 tests in 74.305s / OK`。
- worker app-factory 边界 28 文件、Celery revoke 边界 321 文件、严格 Bug registry 199/199、JSON、
  `compileall` 与 `git diff --check` 均通过；当前 `/health` 返回 `healthy`。
- 本轮不改产品代码、不做 DDL、不重启服务；既有托管进程继续运行，避免在没有修复载荷时制造无意义中断。

## 当前状态与下一轮

- P0：PostgreSQL/PgBouncer 网络尾延迟 `OPEN/REPEATED/OBSERVING`。下一轮要求保留事故分钟的 pool/server
  socket、route、远端 activity 与链路质量，不能只取恢复快照。
- P1：Publisher upload-only 两条防线 `RUNTIME_REPLACED/OBSERVING`。等待自然 upload-only 成功，要求无
  提前 orphan close、成功终态清空错误、队列为 `platform_upload`。
- P2：`b52f7175…` 素材不可见失败作为独立运行拓扑观察项；若同一 durable 本地路径再次只在部分 render
  consumer 不可见，再按共享存储/队列亲和性建立新记录。
- 无索引建议、无 timeout 调整、无历史状态伪修正；生产 DDL 如未来出现充分证据，只能输出人工 migration/
  SQL 建议并由管理员执行。
