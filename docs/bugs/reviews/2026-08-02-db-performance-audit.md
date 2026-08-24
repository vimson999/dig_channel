# 2026-08-02 数据库性能与错误巡检

## 结论

统计窗口固定为 2026-08-01 06:01:18 至 2026-08-02 06:04（Asia/Shanghai），覆盖 `slow_query.log`、`app.log`、`backend.log`、`scheduler.log` 和 `celery/*.log`。本轮捕获到的主矛盾仍是 PgBouncer/网络等待把毫秒级 PostgreSQL executor 放大到秒级，不能用盲目加索引处理。慢查询数量较上一轮下降 75.2%，但 P95 恶化 73.1%，说明常见路径改善、约 60 秒的连接池超时长尾仍反复出现。

安全范围内已修复一个全站请求级 N+1：全局鉴权守卫已解析用户后，路由装饰器仍重复查询同一用户。日志分析器同时新增端点 E2E、失败率、累计耗时、request id 与慢 SQL 关联，以及低频高延迟/无匹配慢 SQL分桶。没有执行生产 DDL、数据回填或破坏性操作。

## 量化证据与基线对比

| 指标 | 本轮 | 上一轮 | 变化 |
| --- | ---: | ---: | ---: |
| 慢 SQL 数 | 565 | 2,275 | -75.2% |
| 慢 SQL P50 | 1.514s | 1.480s | +2.3% |
| 慢 SQL P95 | 15.267s | 8.818s | +73.1% |
| 慢 SQL最大 | 60.692s | 60.396s | +0.5% |
| 连接持有事件 | 78 | 204 | -61.8% |
| 持有 P50 / P95 / 最大 | 10.846s / 60.450s / 578.535s | 8.940s / 40.170s / 80.210s | P95 恶化；最大来自人工只读诊断 |
| 托管进程无匹配慢 SQL持有 | 15 | 92 | -83.7% |
| PgBouncer query timeout | 7 个去重事件 | 14 个 | -50.0% |
| PgBouncer connect timeout | 1 | 0 | 新增 |
| 应用 SQL 错误 | 1 | 4 | -75.0% |

- 另有 4 个 SQL 错误和 578.535 秒持有来自本轮人工只读诊断，已与托管应用流量分离。
- `statement timeout=0`、`lock timeout=0`、`deadlock=0`、`idle in transaction=0`；这是“日志和活动快照未捕获”，不代表数据库从未发生。
- PgBouncer 1,443 个统计样本：等待 P50/P95/最大为 30.02/83.23/344.15ms，query 为 60.32/147.07/1,990.78ms。

按用户影响、累计耗时、P95、最大值和失败情况综合排序的主要 SQL 指纹：

| 排名 | 来源/用途 | 次数 | 累计 | P95 | 最大 | 判断 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 1 | ZSXQ 主题快照（20 IDs） | 19 | 170.28s | 30.72s | 59.01s | executor 0.170ms；网络/池等待 |
| 2 | ZSXQ 主题快照（19 IDs） | 18 | 151.74s | 15.73s | 59.74s | 同上 |
| 3 | scheduler 系统同步配置 | 96 | 131.97s | 2.43s | 4.08s | 17 行 seq scan executor 0.020ms；高频往返 |
| 4 | ZSXQ 评论快照 | 16 | 121.88s | 29.51s | 60.30s | executor 0.235ms；网络/池等待 |
| 5 | Doubao publication_tasks 宽点查 | 9 | 85.43s | 60.69s | 60.69s | PK executor 0.041ms；含 params/result 且遇池超时 |
| 6 | external_documents 列表/扫描 | 19 | 122.99s | - | 约 10s | 表仅 6.70MiB，需继续按端点观察，不加盲索引 |
| 7 | ZSXQ sync run 宽列表 | 1 | 51.90s | 51.90s | 51.90s | 低频高延迟；现有倒序索引 executor 0.255ms |

目标表慢 SQL 命中：`publication_tasks=144`、`zsxq_sync_runs=15`、`zsxq_topics=60`、`zsxq_topic_comments=132`、`zsxq_attachments=24`、`external_documents=2`。

## 页面/API 与 SQL 关联

共关联 1,219 个完整请求、126 个归一化端点，无未闭合请求。按累计耗时和用户影响列出主要端点：

| 端点 | 请求 | 成功/失败 | 累计 | P50 | P95 | 最大 | 匹配慢 SQL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `GET /api/mp3-video-workflows/jobs` | 119 | 119/0 | 96.38s | 0.367s | 2.274s | 19.385s | 9 条 / 29.68s |
| `GET .../bilibili-uploads` | 83 | 83/0 | 96.03s | 0.935s | 4.084s | 6.017s | 11 条 / 22.44s |
| `GET /api/v1/celery/status` | 28 | 28/0 | 75.28s | 0.527s | 17.031s | 24.523s | 3 条 / 18.36s |
| `POST /api/v1/zsxq/system-sync-test/batches` | 3 | 3/0 HTTP | 61.66s | 0.322s | 61.101s | 61.101s | 0 |
| `POST /api/v1/zsxq/sync/runs` | 52 | 52/0 | 58.90s | 0.426s | 2.445s | 17.519s | 6 条 / 33.07s |
| `GET /api/v1/data/table` | 4 | 4/0 | 32.36s | - | 31.388s | 31.388s | 1 条 / 30.58s |
| `POST /api/mp3-video-workflows/jobs` | 3 | 3/0 | 27.34s | - | 21.242s | 21.242s | 有匹配慢 SQL |

