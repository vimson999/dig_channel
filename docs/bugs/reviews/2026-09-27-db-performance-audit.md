# 2026-09-27 每日数据库性能与错误巡检

## 根因闭环结论

固定窗口为 `2026-09-26 06:00:00` 至 `2026-09-27 06:00:00`（Asia/Shanghai；数据库条件为
UTC naive `2026-09-25 22:00:00` 至 `2026-09-26 22:00:00`）。本轮**不能标记已解决**：

- 应用慢 SQL 从 6 条/8.242 秒恶化到 11 条/13.393 秒，P95/max 从 2.086 秒升至 2.305 秒；但
  PgBouncer 八类故障、锁、timeout、长事务均为 0。Insights 代表查询应用耗时 1.607 秒，生产
  `EXPLAIN (ANALYZE, BUFFERS)` executor 仅 34.990ms，仍指向 driver/PgBouncer/私网传输或 fetch
  尾延迟，不支持盲目加索引。
- old-root 根因首次定位到具体远端主机：`pg_stat_activity` 有 6 个来自 `100.94.17.45`、空
  `application_name` 的生产库会话；本机 Tailscale 身份映射为仍在线的 `v9 的 MacBook Pro`。
  同窗 5 个 PDF 清洗和 5 个 ZSXQ 发布任务全部携 `dig_ytb_data_claude_v1` 旧 checkout，累计
  3,813.963 秒，当前项目路径命中 0。状态升级为
  **OPEN / REPEATED / ROOT_HOST_LOCATED / WAITING_MANUAL_HOST_SHUTDOWN**。
- YouTube id 21/56 第八个窗口固定失败，保持 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。
- 固定窗只有上一轮巡检留下的 6 个烟测请求，天然业务页面/API 请求仍为 0；不能把未捕获写成无问题。
- 本轮新增只读 durable/runtime 快照诊断与防复发纪律，没有修改常驻业务代码，故无需重启；也没有执行
  DDL、数据回填、远端停机、断会话或凭据轮换。

## 与上一轮量化对比

| 指标 | 09-26 窗口 | 09-27 窗口 | 变化 | 判断 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL | 6 | 11 | +83.3% | 恶化 |
| 应用慢 SQL 累计 | 8.242s | 13.393s | +62.5% | 恶化 |
| 慢 SQL P50 | 1.044s | 1.033s | -1.1% | 基本持平 |
| 慢 SQL P95 / 最大 | 2.086s / 2.086s | 2.305s / 2.305s | +10.5% | 尾延迟恶化 |
| PgBouncer wait P95 | 34.44ms | 33.96ms | -1.4% | 持平，无错误事件 |
| PgBouncer query P95 | 58.77ms | 59.55ms | +1.3% | 持平 |
| 天然业务 HTTP 请求 | 0 | 0 | 仍为空窗 | 不可作性能环比 |
| Publisher | 19（7 failed） | 0 | 无样本 | 不能判断改善 |
| old-root 内容任务 | Publisher 19/19 | PDF/ZSXQ 10/10 | 跨工作流复发 | 根因主机已定位 |
| YouTube 固定失效源 | id 21/56 | id 21/56 | 第八窗 | 反复 |

## 主要问题排序

| 排名 | 问题 | 累计 / P95 / 最大 | 失败率、页面/用户影响 | 复发与状态 |
| --- | --- | --- | --- | --- |
| 1 | old-root PDF 清洗 | 2,552.684s / 631.670s / 659.734s | 5/5 completed，但由旧部署处理、产物归属不可控 | 多窗；ROOT_HOST_LOCATED |
| 2 | old-root ZSXQ 发布 | 1,261.279s / 414.690s / 452.119s | 5/5 completed，但当前本机无对应完整日志 | 多窗；ROOT_HOST_LOCATED |
| 3 | B站情报整点任务 | 217.205s / 10.225s / 11.346s | 24/24 周期、每周期 6/6 成功 | 较上窗累计 +0.4%；OBSERVING |
| 4 | 本地 workflow notification | 149.437s | 63 个 Celery 完成，durable 57/57 sent、max attempt 1 | 无失败风暴 |
| 5 | YouTube 固定失效源 | channel 18.43s；video 38.88s | channel 49/51，video errors=2 | 第八窗；WAITING_MANUAL_CONFIG |
| 6 | 应用 SQL 远端尾延迟 | 13.393s / 2.305s / 2.305s | 11 条均低频；后台和一次巡检烟测受影响 | 多窗；OPEN/OBSERVING |

