# 2026-08-04 数据库性能与错误巡检

## 结论

固定统计窗口为 2026-08-03 06:01:50 至 2026-08-04 06:01:50（Asia/Shanghai），覆盖 `slow_query.log`、`app.log`、`backend.log`、`scheduler.log`、全部 Celery 日志及与窗口重叠的普通文本和 gzip 轮转文件。应用慢 SQL 497 条，P50/P95/最大为 1.503/8.511/60.074 秒，累计 1,629.957 秒；较上轮数量下降 27.2%、累计耗时下降 26.5%，但 60 秒连接长尾未消失。

本轮最高优先级并非索引：2026-08-04 01:38:53 起，远端 PostgreSQL 持续拒绝当前出口 IP `223.80.177.167`，日志明确为 `no pg_hba.conf entry`。固定窗口内 PgBouncer 记录 1,035 次 `server login failed`、1,035 次 HBA reject 和 885 次缓存登录失败；应用侧队列修复 SQL 同一指纹报错 174 次。重启安全门禁也因数据库不可达退出 3，因此本轮没有重启、没有执行 DDL，也没有把无法取得的实时表/索引统计误报为健康。

## 量化证据与基线对比

| 指标 | 本轮 | 上轮 | 判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 497 | 683 | -27.2%，改善 |
| P50 / P95 / 最大 | 1.503 / 8.511 / 60.074s | 1.519 / 8.590 / 60.367s | 中位与 P95 略改善，60s 长尾反复 |
| 应用慢 SQL 累计 | 1,629.957s | 2,216.962s | -26.5% |
| ad-hoc 慢 SQL | 11 / 145.490s | 52 / 已隔离 | 不纳入应用排名 |
| 连接持有 | 79，P50/P95/max 10.513/60.160/60.874s | 106，P95 60.600s | 数量 -25.5%，长尾未改善 |
| 托管进程无匹配慢 SQL持有 | 20，P95/max 60.469/60.811s | 28，最大 61.272s | 数量下降，网络/池等待仍在 |
| PgBouncer query timeout | 3 个去重事件 | 5 | 改善 40% |
| PgBouncer connect timeout | 5 个去重事件 | 0 | 新增恶化 |
| statement/lock/deadlock/idle-in-tx | 0/0/0/0 | 0/0/0/0 | 未在当前日志捕获 |
| 完整 HTTP 请求 | 3,460 / 126 端点 | 1,803 / 114 端点 | 覆盖量 +91.9% |
| HTTP 成功/失败 | 3,449 / 11，失败率 0.32% | 未见显著失败 | HBA 故障后控制面失败 |
| HTTP P50/P95/max/累计 | 11.7ms / 2.181s / 60.976s / 2,224.921s | - | 新全链路基线 |
| Celery | 358 成功 / 31 retry / 7 failed | 541 / 15 / 1 | retry、failure 恶化 |

PgBouncer 1,440 个 stats 样本中，wait P50/P95/最大为 26.02/79.70/791.95ms，query 为 54.06/170.11/2,473.22ms。01:38 后的 HBA 拒绝属于登录/网络边界，不是 PostgreSQL executor 慢；此前代表性 SQL 在上一轮真实 `EXPLAIN (ANALYZE, BUFFERS)` 仅 0.020-0.228ms，也不支持盲目增加索引。

## 页面、API 与 SQL 关联

按累计耗时、P95、最大耗时、失败和用户影响综合排序：

| 端点 | 请求 | 成功/失败 | P50 / P95 / max | 累计 | 匹配慢 SQL |
| --- | ---: | ---: | ---: | ---: | ---: |
| 研报音频状态 | 370 | 369/1 | 0.949 / 9.056 / 60.976s | 890.945s | 34 条 / 274.592s |
| 研报音频列表 | 533 | 533/0 | 0.408 / 3.344 / 29.934s | 508.927s | 56 条 / 208.670s |
| MP3 B站上传 | 145 | 145/0 | 0.896 / 3.166 / 40.333s | 205.623s | 20 条 / 78.436s |
| 平台凭据 | 149 | 149/0 | 0.348 / 1.602 / 4.191s | 76.178s | 少量慢 SQL |
| MP3 任务详情 | 23 | 23/0 | 0.531 / 6.360 / 32.719s | 59.845s | 6 条 / 42.400s |
| 研报音频详情 | 12 | 12/0 | 1.306 / 13.886 / 15.272s | 48.303s | 仅 1.521s |
| 图表数据 | 4 | 4/0 | 5.445 / 32.247 / 32.247s | 39.506s | 3 条 / 36.685s |
| ZSXQ topics/local | 11 | 11/0 | 0.810 / 19.350 / 19.350s | 35.971s | 6 条 / 27.124s |

研报音频状态与列表在请求量相近/增加的情况下累计耗时分别较上轮上升约 103% 和 107%，仍是最大用户影响面。应用 SQL只能解释其中一部分，剩余长尾与 Session/连接等待、轮询密度和外部 I/O有关。`topics/local` 最大耗时从 3.767s 增至 19.350s，恶化约 5.14 倍。

