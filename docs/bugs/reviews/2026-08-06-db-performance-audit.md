# 2026-08-06 数据库性能与错误巡检

## 结论

固定窗口为 2026-08-05 06:02:19 至 2026-08-06 06:02:19（Asia/Shanghai），覆盖
`logs/slow_query.log`、`logs/app.log`、`logs/backend.log`、`logs/scheduler.log`、
`logs/celery/*.log`、`logs/pgbouncer.log` 及与窗口重叠的文本/gzip 轮转。应用慢 SQL
4,978 条，P50/P95/最大 2.046/6.756/60.772 秒，累计 14,245.447 秒；HTTP 4,431 次、
147 个端点，成功/失败 4,406/25，P50/P95/最大 9.47ms/8.900s/82.233s，累计
6,381.035 秒。

本轮最重要的结论仍是：不能为 2–60 秒应用耗时盲目增加索引。实时只读
`EXPLAIN (ANALYZE, BUFFERS)` 的代表查询为 0.025–26.356ms；目标表索引扫描占比
99.51%–99.95%，没有等待锁和精确定义重复索引。与此同时，PgBouncer 有 38 个去重
query timeout、4 个 connect timeout，托管进程有 703 次无匹配慢 SQL 的长 Session，
门禁捕获 12 次 idle-in-transaction 压力延迟。主因是连接/PgBouncer/公网网络与高并发
数据库往返的乘数效应，叠加部分 Session 生命周期复发，而不是 PostgreSQL executor
或缺索引。

已安全落地三项修复：巡检器汇合 idle-in-transaction 门禁证据；实时诊断强制只读并改用
真实 `feature` 投影 SQL；播客视觉化历史列表从“读取 1,000 行含 JSON 后 Python 切页”改成
数据库 count + 当前页 `OFFSET/LIMIT`。未执行 DDL、数据回填、生产配置、索引增删、原始
JSON 删除或破坏性操作。

## 与上一轮基线对比

| 指标 | 本轮 | 上轮 | 判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 4,978 | 1,363 | +265.2%，显著恶化 |
| P50 / P95 / 最大 | 2.046 / 6.756 / 60.772s | 1.502 / 9.262 / 60.844s | P50 +36.2%，P95 -27.1%，60s 尾部仍在 |
| 应用慢 SQL 累计 | 14,245.447s | 4,359.842s | +226.7%，恶化 |
| ad-hoc 诊断慢 SQL | 319 / 1,130.544s | 272 / 未单列累计 | 已与应用排名隔离 |
| Session 持有 | 1,027，P95/max 33.461/1,313.982s | 215，60.895/645.735s | 数量 +377.7%；最大来自 ad-hoc |
| 托管无匹配慢 SQL 持有 | 703，P95/max 33.402/77.860s | 82，61.034/92.486s | 数量 +757.3%，单次长尾改善 |
| PgBouncer query/connect timeout | 38 / 4 个去重事件 | 29 / 12 | query +31.0%，connect -66.7% |
| PgBouncer wait P95 / query P95 | 166.49 / 517.85ms | 83.11 / 204.98ms | 分别约 +100% / +153% |
| HBA / server login failed | 0 / 0 | 498 / 498 | 出口白名单故障本窗口未复发 |
| HTTP 请求 / 端点 | 4,431 / 147 | 7,284 / 161 | 请求 -39.2% |
| HTTP 成功/失败 | 4,406 / 25，失败率 0.56% | 7,253 / 31，0.43% | 失败数下降，失败率恶化 |
| HTTP P50/P95/max/累计 | 9.47ms / 8.900s / 82.233s / 6,381.035s | 87.51ms / 2.649s / 92.920s / 6,290.320s | 中位改善，P95 +236%，累计 +1.4% |
| Celery completed/retry/failed | 889 / 306 / 176 | 600 / 131 / 33 | 工作量与失败风暴均恶化 |

上一轮轮询修复有明确收益：研报音频状态从 1,150 次降到 166 次（-85.6%），累计耗时从
1,927.298s 降到 1,610.202s（-16.5%）。但单次 P50/P95 反而从 0.622/4.987s 恶化到
9.480/22.842s，说明请求乘数已经下降，剩余连接/网络等待成为主导。列表请求从 381 次降到
301 次，但累计耗时从 459.254s 升到 740.865s，也支持这一判断。

## 页面/API、request id 与 SQL/连接关联

按累计耗时、P95、最大值、失败率和用户影响综合排序：

| 页面/API | 请求 | 成功/失败 | P50 / P95 / max | 累计 | 匹配慢 SQL |
| --- | ---: | ---: | ---: | ---: | ---: |
| 研报音频状态 | 166 | 164/2 | 9.480 / 22.842 / 60.669s | 1,610.202s | 374 / 904.654s |
| 研报音频列表 | 301 | 301/0 | 0.355 / 7.709 / 61.238s | 740.865s | 100 / 290.408s |
| 平台凭据 | 334 | 325/9 | 0.416 / 10.019 / 16.519s | 497.264s | 42 / 107.394s |
| Celery 状态 | 84 | 84/0 | 1.797 / 23.817 / 82.233s | 384.028s | 22 / 66.107s |
| PDF 证据图片 | 121 | 121/0 | 1.276 / 11.920 / 15.757s | 365.791s | 38 / 82.055s |
| 研报音频详情 | 37 | 37/0 | 2.051 / 22.062 / 24.824s | 225.917s | 41 / 116.252s |
| 研报音频抖音上传 | 59 | 56/3 | 0.549 / 11.531 / 36.729s | 183.198s | 12 / 39.002s |
| 研报音频视频号上传 | 59 | 56/3 | 0.550 / 12.670 / 19.515s | 159.745s | 14 / 40.193s |
| Bilibili 分集 | 113 | 112/1 | 0.517 / 4.264 / 28.807s | 156.668s | 9 / 28.739s |
| MP3 B站上传状态 | 136 | 136/0 | 0.946 / 2.108 / 11.006s | 151.967s | 11 / 31.564s |
| 研报音频发起 B站上传 | 14 | 14/0 | 2.678 / 30.120 / 43.223s | 135.163s | 12 / 31.056s |
| 音频下载 | 26 | 26/0 | 0.006 / 30.098 / 37.014s | 116.233s | 3 / 16.256s |
| MP3 详情 | 57 | 57/0 | 0.517 / 3.376 / 33.138s | 105.839s | 13 / 75.795s |
| MP3 列表 | 195 | 195/0 | 0.193 / 1.741 / 16.747s | 99.824s | 10 / 49.312s |
| B站充电看板 | 3 | 2/1 | 32.315 / 61.988 / 61.988s | 96.237s | 3 / 33.702s |

关键时序：

- `b73b9153`：16:02:27 研报音频状态开始；`_live_child_tasks` 因 `server closed`
  持续 60.209s，HTTP 500 总计 60.669s；同分钟 PgBouncer query timeout。SQL 是轻量投影，
  不能归因缺索引。
- `18b805cd`：16:08:23 研报音频列表开始；先执行 0.702s 的 timeout 设置，随后
  `_child_summaries_for_parent_ids` 因 `server closed` 持续 60.224s；HTTP 仍返回 200，
  总计 61.238s，暴露 stale/fallback 成功状态掩盖可用性退化。
- `24540995`：08:29:06 Celery 状态开始；业务任务快照 SQL 1.165s，Celery control/连接等待
  把端点拖到 82.233s并返回 200；同一时段多个 PgBouncer query timeout。
- `9fde20b9`：12:39:44 发起方案三竖版视频；多次点查/宽任务读取累计 18.643s，端点最终
  502/68.214s。它同时包含多轮应用 SQL 和外部/连接等待，属于往返放大，不是单条 executor
  慢查询。
- `b447056b`、`491c8ba5`、`c35ff825`：08:29–08:31 的充电看板、数据汇总、数据表请求
  分别 61.988/61.858/61.003s；匹配 SQL 只覆盖 33.702/1.204/1.282s，剩余长尾与同秒
  PgBouncer timeout/Session 持有一致。

## SQL 指纹与根因分层

按累计耗时最高的应用 SQL 指纹：

