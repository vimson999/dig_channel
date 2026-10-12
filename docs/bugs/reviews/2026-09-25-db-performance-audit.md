# 2026-09-25 每日数据库性能与错误巡检

## 根因闭环结论

固定窗口为 `2026-09-24 06:00:00` 至 `2026-09-25 06:00:00`（Asia/Shanghai）。本轮不能标记“已解决”：

- 应用慢 SQL 11 条、累计 17.970 秒，P50/P95/max 为 1.206/5.692/5.692 秒，较上窗明显恶化。
  最大指纹来自 MP3 音频目录；同形生产 `EXPLAIN` executor 仅 2.176ms，应用耗时约为其 2,615 倍，
  没有新增索引依据，根因边界仍在 driver/fetch/PgBouncer/网络尾延迟。
- Publisher 旧根写入由 26/26 降至 7/16，旧 reason 和换 ID 从 5 条/8 次降为 0，但旧 actor 在当前 backend
  启动后仍能写共享库，物理隔离未完成，保持 **OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**。
- YouTube id 21/56 第六个窗口继续固定失败，保持 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。
- 新发现巡检器因 `🟢 [timestamp]` 格式静默漏计请求；TDD 修复并用生产日志回放后，本窗恢复为 305 个请求、
  88 个端点。上一轮 HTTP 数字不再作为可比基线，状态为 **PRODUCTION_REPLAY_ACCEPTED / OBSERVING**。
- 上轮 scheduler import 日志污染在完整后窗无新 marker，API 路径升级为 **RUNTIME_ACCEPTED**；未受管 runner
  仍只有代码证据，不能一并关闭。

本轮未执行生产 DDL、索引变更、数据回填、任务重投、凭据轮换或破坏性操作。唯一代码变更是离线巡检器，
无需重启 backend/Celery/scheduler。

## 与上一轮量化对比

| 指标 | 09-24 窗口 | 09-25 窗口 | 变化 | 判断 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL | 2 | 11 | +450.0% | 恶化 |
| 应用慢 SQL 累计 | 2.245s | 17.970s | +700.4% | 恶化 |
| 慢 SQL P50 | 1.007s | 1.206s | +19.7% | 恶化 |
| 慢 SQL P95 / 最大 | 1.238s / 1.238s | 5.692s / 5.692s | +359.7% | MP3 冷目录复发 |
| PgBouncer wait P95 | 36.39ms | 43.21ms | +18.7% | 轻度恶化，无错误事件 |
| PgBouncer query P95 | 63.64ms | 69.93ms | +9.9% | 轻度恶化 |
| HTTP 请求 | 16 | 305 | 不可比 | 上轮被格式漏计，不能解释为流量增长 |
| HTTP P95 / 最大 | 4.08ms / 9.74ms | 2.009s / 6.040s | 不可比 | 本轮覆盖修复后首个可信基线 |
| Publisher old-root / 旧 reason / 换 ID | 26/26 / 5 / 8 | 7/16 / 0 / 0 | 症状改善 | 写入围栏仍缺，未闭环 |
| YouTube 固定失效源 | id 21/56 | id 21/56 | 第六窗 | 反复 |

## 主要问题排序

| 排名 | 问题 | 累计 / P95 / 最大 | 失败率、用户与页面影响 | 复发次数/状态 |
| --- | --- | --- | --- | --- |
| 1 | MP3 冷目录 canonical SQL 远端尾延迟 | 5.692s / 5.692s / 5.692s | audio-assets 1/1 成功但页面等待 6.040s；Session hold 5.757s | 09-17、09-18、09-25；OPEN/REPEATED |
| 2 | Publisher 分布式旧 actor | Celery publication 4 次累计 966.301s，P95/max 292.655s | 16 条中 3 条 B站业务失败；7 条 old-root，潜在资产跨根风险 | 多窗；WAITING_DISTRIBUTED_ISOLATION |
| 3 | 页面/API 慢但无匹配慢 SQL | 12 个端点组；最高累计 19.333s、P95 3.713s、max 3.713s | MP3 上传、素材重生成、排行、B站页面；本窗均成功但用户等待 | 新可信基线，OPEN/OBSERVING |
| 4 | YouTube 两个失效源 | channel 16.56s；video 52.42s | channel 49/51、video errors=2，持续失败通知 | 第六窗；WAITING_MANUAL_CONFIG |
| 5 | HTTP 巡检格式漏计 | 修复前 1 条，修复后 305 条 | 会掩盖全站 endpoint P95/失败率与 SQL 关联 | 同类复发；PRODUCTION_REPLAY_ACCEPTED |

## 慢 SQL、Session、PgBouncer 与 executor

