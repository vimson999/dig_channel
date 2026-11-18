# 2026-10-26 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 2026-10-25 06:00:00 至 2026-10-26 06:00:00（Asia/Shanghai；数据库 UTC-naive 边界为
2026-10-24 22:00:00 至 2026-10-25 22:00:00）。本轮不能写成“已解决”。慢 SQL 为 14 条/17.700 秒，数量与
上窗持平、累计 -9.9%、P95/max -9.3%/-9.1%；但 HTTP 只有 15 个完成请求，主要来自上一轮巡检自测，不能把低
流量下的下降当作根治。

P0 反而显著恶化：12/12 个目标 durable task 全部来自旧 checkout，其中 audio clean 1、ZSXQ publish 1、
MP3 workflow 1、Publisher 9；Publisher 失败率 33.3%、累计壁钟 6,951.350 秒、P95 2,191.850 秒。当前
Watchdog 重投一条旧根 MP3 后，本机 worker 失败返回，而旧 actor 随后完成原任务，形成共享 DB/Redis/broker 的
直接竞态。窗后旧 Mac 离线和远端会话为 0 只是点快照，不能抵消 durable 证据。状态继续 **OPEN / REPEATED /
WAITING_DISTRIBUTED_ISOLATION**。

本轮只 TDD 修正只读诊断器的 processing orphan actor 归因盲区，并完成生产快照重放；没有修改业务运行代码、
生产配置或 DDL，无需重启。根因闭环仍取决于人工物理停旧栈或凭据/namespace 隔离后的完整自然窗口。

## 修复前后量化基线

| 指标 | 上一窗口 | 本窗口 | 变化与判断 |
| --- | ---: | ---: | --- |
| 慢 SQL | 14 / 19.638s | 14 / 17.700s | 数量持平，累计 -9.9%；流量低，不能判闭环 |
| 慢 SQL P50 / P95 / max | 1.117 / 2.106 / 2.157s | 1.114 / 1.910 / 1.960s | -0.3% / -9.3% / -9.1% |
| HTTP | 23 完成，21 成功 | 15 完成，13 成功 | -34.8%；两失败为巡检误用旧 Celery 路径的 404 |
| HTTP P50 / P95 / max | 224.15ms / 1.726 / 3.441s | 1.44ms / 1.956 / 3.612s | 组成不同；overview 仍是低频高延迟 |
| Celery 日志 | 41 完成，0 失败 | 61 完成，0 failed 事件 | 60 通知；1 个 MP3 返回 `False`，日志聚合不能视为业务成功 |
| durable 旧根任务 | 1/1 | 12/12 | 从单点扩大到完整内容/发布链，明确恶化 |
| B站情报 | 23/23、161/161 | 24/24、168/168 | 全绿；P95/max +4.2%/+6.4% |
| PgBouncer wait/query P95 | 42.62 / 74.14ms | 45.95 / 72.89ms | wait +7.8%，query -1.7%；八类错误仍 0 |

## 按累计耗时、尾延迟、失败率与复发排序

| 排名 | 问题/复发键 | 量化证据 | 影响 | 状态 |
| ---: | --- | --- | --- | --- |
| 1 | 旧运行体 / `RUNTIME-TOPOLOGY-RELOAD` | 12/12 old-root；Publisher 9 条累计 6,951.350s，3 失败，P95 2,191.850s | 旧代码与当前 Watchdog 竞态，绕过现行审计 | OPEN/REPEATED/WAITING_DISTRIBUTED_ISOLATION |
| 2 | MP3 宽行失败回写 / `d5fd934b6efe6e4c` | 1 次/1.910s；executor 0.042ms | 当前 worker 干扰旧 actor 任务，宽 JSON/TOAST 放大网络 fetch | OPEN/REPEATED |
| 3 | Insights / `674016d39deab3f5` | 1 次/1.960s；executor 40.551ms；overview 3.612s | 低频页面冷态尾部 | OPEN/REPEATED/OBSERVING |
| 4 | notification due / `c1e57cc097d3e051` | 3 次/3.496s，P95 1.235s | 后台扫描；较上窗累计 -22.6% | OPEN/OBSERVING |
| 5 | YouTube 21/56 | channel 49/51、video errors=2，第 23 窗 | 每日固定失败与噪声 | OPEN/REPEATED/WAITING_MANUAL_CONFIG |
| 6 | audio catalog / `be891b5c1af362da` | 自然请求 0 | 高影响旧问题无本窗样本 | OPEN/REPEATED/UNOBSERVED |

## 页面、API、后台任务与 SQL 关联

### SQL、Session、PgBouncer 与 executor

- 14 条应用慢 SQL 累计 17.700 秒；`publication_tasks` 5 条、`zsxq_topics` 1 条。应用 SQL error、statement/
  lock/connection timeout、deadlock、waiting lock、idle-in-transaction 与长 connection hold 均为 0。
