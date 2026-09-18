# 2026-09-17 每日数据库性能与错误巡检

## 窗口、结论与上一轮基线

固定窗口为 `2026-09-16 06:00:00` 至 `2026-09-17 06:00:00`（Asia/Shanghai）。先读取自动化记忆、09-16 报告、数据库 Session 纪律、Bug 与 recurrence registry。本轮没有产品代码、生产配置、DDL、回填或重启。上一轮 P0 停机在本窗前 2 小时 25 分钟仍持续：PgBouncer 最后一条代理 TUN source 是 08:24:59，08:25:16 起恢复 `100.96.3.12` 和 TLS；backend 08:28:11 启动、08:28:55 完成 17/17 worker 与 6 个 scheduler、Watchdog bootstrap。当前 Tailscale `Running`，DB 目标路由为 `utun6`，`/health` 为 healthy。恢复动作发生在本轮巡检之前，不归功于本轮代码改动。

| 指标 | 09-16 报告窗口 | 本窗口 | 解释 |
| --- | ---: | ---: | --- |
| 应用慢 SQL / 累计耗时 | 18 / 29.122s | 10 / 18.974s | -44.4% / -34.8%；流量不同且本窗初段停机，不等于根因闭环 |
| 慢 SQL P50 / P95 / 最大 | 1.333 / 1.861 / 4.704s | 1.209 / 5.042 / 5.042s | P95 +171%，音频目录冷查询长尾仍在 |
| HTTP 完成 / 成功 / 失败 | 1,784 / 1,759 / 25 | 133 / 132 / 1 | 本窗唯一失败为 favicon 404；无已记录 5xx；低流量无法覆盖全部页面 |
| HTTP P50 / P95 / 最大 / 累计 | 163.59ms / 423.28ms / 5.112s / 未列 | 7.33ms / 751.11ms / 12.525s / 52.119s | P95 与最大恶化，网页清洗投递贡献最大 |
| Celery 成功 / 失败 / retry | 83 / 0 / 0 | 151 / 0 / 0 | 恢复后有真实业务任务；成功回调不等于所有外部通知已送达 |
| PgBouncer server crash / proxy source | 2,889 / 2,889 | 571 / 571 | 全部集中在恢复前；08:25:16 后均为 0 |
| PgBouncer query-wait episode | 2 | 1 | 恢复前日志 2 行、按秒去重 1 次；恢复后 0 |

恢复后的 `08:25:16–次日 06:00` 约 21.58 小时共有 261 条新 server connection，全部 source=`100.96.3.12`；server crash、proxy fallback、query/connect/wait timeout、TLS receive error、login/HBA error 均为 0。PgBouncer wait/query 的分钟统计 P95 为 48.17/78.70ms，建连率 12.10/h。当前 `SHOW POOLS` 为 `cl_waiting=0, sv_login=0, sv_used=6`；`SHOW SERVERS` 六条均指向 DB 私网地址并从 Tailscale IP 发出。恢复后窗口尚不足完整 24 小时，因此主故障保持 **OPEN / RECOVERED / OBSERVING**，不能标记 CLOSED。

## 全链路请求、任务与 SQL 关联

133 个 HTTP 请求覆盖日志中出现的 61 个 method/path，成功率 99.25%。仅有的 404 是 `GET /favicon.ico`（4.02ms，request `7542093f`），不是业务 API。完整逐端点请求量、成功/失败、累计、P50/P95、最大、慢 SQL 数与最慢 request id 见文末附表。以下按累计耗时、P95、失败率及用户影响排序：

