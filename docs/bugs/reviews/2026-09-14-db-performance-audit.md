# 2026-09-14 每日数据库性能与错误巡检

## 结论

固定窗口 `2026-09-13 06:00:00` 至 `2026-09-14 06:00:00`（Asia/Shanghai）内，应用 SQL 错误、
traceback、statement/lock/query/connect timeout、deadlock、idle-in-transaction 与 PgBouncer 六类错误均为 0。
慢 SQL 为 24 条/35.476 秒，较上一窗口数量增加 71.4%、总耗时增加 34.2%，但 P95/max 降至
3.198/3.562 秒。代表查询 executor 为 0.025–35.077ms、现场无锁等待、精确重复索引为 0，仍无 DDL 或
提高 timeout 的证据。

本轮发现 P0 业务状态缺陷：上传 Watchdog 用 90 秒阈值清理仍在合法执行窗口内的 Bilibili 投稿，并因
processing scanner 丢失结构化 projection 把目标队列误记为 `render`。已完成 RED→GREEN 根因修复、完整
回归和双安全门标准整栈重启；新运行态已替换，等待自然 upload-only 样本验收。

## 与上一窗口对比

| 指标 | 2026-09-13 | 本窗口 | 变化 |
| --- | ---: | ---: | ---: |
| 应用慢 SQL | 14 | 24 | +71.4% |
| 慢 SQL 总耗时 | 26.437s | 35.476s | +34.2% |
| SQL P50 / P95 / max | 1.093 / 3.400 / 4.951s | 1.090 / 3.198 / 3.562s | 尾部下降 |
| HTTP 完成 / 成功 | 758 / 758 | 413 / 410 | 3 次为预期 400/409，无 5xx |
| HTTP P50 / P95 / max | 3.06ms / 1.471s / 7.647s | 3.32ms / 1.378s / 5.672s | 改善 |
| Celery 完成 / 失败 | 205 / 3 | 148 / 0 | 零失败 |
| PgBouncer wait P95 | 44.62ms | 43.65ms | -2.2% |
| PgBouncer query P95 | 71.85ms | 72.77ms | +1.3% |

## 慢 SQL、Session 与页面链路

- 24 条慢 SQL 总计 35.476 秒。MP3 音频素材目录 2 次/6.759 秒，Publisher processing scanner
  3 次/4.785 秒，通知 provider drain 4 次/4.233 秒，ZSXQ scheduler 配置读 3 次/3.221 秒；其余包括
  ZSXQ publish 状态回写 2.540 秒、内容资产更新 1.862 秒、Insights 聚合 1.701 秒。
- 音频素材目录两次各返回 671/678 行，对应两个逻辑请求 4.201/4.254 秒，其中 SQL 占 6.759 秒；仍是
  远端传输/连接长尾主导。Publisher scanner 的现场 executor 仅 0.039ms，应用为 1.043–2.353 秒。
- 413 个 HTTP 完成请求中 410 成功。失败为两次预期 runtime-status 参数 400 与一次已运行/终态任务取消
  冲突 409；无 5xx。秒级 upload、封面重生成、背景重生成和 web-submit 多数没有对应 >1 秒 SQL。
- runtime guard 12 个短暂观测 episode，最大 3 连接、事务龄 0.578 秒、idle 龄 0.546 秒、defer 0；没有
  connection hold 事件，不能冒充 60 秒 idle-in-transaction 事故。

## Executor、索引与膨胀

- `publication_tasks` 134MB，live/dead 13,514/1,631，index scan 99.79%；`zsxq_topics` 129MB，
  live/dead 12,637/1,584；`zsxq_topic_comments` 118MB，dead 5,644 且 index scan 99.88%。
- ZSXQ Insights 代表查询顺序扫描 12,637 行、筛后 8,229 行，execution 35.077ms；sync runs 列表顺序扫描
  1,062 行，execution 1.572ms。当前规模和现场应用长尾均不支持新增索引。
- 目标表精确重复索引为 0；低扫描索引缺少稳定统计期、写放大和查询收益证据，本轮不删除。
- 现场无 waiting lock，activity 为 idle ClientRead 9、idle-in-transaction 0。服务端角色/数据库保险仍为
  statement 60 秒、lock 5 秒、idle transaction 60 秒；诊断连接使用更短 15/5/60 秒边界。

## Celery、PgBouncer 与 durable 状态

