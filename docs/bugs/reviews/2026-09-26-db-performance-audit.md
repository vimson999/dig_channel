# 2026-09-26 每日数据库性能与错误巡检

## 根因闭环结论

固定窗口为 `2026-09-25 06:00:00` 至 `2026-09-26 06:00:00`（Asia/Shanghai；数据库时间条件换算为
UTC naive `2026-09-24 22:00:00` 至 `2026-09-25 22:00:00`）。本轮不能标记“已解决”：

- 应用慢 SQL 由 11 条/17.970 秒降至 6 条/8.242 秒，P95/max 由 5.692 秒降至 2.086 秒；PgBouncer
  wait/query P95 也降至 34.44/58.77ms。六条真实 SQL 的生产 `EXPLAIN (ANALYZE, BUFFERS)` executor
  仅 0.013–0.162ms，shared read 均为 0，应用侧约慢 7,400–83,000 倍，继续指向 driver/PgBouncer/
  私网往返或冷连接尾延迟，缺索引不是根因。
- Publisher 恶化为 19/19 old-root、7/19 failed，失败率 36.84%；累计 4,412.523 秒，P95/max
  487.898/499.923 秒。当前 Watchdog 确实把一条旧部署创建、素材路径已不可达的任务换 ID 后交给当前
  render worker，最终 0 个音频片段并失败。根因保持
  **OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**。
- YouTube id 21/56 第七个窗口继续固定失败，保持
  **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。
- 固定窗自然 HTTP 请求为 0。原始日志同期只有连续 heartbeat，没有 request/Werkzeug 行；窗后 `/health`
  及四个 ZSXQ 只读端点均被正确解析，排除解析器再次漏计，但本窗页面/API 仍属于“未捕获”，不能报告无问题。
- scheduler 测试 marker 基线 53 未增加，API import 路径继续 RUNTIME_ACCEPTED；未受管 runner 仍只有代码证据。

本轮没有生产 DDL、索引变更、数据回填、任务重投、凭据轮换或破坏性操作。落地的是审计、复发登记和新的
跨 deployment 素材可达性 fail-closed 纪律；物理隔离需要人工协调，故不能把文档防线写成根因已闭环。

## 与上一轮量化对比

| 指标 | 09-25 窗口 | 09-26 窗口 | 变化 | 判断 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL | 11 | 6 | -45.5% | 改善，但根因未闭环 |
| 应用慢 SQL 累计 | 17.970s | 8.242s | -54.1% | 改善 |
| 慢 SQL P50 | 1.206s | 1.044s | -13.4% | 改善 |
| 慢 SQL P95 / 最大 | 5.692s / 5.692s | 2.086s / 2.086s | -63.4% | 改善 |
| PgBouncer wait P95 | 43.21ms | 34.44ms | -20.3% | 改善，无错误事件 |
| PgBouncer query P95 | 69.93ms | 58.77ms | -16.0% | 改善 |
| 自然 HTTP 请求 | 305 | 0 | 覆盖空窗 | 不可作性能环比 |
| Publisher old-root / failed / 旧 reason / 换 ID | 7/16 / 3 / 0 / 0 | 19/19 / 7 / 2 / 3 | 明显恶化 | 分布式旧 actor 复发 |
| YouTube 固定失效源 | id 21/56 | id 21/56 | 第七窗 | 反复 |

## 主要问题排序

| 排名 | 问题 | 累计 / P95 / 最大 | 失败率、页面/用户影响 | 复发与状态 |
| --- | --- | --- | --- | --- |
| 1 | MP3 父工作流长壁钟时间 | 8,189.696s / 2,029.578s / 2,065.473s | 7/7 completed；包含子任务等待，非独立 DB executor 时间 | 后台无前台端点；OPEN/OBSERVING |
| 2 | Publisher 分布式旧 actor | 4,412.523s / 487.898s / 499.923s | 7/19 failed；旧素材不可跨根访问，6 个父任务终态掩盖 child 失败 | 多窗；WAITING_DISTRIBUTED_ISOLATION |
| 3 | YouTube 两个失效源 | channel 17.18s；video 47.81s | channel 49/51、video errors=2；持续失败通知 | 第七窗；WAITING_MANUAL_CONFIG |
| 4 | 应用 SQL 远端尾延迟 | 8.242s / 2.086s / 2.086s | 后台 finalization、B站情报、Publisher、通知；executor 至多 0.162ms | 多窗；OPEN/OBSERVING |
| 5 | HTTP 覆盖空窗 | 0 自然请求 | 全部页面/API P50/P95、失败率不可得 | 未捕获，不是未发生 |

