# 2026-08-09 数据库性能与错误巡检

## 结论

本轮**不能判定数据库链路已解决**。24 小时总慢 SQL 数量、P95 和最大值较上轮改善，
但 P50 恶化，HTTP 失败率升至 6.40%；Tailscale 私网窗口仍出现 1 次真实应用 query timeout
和多次 PgBouncer server socket crash。网络/长连接复发键 `DB-SESSION-LIFECYCLE` 保持
`OPEN/OBSERVING`。

本轮新增并修复一条独立根因：`zsxq-insights overview` 把主题全集的大段正文跨数据库网络传回应用，
PostgreSQL executor 仅 85.817ms而应用 SQL 13.973s。已改为“数据库内完整匹配 + 轻量摘要 +
仅当前页详情”，新 PID 真实冷 API 从 14.920s降至 2.793s，热 API 为 2.32ms。
该项达到 `RUNTIME_REPLACED/OBSERVING`，仍需下一完整窗口证明不复发，不能写成 CLOSED。

## 范围、基线与边界

- 统计窗口：2026-08-08 06:02:19 至 2026-08-09 06:02:19（Asia/Shanghai）。
- 覆盖：`slow_query.log`、`app.log`、`backend.log`、`scheduler.log`、`logs/celery/*.log`
  及轮转文件；按 request id、task id、process/role、SQL origin关联。
- 上轮基线：应用慢 SQL 861 条，P50/P95/max 1.525/7.952/60.615s，总计 2,494.784s；
  HTTP 3,315 次、失败 35（1.06%），P50/P95/max 0.192/2.506/61.932s，总计 2,305.165s；
  Celery 572 完成/70 retry/9 failed。
- 本轮窗口包含 10:34–12:17 的原 TUN 故障/路由切换实验、11:34 后公网物理直连和
  15:12:59 后 Tailscale 私网三个不同 epoch，不能把实验期 HBA/登录失败当作稳定态失败率。
- “未捕获”不等于“未发生”：没有 `pg_stat_statements`，应用日志只记录超过阈值或异常的 SQL；
  PostgreSQL 实时视图只能证明采样时刻无锁/idle transaction，不能回放整日状态。

## 24 小时量化总览

| 指标 | 本轮 | 上轮 | 变化 | 判定 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL 数 | 748 | 861 | -13.1% | 改善 |
| 慢 SQL P50 | 1.746s | 1.525s | +14.5% | 恶化 |
| 慢 SQL P95 | 7.763s | 7.952s | -2.4% | 小幅改善 |
| 慢 SQL max | 42.475s | 60.615s | -29.9% | 改善但仍高 |
| 应用慢 SQL 累计 | 2,041.483s | 2,494.784s | -18.2% | 改善 |
| HTTP 请求 | 1,781 | 3,315 | -46.3% | 流量不同，不作健康结论 |
| HTTP 失败 | 114 / 6.40% | 35 / 1.06% | +5.34pp | 恶化 |
| HTTP P50/P95/max | 0.0055/8.438/160.279s | 0.192/2.506/61.932s | P95/max 恶化 | 故障风暴驱动 |
| HTTP 累计 | 3,479.250s | 2,305.165s | +50.9% | 恶化 |
| Celery | 389 完成/26 retry/5 failed | 572/70/9 | 失败绝对数下降 | 流量不同 |

慢 SQL 目标表计数：`publication_tasks` 155、`zsxq_sync_runs` 21、`zsxq_topics` 174、
`zsxq_topic_comments` 147、`zsxq_attachments` 58、`external_documents` 8。

连接持有事件 173 次，P50/P95/max 8.248/32.657/256.708s；SQL 累计 1,570.581s，
非 SQL 持有累计 849.760s。托管进程中 37 次长持有没有匹配的已完成慢 SQL，
P50/P95/max 15.276/60.672/63.359s，说明连接/网络等待仍无法由慢 SQL 完整解释。
采样时 PostgreSQL 无 waiting lock、无 idle in transaction。

## 页面/API 全链路

全部端点合计 1,781 请求、1,667 成功、114 失败。累计耗时优先的主要端点如下：

