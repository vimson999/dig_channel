# 2026-09-22 每日数据库性能与错误巡检

## 根因闭环结论

固定窗口为 **2026-09-21 06:00:00–2026-09-22 06:00:00（Asia/Shanghai）**。本轮读取上一轮报告、
Bug 记录、数据库 Session 纪律与 recurrence registry，并覆盖 `slow_query.log`、`app.log`、`backend.log`、
`scheduler.log`、全部 `celery/*.log`、独立 B站/YouTube 调度日志、PgBouncer、生产库只读统计、持久任务终态、
请求 ID、SQL origin 和进程拓扑。

本轮最重要的新增闭环是 **Publisher 反复换可见消息 ID 的真实写入者**：另一个旧 checkout 的
Celery/Watchdog 与当前项目共用 PostgreSQL、Redis、队列和重复 nodename。它运行旧代码、持续写入当前源码
已删除的 repair reason；Celery inspect 又会合并重复 nodename，导致前轮只看到一个逻辑节点。本窗 24 条
Publisher 任务中 4 条因此达到 repair limit 失败，另有 4 条完成任务仍经历旧语义换 ID。已落地运行拓扑冲突
检测和 durable repair 前 fail-closed 门禁，定向与全量测试通过，并按安全门标准重启；但重启后尚无自然
Publisher 样本和后续完整窗口，所以状态是 **OPEN/REPEATED/RUNTIME_REPLACED/OBSERVING**，不是已解决。

上一轮 ZSXQ present-empty 修复达到关闭标准：三个自然 `source_refs=[]` 发布任务完成，旧大 JSON 回退指纹为 0，
且覆盖后续完整窗口，标记 **CLOSED/RUNTIME_ACCEPTED**。B站调度器测试日志污染也经 22 个自然周期和后窗确认
未复发，标记 CLOSED。YouTube id 21/56 则继续第三次自然复发，人工配置未执行，仍 OPEN。

## 与上一轮基线比较

| 指标 | 09-21 基线 | 本轮 | 变化 | 判断 |
|---|---:|---:|---:|---|
| 应用慢 SQL 数量 | 21 | 9 | -57.1% | 改善 |
| 应用慢 SQL 累计耗时 | 30.339s | 13.349s | -56.0% | 改善 |
| 应用慢 SQL P50 | 1.268s | 1.319s | +4.0% | 中位略恶化 |
| 应用慢 SQL P95 | 1.993s | 1.964s | -1.5% | 小幅改善 |
| 应用慢 SQL最大值 | 3.508s | 1.964s | -44.0% | 改善 |
| PgBouncer 六类故障 | 0 | 0 | 持平 | 本窗未复发；历史网络根因仍观察 |
| 应用长 hold / idle in transaction | 0 / 0 | 0 / 0 | 持平 | 无 Session 跨外部 I/O 证据 |
| HTTP 请求 | 167 | 219 | +31.1% | 负载增加 |
| HTTP 累计耗时 | 58.129s | 82.606s | +42.1% | 174 次列表轮询占 72.744s |
| HTTP P50 / P95 / max | 206 / 1110 / 6426ms | 357 / 895 / 4890ms | +73.1% / -19.3% / -23.9% | 中位恶化、尾部改善 |
| B站情报自然周期 | 23/23 | 22/22 | 持续正常 | 每轮均 6/6、失败 0 |
| YouTube channel/video | 各失败 2 | 各失败 2 | 反复 | 仍为 id 21/56 |
| Publisher repair-limit 失败 | 0 | 4 | 严重复发 | 跨 checkout 旧 actor 已定位 |

HTTP 失败 13 条中，8 条是 `/channels`、`index.html`、`login.html`、`access/<redacted>` 启动探针；
5 条 contact 失败均发生在旧 backend 的 18:01–18:08 404/405 探测。18:10 重启后两次真实 POST 均为
202，服务端 1.223/1.132 秒，邮件 durable delivery 约 3.17/2.96 秒，没有持续 5xx 风暴。

## 主要问题排序

排序综合累计耗时、P95/max、失败率、受影响入口/用户与复发次数，不按出现次数单排。

