# 2026-09-03 每日数据库性能与错误巡检

## 结论

本轮固定窗口为 `2026-09-02 06:00:00` 至 `2026-09-03 06:00:00`（Asia/Shanghai）。应用慢 SQL
累计耗时从上轮 329.490 秒降至 234.642 秒，下降 28.8%；P50/P95 从 1.778/8.264 秒降至
1.235/2.145 秒。不过最大值升至 19.644 秒，且 PgBouncer 出现三组真实 client/server query timeout、一次
connect timeout 和一次 query_wait_timeout，远端链路根因仍为 `OPEN / REPEATED`。

本轮确认并修复两条 P0 应用根因：

1. 29 次发布/上传/MP3 Celery 任务因后台默认 `create_app()` 导入无关 Web API，在长驻进程中触发
   `ZsxqAnalysisItem` 混版 ImportError。已把整个 task 树统一切到最小 app factory，并新增 AST 门禁。
2. 自然 12 音频提交已证明上一轮 canonical 去重降为 1 SELECT + 1 INSERT，但又暴露 SHA256 在写事务内执行，
   造成 6.1 秒非 SQL 连接持有。已把 PDF/音频指纹计算移到事务开始前。

两项均已通过安全门标准整栈重启到 backend PID 21618；health healthy，15/15 核心 Celery service running、
queue=0，数据库无 waiting lock、无 idle-in-transaction。切点后尚无对应自然业务任务，故保持
`RUNTIME_REPLACED / OBSERVING`，不标 CLOSED。本轮没有新增、删除或重建任何索引。

## 固定窗口量化

| 指标 | 2026-09-02 | 2026-09-03 | 判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL 数 | 126 | 154 | +22.2%，但单次长尾明显收敛 |
| 应用慢 SQL 总耗时 | 329.490s | 234.642s | -28.8% |
| 应用慢 SQL P50 / P95 / max | 1.778 / 8.264 / 12.537s | 1.235 / 2.145 / 19.644s | P50/P95 改善，max 受链路超时拖累 |
| HTTP 请求 / 失败 | 556 / 7 | 1664 / 43 | 流量约 3 倍；失败率 1.26% → 2.58% |
| DB connection held 事件 | 19 | 29 | P50/P95/max 6.618 / 21.976 / 23.792s |
| Celery 成功 / 失败 / retry | — | 154 / 30 / 0 | 29 次为同一混版导入根因 |

154 条应用慢 SQL 中 `publication_tasks` 108 条，是绝对热点；`zsxq_topics`、`zsxq_attachments` 各 1 条，
其余目标表未形成慢 SQL 簇。另有 4 条 ad-hoc 诊断 SQL，已与应用统计分离。

最大慢指纹 `c1e57cc097d3e051` 来自
`services/workflow_notification_outbox.py:488:due_provider_drains`，5 次累计 24.929 秒、最大 19.644 秒。
同类 SQL 在 PostgreSQL 端没有结构性慢执行证据，且同秒出现 server/client timeout，因此不以索引掩盖链路问题。
其他高频来源包括 MP3 任务详情、publisher queue repair、平台凭据与音频目录，单指纹累计约 8–19 秒；下一窗口
继续按 origin 观察，但本轮未达到新增索引或立即改码的证据门槛。

## 页面、API 与 SQL 关联

- `GET /api/v1/mp3-video-workflow/jobs/:id`：95 次，累计 196.778 秒，P95 7.608 秒、最大 12.723 秒；对应
  `publication_tasks` 详情查询和 4-SQL connection hold，仍受远端往返长尾影响。
- `GET /api/v1/bilibili/uploads`：95 次，累计 194.994 秒，P95 4.531 秒、最大 6.219 秒。
- 平台凭据：87 次，累计 111.756 秒，P95 3.745 秒；对应凭据列表慢指纹 7 次累计 8.615 秒。
- `POST /api/v1/bilibili/upload`：16 次，累计 106.689 秒，P95 9.001 秒、最大 16.514 秒；Celery 成功任务中
  还存在约 528–543 秒的真实上传耗时，属于外部平台 I/O，不是数据库 executor。
- `POST /api/v1/content-pipeline/web-submit`：5 次，累计 72.968 秒，最大 43.201 秒。request `dc99bae7`
  的 12 音频批次是本轮事务外哈希修复的现场证据。
- 43 个 HTTP 失败主要来自 31 次竖屏视频文件缺失下载，以及 ZSXQ analysis 新路由发布早期的 404/405、
  charging options 3 次失败；不能把失败率上升等同为数据库失败。

ZSXQ 特殊路径中，analysis runs GET 14 次（10 成功、4 失败，P95 0.411 秒）、POST 7 次（3 成功、4 失败，
最大 1.255 秒）、run detail 2 次成功；system-sync scheduler status 1 次 0.551 秒、groups/local 1 次
0.494 秒。Insights overview/topics/local/tabs/sync/status 在窗口内未捕获流量，只能记为“未观测”，不能写成无问题。

## P0 根因一：后台任务导入完整 Web API 图

发布流水线 16 次、Bilibili upload-only 9 次、MP3 workflow 4 次，共 29 次任务在业务逻辑前失败。调用链为
task `create_app()` → `register_all_apis()` → `apis/v1/zsxq_analysis.py` → 从旧 worker 已缓存的 `models`
导入新符号失败。这与 2026-09-01 notification worker 混版事故完全同构，说明上轮只修单点，没有建立系统防线。

