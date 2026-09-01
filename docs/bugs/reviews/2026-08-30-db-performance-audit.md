# 2026-08-30 数据库性能与错误巡检

## 结论

本轮不能标记“已解决”。固定 24 小时窗口（2026-08-29 06:02:19 至 2026-08-30
06:02:19）中，应用慢 SQL 的数量与累计耗时下降，但 P95 恶化；最终 backend
`2026-08-29 19:06:43` 之后的 10.9 小时没有 PgBouncer 六类错误，通知 outbox 也没有重试或
terminal failure，但这两个窗口都尚不足以关闭复发键。

本轮最重要的反复证据来自真实 ZSXQ Insights 冷请求 `21b8b432`：端点 22.668 秒，三段 SQL
合计 19.597 秒，其中当前页 48 行详情抓取 13.476 秒；同形 SQL 的生产
`EXPLAIN (ANALYZE, BUFFERS)` 仅 1.069 毫秒。根因是冷缓存下多个远端 PostgreSQL/PgBouncer
往返的尾延迟叠加，不是 executor、锁等待或缺索引。现有“全集轻投影 + 当前页详情 + 明确
Session release + fresh/stale cache”代码仍生效，热请求为 0.81 毫秒，因此本轮没有制造无证据 DDL，
也没有用超时、重试或删字段掩盖问题。

## 固定窗口与上一轮对比

| 指标 | 2026-08-29 基线 | 本轮固定 24h | 变化 | 判定 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL 数 | 215 | 166 | -22.8% | 改善 |
| 应用慢 SQL 累计耗时 | 574.128s | 466.080s | -18.8% | 改善 |
| SQL P50 | 1.678s | 1.602s | -4.5% | 改善 |
| SQL P95 | 7.434s | 10.486s | +41.1% | **恶化** |
| SQL 最大值 | 19.924s | 19.576s | -1.7% | 基本持平 |
| HTTP 请求 | 587 | 647 | +10.2% | 流量上升 |
| HTTP 累计耗时 | 466.339s | 504.712s | +8.2% | 恶化 |
| HTTP P95 / 最大值 | 3.352s / 22.828s | 3.500s / 28.182s | +4.4% / +23.5% | 恶化 |
| HTTP 失败 | 2/587 | 2/647 | 0.34%→0.31% | 小幅改善 |
| 长 Session 数 | 33 | 26 | -21.2% | 改善 |
| 长 Session P95 / 最大值 | 20.275s / 22.773s | 19.838s / 22.590s | -2.2% / -0.8% | 仍高 |
| Session SQL / 非 SQL 持有 | 304.950s / 47.997s | 272.313s / 12.548s | -10.7% / -73.9% | 边界明显改善 |
| PgBouncer 去重 connect/query timeout | 10 / 2 | 5 / 1 | -50% / -50% | 改善但仍复发 |
| PgBouncer 建连率 | 72.333/h | 36.333/h | -49.8% | 改善 |

窗口内没有 statement timeout、lock timeout、deadlock、idle in transaction 或应用 SQL error。
“未捕获”不能解释成“未发生”：日志只能覆盖当前托管进程，无法直接证明 PostgreSQL 服务端等待、
公网/Tailscale 每跳延迟或用户未打开的页面。

## 主要问题排序

排序优先使用累计耗时、P95/最大值、失败率、页面影响和复发次数，而非仅看出现次数。

1. **远端数据库/PgBouncer 网络长尾，OPEN/REPEATED**：慢 SQL 累计 466.080 秒、P95
   10.486 秒；固定窗仍有 5/1 个 connect/query timeout。最终进程后窗虽六类错误为零，却仍有
   18 行 `system_configs` 查询 13.443 秒。真实 executor 0.043 毫秒，证实不是表扫描成本。
2. **MP3 audio-assets 与相关控制面，OBSERVING**：60 次请求累计 136.117 秒、P95 13.260 秒、
   最大 23.347 秒；但最终代码窗口真实冷目录 3.009 秒/SQL 1.908 秒，暖目录 2.73 毫秒/0 SQL，
   jobs 235.75 毫秒、精确 runtime status 263.34 毫秒。代码级失效候选扫描与全量 Celery 状态
   已替换，剩余尾部与同一网络簇一致。
3. **后台通知旧窗口失败风暴，RUNTIME_REPLACED/OBSERVING**：混合窗口
   workflow_notify 157 success/34 retry/9 fail，较上一窗口 116/107/29 明显改善；最终进程窗口
   17 次任务、0 retry/0 fail。数据库有 17 条新 delivery，全部 sent、attempt_count=1，provider
   closed；仍缺完整新代码 24 小时、受控临时故障及邮箱回执。
4. **Insights 冷缓存往返，OPEN/REPEATED**：巡检真实冷请求 22.668 秒、热请求 0.81 毫秒；
   executor 1.069 毫秒，Session 持有 13.515 秒完全对应详情 SQL，非 SQL 仅 38.53 毫秒。
