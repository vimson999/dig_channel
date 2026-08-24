# 2026-08-10 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 **2026-08-09 06:02:19 至 2026-08-10 06:02:19（Asia/Shanghai）**。
整体不能标记已解决：应用慢 SQL 和 HTTP 长尾显著改善，但 PgBouncer 去重 query timeout
从 12 恶化到 46，且新 backend 边界后仍有真实 `/api/v1/celery/status` timeout；网络/长连接
复发键继续 `OPEN/OBSERVING`。

本轮运行态替换了两个可证实根因：失败 SQL 计时缺失导致 Session 归因错误；report audio
finalization 永久 JSON 扫描与详情页逐项摘要查询。两者均有红灯测试、代码修复、相关回归、
安全重启和真实 API 证据，但尚无下一完整窗口，只能 `RUNTIME_REPLACED/OBSERVING`。

## 上轮基线与本轮量化

| 指标 | 本轮 | 上轮 | 变化 | 判定 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL | 672 | 748 | -10.2% | 改善 |
| 慢 SQL P50/P95/max | 1.590/5.748/15.047s | 1.746/7.763/42.475s | -8.9%/-26.0%/-64.6% | 改善 |
| 慢 SQL 累计 | 1,564.712s | 2,041.483s | -23.4% | 改善 |
| HTTP 请求 | 5,057 | 1,781 | +184.0% | 流量增长 |
| HTTP 失败 | 54 / 1.07% | 114 / 6.40% | -52.6% / -5.33pp | 改善 |
| HTTP P50/P95/max | 0.013/1.722/33.577s | 0.006/8.438/160.279s | P95/max -79.6%/-79.1% | 改善 |
| HTTP 累计 | 2,180.839s | 3,479.250s | -37.3% | 改善 |
| 长连接持有 | 154 | 173 | -11.0% | 数量改善 |
| 持有 P50/P95/max | 8.113/75.166/76.160s | 8.248/32.657/256.708s | P95 +130.2% | 恶化 |
| Celery | 378 complete / 8 retry / 0 failed | 389 / 26 / 5 | retry -69.2%，failed -100% | 改善 |
| PgBouncer 去重 query timeout | 46 | 12 | +283.3% | 明显恶化 |

窗口内 statement timeout、lock timeout、deadlock、connection timeout、idle-in-transaction
捕获值均为 0；这不等于生产侧绝对未发生。SQL 错误 15 条（应用 13、ad-hoc 2）；
PgBouncer raw timeout 138 行对应 46 组事件。

## 主要 SQL 指纹（按累计耗时优先）

| 指纹 / 来源 | 次数 | 累计 | P95 / max | 根因判定 |
| --- | ---: | ---: | ---: | --- |
| `0b4ccc892361a813` ZSXQ topic 更新 | 40 | 136.857s | 9.021/14.550s | executor 外往返叠加逐批写；反复 |
| `60ff7467696400d3` scheduler config | 70 | 127.761s | 4.082/7.576s | 17 行 executor 0.022ms；网络/PgBouncer |
| `1f91e2f6c30e317a` comment 更新 | 44 | 85.173s | 5.748/11.557s | 网络长尾，非缺索引 |
| `19cc392c5b5cbe76` comment 更新 | 20 | 55.898s | 5.293/7.115s | 同上 |
| `36d6b585e39382cb` report child summaries | 25 | 52.148s | 4.625/8.907s | 高频列表关联；继续观察 |
| `590220bb0dd49238` MP3 load task | 10 | 33.507s | 6.503/max | 新 PID 前样本为主 |
| `88d325b52b433243` ZSXQ snapshots | 14 | 31.731s | — | 网络往返 |
| `4a107631d71dc10f` jobs page | 22 | 48.170s | max 11.544s | 列表/大字段风险 |

目标表慢 SQL：`publication_tasks` 224（上轮 155，+44.5%，恶化）、runs 5（-76.2%）、
topics 93（-46.6%）、comments 110（-25.2%）、attachments 21（-63.8%）、
external_documents 3（-62.5%）。

## 页面/API 全链路

