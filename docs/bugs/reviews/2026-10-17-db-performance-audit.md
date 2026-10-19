# 2026-10-17 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 Asia/Shanghai `2026-10-16 06:00:00..2026-10-17 06:00:00`。本轮**不能判定全局已解决**：
内容管线统计轮询问题获得一个完整后窗，可升级为 `CLOSED / RUNTIME_ACCEPTED`；但旧分布式 actor 对当前根素材
作出 3 次假性不可用判定，Publisher 失败率 15.8%，已经再次造成用户可见失败；音频目录 SQL 的 executor 外
7.5 秒长尾、YouTube 固定无效频道和 B站外部链路根因也仍未闭环。本轮没有生产 DDL，也没有用 timeout、重试或
删字段掩盖问题。

巡检器另发现本地边界被错误当作 UTC 的验收缺陷。已按 RED→GREEN 修复、打印实际 UTC 数据库窗口，并用生产
只读快照重放；详见[专项记录](../2026-10-17-db-runtime-snapshot-timezone-window.md)。

## 与上一窗口的量化对比

| 指标 | 2026-10-16 | 本轮 | 变化/判定 |
| --- | ---: | ---: | --- |
| 慢 SQL 数量 | 48 | 16 | -66.7%，改善 |
| 慢 SQL 累计 | 214.443s | 39.521s | -81.6%，改善 |
| SQL P50 / P95 / max | 6.353 / 9.216 / 9.383s | 1.307 / 7.473 / 7.505s | P50 -79.4%，P95 -18.9%，max -20.0% |
| SQL/PgBouncer 错误 | 6 query timeout | 0 | 统计 timeout 未复发 |
| HTTP | 1,246 请求、31 失败 | 851 请求、6 失败 | 6 个均为 favicon 404；业务 5xx=0 |
| HTTP P50 / P95 / max | 3.85ms / 1.697s / 62.791s | 12.68ms / 1.552s / 60.294s | 尾部略改善 |
| connection hold | 39 | 3 | 本轮均匹配慢 SQL，max 7.681s |

未发现 statement timeout、lock timeout、deadlock、connect/query/query-wait timeout 或持久
idle-in-transaction。32 个 runtime guard 观测最大 3 连接、最大事务/idle 龄 0.486 秒、defer=0。

## 主要问题排序与调用链

排序综合累计耗时、P95/max、失败率、用户影响和复发次数，而不是只看次数。

| 排名 | 指纹/链路 | 修复前/本窗证据 | 根因与状态 |
| ---: | --- | --- | --- |
| 1 | `RUNTIME-TOPOLOGY:material-unavailable-foreign-actor` | Publisher 19 条中 3 失败，失败率 15.8%，失败壁钟 724.650s；影响 2/4 MP3 父任务 | 失败时四个当前根素材均已存在且早于 close；本地日志没有写终态的 actor，说明共享 DB/Redis 的日志外旧实例在别的文件系统视角执行门禁。`OPEN/REPEATED/WAITING_DISTRIBUTED_ISOLATION` |
| 2 | `be891b5c1af362da` audio catalog | 3 次/22.276s，P95/max 7.505s；13 次 API 中 3 次冷链，10 次热链快 | 真实 SQL 在 4,246 行中返回 1,300 行，executor 5.487ms、无锁；长尾在 driver/PgBouncer/私网 fetch，不是缺索引。`OPEN/OBSERVING` |
| 3 | YouTube 手工同步外部 I/O | video 60.294s、channel 25.973s，均无匹配慢 SQL | Google 外部链路等待；数据库不是根因。另有 id21/56 第 14 窗固定失败。`OPEN/REPEATED` |
| 4 | web submit/history | submit 8 次/47.208s、history 145 次/67.456s；submit 无慢 SQL | broker/文件与业务编排耗时；无证据加索引。`OPEN/OBSERVING` |
| 5 | `c1e57cc097d3e051` notification due-provider | 3 次/4.808s，P95 2.235s | 既有 lease 索引的 executor 0.730ms；仍是 executor 外尾部。通知 112/112 sent、max attempt=1，无失败风暴。 |
| 6 | 内容管线统计轮询 | 14 次、全 200、P95/max 579/664ms；两个旧慢指纹和 500 均为 0 | 活跃状态 tick 不再线性刷新重统计；14 次统计对 145 次 history，完整后窗接受。`CLOSED/RUNTIME_ACCEPTED` |

