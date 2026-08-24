# 2026-08-05 数据库性能与错误巡检

## 结论

固定统计窗口为 2026-08-04 06:02:19 至 2026-08-05 06:02:19（Asia/Shanghai），覆盖 `logs/slow_query.log`、`logs/app.log`、`logs/backend.log`、`logs/scheduler.log`、`logs/celery/*.log` 以及与窗口重叠的文本和 gzip 轮转日志。本轮应用慢 SQL 1,363 条，P50/P95/最大为 1.502/9.262/60.844 秒，累计 4,359.842 秒；较上轮分别增加 174.2%、8.7%、1.3% 和 167.5%。HTTP 共 7,284 次、161 个端点，成功/失败 7,253/31，P50/P95/最大为 87.51ms/2.649s/92.920s，累计 6,290.320 秒。

最高优先级仍不是增加索引，而是连接边界和前端轮询的乘数效应：固定窗口前两小时仍有 498 次 PostgreSQL HBA 拒绝；PgBouncer 有 29 个去重 query timeout 和 12 个 connect timeout；托管应用进程有 82 次无法匹配慢 SQL 的长 Session，P95/最大 61.034/92.486 秒。最坏的研报音频列表请求在同一 request id 下先等待 32.106 秒，随后子查询因 server closed 等待约 60.226 秒，连接共持有 92.486 秒，最终 HTTP 200 用时 92.920 秒。真实 `EXPLAIN (ANALYZE, BUFFERS)` 的代表性应用查询仅 0.024-2.937ms，Insights 全量 canonical 查询为 31.684ms，不支持对 60-90 秒应用长尾盲目加索引。

本轮已安全落地研报音频页面轮询治理：后台标签页停止轮询、可见时恢复、测试页间隔由 8 秒放宽至 15 秒，详情页由 6 秒放宽至 10 秒，自动刷新从“列表 + 每个活动任务详情 N+1”改为单次批量列表请求。未执行 DDL、数据回填、生产配置或破坏性操作；未删除业务字段或原始 JSON 审计数据；静态资源带新 cache-bust，浏览器刷新即可生效，不需要重启后端、Celery 或 scheduler。

## 量化证据与上一轮基线

| 指标 | 本轮 | 上轮 | 判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 1,363 | 497 | +174.2%，恶化 |
| P50 / P95 / 最大 | 1.502 / 9.262 / 60.844s | 1.503 / 8.511 / 60.074s | 中位稳定，长尾恶化 |
| 应用慢 SQL 累计 | 4,359.842s | 1,629.957s | +167.5%，恶化 |
| ad-hoc 慢 SQL | 272 | 11 | 已与应用排名隔离，不能混算 |
| 连接持有 | 215，P50/P95/max 9.558/60.895/645.735s | 79，P95/max 60.160/60.874s | 数量和极端长尾恶化 |
| 托管进程无匹配慢 SQL持有 | 82，P50/P95/max 10.352/61.034/92.486s | 20，P95/max 60.469/60.811s | +310%，网络/池等待证据 |
| PgBouncer query/connect timeout | 29 / 12 个去重事件 | 3 / 5 | 显著恶化 |
| HBA reject / server login failed | 498 / 498 | 1,035 / 1,035 | 次数下降但窗口前两小时仍持续 |
| statement/lock/deadlock/idle-in-tx | 0/0/0/0 | 0/0/0/0 | 当前日志未捕获，不等于不存在 |
| HTTP 请求 / 端点 | 7,284 / 161 | 3,460 / 126 | 覆盖量 +110.5% |
| HTTP 成功/失败 | 7,253 / 31，失败率 0.43% | 3,449 / 11，0.32% | 失败数及失败率恶化 |
| HTTP P50/P95/max/累计 | 87.51ms / 2.649s / 92.920s / 6,290.320s | 11.7ms / 2.181s / 60.976s / 2,224.921s | P95 +21.4%，累计 +182.7% |
| Celery completed/retry/failed | 600 / 131 / 33 | 358 / 31 / 7 | 工作量增加，通知重试/失败突出 |

PgBouncer 1,440 个 stats 样本中，wait P50/P95/最大为 26.50/83.11/1,063.17ms，query 为 61.15/204.98/1,143.87ms。应用日志的 123 次 `server closed`、HBA 拒绝和连接超时构成独立于 PostgreSQL executor 的证据链。HBA 拒绝在 08:09 后消失，巡检时数据库已经恢复可达，但连接等待与约 60 秒长尾仍反复出现。

## 页面、API、调用来源与 SQL 关联