| 页面/API | 请求/失败 | P50/P95/max | 累计 | SQL/调用链判定 |
| --- | ---: | ---: | ---: | --- |
| report audio jobs | 901/0 | 0.442/3.283/16.460s | 716.317s | 93 条慢 SQL/208.912s；最大累计成本 |
| report audio status | 222/0 | 0.560/3.397/6.033s | 212.950s | 轮询仍高频 |
| platform credentials | 326/0 | 0.325/1.094/5.593s | 141.785s | 窄查询受网络长尾 |
| report audio detail | 80/0 | 1.101/5.324/5.975s | 114.961s | 详情读大字段 |
| PDF evidence image | 80/50 | 0.581/2.609/3.388s | 67.460s | 主要为缺图 404；非 DB 失败 |
| Celery status | 14/0 | 1.592/20.070/30.205s | 64.861s | 含真实 PgBouncer timeout |
| stick scene image enqueue | 3/0 | 17.016/33.577/33.577s | 64.463s | 低频高延迟 |
| MP3 jobs | 196/0 | 0.016/1.531/5.645s | 60.380s | 上轮失败风暴显著改善 |
| Bilibili intelligence sync | 2/0 | 15.784/33.505/33.505s | 49.289s | 外部 I/O + 13 条慢 SQL |
| Insights overview | 9/0 | 0.002/14.920/14.920s | 25.719s | 含修复前冷样本 |

特殊链路：topics/local POST 7/0，P95/max 3.129s；tabs POST 6/0，max 0.272s；
scheduler status 2/0，max 0.835s。不存在通用 `/sync/status`，实际路径是
`/system-sync-scheduler/status`。GET 误调 topics/local/tabs 各 1 次快速 4xx，是方法契约错误。

低频高延迟还包括 confirm keyframes 2 次/max 17.955s、regenerate script 2 次/max 16.159s、
cover regenerate 4 次/max 10.648s。很慢但无匹配慢 SQL 的端点包括 scene-assets GET 7 次/
max 5.402s、rankings/all 17 次/max 1.755s；需继续检查外部 I/O、缓存和连接等待。

## 后台任务与无前台长持有

| 任务 | complete/retry/failed | P50/P95/max | 累计 |
| --- | ---: | ---: | ---: |
| report audio | 52/4/0 | 16.060/655.562/1441.378s | 5,640.685s |
| ZSXQ sync | 21/4/0 | 154.605/487.838/932.308s | 5,541.479s |
| MP3 process | 20/0/0 | 86.349/150.238/214.528s | 1,681.879s |
| publisher | 10/0/0 | 145.923/244.758/max | 1,652.860s |
| PDF clean | 3/0/0 | 167.4/max 740.911s | 998.598s |
| upload-only | 10/0/0 | 56.090/max 97.255s | — |

report audio `_wait_child` 28 个约 35.8s 样本与 14:30–14:39 timeout storm 同时发生；
MP3 13 个最长 76.160s。失败 SQL 计时缺陷使其“非 SQL”部分失真，需新窗口重新归因。

## 真实 timeout 调用链与反复

request `3b1274e9`：03:03:14 开始 Celery status → 03:03:34.986 PgBouncer timeout →
03:03:44.500 `_business_tasks_snapshot` SQL 错误 → HTTP 200/30.205s。同 SQL当前 executor
约 0.086ms。Web PID 已在 2026-08-09 16:42 加载，故不是旧进程残留。

00:12 另有 ad-hoc timeout；09:53–10:24、11:27–11:29、14:30–14:39 形成事件簇。
server crash 同时出现在老连接和 23–26 秒新连接，不能只归因 stale socket。本轮 PgBouncer admin
DB 密码认证失败，缺少 SHOW POOLS/SERVERS 实时快照；日志统计仍可用。

## 已落地修复与运行态验收

1. 失败 SQL 时长 accessor 缩进修复：20 秒失败测试先红后绿；新 PID 已加载，但尚无真实失败样本。
2. finalization JSON 扫描：约 938 次/33.5s；真实待 v2 修复行数为 0，watchdog 改为仅 stale active。
3. finalization N+1：约 274 次/15.4s；改为每页一次、最多 100 id、JSON 子字段投影。
4. 剩余父任务 over-fetch：真实 detail=1/5 项仍传 856,468 bytes，父 SQL 1.858s、HTTP 4.254s，OPEN。

重启前两次安全检查均确认无不可中断任务。完整栈重启后 backend PID `64496` health 200，
report audio、MP3、ZSXQ、content pipeline 等 worker ready，watchdog/scheduler 存活。真实 API：
Celery status 200/1.454s、Insights 200/2.710s、topics/local 200/0.638s、tabs 200/0.245s、
scheduler status 200/0.436s、report detail 200/4.254s；finalization 字段完整且无旧逐项指纹。

## 表规模、索引与 executor 证据