任务 wall clock 不等于数据库连接持有时间，本表按各问题自身累计、P95、最大、失败率、影响范围和复发次数
排序，不把嵌套阶段简单相加。

## 慢 SQL、request、Session、PgBouncer 与 executor

| 指纹 / origin | 次数 / 累计 / P95 | request 或调用链 | executor 与根因判断 |
| --- | ---: | --- | --- |
| `c1e57cc097d3e051` / notification due | 1 / 2.305s / 2.305s | 后台 provider drains | 上轮同形 0.141ms；非缺索引 |
| `1e920b…` / Publisher stale processing | 2 / 2.178s / 1.125s | 后台 repair 扫描 | 有界索引形状；旧 actor 根因不在查询 |
| `59acf8…` / report audio finalization | 2 / 2.054s / 1.032s | 后台 finalization | 既有有界 ID 查询 |
| `674016…` / Insights theme aggregate | 1 / 1.607s / 1.607s | request `1f9c837b`，overview 冷烟测 | executor 34.990ms，应用约 46 倍 |
| `60ff74…` / ZSXQ all-tabs config | 1 / 1.144s / 1.144s | scheduler | executor 0.024ms；18 行 seq scan 合理 |
| `93f39…` / Publisher processing | 1 / 1.033s / 1.033s | 后台扫描 | 有界形状 |
| `b2e381…` / stick video repair | 1 / 1.030s / 1.030s | 后台 repair | 低频，暂无 executor 扫描证据 |
| `3d9e2b…` / B站竞品查询 | 1 / 1.029s / 1.029s | 独立 scheduler | 低频，无失败风暴 |
| `7c472a…` / report audio recovery | 1 / 1.013s / 1.013s | 后台 recovery | 低频，无前台端点 |

PgBouncer 1,440 个样本、288 次连接，八类故障均为 0；wait P50/P95/max 为
24.20/33.96/259.85ms，query 为 51.24/59.55/128.82ms。应用/traceback/SQL 三路的 statement timeout、
lock timeout、deadlock、query/connect timeout、server closed 均为 0。仅 2 个亚秒 idle observation，最大
1 个连接、xact 0.278s、idle 0.238s、deferral 0，未发现长时间 idle-in-transaction。

`pg_stat_activity` 同时看到本机 `100.96.3.12` 和远端 `100.94.17.45`；后者 6 个会话只能严格归因为
“该主机的空 application_name 客户端”，不能从数据库侧臆断具体 PID。结合 10/10 旧 checkout durable 任务、
当前日志缺口和私网身份，构成远端旧运行时的高置信根因链。

## 页面、API、后台任务与 SQL 关联

固定窗 6 个完整 HTTP、0 incomplete、5 个 endpoint，全部是上一轮 06:04–06:13 巡检烟测：累计
4,257.94ms、6/6 成功。overview 两次累计 3,122.19ms，冷请求匹配 1.607s Insights SQL；status
506.82ms、topics/local 444.68ms、tabs 181.80ms、health 2.45ms。本窗没有天然业务页面流量，故仍属于
coverage anomaly。

窗后只读端到端烟测：

| 端点 | 结果 | 判断 |
| --- | --- | --- |
| `/health` | 200 / 41ms | backend 可达 |
| `/api/v1/zsxq-insights/overview` 冷 / 暖 | 200 / 3.111s / 30.689ms | 缓存有效；冷态仍有远端 SQL 尾延迟 |
| `/api/v1/zsxq/topics/local` | 200 / 473.568ms | 正常返回 |
| `/api/v1/zsxq/topics/local/tabs` | 200 / 235.696ms | 正常返回 |
| `/api/v1/zsxq/system-sync-scheduler/status` | 200 / 530.214ms | scheduler 状态可达 |

三类必须单列的异常：

- **低频高延迟**：11 条慢 SQL 每个指纹仅 1–2 次，最大 2.305 秒。
- **页面/API 很慢但无匹配慢 SQL**：天然业务请求为 0，无法判断；窗后 topics/tabs/status 均无匹配
  `>=1s` SQL，但烟测不足以代表全站。
