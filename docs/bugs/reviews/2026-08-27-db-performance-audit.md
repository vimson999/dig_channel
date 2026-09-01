# 2026-08-27 数据库性能与错误巡检

## 结论

本轮固定窗口为 `2026-08-26 06:02:19` 至 `2026-08-27 06:02:19`，对比窗口为前一日相同时间段。覆盖 `logs/slow_query.log`、`logs/app.log`、`logs/backend.log`、`logs/scheduler.log`、`logs/celery/*.log` 及轮转文件，并用只读 PostgreSQL/PgBouncer 现场、真实 API、worker readiness 和 health 补充运行态证据。

结论不是“全部已解决”。新增 P0 是三个 PDF 工作台在 canonical 批量 miss 后逐项补查；代码、查询预算测试、重启和三个真实 API 已完成，状态为 **RUNTIME_REPLACED / OBSERVING**。续轮又完成 Bilibili competitor 宽行读取的窄投影 + 批量条件更新替换，并以真实 6/6 同步验收，状态同样为 **RUNTIME_REPLACED / OBSERVING**。远端 PostgreSQL/PgBouncer 网络长尾和 Celery status 冷控制面仍为 **OPEN/OBSERVING**。本轮没有执行生产 DDL，没有删除字段或原始 JSON。

## 与上一轮的全链路基线对比

| 指标 | 上一轮 | 本轮 | 变化与判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 273 / 742.191s | 278 / 772.543s | 数量 +1.8%，累计 +4.1%，恶化 |
| 应用慢 SQL P50/P95/max | 1.747/8.590/19.738s | 1.691/8.631/18.455s | P50 -3.2%，P95 +0.5%，max -6.5% |
| HTTP 请求/累计 | 1157 / 815.026s | 1073 / 1079.468s | 请求 -7.3%，累计 +32.4%，恶化 |
| HTTP P50/P95/max | —/3.636/26.030s | 0.004/5.334/32.365s | P95 +46.7%，max +24.3% |
| HTTP 业务失败 | 4 | 0 | 改善；唯一失败为 11ms 的 favicon 扫描 |
| Session 长持有 | 37 | 60 | +62.2%，恶化 |
| Session P95/max | 19.810/25.082s | 27.487/32.329s | +38.8%/+28.9%，恶化 |
| SQL/非 SQL 持有累计 | 387.915/22.027s | 691.744/32.818s | +78.3%/+49.0%，恶化 |
| PgBouncer 去重 connect/query timeout | 9/1 | 10/6 | query timeout 反复且恶化 |
| PgBouncer wait P95/max | 106.70/1126.59ms | 81.70/2021.05ms | P95 改善 23.4%，最大值恶化 79.4% |
| Celery success/retry/fail | 230/13/0 | 259/0/0 | 重试清零，改善 |
| Celery 成功任务累计 | 7624.343s | 7389.626s | -3.1% |

慢 SQL 共 287 条：应用 278 条、一次性/诊断 9 条；全体 P50/P95/max 为 1.691/8.699/18.455 秒。没有 statement timeout、lock timeout、deadlock、connection timeout 或 idle in transaction 日志；有 5 次 server closed。慢 SQL 涉表数：`publication_tasks` 118、`zsxq_topics` 18、`zsxq_attachments` 18、`zsxq_sync_runs` 2、`zsxq_topic_comments`/`external_documents` 0。未捕获不等于未发生，尤其缺少 `pg_stat_statements`，无法还原 PostgreSQL 全历史 executor 分布。

## 页面、API、后台任务与 SQL 关联

以下按累计耗时优先，再参考 P95、最大值、失败率与复发次数：

| 排名 | 页面/API/任务 | 量/成功失败 | 累计 | P95 / max | SQL/调用链判断 |
| --- | --- | --- | ---: | ---: | --- |
| 1 | report audio PDF assets | 21 / 21-0 | 275.912s | 30.849/32.365s | 20 条慢 SQL/89.171s；canonical miss N+1，已替换 |
| 2 | Celery status | 19 / 19-0 | 152.420s | 23.261/23.611s | 10 条慢 SQL/15.364s；主要为 broker inspect/网络冷控制面，OPEN |
| 3 | MP3 audio assets | 33 / 33-0 | 123.573s | 21.950/24.491s | 16 条慢 SQL/48.330s；executor 轻、网络往返长尾，OPEN |
| 4 | MP3 detail | 59 / 59-0 | 71.876s | 4.371/14.456s | 详情大字段已投影；最大值仍受连接长尾影响，OBSERVING |
| 5 | Bilibili upload POST | 9 / 9-0 | 64.689s | 25.503/25.503s | `aa2e3235` 首次 point load 21.483s server closed，rollback 后第 2 次 0.729s，业务 200；恢复有效，网络 OPEN |
| 6 | report audio jobs | 68 / 68-0 | 52.516s | 3.285/18.405s | 低频尾延迟仍与远端 DB 往返重合 |
| 7 | MP3 jobs | 108 / 108-0 | 46.807s | 3.370/5.275s | 高频列表，当前有界投影保持观察 |
| 8 | MP3 retry | 5 / 5-0 | 35.048s | 21.713/21.713s | 低频高延迟，无失败风暴 |
| 9 | content tasks list | 2 / 2-0 | 22.172s | 16.343/16.343s | 低频高延迟，网络/连接尾部 |
| 10 | platform credentials | 28 / 28-0 | 13.865s | 1.948/2.358s | 较上轮累计 -76.7%、P95 -58.4%；single-owner 改善，仍待超时态完整窗口 |