## 页面、API 与 SQL 关联

- 全站 110 个端点、851 个完整请求；845 成功，6 个失败均为 `/favicon.ico` 404，没有业务 5xx。
- `audio-assets` 13 次累计 24.527 秒，3 个请求关联 22.276 秒慢 SQL；缓存热命中 10 次快速，说明是冷读取尾部。
- ZSXQ `overview` 1 次 3.188 秒，关联 Insights SQL 1.797 秒；同形生产 EXPLAIN 执行 35.016ms，
  `topics/local` 1.023 秒、`tabs` 0.279 秒、`sync/status` 0.701 秒。它们仅来自窗口开头上一轮烟测，窗口内
  没有后续自然流量，必须记为覆盖边界，不能宣称未复发。
- 低频高延迟但无慢 SQL：YouTube video/channel 同步 60.294/25.973 秒、4 个素材再生成约 12 秒、
  MP3 job detail 25 次累计 16.506 秒、upload 8 次累计 12.847 秒。应继续按外部 I/O、渲染、broker 和序列化
  分段，不能误归因 PostgreSQL。
- 本轮 3 个连接长持有全部由 SQL 时间主导，SQL 22.418 秒、非 SQL 仅 0.356 秒；没有“长持有但无前台端点”
  的新样本。未捕获不等于未发生。

## 后台任务与运行拓扑

Celery 日志捕获 251 次成功、0 retry、0 failed，active 总计 8,116.360 秒。PDF clean 7 次 active
1,945.928 秒，ZSXQ publish 8 次 1,711.045 秒，Publisher render 11 次 1,780.408 秒，B站 upload-only
8 次 1,634.464 秒。与 durable 壁钟对照，PDF 约 49.9%、ZSXQ publish 34.8%、Publisher 约 36.5% 是排队/
编排/子任务等待，而不是数据库 executor；MP3 父任务约 88.1% 是子任务编排等待，不能把父壁钟当作 DB 时间。

纠正时区后的 durable snapshot：audio clean 1/1、PDF 7/7、MP3 4/4、ZSXQ publish 8/8 均完成；Publisher
16 完成、3 失败。三条失败均为 `queue_repair_blocked/required_material_unavailable`。对应 image/audio 文件
在当前根存在，mtime 早于关闭时间；本地 worker 只看到排队与最终失败，没有执行该 close 的新代码日志。
old-root=0 和当前远端 DB 会话=0 因而不能作为关闭证据，责任动作仍是停旧 Mac 全栈，或轮换 PostgreSQL/Redis
凭据并隔离 broker namespace。

## 外部任务复核

- B站情报 24/24 自然周期、168/168 competitor 成功，P95/max 14.934/14.966 秒，是完整绿窗；但此前 DNS/TUN
  失败时没有现场链路证据，暂为 `OPEN/OBSERVING`，下一次失败须同秒留 DNS、代理/TUN 与实际目标。
- YouTube 自然 channel 为 49/51，video `errors=2`，仍是 id21/56，第 14 个巡检窗口复发。受保护脚本
  [`disable_unavailable_youtube_channels_20260920.sql`](../../sql/disable_unavailable_youtube_channels_20260920.sql)
  仍待人工在 DBeaver 复核执行，随后要求 channel 49/49、video errors=0 连续两个自然周期。

## 表、索引与 EXPLAIN 证据

| 表 | 总大小 | live/dead | idx scan 占比 |
| --- | ---: | ---: | ---: |
| publication_tasks | 148MB | 14,427 / 195 | 99.83% |
| zsxq_sync_runs | 37MB | 1,062 / 46 | 99.91% |
| zsxq_topics | 129MB | 12,637 / 1,584 | 99.52% |
| zsxq_topic_comments | 118MB | 41,333 / 5,644 | 99.85% |
| zsxq_attachments | 78MB | 33,906 / 37,650 | 99.94% |
| external_documents | 6.9MB | 1,817 / 146 | 98.74% |