- **后台任务/连接很久但无前台端点**：10 条 old-root 内容任务累计 3,813.963 秒；B站 24 个周期累计
  217.205 秒；YouTube 两轮累计 57.31 秒。wall clock 不能自动归为 Session hold。

Celery 日志识别 65 个 completed、0 retry、0 failed；其中 workflow notification 63 个/149.437 秒，PDF
clean 2 个/6.394 秒。数据库 durable 记录却有 5 个 PDF clean 与 5 个 ZSXQ publish，进一步证明存在日志集合
之外的远端执行器。durable notification 57/57 sent、attempt 57、max attempt 1；ZSXQ natural sync run 为 0。

## 程序模式复核

- Insights 冷态 3.111 秒、热态 30.689ms，TTL/缓存命中有效；冷查询 executor 34.990ms，主要时间不在
  PostgreSQL executor。不能用热缓存掩盖冷路径。
- 本窗没有天然 topics/local、tabs、sync/status 或其他页面流量，无法证明 N+1、大字段、超大分页、重复聚合、
  隐藏页轮询或缓存失效未复发。
- 10 个旧根任务均成功不等于正确运行拓扑；旧运行时继续拥有 DB/broker 写权限是更上游的数据访问根因。
- 未见长 Session hold、外部 I/O 与事务交叉持有、失败重试风暴或重复通知投递；仅能说明当前日志已捕获窗口
  未出现，不能外推到未捕获远端日志。

## 表规模、顺序扫描与索引复核

| 表 | total / heap / index | live / dead | seq / idx scan | 索引命中判断 |
| --- | --- | ---: | ---: | --- |
| `publication_tasks` | 140MiB / 17MiB / 13MiB | 13,949 / 1,282 | 7,106 / 3,763,914 | 99.81%；HOT update 1.22% |
| `zsxq_topics` | 129MiB | 12,637 / 1,584 | 4,189 / 891,985 | 99.53%；HOT 0.71% |
| `zsxq_topic_comments` | 118MiB | 41,333 / 5,644 | 481 / 358,686 | 99.87%；HOT 70.46% |
| `zsxq_attachments` | 78MiB | 33,906 / 37 | 630 / 1,135,784 | 99.94%；HOT 0.12% |
| `zsxq_sync_runs` | 37MiB | 1,062 / 46 | 291 / 337,756 | 99.91%；HOT 0.33% |
| `external_documents` | 6.9MiB | 1,817 / 146 | 1,062 / 84,987 | 98.77%；HOT 0% |

精确重复索引为 0。低/零扫描候选仍包括 attachments PDF group/tab（约 5,088kB、scan 1）、topics
group/type/create（约 3,720kB、scan 1）、publication Publisher-MP3 match（168kB、scan 0）、external
TTS（232kB、scan 0）与 summary（184kB、scan 0）。统计自 08-08 实例启动累计，不能据单窗删除索引。

代表 EXPLAIN：点查 0.060ms、频道图表 3.241ms、all-tabs config 0.024ms、Publisher 列表 0.061ms、内容
管线列表 0.124ms、任务轻/宽投影均 0.024ms、ZSXQ system runs 1.777ms、Insights canonical 34.990ms。
没有真实 executor 扫描、buffer 或锁证据支持 CREATE/DROP INDEX，因此本轮无 DBeaver DDL 脚本。

## 已落地修复、测试与运行态验证

- 新增只读 `scripts/diagnostics/daily_db_runtime_snapshot.py`，统一输出 durable task、Publisher 复发指标、
  notification、ZSXQ run 与当前数据库 client source；JSONB 子树在 Python 汇总，避免诊断 SQL 类型误判。
- 对诊断器先写失败测试，再实现混合 JSONB/list/NULL 汇总；更新远端旧运行时、Publisher、YouTube、HTTP
  coverage Bug 记录、Session 纪律与 recurrence registry。
- 实际数据库查询均为 `SET TRANSACTION READ ONLY`，EXPLAIN 只读；未终止远端会话、未执行 DDL/回填。
- 窗后 backend health 与四个重点 ZSXQ API 均 200；这是链路可达性，不替代自然任务或完整后窗。
- 定向 runtime snapshot/日志分析器 **33/33**；完整 unit 首次在沙箱内仅因 34 个 loopback bind
  `PermissionError` 失败，授权后同一命令 **1,719/1,719 OK（100.724s）**。
