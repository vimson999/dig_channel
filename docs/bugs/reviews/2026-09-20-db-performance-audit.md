# 2026-09-20 每日数据库性能与错误巡检

## 结论

巡检固定窗口为 **2026-09-19 06:00:00–2026-09-20 06:00:00（Asia/Shanghai）**，覆盖
`slow_query.log`、`app.log`、`backend.log`、`scheduler.log`、所有 `celery/*.log` 与六个独立调度器日志，
并以只读数据库快照、`pg_stat_user_tables/indexes`、`EXPLAIN (ANALYZE, BUFFERS)`、PgBouncer、路由、
持久任务和真实 API 做交叉核对。

本轮**没有根因达到 CLOSED**。慢 SQL 数量与累计耗时小幅恶化，但 P95/max 改善，代表 executor
只有 0.025–35.202ms，且没有锁、长事务、连接等待风暴或索引证据，继续判定为远程数据库链路/驱动尾延迟
`OPEN/OBSERVING`，不盲目加索引。最明确的新增并反复问题是两个已被 YouTube API 判定为不存在或不可用的频道
仍保持本地启用，导致两个每日同步连续多日各失败 2 个来源；失败身份观测已修复并完成运行态替换，但停用来源
需要人工确认，下一自然周期也尚未到，因此仍为 `OPEN/REPEATED`。

## 与上一轮基线的量化比较

| 指标 | 09-19 基线 | 本轮 | 变化 | 判断 |
|---|---:|---:|---:|---|
| 完成慢 SQL 数量 | 11 | 14 | +27.3% | 恶化，但低样本 |
| 慢 SQL 累计耗时 | 15.149s | 17.058s | +12.6% | 恶化 |
| 慢 SQL P50 | 1.154s | 1.036s | -10.3% | 改善 |
| 慢 SQL P95 | 2.289s | 1.732s | -24.3% | 改善 |
| 慢 SQL最大值 | 2.289s | 1.993s | -12.9% | 改善 |
| PgBouncer 六类连接故障 | 0 | 0 | 持平 | 无复发证据，不代表历史根因消失 |
| connection hold / idle in transaction | 0 / 0 | 0 / 0 | 持平 | 未见 Session 跨 I/O |
| B站情报真实周期 | 23/23 成功 | 24/24 成功 | 完整绿窗 | 改善，原网络断点仍未知 |
| Publisher 自然任务 | 5 条失败 | 0 条新任务 | 无样本 | 不能判定改善 |

PgBouncer 记录 287 次后端连接，全部来源为 Tailscale 地址 `100.96.3.12`，无代理 TUN 回退；
1440 个统计点的 wait P50/P95/max 为 14.05/18.11/117.72ms，query 为 40.36/47.02/120.02ms。
巡检时数据库目标路由仍为 `utun6`，本机到数据库网络 RTT 约 27ms。本窗 statement timeout、lock timeout、
query/connect timeout、deadlock、waiting lock 均为 0。

## 主要问题排序与根因

排序综合累计影响、P95/max、失败率、用户/页面影响与复发次数，而非仅按出现次数。

1. **YouTube 两条每日任务固定失败（新增定位、长期复发）**：00:03 频道同步 51 个中成功 49、失败 2，
   13.57s；01:00 视频同步处理 49 个、errors=2，服务耗时 39.75s。频道任务自 09-10、视频任务自
   09-09 起每天以相同数量失败。只读对账定位到 `channels.id=21/56`；其最后快照分别停在 09-09 与
   06-18。使用生产凭据批量查询两个外部 channel ID，YouTube API 返回 `totalResults=0/items=[]`。
   根因是远端来源失效而本地仍启用，并非数据库 executor、Session 或索引。旧通知只保留计数又延长了发现时间。
2. **数据库调用尾延迟（反复）**：14 条慢 SQL 累计 17.058s。最高累计指纹
   `abbd31d685e07695`（B站 competitor video，2 次/2.782s/P95 1.732s）；最高单次
   `fd28c66e902e8061`（B站 schedule config，1.993s）。其余主要为 report-audio recovery
   2.248s、ZSXQ all-tabs config 2.063s、Publisher cleanup 2.020s、ZSXQ insights aggregate
   1.686s。实时同形执行计划仅 0.025–35.202ms，零锁等待和长 hold，证据不支持 PostgreSQL executor 慢。
