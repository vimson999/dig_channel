# 2026-08-25 数据库性能与错误巡检

## 巡检结论

固定窗口为 `2026-08-24 06:02:19` 至 `2026-08-25 06:02:19`（Asia/Shanghai）。
已扫描 `slow_query.log`、`app.log`、`backend.log`、`scheduler.log`、
`celery/*.log`、PgBouncer 活动及轮转日志，并以 2026-08-20 报告为基线。

本轮**不能判定数据库性能问题整体已解决**：HTTP 总耗时明显改善，但应用慢 SQL P95、
PgBouncer query timeout 和连接持有次数恶化；代表查询的 PostgreSQL executor 仍在
`0.022–0.317ms`，根因继续指向远端网络/PgBouncer 往返与上游请求扇出，而不是缺索引。

本轮新增并落地一个无需生产 DDL 的根因修复：MP3 详情页无条件挂载三个发布面板，使隐藏
抖音/视频号面板也读取凭据和上传历史，同一加载还重复读取 B站凭据。代码已改为仅挂载活动
标签、切换时惰性加载、保留草稿并复用首次凭据。静态运行资源已替换，但尚无后续真实浏览器
request-id 窗口，状态为 `RUNTIME_REPLACED / OBSERVING`，不是 CLOSED。

两个上一轮问题取得后续窗口证据：研报物料页隐藏 Celery status 轮询从 809 次降到 3 次且
不再有分钟级序列，记录 CLOSED；ZSXQ scheduler 配置读取稳定为约每 5 分钟一次，轮询放大
根因 `RUNTIME_ACCEPTED`。B站旧全字段 JSON UPDATE 为 0，但窄更新仍有网络长尾且重复索引
未人工清理，继续 OPEN/OBSERVING。

## 与上一轮量化对比

| 指标 | 2026-08-20 基线 | 本轮 | 变化与判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL 数 | 385 | 226 | -41.3%，改善 |
| 应用慢 SQL 累计 | 856.984s | 598.820s | -30.1%，改善 |
| 应用慢 SQL P50 | 1.547s | 1.627s | +5.1%，轻微恶化 |
| 应用慢 SQL P95 | 5.451s | 7.922s | +45.3%，恶化 |
| 应用慢 SQL最大 | 15.516s | 17.576s | +13.3%，恶化 |
| HTTP 请求 | 2,760 | 1,607 | -41.8%，流量下降 |
| HTTP 累计 | 2,255.433s | 771.742s | -65.8%，改善 |
| HTTP P95 / 最大 | 4.286s / 23.281s | 2.231s / 18.284s | -47.9% / -21.5%，改善 |
| HTTP 失败 | 2 / 0.07% | 2 / 0.12% | 数量不变；低流量下失败率恶化 |
| 连接长持有 | 28 | 39 | +39.3%，恶化 |
| 持有 P95 / 最大 | 20.520s / 62.183s | 20.439s / 20.490s | P95 持平；极值改善 |
| PgBouncer 去重 query/connect timeout | 1 / 5 | 5 / 5 | query 明显恶化；connect 未改善 |
| PgBouncer wait/query P95 | 115.17 / 199.49ms | 94.61 / 149.32ms | -17.9% / -25.1%，统计采样改善但超时未清零 |

全部慢样本为 240 条（含 14 条人工诊断），P50/P95/最大为
`1.753/7.922/17.576s`。应用事件中 statement timeout、lock timeout、deadlock、
connection timeout、idle in transaction 均为 0；有 1 个托管 SQL OperationalError 和
3 个明确归为 adhoc 的诊断错误。没有将 adhoc 错误混入生产失败率。

## 页面/API 全链路

按累计耗时排序的主要端点如下：

