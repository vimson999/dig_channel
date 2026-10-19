# 2026-10-16 数据库性能与错误巡检

## 根因闭环结论

巡检窗口为北京时间 **2026-10-15 06:00:00–2026-10-16 06:00:00**。本轮不能标记整体完成：

- 新增 P0：内容管线发布统计旧运行时宽读 `publication_tasks.result`，6 个真实请求触发 PgBouncer
  `query_timeout`；JSONB 投影虽消除失败，但前端又把非状态统计绑定到活动任务的 10 秒轮询，形成 19 次、
  131.459 秒的重复重查询。已用行为 TDD 解除轮询耦合并由当前静态资源运行替换，状态为
  **RUNTIME_REPLACED / OBSERVING**，须下一完整窗口证明不复发。
- 恶化：慢 SQL 由 22 条/53.426 秒升至 48 条/214.443 秒；audio catalog 冷链也由 4 条/29.444 秒升至
  7 条/54.684 秒。生产 executor 分别仅 6.600ms 和毫秒级，根因不在索引或 PostgreSQL executor。
- 改善但未关闭：旧 Mac 会话与 old-root 从上一窗 6 个/2 个降至 0；B站情报从 24/24 周期失败变为前 5 个
  失败、后 18 个连续成功。两项均缺物理隔离或故障同秒链路证据，继续 OPEN。
- 反复：YouTube 固定 `channels.id=21/56` 第 13 个窗口继续失败，受保护停用 SQL 仍待人工执行。
- 本轮无 CREATE/DROP INDEX、ALTER TABLE、回填、生产配置或破坏性动作；没有证据支持 DDL。

## 与上一轮基线对比

| 指标 | 2026-10-15 | 本轮 | 变化与判断 |
| --- | ---: | ---: | --- |
| 慢 SQL 数 | 22 | 48 | +118.2%，恶化 |
| 慢 SQL 累计 | 53.426s | 214.443s | +301.4%，恶化 |
| SQL P50 / P95 / max | 1.312 / 7.374 / 7.602s | 6.353 / 9.216 / 9.383s | +384.2% / +25.0% / +23.4% |
| PgBouncer query timeout | 0 | 6 个真实请求 | 新增；镜像日志原始计数 12，按 request/SQL 去重为 6 |
| 完整 HTTP | 1,179 | 1,246 | +5.7% |
| HTTP 成功 / 失败 | 1,173 / 6 | 1,215 / 31 | 6 个统计 500；其余主要为静态/路由 404 |
| HTTP P50 / P95 / max | 3.83ms / 1.288s / 89.596s | 3.85ms / 1.697s / 62.791s | P95 +31.8%，max -29.9% |
| old-root / 远端旧会话 | 2 / 6 | 0 / 0 | 改善；仍缺物理隔离证据 |
| B站情报失败周期 | 24/24 | 5/23 | 改善；未达到完整绿窗 |
| YouTube 固定失效源 | 第 12 窗 | 第 13 窗 | 反复 |

## 主要问题排序

排序综合累计耗时、P95、最大耗时、失败率、受影响入口及复发次数，不只看出现次数。

| 排名 | 问题/指纹 | 次数与累计 | P95 / max | 失败与影响 | 结论 |
| ---: | --- | ---: | ---: | --- | --- |
| 1 | 发布统计 `a7bfc8355f1ebf38`，旧形 `749030375197552e` | 投影 19 / 131.459s；端点 45 / 277.845s | SQL 9.216 / 9.383s；HTTP 20.628 / 20.674s | 6×500、2×替换期404；投递统计页 | executor 外尾延迟被 10 秒轮询放大；已改码待后窗 |
| 2 | MP3 audio catalog `be891b5c1af362da` | 7 / 54.684s | 9.255 / 9.255s | 14 个 API 中 7 次冷链慢，7 次缓存命中快 | 既有缓存有效，冷 fetch 网络尾延迟反复 |
| 3 | YouTube 页面同步 | video 1 / 62.791s；channel 1 / 49.581s | 单样本同 max | Google SSL EOF/30s read timeout，无慢 SQL | 外部 API/网络，不是 DB |
| 4 | Web submission history / submit | 141 / 67.126s；10 / 60.825s | history 0.860 / 1.962s；submit max 7.482s | 全部成功、无匹配慢 SQL | 亚秒 SQL/文件 staging/hash/broker 分段仍需观察 |
| 5 | ZSXQ Insights `674016d39deab3f5` | 4 / 7.241s | 1.856 / 1.856s | 固定窗无自然重点页面请求 | SQL 指纹存在，但页面覆盖不足，不能判健康 |
| 6 | YouTube id 21/56 | channel 2/51 失败；video errors=2 | 12.87s / 39.20s 任务耗时 | 第 13 窗，持续失败通知 | 永久失效来源未人工停用 |