3. **ZSXQ overview 缓存刷新尾延迟（观察）**：固定窗 request `5ce63ffe` 先用 stale cache 在
   281.61ms 返回 200，随后同一 Web PID 后台刷新 3.636s；刷新含 1.686s aggregate、1.165s topic detail
   与 264.77ms SQL。请求没有跨到该刷新 Session，属于缓存陈旧触发的后台成本，不是前台持有事务。
   重启后真实 overview 验收为 200/1.797s，并匹配 topic detail SQL 1.211s；这进一步支持调用链尾延迟仍在。
4. **Publisher queue-repair（反复、无本窗样本）**：本窗没有新 `publication_tasks`、Publisher 或
   queue-repair 审计，不能把“0 次失败”写成不复发。09-19 五条 repair-limit 失败保持 OPEN/REPEATED，
   下一自然任务仍需采 queue 长度/截断、unacked、worker active/reserved/scheduled 和 delivery 时间。

## SQL 指纹、页面/API 与调用链

| 指纹 / 来源 | 次数 | 累计 | P95/max | 归因 |
|---|---:|---:|---:|---|
| `abbd31d685e07695` `bilibili_intelligence_service.py:307` | 2 | 2.782s | 1.732s | 后台 B站整点同步；executor 代表值远低于应用耗时 |
| `7c472aa43eb81ca0` `report_audio_recovery.py:214` | 2 | 2.248s | 1.235s | 后台窄 ID 恢复查询，无前台 request id |
| `60ff7467696400d3` `zsxq_all_tabs_auto_sync_service.py:157` | 2 | 2.063s | 1.042s | 停用配置读取；未投递 job |
| `f5ee1b5df0b1bd8a` `publisher_queue_repair.py:836` | 2 | 2.020s | 1.013s | cleanup 扫描，本窗无业务候选 |
| `fd28c66e902e8061` `bilibili_intelligence_scheduler_service.py:57` | 1 | 1.993s | 1.993s | 独立调度器配置读取 |
| `674016d39deab3f5` `zsxq_insights_service.py:448` | 1 | 1.686s | 1.686s | stale cache 后台 refresh |
| `5c010eda536e0320` `zsxq_insights_service.py:572` | 1 | 1.165s | 1.165s | stale cache 后台 refresh |
| 其余 outbox / Publisher pending/processing | 3 | 3.099s | 1.036s | 后台轮询；无失败与长事务 |

固定窗只捕获 26 个完成 HTTP 请求（总耗时 1.349s、P50/P95/max 0.80/390.60/441.20ms）。
14 个成功、12 个失败；失败全部是 09-19 06:07 单测/探针的 `/access/<redacted>`、`/index.html`、
`/api/v1/channels`、`/login.html` 各 3 次，不是自然业务 5xx。重点入口为：

| 入口 | 请求 | 成功/失败 | 累计/P95/max | SQL 关联 |
|---|---:|---:|---:|---|
| GET sync/status (`e8615958`) | 1 | 1/0 | 441.20ms | 102.10ms + 100.74ms，无慢 SQL |
| POST topics/local (`a80008ac`) | 1 | 1/0 | 390.60ms | 129.01 + 57.79 + 28.75 + 27.81 + 26.83ms |
| GET insights/overview (`5ce63ffe`) | 1 | 1/0 | 281.61ms | 前台无慢 SQL；后台 refresh 3.636s |
| POST topics/local/tabs (`24d2861d`) | 1 | 1/0 | 213.16ms | 105.70 + 32.64ms |
| POST browser-import/history | 3 | 3/0 | 7.44/3.72/3.72ms | 无慢 SQL |
| GET health | 4 | 4/0 | 5.10/3.69/3.69ms | 无慢 SQL |
| 四类测试/探针路径 | 15 | 3/12 | 9.89/2.10/2.10ms | 无慢 SQL，排除业务失败率 |

