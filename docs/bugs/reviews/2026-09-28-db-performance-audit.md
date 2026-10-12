# 2026-09-28 每日数据库性能与错误巡检

## 根因闭环结论

固定窗口为 `2026-09-27 06:00:00` 至 `2026-09-28 06:00:00`（Asia/Shanghai；数据库条件为
UTC naive `2026-09-26 22:00:00` 至 `2026-09-27 22:00:00`）。本轮**不能标记已解决**：

- 应用慢 SQL 从 11 条/13.393 秒回落到 6 条/8.261 秒，P95/max 从 2.305 秒降至 1.933 秒；但
  P50 从 1.033 秒升至 1.201 秒。PgBouncer 八类故障、锁、timeout、长事务均为 0。
  notification 两个主要形状应用耗时 1.201–1.933 秒，生产 executor 仅 0.034–0.171ms；
  Insights 应用 1.609 秒，executor 35.317ms，仍指向 driver/PgBouncer/私网/fetch 边界，不支持盲目加索引。
- old-root 根因在本窗继续自然复发：2 个 PDF 清洗和 2 个 ZSXQ 发布任务 4/4 均携
  `dig_ytb_data_claude_v1`，当前 root 命中 0，累计 1709.255 秒。本地对应 worker 日志未更新。
  远端 `100.94.17.45` 当前连接归零且 MacBook Pro 当前离线，只能升级为
  **OPEN / REPEATED / HOST_OFFLINE_OBSERVED / WAITING_FULL_WINDOW**，不能覆盖窗口内复发。
- YouTube id 21/56 第九个窗口固定失败，且生产配置仍为 `is_active=true`，保持
  **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。
- 固定窗只有上一轮巡检留下的 6 个烟测请求，天然业务页面/API 请求仍为 0；不能把未捕获写成无问题。
- 本轮 TDD 补齐跨 feature 的 durable root 归属门禁；未修改常驻业务代码，不需要重启，也没有执行 DDL、
  数据回填、远端停机、断会话或生产配置变更。

## 与上一轮量化对比

| 指标 | 09-27 窗口 | 09-28 窗口 | 变化 | 判断 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL | 11 | 6 | -45.5% | 改善，但仍复发 |
| 应用慢 SQL 累计 | 13.393s | 8.261s | -38.3% | 改善 |
| 慢 SQL P50 | 1.033s | 1.201s | +16.3% | 中位数恶化 |
| 慢 SQL P95 / 最大 | 2.305s / 2.305s | 1.933s / 1.933s | -16.1% | 尾延迟改善 |
| PgBouncer wait P95 | 33.96ms | 34.11ms | +0.4% | 持平 |
| PgBouncer query P95 | 59.55ms | 57.35ms | -3.7% | 持平 |
| 天然业务 HTTP 请求 | 0 | 0 | 仍为空窗 | 不可作性能环比 |
| old-root 内容任务 | 10/10，3813.963s | 4/4，1709.255s | 数量 -60.0%，累计 -55.2% | 仍是 100% 旧根，不能判闭环 |
| Publisher | 0 | 0 | 无样本 | 不能判断改善 |
| YouTube 固定失效源 | id 21/56 | id 21/56 | 第九窗 | 反复 |

## 主要问题排序

| 排名 | 问题 | 累计 / P95 / 最大 | 失败率、页面/用户影响 | 复发与状态 |
| --- | --- | --- | --- | --- |
| 1 | old-root PDF 清洗 | 1275.736s / 670.553s / 674.185s | 2/2 completed，但两个 Web 投递由旧部署处理 | 多窗；OPEN/REPEATED |
| 2 | old-root ZSXQ 发布 | 433.519s / 239.019s / 241.492s | 2/2 completed，产物归属仍不可控 | 多窗；OPEN/REPEATED |
| 3 | B站情报整点任务 | 205.722s / 9.325s / 9.722s | 23 个完成周期、每周期 6/6 成功 | 本窗无失败风暴；OBSERVING |
| 4 | workflow notification | 103.806s / 2.994s / 4.480s | Celery 46/46 成功；durable 39/39 sent、max attempt 1 | 本窗未复发失败风暴 |
| 5 | YouTube 固定失效源 | channel 14.17s；video 38.11s | 两条链均固定失败 2 个来源，约 2/51 | 第九窗；WAITING_MANUAL_CONFIG |
| 6 | 应用 SQL 远端尾延迟 | 8.261s / 1.933s / 1.933s | 6 条低频；后台为主，1 条巡检烟测 | 多窗；OPEN/OBSERVING |