5. **低频高延迟工作流端点**：full Celery status 6 次/累计 56.615 秒/P95 27.279 秒，
   continue video 3 次/累计 32.241 秒/最大 28.182 秒，Bilibili upload 6 次/累计 35.800 秒/
   P95 17.900 秒；这些端点在固定窗仍需按调用链继续观察，不能被 MP3/ZSXQ 专项掩盖。

## 页面/API/后台任务与 SQL 关联

### 固定 24 小时端点

| 页面或端点 | 请求/成败 | 累计耗时 | P95 / 最大值 | SQL/调用链判断 |
| --- | ---: | ---: | ---: | --- |
| GET `/api/mp3-video-workflows/audio-assets` | 60/60 成功 | 136.117s | 13.260s / 23.347s | canonical catalog 与网络尾部；非缺索引 |
| GET full Celery status | 6/6 成功 | 56.615s | 27.279s / 28.182s | broker inspect/控制面等待，无匹配慢 SQL |
| Bilibili upload | 6/6 成功 | 35.800s | 17.900s | 外部 I/O/任务调用链 |
| GET MP3 jobs | 31/31 成功 | 35.037s | 2.355s / 16.918s | 个别 trivial SQL 同受远端往返放大 |
| continue full video | 3/3，2 成功 1 失败 | 32.241s | 28.182s | 低频高延迟，需下轮按 request id 继续追 |
| cover/background regenerate | 各 3 次 | 24.909s / 23.419s | 15.636s / 9.902s | 外部渲染调用链；无索引证据 |
| Bilibili intelligence overview | 6/6 成功 | 10.474s | 6.746s | 继续观察缓存与远端往返 |

### 本轮巡检真实端到端验收

- MP3 页面成功显示 worker 正常、10 个可用音频、真实字段与历史“编排 1 次/成功 1”。冷目录
  `450b6362` 为 3.009 秒（canonical SQL 1.908 秒），同条件暖请求 2.73 毫秒/0 SQL。
- ZSXQ Lab 真实登录页面发现 4 个本地星球；选择生财有术后成功加载 17 个 tabs、同步历史与
  实际主题卡片。四次 `/topics/local` 全成功，P95/最大 1.415 秒；两次 `/topics/local/tabs`
  P95/最大 0.288 秒；两次 `/sync/runs` 最大 0.977 秒。未见慢 SQL、长 Session 或错误。
- `/system-sync-scheduler/status` 三次全成功，P95/最大 1.970 秒，其中慢次没有匹配慢 SQL，归入
  PgBouncer/网络或进程状态读取等待，不误报为数据库 executor 问题。
- Insights 冷/热同一页面均返回完整 419 个主题与当前页字段。冷 `21b8b432` 为 22.668 秒；
  热 `29c7ab66` 为 0.81 毫秒，证明缓存生效但冷 miss 没有闭环。
- 新窗口 workflow_notify 1 次成功、0 retry/0 fail；`/health` healthy。没有重启，因为本轮没有
  生产代码变更，也没有必要引入一次新的安全窗口风险。

### 三类必须单列的异常

- **低频高延迟**：Insights 冷请求、continue full video、Bilibili upload 与 cover regenerate。
- **页面/API 很慢但无匹配慢 SQL**：full Celery status；本轮真实 scheduler status 1.970 秒、
  groups/local 1.443 秒、system-sync batches 1.153 秒。
- **后台任务/连接持有很久但无前台端点**：固定窗 26 个长 Session 中 5 个 origin 未知、2 个
  worker；通知旧失败窗为后台外部 I/O 风暴。后窗没有未关联长 Session，但这不代表自然任务均已覆盖。

## 表规模、索引与 EXPLAIN

| 表 | 总大小 | live/dead | seq/idx scan | 写放大观察 |
| --- | ---: | ---: | ---: | --- |
| `zsxq_topics` | 128.5MB | 12,637 / 1,584 | 3,869 / 889,820 | 174,041 update，仅 1,241 HOT |
| `publication_tasks` | 128.1MB | 12,973 / 571 | 6,928 / 3,043,169 | 较上轮 +415 update/dead；241,169 update，仅 3,096 HOT |
| `zsxq_topic_comments` | 118.0MB | 41,333 / 5,644 | 344 / 357,020 | dead 仍高，继续观察 autovacuum |
| `zsxq_attachments` | 77.7MB | 33,906 / 37 | 600 / 1,135,077 | idx 使用充分 |
| `zsxq_sync_runs` | 37.0MB | 1,062 / 46 | 241 / 337,305 | 无本轮新增热点证据 |
| `external_documents` | 6.85MB | 1,817 / 146 | 1,032 / 84,947 | 规模有限；不盲目加索引 |

重点索引证据：`ix_zsxq_topics_group_topic_preferred_synced_desc` 支撑 Insights 当前页详情，
48 行、307 shared hit、executor 1.069 毫秒；MP3 canonical catalog 726 行中匹配 280 行，executor
0.737 毫秒；`system_configs` 18 行顺序扫 executor 0.043 毫秒。以上均排除新索引为当前根治方案。
`ix_zsxq_topics_group_tab_type` 累计扫描 2,990 次，保留；低使用的 `group_type` 与内容状态索引
继续观察，不凭一次窗口 DROP。