| 指纹 / origin | 次数 / 累计 / P95 | 调用链与根因判断 |
| --- | ---: | --- |
| `be891b5c1af362da` / `mp3_video_workflow.py:495` | 1 / 5.692s / 5.692s | request `07585b10`；989 行，Session 5.757s，其中非 SQL 65ms；executor 2.176ms，远端 driver/fetch/网络尾延迟 |
| `4a1f114c4802c3a0` / notification provider | 2 / 2.364s / 1.206s | 后台通知；低频远端往返，无失败风暴 |
| `674016d39deab3f5` / Insights aggregate | 1 / 1.685s / 1.685s | 06:41 缓存刷新，生产计划 executor 34.766ms；非缺索引 |
| `1e920b463e0710b7` / Publisher processing scan | 1 / 1.354s / 1.354s | 后台修复扫描；历史计划命中 status 索引，远端尾延迟 |
| chart/summary/update/due/task load 四类 | 6 / 6.877s / 1.332s | 页面与后台混合；单条 1.059–1.332s，未见锁或 executor 高成本 |

PgBouncer 1,440 个样本八类故障均为 0；wait P50/P95/max 24.68/43.21/170.33ms，query 为
51.60/69.93/202.50ms，288 次连接均来自私网 `100.96.3.12`，proxy fallback 为 0。statement/lock/deadlock/
query/connect timeout、SQL error、traceback episode、waiting lock、idle-in-transaction 均为 0。

唯一长连接持有就是 MP3 目录查询本身：5.757s 中 SQL 5.692s、非 SQL 65ms，没有外部 I/O 与 Session
交叉持有证据。任务准入另有 9 个亚秒 idle episode，最大 2 连接、xact 0.353s、idle 0.301s、deferral 0，
不能冒充长事务事故，也不能漏报。

## 页面、API、后台任务与 SQL 关联

修复请求解析后，共 305 个完整请求、0 incomplete、88 个端点，303 成功、2 失败；失败均为
`/favicon.ico` 404。累计 104.149s，P50/P95/max 2.49ms/2.009s/6.040s。

| 端点 | 请求/失败 | 累计 / P95 / 最大 | SQL 与调用链判断 |
| --- | ---: | ---: | --- |
| MP3 B站上传记录 | 23/0 | 19.333s / 1.143s / 1.186s | 无匹配 >=1s SQL；需补低于阈值 SQL、序列化与外部状态阶段 |
| MP3 B站提交 | 5/0 | 13.784s / 3.713s / 3.713s | 无匹配慢 SQL；外部/broker 与业务准备主导 |
| MP3 finance cover 重生成 | 2/0 | 6.186s / 3.570s / 3.570s | 1 条 task load 1.059s，其余为文件/渲染链 |
| MP3 audio-assets | 1/0 | 6.040s / 6.040s / 6.040s | canonical SQL 5.692s、hold 5.757s，request id `07585b10` |
| MP3 background 重生成 | 2/0 | 5.579s / 3.195s / 3.195s | 无匹配慢 SQL，外部文件/渲染路径 |
| MP3 新建任务 | 2/0 | 4.541s / 2.407s / 2.407s | 无匹配慢 SQL，broker/准备路径待分段 |
| rankings/all | 2/0 | 3.618s / 2.113s / 2.113s | 无匹配慢 SQL，多条低阈值 SQL/聚合可能 |
| chart-data | 2/0 | 2.838s / 2.220s / 2.220s | 匹配 1 条 1.332s SQL |
| data/summary | 1/0 | 2.009s / 2.009s / 2.009s | 匹配 1 条 1.245s SQL |
| charging-dashboard | 1/0 | 1.710s / 1.710s / 1.710s | 无匹配慢 SQL |
| ZSXQ Insights overview | 1/0 | 0.619s / 0.619s / 0.619s | 请求内无 >=1s SQL；启动缓存刷新另有 1.685s 聚合 |

低频高延迟包括 audio-assets、finance/background/cover 重生成、MP3 新建/取消、rankings、chart、summary、
charging dashboard。页面/API 很慢但无匹配慢 SQL共有 12 个端点组，重点是 B站上传记录、提交、重生成、
新建任务与排行；这些只能说明 >=1s SQL 不是全部耗时，不能据此宣告数据库无贡献。

特别复核中，`zsxq-insights/overview` 有 1 个样本；`zsxq/topics/local`、`topics/local/tabs`、`sync/status`
没有自然样本，且生产表中本窗 `zsxq_sync_runs=0`。这是“未捕获”，不是“没有问题”。

后台任务：