| 页面/API 或任务 | 量 / 成败 | 累计、P95、最大 | SQL / 来源 / 判定 |
| --- | --- | --- | --- |
| `POST /api/v1/content-pipeline/web-submit` | 3 / 3 成功 | 21.962s、12.525s、12.525s | 最慢 `bc05ae0c` 返回 202，未命中 1s 慢 SQL；有多次 50–688ms SQL 与两次串行通知 outbox 入队。17:49:53–58 的通知段约 5.35s，17:49:48–52 仍缺阶段跨度标记，保持 OPEN |
| `GET /api/v1/content-pipeline/web-submissions` | 30 / 30 成功 | 12.449s、989ms、1.066s | 无匹配 1s 慢 SQL；前端仅可见且有 active 项时 10 秒轮询，`history.loading` 防重叠。频度和累计耗时继续观察 |
| `GET /api/mp3-video-workflows/audio-assets` | 2 / 2 成功 | 9.653s、5.255s、5.255s | `4b6beb43` / `b8d77c4c` 的同一 canonical SQL 为 5.042/4.002s，合计 9.044s，占本窗慢 SQL 47.7%；SQL origin `_build_audio_asset_catalog_snapshot`，冷目录一次有界读取 773 行 |
| PDF clean Celery | 3 / 3 成功 | 445.608s、195.106s、195.106s | 无 1s 慢 SQL/长 Session 关联；三条真实 Web 提交任务均 `completed/done/100`，31/31 canonical PDF 资产 `cleaned`，审计数据保留 |
| workflow notification Celery | 142 / 142 成功 | 399.355s、3.891s、6.498s | Celery 的 `succeeded` 包含去重返回；只读 outbox 查询本窗新建 98 条均 `sent`、attempt=1、全库 dedupe key 重复 0，provider `closed`、连续失败 0 |
| ZSXQ publish Celery | 3 / 3 成功 | 342.923s、154.596s、154.596s | 后台长任务，不挂在前台端点；无失败/retry/本窗 SQL 长持有证据，外部发布耗时仍需任务阶段细分 |

其余值得观察的前台端点：`GET /api/v1/rankings/all` 2 次/1.086s/P95 574.59ms、`GET /api/v1/bilibili-intelligence/charging-dashboard` 1 次/751ms、`GET /api/v1/data/table` 1 次/544ms，均无匹配的 1s 慢 SQL，不能把“无慢 SQL”误作无数据库成本。`/api/v1/data/chart-data` 2 次/706ms，代表 PostgreSQL executor 3.160ms。ZSXQ insights overview、topics/local、topics/local/tabs、sync/status 在此窗口 **没有前台请求**；仅有 Insights 后端 warmup SQL 1.665s 与 all-tabs scheduler 配置 SQL 1.008s，不能声明这些页面已获端到端验收。全部 61 个端点只代表已进入 request logger 的流量，未捕获的页面、离线客户端和日志缺口不算零故障。

## 慢 SQL 根因与程序模式

10 条慢 SQL 合计 18.974s。按累计耗时前三：音频目录指纹 `be891b5c1af362da` 2 次/9.044s/P95 5.042s；Publisher stale repair `25b2a425193817e3` 2 次/2.655s/P95 1.562s；notification due-provider drain `c1e57cc097d3e051` 2 次/2.344s/P95 1.300s。其余 Insights warmup 1.665s、Bilibili intelligence 1.209s、Publisher pending repair 1.047s、all-tabs config 1.008s。目标表慢样本命中 publication_tasks 3、zsxq_topics 1，另外四个目标表 0；后者只是慢日志阈值口径，不代表没有执行。

音频目录真实 SQL 为 `content_pipeline_assets` 按 `media_type='audio' AND status='cleaned'` 排序、上限 2,500 的窄列查询。当前表约 2,100 行，其中 cleaned audio 773 行；只读 `EXPLAIN (ANALYZE, BUFFERS)` 为顺扫 2,100 行、返回 773、shared read 222、executor **2.508ms**，而应用两次为 4.002/5.042s。已有目录快照 300s TTL、single-flight、文件检查前释放 Session；本窗一条 5.108s Session hold 中 SQL 占 5.042s、非 SQL 66ms，没有发现文件 I/O 跨持有或 N+1 复发。差距位于 PostgreSQL executor 之外的结果传输、驱动、PgBouncer 或网络等待边界；缺少该 request 的池等待和分段传输时间，不能再细分为某一个组件。数据从 08-29 的 280 个 cleaned audio 增至 773，冷查询需持续按行数与 payload bytes 对账；不应仅因顺扫加索引。

Web submit 的最慢 request `bc05ae0c` 在 17:49:46–58 返回 202；认证 SELECT 405ms、任务 INSERT 280ms、canonical SELECT 54ms、7 行 INSERT 67ms、task id UPDATE 688ms，随后两组 outbox SELECT/INSERT/UPDATE 串行执行。真实三批分别有 16、8、7 个 PDF，31/31 最终 cleaned；批量 canonical 正常路径仍为一次 SELECT/一次多行 INSERT，没有逐文件 N+1。前端 history 轮询是 visible + active + single-flight；但本窗 30 次/12.449s，是持续远端往返开销。Web submit 其余约 4 秒的阶段时间缺精确 marker，不能凭聚合日志认定全部是通知；需在接收/暂存、哈希、durable task、broker、两次 outbox 各边界加入分段耗时，再评审批量 outbox 与非阻塞响应的耐久性契约。此项仍 OPEN，没有以调 timeout、隐藏通知或丢审计数据止血。

