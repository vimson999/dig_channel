# 2026-09-29 数据库性能与错误巡检

## 结论

固定窗口为 `2026-09-28 06:00:00`–`2026-09-29 06:00:00`（Asia/Shanghai）。本轮不能写成“已解决”：

- 应用慢 SQL 从 6 条/8.261 秒恶化到 **47 条/68.197 秒**；P50 1.101 秒、P95 2.531 秒、最大
  6.851 秒。数量、累计、P95、最大较前窗分别 `+683.3%`、`+725.5%`、`+31.0%`、`+254.4%`，
  仅 P50 改善 `8.3%`。
- 最严重的业务根因仍是旧 MacBook Pro 运行栈共享生产 DB/broker。它在 Tailscale 重新入网后由
  `100.94.17.45` 换成 `100.119.248.103`，旧固定 IP 观察因此假绿。本窗 39 条相关 durable task 中
  27 条携旧 checkout；五类旧根任务壁钟合计 14,463.677 秒（存在父子嵌套，不作机器利用率求和）。
- 旧根 Publisher 为 17/25，失败 5 条，失败率 29.41%。真实失败链是旧节点写入本地绝对素材路径，
  当前 Watchdog 发现原消息不可见后重投给当前 render，最终报 `max() arg is an empty sequence` 或
  `No valid audio clips`。该症状已用通用素材可达性门禁修复并运行替换，但物理旧栈/共享凭据尚未隔离，
  且尚无修复后的同类自然任务，状态为 **OPEN / REPEATED / RUNTIME_REPLACED / OBSERVING**。
- 代表慢 SQL 的 PostgreSQL executor 只有 0.024–3.656ms，应用侧却为 1.568–6.851 秒；无锁等待、
  statement/lock/connection timeout、server closed 或长 idle-in-transaction。现有证据仍指向远端
  driver/fetch/PgBouncer/私网尾延迟，不支持新增索引。

## 与上一轮的量化对比

| 指标 | 09-28 基线 | 本窗 | 变化/判定 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 6 | 47 | +41 / +683.3%，恶化 |
| 慢 SQL 累计 | 8.261s | 68.197s | +59.936s / +725.5%，恶化 |
| P50 / P95 / max | 1.201 / 1.933 / 1.933s | 1.101 / 2.531 / 6.851s | 中位改善，尾部恶化 |
| 完整 HTTP | 0 | 537 | 恢复真实覆盖；533 成功、4 失败 |
| PgBouncer wait P95 | 34.11ms | 51.61ms | +51.3%，仍远小于应用 SQL |
| PgBouncer query P95 | 57.35ms | 85.65ms | +49.3% |
| old-root durable task | 4/4 | 27/39 | 跨功能明显恶化 |
| old-root 壁钟合计 | 1,709.255s | 14,463.677s | +746.2%；父子嵌套，不等同资源时间 |
| YouTube 固定失败源 | 第 9 窗 | 第 10 窗 | id 21/56 再次复发 |

## 主要问题排序

排序综合累计耗时、P95、最大值、失败率、用户路径和复发次数，而不是只看出现次数。

| 排名 | 问题 | 量化影响 | 根因与状态 |
| --- | --- | --- | --- |
| 1 | 旧运行栈跨部署写入与重投 | old-root MP3 5,562.554s、Publisher 5,026.888s、ZSXQ 发布 2,309.837s、PDF 1,232.496s、音频 331.902s；Publisher 5/17 失败 | Tailscale 重入网换 IP；部署身份与素材不共享。代码门禁已运行替换，物理隔离仍 OPEN |
| 2 | 全站远端 SQL 尾延迟 | 47 条/68.197s，P95 2.531s，max 6.851s | executor 毫秒级，PgBouncer 无故障类；OPEN/REPEATED |
| 3 | 页面/API 非 SQL 长尾 | HTTP 537 次/238.956s；B站上传 POST max 7.838s、job create max 7.248s、content web-submit 4.042s | 外部 I/O、渲染/序列化或低于 1 秒的多次 DB 往返；OBSERVING |
| 4 | 后台任务长耗时 | Celery 成功 106/失败 5；成功壁钟 2,909.111s；Publisher pipeline P95/max 329.826s | 失败 5 条均来自旧根素材链；其他上传/清洗为外部 I/O 主导 |
| 5 | YouTube 固定失效源 | channel 49/51、video errors=2，id 21/56；第 10 窗 | 配置未人工停用；OPEN/REPEATED |
| 6 | B站情报偶发外部连接失败 | 22 周期中 21 次 6/6、1 次 5/6；max 14.136s | `api.bilibili.com:443` 连接失败；未转化为 DB 故障，OPEN/OBSERVING |