| 页面/API | 请求/失败 | P50/P95/max | 累计 | SQL 关联与判定 |
| --- | ---: | ---: | ---: | --- |
| MP3 竖屏视频下载 | 115/33 | 0.004/14.412/142.977s | 571.150s | 仅 4 条匹配慢 SQL；主要是文件/网络等待 |
| MP3 横屏视频下载 | 154/60 | 0.009/10.553/160.279s | 568.805s | 仅 5 条匹配慢 SQL；主要是文件/网络等待 |
| Celery status | 38/0 | 3.111/32.041/50.495s | 339.473s | 31 条慢 SQL；含 1 次真实 PgBouncer timeout |
| platform credentials | 112/4 | 0.537/9.116/22.859s | 260.448s | 30 条慢 SQL；route 故障窗口放大 |
| MP3 bilibili-upload | 11/0 | 4.970/49.007/49.007s | 144.431s | 14 条慢 SQL/68.995s；还含外部上传等待 |
| MP3 bilibili-uploads | 53/8 | 1.071/6.737/13.844s | 136.715s | 29 条慢 SQL/54.191s |
| 研报音频 job status | 33/1 | 0.801/4.548/60.997s | 107.032s | 最差请求未完成 SQL，属于连接黑洞 |
| PDF evidence image | 115/0 | 0.516/2.517/11.078s | 105.064s | 6 条慢 SQL/29.016s，其余含图像 I/O |

### 三类强制异常清单

1. **低频高延迟**：Bilibili upload cancel 2 次，P95/max 29.350s；Bilibili intelligence
   sync 1 次 22.611s；PDF assets 5 次 P95/max 15.970s；Celery render restart 1 次 12.059s。
2. **页面/API 很慢但无匹配慢 SQL**：MP3 background artifact 最大 142.096s、audio background
   60.085s、关键帧 18.345s；这些等待位于文件/网络/进程控制，不应加数据库索引。
3. **后台/连接持有很久但无前台端点**：37 个托管长持有没有慢 SQL completion；Tailscale
   epoch 中仍有 2 个此类事件，P95 31.728s；继续按 process/task/checkout origin追踪。

本窗口没有真实 `overview` 请求，故在窗口内不能宣称未复发；本轮主动冷验收才发现并复现
14.920s问题。`sync/status` 精确路径不存在，实际契约为 `system-sync-scheduler/status`。

## 后台任务

| Celery 任务 | 成功/retry/失败 | P50/P95/max | 成功累计 | 判定 |
| --- | ---: | ---: | ---: | --- |
| ZSXQ sync | 32/5/0 | 253.427/423.517/669.039s | 7,564.211s | 外部 I/O 主导，数据库往返仍占显著累计 |
| PDF clean | 8/0/0 | 458.183/597.848/597.848s | 2,880.386s | 文档处理主导，无前台端点 |
| report PDF parse | 130/0/0 | 1.193/73.450/169.872s | 1,677.561s | 低频长任务需继续分离外部处理与 DB |
| publisher upload-only | 9/0/5 | 118.435/511.854/511.854s | 1,290.319s | 失败仍开放；含外部上传/DB 断连 |
| report audio process | 18/21/0 | 1.785/233.783/252.018s | 543.688s | retry 风暴仍需下一轮确认来源 |
| workflow notification | 115/0/0 | 1.275/1.569/2.564s | 151.685s | 较上轮 60 retry/9 fail 明显改善 |

应用 SQL 错误 205：MP3 `load_task` 171+8、publisher cleanup 11、publisher update 9、
Celery monitor 4、report audio 1、retention cleanup 1。绝大多数发生于路由切换前；
Tailscale 稳定段仅 1 条真实应用 SQL timeout，但这 1 条足以阻止关闭网络根因。

## 主要 SQL 指纹与根因排序

排序优先综合累计耗时、P95/max、失败率、用户影响和复发次数，而非只看次数。

