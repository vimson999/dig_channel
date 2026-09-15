# 2026-09-12 数据库性能与错误巡检

## 结论

固定窗口为 `2026-09-11 06:00:00` 至 `2026-09-12 06:00:00`（Asia/Shanghai）。数据库 SQL 错误、
traceback SQL episode、statement/lock/query/connect timeout、server closed、死锁与 PgBouncer 六类错误均为 0；
没有等待锁或 idle-in-transaction。应用慢 SQL 为 27 条、累计 `70.433s`，P50/P95/max 为
`1.708/7.605/13.839s`；较上一窗口数量增加 107.7%、累计耗时增加 171.2%、P95 增加 121.3%、
最大值增加 239.0%。

恶化样本集中在 18:36–18:42 的旧 Watchdog 进程：Publisher pending 窄投影扫描最高 `13.839s`，
简笔画父任务与 36 行场景资产窄读最高 `7.605/5.789s`。同一查询在常规周期约 `0.03–0.12s`，
生产只读 EXPLAIN 的代表形态仅 `0.022–35.218ms`，现场无锁等待；因此根因仍是
应用驱动/PgBouncer/远端链路尾延迟，不是执行计划退化或缺索引。本轮无 DDL 证据，未添加索引、未调高超时。

前一轮 Publisher 可见消息被 repair 换 ID 的代码修复尚未被旧 Watchdog 加载。本轮安全门读取 47 条近期任务、
0 条活动同步并放行，随后用标准双门禁脚本完成整栈重启；新 backend PID `7204` 健康，17 个 Celery 服务与
Watchdog 均启动成功。首轮 Watchdog 查询回落到 `34–122ms`。状态提升为
`RUNTIME_REPLACED/OBSERVING`，仍需自然 queued/unacked 样本与完整后窗才能关闭。

## 数据范围

- 日志、请求与业务窗口：09-11 06:00 至 09-12 06:00。
- 数据库为 UTC naive，业务快照使用等价 UTC 窗口 `2026-09-10 22:00:00` 至 `2026-09-11 22:00:00`。
- 覆盖 app/backend/scheduler/Celery、publication SQL、PgBouncer 轮转日志、生产只读快照与物理 EXPLAIN。
- 所有生产数据库检查均为只读；未执行 DDL、DML、VACUUM FULL、REINDEX 或数据库配置修改。

## 慢 SQL、HTTP 与后台任务

### 慢 SQL

- 27 条、累计 `70,432.75ms`，全部为应用 SQL。
- Publisher pending scan 指纹 `25b2a425193817e3`：2 次/`14.945s`，P95/max `13.839s`；来源为
  `publisher_queue_repair.py:1453`，SQL 仅投影状态修复所需列。
- MP3 音频目录指纹 `be891b5c1af362da`：4 次/`13.050s`，P95/max `3.695s`；此前同形 executor
  仅 `1.202ms`，冷附件候选也仅 `31.326ms`。
- 简笔画场景资产指纹 `9cd7383320ffa7e0`：3 次/`9.393s`，P95/max `5.789s`；父任务窄投影
  指纹 `eef0f08c4ecec7dd`：1 次/`7.605s`。重启后首轮同路径为约 `34/102ms`。
- 其余主要样本为 business repair `5.778s`、ready repair `3.490s`、report-audio recovery
  `3.453s`、notifications `2.944s`、ZSXQ configs `2.621s`。

### HTTP

- 426 请求、408 成功、18 个 404；业务 5xx 为 0。P50/P95/max 为
  `8.87ms/1.538s/11.784s`，累计 `156.105s`。
- 18 个非 2xx 全部是 08:48 的 `GET /api/v2/tts/task/:id/segment/:id/audio` 单次页面突发，涉及
  18 个不同历史任务；对应点查仅 `110–264ms` 且无慢 SQL。路由会在历史记录仍有 `audio_path`、但文件缺失
  或不属于当前用户时返回 404。因此这是需要单独追踪的历史音频可用性问题，不是本轮数据库失败；现有证据
  尚不足以在数据库巡检中修改业务代码。
- `POST /api/v1/content-pipeline/web-submit` 2/2 成功，耗时 `10.461/11.784s`，没有超过 1 秒 SQL；
  主要时间不在单条数据库执行。
- ZSXQ Insights/topics/local/tabs/sync/status 在固定窗口内没有真实 HTTP 样本，不能以“未报错”记为健康验收。

### Celery 与 durable 终态

- Celery：135 completed、1 failed、0 retry，成功任务总运行时间约 `2,980.289s`。唯一失败为
  `run_bilibili_upload_only` 外部调用；Publisher 数据库终态对应 8 completed、1 failed，失败原因为
  `member.bilibili.com` SSL EOF，不是数据库错误或 repair 自我饿死。
- 内容管线：PDF clean 3/3 completed，audio clean 2/2 completed；69 个资产（PDF 42、audio 27）全部
  `cleaned`，69 个唯一 hash，缺 cleaned path/hash 均为 0。
- ZSXQ clean-output publish：3 completed、2 failed；固定窗口没有 system sync run。
- 通知 durable delivery 73/73 sent，attempt 均为 1、dedupe key 73 个，未发现重复副作用。

## Session、PgBouncer 与物理审查

- 连接 hold 3 次，P50/max `10.252/14.599s`；全来自 scheduler，SQL 累计 `31.386s`、非 SQL 仅
  `0.869s`，且全部匹配上述慢 SQL。没有跨外部 I/O 持有 Session 的新证据。