按累计耗时、P95、最大耗时、失败率、受影响页面和用户影响综合排序：

| 页面/API | 请求 | 成功/失败 | P50 / P95 / max | 累计 | 匹配慢 SQL |
| --- | ---: | ---: | ---: | ---: | ---: |
| 研报音频状态 | 1,150 | 1,148/2 | 0.622 / 4.987 / 61.635s | 1,927.298s | 162 条 / 636.634s |
| 研报音频列表 | 381 | 381/0 | 0.416 / 2.683 / 92.920s | 459.254s | 36 条 / 223.188s |
| 研报视觉方案详情 | 189 | 186/3 | 0.388 / 7.939 / 61.950s | 398.436s | 约 101.706s |
| 平台凭据 | 473 | 472/1 | 0.345 / 1.899 / 60.855s | 375.353s | 约 58.297s |
| Bilibili 分集 | 152 | 150/2 | 0.493 / 3.108 / 60.403s | 281.063s | 部分匹配 |
| Celery 状态 | 17 | 17/0 | 2.132 / 46.345 / 84.789s | 277.848s | 仅 10.959s |
| MP3 B站上传 | 212 | - | - | 247.589s | 部分匹配 |
| 研报音频详情 | 79 | 77/2 | 1.108 / 10.032 / 62.147s | 239.530s | 部分匹配 |
| 竖版封皮下载 | 17 | - | P95 64.119 / max 81.682s | 225.190s | 24.948s |
| 证据图片 | 165 | - | - | 214.195s | 部分匹配 |

研报音频状态一个端点占全部 HTTP 累计时间的 30.6%，而页面会同时请求列表及多个状态；修改前自动刷新还会对每个活动任务再发详情请求。状态请求量是列表的 3.02 倍，符合前端高频轮询和 N+1 放大连接问题的模式。

关键 request id 时序关联：

- `0ffe23a2`：12:16:37 研报音频列表开始；首个列表 SQL 32.106 秒，后续子摘要因 `server closed` 约 60.226 秒，Session 持有 92.486 秒，HTTP 200 总耗时 92.920 秒。主因是连接/PgBouncer/网络长尾，页面 N+1 放大影响。
- `91d54bc8`：12:20:37 研报视觉方案详情；任务读取因 `server closed` 约 60.099 秒，连接持有 60.289 秒，HTTP 500 总耗时 61.950 秒。
- `db410355`：08:15:28 Celery 状态；数据库查询因 `server closed` 约 60.173 秒，而匹配慢 SQL 仅 10.959 秒，叠加 Celery inspect 后 HTTP 200 总耗时 84.789 秒，暴露控制面可用性观测缺口。
- `d93ea4eb`：11:34:02 研报音频状态；底层为多条较小查询，但响应总计 61.635 秒，优先归为连接持有/网络等待而不是 executor。

主要应用 SQL 指纹按累计耗时排序：研报音频 owned asset 82 条/308.779s；研报音频活动子任务 51 条/241.886s；简笔画场景生成 48 条/186.217s；研报音频列表 23 条/165.769s；简笔画进度写入 83 条/130.415s；Bilibili 子任务读取 31 条/108.849s；研报脚本子任务等待 66 条/108.664s；ZSXQ topic snapshot 24 条/97.986s；external document 查询 12 条/94.427s；视觉方案 owned task 14 条/86.624s。`publication_tasks` 相关指纹出现 664 次，是本轮最主要的数据库热点。

## 三类必须单列的异常

### 低频高延迟

- `POST /api/v1/videos/sync` 仅 1 次但耗时 64.039 秒，且没有匹配慢 SQL。
- 金融封皮再生成 3 次，最大 13.209 秒；视频统计 2 次，最大 10.751 秒。
- ZSXQ `topics/local` 11 次，P95/最大均 16.054 秒；虽然最大值较上轮 19.350 秒改善 17.0%，仍属于明显用户长尾。

### 页面/API 很慢但没有匹配慢 SQL

音频下载 44 次、最大 79.010 秒、累计 166.415 秒；微信上传 85 次、最大 12.348 秒；视频同步 64.039 秒；封皮下载 17 次、最大 25.789 秒；竖版封皮下载 17 次、最大 25.388 秒；视频下载 43 次、最大 6.219 秒。此外 MP3 创建、视觉方案创建和部分 retry/status 请求也无匹配慢 SQL。它们应优先检查外部 I/O、对象存储、Celery 控制面、缓存和获取连接等待，不能以“没有慢 SQL”误报为没有性能问题。

### 后台任务或连接持有很久但没有前台端点