- Celery 139 completed、0 retry、0 failed，累计 1,973.630s。Publisher pipeline 4 次累计 966.301s、
  P95/max 292.655s；B站 upload-only 4 次累计 562.651s、P95/max 178.224s；notification 100 次累计
  248.224s、P95 3.508s；MP3 process 31 次累计 196.454s、P95 15.133s、max 46.831s。
- durable notification 87/87 sent、attempt 总数 87、max attempt 1，无失败/重复投递风暴。
- B站情报 24/24 个自然周期均 6/6 成功，累计 220.170s，P50/P95/max 8.869/11.668/12.337s。
- YouTube channel 49/51，业务 16.56s；video 49 个频道、新增 30、更新 53、errors=2、52.42s。
- 本窗数据库任务包括 6 个 ZSXQ clean-output publish、4 个 audio clean、2 个 PDF clean、3 个 MP3 workflow，
  均 completed；无自然 ZSXQ sync run。

没有前台端点的长后台包括 Publisher、B站上传、B站情报、YouTube 与 notification；现有长 Session 证据只指向
MP3 前台目录 SQL，不支持把这些后台耗时误归因为连接持有。

## 程序模式复核与已落地修复

MP3 音频目录仍是单次、有界、显式投影，`client_inflight=1` 且本窗仅 1 条 catalog SQL；旧附件 N+1、
并发搜索叠加、文件 I/O 跨事务没有复发。问题是冷读 989 行却最终 telemetry `items=0`，目录在可用文件很少时
仍跨远端搬运近千条历史行；在没有可靠可用性投影前不能截断历史、删除原始 JSON/审计行或伪造结果。

巡检器根因已按 TDD 修复：请求正则兼容 plain/ANSI emoji、方括号与可选毫秒，并新增真实生产样本合同。
回放从 1 个请求恢复为 305 个，能够把 `07585b10` 的 API、SQL 与 Session 对齐。数据库纪律新增 coverage anomaly
要求。该脚本按次执行，真实日志回放就是运行态验收，无需服务重启。

Publisher 旧 reason、换 ID 和资产不可达失败本窗均改善为 0，但 7 条 old-root 写入仍在当前新 backend 启动后发生，
证明隔离根因未闭环。YouTube 人工停用脚本仍未执行。scheduler 测试 marker 保持历史 53 不增，API import 修复
取得完整后窗。

## 表规模、顺序扫描与索引复核

| 表 | total / 主要组成 | live/dead | seq / idx scan | 判断 |
| --- | --- | ---: | ---: | --- |
| `publication_tasks` | 139MB；heap 17MB、index 13MB、toast 109MB | 13,333 / 470 | 7,103 / 3,688,077 | idx 99.81%；4 条慢 SQL但代表计划仍快 |
| `zsxq_topics` | 129MB | 12,637 / 1,584 | 4,183 / 891,972 | idx 99.53%；Insights 计划 34.766ms |
| `zsxq_topic_comments` | 118MB | 41,333 / 5,644 | 479 / 358,685 | idx 99.87%；本窗无慢 SQL |
| `zsxq_attachments` | 78MB | 33,906 / 37 | 628 / 1,135,768 | idx 99.94%；本窗无慢 SQL |
| `zsxq_sync_runs` | 37MB | 1,062 / 46 | 288 / 337,728 | idx 99.91%；代表计划 1.677ms |
| `external_documents` | 7,016kB | 1,817 / 146 | 1,060 / 84,987 | idx 98.77%；本窗无慢 SQL |
| `content_pipeline_assets` | 3,216kB | 2,771 / 246 | 4,040 / 6,968 | audio 目录 executor 2.176ms；不加索引 |

精确重复索引为 0。低扫描候选包括 attachments PDF group/tab 5MB scan=1、topics group/type/create 3.7MB
scan=1、external TTS/summary scan=0、publication publisher MP3 match scan=0 等，但缺写放大收益、业务完整周期和
删除后计划证据，不生成 DROP。音频目录现有 pkey/canonical/created/status/task-status 索引；真实 SQL 顺扫 2,771 行
只需 2.176ms，CREATE INDEX 无法解决 5.692s 应用时延。因此本轮无 CREATE/DROP INDEX、ALTER 或回填脚本。

## 测试与运行态验证

- TDD 红灯：彩色 production start/end 均不匹配；修复后目标 1/1、分析器 32/32 通过。
- 真实 24h 日志回放：305 个 HTTP、11 条慢 SQL、139 个 Celery 完成、1,440 个 PgBouncer 样本；
  `07585b10` request→SQL→Session→client telemetry 完整关联。