鉴权 HTTP 失败已纳入失败率：`/api/v1/auth/verify` 54 次中 2 次 401；封皮再生成 3 次中 1 次 401；另有 favicon 404。窗口内完整请求没有 5xx，但 `system-sync-test/batches` 在 handler 内吞掉数据库异常并返回 HTTP 200，因此 HTTP 成功率不能代替业务成功率。

特别复核：

- `zsxq-insights/overview`：2 次均 HTTP 200，P50 0.522s、最大 8.179s；慢请求 `eaf261d6` 对应主题聚合 5.763s和选题加载 1.493s。较上一轮 24 次、P50 6.593s、P95 36.296s、最大 60.308s显著改善，但冷请求仍有 8 秒长尾。
- `topics/local`：9 次，P50 0.626s、最大 1.299s；`topics/local/tabs`：5 次，P50 0.263s、最大 0.465s。较上一轮 23.883s/15.508s 峰值明显改善。
- `sync/runs`：52 次，最大 17.519s，6 条匹配慢 SQL累计 33.071s；仍受 PgBouncer 往返影响。
- `sync/status` 相关的附件状态与系统调度状态接口最大分别 0.314s 和 0.440s，窗口内未见异常。

## 三类独立异常

1. 低频高延迟：`POST /api/v1/zsxq/system-sync-test/batches` 仅 3 次却有 61.101s；`POST /api/mp3-video-workflows/jobs` 仅 3 次却有 21.242s；单次 Bilibili intelligence 同步 11.536s。
2. 页面/API 很慢但无匹配慢 SQL：system-sync batches 的 `f277fc10` 持有连接 61.017s；同秒 PgBouncer query timeout，随后发生新连接。异常被 endpoint 捕获成 200/空结果，且通用 SQL 错误记录器只记录 publication_tasks，造成 slow_query.log 缺口。另有上传状态和封皮再生成端点出现 1-5 秒无慢 SQL，指向外部 I/O、缓存或池等待。
3. 后台任务/连接持有很久但无前台端点：ZSXQ sync 22 个成功任务累计 7,659.74s，P50/P95/最大 404.99/684.18/745.95s；属于完整远程同步流程，不可与单 SQL相加。托管无匹配慢 SQL连接持有 15 次、最大 61.017s。workflow notification 有 30 个最终失败、45 个 retry，全部是 `SMTPServerDisconnected`，与 123 个成功任务无 ID 重叠，属于外部 SMTP 可靠性问题，不是数据库 executor 问题。

后台任务其余高累计耗时：`parse_pdf` 98 成功、累计 1,736.49s、最大 181.59s；PDF clean 5 成功、累计 1,683.06s、最大 638.50s；ZSXQ publish 10 成功、累计 1,520.09s、最大约 342.44s；publisher pipeline 8 成功、累计 1,470.69s、最大约 334.06s。没有发现这些成功任务对应的永久数据库错误，但长流程仍须维持外部 I/O 前结束事务的纪律。

## 程序模式检查

- 发现并修复请求级重复鉴权查询：同一 request id `690425e6` 两次 `_get_light_user_by_id`，分别 1.402s/1.205s。现在路由复用全局守卫写入的 `g.current_user`，预计每个已登录受保护请求减少一次用户读取；等待重启后做运行态定量验收。
- ZSXQ 主题/评论同步使用 20/60 ID 批量快照，未退化为逐主题点查；快照 SELECT 不读取 `raw_payload`，原始 JSON 审计数据写入语义未改变。
- 未捕获无界超大分页：关键批次为 topics 20、comments 60、sync runs 40、页面列表 10/50/100；当前证据不能证明所有未触发分支永久无大分页。
- 高频轮询集中在 MP3 jobs 119 次、Bilibili upload 83 次和 Celery status 28 次。鉴权复用直接降低前两类通用数据库往返；Celery status 的长尾还包含 inspect/控制面等待。
- scheduler 系统配置 96 次查询累计 131.97s，executor 仅 0.020ms。后续若优化应做短 TTL 缓存并在配置写入时失效，不应增加索引。
- Doubao 点查包含业务必需的 `params/result`；不能为性能牺牲字段完整性。应优先压低连接池长尾，再按调用阶段评估窄投影。
- ZSXQ 下载流程代码已把主要远端拉取与快照查询分段，但启用附件下载的每主题事务仍需持续用 session 生命周期守卫观察；本窗口未形成可归因的“持有事务跨文件下载”证据，未做推测性重构。

## 表与索引证据