| origin | 次数 | P95 / max | 累计 |
| --- | ---: | ---: | ---: |
| 简笔画图片进度写入 `_update_progress` | 387 | 4.891 / 11.472s | 874.985s |
| 研报音频父任务窄更新 `_execute_parent_update` | 111 | 7.567 / 11.406s | 370.140s |
| ZSXQ 主题增量更新 `_update_existing_topic` | 104 | 6.422 / 9.422s | 304.800s |
| ZSXQ 主题批量快照 `_load_existing_topic_snapshots` | 38 | 30.900 / 60.283s | 289.767s |
| 研报音频 owned asset | 73 | 7.476 / 11.492s | 277.673s |
| runtime guard 活跃任务计数 | 93 | 6.278 / 7.860s | 274.897s |
| PDF 证据映射 | 68 | 15.174 / 59.424s | 271.764s |
| 研报音频活动子任务 | 79 | 7.702 / 25.900s | 270.844s |
| ZSXQ 调度配置 `_db_config_rows` | 107 | 5.200 / 8.624s | 265.589s |
| 研报音频父任务另一更新形态 | 101 | 5.581 / 12.530s | 265.278s |

这组数据说明“查询/更新次数 × 公网往返”是累计时间核心。`publication_tasks` 相关慢 SQL
2,584 条，占应用慢 SQL 51.9%；但实时主键含 result 读取 0.036ms、Publisher feature 列表
0.072ms、Content Pipeline feature 列表 1.532ms。简笔画进度 387 次属于可继续治理的状态写
放大，当前未直接改变其进度语义；下一步应在有真实进度事件预算测试后做 coalescing，而不是
加索引。

应用 SQL 错误 18 条/11 指纹，均为 `OperationalError` 主导；日志有 40 个去重
`server closed`。statement timeout、lock timeout、deadlock 的异常文本均为 0；这只代表
当前日志未捕获对应异常，不代表数据库永远不会发生。PgBouncer 自己记录 38 个 query timeout
和 4 个 connect timeout，不能被应用事件计数 0 覆盖。

## 三类独立异常

### 低频高延迟

- 方案三竖版生成 3 次，1 次失败，P95/max 68.214s。
- B站充电看板 3 次，1 次失败，P50 32.315s、最大 61.988s。
- 数据汇总/数据表各 3 次，均 1 次失败，最大 61.858/61.003s；匹配 SQL 仅约 1.2s。
- 频道列表 3 次全成功，最大 37.298s，其中匹配 SQL 35.553s；真实频道图表 executor
  1.568ms，仍更像连接往返而不是 executor。
- 火柴人视频详情 1 次 10.803s，匹配 SQL 8.325s。

### 页面/API 很慢但没有匹配慢 SQL

- 研报音频封皮下载 10 次，P95/max 15.231s，累计 38.412s。
- MP3 视频号/抖音上传状态各 43 次，累计 29.711/26.165s。
- 竖版封皮下载 8 次，最大 13.946s；横版关键帧封皮 7 次，最大 11.017s。
- 研报音频 retry 6 次，最大 7.158s；播客封皮和封皮重生成各 1 次，约 5.3/4.8s。

这些路径优先检查文件 I/O、对象存储、外部平台、缓存与连接获取；不能因为没有匹配慢 SQL
就报告“没有问题”。

### 后台任务或连接持有很久但没有前台端点

- ZSXQ sync 22 个成功，P50/P95/max 588.4/1,141.8/1,806.8s，累计 10,915.703s。
- 研报音频 237 个成功、257 retry，累计 9,249.766s。
- Publisher 38 个成功、4 retry、162 个唯一 Celery failure ID，累计成功耗时 4,486.291s。失败风暴主要是
  “方案三需要已完成的正文分镜图片或兼容视觉视频”，属于编排前置条件反复投递，复发自
  `2026-07-29-report-audio-scheme3-preflight-blocked-landscape.md`，不能归因数据库。
- 视觉方案 24 个成功，累计 4,302.892s；PDF 清洗 4 个成功，累计 2,523.293s。
- Bilibili only 22 个成功、4 失败，失败为分片连续失败；通知 252 成功、31 retry、9 失败，
  失败为 SMTP 断连；均与 PostgreSQL executor 无关。
- 托管进程 703 次无匹配慢 SQL Session，web 最大 77.860s、worker 最大 61.591s。

## idle-in-transaction 与 Session 生命周期复发

异常文本计数为 0，但 runtime guard 在 14:17–15:26 记录 12 次
`db_idle_in_transaction_pressure` defer，影响 10 个任务；retry 证据中的 idle 数为
6/8/6/7，最大 8。另有一次允许准入快照记录 idle=5。该时间段同时出现大量 5–30s Web
Session 持有与 PgBouncer timeout，说明历史复发键 `DB-SESSION-LIFECYCLE` 仍未达到
accepted。

当前 06:03 的实时只读快照没有 waiting lock，也没有 idle-in-transaction；活动摘要只有
7 条 `idle / ClientRead`。其中两个 gunicorn 服务端连接空闲 17 天、五个 PgBouncer/公网
连接空闲 0–136 分钟，它们不是事务内空闲，不能误杀或误报。本轮无法从事后聚合日志确定
14:17–15:26 的具体 offender；已修复巡检漏报，下一次复发应采集 `application_name`、事务
年龄、`state_change` 和最后 SQL origin。

## 全业务程序模式复核

- 超大分页/一次读取过多行：动态端点 147 个；主列表 per-page 上限大多为 50/100。
  静态扫描发现 `/podcast-visualizer/tasks` 读取最多 1,000 行含 `params/result` 后切页，已改为
  DB count + 当前页 `OFFSET/LIMIT`。ZSXQ 历史兼容回退仍有 500 行 params 扫描，但仅在
  `source_task_id` 投影无法命中时执行，暂不在无真实命中证据下删除兼容性。
- N+1/频繁轮询：上一轮研报音频隐藏页与逐任务详情 N+1 修复已用请求量 -85.6% 验收；
  剩余状态请求单次变慢源自连接层。方案三单请求仍有多轮任务点查，`9fde20b9` 证明一次操作
  能产生多条 1–6s 往返，需后续以阶段快照合并。
- 大字段：`publication_tasks` TOAST 85MB、HOT 1.46%；方案三 `_get_owned_task` 和
  `_latest_matching_final_video_child` 仍物化 `params/result`。原始 JSON 和业务字段全部保留，
  本轮只减少无必要的历史列表物化。
- 重复去重/聚合：Insights canonical 对 7,492 行 Seq Scan + Sort，26.356ms；小表
  `system_configs` Seq Scan 0.025ms，均不值得额外索引。频道小表 63 行 Seq Scan 也是合理计划。
- 缓存：ZSXQ scheduler 配置慢指纹 107 条，比上轮 47 条恶化；60 秒进程内缓存无法跨多个
  worker/scheduler 共享，公网往返变慢后冷命中被放大。当前不在无人值守巡检中引入 Redis
  配置缓存或生产配置变更。
- 外部 I/O 与 Session：703 次无匹配慢 SQL 持有、Celery control 82s、文件下载 10–37s
  均需要继续执行“短事务快照 → 释放 → 外部 I/O → 按 ID 窄写回”纪律。

## ZSXQ / 生财有术重点链路

本窗口没有实际捕获 `/api/v1/zsxq-insights/overview`、`topics/local`、
`topics/local/tabs` 或名为 `sync/status` 的请求，因此不能宣称这些端点健康或改善。实际出现的
ZSXQ 控制链路包括：system scheduler status 11 次、P50/P95/max 1.604/4.245/4.245s；
system test config 12 次、最大 3.633s；system test batches 12 次、最大 9.097s；auto-publish
scheduler status 10 次、最大 1.916s。后台 ZSXQ sync 22 个全部成功，但累计 10,915.703s，
主题更新/快照、评论 upsert 和附件 upsert 均进入慢 SQL 排名。

## 表规模、扫描、写放大与索引证据

统计自 PostgreSQL 2026-07-17 启动后累计：