目标表没有完全重复索引。统计自 2026-08-08 累积且未 reset，低扫描索引不能直接解释本窗工作负载或写放大；
因此不建议 CREATE/DROP INDEX。audio catalog executor 5.487ms、due-provider 0.730ms、Insights 35.016ms、
tabs config 0.024ms，均排除缺索引。`zsxq_attachments` dead tuple 偏高，继续观察 autovacuum，而不是无人值守
VACUUM FULL/REINDEX。没有需要本轮生成的生产 DDL；唯一人工 SQL 是上面的业务停用脚本，内含验证与回滚。

## 已落地修复与验证

- 修复日快照时区语义，显式输出 UTC 数据库边界；定向测试先红后绿，生产只读回放覆盖纠正窗口。
- 将完整后窗证据写回统计轮询、网络尾部、旧运行拓扑、B站和 YouTube 记录及 recurrence registry。
- 此修复只影响离线诊断脚本，不需要重启常驻服务；因此没有调用重启安全门，也没有声称业务运行代码已替换。
- 定向快照测试 3/3 通过；受信环境以项目 `PYTHONPATH` 跑完整 unit 1,737/1,737 通过；35 个前端
  JS/CJS 合同脚本全部通过。compileall、211/211 Bug strict、registry JSON 和 `git diff --check` 均通过。
- 真实只读烟测：health 200/0.064s、overview 200/3.484s、topics/local 200/0.877s、tabs 200/0.295s、
  sync scheduler status 200/0.704s、statistics 200/0.425s。它们证明当前 backend 可用，但不替代下一自然窗口。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| stats polling/query timeout | 活动 tick 放大重统计 | 只在首屏/手工/终态刷新统计 | 完整窗 14/14 200，旧指纹/timeout=0 | 否 | 保留预算合同 | 下一窗继续 500/timeout=0 |
| foreign actor material close | 共享凭据旧实例跨文件系统判断当前路径 | 本地 fail-closed 审计已存在 | 3/19 假失败、文件实际存在 | **是** | 人工停旧栈或轮换 DB/Redis 并隔离 namespace | 完整窗 actor identity 可归因、假失败=0、自然 Publisher 成功 |
| audio catalog long tail | executor 外 fetch/网络尾部 | 缓存已有；未盲加索引 | executor 5.487ms vs app 7.505s | 是 | 保存尖峰同秒 pool/socket/route | 连续后窗 P95 回落且现场链路定位 |
| YouTube id21/56 | 永久不可用来源仍启用 | 观测详情已落地 | 第14窗 2 个固定失败 | **是** | 人工执行受保护脚本 | 连续两周期 49/49、errors=0 |
| B站 DNS/TUN | 历史外部解析链路断点未知 | 隔离及结构化结果已有 | 24/24、168/168 绿窗 | 否 | 下一故障现场取证 | 后续完整窗继续全绿或取得失败断点 |
| snapshot timezone | 本地边界未转 UTC-naive | 显式转换、输出边界、TDD | 生产只读重放命中正确窗口 | 新增并已代码闭环 | 只读诊断仍需日常核对 | 下一轮窗口三源边界一致 |

## 下一轮明确计划

1. 第一优先复核三条 Publisher 复发键；若仍出现，必须记录写终态 actor 的 hostname/deployment/PID/code identity，
   并推进凭据或 broker namespace 物理隔离，不能继续每日修同一症状。
2. 对 audio catalog 新尖峰采集同秒 PgBouncer pool、socket/route 和 DB activity；继续用 executor 对照，禁止盲加索引。
3. 复核统计 API 请求是否仍不随 10 秒 history tick 线性增长，并保持旧指纹、500、query timeout 为 0。
4. 人工处理 YouTube 21/56 后，以两个自然周期验收；B站若再失败，必须保留故障时链路快照。
5. 强制核对本地日志窗口与快照打印的 UTC 窗口；重点入口缺自然流量时继续明确“未捕获”。