后台任务 259 成功、0 retry、0 fail；MP3 任务 41 成功、累计 562.148 秒、P95 50.086 秒、最大 158.345 秒，较上一轮累计 -45.6%、P95 -60.5%，且“起点/终点”误拒绝指纹未出现，但 DeepSeek 不可用后的完整 fallback 本窗未被真实触发，保持 OBSERVING。一次性 `backfill_content_pipeline_cleaned_assets.py` 被多次人工运行，产生 22 条 point load 慢 SQL、累计 62.896 秒及 2 次 server closed；脚本已是逐项流式、窄投影实现，本轮归类为维护流量受网络长尾放大，不误判为常驻 worker N+1。

### 三类必须单列的异常

- **低频高延迟**：MP3 retry、content tasks list、Bilibili upload；均不能用低次数掩盖 P95/最大值。
- **页面/API 很慢但没有等量慢 SQL**：Celery status、content PDF clean submit（1 次 11.958 秒、无慢 SQL）主要落在 broker/网络/连接等待；不能归因于 PostgreSQL executor 或盲加索引。
- **后台任务或连接持有久但没有前台端点**：7 条 worker hold、12 条 unknown role hold；一次性 canonical backfill 和 adhoc SQL 均独立列出。4 个 managed unmatched hold 为 MP3 task/detail，非 SQL 部分 2.85–3.94 秒，需继续按 request/task id 追踪。

## 主要 SQL 指纹与根因

1. `2a9b87d0ffb4c858`：Bilibili competitor 按 30 个 bvid 读取宽行，12 次累计 68.833 秒、P95 12.501 秒、最大 17.635 秒，调用源 `services/...:227`。旧全行更新指纹保持 0，写回已为 delta，但宽 `raw_payload` 读取与网络长尾仍反复；唯一 bvid 索引已存在，不能再盲加索引。
2. `8612d1a6b0a2b496`：一次性 canonical backfill point load，22 次累计 62.896 秒、P95 12.908 秒、最大 14.997 秒。属人工维护脚本流量；两次连接中断说明网络根因仍开口。
3. report audio PDF 初始附件查询：9 次累计 47.630 秒、P95 11.489 秒。真实 executor 冷/热仅 44.529/24.430ms，应用往返而非 executor 是主要差值。
4. MP3 load task：11 次累计 39.022 秒、P95 12.094 秒，含一次 server closed；PK executor 0.036ms，网络/PgBouncer 根因。
5. PDF canonical 单项查询 `74bec...`：6 次累计 30.635 秒、P95 16.019 秒；本轮定位为批量 miss 三态丢失造成的 N+1，已修复并完成运行态替换。
6. scheduler config：12 次累计 27.868 秒、P95 2.966 秒、最大 8.407 秒；较上一轮数量 -53.8%、累计 -51.2%、P95 -51.7%，改善但网络指纹未清零。

## 新增根因修复与运行态验收

修复前 `fd6212e6` 在 30.849 秒内执行 164 条 SQL，SQL 累计 30.224 秒；`2944ee61` 第一页执行 127 条、耗时 16.617 秒。三个 PDF payload helper 用 `canonical_asset or lookup()` 把批量查询的明确 miss 误当成未查询，导致缺失项逐个补查。

修复在研报音频、基础文案物料和 Top10 三入口引入私有 sentinel，只有未传参才兼容单项查询；显式 `None` 保留“已知缺失”语义。TDD 红灯实际捕获 11 条/3 条意外 SELECT；修复后定向测试 4 项通过。

重启前门禁确认无不可中断任务；标准重启后 backend PID 49814、health healthy，17 个 worker ready，active/reserved 为 0。真实研报音频第一页 `73859938` 1.533 秒、7 条 SQL、完整 10 项，相比旧第一页查询 -94.5%、耗时 -90.8%；基础文案 `7ed07ba6` 1.141 秒，Top10 `481426bf` 1.933 秒，均 200 且完整 10 项。修复记录见 `docs/bugs/2026-08-27-pdf-canonical-miss-n-plus-one.md`。由于尚未经过后续完整窗口，状态只能是 RUNTIME_REPLACED / OBSERVING。

