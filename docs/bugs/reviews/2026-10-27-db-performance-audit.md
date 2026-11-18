# 2026-10-27 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 2026-10-26 06:00:00 至 2026-10-27 06:00:00（Asia/Shanghai；数据库 UTC-naive 边界为
2026-10-25 22:00:00 至 2026-10-26 22:00:00）。本轮不能写成“已解决”。慢 SQL 从上一窗
14 条/17.700 秒恶化为 19 条/43.958 秒，数量 +35.7%、累计 +148.3%，P50/P95/max 从
1.114/1.910/1.960 秒升到 1.335/8.692/11.341 秒；主因是两次 MP3 音频素材目录冷建。

业务运行体出现重要改善：28/28 个目标 durable task 都是 current-root；Publisher 16/16 completed，失败率由
33.3% 降到 0，old-root、foreign actor、processing orphan 与素材不可达均为 0；当前根 PDF→ZSXQ、MP3→Publisher
均有自然成功样本。但生产 `pg_stat_activity` 仍见在线 `v9的MacBook Pro`（`100.119.238.48`）的 6 个空
`application_name` 会话。路径相同不能证明 writer actor 相同，且旧主机仍共享生产 DB/Redis/broker 凭据；因此
分布式隔离只能从“业务已复发”降为 **OPEN / REPEATED / OBSERVING / WAITING_DISTRIBUTED_ISOLATION**，不能关闭。

本轮没有发现可由兼容代码改动根治的新缺陷：音频目录既有 2,500 行上限、字段投影、single-flight、5 分钟缓存和
Session 外文件检查均有效，1481 行/854,488 bytes 的生产同形查询 executor 仅 2.869ms，而应用分别为
11.341/8.692 秒；盲目加索引、延长 TTL/timeout 或无界重试都会掩盖 driver/PgBouncer/私网 fetch 根因。本轮只更新
巡检报告、Bug 与 recurrence registry，不修改常驻业务代码、配置或 DDL，因而无需重启。

## 修复前后量化基线

| 指标 | 上一窗口 | 本窗口 | 变化与判断 |
| --- | ---: | ---: | --- |
| 慢 SQL | 14 / 17.700s | 19 / 43.958s | 数量 +35.7%，累计 +148.3%，明确恶化 |
| 慢 SQL P50 / P95 / max | 1.114 / 1.910 / 1.960s | 1.335 / 8.692 / 11.341s | +19.9% / +355.2% / +478.7% |
| HTTP | 15 完成，13 成功 | 893 完成，882 成功 | 覆盖恢复；失败含 8 个 favicon 404、2 个巡检 400、1 个外部 502 |
| HTTP P50 / P95 / max | 1.44ms / 1.956 / 3.612s | 3.62ms / 1.355 / 66.697s | P95 改善，但低频外部同步长尾明显 |
| Celery 完成/失败 | 61 / 0 | 220 / 0 | 覆盖显著恢复；durable 目标链亦全成功 |
| durable 目标任务 | 12/12 old-root | 28/28 current-root | 明显改善，但远端生产会话仍存在 |
| Publisher | 6/9 completed，失败率 33.3% | 16/16 completed，失败率 0 | 自然当前根正向证据；仍需后窗和物理隔离 |
| B站情报 | 24/24、168/168 | 24/24、168/168 | 连续全绿；P95/max 15.041/15.329s |
| PgBouncer wait/query P95 | 45.95 / 72.89ms | 51.65 / 82.76ms | +12.4% / +13.5%；八类错误仍为 0 |

## 按累计耗时、P95、最大耗时、失败率、影响与复发排序

各父子任务壁钟存在包含关系，下面只作同类排序，不跨层相加为用户等待时间。