Session / 错误：应用侧 statement/lock/query/connect timeout、server-closed、deadlock、SQL error traceback、长 idle-in-transaction 均未捕获；Watchdog 7 个短暂 offender episode，最多 2 连接、最长事务龄 0.439s、idle 龄 0.383s，无 admission defer。唯一大于 5s hold 与音频 SQL 一一匹配，非 SQL 仅 66ms。无 SQL 的后台外部 I/O 时长不能被这些计数直接证明为无连接持有；需要阶段 marker 或 pg_stat_activity 同时取证。

## 表、索引与 executor

当前目标表总大小：publication_tasks 135MB（TOAST 105MB）、zsxq_topics 129MB（TOAST 79MB）、zsxq_topic_comments 118MB、zsxq_attachments 78MB、zsxq_sync_runs 37MB、external_documents 7.0MB。索引扫描占比依次约 99.79%、99.54%、99.87%、99.95%、99.92%、98.78%；精确重复索引 0、waiting lock 0、当前 idle-in-transaction 0。publication_tasks/topics/sync_runs/attachments 的累计 HOT update 占比仅 1.25%/0.71%/0.33%/0.12%，写放大风险继续观察；这些为自 08-08 以来累计统计，不应当作单日增量。comments dead tuple 5,644，但本窗无对应高代价 SQL，先观察 autovacuum。

只读 EXPLAIN 的代表 executor：Publisher 点查 0.035ms、20 条任务列表 0.106ms、内容任务列表 0.220ms、chart 3.160ms、all-tabs config 0.027ms、Insights canonical aggregation 36.652ms、ZSXQ system sync 顺扫 1.716ms、音频目录 2.508ms。`pg_stat_statements` 未安装，无法给出数据库侧历史累计耗时。`ix_zsxq_attachments_pdf_assets_group_tab` 5.0MB / 累计 scan 1、`ix_zsxq_topics_group_type_create_desc` 3.7MB / scan 1 可能低价值，但缺真实命中 SQL 参数与对应 EXPLAIN，**不生成 DROP/CREATE SQL，不无人值守改索引**。本轮无待人工执行的新增 SQL；既有维护脚本仍需独立审核，不能据本窗应用耗时执行。

## 根因闭环状态表与下一轮验收

| 指纹 / 复发键 | 根因与本轮变化 | 已落地修复 / 运行态证据 | 是否复发、风险和责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- |
| DB 私网 route `198.18.0.1` / `DB-SESSION-LIFECYCLE`、`RUNTIME-TOPOLOGY-RELOAD` | 主机重启导致 Tailscale `NeedsLogin`，代理 TUN 接管；本窗继续 571 次 crash 到 08:25；随后登录/路由恢复 | 既有 restart safety fail closed；本轮未改代码；08:25 后 261 条 Tailscale source、0 crash/timeout，backend healthy、17/17 bootstrap | **复发后恢复，OPEN/OBSERVING**；启动依赖和失联告警未根治。责任：运行拓扑维护者补身份/路由前置依赖与自动恢复设计 | 从 08:25:16 起完整 24h 的 proxy source、crash、wait/connect/query timeout 全 0；服务、PgBouncer 和真实 API/任务持续 ready |
| `be891b5c1af362da` / `HOT-PATH-PAYLOAD-POLLING` | 音频目录 773 行一次冷取 4–5s，executor 2.508ms；旧附件逐项 N+1 未见复发 | 既有 canonical 快照/Session 释放仍在；两次真实页面 API 200、1 条 hold 5.108s 中非 SQL 66ms | **底层长尾反复，OPEN/OBSERVING**；缺 request 级池等待/传输 marker。责任：数据访问与网络观测 | 冷/强刷 SQL <=2、暖读 0，记录 response bytes、行数、driver/pool wait 与 P95；完整后窗无 >5s hold |
| `web-submit` request `bc05ae0c` / `HOT-PATH-PAYLOAD-POLLING` | 3 次累计 21.962s，串行 durable task、broker 与两次 outbox 往返；4s 阶段缺 marker | 既有批量 canonical 和哈希事务边界；3 个真实任务 completed，31/31 PDF cleaned，无长 Web hold | **残余端到端长尾，OPEN**；本轮未改产品代码，防止在缺证据时破坏通知/审计合同。责任：内容管线维护者补阶段 telemetry 后合并远端往返 | 下一自然多文件提交须输出各阶段耗时、SQL 数、outbox durable/sent 与无活动事务，P95 明显低于 12.525s |
| ZSXQ Insights / all-tabs cold SQL | warmup 1.665s、scheduler config 1.008s；executor 36.652/0.027ms | 既有 canonical 索引有效；DB 只读计划，无前台 ZSXQ 请求 | **OBSERVING，未捕获前台**；责任：页面/API 验收负责人 | 有授权真实 overview、topics/local、tabs、sync/status 请求，记录 request id、P95、payload、SQL origin 与任务状态 |

