# 2026-10-18 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 Asia/Shanghai `2026-10-17 06:00:00..2026-10-18 06:00:00`，对应数据库
UTC-naive `2026-10-16 22:00:00..2026-10-17 22:00:00`。慢 SQL 明显改善且没有 SQL、锁、连接或
PgBouncer 错误，但**不能判定全局已解决**：29/29 条目标 durable task 全部携旧 checkout
`dig_ytb_data_claude_v1`，Publisher 4/16 失败、失败率 25%，旧部署/共享 broker 根因再次自然复发。
本轮把 required-material 终态 writer 的 deployment root、hostname、PID、code identity 持久化到审计 JSON
并写入错误日志；经安全门后标准重启，当前代码已 `RUNTIME_REPLACED`，但尚无修复后的自然同类任务，整体保持
`OPEN / REPEATED / OBSERVING / WAITING_DISTRIBUTED_ISOLATION`。

## 与上一窗口量化对比

| 指标 | 2026-10-17 | 本轮 | 变化/判定 |
| --- | ---: | ---: | --- |
| 慢 SQL 数量 | 16 | 6 | -62.5%，改善 |
| 慢 SQL 累计 | 39.521s | 8.533s | -78.4%，改善 |
| SQL P50 / P95 / max | 1.307 / 7.473 / 7.505s | 1.177 / 2.126 / 2.126s | -10.0% / -71.5% / -71.7% |
| SQL/PgBouncer 错误 | 0 | 0 | 未捕获错误 |
| HTTP | 851 请求、110 端点 | 6 请求、6 端点 | 覆盖骤降；6 条均为上一轮巡检烟测 |
| HTTP P50 / P95 / max | 12.68ms / 1.552s / 60.294s | 263.91ms / 3.348s / 3.348s | 不可作自然流量同比 |
| Publisher | 16/19 完成、3 失败 | 12/16 完成、4 失败 | 失败率 15.8%→25%，恶化 |

窗口内 statement timeout、lock timeout、deadlock、连接/query/query-wait timeout、server closed、持久
idle-in-transaction 均为 0。PgBouncer 1,439 个 stats 样本，连接 churn 288/24h（12/h），wait
P50/P95/max 28.80/43.17/127.52ms，query 64.66/70.60/132.49ms；runtime guard 只见 1 个短 episode，
最大事务龄 0.270 秒、idle 0.234 秒。

## 主要问题排序与调用链

| 排名 | 指纹/链路 | 修复前/本窗证据 | 根因与状态 |
| ---: | --- | --- | --- |
| 1 | `RUNTIME-TOPOLOGY:old-root-shared-broker` | 29/29 durable task 为 old-root；Publisher 4/16 失败，失败壁钟 897.952s、P95 248.151s、max 251.409s | 旧 Mac 在任务时段在线并继续写共享 DB/Redis/Celery；当前 content worker 还误消费 1 条旧根 PDF task，证明 namespace 未隔离。`OPEN/REPEATED` |
| 2 | Publisher `required_material_unavailable` | 4 条均由当前 Watchdog fail-closed，Publisher 总壁钟 4,483.337s | 本地绝对路径与跨部署 consumer 不兼容；终态缺 actor 证据使上一轮无法归因。本轮已补 actor 审计并运行替换，待自然后窗。 |
| 3 | `60ff7467696400d3` all-tabs config | 3 次/4.741s，P95/max 2.126s，origin `_db_config_rows` | 表仅 18 行，EXPLAIN executor 0.022ms；是 driver/PgBouncer/私网 fetch 尾部，不是缺索引。`OPEN/OBSERVING` |
| 4 | Insights `674016d39deab3f5` | 1 次/1.618s，overview 3.348s | 同形 executor 34.938ms；仍是 executor 外尾部。 |
| 5 | YouTube id21/56 | channel 49/51、video errors=2，第 15 窗复发 | 永久不可用来源仍启用；受保护停用脚本待人工。`OPEN/REPEATED/WAITING_MANUAL_CONFIG` |

## 页面、API、后台任务与 SQL 关联

- 固定窗只有 6 个 HTTP，全部是上一轮 06:19 左右的巡检烟测：overview 3.348 秒关联 Insights SQL
  1.618 秒；topics/local 0.801 秒、sync status 0.603 秒、statistics 0.264 秒、tabs 0.220 秒和 health
  7.46ms 没有匹配慢 SQL。没有自然业务 HTTP，不能宣称其他页面、N+1、大字段或轮询未复发。
- 低频高延迟但无慢 SQL：topics/local 与 sync status；窗口样本太少，继续按网络、亚秒 SQL、序列化和缓存
  分段。后台任务长壁钟也没有前台端点：MP3 workflow 3 条累计 4,241.237 秒、PDF clean 4 条
  1,590.974 秒、ZSXQ publish 5 条 1,181.494 秒，主要是外部处理、排队和子任务编排，不是 executor。
- 本地 Celery 日志只捕获 95 个 completed、0 retry/failed，其中 94 个 workflow notification 和 1 个 PDF
  clean；durable DB 明显显示更多旧部署工作，说明当前日志集合不覆盖全部 actor。未捕获不能记为没有问题。
- 窗后标准重启后真实只读 E2E：health 200/0.284 秒，overview 200/0.588 秒，topics/local
  200/0.768 秒，tabs 200/0.358 秒，sync/runs 200/0.454 秒，scheduler status 200/0.372 秒。
  这些只证明当前 backend 可用，不替代自然流量后窗。

## 外部任务复核