- 主要指纹：notification due 3/3.496s；Publisher processing/pending repair 各 2/2.356s、2/2.275s；B站情报
  3/3.285s；Insights 1/1.960s；MP3 `_mark_failed` 宽行点查 1/1.910s；all-tabs config 1/1.362s。
- PgBouncer 1,440 个样本、288 次连接（12/h），来源 `100.96.3.12`；proxy fallback 与八类错误为 0。
  wait P50/P95/max 30.33/45.95/133.40ms，query 65.31/72.89/130.07ms。
- 生产 `EXPLAIN (ANALYZE, BUFFERS)`：publication PK 0.042ms、chart 3.142ms、all-tabs 0.023ms、Publisher
  list 0.054ms、content list 0.222ms、recent wide/narrow 0.028/0.025ms、sync runs 1.638ms、Insights
  40.551ms。MP3 应用点查约为 executor 的 45,000 倍，Insights 约 48 倍，指向网络/driver/TOAST fetch，
  不是 PostgreSQL executor 或缺索引。

### 页面与 API

固定窗 15 个完成请求、10 个端点，13 成功、2 个巡检旧路径 404，累计 9.498 秒，P50/P95/max
1.44ms/1.956s/3.612s。overview 1/1 为 3.612s并匹配 1.960s SQL；Celery status 5/5、累计 3.803s、max
1.956s，但无匹配慢 SQL，属于 worker/broker 探测；topics/local 0.934s、system scheduler 0.601s、sync runs
0.326s、tabs 0.218s。

窗后真实只读 E2E：health 200/0.113s、overview 200/0.150s、topics/local 200/0.817s、tabs 200/0.285s、
sync runs 200/0.419s、system scheduler 200/0.657s、scheduler 200/0.039s、Celery status 200/2.110s。
Celery 当前 17/17 service running、paused=0、queue=0、topology conflict=false、broker ok。

低频高延迟为 overview 3.612s；页面慢但无慢 SQL 为 Celery status 1.956s；后台很久但无前台端点为旧根 MP3
3,609.061s、Publisher max 2,790.105s、YouTube video 约 52.460s、B站情报 max 15.208s。未匹配 >=1s SQL
不能被误报为无问题。

### 后台任务与外部调用

- Celery 日志识别 60 个通知任务，累计 157.200s，P50/P95/max 2.936/3.804/3.980s；另有当前 MP3 worker
  处理旧根重投 1 次/4.737s并返回 `False`。durable notification 49/49 sent、attempt max=1，无失败风暴。
- 生产快照：audio clean 211.345s、ZSXQ publish 147.148s、MP3 3,609.061s，均 old-root；Publisher
  6 completed/3 failed。当前日志只有通知消费和 Watchdog 干预，旧根业务执行不在当前 worker 日志集合。
- processing orphan 快照在修复口径后为 closed=2、actor_missing=1；后者只能归为旧/未知 writer。快照曾把
  `queue_repair.actor` 投影到 processing repair，并把历史已恢复行误计；已用测试和生产重放纠正。
- B站情报 24/24 周期、168/168 competitor 成功，累计 321.868s；YouTube 固定 21/56 第 23 窗失败。
- 固定窗无 upload-only、完整当前根 ZSXQ sync 或自然 audio-assets；21523、21173、分片耗尽及音频目录均按
  未捕获处理，不能用 0 次关闭。

## 程序模式与表/索引复核

真实 SQL 未发现新 N+1、超大分页、重复去重聚合或 Session 跨外部 I/O。`publication_tasks` 宽 JSON/TOAST 仍是
风险，但 executor 点查 0.042ms；禁止删除原始 JSON 审计字段。当前最大程序性问题是跨部署共享队列导致重复投递/
竞态，不是加 timeout、重试或缓存能解决。

| 表 | 总大小 | live/dead | idx scan share | 判断 |
| --- | ---: | ---: | ---: | --- |
| `publication_tasks` | 152MB（18/120/14MB table/toast/index） | 14,730 / 822 | 99.84% | 宽行网络 fetch；PK executor 快 |
| `zsxq_topics` | 133MB | 13,981 / 1,665 | 99.52% | Insights executor 40.551ms |
| `zsxq_topic_comments` | 119MB | 45,884 / 6,093 | 99.85% | 无本窗热点 |
| `zsxq_attachments` | 79MB | 35,988 / 75 | 99.94% | 低扫描候选证据不足 |
| `zsxq_sync_runs` | 37MB | 1,077 / 61 | 99.90% | 代表查询 1.638ms |
| `external_documents` | 7.0MB | 1,817 / 146 | 98.73% | 无热点证据 |

