# 2026-09-11 数据库性能与错误巡检

## 结论

固定窗口为 `2026-09-10 06:00:00` 至 `2026-09-11 06:00:00`（Asia/Shanghai）。数据库 SQL 错误、
traceback SQL episode、锁等待、死锁、statement/lock/query/connect timeout、server closed、长连接 hold 与
PgBouncer 六类错误均为 0。13 条应用慢 SQL 累计 `25.971s`，P50/P95/max 为
`1.385/3.437/4.082s`；较上一窗口 149 条/`244.641s`，数量下降 91.3%、累计耗时下降 89.4%、P95
上升 17.0%。PostgreSQL 同形 executor 仅 `0.027–34.692ms`，没有证据支持新增索引、调高超时或修改
PgBouncer 参数。

本轮发现并修复一条 P0 业务状态机缺陷：Publisher repair 在当前消息仍 queued/unacked 时换 Celery ID，
导致旧消息连续被 stale guard 跳过，最终达到 repair 上限并误失败。TDD 与全量验证已通过；重启安全门
已放行，但标准整栈重启因缺少显式服务中断授权被审批拒绝，运行态保持待替换。

## 数据范围

- 日志、请求与业务窗口：09-10 06:00 至 09-11 06:00。
- 数据库为 UTC naive，业务快照使用等价 UTC 窗口 `2026-09-09 22:00:00` 至 `2026-09-10 22:00:00`。
- 覆盖 app/backend/scheduler/Celery、publication SQL、PgBouncer 轮转日志和生产只读快照。
- 所有生产数据库检查均为只读；未执行 DDL、DML、VACUUM FULL、REINDEX 或配置修改。

## 慢 SQL、HTTP 与后台任务

### 慢 SQL

- 13 条、累计 `25,970.61ms`，全部为应用 SQL；`publication_tasks` 6 条、`zsxq_topics` 2 条、
  `zsxq_attachments` 1 条。
- MP3 音频目录 canonical 指纹 `be891b5c1af362da` 为 4 次/`14.010s`，P50/P95/max
  `3.437/4.082/4.082s`；均来自 4 次冷缓存目录构建。
- 同形生产只读 EXPLAIN 对 642 行仅 `1.202ms`；附件元数据同形候选即使冷读 3,622 个 shared block 也仅
  `31.326ms`。数秒耗时仍是驱动/PgBouncer/远端链路长尾，不是 executor 或缺索引。

### HTTP

- 1,041 请求、1,040 成功、1 个 404，业务 5xx 为 0；P50/P95/max 为
  `4.23ms/1.360s/14.347s`。
- 唯一非 2xx 是本轮自动巡检误探 `GET /api/v1/health` 的 404，不是业务或数据库失败。
- `POST /content-pipeline/web-submit` 5/5 返回 202，P50/P95/max 为
  `10.799/14.347/14.347s`，没有超过 1 秒 SQL；主要时间不在单条数据库执行。
- `GET /audio-assets` 23 次中 19 次命中 300 秒进程缓存、4 次冷构建；client inflight 最大为 1，
  没有并发搜索叠加。

### Celery 与 durable 终态

- Celery：339 completed、1 failed、0 retry。唯一显式失败是 B 站充电档位不可用（错误码 21523），对应
  Publisher 已 durable 收口为 failed，属于外部业务错误。
- 数据库另有一个 Publisher 失败未出现在 Celery failed 数：`d588a3db…` 被 queue repair 达上限误关闭。
  其四个消息后来都到达并被 stale/terminal guard 跳过，根因为修复器替换仍可见消息，而非 broker 丢失。
- 业务任务：Publisher completed 26/failed 2、ZSXQ publish completed 7、MP3 workflow completed 6、
  PDF clean completed 5、audio clean completed 2。
- 通知 119/119 sent，attempt 均为 1、dedupe key 119 个，未发现重复副作用。

## Content Pipeline O(1) 验收

5 个 Web 请求产生 7 个自然清洗任务，资产数依次为 `9+15`、27、22、`4+13`、20，共 110 个。
每个任务严格执行 1 次 canonical SELECT + 1 次 multi-row INSERT。数据库窗口内 audio 28、PDF 82，
110/110 均 cleaned，唯一 hash 110、缺 owner/cleaned path 与全局重复 hash 均为 0。

批量去重保持 `RUNTIME_ACCEPTED/OBSERVING`。日志仍缺少哈希入口直接的 `in_transaction=false` 标记，
文件哈希事务边界继续作为独立观察项，不能由 O(1) SQL 数代替。

