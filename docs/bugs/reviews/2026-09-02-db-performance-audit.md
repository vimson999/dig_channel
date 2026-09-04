# 2026-09-02 数据库性能与错误巡检

## 结论

固定窗口为 `2026-09-01 06:00:00` 至 `2026-09-02 06:00:00`（Asia/Shanghai），上一窗口为
`2026-08-31 06:00:00` 至 `2026-09-01 06:00:00`。本窗显著改善：应用慢 SQL 从 700 降至 126，累计耗时从
`2,262,863.80ms` 降至 `329,490.28ms`；请求失败率从 1.51% 降至 1.26%；query timeout 与
server-closed 均为 0。改善主要来自上一轮修复和流量回落，不能据此宣告网络恢复：PgBouncer 仍有 1 次真实
connect timeout，驱动端 SQL 长尾远高于本地 executor。

本轮新增并改码一条 P0 根因：内容管线一次 20 MP3 提交在 canonical admission 中逐文件 SELECT/savepoint/
INSERT，单请求 82 条 SQL、39.47 秒。修复将正常路径收敛为一次批量 SELECT 和一次批量 INSERT execution，
内容管线 36 项、加入通知链后最终 76 项交叉回归通过，并已安全加载到 backend PID 78814；尚无切点后自然批次，状态为
`RUNTIME_REPLACED / OBSERVING`。

数据库物理层没有 DDL 依据：无重复索引、等待锁或 idle-in-transaction，热点点查 executor 低于 4ms；
Insights canonical selection 的只读计划为 35.177ms，不足以在未证明 workload 收益前新增索引。网络根因继续
`OPEN / REPEATED`。

## 1. 固定窗口总览

| 指标 | 本窗口 | 上一窗口 | 判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 126 | 700 | -82.0% |
| P50 / P95 / max | 1,778.30 / 8,264.42 / 12,536.68ms | 2,045.65 / 10,024.09 / 20,117.73ms | 三个分位均改善 |
| 慢 SQL 累计 | 329,490.28ms | 2,262,863.80ms | -85.4% |
| 请求数 / 成功 / 失败 | 556 / 549 / 7 | 1,589 / 1,565 / 24 | 失败率 1.26% |
| 请求 P50 / P95 / max | 4.87 / 4,088.00 / 39,474.32ms | 198.96 / 4,872.48 / 382,617.64ms | 最大值由内容提交主导 |
| 长 Session hold | 19 | 96 | P50/P95/max 6,857.44/16,914.94/18,706.01ms |
| hold 内 SQL / 非 SQL | 149,955.64 / 21,567.28ms | 1,015,922.00 / 39,550.93ms | 大多数仍是 SQL/网络长尾 |
| idle in transaction | 0 | 0 | 无泄漏证据 |

慢 SQL 表归因只有 `publication_tasks=86`，其余五张重点表为 0；自然应用 SQL error 为 0。两条 SQL error 均是
本轮只读诊断的字段/语法错误，已与自然应用失败分离。

## 2. SQL 与页面/API

| 指纹 / origin | 次数 | P95 / max | 累计 | 判断 |
| --- | ---: | ---: | ---: | --- |
| `45b19a5d74716c47` API 图片 child 批量详情 | 16 | 9,186.31 / 10,083.29ms | 45,158.16ms | 显式完整详情；非 10 秒动态轮询 N+1 |
| `590220bb0dd49238` MP3 父任务完整详情 | 7 | 8,324.05ms | - | 共享 JSON + 网络长尾 |
| `67be5ebc43a56078` script-video 父任务详情 | 9 | 6,720.22ms | - | 14 次显式 GET，未见 `poll=1` |
| `60ff...` all-tabs scheduler config | 6 | - | - | executor 0.029ms，不是索引问题 |
| `be891...` 音频 catalog snapshot | 3 | max 12,536.68ms | - | 远端长尾继续观察 |

页面/API 重点：

- `POST /api/v1/content-pipeline/web-submit` 6/6 成功，P50/P95/max
  `7,964.62/39,474.32/39,474.32ms`，累计 `87,432.13ms`。最慢 request `b6534a0b` 即本轮新根因。
- script-video detail 14/14 成功，P95/max `14,660/18,681ms`，19 条匹配慢 SQL；请求时刻不构成固定间隔
  动态轮询，本窗没有 `poll=1`。
- MP3 B站上传 8/8 成功，P95/max `14,219.77ms` 且没有慢 SQL。自然 request `e1da0b30` 只做一次窄父
  SELECT、一次 task INSERT、一次 upload row INSERT，broker/外部等待期间无连接 hold，也无 broker 后二次写；
  上轮生命周期修复获得自然路径接受证据。
- 7 个失败主要是远端 B站 credential 502、runtime status/启动早期状态、favicon 404 与测试端点；没有自然 SQL
  error 与它们对应。

## 3. 新根因：内容管线去重放大

request `b6534a0b` 提交 20 个唯一 MP3，耗时 39.474 秒；数据库只读对账确认 audio task
`1eb70e38-...` 与同批 4 PDF task `95ea99cd-...` 均完成且没有输入重复。旧 `reserve_files()` 对每个 hash 分别
执行 SELECT FOR UPDATE、SAVEPOINT、INSERT、RELEASE，20 文件加 task 写入形成 82 条 SQL；一次连接持有
5.809 秒，其中 SQL 3.881 秒。