| 表 | 表 / TOAST / 索引 / 总大小 | live / dead | seq / idx scan | 索引扫描占比 | HOT |
| --- | --- | ---: | ---: | ---: | ---: |
| `zsxq_topic_comments` | 91MB / 4.2MB / 23MB / 118MB | 36,733 / 1,580 | 155 / 220,976 | 99.93% | 61.90% |
| `zsxq_topics` | 13MB / 72MB / 30MB / 115MB | 11,763 / 386 | 2,103 / 654,563 | 99.68% | 1.03% |
| `publication_tasks` | 17MB / 85MB / 11MB / 113MB | 11,255 / 1,214 | 2,479 / 2,470,250 | 99.90% | 1.46% |
| `zsxq_attachments` | 29MB / 40kB / 36MB / 65MB | 30,141 / 5,087 | 445 / 925,572 | 99.95% | 0.13% |
| `zsxq_sync_runs` | 1.0MB / 29MB / 832kB / 31MB | 633 / 20 | 176 / 313,322 | 99.94% | 0.35% |
| `external_documents` | 824kB / 4.8MB / 1.2MB / 6.9MB | 1,714 / 131 | 335 / 68,507 | 99.51% | 0% |

相比上一轮，`publication_tasks` 总大小从 107MB 增至 113MB，TOAST 80→85MB；topics
114→115MB；attachments 63→65MB。低 HOT 与宽 JSON/多索引更新继续造成写放大。当天
autovacuum/autoanalyze 已运行，dead tuple 尚未显示失控。

Insights 索引 `ix_zsxq_topics_group_topic_preferred_synced_desc` valid/ready/live，大小 1,136kB，
`idx_scan=917`，较上轮 784 增加 133 次，最近扫描 2026-08-06 03:46:38，继续保留。全量
canonical 查询仍合理选择 Seq Scan + Sort，26.356ms；不强迫使用该索引。

无精确定义重复索引。低使用候选仍包括：附件 group-tab PDF 4.1MB/scan=1、topic
group-type 3.1MB/scan=1、topic group-created 2.4MB/scan=10、附件 publish-status/task/source、
external TTS/summary、publication waiting-label/publisher-mp3、sync tab-id 等。统计窗口仅约 20 天，
这些功能可能低频；本轮不生成新的 DROP 脚本，也不重复执行旧清理脚本。

## 已落地修复

1. 日志分析器新增 idle-in-transaction 门禁证据汇总，同一窗口现可输出 12 defer、10 任务、
   12 个数值观测、最大 8；保留异常文本 0 的独立口径。
2. DB 瓶颈脚本强制 `SET TRANSACTION READ ONLY`，statement timeout 改为事务局部；实际回显
   `transaction_read_only=on`。
3. 代表 SQL 从旧 JSON feature 条件改成真实投影列；Publisher 计划命中
   `ix_publication_tasks_feature_created_desc_v2`，0.072ms。
4. 播客视觉化任务列表改为服务端分页，只物化当前页 `params/result`，不再读取 1,000 行后切片；
   保持返回字段、每页上限和越界页回落语义。
5. 新增 Bug 记录并把复发重新登记到 `DB-SESSION-LIFECYCLE` 与回归验收簇。
6. 完整测试暴露视觉详情页缓存键只覆盖旧 sticky-sidebar 版本；已把既有
   `sticky-sidebar-status-v3` 与 `lightweight-status-poll-v2` 合并进同一个版本键，避免浏览器
   缓存继续运行旧的高频状态轮询脚本。

## 索引整改建议、待人工 SQL 与风险

- 本轮没有证据支持 CREATE INDEX；也没有满足跨周期 owner/使用量/真实 SQL 验证条件的 DROP。
- 不生成新的 DBeaver DDL 脚本。`zsxq_attachment_file_index_cleanup_dbeaver_20260801.sql` 的目标
  已不存在；评论 hash 清理仍有历史 8 条不一致证据，禁止原样重跑；Insights 创建脚本已经生效，
  不要重复执行。
- 当前优先级是连接稳定性、idle offender 归因、方案三失败风暴和减少进度/状态往返。索引增删只会
  增加 `publication_tasks`、topics、attachments 的写放大，不能解决本轮 60s PgBouncer timeout。

## 验证结果

- 定向：DB 分析器、只读/真实 SQL 合同、播客分页合同共 15 tests 通过。
- 实时只读 EXPLAIN：Publisher 0.072ms、Content Pipeline 1.532ms、Insights 26.356ms；无等待锁。
- 完整 unit：`PYTHONPATH=src/backend ... unittest discover` 共 1,016 tests，全部通过；相关视觉
  详情页两个测试模块 96 tests 通过。
- `compileall`、`git diff --check`、recurrence registry JSON 校验和 Bug 记录严格检查全部通过；
  142 条 Bug 均已归类，最近正式回顾为 2026-08-06。
- 重启安全门禁只读检查通过：47 个活动 publication task 中无不可中断任务，ZSXQ sync 为 0。
  随后本地开发栈重启在第 0/8 步因应用 Redis、Celery broker/result backend 均不可达而被预检
  拒绝，未执行停服/重启；代码已保留，需 Redis 恢复后的安全窗口重新执行门禁与重启。

## 下一步

1. 下一窗口优先验收 PgBouncer query timeout、托管无匹配慢 SQL Session 和
   idle-in-transaction defer 是否下降；复发时记录 offender 身份与事务年龄。
2. 对简笔画进度 387 次/874.985s 建立每场景进度写预算，再做不丢终态的 coalescing。
3. 查明 162 次方案三 Publisher 失败执行为何被反复投递；这是编排复发，不是 DB executor。
4. 继续验收研报音频轮询请求量，同时把方案三单请求多轮点查合并为有界阶段快照。
5. `overview/topics/local/tabs/sync/status` 下一次出现真实请求后再更新基线，未出现不能报健康。
6. 恢复本地 Redis 连通后重新运行重启安全门禁，再刷新开发栈并对播客视觉化列表做一次运行态
   分页冒烟；当前不能把“代码测试通过”误报成“运行实例已加载”。

## 08:31–08:53 根因治理续跑

用户追问“是否修了根因”后，本轮没有继续按慢 SQL 次数堆索引，而是补齐 checkout 到 checkin
的 request/Celery/origin 与 SQL/non-SQL 时间，再以新证据修复三条具体生命周期根因。

### 新证据与根因

1. 火柴人图片的最高累计指纹（387 次、874.985 秒）不是缺索引。图片代码在 commit 后读取过期
   `scheme_output.image_gemini_url`，触发新 checkout 后进入 Gemini 浏览器生成；进度回调还逐事件
   commit。现已 commit 前冻结 URL，证据渲染改为不可变 DTO，进度写按 20 点增量合并并保留 95+
   终态，每次写后结束 Session。
2. 08:31 起 5 分钟增量窗口中，MP3 B站状态 23 次累计 157.127 秒、P50 6.302 秒；四个图片
   下载同秒并发、单个约 8.496 秒但无匹配慢 SQL。根因是下载缓存全局锁等待携带鉴权 Session。
   现已在锁前快照权限并释放 Session，查询后再次释放，缓存只保存深拷贝 JSON；前端轮询 5→15 秒，
   增加 in-flight 单飞和隐藏页暂停。
3. 第一次重启后的 08:47:42–08:49:17 新进程窗口只有 1 条应用慢 SQL，但新归因直接记录
   `backend-process-bootstrap` 持有 13,544.87ms：SQL 9 条/487.47ms，non-SQL 13,057.40ms，
   origin 为 `has_active_credential`。根因是一次 config status 重复三次凭据查询，成功路径不清
   Session，随后跨进程检查/启动 I/O。现已合并为一次凭据快照，并在 finally 释放只读 Session。

### 运行态与验证

- 第一轮重启门禁确认 47 个活动 publication task 中无不可中断任务、ZSXQ sync 为 0；完整重启
  成功，Redis/PgBouncer 预检通过，新后端 PID 37909，健康端点返回 `healthy`，28 个后端/worker/
  watchdog/scheduler 进程已核对。图片、MP3 下载、轮询和新连接归因已加载。
