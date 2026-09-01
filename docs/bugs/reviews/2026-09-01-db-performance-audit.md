# 2026-09-01 数据库性能与错误巡检

## 结论

本轮固定窗口为 `2026-08-31 06:00:00` 至 `2026-09-01 06:00:00`（Asia/Shanghai），并以
`2026-08-30 06:00:00` 至 `2026-08-31 06:00:00` 作为上一固定窗。整体不是改善窗口：应用慢 SQL 从
109 条增至 700 条，累计从 `261,399.15ms` 增至 `2,262,863.80ms`；请求从 94 增至 1,589，失败率从
1/94 增至 24/1,589。流量增长不能解释 `publication_tasks` 的 550 条慢 SQL和一次 382.6 秒失败请求；
主因是新文案到成片图片/详情流量把逐场景重复宽读、循环 commit 和远端数据库网络长尾叠加。

本轮定位并改码两条 P0 根因：图片批量提交查询/事务放大，以及长驻 notification worker 混用新旧模块造成
622 次导入失败。两项均已有定向合同并完成最终安全重启；notification 最终进程已真实排空 backlog 至
140/140 sent，provider 恢复 closed。图片路径尚未出现最终切点后的自然提交，因此两项仍保持
`RUNTIME_REPLACED / OBSERVING`，不是 CLOSED。

数据库物理层没有新增/删除索引依据。真实 61-row 图片 child 查询 executor 仅 `0.136ms`，既有父关联与
feature 索引均高频使用；表统计没有等待锁、idle-in-transaction 或精确重复索引。低使用索引只登记观察，
不凭单次 `idx_scan` 执行 DROP。PgBouncer/网络继续 `OPEN / REPEATED`：11 个 query-timeout 事件、1 个
connect-timeout 事件、13 个 server-closed 应用事件，不能用 DDL 掩盖。

## 1. 固定窗口总览与环比

| 指标 | 本窗口 | 上一窗口 | 判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 700 | 109 | +591；显著恶化 |
| 应用慢 SQL P50 / P95 / max | 2,045.65 / 10,024.09 / 20,117.73ms | 1,588.82 / 5,501.09 / 16,284.27ms | P95 增 82.2% |
| 应用慢 SQL 累计 | 2,262,863.80ms | 261,399.15ms | 8.66× |
| 请求数 / 成功 / 失败 | 1,589 / 1,565 / 24 | 94 / 93 / 1 | 失败率 1.51% |
| 请求 P50 / P95 / max | 198.96 / 4,872.48 / 382,617.64ms | 2.87 / 1,414.71 / 22,667.86ms | 最大值由图片提交主导 |
| 长 Session hold | 96 | 7 | P50/P95/max 8,675.43/21,773.10/31,000.70ms |
| hold 内 SQL / 非 SQL | 1,015,922.00 / 39,550.93ms | 64,342.91 / 8,990ms | 主要为慢 SQL/远端往返，不是外部 I/O 空等 |
| idle in transaction | 0 | 0 | 当前无泄漏证据 |

表归因：`publication_tasks=550`、`zsxq_topics=25`、`zsxq_attachments=20`，其余重点表没有慢 SQL。
应用统计排除了 9 条 adhoc diagnostics；窗口后本轮另有两次只读 EXPLAIN 字段拼写错误，均为 `process=-c`
且事务回滚，继续从自然应用失败中排除。

## 2. 最重 SQL 与调用链

| 指纹 / origin | 次数 | P95 / max | 累计 | 根因判断 |
| --- | ---: | ---: | ---: | --- |
| `0b91e04fb500a3eb` `tasks/script_video_workflow.py:_image_children_for_parent` | 80 | 13,976.37 / 16,985.38ms | 315,128.20ms | 每个 scene 重读同一 child 集合并搬运 JSON |
| `67be5ebc43a56078` `api/script_video_workflow.py:_owned_parent` | 73 | 10,520.65 / 20,117.73ms | 279,156.43ms | 详情/动作父任务宽投影 |
| `f0f338b22e5d94f9` `_refresh_parent_image_state` | 51 | 11,893.56 / 17,527.95ms | 228,485.09ms | child 回调反复完整父任务读取 |
| `f0f338b22e5d94f9` `_workflow_or_raise` | 53 | 8,811.71 / 13,056.99ms | 208,297.85ms | 共享 ORM 全列加载 |
| `773ba32db4924cf2` `_create_or_reuse_image_children` | 20 | 12,993.66 / 20,087.88ms | 88,506.51ms | 循环内父/child 查询和 commit |
| `45b19a5d74716c47` API child 批量读取 | 21 | 9,215.15 / 10,347.69ms | 76,240.75ms | 详情流量与远端链路叠加 |