- `compileall`、recurrence JSON 语义检查、Bug strict（209/209）与 `git diff --check` 均通过；完整测试后
  `scheduler.log` mtime 仍为 `2026-09-24 06:40:20 +0800`，没有再次污染生产调度器日志。
- 本轮没有常驻业务代码变更，不需要执行重启安全门或重启 backend/Celery/scheduler。

## 待人工动作、风险与未捕获边界

1. **P0 old runtime**：在 `100.94.17.45` 对应 MacBook Pro 上停止所有持当前生产 PostgreSQL/Redis
   凭据的 backend、Celery、Watchdog、scheduler 和手工脚本。不能证明全停时，应轮换凭据并隔离 broker
   namespace。终止当前 6 个 DB 会话只能止血，不能根治，故本轮未执行。
2. **YouTube**：在 DBeaver 人工复核并执行
   `docs/sql/disable_unavailable_youtube_channels_20260920.sql`；脚本含精确保护、timeout、验证与回滚，仍不由
   无人值守巡检执行。
3. `pg_stat_statements` 未安装；日志缺 pool acquire、fetch/deserialize bytes、应用名与不可伪造 deployment
   identity，秒级差值无法进一步精确分摊。
4. 天然业务 HTTP 为 0；慢 SQL 阈值以下的 N+1、分页、宽字段、重复聚合和轮询无法被当前窗证伪。
5. 远端应用日志不在本机集合；“本机日志无失败”不能覆盖远端 worker 的内部等待与 Session 生命周期。

## 下一轮明确验收计划

1. 取得远端旧栈停机/凭据隔离动作记录；`pg_stat_activity` 中该来源生产连接归零，并要求新连接携
   deployment/host/process identity。
2. 隔离后各取至少一条自然 PDF clean、ZSXQ publish、MP3→Publisher：必须仅携当前 root，无旧 reason、
   replacement、素材不可达或 child failure；再观察完整 24 小时窗口。
3. YouTube 脚本人工执行后，channel/video 连续两个自然周期不再出现 id 21/56，再观察下一周期。
4. 恢复天然页面/API 样本后重算全站 request、失败率、累计、P50/P95/max，优先复核 overview、topics/local、
   tabs、sync/status、MP3 audio-assets，并补 pool/fetch/bytes 分段。
5. 继续把真实 SQL、request/task id、Session、PgBouncer 和 executor 同窗关联；只有 executor 与 buffer/scan
   明确恶化时才提出 DDL。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| old checkout + remote client `100.94.17.45` | MacBook Pro 旧部署共享生产 DB/broker，任务无强 deployment identity | 只读 runtime 快照、host/client 复发契约与 Bug 登记 | 10/10 old-root 任务；6 个远端 DB 会话 | **是，跨工作流** | 人工停旧栈或轮换凭据/namespace | 远端连接归零；三类自然任务当前 root；完整窗无复发 |
| 11 个低频慢 SQL 指纹 | executor 快，时间主要在 driver/PgBouncer/私网/fetch 边界 | 既有有界访问与只读 EXPLAIN；未盲目 DDL | 13.393s；Insights 1.607s vs executor 34.990ms | **是，恶化** | 缺 pool/fetch/network 分段 | 应用与 executor 同窗稳定并取得物理分段 |
| YouTube id 21/56 | 远端无可用内容但本地仍启用 | 结构化失败身份、受保护人工脚本 | channel 49/51、video errors=2 | **第八窗** | 人工执行 SQL | 双自然周期无两源，再观察一周期 |
| 天然 HTTP coverage=0 | 本窗无业务访问，非 parser 回归 | coverage anomaly 纪律和烟测回放 | 6 个请求均为巡检烟测 | 未知 | 全站自然行为未捕获 | 下窗自然请求三路日志数量级一致 |
| Insights 冷态聚合 | 冷缓存触发聚合，远端边界放大 | 既有 TTL/single-flight/stale cache | 冷 3.111s、热 30.689ms；executor 34.990ms | 冷态仍慢 | 缺 pool/fetch bytes | 自然冷/暖命中率和阶段分段齐全 |
| B站/通知外部任务 | 当前无失败风暴 | 有界周期与 durable attempt 观测 | B站 24/24；通知 57/57、max attempt 1 | 本窗未复发 | 上游网络断点仍未知 | 后续完整窗保持成功，故障时采 DNS/proxy/TUN 现场 |