低频高延迟：固定窗最高为 sync/status 441.20ms；重启冷态 overview 为 1.797s。页面/API 慢但没有匹配
慢 SQL：固定窗四个重点入口均低于慢 SQL 阈值，overview 的主要刷新在响应之后。后台/连接持有很久但无前台端点：
没有 `connection_held`，但 B站、recovery、Publisher/outbox 慢 SQL 属于独立后台来源。

## 后台任务与错误归因

- Celery 原始分析为 43 条 `workflow_notify` completed、0 retry/failed，累计 73.140s、P50/P95/max
  2.128/2.444/2.553s；持久 outbox 对账显示固定窗 22 条 email sent、最大 attempt=1、无重复 dedupe key。
- B站情报 24 个真实周期全部 6/6 completed，累计 199.888s、P50/P95/max 8.219/9.661/9.919s；
  原 24/24 DNS 失败的物理断点仍未知，保持 OPEN/OBSERVING。
- B站充电 00:00 一个周期 6/6 成功。
- YouTube 频道/视频各一个自然周期，分别 49/51 和 errors=2，见新增 Bug。
- 原始分析器的 14 条 `server_closed` 和 2 个 SQL traceback 均来自 09-19 06:07–06:12 的测试模拟：
  `for test`、`publish-task-1`、`sync-disconnect-1`、fake timeout 与 SQLite DDL；PgBouncer 同期真实故障为 0。
  它们没有计入生产错误。巡检后诊断曾因 shell 引号错误产生 2 条只读 ad-hoc SyntaxError，已 rollback、无写入，
  且在固定窗之外，不混入本轮统计。

## 表规模、扫描与索引结论

| 表 | 总大小 | idx scan 占比 | dead tuples | 结论 |
|---|---:|---:|---:|---|
| `publication_tasks` | 137MB（TOAST 107MB） | 99.80% | 2,273 | 大 JSON/宽行是主要空间；点查 0.035ms，无新索引依据 |
| `zsxq_topics` | 129MB（TOAST 79MB、索引 36MB） | 99.54% | 1,584 | insights 代表计划 35.202ms；不因应用 1–2s 盲加索引 |
| `zsxq_topic_comments` | 118MB | 99.87% | 5,644 | dead 较高但 HOT 70.46%，继续观察 autovacuum |
| `zsxq_attachments` | 78MB | 99.95% | 37 | 低扫描 5MB group/tab PDF 索引仅 scan=1，候选低价值 |
| `zsxq_sync_runs` | 37MB | 99.92% | 46 | 代表查询 1.545ms |
| `external_documents` | 约 7MB | 98.77% | 146 | 无慢指纹与新增证据 |

没有精确重复索引；部分重叠/低扫描索引仍仅为观察候选，不能仅凭 `idx_scan` 低就删除。当前数据库没有
`pg_stat_statements`，无法把累计 executor 指纹做长期归因；这是未捕获边界，不是“没有问题”。本轮未执行或建议
任何 CREATE/DROP INDEX、ALTER TABLE、回填或 VACUUM FULL。

## 已落地修复、测试与运行态验证

- 两个 YouTube 调度器的失败通知与 warning 现在保留最多 10 个失败频道的 id、名称和错误；两条新 TDD
  在修复前因 `summary.failures` 缺失失败，修复后相关 15 项通过。
- 完整 unit：受限沙箱 1695 项中 34 项仅因禁止绑定 `127.0.0.1` 报 PermissionError；允许 loopback 后原命令
  重跑为 **1695/1695 OK（89.224s）**。`compileall`、worker app-factory 28 文件、Celery revoke 321 文件、
  Bug strict 205/205、registry JSON 与 `git diff --check` 均通过。
- 重启安全门先后读取 46 条近期任务、0 条活动 ZSXQ sync；标准脚本冻结 17 个队列、关闭 API 入队并通过
  第二次门禁，未使用 FORCE。新 backend PID 23439，bootstrap **17/17 worker、6/6 scheduler、errors=[]**；
  B站 PID 24032、YouTube channel PID 24049、YouTube video PID 24056。