| 排名 | 问题/复发键 | 量化证据 | 影响 | 状态 |
| ---: | --- | --- | --- | --- |
| 1 | 运行体隔离 / `RUNTIME-TOPOLOGY-RELOAD` | Publisher 16 条累计 7,025.049s、P95/max 923.986/1,034.305s，全部成功；但远端在线主机仍有 6 个 DB 会话 | 旧 actor 仍具共享生产入口，当前绿窗不能证明物理隔离 | OPEN/REPEATED/OBSERVING |
| 2 | audio catalog / `be891b5c1af362da` | 2 次/20.033s，P95/max 8.692/11.341s；HTTP P95/max 9.431/12.106s；executor 2.869ms | MP3 编排页面冷启动，5 分钟 cache miss 后复发 | OPEN/REPEATED/OBSERVING |
| 3 | MP3 workflow | 4/4 completed，累计 6,105.488s，P95/max 1,928.027/1,960.008s | 后台用户交付耗时，主要是媒体/外部编排，不是慢 SQL | OPEN/OBSERVING |
| 4 | Publisher/upload-only 外部传输 | 8+8 个任务均成功，累计 2,284.544s + 2,095.284s；一条 67 分片上传有 32 个首试失败后恢复 | 无最终失败，但分片链路不稳定且现场分类不足 | OPEN/REPEATED/OBSERVING |
| 5 | ZSXQ publish 1059 | 4/4 completed，累计 1,210.582s；4 次 publish retry + 4 次 upload failure 均有界恢复 | 延长发布链；外部 1059 仍复发 | OPEN/OBSERVING |
| 6 | Insights / `674016d39deab3f5` | 固定窗 SQL 2.070s、executor 41.623ms；窗后真实 overview 4.228s、SQL 1.962s | overview 冷态尾延迟 | OPEN/REPEATED/OBSERVING |
| 7 | YouTube 21/56 | channel 49/51、video errors=2，第 24 窗 | 每日确定失败与告警噪声 | OPEN/REPEATED/WAITING_MANUAL_CONFIG |

## 页面、API、后台任务与 SQL 关联

### SQL、Session、PgBouncer 与 executor

- 19 条应用慢 SQL 累计 43.958 秒；`publication_tasks` 10 条、`zsxq_topics` 1 条。应用 SQL error、statement/
  lock/connection timeout、deadlock、server close 与 waiting lock 均为 0。
- 主要指纹：audio catalog 2/20.033s；MP3 `load_task` 2/2.829s；notification due 2/2.318s；all-tabs
  config 2/2.275s；Publisher processing scan 1/2.199s；stick-figure repair 1/2.120s；Insights 1/2.070s。
- 两次长 connection hold P50/P95/max 8.749/11.426/11.426s，均属于 web audio catalog；匹配 SQL 合计
  20.033s，Session 内非 SQL 仅 141.27ms。26 次 runtime guard idle observation 最大连接数 2、事务龄
  0.343s、idle 0.307s、defer 0，不构成 durable idle-in-transaction。
- PgBouncer 1,440 样本、288 次连接、12/h，来源均为当前 Mac mini `100.96.3.12`；proxy fallback 与八类错误为
  0。wait P50/P95/max 27.23/51.65/335.94ms，query 65.53/82.76/320.45ms。
- 生产 `EXPLAIN (ANALYZE, BUFFERS)`：publication PK 0.054ms、chart 3.056ms、all-tabs 0.024ms、Publisher
  list 0.077ms、content list 0.246ms、recent wide/narrow 0.024/0.028ms、sync runs 1.528ms、Insights
  41.623ms。audio catalog 为 Seq Scan + sort、1481 行/854,488 bytes、504 shared hit、无物理读，executor
  2.869ms；应用最大约为 executor 的 3,953 倍。小表取 1481 个候选的顺扫符合查询形态，不支持索引 DDL。

### 页面与 API

固定窗 893 个完成请求、122 个端点，882 成功、11 失败，累计 363.831 秒，P50/P95/max
3.62ms/1.355s/66.697s。失败由 favicon 404 八次、巡检遗漏必填 group 的 topics/local 与 tabs 400 各一次，及
charging-options 外部 502 一次组成；后者 request `7f73001b` 为 584.76ms，随后同凭据 request `36c00e58`
200/1.832s，属于外部调用短暂失败，不是数据库错误。

- `POST /api/videos/sync` 1/1、66.697s；`POST /api/channels/sync/all` 1/1、21.425s，均无匹配慢 SQL。
- `GET .../audio-assets` 5/5、累计 21.557s，P95/max 12.106s；两个 cold request `aa2987f3`/`2acd5aa0`
  分别关联 SQL 11.341/8.692s，1481 行；其余 cache hit 快速。
- `POST .../content-pipeline/web-submit` 4/4、累计 29.049s、max 7.533s；`GET .../web-submissions`
  40/40、累计 19.205s、P95 582ms、max 1.598s，均无匹配慢 SQL。
- MP3 artifact regeneration/jobs/uploads、charging/seasons、B站情报 dashboard/data/rankings 与 Celery status
  也有 1.8–3.7s 样本但无匹配 >=1s SQL，须继续按外部 I/O、broker、文件和序列化分段。

三类单列异常：低频高延迟为 videos sync 66.697s、channels sync 21.425s、audio-assets 12.106s；页面/API
很慢但无慢 SQL为同步、web-submit、submissions、MP3 artifact 与 Celery status；后台任务很久但无前台端点为
MP3 max 1,960.008s、Publisher max 1,034.305s、upload-only max 385.992s、ZSXQ publish max 418.428s。
没有匹配慢 SQL只说明当前阈值未捕获数据库段，不能误报为没有问题。