| 端点 | 请求/失败 | 累计 | P95 / 最大 | 关联慢 SQL | 判断 |
| --- | ---: | ---: | ---: | ---: | --- |
| `GET /api/v1/platform-credentials` | 188 / 0 | 142.744s | 2.648 / 16.993s | 19 / 46.404s | MP3 隐藏面板扇出主受害者；executor 0.067ms |
| `GET /api/mp3-video-workflows/jobs` | 217 / 0 | 91.073s | 2.071 / 12.115s | 11 / 29.846s | 高频列表，网络尾延迟 |
| `POST .../bilibili-upload` | 12 / 0 | 64.747s | 14.400 / 14.763s | 4 / 27.131s | 低频高延迟 |
| `GET .../jobs/:id` | 36 / 0 | 59.943s | 6.918 / 8.388s | 10 / 31.574s | 宽任务详情 + 页面扇出入口 |
| `GET .../wechat-channels-uploads` | 50 / 0 | 40.189s | 2.728 / 6.662s | 2 / 3.083s | 隐藏面板无收益请求 |
| `GET .../bilibili-uploads` | 34 / 0 | 39.196s | 4.172 / 5.364s | 6 / 10.392s | 活动面板/上传状态 |
| `GET .../douyin-uploads` | 50 / 0 | 38.292s | 2.681 / 5.286s | 4 / 9.836s | 隐藏面板无收益请求 |
| `POST .../xiaojunjun_background/regenerate` | 4 / 0 | 29.293s | 17.759 / 17.759s | 2 / 13.705s | 低频高延迟 |
| `POST /api/mp3-video-workflows/jobs` | 6 / 1 | 28.928s | 18.284 / 18.284s | 3 / 5.047s | 失败率 16.7%，需保留真实失败 |
| `GET /api/mp3-video-workflows/audio-assets` | 10 / 0 | 24.841s | 15.239 / 15.239s | 4 / 15.447s | 低频高延迟，附件/正文预览查询 |
| `POST .../xiaojunjun_cover/regenerate` | 5 / 0 | 21.529s | 9.215 / 9.215s | 0 | 页面慢但无匹配慢 SQL |

另一个失败为 `GET /api/bilibili-publish/charging-options` 1/10（10%）。HTTP 失败只有两条，
但不能因总体失败率低而忽略受影响的创建任务与充电选项页面。

### request id 与调用链证据

MP3 详情最慢 request `b4243f35` 为 8.388s。其完成后同一页面在约一秒内发出
`42947126`、`dc7f3c48`、`2724ce31` 三条平台凭据请求，并读取隐藏抖音/视频号上传历史。
`42947126` 自身持有连接 5.106s，其中 SQL 2.790s、非 SQL 持有 2.316s。源码对应
`render()` 同时 mount 三个平台，以及页面和 B站面板各自加载凭据。

连接持有 39 次中，22 次来自 Web、5 次 worker、1 次 scheduler、11 次 adhoc；SQL 累计
330.903s，非 SQL 持有 44.263s。`load_task` 9 次长持有累计 83.235s，其中非 SQL
23.228s；7 次托管长持有没有匹配到单条慢 SQL，P95/最大 12.164s，证明页面/API 慢并不总能
由 slow SQL 一一解释。

## 主要 SQL 指纹与根因排序

| 指纹 / origin | 次数 | 累计 | P95 / 最大 | 根因判断与状态 |
| --- | ---: | ---: | ---: | --- |
| `590220bb0dd49238` `load_task` | 12 | 62.005s | 7.573 / 11.719s | publication_tasks 宽详情；页面扇出已修，宽载荷仍 OPEN |
| `60ff7467696400d3` scheduler config | 24 | 55.681s | 4.246 / 15.489s | 全部成功读取已降为 5 分钟节奏；轮询 CLOSED、网络 OPEN |
| `dc88dd6d9de9df14` platform credentials | 17 | 42.762s | 4.451 / 14.687s | 19 行小表；请求扇出已修，待真实窗口 |
| `861525af70dfab9f` audio assets | 8 | 34.781s | 14.909 / 14.909s | 附件+正文预览，低频网络长尾 OPEN |
| `3d9e2b7947d50756` B站 competitor select | 2 | 23.813s | 15.529 / 15.529s | 代表查询窄、executor 快；网络 OPEN |
| `e73fec8af8fe216a` content progress update | 11 | 24.353s | 6.071 / 6.071s | 窄更新仍被网络放大；继续观察 |
| `fe0ed7e949a50f71` optimized video select | 1 | 17.576s | 17.576 / 17.576s | 低频后台、无前台端点，网络/缓存回源 OPEN |