任务 wall clock 不等于数据库连接持有时间；本表不把嵌套阶段重复相加，也不把 completed 的旧根任务当成正确拓扑。

## 慢 SQL、request、Session、PgBouncer 与 executor

| 指纹 / origin | 次数 / 累计 / P95 | request 或调用链 | executor 与根因判断 |
| --- | ---: | --- | --- |
| `c1e57cc097d3e051` / notification due | 2 / 2.393s / 1.201s | Watchdog provider drain | 同形 executor 0.171ms；既有 lease 索引，非缺索引 |
| `9583c5faf59cd676` / delivery for update | 1 / 1.933s / 1.933s | B站完成通知锁单行 | 去掉锁语义的同形点查 executor 0.034ms；主键命中 |
| `674016d39deab3f5` / Insights theme aggregate | 1 / 1.609s / 1.609s | request `edac6f37`，overview 冷烟测 | canonical executor 35.317ms；应用约 46 倍 |
| `1e920b463e0710b7` / business processing repair | 1 / 1.301s / 1.301s | Watchdog 有界 stale scan | 低频；既有 feature/status/updated 索引族，无扫描恶化证据 |
| `b2e38145daa34074` / stick video repair | 1 / 1.026s / 1.026s | Watchdog ready-stage scan | 低频；无锁等待或失败风暴 |

PgBouncer 1440 个样本、287 次连接，八类故障均为 0；287/287 server connection 来自当前 Mac mini
`100.96.3.12`，proxy TUN fallback 为 0。wait P50/P95/max 为 24.39/34.11/112.78ms，query 为
51.23/57.35/103.12ms。应用/traceback/SQL 三路的 statement timeout、lock timeout、deadlock、
query/connect timeout、server closed 均为 0。固定窗没有 connection hold 或 idle-in-transaction episode，
实时也没有 waiting lock；一次快照中的 11ms watchdog `idle in transaction` 在下一快照消失，不属于长事务。

`pg_stat_activity` 06:03 与 06:07 均未再看到 `100.94.17.45`；Tailscale 显示该 MacBook Pro 当前离线、
最后在线时间为 09-27 22:20。四条 old-root 任务在 16:27–16:51 已完成，说明远端离线发生在复发之后。

## 页面、API、后台任务与 SQL 关联

固定窗 6 个完整 HTTP、0 incomplete、5 个 endpoint，全部是上一轮 06:09 巡检烟测：累计
4169.34ms、6/6 成功。overview 两次累计 3079.35ms，冷请求匹配 1.609s Insights SQL；status
499.75ms、topics/local 413.57ms、tabs 173.32ms、health 3.35ms。本窗没有天然业务页面流量，仍属于
coverage anomaly。

窗后真实只读烟测（backend 记录）：

| 端点 | 结果 | SQL / payload 关联 |
| --- | --- | --- |
| `/health` | 200 / 2.30ms | 无数据库瓶颈 |
| `/api/v1/zsxq-insights/overview` 冷 / 暖 | 200 / 3658.68ms / 0.48ms | 冷态 aggregate 1611.46ms；热缓存有效 |
| `/api/v1/zsxq/topics/local` 首轮 / 复测 | 200 / 1663.68ms / 862.68ms | 首轮 topics 756.93ms、attachments 656.39ms；响应 132951B |
| `/api/v1/zsxq/topics/local/tabs` | 200 / 240.69ms | 单次聚合 SQL 92.70ms |
| `/api/v1/zsxq/system-sync-scheduler/status` | 200 / 417.54ms | credential/config 两次有界查询 87.30/155.22ms |

topics/local 的精确生产形状中，21 行 topics 与 51 行 attachments 的
`EXPLAIN (ANALYZE, BUFFERS)` 分别只有 0.066ms 和 0.257ms，均命中现有索引；topic content 33921B、
attachment raw payload 47028B，最终 HTTP 响应约 133KB。它是固定次数批量加载，不是按 20 个主题线性增长的
N+1；当前差值仍主要位于远端往返/fetch/ORM/JSON 序列化边界，不能靠新索引解决。

三类必须单列的异常：

- **低频高延迟**：6 条慢 SQL 的每个指纹仅 1–2 次，最大 1.933 秒。
- **页面/API 很慢但无匹配慢 SQL**：固定窗天然业务为空；窗后 topics/local 首轮 1.664 秒，但两条主要 SQL
  均低于 1 秒阈值，说明只看 slow_query 会漏掉多个亚秒远端往返叠加。