MP3 父任务与 Publisher child 的壁钟时间存在嵌套，不能简单相加为独立资源耗时；本表按每个问题自身的
累计、P95、最大、失败率和用户影响排序，不把任务 wall clock 误作数据库连接持有时间。

## 慢 SQL、Session、PgBouncer 与 executor

| 指纹 / SQL origin | 应用耗时 | executor / buffers | 调用链与根因判断 |
| --- | ---: | ---: | --- |
| `59acf8fbd7ac9933` / `report_audio_finalization.py:1360` | 2.086s | 0.039ms / hit 6 read 0 | finalization ID 有界扫描；排序后 index scan，后台无 request id |
| `abbd31d685e07695` / `bilibili_intelligence_service.py:307` | 1.991s | 0.162ms / hit 86 read 0 | 30 个 bvid 的 `IN` 查询；bitmap index/heap，非 executor 慢 |
| `c014bff91db5d2af` / Publisher pending repair | 1.077s | 0.013ms / hit 1 read 0 | 有界 index scan；旧 actor 问题在任务来源，不在此 SQL |
| `c1e57cc097d3e051` / notification due aggregate | 1.044s | 0.141ms / hit 123 read 0 | aggregate/sort/bitmap；无失败风暴 |
| `f5ee1b5df0b1bd8a` / Publisher terminal-parent cleanup | 1.033s | 0.094ms / hit 45 read 0 | 有界父子清理；现有索引命中 |
| `9583c5faf59cd676` / notification delivery point | 1.011s | 0.030ms / hit 3 read 0 | 主键点查；EXPLAIN 为不加行锁的 executor 等价形状 |

六条均为后台 origin，没有可关联的前台 request id。PgBouncer 1,440 个样本八类故障均为 0；wait
P50/P95/max 为 24.43/34.44/120.17ms，query 为 51.06/58.77/124.42ms。288 次连接均来自私网
`100.96.3.12`，proxy fallback 为 0。应用/traceback/SQL 三路的 statement timeout、lock timeout、
deadlock、query/connect timeout、server closed、waiting lock、idle-in-transaction 均为 0。

任务准入只出现 1 个亚秒 idle observation，最大连接 1、xact 0.123s、idle 0.096s、deferral 0；没有
外部 I/O 与 Session 长时间交叉持有证据。改善只代表本窗尾延迟下降，不能替代网络/驱动根因定位。

## 页面、API、后台任务与 SQL 关联

固定窗 0 个完整 HTTP 请求、0 incomplete、0 endpoint。backend 在整个窗口持续输出五分钟 heartbeat，确实没有
request logger 与 Werkzeug 访问行；边界后 `06:04:09` 的 `/health` 为 200/2.45ms 并被解析器捕获。

因此三类异常按以下口径报告：

- **低频高延迟**：固定窗六条后台 SQL 均仅出现一次，1.011–2.086 秒，但 executor 仅 0.013–0.162ms。
- **页面/API 很慢但无匹配慢 SQL**：固定窗没有自然请求样本，无法判断；这是未捕获，不是 0 个问题。
- **后台任务很久但无前台端点**：7 个 MP3 workflow 累计 8,189.696 秒；19 个 Publisher 累计
  4,412.523 秒；均无固定窗前台请求，且现有日志不支持把 wall clock 归因于 Session hold。

窗后只读端到端烟测不计入固定窗基线：

| 端点 | 状态 / 耗时 / bytes | SQL 与判断 |
| --- | --- | --- |
| `/api/v1/zsxq-insights/overview` 冷请求 | 200 / 3.121s / 7,521B | request `1f9c837b`；theme aggregate SQL 1.607s；同参数暖请求 30ms，缓存有效但冷聚合尾延迟仍需分段 |
| `/api/v1/zsxq/topics/local` | 200 / 0.445s / 2,574B | request `470f53e5`；返回 1 条，本次无 >=1s SQL |
| `/api/v1/zsxq/topics/local/tabs` | 200 / 0.182s / 4,638B | request `c5c6d5fc`；本次无 >=1s SQL |
| `/api/v1/zsxq/system-sync-scheduler/status` | 200 / 0.507s / 6,361B | request `1403e084`；scheduler alive，本次无 >=1s SQL |

后台任务固定窗：

- 数据库权威任务：MP3 workflow 7/7 completed，累计 8,189.696s，P50/P95/max
  1,023.892/2,029.578/2,065.473s；Publisher 12 completed、7 failed；content audio clean 1/1
  completed、145.750s；ZSXQ clean-output publish 1/1 completed、121.885s；自然 ZSXQ sync run 为 0。
- Celery 日志识别 106 个 completed、0 retry、1 failed，成功累计 246.137s；旧 actor 的完整成功日志并不在
  当前日志集合，因此 Publisher 以数据库 durable 记录为权威。
