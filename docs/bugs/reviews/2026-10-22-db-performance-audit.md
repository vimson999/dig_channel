# 2026-10-22 数据库性能与错误巡检

## 结论

本轮覆盖 2026-10-21 06:00:00 至 2026-10-22 06:00:00（Asia/Shanghai），对应数据库 UTC
边界 2026-10-20 22:00:00 至 2026-10-21 22:00:00。没有达到“全部根因闭环”：数据库 executor、锁和
索引不是当前主瓶颈，但两个 P0 复发键仍为 OPEN/REPEATED。

1. MP3 音频目录的 3 次冷建 SQL 累计 24.109 秒，P95 8.006 秒、最大 8.269 秒；上一轮同形生产
   executor 仅 5.182ms，三次连接持有中 SQL 占 24.250 秒、非 SQL 仅 352ms。根因仍位于 PostgreSQL
   executor 之外的驱动 fetch、PgBouncer/私网传输边界，不能靠加索引或放宽 timeout 根治。
2. 旧 Mac 运行体本窗再次直接连接生产库：`pg_stat_activity` 守卫日志捕获
   `dig_ytb_scheduler_celery_watchdog_loop_py_92458`，来源 `100.117.177.98/32`。同窗 Publisher 9 条中
   1 条被写为 `required_material_unavailable`，但所列当前根 image/audio 在当前节点均存在；本地日志只有
   消息可见、当前 worker 收到终态后跳过，没有失败写入。它是日志外旧 actor 误关，不是本地素材缺失。

本轮未改生产代码、未重启常驻服务、未自动执行 DDL。所有索引建议保持观察；YouTube 两个永久失效源仍等待
人工执行已有受保护 SQL。ZSXQ 发布出现多次 1059 瞬时上传失败，但 durable run 最终 25/25 文件提交并验证，
没有最终丢件或失败风暴。

## 与上一轮量化对比

| 指标 | 2026-10-21 基线 | 本轮 | 变化与判定 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 13 条 / 37.683s | 16 条 / 41.431s | 数量 +23.1%，累计 +9.9%，恶化 |
| 慢 SQL P50 / P95 / max | 1.310 / 8.041 / 8.181s | 1.322 / 8.006 / 8.269s | P95 -0.4%，max +1.1%，高尾持平复发 |
| audio catalog | 3 条 / 24.138s | 3 条 / 24.109s | 累计 -0.1%，仍是第一累计耗时指纹 |
| PgBouncer 八类错误 | 0 | 0 | 第二个零错误完整窗；不能覆盖 fetch 高尾 |
| statement / lock / connection timeout | 0 / 0 / 0 | 0 / 0 / 0 | 无新增 |
| idle-in-transaction 压力 | 0 defer | 0 defer | 11 个瞬时观测，最大事务龄 0.305s |
| Publisher | 16/36 完成，20 失败 | 8/9 完成，1 失败 | 失败率 55.6% → 11.1%，改善但未闭环 |
| foreign actor evidence | 17/36 | 1/9 | 47.2% → 11.1%，显著改善但再次复发 |
| HTTP | 928 完成，918 成功 | 592 完成，585 成功 | 7 失败含 5 favicon 404、2 个 B站凭据/外部 502 |
| YouTube 固定失效源 | id 21/56，第 18 窗 | id 21/56，第 19 窗 | 反复，人工动作未执行 |

## 页面、API、后台任务与 SQL 关联

固定窗共有 592 个完整 HTTP 请求、111 个端点；585 成功、7 失败，累计 313.666 秒，P50/P95/max
3.58ms/1.207s/68.941s。