- 新归因加载后，连接长持有日志已包含 request/task/thread/origin、`sql_count`、`sql_total_ms`、
  `sql_max_ms` 和 `non_sql_hold_ms`；分析器可按 origin 与 non-SQL 累计排名。
- 第三项修复完成后再次跑完整 unit：1,028 tests 全部通过；`compileall`、JavaScript 语法、registry
  JSON、145 条 Bug 严格归类和 `git diff --check` 均通过。
- 第二次门禁发现不可中断 ZSXQ sync run `47e696f1-79db-43ed-b1d9-7deabddb27ab` 正在
  `sys4_workflow_005017`，因此拒绝重启且未使用强制覆盖。第三项代码待该同步结束后的安全窗口加载。

### 索引与风险边界

续跑仍没有新 CREATE/DROP INDEX 证据，也未执行 DDL、数据回填、配置变更或删除原始 JSON。
启动预检继续提示历史评论 hash 索引维护尚未登记且两个等价旧索引仍在；该事项保持人工 DBeaver
脚本和既有验证/回滚纪律，本轮没有趁重启自动执行。

### 当前任务状态

代码级根因已继续修复并通过定向回归；新 PID `47594` 已加载 bootstrap Session 与 ZSXQ
页级批量写路径，DB-SESSION 的 bootstrap/页主题分支已进入 `runtime_replaced`。但附件状态批量写和
新主题页边界 flush 尚未运行态加载，且当前 PgBouncer 被 PostgreSQL HBA 拒绝阻断；下一动作是先恢复
稳定数据库白名单，再通过门禁重启，最后用真实附件/已有主题轮次完成端到端对比。

### 08:57 Publisher 失败风暴对账

- 162 不是重复日志：render 日志中是 162 个不同 Celery task ID；只读数据库以这些 ID 对账，
  162 个全部匹配失败 Publisher，错误均为“方案三需要已完成的正文分镜图片或兼容视觉视频”，
  且只来自 5 个研报音频父任务。
- 三个历史失败样本的 `vertical_video.scheme=scheme3`，但 scene count=0、无 visual video path、
  无 visual workflow id。旧路径先建子任务、进入封面/合成后才失败，父任务续跑再建一个，构成确定性
  失败创建风暴和数据库状态写放大。
- 当前代码已经把方案三解析提前到 `_create_publisher_video_task`：同 PDF 视觉工作流未就绪时抛
  `ChildTaskStillRunning(scheme3_visual)`，父任务 defer 且不创建 Publisher；就绪后显式传递 scene
  timeline 或兼容 video path。对应时间线传递与未就绪 defer 合同测试均通过，研报音频模块 54 tests
  通过；该代码已随 08:47 的新 report_audio worker 加载。
- 新 worker 后尚未再出现该错误，但也没有新的对应方案三任务，因此只记录为“运行代码已替换、待真实
  正向任务验收”，不把短窗口零事件误报成故障已经定量消失。

### 09:08 安全门禁复核

- 前一批 ZSXQ 同步与 Bilibili 上传已成功结束，但门禁复跑时系统已经启动新的 ZSXQ 同步
  `4a1414c6-60e4-4fbe-b61f-0fc167dcfef7` 和发布任务
  `420565d5-3937-4da4-892c-ffaa237de581`；门禁再次拒绝重启，未强制中断业务。
- 门禁查询 `publication_tasks WHERE status='processing' ORDER BY updated_at, created_at LIMIT 100`
  本次应用观测为 4,758.29ms。历史同一指纹的只读 `EXPLAIN (ANALYZE, BUFFERS)` 已命中
  `idx_publication_tasks_status`，PostgreSQL executor 仅 0.040ms；结合本窗口 PgBouncer wait/query
  分位数升高，继续归因连接排队而非缺索引，不新增索引。
- 因新任务连续接力，bootstrap Session 修复仍只处于 `code_changed`；必须等真正安全窗口加载后，
  再以新进程 `backend-process-bootstrap / has_active_credential` 的 checkout-checkin 日志验收。
- 09:08:26–09:18:33 的 Bilibili 分片上传耗时 608.57 秒并最终成功；同一新进程窗口的
  `platform_upload.log` 没有新的长连接持有记录，说明该低频高延迟属于外部上传/分片重试，
  没有跨数据库 Session，不能计入 PostgreSQL executor 或作为加索引依据。
- ZSXQ 同步实测出现 47 条附件快照的批量差异更新，SQL 影响 0 行但耗时 2,056.51ms；现有
  `IS DISTINCT FROM` 已避免实际写入，却仍需把 47 份完整 `raw_payload` 送往远端 PostgreSQL
  比较。若要消除这类零写入大字段往返，需要为附件增加 payload hash、回填并改为 hash 判定，
  涉及 `ALTER TABLE`/回填和兼容部署，留作人工 DBeaver 方案，不在本轮自动执行，也不删除原始 JSON。

### 09:31–09:36 运行态安全窗口复核

- 只读进度快照显示该全 tabs run 仍在正常推进：`status=running`、心跳持续更新，
  当前 tab 从“生财朋友圈”切换到“超级标”，分页为 1/2、目标 40 条；不能按“长时间 processing”
  误判为卡死，也不能为了重启强制撤销。
- 09:34–09:35 同步写入 `UPDATE ZSXQ_SYNC_RUNS` 仅 30.91–33.76ms，但安全门禁在
  09:36 对 `publication_tasks` 和 `zsxq_sync_runs` 查询分别观测 2,558.07ms/1,027.04ms。
  同一 SQL 形态的分钟级波动再次排除固定缺索引，支持 PgBouncer/远端连接等待归因。
- 09:36 门禁仍只拦截该一个 ZSXQ run，未使用 `FORCE_RESTART_ACTIVE_TASKS=1`；bootstrap 修复
  继续保持 `code_changed`，等待真实安全窗口后再替换进程。
- 09:38 只读快照显示当前 tab 为“官方活动”第 1/2 页；连本次只读诊断的
  `SET TRANSACTION READ ONLY` 也出现 3,607.78ms，证明连接层等待会影响诊断 SQL 本身。
- 09:42 ZSXQ `_upsert_topic_comments` 按 `(group_id, topic_id, comment_id)` 查询 33 条评论
  耗时 12,766.90ms；该条件已有唯一约束，结合同一窗口其他查询在 30ms–12.8s 间波动，
  继续排除“为该查询盲加索引”的做法。
  09:40 同步 checkout 持有 6,599.52ms，其中 SQL 5,866.02ms、non-SQL 733.50ms，
  继续支持“远端往返占主因、非外部 I/O 跨 Session”。

### 09:46–09:57 页内往返根因修复与安全门禁复核

- 活动全 tabs 同步的实时日志进一步确认逐条宽写热点：
  `services/zsxq_sync_service.py:5792:_update_existing_topic` 的 `UPDATE ZSXQ_TOPICS`
  单条 1,748.18–2,534.62ms；同一页的 `UPDATE ZSXQ_SYNC_RUNS` 心跳为 666.98–1,021.07ms。
  这不是固定执行计划慢，而是每个主题一次远端往返叠加了 PgBouncer/网络等待；动态
  `comments_count/likes_count/reading_count/user_specific` 等字段使完整 payload hash 变化，
  不能通过删除原始 JSON 或牺牲字段完整性规避。
- 已落地代码根因修复：existing topic 投影先准备、再按字段集合页内 `executemany` 批量 UPDATE；
  同步心跳 commit 限制为最多 30 秒一次，页末强制保存最终 result。带附件下载的路径在外部 I/O
  前先刷批次，避免下载分支内部 commit 重复执行。没有 CREATE/DROP INDEX、ALTER、回填、配置
  变更或原始 JSON 删除。
- 新增 Bug `docs/bugs/2026-08-06-zsxq-page-update-roundtrip-amplification.md`，并纳入
  `DB-SESSION-LIFECYCLE` 复发注册。定向 112 tests、完整 unit 1,030 tests（09:56:12）均通过；
  `compileall`、`git diff --check`、Bug 严格检查 146/146、registry JSON 均通过。