| 表 | 总大小 | live/dead | HOT update | index scan |
| --- | ---: | ---: | ---: | ---: |
| zsxq_topics | 123MB | 11,559 / 2,047 | 0.83% | 99.67% |
| zsxq_topic_comments | 118MB | 41,079 / 4,486 | 67.39% | 99.93% |
| publication_tasks | 118MB | 11,899 / 765 | 1.40% | 99.80% |
| zsxq_attachments | 69MB | 32,172 / 3,835 | 0.12% | 99.95% |
| zsxq_sync_runs | 34MB | 913 / 189 | 0.34% | 99.94% |
| external_documents | 6.9MB | 1,760 / 146 | 0% | 99.11% |

PostgreSQL 16.13 现场无 waiting lock、无 idle in transaction；角色 timeout 为 60s/5s/60s；
未安装 pg_stat_statements。EXPLAIN：publication PK 0.043ms、channel chart 1.846ms、scheduler
config 17 行 0.022ms、publisher list 0.058ms、content list 0.403ms、recent JSON 20 行
0.025ms、runs 913 行 1.197ms、Insights 6,476 行 35.752ms。应用秒级耗时不由 executor/索引解释。

未发现完全重复索引；低使用索引无足够 drop 证据。本轮不执行 DDL。现有人工脚本
`docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql` 仍待 DBA 在 DBeaver 执行；门禁确认
两个等价重复 topic 索引仍存在且迁移未登记。脚本自带 timeout、验证和回滚；并发写/owner 是风险。

## 测试与未捕获边界

- TDD：4 条新增行为先红后绿；相关模块 177 tests passed。
- 完整 unit：`PYTHONPATH=src/backend ... unittest discover -s tests/unit -q`，1,086 tests / 39.992s，PASS。
- `compileall`、`git diff --check`、registry JSON 解析均 PASS；严格 Bug 检查 157/157 已归类，PASS。
- 新失败 SQL 埋点尚无真实 timeout 样本；不能证明运行态归因已经校准。
- 历史 SQL 无 server-side executor trace；PgBouncer admin auth 失败；smoke 只有数分钟。
- 54 个 HTTP failure 含业务 4xx/缺图；HTTP 200 也可能包含 30 秒降级和 DB timeout。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `DB-SESSION-LIFECYCLE` / `58f8...` | executor 外网络/PgBouncer timeout | 既有网络隔离和短事务防线 | request `3b1274e9` 仍 30.205s | 是 | 运维查 MTU/reset/socket；OPEN | 24h timeout=0、crash=0、20/60s持有=0 |
| failed SQL duration=0 | accessor 缩进导致失败时长丢失 | accessor + 契约测试 | 新 PID/health；无失败样本 | 未知 | OBSERVING | 下一真实失败 SQL 的 sql_max 覆盖等待 |
| `6b1c434d80a9291e` | 完成任务永久 JSON 轮询 | watchdog 仅 stale active | 新 worker/scheduler 已加载 | 修复前反复 | 完整窗口待验 | 新 PID 24h 指纹=0，active repair 正常 |
| `af15f...` finalization N+1 | 逐项完整 JSON 查询 | bounded subfield projection | 真实 5 项字段完整、无该指纹 | 修复前反复 | 父 over-fetch 仍 OPEN | N+1=0，查询数不随项目线性增长 |
| `21727658a045825c` parent payload | detail 列表整页父 JSON | 尚未根治 | 5 行 856KB，SQL 1.858s | 是 | API owner 分离摘要/详情 | 不再批量 full payload，字段/页面验收通过 |
| Insights cold transfer | 全集正文跨网络 | 上轮 DB 内匹配+当前页详情 | smoke 2.710s | 短窗未见 | OBSERVING | 24h cold P95<5s、max<10s、旧指纹=0 |

## 下一轮明确验收计划

1. 以 PID `64496`/06:17:53 和 worker 06:18 后为边界重跑 24 小时，旧 PID/ad-hoc 分栏。
2. 优先复核 46 组 timeout、server crash、Celery snapshot，并用新失败计时重算 SQL/non-SQL。
3. finalization 两个旧指纹必须为 0，且至少有真实 active repair 或详情流量。
4. 为 detail 父 payload 制定不丢字段的摘要/单详情拆分，以字节、SQL 次数、P95 验收。
5. 继续按累计、P95、最大、失败率、受影响用户/页面、复发次数排序，不以数量暂降作为 CLOSED。