本轮没有执行 CREATE/DROP INDEX、ALTER TABLE、回填或生产配置变更，也没有新增待 DBeaver
执行的 SQL。既有 `docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql` 已是只读验收脚本，
当前无待执行 DDL；未来任何索引动作仍须带 `lock_timeout`、`statement_timeout`、验证和回滚。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `DB-SESSION-LIFECYCLE` / PgBouncer timeout | Tailscale/PgBouncer 远端往返长尾；非 executor | server_lifetime=1800、短事务、Session release、完整网络分类 | 后窗六类错误 0；但 18 行配置 SQL 13.443s、Insights 详情 13.476s | **是** | 保持 OPEN；不得加索引或仅放宽超时 | 最终 PID 后完整 24h 六类错误 0，真实冷页与任务无 >5s 网络尾部 |
| Insights `5c010eda536e0320` | 冷 miss 下三段远端查询串行叠加；详情 fetch 长尾 | 全集轻投影、页详情上限100、显式 read scope、fresh/stale cache | 冷22.668s/热0.81ms；executor1.069ms；非SQL hold38.53ms | **是（网络层）** | OPEN/REPEATED；不删除正文、不伪造缓存 | 多次真实冷 key P95<5s，executor与应用差值受控，后续窗口不复发 |
| MP3 catalog `be891b...` | 旧失效候选扫描已替换；剩余为远端尾延迟 | authoritative snapshot、single-flight、查询后 release、runtime-only status | 冷3.009s/SQL1.908s，暖2.73ms/0SQL；历史卡片正确 | 否（代码根因）；网络仍是 | OBSERVING；自然新 pipeline 尚无 | 完整24h + 自然新投递 identity/唯一计数/无旧全量状态 |
| workflow notification storm | 每 event 重试已由 durable outbox/provider 熔断替换 | outbox、dedupe、provider lease/circuit、watchdog | DB17/17 sent且 attempt=1；后窗17任务0 retry/0 fail | 固定窗含旧复发；新代码后未见 | OBSERVING；缺故障恢复与邮箱回执 | 新代码完整24h terminal=0；临时故障单 probe、恢复全 sent、event 不重复 |
| MP3 source identity | `pipeline:<uuid>` 曾写入36字符列被截断 | owner+UUID规范化、JSON保留完整引用、历史窄兼容、未知超宽 fail closed | 真实历史编排1次/成功1；新自然任务0 | 未见新复发但未覆盖新写 | OBSERVING | 自然新任务 UUID36、feature正确、JSON完整、页面只计一次 |

## 验证与未捕获边界

定向 Insights/分析器/通知/source identity 共 98 项通过；完整 unit 1,462 项通过。完整 unit 首次在
受限沙箱中有 34 个 `127.0.0.1` 临时测试服务器绑定 `PermissionError`，获准解除该纯环境限制后同一
命令 1,462/1,462 通过。`compileall`、`git diff --check`、recurrence JSON 校验与
`review_bug_records.py --strict` 均通过；严格检查确认 178/178 条 Bug 已归类、回顾日期为
2026-08-30。运行态已有真实浏览器、API、worker、数据库只读统计与 EXPLAIN 证据；测试不能替代上述
真实证据。

仍未捕获：没有制造昂贵 MP3 自然新投递；没有注入 SMTP 故障；无法读取邮箱客户端确认收件；
最终进程只有 10.9 小时而非完整 24 小时；没有覆盖每个低频后台任务；PgBouncer 日志不能直接给出
PostgreSQL 服务端每阶段与 Tailscale 每跳耗时。因此这些项目保持 OPEN/OBSERVING。

## 下一轮明确验收计划

1. 以 2026-08-29 19:06:43 为最终进程边界跑满 24 小时，优先复核六类 PgBouncer 错误、
   `60ff7467`、Insights 三段 SQL 与所有 >5 秒无 executor 证据的请求。
2. 对 Insights 使用不同真实缓存键做至少 5 次冷/热配对，分开记录 execute/fetch/checkin，要求冷
   P95 <5 秒且热命中不掩盖冷尾部；否则继续上溯网络与连接池，不加无效索引。
3. 对自然产生的下一条 pipeline MP3 验证顶层 UUID/source_feature、审计 JSON、workflow-stats
   唯一计数；没有自然任务则继续 OBSERVING，不人为制造昂贵业务。
4. 对通知按 delivery/event id 对账，要求 terminal failure=0；若出现 provider 临时故障，验证整个
   窗口只有一个探针且恢复后 pending 全 sent。邮箱收件回执仍单列为人工边界。
5. 继续统计上述六张重点表的 live/dead、seq/idx、HOT、索引扫描增量；只有真实 SQL + 统计 +
   EXPLAIN 三证一致，才生成带风险与回滚的人工 DDL 脚本。