request `0ebf3a88` 是根因样本：22 场景图片提交在 17:51:16 开始，`POST .../images` 最终
`382,617.64ms / 500`，52 条慢 SQL 累计 `190,466.73ms`。数据库目前已保留该父任务 61 个 child attempt；
旧代码每个场景重复读取全部历史 child，并逐项 insert/commit/broker/二次写 Celery ID。

真实只读计划直接否定“缺索引”：相同生产父任务的一次窄 child 查询返回 61 行，BitmapAnd 使用
`ix_publication_tasks_feature_created_desc_v2` 与 `ix_publication_tasks_parent_created`，planning
`0.255ms`、execution `0.136ms`、shared hit 51、read 0。父任务点查 executor `0.035ms`。驱动端一次
往返仍为约 29–119ms，日志中的秒级长尾是远端/PgBouncer/字节与重复往返，不是 executor。

## 3. 页面/API 统计

| API | 次数 | 成功 / 失败 | P50 / P95 / max | 说明 |
| --- | ---: | ---: | ---: | --- |
| GET script-video detail | 237 | 236 / 1 | 445.11 / 10,607.06 / 23,612.69ms | 累计 489,624.45ms；上一轮轮询修复前流量仍占主导 |
| POST script-video images | 1 | 0 / 1 | 382,617.64ms | 本轮新 P0 根因，52 条慢 SQL |
| GET script-video audio file | 119 | 118 / 1 | 518.15 / 7,636.42 / 22,155.65ms | 27 条慢 SQL；网络长尾 |
| GET report-audio PDF assets | 46 | 46 / 0 | 962.19 / 9,270.09 / 25,366.61ms | 30 条旧慢 SQL，多数发生既有修复切点前 |
| GET PDF merge list | 58 | 44 / 14 | 331.16 / 3,508.48 / 18,024.02ms | 旧宽轮询；修复后真实轻查询已在上一轮短窗验收 |
| POST content-pipeline web submit | 4 | 4 / 0 | 23,777.89 / 41,690.71 / 41,690.71ms | 外部/文件工作，无匹配等量慢 SQL |
| POST ZSXQ topics/local | 5 | 4 / 1 | 999.99 / 21,088.98 / 21,088.98ms | 失败同秒 PgBouncer query timeout，网络根因保持 OPEN |

低频高延迟与“慢 API 无匹配慢 SQL”仍分开处理。音频编辑、Celery stop、文件下载、MP3 retry 等主要是
控制面/文件/网络等待，不能误归为 SQL executor；对应接口仍保留端到端超时与失败观察。

## 4. Celery 与错误

Analyzer 记录完成 254、retry 1、失败 627。长任务成功耗时主要来自 ZSXQ 发布、Publisher、PDF/音频清洗；
这些任务本来包含外部下载、清洗、上传或编码，不能把总耗时直接当数据库耗时。

627 个失败中有 622 个来自 workflow notification 混版 ImportError。它们不是 622 个不同通知业务终态：
旧 worker 每次 `create_app()` 导入新 route 时，从已缓存旧 service module 找不到
`audio_upload_max_bytes`，任务在取得 durable delivery 前退出；watchdog 约每 85 秒补投同一 pending 集合。
磁盘函数存在且新进程完整 app factory smoke 成功，证明根因是长期驻留进程模块图混版。

SQL error 共 10 条，其中自然应用 8 条：图片 child OperationalError 5、父 owned query 2、父 loader 1，均与
同一远端查询失败簇一致。两条固定窗内 adhoc 错误明确排除。没有 statement timeout、lock timeout、deadlock
或 idle-in-transaction 事件。

## 5. PgBouncer、连接与网络