## SQL、request、Session、PgBouncer 与 executor 关联

统计端点共 45 个请求：6 个旧全 JSON 请求在 20.06–20.33 秒收到 500，并与 PgBouncer query timeout、
SQL origin `apis/v1/content_pipeline.py:459:web_content_pipeline_submission_statistics` 一一对应；2 个 404 出现在
路由尚未加载的替换间隙；其余 37 个成功。新投影读取 16 行时，完整 `result` 约 684,588 bytes，投影结果约
74,414 bytes。生产只读 `EXPLAIN (ANALYZE, BUFFERS)` 为 execution 6.600ms、planning 0.794ms、
`shared hit=1429`、无 read，命中既有索引。因此应用 6–20 秒耗时发生在 executor 之后的
PgBouncer/私网/driver fetch 边界。

Session 生命周期分析捕获 31 个 Web hold，P50/P95/max 7.710/20.578/20.596 秒；全部能关联到慢 SQL，
SQL 时间 314.333 秒、非 SQL 仅 3.106 秒，没有发现跨外部 I/O 持有数据库 Session。36 个 runtime guard
短 episode 最大 3 连接、事务龄 0.479 秒、idle 0.445 秒，压力/延期/持久 idle-in-transaction 均为 0。
这进一步排除外部 I/O 交叉持有、锁队列和长期 idle transaction。

## 页面/API 全链路

窗口内 1,246 个完整请求覆盖 108 个端点，1,215 成功、31 失败。失败拆分为：统计 API 6×500 和 2×404、
favicon 20×404、CSS 2×404、files 1×404；不能把静态 404 与真实业务 500 混算。

- 低频高延迟：YouTube video sync 62.791 秒、channel sync 49.581 秒，均无慢 SQL，日志对应 Google API
  SSL EOF/read timeout。
- 页面很慢但无匹配慢 SQL：web submissions 141 次累计 67.126 秒，web submit 10 次累计 60.825 秒；
  应继续补文件 staging、hash、通知 outbox 与 broker 阶段 telemetry。
- 后台/连接很久但无前台端点：durable MP3 workflow 4 项累计壁钟 4,472.146 秒；PDF clean 8 项
  2,236.481 秒；ZSXQ publish 10 项 2,274.498 秒。它们与前台 HTTP 分开统计，不能用 HTTP 健康覆盖。
- audio assets 14 次累计 60.531 秒，其中 7 次冷链匹配 54.684 秒 SQL，另 7 次缓存命中快速；没有逐附件 N+1。

固定窗没有 zsxq-insights overview、topics/local、topics/local/tabs、sync/status 的自然请求，属于覆盖边界，
不能写成未复发。窗后真实验收：overview 200/3.231 秒，topics/local 200/1.098 秒，tabs 200/0.352 秒，
system-sync-scheduler/status 200/0.745 秒；overview 为冷 cache MISS。这些烟测证明入口可用，不替代下一自然窗口。

## 后台任务、失败率与外部链路

- Celery 捕获 228 个 completed、0 retry、1 failure，active 总耗时 6,835.887 秒。PDF clean active
  1,874.060 秒、durable wall 2,236.481 秒，等待/编排差 362.421 秒（16.2%，较上一窗明显改善）。