## SQL、页面/API 与后台任务关联

### SQL 指纹

| 指纹/来源 | 次数 | 累计 / P95 / max | executor 复核 | 判定 |
| --- | ---: | ---: | ---: | --- |
| `be891b5c1af362da` MP3 audio catalog | 1 | 6.851 / 6.851 / 6.851s | 3.656ms，顺扫 2,985 行、返回 1,047 行、326 read blocks、无 temp | 应用约 executor 1,874 倍；一次有界投影，无 N+1/索引依据 |
| `b2e3db53ef78819b` artifact CAS update | 4 | 6.375 / 3.113 / 3.113s | CAS/PK 形态，未见锁 | 远端写尾延迟 |
| `451351aac9133e12` MP3 task 轻量读取 | 4 | 4.319 / 1.117 / 1.117s | 既有索引 | 页面/job detail 相关 |
| `1e920b463e0710b7` processing repair scan | 3 | 3.986 / 1.568 / 1.568s | 0.170ms、33 shared blocks，命中既有索引 | 应用约 executor 9,221 倍 |
| `674016d39deab3f5` Insights theme aggregate | 2 | 3.685 / 2.073 / 2.073s | 既有聚合形态 | 冷 overview；热缓存有效 |
| `590…` task params/result 点查 | 2 | 3.180 / 2.110 / 2.110s | PK executor 0.024ms、3 hits | 应用约 executor 87,900 倍；JSON 宽读仍需按调用点约束 |

唯一长连接持有为 audio catalog request `0817e459`：hold 6.897 秒，其中 SQL 6.851 秒、非 SQL
46ms，排除该请求的 Session 跨文件/外部 I/O。runtime guard 捕获 18 个短暂 episode，最大 2 连接、
事务龄 1.038 秒、idle 0.911 秒、defer 0；没有未匹配长 hold。

### 页面/API

固定窗共 97 个端点、537 个完整请求，P50/P95/max 为 6.41ms/2.112s/7.838s，成功 533、失败 4。
三条失败是 favicon 404；唯一业务失败为 request `07d653e7` 的
`GET /api/bilibili-publish/charging-options` 502/1.530s。同端点 5 次中 4 次成功；其 DB SELECT/UPDATE
仅 75/438ms，证据更符合外部凭据/API 偶发失败，不能归因缺索引。

- 低频高延迟：audio-assets 1 次 7.524s（SQL 6.851s）；finance cover regenerate 2 次，max 6.830s；
  MP3 job create 2 次，max 7.248s；content web-submit 1 次 4.042s。
- 页面/API 慢但没有匹配 `>=1s` SQL：B站上传 POST 4 次累计 15.333s/max 7.838s（仅 1 条慢 SQL
  1.071s）、content web-submit 4.042s、部分 job create/detail 与平台凭据请求。低于阈值 SQL、外部上传、
  文件处理和响应序列化仍是未拆分边界。
- 后台长但无前台端点：PDF clean 483.131s、ZSXQ publish 284.692s、B站 upload-only 4 次累计
  673.243s/max 349.041s，以及旧根 durable 任务。它们不能从 HTTP 统计推断正常。

重点链路本窗自然样本：overview 2 次（0.48ms/3.659s，冷聚合 1.611s）、topics/local 2 次
（0.863/1.664s）、tabs 2 次（其中一条 240.69ms）、sync status 有自然样本。未捕获的慢 SQL 阈值以下
N+1、响应字节和 pool/fetch 分段仍不能宣告不存在。

### 后台任务