## Session、PgBouncer 与物理审查

- 连接 hold、idle-in-transaction 异常、准入 defer 均为 0；runtime guard 有 42 个短暂 offender episode，
  最大 3 连接、事务年龄 `0.619s`、idle 年龄 `0.493s`，不构成 60 秒事故。
- PgBouncer 1,440 个采样、288 次 server connection（12/h）；wait P50/P95/max
  `26.83/52.10/198.95ms`，query `56.76/79.98/237.69ms`，六类错误与 route fallback 均为 0。
- `publication_tasks` 约 134MB，live/dead 13,422/2,640；dead 较上次 1,751 增加 889（50.8%），
  属于写入 churn 观察项，尚无膨胀或 DDL 证据。其他目标表 dead 最多 5,644，索引扫描占比 98.78%–99.95%。
- 没有等待锁、idle-in-transaction 或精确重复索引；`pg_stat_statements` 未安装。
- 代表 EXPLAIN：publication PK `0.029ms`、chart `3.406ms`、Publisher list `0.049ms`、Content task list
  `0.300ms`、sync run `1.663ms`、Insights canonical `34.692ms`。

## 新发现：可见消息被 repair 自我作废

真实日志与数据库按 child、broker ID 和时间对账后，确认 `d588a3db…` 在 16:54、16:57、17:00 三次被
换 ID；每次旧消息随后到达，但 current-id guard 按设计将其跳过，17:04 repair 又把任务按上限关闭。
旧实现的注释已要求 worker 与 queue 都不可见才修复，实际代码却在 queue/unacked 可见时继续重投。

新增回归先在旧实现得到 `requeued=1` 的 RED，再修成可见即等待；目标与完整 queue-repair 套件联合
51/51 通过。详情见 `2026-09-11-publisher-queue-repair-visible-message-starvation.md`。

## 根因闭环状态

| 根因簇 | 本轮证据 | 状态与下一步 |
| --- | --- | --- |
| Publisher 可见消息被换 ID | 真实四 ID 时间线；TDD 51/51 | `CODE_VERIFIED/AWAITING_RUNTIME_REPLACEMENT`；安全重启后观察自然任务 |
| 远端 DB 网络/驱动长尾 | SQL timeout 0、executor 低于 35ms，但冷目录应用侧最高 4.082s | `OPEN/REPEATED/OBSERVING`；尖峰继续同步保存 route/pool/socket/activity |
| Content canonical N+1 | 7 个自然任务、110 资产均为 1 SELECT + 1 INSERT | `RUNTIME_ACCEPTED/OBSERVING` |
| 文件哈希跨事务 | O(1) 与终态正常，但仍缺直接 marker | `OBSERVING`；等待自然 `in_transaction=false` 证据 |
| 父任务完成唤醒宽读 | 旧形态无复发，但本窗仍无 report-audio 自然 Publisher completion | `OBSERVING`；不以缺样本关闭 |
| 巡检 offender 口径 | 新分析器报告 42 episode 与年龄边界 | `RUNTIME_ACCEPTED/OBSERVING` |

## 变更与验证

- 修改 Publisher pending repair 与对应回归，新增 Bug、规则、复发登记和本报告。
- 保留工作区既有的 ZSXQ、audio cleaner、分析器与文档改动，不覆盖或回退用户修改。
- TDD RED→GREEN 已完成；queue-repair 联合运行 51/51 通过。
- 完整 unit 在受限沙箱运行 1,666 项，34 项仅因禁止绑定 `127.0.0.1` 报 `PermissionError`；允许
  loopback 后原命令复跑为 `Ran 1666 tests in 83.477s / OK`。
- `compileall`、worker app factory 27 文件、Celery revoke 317 文件门禁、registry JSON、Bug strict
  197/197 与 `git diff --check` 均通过。
- 重启安全门读取 47 条近期任务与 0 条活动同步并放行；标准重启审批因当前请求未显式授权短暂服务中断而
  拒绝，未 FORCE、未绕过，状态保持 `CODE_VERIFIED/AWAITING_RUNTIME_REPLACEMENT`。

## 下一轮重点

1. 核对 Publisher queued/unacked 可见时 `celery_task_id` 与 repair count 都保持不变。
2. 对账 Celery delivery 与数据库业务终态，单列 stale/terminal skip，防止业务失败被 worker success 掩盖。
3. 在网络慢峰同步保存 route、PgBouncer pool/server、socket 与 PostgreSQL activity。
4. 继续采集 Content hash 直接事务边界与 report-audio 自然父唤醒样本。