- Publisher 17 项：16 completed、1 failed，失败率 5.9%；失败原因是 B站发布时间必须至少提前 5 分钟，
  不是数据库或 old-root。通知 durable 115/115 sent、max attempt 1。
- B站情报前 5 个周期 0/7、35 次 DNS 失败，随后 18 个周期连续 7/7；恢复原因未捕获，继续等待同秒链路追踪。
- YouTube 自然 channel sync 49/51，video sync errors=2，固定 id 21/56 第 13 窗复发；页面手工同步的
  Google 网络超时另算，不扩大停用范围。
- durable root：audio clean 2/2、PDF clean 8/8、MP3 4/4、Publisher 17/17、ZSXQ publish 10/10
  全为 current-root，old-root/unclassified 均为 0；当前生产会话也无旧 Mac 地址。物理隔离仍未证明，保持观察。

## 目标表规模与索引复核

| 表 | 总大小 | live / dead | seq scan / idx scan | 索引扫描占比 | 判断 |
| --- | ---: | ---: | ---: | ---: | --- |
| publication_tasks | 148.0MB | 14,496 / 2,479 | 7,168 / 4,085,074 | 99.825% | 统计投影已命中既有索引；不加索引 |
| zsxq_sync_runs | 37.0MB | 1,062 / 46 | 312 / 338,045 | 99.908% | 无新慢指纹证据 |
| zsxq_topics | 128.5MB | 12,637 / 1,584 | 4,289 / 892,871 | 99.522% | Insights 指纹需继续分段，不盲加索引 |
| zsxq_topic_comments | 118.0MB | 41,333 / 5,644 | 539 / 359,504 | 99.850% | 无 DDL 证据 |
| zsxq_attachments | 77.7MB | 33,906 / 37 | 649 / 1,135,973 | 99.943% | 无 DDL 证据 |
| external_documents | 6.85MB | 1,817 / 146 | 1,081 / 84,997 | 98.744% | 小表，低使用索引不能据此删除 |
| content assets 关联表 | 4.81MB | 4,142 / 824 | 4,105 / 10,306 | 71.51% | 先找真实慢 SQL，再决定索引 |

完全重复索引定义为 0。低使用候选包括附件 PDF group/tab、topic group/type、external status 与 publisher match
等，但缺少 stats reset 时间、写放大 A/B、真实慢 SQL 与 `EXPLAIN` 证据；本轮不生成 CREATE/DROP 脚本。
唯一待人工 SQL 仍是 `docs/sql/disable_unavailable_youtube_channels_20260920.sql`，它是业务来源配置，不是索引
整改；脚本默认 ROLLBACK，需在 DBeaver 逐段审核，风险是把暂时不可用来源误判为永久失效。

## 已落地修复与防复发契约

- `content-pipeline-submit.js`：活动 tick 只调用轻量历史；检测活动态转终态时仅刷新一次统计。
- `content_pipeline_submission_stats_contract.js`：真实脚本行为合同先红后绿，锁定“活动 tick 无统计、终态一次、
  终态后停止”。
- 数据库 Session 纪律新增：统计/聚合不得绑定状态 tick，允许触发源必须有限并证明请求量不随活动时长线性增长。
- 新 Bug 记录与 recurrence registry 同时登记 `HOT-PATH-PAYLOAD-POLLING`、`DB-SESSION-LIFECYCLE`，
  下一轮优先复核旧/新 SQL 指纹、500、Session hold 与请求触发源。

## 测试与运行态验证

- 定向前端行为合同：通过；Node 语法检查通过。
- 定向后端统计投影：`3 passed, 3 skipped, 11 subtests passed`；完整 unit：1,736 项通过；完整前端
  Node 合同：103 项通过；`compileall`、Bug strict（210/210 已归类）、registry JSON 与
  `git diff --check` 均通过。沙箱内完整 unit 因 Redis socket `PermissionError` 被中断，随后以同一命令在
  受信环境重跑全绿，未把沙箱网络限制记成代码失败。