- durable notification 94/94 sent，attempt 总数 94、max attempt 1，无失败或重复投递风暴。
- B站情报 24/24 个小时周期均 6/6 成功，累计 216.387s，P50/P95/max 8.819/10.420/10.435s。
- YouTube channel 49/51，业务 17.18s、调度总 19.30s；video 处理 49 个频道、新增 22、更新 57、
  errors=2、47.81s；失败仍为 id 21/56。

## Publisher 反复问题与根因

19 条 Publisher 全部由旧 checkout 根创建；12 completed、7 failed，失败包括 6 条
`'NoneType' object is not subscriptable` 和 1 条 `max() arg is an empty sequence`。具体任务
`62663a5e-418c-457f-a478-96c0fc1f39aa` 的证据链为：

1. 旧 actor 持久化不存在的 `.../dig_ytb_data_claude_v1/...jpg` 与 `.mp3` 绝对路径；
2. 当前 Watchdog 的 worker active/reserved/scheduled、broker queue、Redis unacked 均为 0；
3. Watchdog 以 `pending_queued_not_visible_in_celery` 把 `623d…` 换为 `e737…`；
4. 当前 render worker 接收新消息，文件过滤后 `Audio Segments: 0`；
5. MoviePy `concatenate_audioclips([])` 抛出 empty sequence，child failed；父 MP3 workflow 最终 completed。

本机巡检时所有 Celery/Watchdog 命令均来自当前项目根，旧目录、launchd、crontab 入口为空，old-root active
任务也为 0。这只是当前本机快照，不能推翻窗口 durable 证据。新增纪律要求 durable deployment identity 和
素材可达性校验，跨 deployment 重投必须 fail closed；但真正根治仍需要停止所有共享生产 DB/Redis 凭据的旧
部署，无法证明全停时轮换凭据并隔离 broker namespace。本轮没有用更多重试、超时或吞错掩盖。

## 表规模、顺序扫描与索引复核

| 表 | total / heap / index | live / dead | seq / idx scan | 判断 |
| --- | --- | ---: | ---: | --- |
| `publication_tasks` | 139.9MiB / 17.2MiB / 13.2MiB | 13,939 / 1,171 | 7,105 / 3,726,280 | idx 99.81%；三条真实形状 executor 0.013–0.094ms |
| `zsxq_topics` | 128.5MiB | 12,637 / 1,584 | 4,185 / 891,982 | idx 99.53%；固定窗无自然页面 SQL |
| `zsxq_topic_comments` | 118.0MiB | 41,333 / 5,644 | 480 / 358,685 | idx 99.87%；本窗无慢 SQL |
| `zsxq_attachments` | 77.7MiB | 33,906 / 37 | 629 / 1,135,783 | idx 99.94%；本窗无慢 SQL |
| `zsxq_sync_runs` | 37.0MiB | 1,062 / 46 | 290 / 337,739 | idx 99.91%；自然 run 为 0 |
| `external_documents` | 6.9MiB | 1,817 / 146 | 1,061 / 84,987 | idx 98.77%；本窗无慢 SQL |

精确重复索引为 0。低/零扫描候选包括 publication Publisher-MP3 match（172kB、scan 0）、attachments PDF
group/tab（约 5.2MB、scan 1）、topics group/type/create（约 3.8MB、scan 1）、external TTS/summary（scan 0）
和若干 B站 40–49kB 索引（scan 0）。缺少完整业务周期、写放大收益和删除后计划证据，不生成 DROP。

六条真实慢 SQL 都命中既有索引或在极小集合上聚合，executor 至多 0.162ms；CREATE INDEX 无法解释秒级应用
耗时。本轮没有 CREATE/DROP INDEX、ALTER TABLE 或回填脚本，也没有需要在 DBeaver 执行的新索引 SQL。

## 已落地修复、测试与运行态验证

- 更新 Publisher、YouTube、MP3 目录、HTTP coverage、scheduler 五条 Bug 记录及 recurrence registry；新增
  “跨 deployment identity/素材不可达时重投 fail closed，父终态不得掩盖 child 失败”纪律。
- 慢 SQL 六个真实形状均以只读事务执行 `EXPLAIN (ANALYZE, BUFFERS)`；executor 0.013–0.162ms，未锁生产行，
  未执行 DDL。
- 真实固定窗日志回放覆盖 slow/app/backend/scheduler/celery/PgBouncer；窗后 health 与四个 ZSXQ 只读 API
  均 200，Insights 冷/暖为 3.121s/30ms。
- 定向分析器/scheduler/Publisher repair 联合套件 **87/87**；完整 unit **1,718/1,718 OK
  （92.041s）**。