publication_tasks 专项分析捕获 278 条记录、71 个签名（含 106 条日志重复），仍有
`slow_payload`、`slow_payload_write` 与 multi-row payload；重点表慢样本为 publication_tasks
94、zsxq_topics 9、zsxq_attachments 9，其余重点表在当前阈值下为 0。阈值下未捕获不等于
未发生，下一轮仍需按 JSON 条件、宽详情和批量写分类复核。

唯一托管 SQL 错误位于 `_legacy_pdf_asset_items`，与 10:04:55 PgBouncer query timeout 同窗，
连接持有 20.446s、SQL 20.413s；状态 OPEN，不能用成功重试掩盖。statement/lock timeout 为 0。

## 后台任务

Celery 记录 232 个完成事件、3 个失败事件、0 retry，完成累计 7,838.331s：

| 任务 | 完成 / 失败事件 | 累计 | P95 / 最大 | 判断 |
| --- | ---: | ---: | ---: | --- |
| publisher pipeline | 11 / 1 | 2,315.883s | 312.241 / 312.241s | 失败事件率 8.3%，媒体/外部 I/O 长任务 |
| ZSXQ publish clean output | 4 / 0 | 1,456.950s | 784.754 / 784.754s | 后台长任务，无前台端点 |
| PDF clean | 2 / 0 | 1,428.174s | 1,292.130 / 1,292.130s | 外部处理主导，不是 executor |
| B站 upload only | 10 / 2 | 1,071.622s | 166.221 / 166.221s | 失败事件率 16.7%，需按 task id 复核最终状态 |
| MP3 workflow job | 67 / 0 | 983.642s | 89.452 / 146.753s | 页面/API 主业务链 |
| audio clean | 2 / 0 | 391.317s | 333.404 / 333.404s | 后台长任务 |
| workflow notify | 136 / 0 | 190.744s | 1.594 / 2.150s | 本窗无风暴，但缺故障注入/最终送达，SMTP 根因仍 OPEN |

`workflow_notify` 的 0 retry/0 failed 只说明本窗口没有捕获 SMTP 故障，不能替代跨 event
provider 熔断、durable pending 和最终送达证据。

## PgBouncer、PostgreSQL executor 与 Session

- PgBouncer 原始日志为 connect timeout 5、query timeout 12 行；按时间去重后分别 5/5。
- 1,440 个 stats 样本 wait P50/P95/最大为 `26.90/94.61/1139.13ms`，query 为
  `54.50/149.32/923.36ms`。
- server source 全部为本机 Tailscale 地址 `100.96.3.12`，1,735 次连接，proxy TUN 回退为 0。
- 现场 `SHOW POOLS`：业务池 client active/waiting 为 0/0，6 个 server 为 `used`；
  `SHOW SERVERS` 均使用 TLSv1.3，远端 `100.92.99.39:5432`，当前 wait=0。
- PostgreSQL 现场为 1 active、7 idle、0 idle-in-transaction、0 waiting lock；没有锁队列证据。
- `EXPLAIN (ANALYZE, BUFFERS)`：system_configs 0.022ms、platform credentials 0.067ms、
  publication_tasks 宽 point lookup 0.041ms、MP3 list 0.159ms。秒级应用耗时不能归因于 executor。

结论：当前没有通过新增索引能消除的主要 executor 瓶颈；PgBouncer/网络根因保持 OPEN。

## ZSXQ 特别链路

固定窗口没有捕获 `zsxq-insights overview`、`topics/local`、`topics/local/tabs`、
`sync/status` 的业务访问，不能报告为“没有问题”。窗口后做了只读真实探针：