Celery 完成 600 个任务。研报脚本 19 个累计 10,696.145 秒、最大 1,850.5 秒；ZSXQ sync 20 个累计 9,561.171 秒、P95 753.611 秒、最大 1,222.656 秒；PDF 清洗 5 个累计 3,367.693 秒；发布器 24 个累计 3,136.713 秒；ZSXQ 发布 10 个累计 2,784.054 秒；视觉方案 27 个累计 2,736.303 秒。通知任务 183 个成功、123 次 retry、31 次失败，主要是 SMTP 断连；Bilibili 2 次失败是 HTTP 403/凭据或风控。上述失败不能自动归因数据库 executor。

连接持有还包括 11 条 ad-hoc Session，其中最大 645.735 秒；它们已从应用排名隔离，但仍应由调用者自行治理。托管应用的 82 次无匹配慢 SQL长持有中，web 最大 92.486 秒，worker 最大约 61.911 秒，集中在 08:17-08:21、12:18-12:24 和 16:27 附近。

## 全业务覆盖与程序模式复核

动态统计覆盖 161 个实际出现的 HTTP 端点和 18 类 Celery 任务，不限于提示列出的页面。静态复核同时检查了前端轮询、后端分页/投影、任务状态查询和 ZSXQ 相关服务；未出现请求的页面不能宣称健康。

- 超大分页/过多行：前台分页通常有限制；后台候选扫描仍存在 500-5,000 行级上限，应继续以真实请求、行数和计划验证，不能仅凭阈值加索引。
- N+1：研报音频测试页自动刷新曾先取列表，再对每个活动任务逐一取详情，是本轮明确可修复的放大器，已经改为单次批量列表。评论、主题和附件主要读取路径仍使用投影列和批量 `IN`，未发现逐条评论查询的新证据。
- 大字段读取：`publication_tasks`、视觉方案和部分 worker 路径仍存在 payload 类字段物化；目标表的大 TOAST 体积和低 HOT 更新也说明宽行/JSON 更新成本。保留业务完整性和原始审计 JSON，不做删字段式“优化”。
- 重复去重/聚合：Insights canonical 查询会对 7,383 行做 `DISTINCT ON`/排序，但实测 31.684ms；当前不构成秒级主因。ZSXQ 快照/评论 upsert 的累计耗时更多受连接事件和批处理规模影响。
- 缓存：scheduler 配置慢指纹由上轮 32 条增至 47 条，60 秒进程内缓存仍减少了旧基线的读取，但多进程/任务入口会各自命中冷缓存；需要继续观察，不能把一次缓存修复视为根治所有来源。
- 频繁状态轮询：研报音频状态 1,150 次、累计 1,927.298 秒，是最清楚的页面放大器；本轮已降低频率、隐藏页停轮询并消除自动刷新 N+1。
- 外部 I/O 与 DB Session 交叉持有：音频/封皮/视频下载、Celery inspect、SMTP 和 Bilibili 外部请求存在长耗时而无等量慢 SQL；request id 时序显示部分请求在 DB 连接异常后继续等待外部控制面。应坚持外部 I/O 前结束事务/释放 Session 的生命周期纪律。

## ZSXQ / 生财有术特别链路

| 链路 | 请求 | P50 / P95 / max | 累计 | 慢 SQL |
| --- | ---: | ---: | ---: | ---: |
| `topics/local` | 11 | 2.047 / 16.054 / 16.054s | 32.966s | 3 条 / 18.260s |
| `sync/runs` | 4 | 2.147 / 2.910 / 2.910s | 8.482s | 3 条 / 5.077s |
| `system-sync-scheduler/status` | 3 | 3.104 / 4.041 / 4.041s | 8.020s | 2 条 / 3.334s |
| `groups/local` | 3 | - / - / 4.314s | - | 部分匹配 |
| `attachments/status` | 7 | - / - / 1.527s | - | 部分匹配 |
| `topics/local/tabs` | 4 | 1.030 / 1.585 / 1.585s | - | 0 |
| `system-sync-test/batches` | 3 | - / - / 1.929s | - | 部分匹配 |
| `config` | 3 | - / - / 1.216s | - | 0 |

`zsxq-insights overview` 本窗口仍没有实际请求，不能宣称健康；提示中的精确 `sync/status` 路径没有出现，实际出现的是 `system-sync-scheduler/status`。`topics/local` 峰值有所改善但 P95 仍为 16 秒。Insights 新索引已有真实扫描证据，详见下节。

## 重点表、索引与执行计划