Celery 106 次成功、5 次失败、0 retry；成功任务累计 2,909.111 秒。Publisher pipeline 5 成功/5 失败，
成功 P95/max 329.826 秒；B站 upload-only 4 次成功累计 673.243 秒；PDF clean 1 次 483.131 秒；
MP3 process_job 20 次成功累计 304.571 秒；ZSXQ publish 1 次 284.692 秒；通知 75 次成功、P95 8.286 秒。
durable 通知 104/104 sent、最大尝试 1，没有失败风暴或重复投递。

## 根因修复与运行态验收

生产失败行的 `params/result` 精确显示旧路径 `/Users/v9/Documents/workspace/v9/code/dig_ytb_data_claude_v1/...`，
且 `queue_repair.reason=pending_queued_not_visible_in_celery`。本轮在
`src/backend/services/publisher_queue_repair.py` 落地：

1. Publisher synthesis pending/processing 仅在确认 active/reserved/scheduled、Redis queue/unacked 都不含原消息后，
   检查 audio 与 image/video 必需素材。
2. 文件检查前强制释放 DB Session；缺失绝对路径时不再投递，而写入
   `required_material_unavailable`、`queue_repair_blocked` 和缺失路径审计，保留 params/result 原始证据。
3. B站 upload-only 路径保持轻投影且不受该素材门禁影响；常规扫描没有把 params 扩入全局候选查询。

TDD 红灯分别复现 pending 旧行为会错误重投、processing 会进入 render；修复后两条均 fail closed。
定向 88/88、完整 unit 1,722/1,722、`compileall`、worker app-factory 门禁、Celery revoke AST 门禁和
`git diff --check` 均通过。沙箱内完整单测的 34 个错误全部是绑定 `127.0.0.1` 被拒；允许回环后原命令全绿。

重启前安全门读取 46 条近期任务、0 条活动 ZSXQ run，两次均确认无不可中断任务；标准脚本未 FORCE，
最终重启到 backend PID 71629。bootstrap 17/17 Celery、Watchdog、6/6 scheduler 均 `ok`、`errors=[]`，health
200。新 Watchdog 第一轮所有 repair scan 返回 0 候选且无错误。最终 PID 上真实只读 E2E：overview
200/289ms、topics/local 200/780ms、tabs 200/263ms、sync status 200/191ms。

这只证明代码加载和常规页面/后台扫描可用；尚无修复后同类旧根自然任务，因此根因不能 CLOSED。

## 表与索引复核

| 表 | 总/heap/index | live/dead | seq/index scan | 观察 |
| --- | --- | ---: | ---: | --- |
| `publication_tasks` | 141/17/13MB | 13,992/2,130 | 7,114/3,820,517 | index share 99.814%；HOT 1.217%，JSON 更新写放大继续观察 |
| `zsxq_topics` | 129/13/36MB | 12,637/1,584 | 4,206/892,162 | index share 99.531% |
| `zsxq_topic_comments` | 118/91/23MB | 41,333/5,644 | 487/358,854 | index share 99.864%，HOT 70.456% |
| `zsxq_attachments` | 78/35/42MB | 33,906/37 | 632/1,135,844 | index share 99.944% |
| `zsxq_sync_runs` | 37/1.2/0.9MB | 1,062/46 | 295/337,785 | index share 99.913% |
| `external_documents` | 6.9/0.9/1.2MB | 1,817/146 | 1,064/84,991 | index share 98.764% |

精确定义重复索引为 0。累计低扫描索引包括 external TTS 232KB/0、summary 184KB/0、publisher MP3
168KB/0、attachments PDF group/tab 5,088KB/1、topics group/type/create 3,720KB/1，但统计窗口和读写收益
证据不足，不能据此删除。三个代表 EXPLAIN 均证明 executor 毫秒级；本轮 **无 CREATE/DROP INDEX、ALTER、
回填或生产配置脚本，也无待人工 DDL**。如以后考虑删除低价值索引，必须先记录 stats reset 时间、写维护成本、
查询 owner，并逐条提供 `lock_timeout`、`statement_timeout`、验证与回滚脚本。

## 反复问题、边界与下一轮验收