本轮将 `src/backend/tasks/**/*.py` 的 50 个直接调用全部改成
`create_app(register_apis=False)`，并增加 `check-worker-app-factory-boundary.py` AST 门禁。门禁坏样本必须失败、
好样本与真实 27 文件 task 树必须通过。详细记录见
`2026-09-03-worker-app-factory-api-import-fanout.md`。

## P0 根因二：内容指纹跨写事务

自然 request `dc99bae7` 包含两个提交，其中 12 音频批次在任务 INSERT 后约 6.1 秒才进入 canonical SELECT；
连接持有 6.395 秒，SQL 仅 0.280 秒、非 SQL 6.114 秒。日志同时确认上轮 N+1 修复有效：3 文件与 12 文件
批次都只有一次 canonical SELECT 和一次多行 INSERT。

`reserve_files()` 原先把文件 SHA256 和数据库预留捆绑，调用方 flush 后才执行文件读取。现拆为事务前
`fingerprint_files()` 和只消费纯值的 `reserve_fingerprints()`；兼容入口与唯一约束竞态兜底保持不变。详细记录见
`2026-09-03-content-pipeline-hash-inside-transaction.md`。

## PgBouncer、网络与 Session

PgBouncer 原始日志可归并为三个 query-timeout episode：16:26、18:20、18:50 均有 server query timeout
与约 20–21 秒 client query timeout；16:27 另有 10 秒 connect timeout 和 15 秒 query_wait_timeout。统计窗口的
平均建连速率约 12.5/h，来源仍为 `100.96.3.12`，代理路径仍显示 TUN。该根因延续
`2026-08-08-postgres-tunneled-route-query-timeout.md`，状态 `OPEN / REPEATED`。

29 个 connection-held 事件中，14 个来自 Web、10 个 scheduler、5 个未知；SQL 累计 144.219 秒、非 SQL
109.837 秒。21 个托管 hold 没有匹配慢 SQL，证明不能只按 SQL 指纹查 Session 生命周期。重启后只读现场为
idle 7、waiting lock 0、idle-in-transaction 0，角色硬保险仍为 statement_timeout=60s、lock_timeout=5s、
idle_in_transaction_session_timeout=60s。

## 表、索引、膨胀与 EXPLAIN

| 表 | 总大小 | live / dead | HOT update | index scan |
| --- | ---: | ---: | ---: | ---: |
| publication_tasks | 129MB（TOAST 100MB） | 13,145 / 491 | 1.27% | 99.78% |
| zsxq_topics | 129MB | 12,637 / 1,584 | 0.71% | 99.55% |
| zsxq_topic_comments | 118MB | 41,333 / 5,644 | 70.46% | 99.89% |
| zsxq_attachments | 78MB | 33,906 / 37 | 0.12% | 99.95% |
| zsxq_sync_runs | 37MB | 1,062 / 46 | 0.33% | 99.93% |
| external_documents | 7MB | 1,817 / 146 | 0% | 98.79% |

`publication_tasks` dead tuple 已从上轮 2,245 降至 491，09-02 18:12 已自动 vacuum；没有等待锁、精确重复索引
或失效索引。Insights canonical index valid/ready/live，累计扫描 3,930 次。代表查询 executor：point lookup
0.029ms、channel chart 3.396ms、scheduler config 0.025ms、publisher list 0.090ms、content list 0.177ms、
recent wide/narrow 0.025ms、sync runs 1.535ms、Insights canonical 35.301ms。`pg_stat_statements` 未安装，
因此不把瞬时统计外推为历史结论。

以上证据不支持 CREATE/DROP INDEX、REINDEX、VACUUM FULL 或生产回填；本轮未执行 DDL，也没有输出待人工执行的
DBeaver SQL。

## 验证与状态

- TDD：事务外哈希 PDF/音频 2 项先红后绿；worker app factory 门禁坏/好/真实树 3 项先红后绿。
- 目标交叉：内容去重、Web submit、通知、app factory 与 notification retry 共 40/40 通过。
- 完整 unit：sandbox 首轮 1589 项因禁止 loopback bind 报 36 个环境错误；获准环境重跑后暴露并修正 2 个旧 mock
  合同，最终 1589/1589 通过。
- 静态门禁：worker app factory 27 文件通过；Celery revoke 314 文件通过；compileall、JSON、bug/registry strict、
  `git diff --check` 均通过。
- 重启：两次安全门均为 47 条近期任务、0 活动 sync、0 不可中断任务；标准流程冻结 17 队列并二次检查，未用
  FORCE。新 backend PID 21618，health healthy；15/15 核心服务 running、queue=0。
- 重启后 DB：waiting lock 0、idle-in-transaction 0。自然目标任务尚未发生，两条新修复均保持 OBSERVING。

## 下一轮验收

1. 捕获重启切点后的第一条自然发布、upload-only 或 MP3 任务，要求无 API import fanout、无
   `ZsxqAnalysisItem` ImportError，且业务终态成功。
2. 捕获第一条自然多文件内容提交，要求 SHA256 时 `in_transaction=False`、canonical 仍为 1 SELECT + 1 INSERT，
   并关联 request id、connection hold 与最终任务。
3. 继续按 episode 统计 PgBouncer 六类连接错误与 TUN/Tailscale 路径；网络 query timeout 未归零前不得关闭。
4. 对 MP3 detail、Bilibili uploads、凭据列表继续比较请求 P50/P95/max 与 executor；executor 快而端到端慢时不加索引。