| 页面/API/任务 | 请求或任务 | 累计 / P95 / max | SQL 与调用链结论 |
| --- | ---: | --- | --- |
| `GET .../audio-assets` | 13/13 成功 | 26.838s / 9.044s / 9.170s | 3 个冷建匹配 24.109s canonical SQL；热命中快速 |
| `POST /api/v1/videos/sync` | 1/1 成功 | 68.941s | 无匹配慢 SQL；Google/YouTube 外部 I/O |
| `POST /api/v1/channels/sync/all` | 1/1 成功 | 62.186s | 无匹配慢 SQL；Google/YouTube 外部 I/O |
| 内容 Web submissions 列表 | 33/33 | 15.814s / 639ms | 无慢 SQL；没有旧大 JSON 指纹 |
| B站 charging dashboard | 11/11 | 11.863s / 2.028s | 1 条 1.244s SQL，executor/索引非主因 |
| B站 data table | 11/11 | 10.473s / 1.568s | 1 条 1.042s SQL |
| 内容 `web-submit` | 1/1 | 9.619s | 无匹配慢 SQL；文件、hash、broker 阶段未完全细分 |
| rankings / chart | 各 11 | 9.553s / 8.312s | 无匹配慢 SQL；缓存/序列化与亚秒往返 |
| artifact regenerate | 6 个 | finance 7.034s、background 6.509s、cover 4.445s | 无匹配慢 SQL；后台文件/生成链 |
| Publisher pipeline | 5 个 Celery 成功 | 857.286s，P95 295.364s | 外部上传/媒体处理；另有 1 条 durable 误关由旧 actor 写入 |
| Publisher upload-only | 4/4 Celery 成功 | 686.514s，P95 300.987s | 本窗无 21173/21523；一个绿窗不足关闭定时余量问题 |
| ZSXQ publish | 1/1 Celery 成功 | 450.302s | 多次 1059 外部上传瞬断，4 轮后 25/25 verified |
| MP3 workflow | 26 个 Celery 成功 | 211.551s，max 49.731s | 文件/媒体处理，不是前台 SQL 长尾 |
| audio clean | 1 个 Celery 成功 | 167.185s | 后台任务，无前台端点 |

三类异常单列：

- 低频高延迟：两个 YouTube 手工同步分别 68.941/62.186 秒，均无匹配慢 SQL。
- 页面/API 很慢但无匹配慢 SQL：`web-submit`、rankings/chart、artifact regenerate、部分上传与 job
  创建，优先按外部 I/O、文件、broker、缓存和亚秒多往返分析，不能归为 PostgreSQL executor。
- 后台任务或连接持有很久但无前台端点：Publisher、ZSXQ publish、audio clean；三个超过 8 秒的数据库
  hold 全部与 audio catalog 慢 SQL 对齐，没有跨外部 I/O 持有 Session 的新证据。

重点 ZSXQ 页面在固定窗没有可信自然样本，不能写成“没有问题”。窗后只读验收为 health 200/0.131s、
overview 200/3.452s、topics/local 200/0.536s、tabs 200/0.362s、sync runs 200/0.404s、scheduler status
200/0.599s；它们只证明当前可用性，并计入下一窗口，不替代自然流量或后窗。

## SQL 指纹、Session 与 executor 证据

按累计耗时、P95、最大耗时和用户影响排序：

1. `be891b5c1af362da`，来源 `services/mp3_video_workflow.py:495:_build_audio_asset_catalog_snapshot`：
   3 次、累计 24.109 秒、P50/P95/max 8.006/8.006/8.269 秒，影响 3 个冷目录请求。
2. terminal parent cleanup：2 次、累计 3.159 秒、P95/max 2.043 秒。
3. ZSXQ Insights 聚合：1 次、2.015 秒。
4. 其余报告恢复、B站 child/credential/config、内容 canonical、数据表、通知等各约 1.0–1.38 秒。

16 条慢 SQL 中 `publication_tasks` 相关 6 条、`zsxq_topics` 1 条，`zsxq_topic_comments`、
`zsxq_attachments`、`external_documents` 和 `zsxq_sync_runs` 未进入慢 SQL 样本。三次长 hold 的
P50/P95/max 为 8.339/8.354/8.354 秒，SQL 累计 24.250 秒、非 SQL 累计 352ms；没有未匹配的长 hold。
PgBouncer 1,439 个 stats 样本中 wait P50/P95/max 29.25/52.37/181.01ms，query
65.20/82.61/218.66ms；288 次连接 churn（12/h）全部来自当前节点 `100.96.3.12`，无 proxy fallback。

这组证据排除了锁等待、executor 慢和跨外部 I/O 持有 Session，但尚未把驱动 execute/fetch、payload bytes、
deserialize 与同秒 pool/server socket 拆开；因此 audio catalog 只能维持 OPEN/REPEATED/OBSERVING。

## 表规模与索引复核