续轮针对 Bilibili 指纹 `2a9b87d0ffb4c858` 落地窄读：identity SELECT 只取 `id,bvid`，既有行每页分别执行一次标量投影和一次 raw JSON 的 `UPDATE ... FROM (VALUES ...)`，二者均以 `IS DISTINCT FROM` 避免无变化写回，JSON 显式 cast 为 JSONB。安全门和标准重启完成后，新 backend PID 70203、scheduler PID 70795；真实 `scheduler_once` 为 6/6、180 条、0 失败、11.702 秒，相比 12:11 同规模旧代码 18.802 秒下降 37.8%。目标表 SQL 从 6 SELECT + 66 UPDATE + 1 INSERT 收敛至 6 SELECT + 12 UPDATE + 2 INSERT，新 SELECT/UPDATE 最大 239.46/180.02ms，验收窗口没有新 Bilibili 慢 SQL。两条新视频只读抽检均保留 38 个来源字段和全部核心审计键。修复记录见 `docs/bugs/2026-08-19-bilibili-intelligence-duplicate-scheduler-write-amplification.md`；后续完整窗口前仍为 RUNTIME_REPLACED / OBSERVING。

## 专项链路复核

- `zsxq-insights overview`、topics/local、topics/local/tabs、sync/status 在固定窗口内没有自然请求，明确标为**未观察到流量**，不能写成无问题。
- 重启后的只读/真实探针：Insights 冷请求 `c2beca75` 9.603 秒，主 DB hold 5.744 秒、SQL 5.704 秒；随后 cache hit `d1b861cd` 0.091 秒。数据库 executor 现场 35.378ms，冷端到端差值仍指向远端网络/池；topics/local 0.601 秒、tabs 1.457 秒、sync/status 0.204 秒，均 200。
- platform credentials 28 次累计 13.865 秒，较上一轮明显改善；隐藏抖音/视频号历史仍为 0。但本窗没有“首请求超过 15 秒”的真实场景，single-owner v2 不能 CLOSED。
- Celery status 重启后首次约 16.5 秒，随后缓存命中 1–3ms；broker healthy 不能否定固定窗口 152.420 秒长尾。

## 表规模、扫描与索引证据

只读现场大小约为：`zsxq_topics` 129MB、`publication_tasks` 126MB、`zsxq_topic_comments` 118MB、`zsxq_attachments` 78MB、`zsxq_sync_runs` 37MB、`external_documents` 7MB。索引命中率分别为 topics 99.59%、publication 99.77%、comments 99.91%、attachments 99.95%、sync 99.93%、external 98.80%。目标表没有同定义重复索引。

写放大仍需关注：comments 237,197 updates（HOT 70.46%）；topics 174,041（HOT 0.71%）；publication 239,417（HOT 1.29%）；sync 111,710（HOT 0.33%）；attachments 264,485（HOT 0.12%）；external 6,265（HOT 0%）。低 HOT 并不自动证明应删索引，必须结合完整周期写入与查询语义。

代表 executor：publication PK 0.033–0.036ms、system config 0.023ms、publisher list 0.070ms、content list 0.183ms、sync runs 1.520ms、Insights 35.378ms；PDF 列表热 24.430ms，canonical batch 热 0.317ms。应用秒级/十秒级与这些值不匹配，因此当前主要风险是远端连接/网络和应用往返，不是 executor 缺索引。

本轮不执行 CREATE/DROP INDEX。现有人工脚本仍为：

- `docs/sql/bilibili_competitor_bvid_index_cleanup_dbeaver_20260819.sql`：Bilibili 重叠索引维护，需 DBeaver 逐条执行、观察锁与回滚。
- `docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql`：schema readiness 仍提示 `20260724_comment_hash_indexes` 未应用且存在等价索引，风险更高，只能在维护窗人工执行。

两者都已有 `lock_timeout`、`statement_timeout`、验证和回滚说明；在没有新的 `pg_stat_user_indexes` 周期证据前，不扩大 DDL 范围。

## PostgreSQL、PgBouncer、网络与 Session

固定窗口 PgBouncer 原始 connect/query timeout 为 10/18，去重为 10/6；wait P50/P95/max 27.20/81.70/2021.05ms，query P50/P95/max 53.81/148.08/1144.66ms。现场 `SHOW POOLS` 为 transaction 模式、client active/waiting 0/0、6 个 server used、server login 0、maxwait 0；6 条 TLS 1.3 server 都从 `100.96.3.12` 连接 `100.92.99.39:5432`。这是健康瞬时快照，不能抵消窗口超时证据。

60 条 Session 长持有里 web/worker/unknown 为 41/7/12；SQL 累计 691.744 秒、非 SQL 32.818 秒。没有当前 waiting lock 或 idle in transaction；数据库硬化仍为 statement 60 秒、lock 5 秒、idle-in-transaction 60 秒。未安装 `pg_stat_statements` 是本轮 executor 归因边界。