- 重启后 `/health` 200/0.69ms，ZSXQ status 200/177.20ms、alive=true、两条 all-tabs 配置均 enabled=false，
  overview 200/1.797s。测试期间没有再把 partial/fake timeout/SQLite DDL 写入 B站独立调度器文件。
- 这只证明代码与进程已替换；YouTube 两个自然周期要到 09-21 00:03/01:00，不能提前写成根因已解决。

## 人工动作、风险与回滚

人工脚本：`docs/sql/disable_unavailable_youtube_channels_20260920.sql`。它不是索引 DDL，而是精确保护的
业务配置 DML；默认 `ROLLBACK`，带 5s `lock_timeout`、30s `statement_timeout`、前置身份/快照核对、
`RETURNING`、验证与回滚语句。执行人必须先重新调用 YouTube API 确认两个 external ID 仍为 0 results，
再把末尾改为 COMMIT。风险是平台临时不可达被误判为永久删除；因此本轮没有无人值守执行。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
|---|---|---|---|---|---|---|
| `YT-SOURCE-21-56` | 远端频道 0 results，本地仍 active；通知丢身份 | 失败详情 TDD + 运行替换；人工 SQL | PID 24049/24056 已启动 | 是，连续多日 | 人工复核并在 DBeaver 停用或替换来源 | 00:03 为 49/49、01:00 errors=0，再一周期不复发 |
| `DB-SESSION-LIFECYCLE` | executor 快、应用 1–2s，疑似远程链路/驱动尾延迟 | 既有 Session 边界和四源观测 | PgBouncer 0 故障、0 hold，route utun6 | 历史反复 | 原私网断点未知；下次异常同秒采证 | 完整后窗无慢尾延迟或捕获并修正物理断点 |
| `ZSXQ-CACHE-REFRESH` | stale cache 后台刷新需 3.636s | 前台 stale-while-refresh 已存在 | 前台 281.61ms；重启冷态 1.797s | 未显著恶化 | 低流量，缺长期 executor 指纹 | 持续量化 cache hit/miss、后台耗时及请求隔离 |
| `PUBLISHER-QUEUE-REPAIR` | 可见性误判断点尚缺现场证据 | 既有防线保留 | 本窗无自然任务 | 无法判断 | 无样本不是未发生 | 下一候选完整 queue/unacked/worker 现场，无换 ID 自饿死 |
| `SCHEDULER-LOG-CONTAMINATION` | import 时挂生产 FileHandler | 移到显式 main，已重启 | PID 24032；全量测试未再污染独立文件 | 固定窗为旧污染 | 尚无新 PID 自然整点周期 | 下一真实 6/6 后仍无测试指纹，后窗继续验收 |
| `BILI-EXTERNAL-CONNECT` | 原 DNS/proxy/TUN 断点未捕获 | 观测规则保留 | 完整 24/24 周期 6/6 | 本窗未复发 | 恢复原因未知 | 异常时采 DNS/目标/代理/TUN；继续完整绿窗 |

## 未捕获边界与下一轮计划

本窗 HTTP 只有 26 个、重点业务入口各 1 次且多为上一轮主动探针；没有真实 Publisher、ZSXQ sync run、
音频/视频重任务或数据库故障注入样本。`pg_stat_statements` 不可用，无法证明未记录指纹没有发生；测试通过、
进程 ready 和一次页面 200 也都不能替代自然业务验收。

下一轮必须优先：人工配置执行状态与两个 YouTube 自然周期；新 B站 PID 的真实 6/6 与日志隔离；Publisher
首个自然候选的完整可见性现场；固定 24 小时 SQL/HTTP/Celery/PgBouncer 四源重算；overview cache hit/miss
与刷新耗时；六张重点表 dead tuples、idx scan 和同形 EXPLAIN。只有根因落地、自然运行成功且后续窗口未复发，
才允许改为 CLOSED/RUNTIME_REPLACED。