数据来自只读 `pg_stat_user_tables`、`pg_stat_user_indexes`、索引定义/有效性和 `EXPLAIN (ANALYZE, BUFFERS)`：

| 表 | 总大小 | live/dead | seq/idx scan | 更新/HOT | 观察 |
| --- | ---: | ---: | ---: | ---: | --- |
| publication_tasks | 102.4MiB | 8,711/1,731 | 1,975/2,293,039 | 188,173/3,096 | 宽 JSON 更新导致 HOT 比例低 |
| zsxq_sync_runs | 29.1MiB | 564/0 | 168/283,882 | 78,886/372 | 现有倒序索引命中 |
| zsxq_topics | 108.3MiB | 11,320/378 | 1,921/548,394 | 94,068/1,241 | 主题唯一索引 executor 0.170ms |
| zsxq_topic_comments | 117.6MiB | 40,349/6,260 | 127/161,223 | 141,593/76,166 | 评论复合索引 executor 0.235ms |
| zsxq_attachments | 65.2MiB | 28,157/3,163 | 414/847,871 | 235,950/316 | 写放大/HOT 比例最低 |
| external_documents | 6.70MiB | 1,652/26 | 331/49,338 | 6,145/0 | 表小，慢点不支持盲加索引 |

索引扫描比例均高于约 99.3%，所有索引 valid/ready；按列/谓词/顺序签名未发现完全重复索引。核心 EXPLAIN 实测：主题快照 0.170ms、评论快照 0.235ms、sync runs 0.255ms、system config 0.020ms、channels 0.034ms、publication_tasks PK 宽点查 0.041ms。应用侧 1-60 秒与 executor 差距足以排除“缺索引是当前主因”。

唯一持续低价值候选是 `ix_zsxq_attachments_group_tab_status_file`：4.27MiB、累计 scan=0。它与附件频繁更新叠加写放大，但 DROP 仍需要管理员所有权和锁风险评估。

## 已落地修复

1. `get_current_user()` 在请求上下文内复用非空 `g.current_user`，保留空值后续重新解析语义和全部权限字段。
2. 日志分析器新增端点归一化、请求量/成功失败/累计耗时/P50/P95/最大值、request id 对应慢 SQL、SQL 多维排名、低频高延迟和无匹配 SQL 端点。
3. 将人工诊断 SQL/连接持有与托管进程流量分离，并对 PgBouncer 重复日志按时间事件去重。
4. 新增 Bug 记录和 recurrence registry 条目，锁定请求级身份共享及查询次数纪律。

## 验证结果

- 日志分析器、鉴权边界和数据库投影纪律定向测试：19 passed。
- 相关三文件回归：97 passed，1 个既有且无关的火柴人静态 cache-bust 断言失败。
- Bug 记录严格检查：137/137 已归类，检查通过。
- 完整 unit tests：`1003 passed, 3 skipped, 53 subtests passed, 1 failed`；唯一失败为既有火柴人前端 cache-bust 断言仍期待 `lightweight-status-poll-v2`，HTML 已是 `20260729-sticky-sidebar-status-v3`，与本轮数据库修复无关。
- compileall、`git diff --check` 均通过；Bug 记录严格检查 137/137 已归类。
- 重启安全门禁拒绝重启：`report_visual_plan` `c6c02b63-f5ff-49c1-b561-75220f139462` 正在 `wait_browser_profile`，ZSXQ sync `226dabb4-3955-4274-926a-d22a7865003b` 正在 `auto_all_tabs_sc`。遵循门禁未强制重启，鉴权修复尚未加载。

## 索引整改建议与待人工 SQL

- 本轮不建议 CREATE INDEX；现有真实 SQL 均由有效索引或极小表 seq scan 在 0.020-0.255ms 完成。
- 继续人工评估删除零扫描附件索引。脚本：`docs/sql/zsxq_attachment_file_index_cleanup_dbeaver_20260801.sql`，已包含 `lock_timeout`、`statement_timeout`、执行前验证、逐条 DDL、执行后验证和回滚 CREATE INDEX。只能在 DBeaver 安全窗口由管理员执行，本轮未执行。
- 风险：累计 scan=0 受 PostgreSQL stats 生命周期限制；执行前必须再次确认服务启动/统计周期、无隐藏查询命中、索引 owner、锁等待和回滚窗口。

## 下一步

1. 通过重启安全门禁后加载鉴权修复，并用真实 request id 验收受保护请求只有一次用户查询。
2. 优先处理 PgBouncer 约 60 秒 query timeout/connect timeout 长尾，联合检查池大小、server lifetime、远端链路和同秒任务并发；不要先加索引。
3. 为通用 SQL 异常补齐非 publication_tasks 的 request id/origin 记录，避免 handler 返回 200 时形成观测盲区。
4. 调查 scheduler 配置查询的可失效短缓存和 workflow notification 的 SMTP 断连；两者分别属于高频数据库往返与外部 I/O 可靠性。
5. 下一轮继续核对 overview 冷请求、sync runs、MP3 轮询端点、Watchdog 新 PID，以及附件索引 scan 和写放大。