| 优先级/指纹 | 次数 | 累计/P95/max | 根因与状态 |
| --- | ---: | ---: | --- |
| P0 网络/PgBouncer `DB-SESSION-LIFECYCLE` | 12 个去重 query timeout、1 connect timeout | HTTP 3,479s；持有 P95 32.657s | executor 外网络/长连接；反复；OPEN |
| `0b4ccc892361a813` ZSXQ topic update | 60 | 163.182/5.867/10.417s | 页级批量已存在，Tailscale 往返仍放大；OBSERVING |
| `60ff7467696400d3` scheduler config | 78 | 154.538/4.193/7.794s | 高频配置读取与网络往返；既有缓存修复需复核 |
| `f405eb580b433c78` MP3 load_task | 18 | 146.665/16.721/42.475s | 171 次错误，路由风暴时主键查询失败；非缺索引 |
| `1040e9d3426c0dd0` ZSXQ snapshot | 28 | 113.604/19.161/30.869s | 小批量 executor 与应用差距；网络/连接等待 |
| `9da4aded84860e3e` MP3 list assets | 31 | 102.408/9.257/15.792s | 其中含诊断进程；top 120 内容仅约 11KB，非大字段根因 |
| `58f8cd43a85581b3` Celery business snapshot | 7 | 44.825/27.687/27.687s | 真实 timeout；当前 executor 0.086ms，OPEN |
| Insights selected topics | 冷验收 1 | 13.973s app / 85.817ms executor | 759KB全文传输；已部署修复，OBSERVING |

## 路由 epoch 与复发证据

### 公网物理直连 11:34–15:12:58

- 应用慢 SQL 88，P50/P95/max 1.518/15.379/32.505s，总计 288.542s。
- HTTP 584，失败 1（0.17%），最大 60.997s；4 个去重 query timeout。
- 17 个连接持有，P95/max 60.672/80.724s，非 SQL 持有 187.332s。

### Tailscale 15:12:59–06:02:19

- 应用慢 SQL 291，P50/P95/max 1.637/5.270/15.792s，总计 650.760s。
- HTTP 37/37 成功，P50/P95/max 0.006/12.059/32.041s，总计 89.094s。
- Celery 216 完成/21 retry/0 failed。
- 5 个去重 query timeout中，4 个来自 ad-hoc诊断；1 个来自 request `d48fbee4`
  `/api/v1/celery/status`，应用等待 20s、端点 32.041s；同 SQL executor 0.086ms。
- 无 connect/HBA/login failure，但 PgBouncer 仍出现多次 `server conn crashed?`。
- 当前 Tailscale 到数据库为 direct UDP、约 26ms；这只排除 DERP，不排除 MTU、socket stale、
  远端 reset或 PgBouncer server connection生命周期问题。

因此本轮属于“总体改善但仍复发”，不得把 Tailscale 切换视为根治。

## 本轮已落地修复与运行态验证

修复文件：`src/backend/services/zsxq_insights_service.py`。

- 完整 regex/搜索仍在 PostgreSQL 内基于全字段执行，未删除正文、原始 JSON或审计数据。
- 第一阶段仅返回 392 条轻量统计摘要；第二阶段仅返回当前页 30 条详情；单页上限 100。
- 修复前 request `25115ce7`：overview 14.920s；selected SQL 12.319s；Session 14.890s。
- 精确 EXPLAIN：396 行、758,615 bytes，executor 85.817ms、应用 13.973s。
- 修改后服务冷构建 2.863s；摘要 484.11ms，详情 207.19ms；热构建 0.743s。
- 重启安全门禁确认无不可中断任务；`restart-dev-stack.sh` 成功启动新 backend PID 56236、
  17 个 Celery 服务、watchdog 和各 scheduler。
- 新 PID API：health 200/0.216s；overview cold 200/2.854s、warm 200/0.114s；
  Celery status 200/1.203s；topics/local POST 200/0.747s；tabs POST 200/0.283s；
  scheduler 200/0.289s。
- overview page 1/2 各 30 条且 topic id 重叠为 0；`page_size=5000` 被钳制为 100。
  page 2 在一次网络长尾中仍为 5.577s（鉴权 SQL 2.565s、页详情 SQL 2.876s），无 timeout；
  这进一步证明公共网络复发键不能随 Insights 数据访问修复一并关闭。