没有 exact duplicate index；低扫描候选 attachments PDF group-tab 约 5MB（scan=1）、topics group/type 约 3.8MB
（scan=1），但统计自 2026-08-08 启动累积，缺 reset-window 写放大和反事实计划证据。`pg_stat_statements` 不可用，
当前无 waiting lock。**本轮不生成 CREATE/DROP INDEX、ALTER、回填或生产配置脚本。**

## 已落地修复、验证与人工动作

- TDD 修复 [`daily_db_runtime_snapshot.py`](../../../scripts/diagnostics/daily_db_runtime_snapshot.py)：processing
  orphan 从自己的 `processing_orphan_repair` 子树读取 actor，只统计当前 `orphaned_processing` 终态；空 actor 与
  缺失 actor 均保持可见，queue/processing 两类相互独立。
- 定向 6/6 与独立评审通过，生产只读重放得到 closed=2、actor_missing=1；这是诊断运行态验收，不代表旧运行体
  根因关闭。只改离线诊断器，无需运行栈重启，也未触发重启安全门。
- 完整 unit 1,742/1,742 通过（首次沙箱内 34 个本地 HTTP server 用例因禁止绑定 loopback 报
  `PermissionError`，允许 loopback 后原命令全绿）；另有定向/纪律 15/15、compileall、Celery revoke 边界
  326 个 Python 文件、worker app-factory 边界 28 个文件、Bug strict 213/213、registry JSON 与
  `git diff --check` 全部通过。测试不替代自然运行态后窗。
- **P0 人工动作：**停止旧 Mac 的 backend/Celery/Watchdog/scheduler/脚本；无法证明全停时轮换 PostgreSQL/
  Redis 凭据并隔离 Celery namespace。直接断当前 DB 会话不能阻止重连。
- YouTube 只保留既有 [`disable_unavailable_youtube_channels_20260920.sql`](../../sql/disable_unavailable_youtube_channels_20260920.sql)
  供 DBeaver 人工逐段复核；只处理 21/56，按脚本的 lock/statement timeout、验证与回滚执行。

## 未捕获边界与根因闭环状态表

固定窗 HTTP 主要为巡检自测，未覆盖全部页面；当前远端会话为 0、旧机 offline 都是窗后快照；
`pg_stat_statements` 不可用；自然 audio-assets、upload-only、当前根完整 ZSXQ 与 Publisher 均缺样本。

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 复发 | 剩余风险/责任动作 | 下一轮验收 |
| --- | --- | --- | --- | --- | --- | --- |
| old-root / runtime topology | 旧主机共享 DB/Redis/broker，未物理隔离 | actor/root/终态门禁 | 12/12 old-root；当前与旧 actor 竞态 | 是 | 人工停旧栈或轮换凭据/namespace | 完整 24h old/foreign/remote=0；当前根两条自然链成功 |
| processing actor 诊断 | 快照误用 queue actor 且计入历史修复 | TDD 独立子树与终态过滤 | 生产重放 2/1，独立评审通过 | 诊断已替换 | 不能识别缺 actor 的具体主机 | 下一窗持续输出且与日志/终态一致 |
| MP3 宽行 `d5fd...` | 旧根重投触发当前 worker 宽 JSON fetch | 既有 fail-closed/审计 | 应用 1.910s vs executor 0.042ms | 是 | 先隔离 actor，再量化 bytes/fetch | 无跨部署重投；当前根自然任务成功 |
| Insights `6740...` | executor 外 fetch/网络尾部 | 轻摘要与页详情拆分 | 1.960s vs 40.551ms；暖态 0.150s | 是 | 补分段指标 | 自然冷暖 P95 稳定且无全文搬运 |
| notification due | 周期 drain 的远程 fetch | outbox 状态机 | 3/3.496s；49/49 sent | 未恶化 | 关联队列深度/provider | 完整窗无失败风暴且累计不反弹 |
| audio catalog | executor 外 fetch/网络尾部 | O(1)投影/single-flight | 本窗 0 自然请求 | 未捕获 | 不得按 0 次关闭 | 自然冷暖配对、分段/bytes 齐全 |
| YouTube 21/56 | 永久不可用来源仍启用 | 人工 SQL 已备 | 49/51、errors=2，第 23 窗 | 是 | DBeaver 人工停两源 | 连续两周期 49/49、errors=0 |
| B站 DNS/TUN | 历史断点未取证 | 有界周期/失败保留 | 24/24、168/168 | 本窗否 | 故障时抓物理现场 | 后窗尾延迟稳定；失败有同秒证据 |

下一轮先验收分布式隔离，再补当前根自然 PDF→ZSXQ、MP3→Publisher、audio-assets、overview、upload-only；复核
YouTube 21/56、notification drain 和所有目标表。未满足自然运行与后窗条件前不得标 CLOSED。