- `topics/local` 首次 200/16.819s，主题 21 行 SQL 6.081s、附件 51 行 SQL 9.562s；
  executor 现场仅 0.039/0.317ms。随后 5 次 API 为 1.057–3.445s，显示冷网络/池长尾。
- `topics/local/tabs` 200/0.158s；`sync/status` 200/0.446s。
- Insights 冷请求 200/4.253s，第二次缓存 HIT 0.56ms；说明缓存有效，但冷链网络成本仍在。
- 当前页附件约 52 行，raw_payload 合计约 48KB、完整行约 59KB，不支持“大字段传输是本次
  16.8s 主因”的解释。

## 重点表规模与索引证据

| 表 | 总大小 | live / dead | seq scan / idx scan | 判断 |
| --- | ---: | ---: | ---: | --- |
| `zsxq_topics` | 129MB | 12,637 / 1,584 | 3,426 / 884,614 | 索引使用充分；关注 dead tuple |
| `publication_tasks` | 126MB | 12,382 / 10 | 6,886 / 2,960,255 | 宽 JSON 热点来自访问形态，不是缺点查索引 |
| `zsxq_topic_comments` | 118MB | 41,333 / 5,644 | 295 / 356,958 | dead tuple 较高；仅维护窗治理 |
| `zsxq_attachments` | 78MB | 33,906 / 37 | 591 / 1,133,722 | 当前页代表查询走既有组合索引 |
| `zsxq_sync_runs` | 37MB | 1,062 / 46 | 230 / 337,003 | 无本窗慢样本 |
| `external_documents` | 7MB | 1,817 / 146 | 1,026 / 84,935 | 无本窗慢样本 |

六张重点表的生产定义未发现完全相同键序的重复索引。本轮未执行 CREATE/DROP INDEX、
ALTER TABLE、数据回填或生产配置变更，也未删除/裁剪原始 JSON。

### 待人工 SQL 与风险

1. `docs/sql/bilibili_competitor_bvid_index_cleanup_dbeaver_20260819.sql` 仍可在人工维护窗逐段
   复核：真实证据为两个等价 UNIQUE btree(bvid)，保留约束索引、并发删除独立索引；脚本含
   `lock_timeout`、`statement_timeout`、验证和回滚。未执行前重复索引风险 OPEN。
2. `docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql` 同时包含列回填、多个 DROP INDEX、
   ANALYZE 和 alembic 版本修改，风险显著高于单索引清理。本轮现场六表无完全重复索引，禁止
   直接执行；必须先重新采集每个索引的定义、scan、写放大、迁移头和回滚方案，再由人工决定。

## 已落地变更与验证

- MP3 发布中心只挂载活动面板；平台切换前保存草稿并惰性加载目标面板。
- 页面已取得的 B站凭据直接复用；失败读取仍可由面板重试。
- HTML cache-bust 更新为 `20260825-publish-panel-lazy-mount-v1`，本地运行服务已返回新版本。
- 新增 Node 真实类行为契约；旧代码先因缺少活动面板分发器失败，修复后通过。
- 修正 DB 投影纪律测试的控制流假阳性：原测试以第一次 `commit()` 做全文切分，把互斥并已
  return 的另一分支快照误判为 commit 后 ORM 回读；改为逐 AST block 验证每次 `task.to_dict()`
  后的下一语句必须 commit，生产代码未改变。

验证结果：

- 定向：7 passed；发布标签页文件单独 6 passed；Node 行为契约通过；JS syntax 通过。
- 完整 unit 分段覆盖全部 1,375 项：沙箱内 1,340 passed / 3 skipped；仅 3 个 loopback
  文件获准绑定 127.0.0.1 后 32 passed。一次未带 PYTHONPATH 的收集失败和一次放开全部网络后
  进入真实 SSL 等待的尝试均不计作通过，后者在 741 passed 后安全中断。