- runtime guard 记录 13 个短暂 offender episode，最大 2 连接、事务年龄 `0.336s`、idle 年龄
  `0.302s`，无准入 defer，不构成 idle-in-transaction 事故。
- PgBouncer 1,440 个采样、288 次 server connection（12/h）；wait P50/P95/max
  `19.69/55.60/475.89ms`，query `46.68/98.90/1174.45ms`。较上一窗口 P95 分别增加 6.7% 与
  23.7%；六类错误和代理/TUN route fallback 均为 0，来源保持 `100.96.3.12`。
- `publication_tasks` 约 134MB，live/dead 13,443/233；dead 较上一窗口 2,640 下降 91.2%，且
  09-11 已发生 autovacuum/autoanalyze。其他目标表 dead：topics 1,584、comments 5,644、attachments 37、
  sync runs 46、external documents 146；索引扫描占比 `98.78%–99.95%`。
- 无等待锁、无精确重复索引；`pg_stat_statements` 未安装。代表 EXPLAIN：publication PK `0.034ms`、
  chart `3.322ms`、config `0.022ms`、Publisher list `0.069ms`、Content list `0.163ms`、sync runs
  `1.738ms`、Insights canonical `35.218ms`。

## 运行替换与边界样本

- 旧 Watchdog PID `42792` 自 09-08 起运行，确实未加载 09-11 的 queue-repair 修复；旧 backend PID
  为 `41942`。
- `check-restart-safety.py` 读取 47 条近期任务与 0 条活动同步并放行；标准重启脚本冻结 17 个队列、
  关闭 API 入队入口并再次通过安全门，全程未使用 FORCE。
- 新 backend PID `7204` 的 `/health` 返回 healthy；bootstrap 17/17、`errors=[]`，目标 worker ready，
  Watchdog 于 06:05:14 启动。
- 固定窗口外的首轮 Watchdog 查询为 `34–122ms`，没有长 hold 或 SQL 错误。启动时 Insights 冷缓存刷新
  有一条应用 SQL `1.678s`、总刷新 `3.225s`；同形 executor `35.218ms`，记作后窗冷启动边界样本。
- 排除测试运行后的 06:12–06:15 短窗仅有 1 个 health 请求（200/`2.38ms`），慢 SQL、hold、SQL 错误、
  timeout 与 PgBouncer 六类错误均为 0；这是重启 smoke 证据，不代替完整 24 小时验收。
- 首轮没有自然 queued/unacked Publisher 候选，尚不能证明运行态中 ID 与 repair count 在可见消息下稳定。

## 根因闭环状态

| 根因簇 | 本轮证据 | 状态与下一步 |
| --- | --- | --- |
| Publisher 可见消息被换 ID | 旧 PID 未加载；标准双门禁重启成功；首轮查询恢复 | `RUNTIME_REPLACED/OBSERVING`；等待自然 queued/unacked 候选与完整后窗 |
| 远端 DB 网络/驱动长尾 | 错误/timeout 为 0，但应用 SQL P95 `7.605s`、executor 最高 `35.218ms` | `OPEN/REPEATED/OBSERVING`；慢峰同步保存 route/pool/socket/activity |
| Content canonical N+1 | 5 个自然任务、69 个资产全部 cleaned 且 hash 唯一 | `RUNTIME_ACCEPTED/OBSERVING`；本窗未捕获逐任务 SQL 预算 marker |
| 文件哈希跨事务 | 终态与资产完整，但仍缺 hash 入口 `in_transaction=false` 直接 marker | `OBSERVING`；不能由成功终态替代事务边界证据 |
| 父任务完成唤醒宽读 | 本窗 Publisher 全部来自 MP3 workflow，无 report-audio 自然 completion | `OBSERVING`；不以缺样本关闭 |
| 巡检 offender 口径 | 13 episode、最大事务年龄 `0.336s` | `RUNTIME_ACCEPTED/OBSERVING` |
| TTS 历史音频可用性 | 18 个历史 segment audio 404，DB 点查正常 | `OPEN/OBSERVING`；下一轮先对账文件存在性、owner 与清理策略 |

## 变更与验证

- 本轮没有修改业务代码或数据库结构；更新 Publisher/网络复发记录、recurrence registry、README 与本报告。
- 保留工作区所有既有用户改动，不覆盖、不回退。
- 定向 queue-repair/revoke/worker boundary 回归：56/56 通过。
- 完整 unit 首次在受限沙箱运行 1,666 项，34 项仅因禁止绑定 `127.0.0.1` 报 `PermissionError`；允许
  loopback 后原命令复跑为 `Ran 1666 tests in 82.141s / OK`。
- `compileall`、worker app factory 27 文件、Celery revoke 317 文件门禁、registry JSON、Bug strict
  197/197 与 `git diff --check` 均通过。

## 下一轮重点

1. 对自然 Publisher queued/unacked 候选核对 `celery_task_id`、repair count 与 broker 可见性，完成运行验收。
2. 在 SQL 慢峰同步保存 route、PgBouncer pool/server/socket 与 PostgreSQL activity；连续零 timeout 仍不足以关闭长尾簇。
3. 对 18 个 TTS 历史 audio 404 做文件、owner、清理记录三方对账，再决定修数据、修路由还是调整历史展示。
4. 采集 Content hash 直接事务边界、逐任务 canonical SQL 预算与 report-audio 自然父唤醒样本。