主要应用 SQL 指纹按累计耗时排序：研报音频列表 40 条/156.268s；Bilibili 子任务读取 21 条/141.565s（最大 60.074s）；研报音频 owned asset 25 条/130.108s；ZSXQ 评论 upsert 10 条/82.358s；关联视觉 payload 2 条/64.955s；ZSXQ topic snapshot 21 条/62.720s；external document 单次 59.080s；scheduler 配置 32 条/44.662s。scheduler 配置慢指纹由上轮 91 条降至 32 条，说明已上线的 60 秒缓存生效，但连接长尾仍会制造慢样本。

## 三类异常与程序模式

- 低频高延迟：图表数据 4 次最大 32.247s；研报音频创建 5 次最大 24.634s；ZSXQ topics/local 11 次最大 19.350s。不能被高频排名掩盖。
- 页面/API 慢但无匹配慢 SQL：研报音频创建、抖音/微信上传状态、收费选项、MP3 创建、金融封皮、排行榜、登录/验证、竖版生成、Bilibili intelligence dashboard、视觉视频详情、ZSXQ tabs。优先查外部 I/O、缓存、队列控制面和获取连接等待。
- 后台长任务无前台端点：ZSXQ sync 19 个累计 6,585.282s、视觉方案 36 个累计 6,107.380s、研报音频 35 个累计 1,865.564s、ZSXQ publish 9 个累计 1,287.417s。Celery 7 次失败和 31 次重试主要集中于通知及外部服务，不能自动归因 executor。

静态复核未发现把单条评论逐条查询的明显 N+1：评论、主题与附件快照均以投影列和批量 `IN` 读取。页面分页通常限制在 100；500-5,000 的读取上限位于后台候选扫描，需要结合恢复后的真实行数/执行计划再优化。`publication_tasks` 专项审计仍发现 27 次 `_load_task_results` 多行 payload 物化，以及 report audio/图像资产慢 payload 写入；它们属于 recurrence registry 中未关闭的 P0 大字段轮询/写放大问题，本轮在数据库中断期间没有做缺乏运行证据的字段裁剪，也未删除原始 JSON 审计数据。

## 特别链路与反复根因

- `zsxq-insights overview` 本窗口没有实际请求，不能宣称健康。
- `topics/local` 11 次、最大 19.350s，显著恶化；`tabs` 3 次、最大 1.148s；`sync/runs` 3 次、最大 0.924s；attachments status 11 次、最大 2.281s。提示中的 `sync/status` 路径没有实际请求。
- HBA/出口 IP 白名单问题在 2026-07-27 已以另一出口 IP 出现，本轮再次发生，根因是动态出口与固定 allowlist 不匹配；队列 watchdog 174 次错误都在 `finally` 回滚/清理，不能误诊为 idle transaction 泄漏。
- 约 60 秒连接持有和 server closed 仍反复出现；现有日志没有 statement timeout、lock timeout、deadlock 或 idle-in-transaction 证据，但“当前未捕获”不等于系统不存在。
- 鉴权请求级复用在本窗口登录/验证端点的延迟看起来改善，但 HBA 故障阻止了精确 SQL 次数验收，需恢复后用 request id 再验证一次用户查询预算。

## 表、索引与执行计划

HBA 拒绝导致本轮无法新取 `pg_stat_user_tables`、`pg_stat_user_indexes`、表大小及 `EXPLAIN (ANALYZE, BUFFERS)`。最后可用的 2026-08-03 基线为：`zsxq_topic_comments` 118MiB、`zsxq_topics` 110MiB、`publication_tasks` 104MiB、`zsxq_attachments` 66MiB、`zsxq_sync_runs` 29MiB、`external_documents` 6.856MiB；当时目标表索引扫描约 99.4%-99.95%，无完全重复索引，代表性 executor 仅 0.020-0.228ms。

因此本轮不新增/删除索引。恢复数据库后必须重新取得表扫描、索引扫描、HOT 更新、索引尺寸与真实计划，再决定附件低价值索引和评论 hash 重叠索引；上一轮数据只能作为过期基线，不能替代本轮证据。

## 已落地修复与验证

- 修复巡检器遗漏 `.gz` 轮转日志的问题，并为 PgBouncer HBA/login/connect/query timeout 分类；新增固定 `--until` 截止时间、SQL/请求/Celery 累计耗时及端点成功失败统计。本报告所有数据均用修复后的完整固定窗口重算。
- 新增 gzip 发现/读取与 HBA 分类回归测试，分析器定向测试 12 项全部通过；DB/投影/轮询/ZSXQ/队列修复定向集 166 项中 165 通过，1 项为既有前端 cache-bust 版本串失败。
- 完整 unit 在正确 `PYTHONPATH=src/backend` 下运行 992 项，991 通过、1 项失败；同一既有断言期待 `lightweight-status-poll-v2`，页面现为 `sticky-sidebar-status-v3`，与本轮变更无关。
- 重启前已运行 `scripts/dev/check-restart-safety.py`；数据库登录被 HBA 拒绝，门禁退出 3，故未运行 `restart-dev-stack.sh`、未使用强制参数。
- Bug 记录、README 和 recurrence registry 已登记 gzip 统计回归与 HBA 重发故障；没有执行生产 DDL、配置更改、回填或破坏性操作。
- `compileall`、`git diff --check`、recurrence registry JSON 解析均通过；Bug 记录严格检查 140/140 通过、9 个根因簇全部已归类。