## 测试与门禁

- 定向 TDD：PDF 旧实现 2 项失败，修复后 4 项通过；Bilibili 旧 identity SELECT 的 2 项契约测试按预期失败，修复后 Bilibili 切片 23 项通过。
- 完整 unit：首次缺少项目 `PYTHONPATH` 时收集失败；加上 `PYTHONPATH=src/backend` 后，沙箱内 34 项因禁止绑定 `127.0.0.1` 临时端口而统一报 `PermissionError`，其余 1384 项通过。允许 loopback 后以同一命令重跑；加入 Bilibili 回归后最终为 1411 passed、3 skipped、305 subtests passed，无业务断言失败。
- `python -m compileall -q src/backend`、`git diff --check`、`jq empty docs/bugs/recurrence-registry.json` 均 0 退出；`review_bug_records.py --strict` 校验 173 条记录全部归类、回顾周期有效。
- 真实 runtime：安全重启、backend health、Celery ready、三个 PDF API 与四个 ZSXQ 专项探针均已执行。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| PDF canonical miss 单项补查 `74bec...` | 批量 miss 与未查询都用 `None`，逐项 fallback | 三入口 sentinel + SQL 预算测试 | 127→7 SQL；16.617→1.533s；另两入口 1.141/1.933s | 新增，同簇复发 | OBSERVING；应用负责完整窗口复核 | 三入口单项指纹 0、每页 O(1)、字段完整、P95 持续回落 |
| PgBouncer connect/query timeout | 远端链路/池尾延迟；executor 远低于应用耗时 | 既有短事务、重试恢复与诊断 | 当前 pool 健康；窗口仍 10/6 去重 timeout | 是 | OPEN；网络/DB 管理侧需保留 route/source/timeout 证据 | 完整窗 timeout 0，冷请求 executor/端到端差值收敛 |
| Celery status 冷控制面 | broker inspect 与网络等待，不是 SQL executor | 既有缓存/single-flight/session release | 热缓存 1–3ms，窗 P95 23.261s | 是 | OPEN；继续按冷/热与 request id 拆分 | 冷请求 P95 收敛、无跨 inspect Session hold |
| Bilibili competitor 宽行读取 `2a9b...` | 为比较变化而读回 `raw_payload`，叠加远端往返 | `id,bvid` 窄读 + 两类 `UPDATE ... FROM VALUES` 条件更新 + SQL 契约测试 | 6/6、180 条、11.702s；66→12 UPDATE；SELECT/UPDATE max 239.46/180.02ms；raw 38 键完整 | 本窗曾复发；新 PID 后未见 | OBSERVING；完整窗口、网络长尾和人工重叠索引维护仍待复核 | 新 PID 后宽读指纹 0、raw-only change 正确、无慢批量 SQL；DDL 另行人工验收 |
| MP3 credential second owner | 首请求超时后失败态未共享 | v2 single-owner 已在前轮加载 | 本窗 credentials P95 1.948s，隐藏历史 0 | 未捕获超时态 | OBSERVING | 真实 >15s 场景仍最多 1 owner，无隐藏历史 |
| MP3 DS 起终点误拒绝 | 语义词被严格结构规划误判 | 解析/规划兼容已落地 | 41 success、0 retry/fail，本窗无该指纹 | 未见 | OBSERVING | 真实 DS 不可用 fallback 完成且后续完整窗无复发 |

## 下一轮明确验收计划

1. 首先按三个 PDF API、canonical 单项指纹和 request id 复核，要求完整窗口每页查询 O(1)、结果字段/数量完整、P95 与 Session hold 不反弹；满足后才将新记录 CLOSED。
2. 复核 PgBouncer 去重 connect/query timeout、server closed、source address、pool wait 与相同请求 executor；任一继续出现都保持 OPEN，并追到远端 route/PgBouncer/PostgreSQL 边界。
3. 对 Celery status 分冷/热统计，检查鉴权 Session 是否在 broker inspect 前释放；不能用热缓存掩盖冷请求。
4. 对 Bilibili competitor 新 PID 后的完整窗口优先复核：旧宽读和逐行 UPDATE 指纹必须为 0，批量 SQL P95/最大值不得反弹，raw-only change 与新建行审计 JSON 必须完整；人工索引脚本执行前后分别保存 pg_stat 与 EXPLAIN。
5. 等自然流量覆盖 ZSXQ insights/topics/tabs/sync status；若仍无流量，继续明确“未观察”，不得宣称未复发。
6. 继续检查超大分页、N+1、大字段、重复聚合、缓存失效、频繁轮询、外部 I/O 跨 Session 和失败重投；新增问题必须进入 Bug 记录与 recurrence registry。