- Celery 148 次完成、0 失败、0 retry。Publisher pipeline 5 次/1206.695 秒，upload-only 5 次/833.424 秒，
  content PDF clean 1 次/1006.613 秒，ZSXQ publish 1 次/476.951 秒；均保留真实外部耗时。
- PgBouncer 1,440 个样本，六类错误与 route fallback 均为 0；wait P50/P95/max
  23.22/43.65/273.25ms，query 56.96/72.77/373.03ms，server churn 12/h。网络尾延迟保持
  `OPEN/REPEATED/OBSERVING`，不以错误为 0 或单窗稳定关闭。
- 内容资产新增 audio 7、PDF 59，均 cleaned、hash 唯一且无路径/哈希缺失；81 条 notification 全部 sent、
  attempt=1、无 dedupe 冲突。窗口内 ZSXQ sync run 为 0。
- 历史 active backlog 仍有 40 条 `images_done`、5 条 `images_queued` 等 47 条旧视觉任务；均早于本窗口，
  本轮不把旧 waiting/ready 状态伪改为终态。

## Publisher 根因与改动

- 两条 upload-only 任务 `34f36a3f…`、`65df0ec7…` 在约 90 秒时被 Watchdog 收口，之后分别于约
  3.7/3.9 分钟内取得有效 BVID `BV1s5Yv6xErZ`、`BV1C5Yv6xEAN` 并成功完成；两者仍保留 orphan 错误。
- 同窗 5 条成功 Bilibili 上传耗时为 81.6、167.7、170.2、201.1、212.8 秒，直接否定 90 秒为安全孤儿
  阈值。当前上传器硬超时为 1200 秒，因此回收者不能在执行者合法截止前写失败。
- processing scanner 没有读取 `PublicationTaskBilibiliUpload` projection，同时刻意不读 `params`；统一
  router 因此回退到 `render`，与两条数据库审计完全一致。
- 修复把 Bilibili grace 绑定为上传硬超时 + 60 秒结算余量；worker 不可见后继续检查 broker/unacked；批量
  projection task id 恢复 `platform_upload` 路由。外部投稿仍不自动重投。

## 验证状态

- TDD 三个失败场景均已复现并修复：合法执行窗、生产 scanner 路由、unacked 可见。
- queue-repair 全文件 51/51 通过；Publisher queue/API/uploader 交叉套件 161/161 通过。
- 完整 unit 在可绑定 loopback 的环境中 1675/1675 通过；app-factory 27 files、revoke 318 files、compileall、
  严格 bug registry、JSON 工具与 `git diff --check` 均通过。
- 重启安全门两次读取 46 条近期 Publisher 任务、0 条活动 sync，冻结 17 队列且未用 FORCE；新 backend
  PID `20631` healthy，bootstrap 17/17、`errors=[]`，`platform_upload` PID `20707` ready，Watchdog
  PID `21170` 已启动。四个目标 runtime status 均 running、未暂停、queue 0。
- 重启后数据库为 active 1、idle 9、idle-in-transaction 0、completed-with-error 新增 0、活动上传 0。
  当前状态为 `RUNTIME_REPLACED/OBSERVING`，因尚无新进程自然上传样本不得关闭。

## 诊断边界

- 06:14 日志中的 5 个 `server_closed` 与 1 个 traceback 均来自上一轮只读 ad-hoc 诊断进程，分析器正确
  归为 `adhoc_diagnostics`，固定窗应用事件仍为 0。
- Celery inspect 回报 27 个节点但共享相同 nodename；两条被误收口任务的 broker id 未出现在本机
  `platform_upload` received/success 日志。可能存在重复 nodename 的响应覆盖或另一消费者日志盲区，这是基于
  现场拓扑的推断，不作为已经证明的唯一根因；时间契约、队列路由和缺失 unacked 检查已有确定代码证据。

## 当前状态与下一轮

- Publisher 上传孤儿判定：`RUNTIME_REPLACED/OBSERVING`；等待自然 upload-only 成功，
  要求无早于 1260 秒的 orphan close、队列为 `platform_upload`、成功终态清空错误。
- Publisher success 清错与 queued/unacked 防线继续 `OBSERVING`；本窗两条矛盾状态发生在 Watchdog 上游误判，
  新回归同时覆盖 processing unacked。
- PostgreSQL/PgBouncer 网络尾延迟继续 `OPEN/REPEATED/OBSERVING`；不做 DDL、不提高 timeout。
- 外部副作用保持 fail-safe：不伪造结果、不盲目重投、不自动改写历史成功/失败记录。