| 排名 | 问题 | 累计 / P95 / max | 失败与影响 | 根因与状态 |
|---:|---|---|---|---|
| 1 | Publisher 可见消息反复换 ID | repair 约每 3m13s；相关慢 SQL 3.638s | 24 条中 4 条 repair-limit 失败；MP3 用户工作流受影响 | 旧 checkout 双栈共享 broker/DB/nodename；门禁已运行替换，OPEN/REPEATED/OBSERVING |
| 2 | 内容管线列表轮询与提交 | 列表 72.744s，P95/max 871/2163ms；提交 4.890s | 175/175 成功；页面等待显著 | 174 次 active-only 轮询；提交慢但无慢 SQL，外部/文件/broker 阶段，OBSERVING |
| 3 | PDF clean / ZSXQ publish 长后台任务 | 901.195s / 806.215s | 各 1/1 成功 | 清洗与远端上传外部 I/O，不是 DB executor；无 Session 长 hold |
| 4 | ZSXQ Insights 聚合 SQL | 3.505s；P95/max 1.836s | 2 次后台聚合；overview 页面 741ms 无匹配慢 SQL | executor/锁/索引无恶化证据，主要为远程往返与正则聚合，OBSERVING |
| 5 | YouTube 失效来源 | channel 14.18s；video 43.20s | 每条链固定失败 2 个 | 远端不可用而本地仍 active，OPEN/REPEATED/WAITING_MANUAL_CONFIG |
| 6 | contact 旧进程探测 | 2.850s；P95/max 1.223s | 7 次中 5 次旧进程 404/405 | 18:10 后 2/2 成功，RECOVERED/OBSERVING |

## SQL 指纹、request id 与调用链

| 指纹 / SQL origin | 次数 | 累计 | P95/max | 关联与根因判断 |
|---|---:|---:|---:|---|
| `674016d39deab3f5` `zsxq_insights_service.py:448` | 2 | 3.505s | 1.836s | Insights 聚合；无锁/timeout，PgBouncer query max 180ms，不支持 executor 卡住 |
| `f5ee1b5df0b1bd8a` Publisher terminal-child cleanup | 2 | 2.548s | 1.319s | Watchdog 后台清理；与跨 checkout 任务风暴相关，但 SQL 本身无失败 |
| `95b0e3fcd9a308f7` canonical asset update | 1 | 1.964s | 1.964s | PDF clean 产物登记；任务总耗时 901s 主体是清洗 I/O |
| `9984858fbb37f9bc` web submissions list | 1 | 1.910s | 1.910s | request `fb5b2781` 总耗时 2.163s；其余 173 次没有慢 SQL |
| `c1e57cc097d3e051` notification due drains | 1 | 1.198s | 1.198s | durable outbox 38/38 sent，无失败重试风暴 |
| `abbd31d685e07695` B站 competitor sync | 1 | 1.134s | 1.134s | 22 个自然周期 132/132 competitor 成功 |
| `93f39f2621976dbe` Publisher processing repair scan | 1 | 1.089s | 1.089s | 双栈竞争现场的一部分；根因是运行拓扑而非索引 |

`publication_tasks` 占 4 条、`zsxq_topics` 2 条，其余重点表慢 SQL 为 0。SQL error 为 0；statement、lock、
deadlock、query、connect timeout 均为 0。两个短暂 runtime observation 最大连接 3、事务龄 0.151 秒、
idle 0.123 秒，无准入 defer，不构成 idle-in-transaction 事故。

PgBouncer 1440 点：wait P50/P95/max 24.91/46.10/179.26ms，query 51.22/73.44/180.09ms；
288 次连接（12/h）全部来自 `100.96.3.12`，proxy TUN fallback 0，六类故障全 0。应用 1.1–1.96 秒慢 SQL
明显高于 PgBouncer query 统计尾部，而既有同形 executor 计划只有毫秒到 35ms；本窗没有新 DDL 依据。

## 页面/API 全量端点

共 219 个完整请求、20 个 method/path 组，206 成功、13 失败，总耗时 82.606 秒，
P50/P95/max 356.53/895.27/4889.93ms。