下一轮先复核网络故障完整后窗和音频目录/网页投递相同指纹，再扩展到全部有流量页面、任务及目标表；没有自然样本时明确记为未验收。只有代码/配置、定向回归、完整测试、真实运行与后续窗口零复发都齐备，才能把相应根因关闭。

## 验证与采集边界

- 定向日志分析器 30/30、worker app-factory 边界 28 文件、Celery revoke 边界 321 文件、`compileall`、`git diff --check`、Bug strict 200/200 已通过。
- 完整 unit 首轮 1,689 项：34 个本机回环端口沙箱权限错误，另 1 个排行榜测试硬编码 `2026-09-15`，与已存在的未提交排行榜工作区改动冲突。允许回环后同一完整命令重跑 1,689 项，34 个端口错误消失，仍有上述 1 项失败（实现返回当前 `2026-09-17`）；本轮不覆盖正在改动的排行榜代码，也不将全量测试写成通过。
- 运行态以真实自然 133 个 HTTP、151 个 Celery task、三条清洗任务和 31 个 durable PDF 资产验收；未人工投递新任务、未发送测试邮件、未重启。已有 `BACKEND_BOOTSTRAP` 结果、当前 PID、health、Tailscale route、PgBouncer SHOW POOLS/SERVERS 和只读 DB 快照互证。
- SQL 慢日志阈值为 1s（publication_tasks 有单独 500ms guard），因此“无匹配慢 SQL”不是“无 SQL”。PgBouncer wait/query 来自分钟平均，不能反推特定 request 的排队。当前索引统计是累计值且无 `pg_stat_statements`；没有证据支持生产 DDL。诊断中一次错误使用不存在的 `circuit_state` 列，已纠正为模型实际 `state` 并重新只读验证；它属于本轮 ad-hoc 查询，不是应用 SQL 事故。

## 已观测端点全表

下表由同一固定窗口日志分析器生成。毫秒；`慢 SQL` 为与 request id 匹配的 >=1s 查询，静态资源与 OPTIONS 也保留，避免只挑选已知业务入口。