实时只读采样显示 PostgreSQL 16.13、数据库 `show_ytb_data`、角色 `cappa_rw`，不在 recovery；角色 `statement_timeout=1min`、`lock_timeout=5s`、`idle_in_transaction_session_timeout=1min`。当前没有 waiting lock 或 idle in transaction；7 个 idle Session 均为 `ClientRead`/池化空闲，不能误报为事务泄漏。`pg_stat_statements` 未安装，因此本轮以结构化应用日志、PgBouncer stats、`pg_stat_user_*` 和定向 EXPLAIN 交叉验证。

| 表 | 表 / TOAST / 索引 / 总大小 | live / dead | seq / idx scan | HOT 更新率 | 判断 |
| --- | --- | ---: | ---: | ---: | --- |
| `zsxq_topic_comments` | 91MB / 4.2MB / 23MB / 118MB | 40,620 / 7,769 | 143 / 205,171 | 60.05% | dead 较多，关注 vacuum；读路径索引命中高 |
| `zsxq_topics` | 13MB / 72MB / 29MB / 114MB | 11,629 / 0 | 2,055 / 617,475 | 1.10% | TOAST 大、HOT 低，宽 JSON 写放大 |
| `publication_tasks` | 17MB / 80MB / 10MB / 107MB | 10,170 / 1,162 | 2,475 / 2,418,269 | 1.54% | 本轮最高频热点，payload/状态更新写放大 |
| `zsxq_attachments` | 28MB / 40kB / 35MB / 63MB | 29,487 / 3,255 | 426 / 904,586 | 0.13% | 索引多且 HOT 极低，优先治理写放大 |
| `zsxq_sync_runs` | 1.0MB / 29MB / 832kB / 31MB | 774 / 113 | 173 / 305,051 | 0.38% | JSON/状态频繁更新，表小无需为 seq scan 紧张 |
| `external_documents` | 824kB / 4.8MB / 1.2MB / 6.9MB | 1,696 / 45 | 334 / 62,598 | 0% | 表小，低 HOT；单次 60 秒不是 executor 证据 |

六表索引扫描占比约 99.47%-99.95%，未发现定义完全重复的索引。低使用候选包括附件 group-tab PDF 索引 4MB/scan=1、topic group-type 3.1MB/scan=1、group-created 2.4MB/scan=10，以及附件 publish-status、task、source 和 external document TTS/summary 等索引；但统计从 2026-07-17 服务启动累计，功能也可能低频，不能凭低 scan 直接删除。

真实 `EXPLAIN (ANALYZE, BUFFERS)`：watchdog 主键查找 0.032ms、channel chart 1.353ms、scheduler config 0.034ms、publisher JSON feature 列表 2.882ms、content pipeline JSON 列表 2.937ms、recent task 含 JSON 0.046ms/投影后 0.024ms、sync runs 全表扫描 0.892ms。秒级/60 秒慢日志与这些 executor 计划不一致，进一步支持连接边界、Session 持有和应用放大是主因。

2026-08-04 人工创建的 `ix_zsxq_topics_group_topic_preferred_synced_desc` 当前 valid/ready/live，大小 1,072kB，`idx_scan=784`、读取 8,049 tuple、回表 8,038 tuple，最近扫描 2026-08-05 04:28:15；相比创建后首次检查的 `idx_scan=0` 已有明确真实使用，建议保留。完整 group canonical 查询仍选择 `Seq Scan + Sort`，7,383 行执行 31.684ms，说明该索引服务于其他真实查询形态，不能强行让全量查询走索引，也无需再加一枚相似索引。

## 反复问题与根因

- HBA/出口 IP：2026-07-27 和 2026-08-04 均出现动态出口不在 allowlist；本窗口前两小时仍有 498 次拒绝。根因是网络身份不稳定，不是 SQL 或 Session 清理。
- 约 60 秒连接长尾：PgBouncer query/connect timeout、应用 `server closed`、托管 Session 长持有与 HTTP request id 同时出现；本轮数量恶化，属于未根治的连接边界问题。
- `publication_tasks` 大字段轮询/写放大：表 TOAST 80MB、HOT 1.54%，研报音频/视觉/简笔画的读写指纹再次占据累计耗时前列。根因是高频状态更新、宽 payload 和页面轮询共同放大。
- 页面轮询：recurrence registry 中的 hot-path 轮询问题再次由研报音频状态端点暴露；本轮已修可见性与 N+1，但仍需下一轮用端点请求量验收。
- scheduler 配置读取：慢指纹从 32 次回升到 47 次，说明进程内缓存无法跨 worker/scheduler 共享；当前 executor 0.034ms，不应继续以索引处理。
- 生命周期纪律：现有代码和历史 Bug 记录已要求 commit/rollback/close，当前无 idle-in-transaction；但外部 I/O 与 DB Session 交叉持有仍有请求级证据，应继续用 request id 和 Session hold 监控，而不是把“finally 有 rollback”当作充分证明。