- `topics/local count=10000` 已由既有服务端限制为 100 条，但仍返回 486,808 bytes/3.188s；
  作为下一轮大字段边界继续观察，不在无完整字段契约分析下进一步缩减。

## 表规模、索引与 DDL 决策

| 表 | 总大小 | live/dead | 索引扫描占比 | 结论 |
| --- | ---: | ---: | ---: | --- |
| `publication_tasks` | 115MB（TOAST 87MB） | 11,812/315 | 99.80% | 大 JSON 是主要体积，热点路径继续窄投影 |
| `zsxq_topics` | 121MB（TOAST 76MB） | 12,167/253 | 99.64% | canonical 索引累计扫描 2,659，保留 |
| `zsxq_topic_comments` | 118MB | 41,019/4,342 | 99.93% | dead 较高但无锁/膨胀证据支持无人值守维护 |
| `zsxq_attachments` | 68MB | 31,512/2,055 | 99.95% | HOT update 仅 0.12%，继续关注写放大 |
| `zsxq_sync_runs` | 34MB | 892/159 | 99.94% | JSON/状态写入继续窄化 |
| `external_documents` | 6.9MB | 1,748/146 | 99.19% | 无新增索引理由 |

真实 EXPLAIN executor：publication PK 0.073ms、channel 1.777ms、config 0.041ms、publisher
0.089ms、content 0.230ms、recent JSON 0.045ms、narrow 0.049ms、system runs 1.410ms、
Insights canonical 33.943ms。当前没有证据支持新增索引。

未发现完全相同定义的重复索引；启动 schema 门禁仍提示两个等价/重叠索引
`ix_zsxq_topics_group_tab_type_create_desc`、`ix_zsxq_topics_group_type_create_desc` 及迁移
`20260724_comment_hash_indexes` 未登记。不得无人值守执行；管理员应在 DBeaver 逐条运行现有
`docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql`，遵守其中 lock/statement timeout、
验证和回滚步骤。风险是并发写入时锁等待及错误删除业务仍使用索引，执行前必须再次采集
`pg_stat_user_indexes` 和当前定义。

## 测试与门禁

- 定向：`test_zsxq_insights_service.py` + `test_zsxq_insights_api.py`，14 passed。
- 完整 unit：1,093 passed、3 skipped、60 subtests passed；仅 5 条既有 SWIG deprecation warning。
- `compileall src/backend`：通过。
- `git diff --check`：通过。
- recurrence registry JSON：通过解析。
- Bug strict：155 条记录全部归类、9 个根因簇、最近回顾 2026-08-09，检查通过。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `DB-SESSION-LIFECYCLE` | executor 外网络/PgBouncer长连接黑洞 | 公网 route-exclude、HBA、Tailscale目标、监控分栏 | Tailscale 37 HTTP全成功但仍1应用 timeout与server crash | 是 | 运维继续查 MTU/reset/stale server socket | 完整24h托管应用 timeout=0、crash=0、20/60s持有=0 |
| Insights 全文传输 | 先拉396行/759KB再分页 | 轻量摘要+当前页详情+page cap100 | 新 PID cold 2.793s、warm 2.32ms | 本轮已复现后替换 | 需后续自然冷窗口 | cold P95<5s、无>10s、无timeout，连续窗口确认 |
| ZSXQ同步批量指纹 | 外部 I/O 与网络往返叠加 | 既有页级批量/短事务继续生效 | 32任务成功、5 retry；SQL累计仍高 | 观察中 | worker owner继续按task id分段 | 下一真实全tabs轮次无30s SQL/长持有 |
| MP3下载/状态风暴 | 路由故障+文件/外部I/O；大量无匹配SQL | 本轮未以超时/重试掩盖 | 竖/横下载失败33/60，最大143/160s | 是 | owner复核新路由下真实下载 | 新窗口失败率<1%、max<10s且无DB timeout |
| Publisher/report audio retry | 外部上传与DB断连混合 | 既有状态机/通知修复 | upload-only 5 failed；audio 21 retry | 是 | 按任务id关联下一delivery | 真实任务0重复投递、终态一致、无SQL错误 |
| ZSXQ重叠索引维护 | 历史等价索引/迁移登记缺口 | 已有人工DBeaver脚本，未执行DDL | schema门禁明确阻塞 | 未变 | DBA人工执行并保留回滚证据 | idx定义/scan复核后执行、迁移登记一致 |