| method/path | 请求 | 成功/失败 | 累计 | P50/P95/max | 慢 SQL |
|---|---:|---:|---:|---:|---:|
| GET `/api/v1/content-pipeline/web-submissions` | 174 | 174/0 | 72.744s | 396/871/2163ms | 1 / 1.910s |
| POST `/api/v1/content-pipeline/web-submit` | 1 | 1/0 | 4.890s | 4890/4890/4890ms | 0 |
| POST `/api/v1/contact-messages` | 7 | 2/5 | 2.850s | 214/1223/1223ms | 0 |
| GET `/api/v1/zsxq-insights/overview` | 1 | 1/0 | 741ms | 741/741/741ms | 0 |
| POST `/api/v1/zsxq/topics/local` | 1 | 1/0 | 538ms | 538/538/538ms | 0 |
| GET `/content-pipeline-submit.html` | 2 | 2/0 | 311ms | 4/307/307ms | 0 |
| GET `/api/v1/zsxq/system-sync-scheduler/status` | 1 | 1/0 | 286ms | 286/286/286ms | 0 |
| POST `/api/v1/zsxq/topics/local/tabs` | 1 | 1/0 | 175ms | 175/175/175ms | 0 |
| GET `/js/content-pipeline-submit.js` | 2 | 2/0 | 14ms | 3/10/10ms | 0 |
| GET `/js/env.js` | 2 | 2/0 | 11ms | 5/7/7ms | 0 |
| GET `/health` | 8 | 8/0 | 11ms | 1/3/3ms | 0 |
| GET `/js/shared-navigation.js` | 2 | 2/0 | 9ms | 4/5/5ms | 0 |
| GET `/api/v1/auth/verify` | 2 | 2/0 | 7ms | 2/5/5ms | 0 |
| POST `/api/v1/zsxq/browser-import/history` | 2 | 2/0 | 6ms | 2/4/4ms | 0 |
| OPTIONS `/api/v1/auth/verify` | 2 | 2/0 | 3ms | 1/2/2ms | 0 |
| GET `/access/<redacted>` | 4 | 2/2 | 3ms | 1/1/1ms | 0 |
| GET `/index.html` | 2 | 0/2 | 2ms | 0/2/2ms | 0 |
| GET `/api/v1/channels` | 2 | 0/2 | 2ms | 0/1/1ms | 0 |
| GET `/login.html` | 2 | 0/2 | 2ms | 0/1/1ms | 0 |
| OPTIONS `/api/v1/contact-messages` | 1 | 1/0 | 2ms | 2/2/2ms | 0 |

三类异常单列：

- **低频高延迟**：web-submit 1 次 4.890 秒，无慢 SQL；contact 成功样本约 1.1–1.2 秒；Insights 741ms。
- **页面/API 慢但无匹配慢 SQL**：web-submit、contact、Insights、topics/local、tabs 和 sync/status；现有证据
  指向文件、外部 I/O、broker、序列化或远程连接阶段，未用加超时/重试掩盖。
- **后台任务/连接持有很久但无前台端点**：PDF clean 901.195 秒、ZSXQ publish 806.215 秒；应用
  connection hold 为 0，不能误报为长事务。当前 174 次轮询已有 Page Visibility、active-only、single-flight 与
  终态停止；08:52–09:21 覆盖两条真实长任务，不是无界终态轮询，保持观察而不重复改代码。

## 后台任务、通知与错误归因

- Celery 117 个完成、0 retry、0 failed：PDF clean 1/1（901.195s）、ZSXQ publish 1/1（806.215s）、
  workflow notification 110/110（累计 304.390s、P95 4.113s、max 6.086s）、Publisher worker 5/5
  （累计 4.205s）。worker success 不能替代 Publisher durable 终态；数据库仍有 4 条 repair-limit 失败。
- durable notification 最近 24h 共 38 条、38 attempts、全部 sent；没有通知失败风暴。
- B站情报 22/22 自然周期，每周期 6/6，合计 132/132 competitor 成功；06 点因重启边界未计完整周期。
- YouTube 00:03 为 49/51，01:00 errors=2；失败身份连续三窗稳定为 id 21/56。
- 固定窗原始 10 条 server-closed 与 2 个 SQL traceback 全在 06:22–06:24 单测故障注入，带
  `unittest.mock`、`for test` 或 `sync-disconnect-1`。自然进程没有结构化 SQL error；不能把测试污染算作生产断连。