- 远端旧 actor：同一物理主机换 Tailscale node/IP，固定地址不是稳定身份。必须人工停止该 MacBook Pro 上所有
  backend/Celery/Watchdog/scheduler/手工脚本，或轮换 PostgreSQL/Redis 凭据并隔离 broker namespace。
- YouTube：人工停用脚本仍未执行；下一轮必须继续优先核对 id 21/56，而不是重复诊断同一症状。
- B站情报：21/22 完整成功，单次 `api.bilibili.com` 连接失败；下次故障必须同秒采 DNS、代理/TUN、目标地址。
- `logs/scheduler.log` 最后更新时间仍是 2026-09-24 06:40，本窗以各独立 scheduler log 为准；日志缺失不代表任务没运行。
- 窗后 `2026-09-29 06:29:19` 的一条 `pg_stat_activity` SyntaxError 是本次巡检探针引号错误；修正后的
  只读探针 06:29:34 成功，当前只有 `100.96.3.12` 与 localhost、`idle in transaction=0`。下一轮须把
  前一条明确归为巡检自污染，不能误报自然生产 SQL 错误。
- 应用慢 SQL 只记录超过阈值的完成语句，不能覆盖低于阈值的 N+1、连接 acquire、driver fetch、序列化或失败 SQL。
- audio-assets 需要登录，本轮重启后没有伪造登录态做探针；以固定窗真实 request `0817e459` 与下一自然请求继续验收。

下一轮明确条件：

1. 从 PID 71629 / Watchdog 2026-09-29 06:27 切点观察完整 24 小时；`required_material_unavailable` 应只阻断
   真正缺失素材，且不得再出现 `max() empty`、`No valid audio clips` 或跨根重投。
2. durable task 的 old-root 数必须为 0，且至少各有一条自然 PDF、ZSXQ publish、MP3→Publisher 由当前 root 完成；
   没有自然样本则保持 OBSERVING。
3. 以 host/node/deployment identity 复核 Tailscale 和 `pg_stat_activity`，不得再只盯 `100.94.17.45`；空
   `application_name` 的远端连接必须归因或隔离。
4. 对 audio-assets、overview、topics/local、tabs、sync status 记录 request id、bytes、pool/fetch/serialize 分段；
   同形 executor 仍低于 10ms 时禁止新增索引。
5. YouTube id 21/56 经人工脚本停用后要求 channel 49/49、video errors=0 两个自然周期；B站再次失败时采完整网络现场。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `old-root + pending_queued_not_visible + missing material` | 旧部署共享 DB/broker，素材仅存在旧 checkout；主机重入网换 IP | repair 后、Session 释放时做通用素材可达性 fail-closed；保留审计 | 1722 tests；PID71629；17/17+Watchdog+6 scheduler；第一轮 0 candidate | 本窗是；修复后尚无自然样本 | **OPEN/OBSERVING**；人工停旧栈或轮换凭据/namespace | 完整窗 old-root=0，三类自然任务当前 root，旧失败指纹=0 |
| `be891b5c1af362da` | executor 外 driver/fetch/网络尾延迟 | 既有一次有界投影与缓存保持 | executor 3.656ms vs app 6.851s | 是 | OPEN；补 pool/fetch/bytes | 冷/暖自然请求、暖目录 SQL=0、无长 hold |
| 全站 SQL 远端尾延迟 | 私网/PgBouncer/driver 抖动，非 executor/锁 | 既有 Session 边界与 telemetry | 0 timeout/lock/长事务；PgBouncer 八类故障 0 | 恶化 | OPEN；同秒四源关联 | P95/max 回落并连续完整窗无故障，仍需 executor 对照 |
| YouTube `21/56` | 已知失效来源仍 active | 观测字段已运行；停用脚本待人工 | 第10窗 channel/video 各 errors=2 | 是 | OPEN/WAITING_MANUAL_CONFIG | 人工停用后两个自然周期 errors=0 |
| B站 intelligence partial | 外部连接偶发失败，物理断点未捕获 | 日志归因与逐 competitor 隔离 | 21/22 完整，1 次 5/6 | 是 | OPEN/OBSERVING | 故障同秒网络证据或完整后窗 0 失败；不能反推根治 |