- 09:57 的只读安全门禁自身观测到 `publication_tasks` 3,323.43ms、`zsxq_sync_runs` 1,018.65ms，
  checkout 5,241.64ms（SQL 4,342.08ms、non-SQL 899.56ms），并发现新的不可中断
  `auto_all_tabs_hh` run `426d7c5f-b78e-4b88-95c7-c4463550ba19`（洪灝的宏观策略）。
  门禁继续拒绝重启且未使用 force；新批量路径和 bootstrap 修复仍未运行态加载，不能提前宣称
  慢查询已定量清零。

### 10:00–10:04 安全重启与新 PID 验收

- 09:59:39 安全门禁返回 `OK: no non-interruptible processing tasks found` 后，按门禁执行完整
  `scripts/dev/restart-dev-stack.sh`；Redis/PgBouncer/schema 预检通过，旧后端/worker/scheduler
  已停止，新后端 PID `47594`，8/8 监听验收通过。
- `GET /health` 返回 `{"status":"healthy","version":"1.0.0"}`。新进程日志的
  `process_started_at=2026-08-06T02:01:48Z` 已出现正常请求；新 PID 没有匹配
  `backend-process-bootstrap`/`has_active_credential` 的 13.545s 长连接持有，旧 PID `37909`
  的历史样本仍保留在日志中但不计入新窗口。bootstrap Session 根因已完成运行态替换验收。
- 新 `zsxq_sync` worker 在 10:02:48 报 `ready`，说明页级批量代码已随重启载入；自动调度器下次
  全 tabs 轮次为 11:50 左右，当前没有人为触发外部同步，因此“批量 UPDATE 次数下降”仍待下一
  真实轮次定量确认，不把单元测试冒充运行态收益。
- 重启后新窗口仍可见远端等待型慢 SQL，例如 `has_active_credential` 2.65s、MP3 `load_task`
  5.08s；对应新连接持有 9.07s 中 SQL 8.20s、non-SQL 0.87s。这些是连接/PgBouncer 或宽 JSON
  读取长尾，不能据此盲加索引；bootstrap 非 SQL 长持有已不再出现。

### 10:01:48–10:04:30 新 PID 短窗口基线

- 新进程短窗口仍捕获 34 条应用慢 SQL，累计 77,145.72ms，P50/P95/最大为
  2,173.94/3,957.77/5,077.59ms；命中 `publication_tasks` 12 条、`zsxq_sync_runs` 3 条，
  尚未进入新的 ZSXQ 全 tabs 页批处理阶段。
- 累计耗时最高的指纹是 `has_active_credential` 5 次/12,015.18ms，随后为 MP3
  `load_task` 3 次合计 11,414.76ms、`_resolve_credential_record_uncached` 4 次/6,125.91ms、
  自动同步配置读取 2 次/4,846.14ms。它们属于远端连接/PgBouncer 等待、宽 JSON 或请求链路，
  不能用“出现了慢 SQL”直接推出缺索引。
- 48 个请求全部成功；请求 P50/P95/最大为 2.01/10,402.56/25,793.83ms。低频高延迟包括
  Bilibili 上传 POST 25.79s、认证校验 12.77s、任务详情 GET 10.11s；平台凭据 GET
  9.75s、任务详情 GET 10.11s 没有匹配慢 SQL，仍归为页面/API 很慢但 SQL 不匹配的端到端长尾。
- 连接持有 4 次，P50/P95/最大 9,065.91/11,100.51/11,100.51ms，SQL 总计
  33,964.07ms、non-SQL 3,104.90ms，且没有“无匹配慢 SQL”的持有样本；SQL 错误、statement/lock/
  connection/query timeout、idle-in-transaction 均为 0。PgBouncer wait/query 分位数为
  294.49/365.17ms 与 757.48/812.97ms，说明新窗口仍有连接层长尾，但未再出现 bootstrap 的
  13.5s non-SQL 长持有。
- 该窗口只证明新 PID 健康和 bootstrap 生命周期根因已替换，不能证明页批量写收益已经实现；
  真实收益要在自动调度的下一轮全 tabs 运行中比较 `zsxq_topics` UPDATE 次数、`zsxq_sync_runs`
  心跳次数、累计耗时和端到端页面/API 失败率。

### 10:04:21–10:25:10 自动同步真实负载与附件状态根因

- 新 PID 的自动同步/重试窗口共捕获 219 条应用慢 SQL，累计 677,175.11ms，P50/P95/最大为
  2,920.15/5,334.07/10,723.77ms；`zsxq_topics` 35 条、`zsxq_attachments` 91 条、
  `zsxq_sync_runs` 21 条。累计耗时最高是旧 worker 的逐附件 `_update_attachment_download_state`
  74 次/234,370.98ms（P50/P95/最大 3,360.26/5,303.02/5,989.57ms），这是本轮新增确认的
  高频热写根因，不是主键索引缺失。
- 页级主题批量路径已经在运行日志留下 `_apply_existing_topic_updates` 3 次（累计 15,763.86ms，
  最大 10,723.77ms），未出现旧的 `_update_existing_topic` 指纹；这证明批量代码确实被旧 PID
  加载，但当前任务在远端断连/重试中，不能把这 3 次当成稳定收益。最终成功的 4e9f1b57 任务
  7 页/115 条主题，`topics_new=0/topics_updated=0/topics_existing=6`，没有覆盖有变化主题的
  正向批量写场景。
- 连接持有 84 次，P50/P95/最大 6,491.95/15,323.20/33,141.78ms，SQL/non-SQL 总计
  442,338.49/248,686.56ms；其中 `_update_attachment_download_state` 19 次持有累计
  112,009.70ms，14 次持有没有匹配慢 SQL。窗口有 13 次 server-closed；ZSXQ 任务一次重试后
  因 `Connection closed by server` 被 reject，后续重试才完成。该现象同时说明“连接层/远端故障”
  会放大所有 SQL 指纹，不能只按次数加索引。
- 已在工作树实现附件状态批量提交与新主题/附件页边界 flush：状态队列按字段形状 `executemany`、
  外部下载前仍强制 commit/release、失败保留可重试 pending、原始 JSON 不删。定向测试已 113
  通过；完整 unit `1,031 tests`（10:37:38）也全部通过，但这组新代码尚未运行态加载。

### 10:27:45–10:32 数据库 HBA 再次阻断重启与业务

- 10:27:45 只读安全门禁先返回 `OK: no non-interruptible processing tasks found`；随后
  `restart-dev-stack.sh` 在 schema 预检遇到 `server closed the connection unexpectedly` 中止，
  没有停止旧 PID、没有 force、没有执行 DDL。当前 Flask PID `47594`、ZSXQ worker `48560` 仍在运行。
- PgBouncer 原始日志从 10:29:45 起持续记录 `no pg_hba.conf entry for host "223.80.177.164"`，
  10:30:34 起进入 cached server-login failure；平台凭据、MP3 任务/上传 API 随后出现 500，而
  `/health` 仍为 200。这是 2026-08-04 HBA 出口 IP Bug 的再次复现，必须人工恢复最小白名单或稳定
  出口后再重启/验收，不能用索引或应用重试掩盖。

### 10:44–10:52 旧 worker 运行态复核与最新代码回归

- 只读日志窗口仍有 19 条应用慢 SQL，累计 45,246.03ms，P50/P95/最大为
  2,369.62/3,915.92/5,744.07ms；主要来自 MP3 `load_task`（3 次/11,239.58ms）、平台凭据
  列表（3 次/6,838.64ms）和另一个 MP3 `load_task` 形状（3 次/6,472.47ms）。这说明当前长尾
  已转移到凭据/任务详情等页面链路，不能把旧 ZSXQ 附件热写的历史窗口当成最新修复已生效。
- 端到端共有 141 个完成请求、1 个失败，累计 117,324.56ms，P50/P95/最大为
  2.17/4,123.53/13,844.39ms；低频高延迟包括 Bilibili 上传 POST 13.84s、任务详情 GET
  11.09s。另有多个下载 artifact/视频接口 3.79–4.47s 但没有匹配慢 SQL，继续归为外部 I/O/文件
  读取长尾，而非索引问题。后台 ZSXQ 同步 1 次成功、56.05s（7 页/115 条，新增 0、更新 0、
  已有 6、附件 7），没有覆盖“有变化主题+附件下载”的正向批量场景。