## 下一轮明确计划

1. 首先按 Tailscale稳定段统计托管应用与 ad-hoc诊断分栏的 query/connect timeout、server crash、
   Session 20/60 秒长尾；任一非零继续沿 request/task/SHOW SERVERS socket年龄追踪。
2. 优先自然捕获 overview冷请求，验证轻量摘要行数/字节、cold P95<5s、max<10s；热缓存不能替代冷验收。
3. 复核 `topics/local` 100 条 487KB边界，先确认前端字段使用和 executor/传输拆分，再决定投影，
   禁止删字段或审计数据。
4. 追踪新路由下 MP3横竖下载、publisher upload、report audio retry的真实任务；失败必须关联 task/request id。
5. DBA若执行索引清理脚本，下一轮核对 migration登记、索引使用、写放大、锁等待和回滚记录。
6. 只有代码、完整测试、真实 API/worker验收和后续完整窗口均通过，才把对应项从 OBSERVING改为 CLOSED。

## 2026-08-09 11:17 跟进处置

本轮对报告截止点后的运行日志和真实进程栈继续追踪，确认问题不只是网络波动：

- 09:53–10:24 的 PgBouncer 日志仍出现 12 组 query timeout/server connection failure。
- 本机 MP3 worker 的真实任务在子任务等待阶段把 SQLAlchemy Session 跨磁盘扫描、`ffprobe`、
  Celery inspect 和轮询 sleep 持有，单次非 SQL 持有约 75 秒；这会放大任何短时网络故障。
- `mp3_video_workflow.py` 已改为先投影任务快照、归还 Session，再执行磁盘/进程/网络探测；
  同时复用已探测的视频路径，避免重复 `ffprobe`。
- MP3 worker 已在确认 active task 为 0 后安全重启，新 worker online、inspect 正常、无活动任务，
  因而该修复已进入本机实际执行链路。
- 重启过程中又捕获到 Celery 管理流程在 stop guard 查询后跨进程退出等待持有连接约 20.9 秒；
  `_stop_service` 现已把 Session 释放设为允许停止和安全门拦截两条路径的固定边界。
- 本机 Flask 管理进程和 Celery Watchdog 随后单独重启加载该边界修复；新 Flask PID 与 5001
  监听者一致且 `/health` 为 200，Watchdog running；17 个业务 worker 全程未停止。
- 启动预检还发现 `CELERY_RESULT_BACKEND` 遗留公网 `121.4.126.31:6379/15`，在公网 Redis
  已关闭后必然超时；本机 `.env` 已备份并改为 Tailscale `100.92.99.39:6379/15`。
  缓存、broker、result backend 三链路预检现均通过，Flask 重载后不再产生该启动 Warning。

验证与发布证据：

- MP3 修复后的完整 unit 门禁：1,095 passed、3 skipped、60 subtests passed；最终两处边界的
  相关回归集：125 passed；`git diff --check` 通过。
- 生产发布版本为 `7f2b032+worktree-20260809-db-audit-r2`，镜像
  `sha256:00e8445558ce...`；旧版保留为 rollback tag。
- 候选镜像内 `compileall`、PostgreSQL `SELECT 1`、Redis `PING` 和源码哈希校验均通过后才切换。
- 切换后 `nginx/postgresql/redis-server/docker/fail2ban/tailscaled` 全部 active，容器 healthy，
  本机与公网 HTTPS `/health` 均为 200；新容器窗口未见 5xx、Traceback、query timeout 或断链日志。
- 系统盘使用率 56%，剩余约 34GB；生产服务器继续只运行 Web，不启动 Celery worker。

结论仍为 `OPEN/OBSERVING`。已经关闭两个可证实的应用 Session 持有源，但网络/PgBouncer
复发键需从本次发布时点重新累计完整 24 小时：托管应用 query/connect timeout=0、server crash=0、
20/60 秒 Session 持有=0，且至少一轮真实 MP3/Insights/Celery status 负载通过后，才可改为 CLOSED。