- `compileall`、`git diff --check`、recurrence JSON、Bug record strict 检查均通过。
- 改动仅为静态前端、测试和文档，不需要重启 backend/Celery/scheduler，因此未运行重启脚本；
  避免无收益地触碰正在运行的任务。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 状态 / 剩余风险 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| MP3 detail → credentials/uploads 扇出 | 隐藏面板无条件 mount + B站凭据双 owner | 活动面板惰性挂载、草稿保留、凭据复用 | 新静态资源 200；缺真实浏览器 request cluster | 新增 | RUNTIME_REPLACED/OBSERVING；旧页面与其他 mount 入口风险 | 一次详情最多 1 条 B站凭据；未切标签时抖音/视频号请求为 0 |
| `/celery/status` 隐藏轮询 | hidden 页面旧 timer | 共享 Visibility/single-flight（上一轮） | 809→3，无分钟序列 | 否 | CLOSED | registry 级常规监控，复发立即 reopen |
| `60ff...` scheduler config | 60s TTL 把 30s loop 退化为分钟查询 | 300s 成功快照（上一轮） | 291 次严格约 5min；慢样本 -78.2% | 否 | RUNTIME_ACCEPTED；网络长尾另属 OPEN | 保持约 288/日，不出现分钟级序列 |
| `f28c58...` B站全字段 UPDATE | 双 scheduler + 未变化 JSON 重写 | OS lock + 变化列写入（上一轮） | 22 周期、单序列、旧指纹 0 | 否 | 代码根因接受；窄 UPDATE 网络尾延迟和重复索引 OPEN | 人工索引脚本验收；窄 UPDATE P95<500ms |
| PgBouncer query/connect timeout 5/5 | 远端网络/池冷连接与 fetch/rollback 往返 | 既有热池/路由防线 | executor <0.4ms；timeout 未清零 | 是 | OPEN，高风险 | 完整窗口 query/connect timeout=0；分解 execute/fetch/rollback |
| publication_tasks 宽详情/JSON | 页面宽详情与任务状态载荷 | 本轮只减少无收益调用次数 | 94 慢样本，`590...` 62.005s | 是 | OPEN；不能删字段或审计 JSON | 按页面请求预算下降；宽字段按需但字段完整 |
| SMTP provider 风暴 | 跨 event 无共享熔断/durable pending | 既有单层指数抖动 | 136 success/0 fail，无故障样本 | 未捕获 | OPEN；未捕获不是未发生 | 故障注入、全局连接预算、event 最终送达 |
| ZSXQ 冷链 | 远端网络结果 fetch/回收尾延迟 | 既有有界 scope/缓存 | 冷 4.253–16.819s，热 0.56ms，executor <0.4ms | 仍见 | OPEN/OBSERVING | 固定业务流量窗口；execute/fetch/rollback 分段且 timeout=0 |

## 未捕获边界与下一轮明确计划

- 固定窗口没有 ZSXQ 四个重点端点的自然业务流量；窗口后探针只能补根因证据，不能替代完整
  生产流量分布。下一轮优先按端点、request id 与 SQL origin 重新关联。
- 当前日志可关联 SQL hook 与 connection hold，但不能完整分离所有 result fetch、socket wait、
  rollback/checkin；`pg_stat_statements` 当前不可用。下一轮继续用 lifecycle 分段日志补齐。
- Celery 指标以日志事件为准，队列等待与执行时间未对所有任务完全分离；失败事件不保证终态未恢复。
- 新 MP3 前端没有自动操控现有已认证浏览器，因此缺真实 UI 标签切换 E2E；下一窗口必须用真实
  新版本页面产生 request cluster，不能用 Node 通过冒充运行态闭环。
- 下一轮按优先级复核：PgBouncer 5/5 timeout 与 legacy PDF error；MP3 请求预算；
  publication_tasks 宽载荷；B站窄 UPDATE/人工索引；ZSXQ 冷链；SMTP 故障与最终送达。