| 表 | 总大小 | live / dead rows | index scan 占比 | 结论 |
| --- | ---: | ---: | ---: | --- |
| `publication_tasks` | 150MB（table 18MB、TOAST 118MB、index 14MB） | 14,653 / 2,342 | 99.83% | 大 JSON/TOAST 是主要体积，不删除审计原文 |
| `zsxq_topics` | 132MB | 13,847 / 1,621 | 99.52% | Insights 选择性低，当前顺扫合理 |
| `zsxq_topic_comments` | 119MB | 45,350 / 5,546 | 99.85% | 本窗无慢指纹 |
| `zsxq_attachments` | 79MB | 35,870 / 71 | 99.94% | 本窗无慢指纹 |
| `zsxq_sync_runs` | 37MB | 1,074 / 58 | 99.90% | 顺扫 executor 1.561ms，无加索引依据 |
| `external_documents` | 6.9MB | 1,817 / 146 | 98.74% | 本窗无慢指纹 |

没有 exact duplicate index。低/未使用索引列表只保留为候选，不能在缺少写放大、查询命中和完整观察窗时删除。
代表 `EXPLAIN (ANALYZE, BUFFERS)`：publication PK 0.040ms、channel chart 2.969ms、all-tabs config
0.028ms、Publisher list 0.039ms、内容列表 0.143ms、任务列表有/无 JSON 均 0.025ms、sync runs
1.561ms、Insights 顺扫 13,847 行返回 7,701 行为 39.923ms。audio catalog 上一轮同形 executor
5.182ms。`pg_stat_statements` 未安装，因此查询历史边界明确保留。

结论：本轮没有任何 CREATE/DROP INDEX、ALTER TABLE 或回填依据，不生成新 DDL。已有
`docs/sql/disable_unavailable_youtube_channels_20260920.sql` 仍是业务配置脚本，不是索引整改；只允许人工
在 DBeaver 中逐条复核并执行。

## 反复问题、修复状态与运行态证据

### 旧 Mac / shared DB-Redis-Celery actor

- 修复前证据：上一窗 `foreign_actor_evidence=17/36`，本窗仍有 1/9。
- 当前直接证据：15:30:40 的生产守卫日志捕获旧 Mac watchdog application name 与
  `100.117.177.98/32`；Tailscale 显示该节点当日 22:33 前仍在线。失败 task
  `054f52a6-ef58-4d00-9194-9f504c276c1e` 的 image/audio 均真实存在，本地 mp3 worker 看见队列和 active，
  render worker收到时任务已是终态，本地无失败写入。
- 已落地防线：此前已为 pending/processing 和 upload-only 素材不可达终态持久化 deployment、hostname、
  PID、code identity、路径与关闭时间；日快照以 actor 缺失 + mtime-before-close 识别 foreign actor。
- 运行态结论：审计防线有效，本窗把日志外误关识别为 1；但它不能隔离旧 actor。状态
  OPEN/REPEATED/RUNTIME_REPLACED/WAITING_DISTRIBUTED_ISOLATION。
- 责任动作：人工停止旧 Mac 的 backend/Celery/Watchdog/scheduler/脚本；不能证明全停时，轮换 PostgreSQL/
  Redis 凭据并隔离 broker namespace。下一轮要求完整 24h 远端 actor=0、foreign actor=0、old-root=0，且
  当前根 PDF→ZSXQ、MP3→Publisher 自然成功。

### audio catalog executor 外尾延迟

- 修复前证据：本窗 3 次 / 24.109 秒，应用约为 5.182ms executor 的 1,500 倍；Session 非 SQL 占比仅 1.4%。
- 已落地防线：单次有界投影、查询后冻结 DTO 并释放 Session、文件校验在 Session 外、300 秒目录缓存；旧附件
  N+1 和 deferred lazy load 未复发。
- 运行态结论：查询预算防线继续 RUNTIME_REPLACED，但物理 fetch/PgBouncer/私网根因未定位，保持
  OPEN/REPEATED/OBSERVING。
- 下一轮验收：自然冷建同时记录 pool acquire、driver fetch bytes/deserialize、response bytes、cache hit，
  并在尖峰同秒保存 PgBouncer pool/server socket、route 和 PostgreSQL activity；暖请求目录 SQL 必须为 0。