- 连接持有 1 次、7,904.24ms（SQL 7,577.42ms、non-SQL 326.82ms），无 idle-in-transaction、
  statement/lock/query/connect timeout；PgBouncer 仍记录 HBA rejected 2、cached login failure 3，
  因而该窗口不能用于判断 executor 或索引收益。原始日志最后仍可见 `no pg_hba.conf entry`，最新
  附件批量代码未通过安全重载进入 PID `48560`。
- 针对最后一处页级边界漏洞又做了代码收紧：新主题无附件下载时，评论/外链扫描在 `no_autoflush`
  中执行，心跳不再提前 flush 新行；已有主题切入前只提交一次前置新行，页末统一落盘；心跳计时从
  页快照提交后开始，避免每页首主题的冗余 UPDATE，同时保留最长 30 秒心跳和页末强制 result。
- 最新回归重新完成：完整 unit `1,031 tests`（10:51:12）通过；定向 ZSXQ/投影纪律 113 tests、
  `compileall`、`git diff --check`、recurrence registry JSON 和 Bug 严格检查 148/148 全部通过。
  这证明代码根因修复可回归，但没有把未重载的旧进程指标冒充为运行态收益。

### 当前结论

代码根因已经修改到 Session/往返层：ZSXQ 主题投影按页批量、附件状态按字段形状批量、外部下载前
释放连接、新主题页边界禁止 autoflush、失败保持可重试；此前 bootstrap、外部 I/O 跨 Session、
轮询/失败风暴也已修复。仍然慢的当前窗口主要是 PgBouncer/远端 HBA 与网络等待、凭据/任务宽 JSON
读取和无 SQL 的文件/上传 I/O；不是“索引没加够”。10:58 的提升权限只读门禁已经成功连接
PostgreSQL，说明 HBA/出口至少暂时恢复；但附件最新代码尚未进入 worker，且门禁发现 2 个仍在进行的
Bilibili 上传任务，不能中断。下一步固定为：等待不可中断任务自然结束 → 重新只读门禁 →
`restart-dev-stack.sh` → 健康/PID/代码版本确认 → 真实正向 ZSXQ 批量轮次对比 UPDATE 次数、累计耗时、
P95、连接持有和端点失败率。

### 10:57–10:58 HBA 恢复后的安全门禁

- 沙箱内门禁曾因本地网络权限返回 `Operation not permitted`；按规则以提升权限重跑后，远端 PostgreSQL
  连接成功。门禁只读查询本身仍受远端等待影响：`publication_tasks` 49 行耗时 1,110.49ms，
  `zsxq_sync_runs` 查询 423.38ms；这再次说明 executor 不能仅凭应用慢度判断。
- 门禁明确发现 2 个不可中断 `publisher/upload_bilibili` 任务（10%），因此返回 `BLOCKED`，没有
  使用 force、没有重启、没有改配置或 DDL。待这两个上传自然完成后必须重新执行门禁；只有门禁全绿才
  允许加载附件状态批量代码。
- 11:02 再次只读门禁确认其中 1 个已完成，仍有 1 个任务 `7524de1f-681e-40e6-bdb2-b8e4240ad068`
  停在 `upload_bilibili` 10%；同时 `publisher_queue_repair` 对该子任务的只读检查出现
  `Timeout reading from socket`。这属于外部上传/远端连接安全窗口，不足以授权 force 或把任务标为可中断。

### 11:07–11:24 新 worker 基线与附件正向同步证据

- 11:06:59 只读安全门禁终于全绿；随后按门禁执行 `restart-dev-stack.sh`，新后端 PID 为 `55059`，
  ZSXQ worker `55454` 于 11:08:36 ready，health 返回 healthy，8/8 listener 检查通过。此时加载的
  仍是“附件边界补强”之前的工作树版本，最新的显式 `flush/no_autoflush` 追补尚未进入 worker。
- 自动 all-tabs Honghao 任务 `9fac2a1e1...` 于 11:10:09 完成，63.2199s、7 页/115 topics，
  `topics_new=0/topics_updated=0`，只覆盖无变化主题，不能作为正向批量写收益样本。
- 随后的正向系统同步 `f3545365-4503-4dc0-93fe-80d396497d2d` 于 11:10:10–11:24:00 完成
  829.6246s，4 页/78 topics，新增 10、更新 68、已有 68，附件 503、下载 17、失败 1。
  11:10:10–11:24:01 分析器捕获 12 条应用慢 SQL，累计 44,510.82ms，P50/P95/最大为
  1,326.07/5,085.20/25,091.23ms；SQL 错误、statement/lock/query/connect timeout 和
  idle-in-transaction 事件均为 0，PgBouncer HBA/login/timeout 事件也为 0。
- 最大样本已把隐藏根因闭环：`services/zsxq_sync_service.py:6266:flush_state_updates` 触发
  25,091.23ms 的 `INSERT ZSXQ_ATTACHMENTS` 隐式 ORM flush，来源标注在
  `_apply_attachment_download_state_updates`；同一 checkout 持有 26,449.39ms，其中 SQL
  26,395.66ms、非 SQL仅 53.73ms。另一长尾是 `_load_existing_attachment_snapshots` 19-topic
  投影读取 5,085.20ms，连接持有 6,293.71ms（SQL 6,186.07ms、非 SQL 107.64ms）。这不是索引
  缺失证据，而是跨公网连接等待叠加到 ORM autoflush/宽投影读取。
- 批量 UPDATE 的运行态收益已有局部证据：后续 `UPDATE ZSXQ_ATTACHMENTS` 影响 2–3 行约
  63–333ms，页末一次影响 84 行仅 45.72ms；但这些日志仍来自旧 worker，不能冒充最新追补代码
  已验收。最新追补已在代码中明确“先 flush 新 ORM 行，再 `no_autoflush` Core executemany，
  最后 commit”，专门消除上述隐式 flush 归因错位。
- 同窗口端到端只捕获 7 个探针请求（3 成功/4 失败），不能代表全业务页面健康；后台任务统计为
  ZSXQ sync 829,624.60ms、workflow notify 1,327.33ms。探针失败来自栈重载期间的静态/访问路径，
  不把它误归因为该同步 SQL。

### 11:25–当前 发布接力再次占用安全窗口

- 清洗任务 `d78f6c68-7172-417b-b263-a567416aafda` 于 11:25:08 完成（17 files），随后自动发布
  任务 `7ce078e6-f030-492e-ba58-a7280977a9e7` 在 5% 进入不可中断状态；11:25:58 安全门禁因此
  再次返回 `BLOCKED`，尚未加载最新附件边界补丁，也没有 force。
- 发布日志同时出现知识星球外部接口 `code=1059` 重试和 `code=13407` 当日文件限额，七牛上传本身
  成功；这属于外部 I/O/平台配额，不是 PostgreSQL executor。发布起始阶段唯一明显 DB 长尾为
  `tasks/zsxq_publish.py:166` 点查 `publication_tasks` 1,882.59ms，另有一次 runtime guard
  观测到约 1.9s idle-in-transaction，需在发布结束后继续观察生命周期，但不能把外部上传时长归给索引。

当前运行态结论更新为：页面批量、附件批量和外部 I/O 边界的代码根因均已修改并通过 1,033 个完整
单测；但最新 `flush/no_autoflush` 追补尚未重载，且安全窗口被自动发布接力占用。待发布自然结束后，
必须再次门禁→重启→以“正向新增/附件同步”窗口确认不再出现 25 秒隐式 INSERT/26 秒连接持有，
同时继续把 5 秒附件快照读取按 PgBouncer/网络等待与真实 EXPLAIN 区分，禁止盲加索引。

### 11:32–11:34 最新 worker 运行态复盘：附件边界已部分验收，但暴露下载分支的主题单行刷写