| 方法与路径 | 请求 | 成功/失败 | 累计 ms | P50 / P95 / max ms | 慢 SQL | 最慢 request id |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `POST /api/v1/content-pipeline/web-submit` | 3 | 3/0 | 21961.61 | 4911.70 / 12525.47 / 12525.47 | 0 | `bc05ae0c` |
| `GET /api/v1/content-pipeline/web-submissions` | 30 | 30/0 | 12449.12 | 399.22 / 989.21 / 1066.17 | 0 | `640c5ca5` |
| `GET /api/mp3-video-workflows/audio-assets` | 2 | 2/0 | 9653.15 | 4397.96 / 5255.19 / 5255.19 | 2 | `4b6beb43` |
| `GET /api/v1/rankings/all` | 2 | 2/0 | 1085.77 | 511.18 / 574.59 / 574.59 | 0 | `0382fd9e` |
| `GET /api/v1/rankings/monthly-revenue` | 3 | 3/0 | 883.95 | 331.91 / 332.10 / 332.10 | 0 | `f528c66c` |
| `GET /api/v1/rankings/daily-revenue` | 3 | 3/0 | 881.17 | 200.45 / 505.64 / 505.64 | 0 | `9c81a770` |
| `GET /api/v1/bilibili-intelligence/charging-dashboard` | 1 | 1/0 | 751.11 | 751.11 / 751.11 / 751.11 | 0 | `61fe9f63` |
| `GET /api/v1/data/chart-data` | 2 | 2/0 | 706.25 | 351.76 / 354.49 / 354.49 | 0 | `2d9335f2` |
| `GET /api/v1/rankings/channel/:id/profile` | 1 | 1/0 | 687.30 | 687.30 / 687.30 / 687.30 | 0 | `8a2d8e77` |
| `GET /api/v1/data/summary` | 1 | 1/0 | 605.83 | 605.83 / 605.83 / 605.83 | 0 | `cdf72a14` |
| `GET /api/v1/data/table` | 1 | 1/0 | 543.57 | 543.57 / 543.57 / 543.57 | 0 | `0cedf1eb` |
| `GET /api/v1/celery/runtime-status` | 1 | 1/0 | 377.87 | 377.87 / 377.87 / 377.87 | 0 | `c347eda1` |
| `GET /api/mp3-video-workflows/jobs` | 1 | 1/0 | 366.67 | 366.67 / 366.67 / 366.67 | 0 | `30525a41` |
| `GET /api/v1/channels` | 1 | 1/0 | 251.22 | 251.22 / 251.22 / 251.22 | 0 | `0d47062b` |
| `GET /rankings.html` | 1 | 1/0 | 227.55 | 227.55 / 227.55 / 227.55 | 0 | `ef98ed30` |
| `GET /api/v1/rankings/yearly-revenue` | 1 | 1/0 | 187.75 | 187.75 / 187.75 / 187.75 | 0 | `54627e0a` |
| `GET /api/v1/channels/categories` | 1 | 1/0 | 128.78 | 128.78 / 128.78 / 128.78 | 0 | `bc7d4461` |
| `GET /js/env.js` | 5 | 5/0 | 40.80 | 7.33 / 14.22 / 14.22 | 0 | `6d036a0d` |
| `GET /js/rankings-trends.js` | 1 | 1/0 | 36.64 | 36.64 / 36.64 / 36.64 | 0 | `adbfebc8` |
| `GET /js/rankings-page.js` | 1 | 1/0 | 34.67 | 34.67 / 34.67 / 34.67 | 0 | `eb33e493` |
| `GET /js/shared-navigation.js` | 5 | 5/0 | 29.89 | 6.53 / 8.78 / 8.78 | 0 | `1be4afc7` |
| `GET /js/config.js` | 3 | 3/0 | 21.44 | 7.13 / 7.82 / 7.82 | 0 | `4c0f64be` |
| `GET /js/api.js` | 3 | 3/0 | 17.27 | 5.53 / 7.16 / 7.16 | 0 | `4073f0c9` |
| `GET /js/utils.js` | 3 | 3/0 | 15.86 | 4.94 / 6.84 / 6.84 | 0 | `ce944570` |
| `GET /css/main.css` | 2 | 2/0 | 14.17 | 6.12 / 8.05 / 8.05 | 0 | `46f519c2` |
| `OPTIONS /api/v1/auth/verify` | 6 | 6/0 | 14.03 | 1.32 / 5.25 / 5.25 | 0 | `4afab51b` |
| `GET /mp3-video-workflow.html` | 1 | 1/0 | 11.01 | 11.01 / 11.01 / 11.01 | 0 | `2e78301a` |
| `GET /js/download-count-highlight.js` | 1 | 1/0 | 10.57 | 10.57 / 10.57 / 10.57 | 0 | `db0d991a` |
| `GET /js/mp3-video-workflow.js` | 1 | 1/0 | 10.04 | 10.04 / 10.04 / 10.04 | 0 | `78fa3d38` |
| `GET /api/v1/auth/verify` | 6 | 6/0 | 7.63 | 1.26 / 1.47 / 1.47 | 0 | `a3e4f285` |
| `GET /js/dashboard.js` | 1 | 1/0 | 7.57 | 7.57 / 7.57 / 7.57 | 0 | `69f64749` |
| `OPTIONS /api/mp3-video-workflows/jobs` | 2 | 2/0 | 6.80 | 1.18 / 5.62 / 5.62 | 0 | `f08d08bb` |
| `GET /assets/images/apple-touch-icon.png` | 1 | 1/0 | 6.32 | 6.32 / 6.32 / 6.32 | 0 | `f4c2c7ee` |
| `GET /js/channel-owner-profile.js` | 1 | 1/0 | 6.00 | 6.00 / 6.00 / 6.00 | 0 | `41e3cad5` |
| `OPTIONS /api/mp3-video-workflows/status` | 2 | 2/0 | 5.61 | 1.28 / 4.33 / 4.33 | 0 | `53b4e9a9` |
| `OPTIONS /api/mp3-video-workflows/audio-assets` | 2 | 2/0 | 5.51 | 1.53 / 3.98 / 3.98 | 0 | `d84578f4` |
| `OPTIONS /api/v1/celery/runtime-status` | 2 | 2/0 | 5.13 | 0.93 / 4.20 / 4.20 | 0 | `918c85c9` |
| `GET /js/bilibili-charging-dashboard.js` | 1 | 1/0 | 5.06 | 5.06 / 5.06 / 5.06 | 0 | `d6a0b442` |
| `GET /js/data-table.js` | 1 | 1/0 | 4.94 | 4.94 / 4.94 / 4.94 | 0 | `8172b181` |
| `OPTIONS /api/v1/rankings/all` | 2 | 2/0 | 4.46 | 1.85 / 2.61 / 2.61 | 0 | `8c40d5dd` |
| `GET /js/new-main.js` | 1 | 1/0 | 4.25 | 4.25 / 4.25 / 4.25 | 0 | `475f1fbe` |
| `GET /favicon.ico` | 1 | 0/1 | 4.02 | 4.02 / 4.02 / 4.02 | 0 | `7542093f` |
| `GET /api/mp3-video-workflows/status` | 1 | 1/0 | 3.75 | 3.75 / 3.75 / 3.75 | 0 | `7bd744e3` |
| `OPTIONS /api/v1/rankings/monthly-revenue` | 2 | 2/0 | 3.67 | 0.83 / 2.84 / 2.84 | 0 | `f32da185` |
| `OPTIONS /api/v1/rankings/daily-revenue` | 3 | 3/0 | 3.44 | 0.99 / 1.66 / 1.66 | 0 | `f0d957e8` |
| `GET /js/content-pipeline-submit.js` | 1 | 1/0 | 3.29 | 3.29 / 3.29 / 3.29 | 0 | `61003f39` |
| `GET /content-pipeline-submit.html` | 1 | 1/0 | 3.18 | 3.18 / 3.18 / 3.18 | 0 | `b14e2450` |
| `GET /api/v1/channels/defaults` | 1 | 1/0 | 3.10 | 3.10 / 3.10 / 3.10 | 0 | `9e2c5ea2` |
| `GET /js/channel-management.js` | 1 | 1/0 | 2.61 | 2.61 / 2.61 / 2.61 | 0 | `4ac9728a` |
| `OPTIONS /api/v1/rankings/channel/:id/profile` | 1 | 1/0 | 2.21 | 2.21 / 2.21 / 2.21 | 0 | `b3901301` |
| `GET /index.html` | 1 | 1/0 | 1.98 | 1.98 / 1.98 / 1.98 | 0 | `a65673c9` |
| `OPTIONS /api/v1/channels` | 1 | 1/0 | 1.92 | 1.92 / 1.92 / 1.92 | 0 | `2889c2ec` |
| `OPTIONS /api/v1/bilibili-intelligence/charging-dashboard` | 1 | 1/0 | 1.76 | 1.76 / 1.76 / 1.76 | 0 | `3dcb2893` |
| `OPTIONS /api/v1/data/summary` | 1 | 1/0 | 1.71 | 1.71 / 1.71 / 1.71 | 0 | `e7b98589` |
| `OPTIONS /api/v1/data/chart-data` | 2 | 2/0 | 1.70 | 0.84 / 0.86 / 0.86 | 0 | `61e6bc04` |
| `GET /channel-owner.html` | 1 | 1/0 | 1.69 | 1.69 / 1.69 / 1.69 | 0 | `cf8d9446` |
| `GET /site.webmanifest` | 1 | 1/0 | 1.35 | 1.35 / 1.35 / 1.35 | 0 | `946f7c1d` |
| `OPTIONS /api/v1/rankings/yearly-revenue` | 1 | 1/0 | 1.08 | 1.08 / 1.08 / 1.08 | 0 | `94e424ec` |
| `OPTIONS /api/v1/channels/categories` | 1 | 1/0 | 0.97 | 0.97 / 0.97 / 0.97 | 0 | `b7e3336e` |
| `OPTIONS /api/v1/data/table` | 1 | 1/0 | 0.83 | 0.83 / 0.83 / 0.83 | 0 | `c27409f2` |
| `OPTIONS /api/v1/channels/defaults` | 1 | 1/0 | 0.82 | 0.82 / 0.82 / 0.82 | 0 | `230fd35e` |