## 待人工处理、风险与下一步

1. P0：由数据库/网络管理员确认当前合法出口 `223.80.177.167`，在云安全组或 PostgreSQL HBA 恢复最小范围访问；长期应改为稳定 NAT/固定出口。变更前核对来源身份，禁止临时放开全网。
2. 访问恢复后先验证 PgBouncer 登录、应用只读查询和 Celery status，再重新运行重启安全门禁；只有门禁通过才可重启需要加载代码的后端/worker/scheduler。
3. 重新采集六张重点表的 `pg_stat_*`、尺寸和真实 `EXPLAIN (ANALYZE, BUFFERS)`。现有两份人工脚本仍待 DBeaver 安全窗口逐条执行：`docs/sql/zsxq_attachment_file_index_cleanup_dbeaver_20260801.sql` 与 `docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql`；两者均不得在本轮无证据状态下执行。
4. 下一轮继续盯住研报音频轮询累计耗时、topics/local 19 秒回退、60 秒连接持有，以及 `publication_tasks` 多行 payload 物化/慢 JSON 写入；把健康检查扩展为真正的数据库 readiness，避免 Flask 仍返回普通健康响应而关键控制面已经 500。

## 2026-08-04 08:20 人工脚本落库复核

本节是报告生成后的只读复核，不执行任何 DDL 或数据更新。数据库身份为
`show_ytb_data / cappa_rw`，当前出口 `223.80.177.167` 已可连接；重启安全门禁仍因
1 个不可中断的 ZSXQ sync 任务阻止重启。

| 脚本/变更 | 当前证据 | 结论 |
| --- | --- | --- |
| `zsxq_attachment_file_index_cleanup_dbeaver_20260801.sql` | `ix_zsxq_attachments_group_tab_status_file` 已不存在 | DROP 效果已生效；数据库不能证明具体由哪次 DBeaver 操作完成，不要再次执行 DROP；回滚 CREATE 也未执行 |
| `comment_hash_index_cleanup_dbeaver_20260724.sql` | `raw_payload_hash` 存在；40,516/40,516 非空；但 8 条与 `md5(raw_payload::jsonb::text)` 不一致；两个 topic-type 索引仍在（scan 960 和 1）；Alembic 为 `20260729_evidence_clean_visual` | 不能判定脚本整体完成；更像迁移主体已落库但 owner/后续写入留下残留。禁止原样重跑，因为会触碰仍有 960 次扫描的索引 |
| `zsxq_attachment_publish_status_index_cleanup_dbeaver_20260729.sql` | 单列 `ix_zsxq_attachments_publish_status` 不存在；复合索引 `...publish_status_updated_at` 存在且 scan=4 | 删除效果已生效；具体执行来源无法仅凭当前目录证明，不需重复执行 |
| `zsxq_topic_payload_hash_dbeaver_20260722.sql` | `zsxq_topics.raw_payload_hash` 存在；10,601 行缺失 0、哈希不一致 0 | 结构与回填效果已完成；更可能由 Alembic/等价管理员脚本完成，不能证明是 DBeaver 原脚本 |

评论哈希的 8 条不一致全部属于同一 `topic_id=14422124555541822` 的历史评论，`last_synced_at` 为 2026-07-24；原始 JSON 未被本次只读检查修改。两个残留 topic 索引 owner 均为 `postgres`，当前连接用户 `cappa_rw` 无 `postgres` MEMBER 权限，符合迁移因所有权跳过的证据。下一步应先确认应用写入路径为何改变了 `raw_payload` 却未同步哈希，再生成只修这 8 条的带锁超时、校验和回滚方案；不要重跑会批量删除索引的旧脚本。

## 2026-08-04 08:42 Insights 索引执行后验收

- `ix_zsxq_topics_group_topic_preferred_synced_desc` 已存在，owner=`postgres`，
  `indisvalid=true`、`indisready=true`、`indislive=true`，大小 704 kB；创建进度 0、
  当前锁 0。说明 DBeaver 的 CREATE INDEX 已完成，当前不需要重启项目。
- `pg_stat_user_indexes.idx_scan=0`。针对生财有术 group 的真实 canonical-topic 查询，
  `EXPLAIN (ANALYZE, BUFFERS)` 仍为 `Seq Scan + Sort`，执行时间 26.525ms；因此当前
  不能把任何页面改善归因于该索引，先观察真实请求和后续计划。
- `alembic_version` 仍为 `20260729_evidence_clean_visual`。本次是索引 DDL 生效，
  不是完整 Alembic revision 登记；禁止手动更新版本表。后续需在受控迁移流程中处理
  revision 记录，或经评审决定保留/回滚该低命中索引。