- 第二次安全门禁在 11:30:06 全绿后完成重启；旧后端替换为 PID `58294`，旧 ZSXQ worker
  `58697` 于 11:31:26 ready。该 worker 已载入显式 `flush/no_autoflush` 追补。系统同步任务
  `a930c0f1-4595-4879-b47c-7f9e3a956a0a` 对应 run `885c6114-fa8d-48d9-984c-e59c5dea563c`
  于 11:32:00–11:33:53 成功，4 页/78 topics，`topics_new=0/topics_updated=67`，附件 503，
  下载 0、复用本地 137、失败 1。
- 这轮没有 `INSERT ZSXQ_ATTACHMENTS`，也没有此前 25 秒隐式 flush/26 秒连接持有；附件状态批量
  UPDATE 多次影响 9 行，约 0.52–0.69 秒。说明显式 `flush` 与 `no_autoflush` 边界已在运行态生效，
  但该轮没有新增下载，不能把它当成新附件 INSERT 的正向覆盖。
- 同窗口 11:32:00–11:34:10 的应用慢 SQL 为 6 条，累计 39,710.70ms，P50/P95/最大
  `1,681.43/16,801.68/16,801.68ms`；连接持有 1 次、17,083.80ms，其中 SQL 17,007.56ms、
  non-SQL 76.24ms，statement/lock/query/connect timeout、SQL 错误、idle-in-transaction 和
  PgBouncer HBA/login/timeout 均为 0。最大长尾不是 executor/index 证据：
  `_submit_auto_script_material_dispatch_for_run` 的 137-ID 窄列附件 SELECT 16,801.68ms，
  同方法历史上对 174–253 个 ID 也出现 4.25–16.10 秒；当前更符合远端连接等待叠加“复用附件候选集较大”，
  不改索引、不牺牲复用语义。
- 同步过程中 Redis broker 在 11:33:25 断连并恢复 1 条未确认消息，同一 Celery task id 被二次投递；
  首次任务成功 113.214 秒，幂等终态守卫随后记录 `duplicate terminal delivery ignored`，重复投递
  0.870 秒直接跳过。该问题属于 broker/late-ack 复发，不是 PostgreSQL executor；需单独监控，不能
  用数据库索引修复。
- 运行日志同时揭示一个仍未收干净的代码根因：`download_attachments=True` 时，上一版在下一主题
  开始前把 `pending_topic_updates` 先执行并提交，导致 67 个 existing topics 仍退化成单行
  `UPDATE ZSXQ_TOPICS`。这解释了为什么仅看 `_apply_existing_topic_updates` 指纹会高估收益。

### 11:47–11:50 主题下载边界根因修复并完成安全重载

- 已移除上述“下一主题前刷 pending topic UPDATE”分支。主题投影更新现在只保留为 Python 标量映射；
  下载前的 Session commit 只提交评论/附件投影和下载状态，页心跳或页末再按字段形状执行 Core
  `executemany`。因此外部 I/O 不跨 Session，且不会为下载安全边界重新引入一条主题往返。
- 新增回归 `test_existing_topic_updates_stay_batched_across_download_commits`，验证开启下载时两条
  existing topic 只触发一次、包含 2 条映射的批量应用；定向 ZSXQ/投影纪律测试现为 115 项全绿。
- 11:47:37 首次门禁因沙箱网络权限拒绝而安全退出；提升权限重跑在 11:48:00 成功连接数据库，
  `publication_tasks` 47 行/108.86ms、`zsxq_sync_runs` 0 行/38.89ms，确认无不可中断任务。
  随后完整 `restart-dev-stack.sh` 在无 force 情况下完成，后端新 PID `60726`，11:49:09 `/health`
  返回 `healthy`，ZSXQ worker 11:49:15 ready。重启 schema 预检仍仅提示 20260724 评论索引维护未登记及
  两个等价索引待管理员处理；没有执行 DDL。
- 最新 worker 尚未再跑一轮包含“下载开启且 existing topic 有变化”的真实同步，因此不能把该修复
  运行态收益提前写成已证实。下一轮验收只需比较 `UPDATE ZSXQ_TOPICS` 实际 SQL 次数、
  `_apply_existing_topic_updates` 批次数、页面/后台总耗时与连接持有；若仍有慢度，继续按
  PgBouncer/网络等待和 137-ID 复用候选读取单列，不盲加索引。
- 11:51–11:52 重新执行完整 unit，`Ran 1033 tests in 39.460s`、`OK`；随后 `compileall`、
  `git diff --check`、recurrence registry JSON 和 Bug 严格检查 148/148 均通过。该结果只证明
  代码与合同回归完整，不替代下一轮真实 ZSXQ 外部同步验收。

### 11:50:43–11:53 工作流正向轮次与新发现的通知生命周期根因

- 新 worker `61141` 自动消费同步任务 `e5ce5e4f-38f3-456b-987f-b0b763c1c55d`，对应 run
  `9c4b0d34-3736-4691-8ad2-53e1e153b6b1`，11:50:43–11:52:21 完成 4 页/78 topics，
  `topics_new=0/topics_updated=62/topics_existing=78`，附件 503，下载 0，失败 2，复用 137。
  这是新代码首次覆盖“下载开启 + existing topic 有变化”的正向轮次，但没有覆盖新增附件 INSERT。
- 主题更新运行态收益已经闭环：旧 worker 78 次单行 `UPDATE ZSXQ_TOPICS`、日志耗时合计
  13,379.76ms；新 worker 为 7 次批量操作、合计影响 78 行、耗时 5,013.02ms，round-trip
  减少 91.0%，主题写耗时下降 62.5%。批次行数为 18、2、5、14、20、10、9，无单行 UPDATE。
- 该同步任务端到端 96.563s；精确分析窗口 11:50:43–11:52:21 捕获 5 条应用慢 SQL、累计
  8,127.62ms，P50/P95/最大 1,632.33/1,953.34/1,953.34ms。主要指纹为
  `_apply_existing_topic_updates` 2 次/2,977.19ms、`_load_existing_attachment_snapshots`
  2 次/3,543.74ms、`_find_existing_downloaded_attachment` 1 次/1,606.69ms；均为窄投影或点查
  叠加 PgBouncer/网络等待，没有 connection hold、SQL 错误、statement/lock/query/connect timeout
  或 idle-in-transaction。当前证据仍不支持新增索引。
- 同一工作流最终通知阶段暴露另一条独立根因：`_queue_notification` 在连续终态事件之间因
  `commit()` 默认过期 ORM，触发 `zsxq_workflow_runs` 主键全行 SELECT 33,513.64ms；连接持有
  33,626.75ms（SQL 33,559.39ms、non-SQL 67.36ms），没有超时或 SQL 错误。该查询条件已是主键，
  不是索引缺失；根因是 `sync_failed → workflow_failed` 的过期 Session 生命周期。
- 已在工作树修复：通知链路局部 `expire_on_commit=False`，提交后不再访问过期 parent；通知标记改用
  `ZsxqWorkflowRun` 主键 Core UPDATE，并同步 identity-map 已提交快照。新增连续通知回归测试，
  定向 workflow/session/投影 86 tests 通过；但当前运行态 worker 尚未加载该最新修复，必须重新执行
  安全门禁后重载，再用真实 finalizer 窗口确认 33 秒级整行刷新消失。

### 11:53 时点量化结论与边界（已由下节运行态更新）

- 已实证根因修复：ZSXQ existing-topic 页内逐条 UPDATE → 7 个批次，真实主题写 round-trip -91.0%、
  累计写耗时 -62.5%；旧的附件隐式 INSERT/跨下载持有也已通过显式 flush/no_autoflush 代码与新 worker
  无新增下载轮次部分替换。
- 在 11:53 截止时尚未实证真实新增附件下载的 INSERT + 状态批量 UPDATE，及 workflow notification
  生命周期修复在新 PID 的运行态收益；下方 12:31–12:33 段已补齐后者和新 worker 页级评论证据。

### 12:13:44–12:21:32 全 tabs 旧 worker 运行态：评论与外链 N+1 根因闭环

- 任务 `823fc84f-ac5d-457f-b454-c83c42a5dbd0` 于 12:21:30 完成，耗时 466.0209s，34 页/610 条
  主题（新增 13、更新 418、已有 597、附件 867、下载 0）。这是旧 worker 的对照，不代表后续
  页级快照补丁已加载。