修复先在内存保序去重，再以单个 `IN (...) FOR UPDATE` 加载整批 canonical rows；缺失项一次
`add_all + flush`。唯一约束仍裁决真实并发竞态，只有竞态异常路径进入旧单行兜底。8 文件 TDD 旧实现 8 次
SELECT，修复后严格 1 次 SELECT + 1 次 INSERT execution；PDF/音频共享入口和可回收状态语义均通过。

## 4. Celery、通知与 Session

- Celery 完成 246、retry 0、failed 4。4 个失败都是 06:00:07–06:04:29 的旧通知 ImportError，发生在上一轮
  最终进程切点前；06:39:24 后的最终 worker 到本窗结束未再出现该错误，并持续记录 durable sent。由于完整
  后窗还差约 39 分钟，状态保持 OBSERVING。
- workflow notification 有 4 次长 hold。其中 06:41 样本 SMTP 在 06:41:00 已完成，随后
  complete-delivery 事务到 06:41:17 才归还连接；不是 SMTP 跨 Session 复发。其余后续 hold 的非 SQL 仅
  63–69ms，主要是数据库 SQL/网络耗时。
- 06:16 重启后只读对账为 sent 235、pending/retry_wait/dead-letter 均为 0；email provider closed、连续失败
  0、`open_until=NULL`，最新真实发送到 05:55:46，idle-in-transaction 为 0。
- 唯一另一条非通知长 hold 是新内容提交样本：82 条 SQL/5.809 秒，已按根因改码。
- Watchdog 展示聚合从 2026-08-31 14:38:18 切点到本窗结束旧三个 origin 均为 0，已超过 24 小时并覆盖下一
  固定窗；该历史防线具备 runtime acceptance，但系统性根因簇仍不因单项通过而 CLOSED。

## 5. PgBouncer、连接与网络

- PgBouncer：query timeout 0、connect timeout 1、query wait timeout 0、server crash 0、TLS receive 0、
  server login/HBA reject/cached login failure 0；应用 server-closed 0。
- stats wait P50/P95/max `26.81/58.16/337.28ms`，query `55.31/101.45/2,636.24ms`。
- 24 小时建连 289 次，即 12.042/h；server source 仍全部为 `100.96.3.12`，proxy TUN fallback 0。
- timeout 数量改善但 connect timeout 仍真实存在，且多个 executor 低于 4ms 的 SQL 在应用侧达到数秒。因此
  网络根因继续 `OPEN / REPEATED`，不抬 timeout、不扩连接池、不用 DDL 掩盖。

## 6. 表、索引与 EXPLAIN

| 表 | total / table / TOAST / indexes | live / dead | HOT % | idx scan % |
| --- | --- | ---: | ---: | ---: |
| `zsxq_topics` | 129 / 13 / 79 / 36MB | 12,637 / 1,584 | 0.71% | 99.55% |
| `publication_tasks` | 128 / 17 / 99 / 12MB | 13,102 / 2,245 | 1.27% | 99.78% |
| `zsxq_topic_comments` | 118 / 91 / 4 / 23MB | 41,333 / 5,644 | 70.46% | 99.89% |
| `zsxq_attachments` | 78 / 35 / 0.1 / 42MB | 33,906 / 37 | 0.12% | 99.95% |
| `zsxq_sync_runs` | 37 / 1.2 / 35 / 0.9MB | 1,062 / 46 | 0.33% | 99.93% |
| `external_documents` | 7.016MB total | 1,817 / 146 | 0% | 98.80% |

只读 EXPLAIN：publication task 点查 `0.033ms`、频道图表 `3.434ms`、all-tabs config `0.029ms`、publisher
列表 `0.071ms`、content pipeline 列表 `0.275ms`、近期宽/窄任务 `0.027/0.029ms`、sync runs
`1.681ms`。Insights canonical selection 为 `35.177ms`；既有 canonical index valid/ready/live、累计扫描
3,930 次。低使用索引和低 HOT 表继续登记观察，没有安全 DROP、VACUUM FULL 或新索引依据。本轮无 DDL、无
DBeaver 待执行 SQL。

## 7. 变更、重启与验收

1. 内容管线 TDD 红灯明确为 8 次 canonical SELECT；修复后 1 次 SELECT + 1 次 INSERT execution。内容管线
   套件 36/36、加入通知链后的最终交叉套件 76/76 通过，compileall 与 diff check 通过。
2. 重启安全门在冻结前后各读取 47 条近期任务、0 个活动 sync，均为 0 个不可中断任务；未使用 FORCE。
3. 标准整栈重启后 backend PID 78814 healthy；bootstrap 17/17、`errors=[]`，content_pipeline worker
   06:13:36 ready。06:16 只读对账为 outbox 235/235 sent、provider closed、idle-in-transaction 0。新根因进入
   `RUNTIME_REPLACED / OBSERVING`，待自然多文件提交。
4. 本轮没有任何 CREATE/DROP INDEX 或其他 DDL；网络/PgBouncer 根因仍为 `OPEN / REPEATED`。

## 8. 下一轮优先级

1. 捕获 PID 78814 后首条自然多文件 Web 提交，核对 canonical SELECT=1、INSERT execution=1、SQL count、
   connection hold、unique/duplicate 与任务终态。
2. 完成 notification 最终切点后的完整 24 小时和下一固定窗；ImportError 必须继续为 0，并分开记录 SMTP 与
   DB complete-transaction 长尾。
3. 继续捕获 script-video 图片自然提交、平台 cancel/revoke 与报告音频 B站自然路径；缺少目标动作时不得接受。
4. 联合跟踪 connect/query timeout、server-closed、20 秒 hold 与 API 5xx；executor 低于 10ms 时继续反对
   新增索引。