- B站情报 24/24 自然周期、168/168 competitor 成功，累计 325.382 秒，P50/P95/max
  13.221/15.146/18.852 秒；连续第二个完整绿窗，但历史 DNS/TUN 故障现场仍没有物理断点证据，保持
  `OPEN/OBSERVING/WAITING_NETWORK_TRACE`。
- YouTube 自然 channel 49/51、耗时约 20.77 秒；video 处理 49 个来源、`errors=2`、耗时约 60.44 秒，
  失败仍精确为 id21/56。需人工执行受保护业务脚本后用连续两个自然周期验收。
- durable notification 83/83 sent、attempt 均为 1，没有通知失败风暴。

## 表、索引与 EXPLAIN 证据

| 表 | 总大小 | live/dead | idx scan 占比 |
| --- | ---: | ---: | ---: |
| publication_tasks | 148MB | 14,564 / 695 | 99.83% |
| zsxq_sync_runs | 37MB | 1,062 / 46 | 99.91% |
| zsxq_topics | 129MB | 12,637 / 1,584 | 99.52% |
| zsxq_topic_comments | 118MB | 41,333 / 5,644 | 99.85% |
| zsxq_attachments | 78MB | 33,906 / 37 | 99.94% |
| external_documents | 6.9MB | 1,817 / 146 | 98.74% |

没有完全重复索引；统计自 2026-08-08 未 reset，低 scan 索引不能直接代表本窗价值。代表 EXPLAIN：all-tabs
config 0.022ms、Insights 34.938ms、Watchdog 主键 0.051ms、Publisher/Content 列表 0.055/0.095ms、
ZSXQ system sync 1.674ms，均无缺索引证据。`zsxq_attachments` dead tuple 已由 37,650 降到 37，表明
autovacuum 生效。本轮不建议 CREATE/DROP INDEX，也未生成生产 DDL。

## 已落地修复与验证

- `publisher_queue_repair.py` 在 pending/processing 的 required-material fail-closed 审计和日志中加入
  deployment root、hostname、PID、code identity；保留原 params/result 和缺失路径，不伪造素材、不删审计。
- TDD 先红：两个定向测试因缺 `actor` 失败；实现后 2/2 绿，相关文件 54/54、完整 unit 1,737/1,737
  通过。
- 重启前安全门读取 46 条近期任务和 0 条活动 ZSXQ run，确认无不可中断任务；标准重启后 backend PID
  14483、17/17 worker、Watchdog PID 15040、6/6 scheduler 均来自当前 checkout，bootstrap `errors=[]`。
- 代码已 `RUNTIME_REPLACED`；没有人为制造任务或假数据，窗后尚无自然 required-material 终态，因此仍为
  `OBSERVING`，不能写成已解决。

## 待人工动作、风险与未捕获边界

1. 在旧 MacBook Pro 停止并卸载 backend、Celery、Watchdog、scheduler 和手工脚本；若不能证明全停，轮换
   PostgreSQL/Redis 凭据并隔离 Celery broker namespace。旧主机当前离线只是一帧快照，不是物理隔离。
2. 在 DBeaver 审核并逐条执行
   [`disable_unavailable_youtube_channels_20260920.sql`](../../sql/disable_unavailable_youtube_channels_20260920.sql)；
   这是业务配置变更，不在无人值守巡检中执行。未新增索引/表结构 SQL。
3. HTTP 自然业务覆盖从 851 降为 0（6 条均为旧烟测），当前日志只覆盖本机 actor；其他页面、跨主机 worker、
   大字段/N+1/轮询只能标记未捕获。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| old-root shared broker | 旧部署共享 DB/Redis/Celery 与本地绝对路径 | actor 审计门禁已运行替换 | 29/29 old-root、Publisher 4/16 失败；当前栈 ready | **是** | 人工停旧栈或轮换凭据并隔离 namespace | 完整 24h all-feature old-root=0、foreign actor=0，当前根自然任务成功 |
| required-material terminal writer | 终态缺 deployment/host/PID/code 归因 | JSON+日志持久化 actor | Watchdog PID 15040 已加载；无自然同类事件 | 未验收 | 不能用单元测试替代后窗 | 自然终态必须可归因且假关闭=0 |
| DB tail fingerprints | executor 外 driver/PgBouncer/私网 fetch | 保持轻投影与缓存；未盲加索引 | app 1.6–2.1s vs executor 0.022–34.938ms | 是 | 尖峰同秒采 pool/socket/route | 连续后窗 P95 回落或取得网络断点 |
| YouTube 21/56 | 永久不可用来源仍启用 | 受保护人工脚本已备 | 第 15 窗 2 个固定失败 | **是** | 人工执行脚本 | 连续两周期 channel 49/49、video errors=0 |
| B站 DNS/TUN | 历史外部链路断点未知 | 逐 competitor 隔离与结构化结果 | 24/24、168/168，第二绿窗 | 否 | 下一故障同秒取证 | 后续全绿或捕获 DNS/TUN/目标证据 |

## 下一轮明确计划

1. 第一优先按 actor 审计复核任何 Publisher fail-closed，按 hostname/root/PID/code identity 归因；检查所有
   feature old/current/unclassified 和跨 namespace 消费，禁止只看当前 DB 会话。
2. 要求旧 Mac 物理停栈或凭据/namespace 隔离证据；未完成前即使 old-root 暂时归零也不得关闭。
3. 对 all-tabs/Insights 新尖峰保存同秒 PgBouncer、socket/route 和 executor 对照，继续禁止盲加索引。
4. 重新建立全站自然 HTTP/后台任务覆盖；重点复核 overview、topics/local、tabs、sync status，但不忽略其他端点。
5. YouTube 21/56 人工处理后验收双自然周期；B站下一窗继续 24/24，失败时必须现场取证。