- 精确窗口共 35 条慢 SQL，应用 32 条/50,467.81ms，P50/P95/最大为 1,279.26/2,759.67/
  4,131.68ms；连接持有 18 次，P50/P95/最大为 8,198.24/15,237.13/16,753.64ms，SQL/non-SQL
  总计 147,734.87/5,195.90ms。`_upsert_topic_comments` 15 次持有累计 128,676.43ms，
  `external_document_service._upsert_document_shells` 2 次累计 15,187.00ms；这是页内逐主题
  评论/外链 N+1 叠加远端往返的直接证据，不是索引缺失。无 SQL 错误、statement/lock/query/connect
  timeout、idle-in-transaction 或 PgBouncer 登录/HBA 事件。
- 已在代码中改为页级轻量评论快照（按 comment ID 分块、Core 差异回写）和外链 DTO +
  `scan_topic_batch`（shell/关系各一组查询），完整保留 `raw_payload` 审计 JSON。

### 12:31–12:33 安全重载与新 worker 页级快照验收

- 823fc 自然完成后重新执行安全门禁：`publication_tasks` 47 行/98.25ms、`zsxq_sync_runs` 0 行/
  166.03ms，返回 `OK: no non-interruptible processing tasks found`；随后按门禁完成重启。schema
  预检仍只提示 `20260724_comment_hash_indexes` 未登记及两个等价索引待管理员处理；没有执行 DDL。
  新后端健康探针经提升权限返回 `{"status":"healthy","version":"1.0.0"}`，ZSXQ worker
  `67018`、workflow worker `67030` 均 ready。
- 新 worker 任务 `8d8d1ba6-e0ef-4f31-ac17-d3b0141baff1` 于 12:33:27 完成，7 页/115 topics、
  58.5209s，无变化主题；但已能看到页级 `_load_existing_topic_comment_snapshots`：两次读分别影响
  258/418 行，耗时 1,128.76/924.55ms。12:32:10–12:33:28 精确窗口仅 4 条应用慢 SQL、
  4,516.18ms，P50/P95/最大 1,131.81/1,199.62/1,199.62ms，connection hold=0，SQL 错误/
  timeout/idle-tx=0。旧的 `_upsert_topic_comments` 逐主题慢指纹没有出现。
- 同一窗口后台 1 个 ZSXQ sync 成功 58,520.95ms、3 个 workflow notify 均成功；其中一次
  `sync_failed` 8.6199s 是邮件/通知外部 I/O 长尾，未匹配 33 秒级 `zsxq_workflow_runs` 全行刷新。
  端到端探针只捕获 1 个 `/health` 成功请求，不能据此宣称全部页面/API 已健康；完整页面/API 基线
  仍以本报告前述 24 小时采样为准。
- 当前仍需观察后续 Shengcai queued run `fa7e5ece-d4ab-43c7-ab77-268d32e447ee` 的页级评论/外链
  批次；该轮启动时没有不可中断任务门禁问题，不能中途重启或 force。

### 当前结论更新

- 已实证并替换的代码根因：外部 I/O 跨 Session、ZSXQ 主题逐条宽 UPDATE、附件状态逐条写、隐式
  新行 flush、workflow 连续通知过期 ORM、以及评论/外链页内 N+1。主题写 round-trip 真实下降
  91.0%，评论页级快照已在新 PID 运行态出现且长连接归零。
- 仍然存在的慢度主要是远端 PostgreSQL/PgBouncer 往返（页级评论快照、主题/附件窄投影、workflow
  `ensure_schema`）、外部邮件/下载 I/O 和少量批量写长尾；没有新的 `pg_stat_user_indexes` +
  `EXPLAIN (ANALYZE, BUFFERS)` 证据，继续不改索引。新增附件 INSERT 的正向 33.02ms 证据已记录，
  25 秒隐式 INSERT/26 秒持有未再出现。

### 12:33–12:46 上游重试、最终补丁重载与带附件同步验收

- 旧 worker 的 Shengcai 任务 `fa7e5ece-d4ab-43c7-ab77-268d32e447ee` 在 12:34:42 因知识星球
  `code=1059` 重试一次，12:40:09 最终成功；run `9c2ea6bd-f897-4586-9f22-a9117de90893`
  耗时 27.0596s，4 页/78 topics，更新 47，附件 503、下载 0、失败 0。12:33:29–12:40:10
  分析器仅捕获 2 条应用慢 SQL、累计 4,955.97ms，P50/P95/最大 1,520.72/3,435.25/3,435.25ms，
  指纹分别为 `_apply_existing_topic_updates` 1,520.72ms 与
  `_load_existing_attachment_snapshots` 3,435.25ms；connection hold、SQL 错误、各类 timeout、
  idle-in-transaction 均为 0。PgBouncer wait/query P50/P95/最大为 31.75/54.11/54.11ms 与
  71.16/107.75/107.75ms；3 个 `server_closed` 事件与上游重试同窗，未形成数据库失败风暴。
- 等待任务自然结束后，12:44:38 提升权限安全门禁读取 `publication_tasks` 47 行/333.67ms、
  `zsxq_sync_runs` 0 行/40.77ms，明确 `OK: no non-interruptible processing tasks found`。随后
  第二次安全重载完成：`zsxq_sync` 12:45:54、`zsxq_workflow` 12:45:56、`workflow_notify`
  12:45:57 ready，backend bootstrap 12:46:16 finish；健康端点返回 `healthy`。schema 预检仍只提示
  `20260724_comment_hash_indexes` 未登记及两个等价索引待管理员处理，未执行任何 DDL。
- 最终的“提交前提取外链、跨提交只传链接列表”代码已由新 worker 执行首轮带附件同步：任务
  `a4388ac3-405e-47bf-9055-3eed019b7c45` / run `9540cf00-116b-4298-a1f9-d902bd8092c2` 于
  12:46:17–12:46:55 成功，38.7318s、4 页/78 topics、更新 77，附件 503、下载 1、失败 0。
  12:46:17–12:46:57 精确窗口为 3 条应用慢 SQL、累计 6,011.45ms，P50/P95/最大
  1,537.22/3,431.14/3,431.14ms：附件快照 3,431.14ms，主题页批量 UPDATE 两次
  1,043.09/1,537.22ms；connection hold、SQL 错误、statement/lock/query/connect timeout、
  idle-in-transaction 均为 0。Celery sync 38,731.84ms、workflow 3,519.62ms、3 个通知共
  4,083.68ms 且全部成功。该窗口没有可归因的前台页面/API 请求，不能把后台样本扩大解释为全页面健康。
- 这一轮没有出现评论逐主题 `_upsert_topic_comments`、外链 shell/关系 N+1 或 25 秒隐式
  `INSERT ZSXQ_ATTACHMENTS`/26 秒连接持有。由于本轮日志没有可识别的新增外链样本，外链 DTO 的
  “不携带 raw JSON、保留链接列表”由回归测试和代码路径确认，仍需含真实外链的后续样本观察；不把
  未捕获误报为“没有外链问题”。

### 最终运行态判断

- 根因闭环范围现在包括：外部 I/O 跨 Session、主题逐条宽 UPDATE、附件状态逐条写/隐式 flush、
  workflow 通知过期 ORM 全行刷新、评论逐主题 N+1、外链逐主题 shell/关系 N+1。主题写真实批次已
  将 round-trip 降低 91.0%，旧评论 N+1 的 15 次长持有（累计 128,676.43ms）在新 worker 页级
  快照路径中归零。
- 剩余慢度不是“还缺一个索引”的证据：当前可观测长尾是远端 PostgreSQL/PgBouncer 的窄投影读、
  批量宽 UPDATE、workflow `ensure_schema`、外部邮件/知识星球 I/O，以及偶发上游 1059。没有新的
  `pg_stat_user_indexes` + `EXPLAIN (ANALYZE, BUFFERS)` 谓词证据，不执行 CREATE/DROP INDEX、
  ALTER、回填或生产配置变更；待管理员按 DBeaver 脚本处理启动预检提示的重复/未登记评论索引。