窗后真实只读 E2E：health healthy；overview 200/4.228s（SQL 1.962s）、topics/local 200/0.663s、tabs
200/0.221s、sync runs 200/0.326s、system scheduler 200/约 0.644s、scheduler 200/0.004s；Celery status
首次 200/2.073s、热态 7ms。Celery `broker_ok=true`，17/17 service running、paused=0、queue=0、
topology conflict=0。

### 后台任务、外部调用与运行体

- Celery 日志共 220 completed、0 retry task、0 failed task，累计 6,460s：Publisher pipeline 8 次/
  2,284.544s，upload-only 8 次/2,095.284s，ZSXQ publish 4 次/792.197s，MP3 process 80 次/
  714.154s，notification 116 次/304.010s，audio clean 4 次/270.264s。
- 生产 durable：audio clean 4/4、ZSXQ publish 4/4、MP3 workflow 4/4、Publisher 16/16 completed；28/28
  current-root，old-root/foreign actor/processing orphan/material unavailable/upload missing 均 0。notification
  86/86 sent、attempt max=1。
- 当前数据库会话除当前 Mac mini 与服务端 gunicorn 外，仍有 `100.119.238.48` 六个空 `application_name`
  会话；Tailscale 将其映射到在线 `v9的MacBook Pro`。本窗本地日志能覆盖全部目标 durable task，未证明其参与
  写入，但也没有凭据隔离证据，故只能观察不能关闭。
- B站情报 24/24 周期、168/168 competitor 成功，累计 319.585s，P50/P95/max 13.281/15.041/15.329s。
- YouTube channel 51 个来源中 49 成功、21/56 失败，24.32s；video 处理 49 个可枚举来源、新增 28、更新
  57、errors=2，仍精确为 21/56。
- B站 upload-only 8/8 成功，21173/21523/分片重试耗尽均为 0；但一条 67 分片上传有 32 个 attempt=1
  失败后恢复。现有日志仍缺响应分类、目标/CDN、upload session 与阶段耗时，不能把最终成功当作根因闭环。
- ZSXQ 四条自然发布最终全成功，但出现 8 个实际外部失败调用：publish 1059 四次、upload 1059 四次，均在
  有界策略内恢复。system ZSXQ sync scheduler 本窗禁用；本轮只覆盖当前根清洗→发布链，不冒充系统同步验收。

## 程序模式与表/索引复核

未发现新的 N+1、超大分页、重复去重聚合、缓存失效风暴或 Session 跨外部 I/O。audio catalog 每次有界返回
1481 行，字段投影与 Session 外文件检查有效；问题在 cache miss 后的远端 fetch 尾部，不以扩大缓存 TTL 隐藏。
Insights 继续保持轻摘要 + 当前页详情，没有恢复全量正文跨网络；publication 原始 JSON/TOAST 审计字段完整保留。

| 表 | 总大小 | live/dead | idx scan share | 判断 |
| --- | ---: | ---: | ---: | --- |
| `publication_tasks` | 152MB（18/120/14MB table/toast/index） | 14,758 / 1,342 | 99.84% | 10 个慢样本均非 executor/索引瓶颈 |
| `zsxq_topics` | 133MB | 13,981 / 1,665 | 99.52% | Insights executor 41.623ms |
| `zsxq_topic_comments` | 119MB | 45,884 / 6,093 | 99.85% | 无本窗慢指纹 |
| `zsxq_attachments` | 79MB | 35,988 / 75 | 99.94% | 无本窗慢指纹 |
| `zsxq_sync_runs` | 37MB | 1,077 / 61 | 99.90% | 代表查询 1.528ms |
| `external_documents` | 7.0MB | 1,817 / 146 | 98.73% | 无本窗慢指纹 |

没有 exact duplicate index；低扫描候选仍为 attachments PDF group-tab 约 5MB（scan=1）和 topics group/type
约 3.8MB（scan=1），但统计自 2026-08-08 server start 累计，缺少 reset-window 写放大与反事实执行计划证据。
`pg_stat_statements` 不可用，当前无 waiting lock。**本轮不生成或执行 CREATE/DROP INDEX、ALTER、回填或生产
配置脚本。**

## 已落地修复、测试、运行态验证与人工动作

- 本轮没有新业务代码修复；已有 audio catalog 的投影、上限、single-flight、缓存与 Session 边界继续生效，
  当前证据要求先补 driver/network 分段，不能用伪修复掩盖。
- 真实运行态已覆盖当前根四类 durable 链路、893 个 HTTP、220 个 Celery 完成事件和重点只读 API；这是本窗
  正向证据，不是未来后窗，也不能替代远端 actor 隔离。