- **后台任务/连接很久但无前台端点**：4 条 old-root 内容任务累计 1709.255 秒；B站 23 个完成周期累计
  205.722 秒；YouTube 两轮累计 52.28 秒。wall clock 不能自动归为 Session hold。

Celery 日志识别 46 个 completed、0 retry、0 failed，全部为 workflow notification；数据库 durable notification
为 39/39 sent、attempt 39、max attempt 1。durable 数据有 4 个内容任务，而本地 content pipeline / ZSXQ
publish worker 日志没有对应更新，是远端旧执行器的证据，不是日志解析器遗漏。

## 程序模式复核

- Insights 冷态 3.659 秒、热态 0.48ms，TTL/cache 命中有效；冷查询 executor 35.317ms，缓存不能掩盖冷路径。
- topics/local 仍为 6 个固定批量查询；topics、attachments、external links/documents、favorites 均按页面集合加载，
  未见按 20 个主题逐条查询。本轮没有发现新 N+1、超大分页或无界大字段读取。
- 132951B HTTP 响应与约 81KB topic/attachment 主体不算超大，但多个远端 SQL 往返可叠加到 1.664 秒；需继续补
  pool acquire、fetch/deserialize 与 response serialization 分段，不能仅以 `>=1s` 慢 SQL 判断页面无 DB 贡献。
- 固定窗没有天然页面、隐藏 tab、详情轮询或 MP3 页面样本，因此频繁轮询、缓存失效和其他页面 N+1 仍属未捕获。
- 没有捕获长 Session hold、外部 I/O 与事务交叉持有、通知失败重试风暴或重复投递；结论只覆盖本机日志集合。

## 表规模、顺序扫描与索引复核

| 表 | total / heap / index | live / dead | seq / idx scan | 索引命中判断 |
| --- | --- | ---: | ---: | --- |
| `publication_tasks` | 140MiB / 17MiB / 13MiB | 13953 / 1338 | 7108 / 3788654 | 99.81%；HOT update 1.22% |
| `zsxq_topics` | 129MiB | 12637 / 1584 | 4193 / 892008 | 99.53%；HOT 0.71% |
| `zsxq_topic_comments` | 118MiB | 41333 / 5644 | 482 / 358707 | 99.87%；HOT 70.46% |
| `zsxq_attachments` | 78MiB | 33906 / 37 | 631 / 1135785 | 99.94%；HOT 0.12% |
| `zsxq_sync_runs` | 37MiB | 1062 / 46 | 293 / 337767 | 99.91%；HOT 0.33% |
| `external_documents` | 6.9MiB | 1817 / 146 | 1063 / 84987 | 98.76%；HOT 0% |

精确重复索引仍为 0。低/零扫描候选仍包括 attachments PDF group/tab（约 5088kB、scan 1）、topics
group/type/create（约 3720kB、scan 1）、publication Publisher-MP3 match（168kB、scan 0）、external TTS
（232kB、scan 0）与 summary（184kB、scan 0）。统计自 08-08 实例启动累计，且业务自然样本不足，不能据此删除。

代表 EXPLAIN：任务点查 0.050ms、频道图表 3.214ms、all-tabs config 0.033ms、Publisher 列表 0.050ms、
内容管线列表 0.091ms、任务轻/宽投影 0.024/0.025ms、ZSXQ system runs 1.527ms、Insights canonical
35.317ms、topics/local 页面 0.066ms、attachments 批量 0.257ms、notification due/delivery 0.171/0.034ms。
没有真实 executor、buffer、顺序扫描或锁证据支持 CREATE/DROP INDEX，本轮不生成或执行 DDL。

## 已落地修复、测试与运行态验证

- 修复巡检门禁：`daily_db_runtime_snapshot.py` 现在对窗口内所有 `publication_tasks.feature` 输出
  old/current/unclassified root 汇总，并逐行列出 old-root task id、时间、状态与持续时间。
- TDD 红灯先因 `summarize_task_roots` 缺失失败；最小实现后定向 2/2 通过。生产只读回放正确输出 PDF 2/2、
  ZSXQ publish 2/2 old-root，避免 Publisher=0 时再次假绿。
- 相关定向套件 75/75 通过；按项目标准 `PYTHONPATH=src/backend` 完整 unit tests 1720/1720 通过。
  首次沙箱运行的 35 个错误均为禁止本机回环端口，放宽本机测试权限后仅暴露缺 `PYTHONPATH` 的发现错误，
  按标准环境重跑后归零；这两次环境失败不计为业务回归。