## Publisher 跨 checkout 根因与修复证据链

修复前：四个失败任务均来自 `mp3_video_workflow`，repair history 约每 3m13s 换一次 Celery ID，count 3 后
关闭；成功任务也含旧 reason。当前 Watchdog 同时发布自己的 replacement，但 CAS rowcount=0 后撤销，说明
数据库已被另一个 actor 先更新。父任务 notification/产物路径明确落在旧根目录
`/Users/v9/Documents/workspace/v9/code/dig_ytb_data_claude_v1`，而当前 worker 日志只见 revoke 广播。

根因：旧 checkout 的 Celery/Watchdog 与当前项目共享 broker、DB、queue 与 nodename。重复 nodename 使 inspect
响应合并，旧代码能消费当前任务而原拓扑观测无法显示第二节点。

代码变更：Celery 控制面只在进程命令明确包含另一绝对 checkout 路径时暴露
`runtime_topology_conflict`；Watchdog 在任何 durable repair 前发现冲突即
`foreign_runtime_topology` fail-closed。短命令路径未知，不误判。

验证：TDD RED 分别为缺少冲突状态和未阻止 repair；GREEN 后 Watchdog/Publisher/bootstrap 99/99，完整 unit
1709/1709。标准重启后只保留当前 checkout 进程、worker ready、health 正常，代码已 RUNTIME_REPLACED。
没有人为构造 Publisher 业务数据；下一自然样本和完整后窗尚缺，所以问题继续 OPEN/OBSERVING。

## 表规模、扫描、索引与写放大

| 表 | 总大小 | live/dead | seq scan / idx scan | 结论 |
|---|---:|---:|---:|---|
| `publication_tasks` | 138MB（heap 17 / index 13 / TOAST 108） | 13,788 / 1,367 | 7,092 / 3,603,796 | repair 风暴是运行拓扑；无索引缺口证据 |
| `zsxq_topics` | 129MB（heap 13 / index 36 / TOAST 79） | 12,637 / 1,584 | 4,170 / 891,793 | 正则聚合受远程往返；不盲目加索引 |
| `zsxq_topic_comments` | 118MB | 41,333 / 5,644 | 472 / 358,525 | 本窗慢 SQL 0；已有 autovacuum |
| `zsxq_attachments` | 78MB | 33,906 / 37 | 625 / 1,135,724 | 健康 |
| `zsxq_sync_runs` | 37MB | 1,062 / 46 | 284 / 337,680 | 本窗慢 SQL 0 |
| `external_documents` | 7,016KB | 1,817 / 146 | 1,057 / 84,985 | 低样本，无 DDL 依据 |

完全重复索引为 0。web submission history 组合索引已扫描 1,680 次，单列 `web_submission_id` 索引扫描仍为 0；
Publisher stale repair 索引累计扫描 74,010 次。低使用不等于低价值，本窗没有“真实慢 SQL + table/index stats +
EXPLAIN”同时支持新增或删除索引，故 **不执行也不生成 CREATE/DROP INDEX、ALTER TABLE 或回填 SQL**。
人工数据库动作只有既有 YouTube 来源停用脚本；无索引整改 SQL。

## 测试、重启与运行态验收

- 新增外部 checkout 冲突与 repair fail-closed 两个 TDD 回归；定向联合 99/99。
- 完整 unit 沙箱初跑 1709 项，仅 34 项因禁止绑定 `127.0.0.1` 报 PermissionError；允许 loopback 后原命令
  复跑 `Ran 1709 tests in 111.040s / OK`。
- `compileall`、worker app factory、Celery revoke boundary、Bug strict、registry JSON 与 `git diff --check`
  全部通过。
- 重启前 `check-restart-safety.py` 确认没有不可中断任务；标准 `restart-dev-stack.sh` 未使用 FORCE。
- 重启后 backend health 200、bootstrap 17/17 Celery service、Watchdog、6/6 scheduler 均成功；全部受管 worker
  ready，当前 Celery/Watchdog 命令根目录一致，`_foreign_runtime_processes()` 返回空。