- 本轮只改文档与 registry，无常驻代码变更，不需要 `check-restart-safety.py` 或重启；没有绕过安全门。
- 数据库诊断定向测试 42/42 通过；完整 unit 首次在受限沙箱运行 1,742 项，34 项只因禁止绑定
  `127.0.0.1` 报 `PermissionError`，允许本地 loopback 后以同一测试集合重跑 1,742/1,742 通过。
  `compileall`、Bug strict 213/213、registry JSON 与 `git diff --check` 全部通过。测试只验证代码/纪律，不替代
  自然运行态。
- **P0 人工动作：**在在线 MacBook Pro 停止并卸载所有旧 backend/Celery/Watchdog/scheduler/脚本；无法证明
  全停时，轮换 PostgreSQL/Redis 凭据并隔离 Celery namespace，同时给所有客户端设置不可伪造的
  `application_name`/deployment identity。直接断 6 个会话只会被共享凭据重连。
- YouTube 仅保留既有
  [`disable_unavailable_youtube_channels_20260920.sql`](../../sql/disable_unavailable_youtube_channels_20260920.sql)
  供 DBeaver 人工逐段复核；按脚本 lock/statement timeout、验证与回滚执行，只处理 21/56。

## 未捕获边界与根因闭环状态表

`pg_stat_statements` 不可用；应用慢日志只捕获 >=1s SQL；远端空 `application_name` 无法归因具体进程；本地日志
不能观察远端 actor；system ZSXQ sync 被禁用；B站失效档位新防线没有自然 invalid-tier 样本；分片成功恢复仍缺
响应/CDN/session 证据。这些都按未捕获或待验收处理。

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| runtime topology | 多主机共享 DB/Redis/broker，缺强 actor identity | root/actor/终态诊断与 fail-closed | 28/28 current、Publisher 16/16；仍有远端会话 6 | 风险仍在，业务本窗未复发 | 人工停旧栈或轮换凭据/namespace | 完整 24h remote/old/foreign=0，且当前根两条自然链成功 |
| audio `be891...` | executor 外 driver/PgBouncer/私网 fetch | 投影/上限/single-flight/cache/Session 外文件 I/O | 20.033s vs executor 2.869ms；两长 hold 非 SQL仅141ms | 是 | 补 pool acquire、fetch bytes/deserialize、socket/route | 冷暖自然配对；暖 SQL=0；冷链分段无秒级未知区 |
| Insights `6740...` | executor 外远程 fetch 尾部 | 轻摘要/当前页拆分 | 2.070s vs 41.623ms；窗后 4.228s | 是 | 补 bytes/driver/route 分段 | 多组自然冷暖样本且冷 P95 稳定、无全文搬运 |
| Publisher wall time | 媒体渲染/外部上传编排 | 有界阶段与 durable 状态 | 16/16成功，P95 923.986s | 未失败但耗时高 | 分段媒体/上传等待，不与父任务重复相加 | 后窗成功且阶段 P95 不恶化 |
| B站 chunk | 外部媒体分片不稳定，缺幂等续传证据 | 四次有界重试、失败保留 | 32 个首试失败均恢复，8/8 upload-only成功 | 是（未耗尽） | 保存响应分类/CDN/session/阶段耗时 | 故障可分类、无重复稿件，完整后窗无耗尽 |
| stale tier 21523 | 保存的外部选项生命周期失效 | 上传前权威校验、waiting_user_reselect | 8/8成功但无 invalid-tier 样本 | 未捕获 | 等真实失效项与用户重选 | 上传前拦截、upload=0、重选后成功、后窗21523=0 |
| ZSXQ 1059 | 外部上传/发布瞬时失败 | 有界 retry 与原错保留 | 8 次失败调用后4/4任务完成 | 是 | 保持失败分类，防无界/重复投递 | 后窗无风暴、单任务预算内且无重复发布 |
| YouTube 21/56 | 永久不可用来源仍启用 | 受保护人工 SQL 已备 | 49/51、errors=2，第24窗 | 是 | DBeaver 人工停两源 | 连续两自然周期 49/49、errors=0 |
| target indexes | 低扫描索引可能低价值 | 只读统计/EXPLAIN 门禁 | 无重复索引、热点 executor 毫秒级 | 无新增 | 不盲删；补 reset-window 反事实 | 有真实 SQL+stats+EXPLAIN 才生成 DDL 提案 |

下一轮优先复核远端会话、old/foreign actor 和当前根自然链；在 audio/Insights 下一次冷建现场同时采 pool acquire、
driver fetch bytes/deserialize、server socket、route、远端 activity 与响应 bytes；继续追踪 B站分片、ZSXQ 1059、
stale tier 与 YouTube 21/56。任何一项缺真实运行后窗时都不得标 CLOSED。