- PgBouncer 原始日志：connect timeout 1、query timeout 33 行，按事件去重为 1 和 11；query wait timeout、
  server crash、TLS receive、server login、HBA reject、cached login failure 均为 0。
- 1,440 个 stats sample：wait P50/P95/max `25.40/80.58/877.60ms`；query
  `54.56/239.27/2,313.53ms`。
- 24 小时建连 297 次，即 12.375/h；server source 均为 `100.96.3.12`，proxy TUN fallback 0。
- 应用侧 `server_closed=13`。这与远端链路已有抖动一致，不能因 PgBouncer 六类中多数为 0 就写成恢复。
- 06:16 与 06:22 两次只读快照均只有 7 个 idle ClientRead 和探针自身 active 1；idle-in-transaction、等待锁
  均为 0。

网络根因继续 `OPEN / REPEATED`。应用修复目标是减少传输字节、查询次数和连接租约；本轮没有抬 timeout、
增加连接池、隐藏错误或用重试放大数据库压力。

## 6. 表体积、更新与索引审查

| 表 | total / table / TOAST / indexes | live / dead | update / HOT % | idx scan % |
| --- | --- | ---: | ---: | ---: |
| `zsxq_topics` | 129 / 13 / 79 / 36MB | 12,637 / 1,584 | 174,041 / 0.71% | 99.55% |
| `publication_tasks` | 128 / 17 / 99 / 12MB | 13,069 / 1,696 | 242,294 / 1.28% | 99.77% |
| `zsxq_topic_comments` | 118 / 91 / 4 / 23MB | 41,333 / 5,644 | 237,197 / 70.46% | 99.90% |
| `zsxq_attachments` | 78 / 35 / 0.1 / 42MB | 33,906 / 37 | 264,485 / 0.12% | 99.95% |
| `zsxq_sync_runs` | 37 / 1.2 / 35 / 0.9MB | 1,062 / 46 | 111,710 / 0.33% | 99.93% |
| `external_documents` | 6.9MB total | 1,817 / 146 | 6,265 / 0% | 98.80% |

`publication_tasks`、topics、attachments 与 sync_runs 的低 HOT 比率继续说明宽 JSON 更新的长期写放大风险，但
dead tuple 比例与 autovacuum 现场没有给出紧急 VACUUM FULL/REINDEX 依据；这类破坏性操作不在应用连接上执行。

没有精确重复索引。Insights canonical-topic 索引 1,648KB、`idx_scan=3,930`、valid/ready/live，最近扫描
2026-08-31 06:09，继续保留。低使用观察项包括附件 PDF partial 5,088KB/1 scan、topics group-type
3,720KB/1 scan，以及少量 external_documents/sync_runs 索引；统计自 2026-08-08 起且用途包含低频运维，
不足以安全 DROP。结论：本轮无 DDL、无 DBeaver 待执行 SQL。

## 7. 本轮代码修复

### 7.1 图片批量提交

- 父任务与图片 child 统一 `load_only` 白名单，去除所有无关共享任务投影。
- 一次 child 预取后在内存按 sequence + scene hash 匹配；查询数不随场景数增长。
- 所有新 child 一次批量 commit，并在首事务预存业务 ID 与稳定 Celery ID。
- 释放连接后带明确 `task_id` 发布；成功路径零 post-broker write，失败 publish 汇总为一次短事务并只标记
  受影响 child。

旧实现 4 场景为 13 条 task SELECT；新合同 child 创建不超过 2 条、完整 enqueue 不超过 4 条，且 SQL 不含
`report_audio_pdf_bytes` 或 Publisher 无关列。broker 故障注入同时覆盖 stable ID、durable insert、
Session=0 和单项失败收口。

### 7.2 通知 worker 混版

- `create_app(config_class=Config, *, register_apis=True)` 保持默认 Web 行为。
- notification drain 使用 `create_app(register_apis=False)`，只初始化 extensions/DB context，不导入所有 API。
- 行为测试调用实际 task 并断言工厂参数；默认 app smoke 保持。