- `compileall`、worker app-factory boundary（28 个文件）、Celery revoke boundary（323 个 Python 文件）、
  recurrence JSON、Bug strict（208/208）和 `git diff --check` 均通过。
- 完整测试后 `scheduler.log` mtime 仍为 `2026-09-24 06:40:20 +0800`，确认本轮测试没有再次写入该生产日志。
- 本轮没有修改常驻应用代码，故没有需要加载的新代码，也未触发重启；不得用烟测替代下一完整自然窗口。

## 待人工动作、风险与未捕获边界

1. **P0 Publisher**：盘点并停止所有共享生产 PostgreSQL/Redis 凭据的主机、容器和启动器；不能证明全停时，
   轮换凭据并隔离 broker namespace。此动作涉及生产配置与可能中断，不做无人值守执行。
2. **YouTube**：在 DBeaver 人工复核并执行既有
   `docs/sql/disable_unavailable_youtube_channels_20260920.sql`；脚本已有 lock/statement timeout、精确保护、
   验证与回滚，仍不由巡检无人值守执行。
3. `pg_stat_statements` 不可用；日志仍缺 SQL pool-acquire、fetch/deserialize bytes 与网络阶段，无法把秒级应用
   SQL 精确分配给驱动、PgBouncer 等待和私网传输。
4. 固定窗没有自然页面/API，慢 SQL 阈值以下的 N+1、大字段、重复聚合、轮询与缓存失效无法由本窗证明为 0。
   窗后烟测只是目标链路可达性证据。
5. 当前本机未发现旧进程不等于其他主机、已退出进程或共享凭据入口不存在；old-root active=0 也不等于已隔离。

## 下一轮明确验收计划

1. Publisher：先取得物理隔离动作记录；自然 MP3→Publisher 必须成功，old-root、旧 reason、ID replacement、
   repair-limit、素材不可达和 child failure 均为 0，再观察后续完整 24 小时窗口。
2. Watchdog：对跨 deployment 或必需素材不可达的 pending child，验证其 fail closed 而不发布新 Celery ID；
   记录 deployment/host/PID/code identity 与素材探测结果。
3. YouTube：人工脚本执行后，channel/video 两个自然周期不再出现 id 21/56，再观察下一周期。
4. 页面/API：必须取得自然请求并重算全站 endpoint 请求量、失败率、累计、P50/P95/max；优先复核 ZSXQ 四链、
   Insights 冷/暖缓存、MP3 audio-assets，并补 pool/fetch/bytes 分段。
5. 继续对六类 SQL 指纹做应用耗时、PgBouncer、executor 同窗对照；只有 executor 与真实扫描证据恶化才评估 DDL。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| old-root Publisher + `pending_queued_not_visible_in_celery` | 旧部署仍共享 DB/broker；当前重投未验证跨根素材可达 | CAS/current-id/queue 现场防线；新增跨 deployment fail-closed 纪律 | 19/19 old-root、7 failed；`62663a5e…` 完整 old path→requeue→0 audio→failed 链 | **是，恶化** | 人工物理隔离；纪律尚非运行代码围栏 | 隔离后自然成功，完整窗六类复发指纹均 0 |
| 六个后台 SQL 指纹 | executor 快，应用秒级耗时在 driver/PgBouncer/私网边界 | 既有有界查询/索引；本轮不盲目 DDL | 应用 1.011–2.086s；executor 0.013–0.162ms；无锁/timeout | **是但改善** | 缺 pool/fetch/network 分段 | 应用与 executor 同窗稳定，并取得物理网络分段 |
| YouTube id 21/56 | 远端无可用内容但本地仍启用 | 结构化失败身份、受保护停用脚本 | channel 49/51、video errors=2 | **第七窗** | 人工复核执行 SQL | 双自然周期无两源，再观察一周期 |
| HTTP coverage=0 | 本窗无自然访问，不是解析格式回归 | 生产样本 parser 合同与 coverage anomaly 纪律 | heartbeat 持续；窗后 health/四 API 被正常捕获 | 未知 | 自动 fail-loud coverage 尚未实现 | 下一自然窗三路日志数量级一致 |
| Insights 冷聚合 | 冷缓存触发聚合，远端 SQL 尾延迟占约一半 | TTL/single-flight/stale cache 既有 | 窗后冷 3.121s、SQL 1.607s；暖 30ms | 窗后观测 | 缺当前 executor 与 pool/fetch 分段 | 自然冷/暖样本、命中率与分段齐全 |
| scheduler 测试 marker | import 期全局 handler（既有修复） | API/库副作用已移除 | 本窗 marker 53 不增 | API 未复发 | runner 未受管 | runner 真正托管后再验；API 保持 accepted |