### 外部调用

- YouTube 00:03 channel sync 为 49/51，01:00 video sync 处理 49 个来源且 errors=2，仍精确是 id 21/56；
  第 19 窗 OPEN/REPEATED/WAITING_MANUAL_CONFIG。相较上一窗没有额外瞬时失败，是改善，不是闭环。
- B站情报本窗因上轮重启起点为 07:19，共 23/23 周期、161/161 competitor 成功；P95/max
  16.356/17.333 秒，较上一窗 +8.6%/+10.4%，无慢 SQL。网络根因继续 OBSERVING。
- 两个 charging-options 502 后续恢复为 4 成功/2 失败，属于 credential/外部服务，不是数据库失败。
- upload-only 4/4 成功且无 21173/21523，是定时余量问题的一个绿窗；历史复发仍 OPEN。
- ZSXQ 1059 经四轮有界重试最终 25/25 selected/uploaded/submitted/verified，durable run completed；没有删除
  原始错误或用假成功掩盖瞬时失败，下一轮继续观察同码与最终失败率。

## 验证与未捕获边界

- 运行态：当前 health、overview、topics/local、tabs、sync runs、scheduler status 均真实 200；当前数据库
  无 waiting lock，实时 activity 仅短事务/idle ClientRead。
- 定向回归 90/90、完整 unit 1,740/1,740 通过；`compileall`、Celery revoke 边界（326 个 Python 文件）、
  worker app factory 边界（28 个文件）、Bug strict、recurrence JSON 与 `git diff --check` 全部通过。完整 unit
  首次在受限沙箱内仅因 34 个 loopback bind `PermissionError` 未通过，允许本机临时端口后同一命令全绿。
- 本轮无代码变更，因此没有以重启制造风险；此前加载的 actor 审计由本窗真实误关样本验证生效。
- 固定窗没有重点 ZSXQ 页面自然流量，`pg_stat_statements` 不可用，HTTP 只关联到 >=1s 慢 SQL；亚秒 N+1、
  驱动 fetch 内部阶段和窗口外用户体验不能据此判定未发生。
- Celery 日志 107 个 completed/0 retry/0 failed 不覆盖日志外 actor 对 durable DB 的直接终态写入；必须联合
  数据库快照、路径 mtime、应用日志和拓扑证据。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `be891b5c1af362da` | executor 外 driver/fetch/PgBouncer/私网尾部，具体物理断点未拆开 | O(1) 投影、Session 外文件校验、缓存 | 3 次/24.109s；executor 5.182ms；hold 非 SQL 352ms | 是 | 增加分段 telemetry；不得盲目索引/加 timeout | 冷建分段齐全、暖链 SQL=0、连续完整窗高尾消失 |
| `foreign_actor_evidence` / shared runtime | 旧 Mac 仍共享生产 DB/Redis/Celery 与本地文件路径语义 | 两类失败终态 actor 审计、mtime recurrence gate | 旧 watchdog IP 直证；1/9 误关，素材真实存在 | 是 | 人工停旧栈，或轮换凭据并隔离 namespace | 完整 24h remote/foreign/old-root 全 0 + 两类自然任务成功 |
| YouTube id 21/56 | 远端已永久无结果但本地仍启用 | 受保护停用 SQL 已生成 | 第 19 窗 channel 49/51、video errors=2 | 是 | 人工 DBeaver 执行，仅限 21/56 | 连续两个自然周期 channel 49/49、video errors=0 |
| `SCHEDULED-UPLOAD-TIME-MARGIN` | 入口五分钟校验未包含排队/上传预算 | 失败 durable 化；根因代码未修 | 本窗 upload-only 4/4 成功、无 21173 | 否（单窗） | 仍需执行前余量契约与真实定时投稿 | 定向测试 + 自然投稿 + 后续完整零复发窗 |
| ZSXQ 1059 | 外部上传瞬时失败 | 有界轮次、最终逐文件验证 | 4 轮后 25/25 verified、durable completed | 瞬时发生 | 监控最终失败率，禁止无界 retry | 下一窗最终 failed=0，1059 不形成重试风暴 |

因此本轮结论是“数据库错误面保持绿色，但两个 P0 根因仍复发”；不得表述为已解决。