第一次安全重启前门禁读取 47 条近期任务、0 个活动 sync；未使用 FORCE。新 backend PID 74071 healthy，
bootstrap 17/17、`errors=[]`，notification worker 06:06:17 ready。06:06:24 起 backlog 真实转 sent，旧
ImportError 为 0。06:22 只读 outbox：sent 119、pending 20、retry_wait 1；provider 因真实
`SMTPServerDisconnected` 处于 open、连续失败 4。durable 事件没有删除或伪造 sent，既有 SMTP 根因继续
独立观察。

最终代码回归后，06:38 safety gate 再次读取 47 条近期任务、0 个活动 sync、0 个不可中断任务；无 FORCE
标准整栈重启完成。最终 backend PID 77653 healthy，bootstrap 17/17、`errors=[]`；workflow_notify PID
78132 于 06:39:24 ready，report_pdf_gemini 五个 worker 也全部加载同一 revision。06:41:43–06:41:57 最终
通知进程连续真实发送 backlog，无 ImportError。06:43 只读对账为 sent 140、无 pending/retry_wait/dead，
provider closed/连续失败 0、idle-in-transaction 0，证明此前 provider 断连后的 durable 积压已自然恢复。

## 8. 历史修复稳定性

- Watchdog 展示聚合：从上一轮最终切点 2026-08-31 14:38:18 到本窗口结束，旧
  `_report_video_stage_stats/_report_parse_stage_stats/_report_ai_stage_stats` origin 为 0；不再是仅 3 个周期，
  现已覆盖约 15 小时 22 分钟。仍需完整 24 小时与下一窗口后才能 accepted。
- 平台 retry/cancel/revoke：后窗有一次自然 MP3 retry 200/1,238.01ms、无匹配慢 SQL，但没有覆盖全部平台、
  cancel、duplicate/orphan/quota 分支；继续 `RUNTIME_REPLACED / OBSERVING`。
- PDF merge、MP3/报告音频 B站提交、script-video 动态轮询等上一轮修复均已加载，但本固定窗包含切点前旧流量，
  且部分缺少自然目标动作，不能用混合窗口降级/关闭。
- 网络/PgBouncer：11 个 query-timeout 事件和 13 个 server-closed，继续复发。

## 9. 验收门禁与当前状态

验收已完成如下：

1. 定向脚本视频/API/service/通知/outbox 套件 66 项通过；compileall、13 个前端合同、全部前端 JS syntax、
   Remotion TypeScript check、304 文件 AST revoke gate、迁移 single-head、registry JSON、Bug strict
   `192/192`、`git diff --check` 均通过。
2. 全量 unit 在 sandbox 中运行 1,538 项，报告 34 个 loopback bind `PermissionError`、无断言失败；安全审查
   拒绝用可能连接真实 Redis/数据库并包含模拟 DROP 的整套命令越权复跑。将三个受影响 loopback 文件单独在
   获准环境运行 32 项全部通过，未用绕过手段掩盖环境限制。
3. 真实数据库 restart safety gate 为 0 个不可中断任务；无 FORCE 标准重启完成。最终 backend healthy、
   bootstrap 17/17、目标 workers ready，最终通知进程真实发送多条 delivery，ImportError=0；outbox 140/140
   sent、provider closed、idle-in-transaction 0。
4. 最终切点后没有自然图片提交，所以图片根因只能 `RUNTIME_REPLACED / OBSERVING`；24 小时和下一后续窗口前
   所有根因均不得 CLOSED。网络/PgBouncer 仍为 `OPEN / REPEATED`。

## 10. 下一轮优先级

1. 从最终 backend/worker 切点捕获第一条自然 `POST .../images`，要求 child SELECT O(1)、批量 insert 一次、
   broker 前 Session=0、稳定 task ID、成功路径无二次写；对账 request/task/broker/scene/attempt。
2. 统计 notification ImportError、outbox sent/pending/retry_wait/dead-letter 与 provider 熔断；混版错误必须为
   0，SMTP 断连按真实送达独立处理。
3. 完成 watchdog 展示 SQL 24 小时归零和后续窗；继续捕获平台 cancel/revoke 的自然结构化边界事件。
4. 网络层要求 query/connect timeout、server closed、20 秒 hold 与 API 5xx 联合下降；executor 仍低于 10ms
   时继续反对新增索引。