- 当前静态 JS HTTP 200、33,095 bytes 且包含新合同；统计 API HTTP 200/0.586 秒；health HTTP 200/0.053 秒。
- ZSXQ 四个重点入口窗后真实 HTTP 均 200；固定窗无自然样本仍按未捕获。
- 本次只改静态前端与文档/测试，backend、Celery、scheduler 无需重启，故未运行
  `check-restart-safety.py`，也未触发不安全重启。

## 未捕获边界

- 固定窗没有 ZSXQ 四个重点页面自然流量；窗后烟测不能代表用户分布、热/冷 cache 或 24 小时行为。
- PgBouncer/网络尖峰时没有同秒 pool/server socket、route、远端 activity，当前恢复快照不能反证故障。
- `pg_stat_*` 累计值缺少可信 stats reset 时间，不能把低扫描索引直接判为冗余。
- 日志镜像会把 SQL error 计数从 6 放大为 12；本报告按 request id、时间与 SQL 去重，但其他无 request id
  事件仍可能存在归并误差。
- 未捕获不等于未发生；测试通过不代替自然用户、worker 或调度器后窗。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| 统计 `749030...` / `a7bfc...` | 全 JSON + 非状态统计绑定 10s 状态 tick，网络尾延迟被放大 | JSONB 投影已加载；本轮解除轮询耦合并加行为门禁 | 静态 JS、统计 API 真实 200 | 新增，OBSERVING | 网络断点未定位；自动复核 | 500/timeout=0，统计不随活动时长线性增长，完整绿窗 |
| audio catalog `be891...` | 冷 fetch 的 executor 外私网/driver 尾延迟 | 既有 TTL/single-flight cache | 7 次冷链慢、7 次热命中快 | 是 | 捕获尖峰同秒 pool/socket/route | SQL P95 回落且无 query timeout；executor 对照保留 |
| old-root / 远端会话 | 旧 Mac 共享 DB/broker 写权限 | 现有跨根 fail-closed；无新权限变更 | 本窗所有 feature old-root=0、旧会话=0 | 本窗未见，历史反复 | 人工停旧栈或轮换凭据/隔离 namespace | 物理围栏 + 后续完整窗 + 当前根自然成功 |
| B站 DNS | DNS/代理/TUN 外部链路断点未知 | 失败隔离，未用无界重试 | 5 失败后 18 连续成功 | 是 | 下次失败同秒网络追踪 | 完整 24h 23–24 个自然周期全部 7/7 |
| YouTube 21/56 | 永久不可用来源仍启用 | 结构化身份观测；受保护 SQL 已生成 | 第 13 窗精确复发 | 是 | 人工 DBeaver 审核执行 | channel 49/49、video errors=0 连续两周期 |
| 页面慢无慢 SQL | 外部 API、文件/hash/broker 或亚秒 SQL 尚未完整分段 | 现有 request/SQL 关联 | YouTube、history、submit 已分类 | 部分反复 | 增补阶段 telemetry | 各阶段累计/P95/max 可归因且无隐藏 Session hold |

## 下一轮明确验收计划

1. 首先复核统计两指纹、端点 500/404、触发来源和 Session hold，确认修复后的完整后窗而非重新扫描症状。
2. 对任何应用 SQL >500ms 且 executor <10ms 的新尖峰同步保存 PgBouncer pool/socket、route 与 activity。
3. 继续对所有 `publication_tasks.feature` 做 current/old/unclassified 根汇总，并核对旧 Mac 会话与物理隔离动作。
4. B站若再失败立即采 DNS/代理/TUN/aiohttp 现场；无失败也必须完成 24 小时自然周期统计。
5. 人工复核并决定是否执行 YouTube 21/56 受保护脚本；未执行则继续 OPEN，不再重复把症状写成新问题。
6. 用自然流量复核 ZSXQ 四入口；若仍无样本，继续明确报告覆盖缺口。
7. 只有代码、定向与完整测试、真实运行态和后续窗口全部满足时，才将对应根因升级为 CLOSED。