- `compileall`、`git diff --check`、recurrence registry JSON 校验及 Bug 记录 strict 检查 209/209 均通过。
- 更新 old-runtime 与 YouTube Bug、Session 纪律、README 和 recurrence registry；状态均保持 OPEN。
- 生产查询全部 `SET TRANSACTION READ ONLY`，EXPLAIN 仅用于 SELECT；未终止连接、未改配置或数据。
- 窗后 backend 与五个重点只读 API 均 200；远端 MacBook Pro 当前离线且来源连接两次采样为 0，这是正向观察，
  但不替代后续完整窗口和自然任务。
- 本轮没有常驻业务代码变更，不需要执行重启安全门或重启 backend/Celery/scheduler。

## 待人工动作、风险与未捕获边界

1. **P0 old runtime**：确认 `100.94.17.45` 对应 MacBook Pro 上旧 backend/Celery/Watchdog/scheduler/手工脚本
   已被永久停用，而非临时关机；无法证明时轮换生产 PostgreSQL/Redis 凭据并隔离 broker namespace。
2. **YouTube**：在 DBeaver 人工复核并执行
   `docs/sql/disable_unavailable_youtube_channels_20260920.sql`；脚本有精确保护、timeout、验证与回滚，本轮不执行。
3. `pg_stat_statements` 未安装；日志缺 pool acquire、fetch/deserialize bytes、response serialization 以及所有连接的
   deployment identity，应用/executor 差值仍无法做物理分段。
4. 天然业务 HTTP 为 0；慢 SQL 阈值以下的 N+1、分页、宽字段、轮询和缓存问题无法被固定窗证伪。
5. 远端应用日志不在本机集合；当前离线、连接为 0 和本地日志无失败都不能证明远端旧软件已移除。

## 下一轮明确验收计划

1. 固定窗优先检查 `100.94.17.45` 全窗连接与 Tailscale 在线历史；取得停旧栈或凭据/namespace 隔离记录。
2. 至少各取一条自然 PDF clean、ZSXQ publish、MP3→Publisher，要求 current-root=100%、old-root=0、
   unclassified=0，且无旧 reason、replacement、素材不可达或 child failure；再过一个完整 24 小时窗口。
3. YouTube 脚本人工执行后，channel/video 连续两个自然周期不再出现 id 21/56，再观察一周期。
4. 恢复天然页面/API 样本后重算全站 request、失败率、累计、P50/P95/max；重点补 overview、topics/local、tabs、
   sync/status 与 MP3 页的 pool/fetch/serialize/bytes 分段。
5. 继续同窗关联真实 SQL、request/task id、Session、PgBouncer 与 executor；只有 executor 与 buffer/scan 明确恶化
   时才提出 DDL。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| old checkout / `100.94.17.45` | MacBook Pro 旧部署共享生产 DB/broker；缺强 deployment identity | 全 feature root 快照门禁、Bug/纪律/registry | 4/4 old-root；当前 host offline、连接 0 | **是** | 临时离线可重连；确认永久停用或轮换凭据/namespace | 三类自然任务 current-root；完整窗 old-root/远端连接为 0 |
| 6 个低频慢 SQL | executor 快，时间主要在 driver/PgBouncer/私网/fetch | 既有有界访问与只读 EXPLAIN；未盲目 DDL | 8.261s；notification 1.933s vs executor 0.034ms | **是，量下降** | 缺 pool/fetch/network 分段 | 应用与 executor 同窗稳定并取得物理分段 |
| YouTube id 21/56 | 远端无可用内容但本地仍启用 | 结构化失败身份、受保护人工脚本 | channel 49/51；video errors=2；is_active=true | **第九窗** | 人工执行 SQL | 双自然周期无两源，再观察一周期 |
| 天然 HTTP coverage=0 | 本窗无业务访问，非 parser 回归 | coverage anomaly 纪律与烟测回放 | 6 个请求均为巡检烟测 | 未知 | 全站自然行为未捕获 | 下窗自然请求三路日志数量级一致 |
| topics/local 亚秒 SQL 叠加 | 固定 6 次批量远端往返与 fetch/serialize 累加 | 既有批量加载与页面上限；补 executor/bytes 证据 | 首轮 1.664s；executor 0.066/0.257ms；133KB | 窗后仍慢 | 缺 pool/fetch/serialize 分段 | 天然冷暖样本与分段齐全，查询数保持 O(1) |
| B站/通知外部任务 | 当前无失败风暴 | 有界周期、durable attempt 与结构化完成日志 | B站 23 周期成功；通知 39/39 sent | 本窗未复发 | 上游网络断点仍未知 | 后续完整窗保持成功；故障时采 DNS/proxy/TUN 现场 |