- `compileall`、worker app-factory boundary 28 个文件、Celery revoke boundary 323 个文件通过。
- 首次受限全量发现 35 个 loopback bind `PermissionError`，另有新 Bug 尚未登记的预期门禁红灯；登记后在允许
  loopback 且使用项目标准 `PYTHONPATH=src/backend` 的环境重跑，最终 **1,718/1,718 OK（98.356s）**。
- Bug strict **208/208**、recurrence JSON、`git diff --check` 均通过；全量测试前后 scheduler 四类历史测试
  marker 均为 53，没有新污染。
- 离线脚本没有常驻进程需要替换，因此未运行重启安全门、未重启服务；既有 backend/worker 运行态未被本轮改变。

## 待人工 SQL、风险与未捕获边界

1. 本轮无索引或表结构 SQL。既有 `docs/sql/disable_unavailable_youtube_channels_20260920.sql` 仍需人工在
   DBeaver 按 lock/statement timeout、验证与回滚说明复核执行。
2. Publisher 仍需人工盘点所有共享生产 PostgreSQL/Redis 凭据的主机、容器和启动器；无法证明全停时，轮换凭据并
   隔离 broker namespace。单机 `ps`、0 个旧 reason 或一次成功任务都不能替代物理围栏。
3. `pg_stat_statements` 不可用；日志缺 SQL pool-acquire、fetch bytes 与 response bytes 分段，无法把 5.692s
   进一步精确分配给 pool、driver 解码、网络传输或 PgBouncer 排队。
4. 慢 SQL 阈值以下的 N+1、重复聚合与轮询仍可能存在。`topics/local`、tabs、sync/status 无样本，未捕获不能写成未发生。
5. HTTP 基线从本轮起才可信；09-24 的 16 请求不能用于环比。自动 coverage anomaly 尚未实现，保持 OBSERVING。

## 下一轮明确验收计划

1. MP3 audio-assets：记录候选/存在/返回行数、response bytes、pool acquire、fetch/deserialize 分段；要求暖请求目录 SQL=0，
   冷请求最多 2 条，旧附件 N+1=0；同形 executor 仍低而应用复发时采同秒网络/PgBouncer证据。
2. Publisher：先验证跨主机物理隔离动作，再要求自然 MP3→Publisher 成功且完整 24h 的 old-root、旧 reason、ID
   replacement、repair-limit、缺失资产均为 0。
3. YouTube：人工脚本执行后，channel/video 两个自然周期不再出现 id 21/56，再观察下一周期。
4. HTTP 观测：持续确认 request start/end 覆盖非零并与 access/API 日志数量级一致；补自动 coverage anomaly 后再评估关闭。
5. 用同一 06:00–06:00 窗口重算全部页面/API/任务/SQL/PgBouncer；目标 ZSXQ 页面没有自然样本时继续明确“未捕获”。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `be891b5c1af362da` | authoritative 冷目录跨远端 fetch 近千行；executor 快，driver/fetch/网络尾延迟 | 单次有界投影、TTL/single-flight、Session 后文件检查已在既有代码 | API 6.040s、SQL 5.692s、executor 2.176ms、hold 非SQL65ms | **是** | 补 pool/fetch/bytes；不能盲目索引或删审计 | 冷/暖分段齐全，完整窗旧N+1=0且用户时延稳定 |
| old-root Publisher rows | 旧 actor 仍共享 DB/broker，症状下降不等于失去写权限 | CAS/current-id/repair 防线已在当前代码 | 7/16 old-root；旧reason/replacement 0；无资产失败 | **是** | 人工停旧栈或轮换凭据/namespace | 物理隔离后自然成功+完整窗五类指纹0 |
| YouTube id 21/56 | 远端无内容但本地仍启用 | 结构化失败身份、受保护停用脚本 | channel 49/51、video errors=2 | **第六窗** | 人工复核执行脚本 | 双自然周期无该两源，再观察一周期 |
| 彩色 request 行漏计 | parser 只接受 plain 且带毫秒格式 | 正则兼容 + 真实生产样本测试 + 纪律更新 | 回放 1→305 请求，完整关联 `07585b10` | 同类复发 | 自动 coverage anomaly 未实现；OBSERVING | 下一窗三路数量级一致且异常能 fail loud |
| scheduler 测试 marker | import 期全局文件 handler | 上轮已移除 API/库副作用 | 完整后窗 marker 53 不增，三路自然 DB 错误0 | API 未复发 | runner 未受管；仅代码合同 | runner 实际受管启动后再验；API 可保持 RUNTIME_ACCEPTED |
| ZSXQ topics/tabs/status | 本窗无自然样本 | 既有查询/Session 防线 | overview 1次619ms；其余未捕获 | 未知 | 不得据0样本关闭 | 自然请求后串 request→SQL→Session→PgBouncer→executor |