## 已落地修复

- 研报音频测试页：默认轮询 8 秒调整为 15 秒，最小值 1 秒调整为 5 秒；隐藏标签页停止轮询，可见时恢复，离开页面清理 timer。
- 研报音频详情页：自动刷新由 6 秒调整为 10 秒，同样在隐藏标签页暂停。
- 消除研报音频自动刷新 N+1：不再对每个活动任务并发请求详情后再请求列表，自动刷新只调用批量 `loadHistory`；创建任务后的即时单任务刷新仍保留。
- 为两个 HTML 更新静态资源 cache-bust，用户刷新页面即可获取新逻辑。
- 巡检器新增最坏 HTTP request id/起始时间和托管无匹配慢 SQL Session 样本，后续可直接按来源关联 60 秒长尾。
- DB 诊断器新增 Insights group 参数、索引实时使用统计和 canonical `EXPLAIN (ANALYZE, BUFFERS)`，防止只看索引存在性或应用耗时下结论。
- 新增 Bug 记录、README 和 recurrence registry hot-path 条目。本轮未删除原始 JSON、未裁剪业务字段、未执行生产 DDL。

## 验证结果

- 定向测试：110 passed，另含 3 个 subtests；1 个既有失败为前端 cache-bust 断言仍期待 `lightweight-status-poll-v2`，页面当前是 `sticky-sidebar-status-v3`，与本轮修改无关。
- 完整 unit：1,019 passed、3 skipped、53 subtests；同一既有 cache-bust 断言失败，无新增失败。
- 两个修改的前端 JS 均通过 `node --check`；两个诊断脚本通过 `py_compile`。
- `compileall src/backend scripts tests`、`git diff --check`、recurrence registry JSON 解析均通过。
- Bug 记录严格检查 141/141 通过，9 个 recurrence 根因簇全部归类。
- 本轮没有需要加载的后端/worker/scheduler 代码，因此无需重启，也未运行重启脚本；静态资源刷新即可生效。

## 索引整改建议、待人工 SQL、风险与下一步

1. 保留 Insights 新索引：784 次扫描证明它已被真实查询使用；canonical 全量查询 31.684ms 尚不值得为其追加重叠索引。下一轮继续记录 `idx_scan` 增量和 overview 的实际计划。
2. 不新增索引：六张重点表索引扫描率已经很高，秒级长尾缺乏 executor 证据。优先降低 `publication_tasks`/附件/同步表的更新频率和宽字段物化，提升 HOT，而不是增加写放大。
3. 低价值索引只列候选，不在本轮执行 DROP。需要至少跨一个完整业务周期核对功能 owner、读写比、`idx_scan` 增量和 DBeaver 验证后再决定。`zsxq_attachment_file_index_cleanup_dbeaver_20260801.sql` 的目标索引已不存在；`comment_hash_index_cleanup_dbeaver_20260724.sql` 曾有 8 条 hash 不一致且残留索引仍有真实扫描，禁止原样重跑；Insights 创建脚本已生效，不要重复执行。
4. 本轮没有新增待人工 SQL。任何后续 CREATE/DROP INDEX、ALTER TABLE、回填或生产配置仍必须在 DBeaver 安全窗口逐条执行，设置 `lock_timeout`/`statement_timeout`，先验证计划和锁，再准备回滚；不要把旧脚本当作幂等脚本重复运行。
5. P0 网络项：确认 PgBouncer/PostgreSQL 使用稳定出口或最小范围固定 allowlist，并对 query/connect timeout 和 server closed 建报警。HBA 已恢复可达，但动态出口根因尚未消除。
6. 下一轮验收修复效果：重点比较研报音频状态/列表请求比、请求量、累计耗时和 request id 下的 SQL 数；预期隐藏页请求归零，自动刷新每轮只保留一次列表请求。
7. 继续拆分“数据库连接”“Celery inspect”“对象存储/下载”“SMTP/Bilibili”阶段耗时，避免 Celery status、下载和上传类端点在没有慢 SQL时成为盲区。若未来确需重启，必须先运行 `scripts/dev/check-restart-safety.py`，仅在无不可中断任务时执行 `scripts/dev/restart-dev-stack.sh`。