- 真实只读页面/API 与空队列控制面验收通过；没有制造假 Publisher 任务代替自然业务证据。

## 人工动作、风险与未捕获边界

YouTube 受保护脚本 `docs/sql/disable_unavailable_youtube_channels_20260920.sql` 仍默认 ROLLBACK，包含精确来源保护、
5 秒 `lock_timeout`、30 秒 `statement_timeout`、验证和回滚。人工需先再次确认两频道仍为 0 results，再在
DBeaver 分段审核执行；风险是把暂时私有或短时不可见频道停用。本轮未无人值守执行生产配置或 DDL。

未捕获不等于未发生：固定窗只有 overview/topics/tabs/status 各一个页面样本；Insights 聚合慢 SQL 是后台缓存路径，
未与当次 overview request 直接匹配；重启后没有自然 Publisher 候选；PgBouncer 无错误也不能关闭历史私网物理
断点。contact 旧进程失败恢复仅有两个新样本。上述边界全部保留为下一轮复核条件。

## 下一轮明确验收计划

1. 首先核对所有 Celery/Watchdog 的绝对根目录、cwd、nodename 与 `runtime_topology_conflict`；发现外部 checkout
   必须保持 business repair fail-closed，并记录 PID、命令、队列与安全停止结果。
2. 对下一条自然 MP3→Publisher 任务关联父子 ID、repair history、worker active/reserved/scheduled、Redis
   queue/unacked、current-id 与终态；要求无旧 reason、无可见消息换 ID、无 repair-limit。
3. 再观察一个完整窗口；只有自然 Publisher 成功且双栈不复发，才能从 OPEN/OBSERVING 进入 CLOSED 评估。
4. 继续监控已关闭的 ZSXQ 指纹 `f8b9a0ddf265ae34`；任何新任务回读 source clean 大 JSON 即按同一复发键重开。
5. 人工处理 YouTube 后验证 channel 49/49、video errors=0，并再过一个自然周期。
6. 对 web-submit、contact 与 174 次列表轮询继续按 request id 拆文件、broker、外部 I/O 与 SQL；若非活动阶段仍轮询，
   才新增节流修复，不能仅凭次数下结论。
7. 任一 server_closed/query-wait 必须按同秒应用、PgBouncer、route/source 与 PostgreSQL activity 四源取证。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
|---|---|---|---|---|---|---|
| `ORCH-PARENT-CHILD-STATE` / `RUNTIME-TOPOLOGY-RELOAD` | 旧 checkout 双栈共享 broker/DB/nodename | 外部路径冲突告警；durable repair 前 fail-closed | 定向99/99、全量1709/1709、安全重启、当前 foreign=0 | 本窗是 | 自动化：自然任务及后窗；不得按当前空队列关闭 | 自然 Publisher 无旧 reason/换 ID/repair-limit，后窗无双栈 |
| `f8b9a0ddf265ae34` | present-empty 被误作字段缺失 | 字段存在性判据与查询预算 | 3 个自然空数组任务完成，完整后窗指纹0 | 否 | 继续作为 sentinel | 新出现必须逐任务解释；当前 CLOSED |
| YouTube id 21/56 | 远端不可用、本地仍 active | 身份观测；受保护人工 SQL | 第三次自然周期仍 49/51、errors=2 | 是 | 人工复核并执行脚本 | 49/49、errors0，再过一周期 |
| `674016d39deab3f5` | 正则聚合 + 远程往返 | 既有缓存与短读 Session | 2次3.505s；无锁/timeout，PgBouncer query max180ms | 持续尾延迟 | 自动化：冷/热分开观测 | P95下降；异常时取 executor/BUFFERS 与 request 同秒证据 |
| web submit/list | 长任务期间状态轮询；提交非 SQL 阶段 | 已有 visibility/active-only/single-flight/终态停止 | 175/175成功；列表72.744s，提交4.890s | 负载上升 | 自动化：检查非活动轮询和阶段 telemetry | 非活动0轮询，提交各阶段耗时可归因 |
| 调度器测试污染 | import-time FileHandler | logging 配置移到 main | 22/22自然周期，新污染0 | 否 | 继续按 PID/marker 隔离 | 当前 CLOSED；新自然污染立即重开 |
