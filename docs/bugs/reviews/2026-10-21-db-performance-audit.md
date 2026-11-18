# 2026-10-21 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 `2026-10-20 06:00:00`–`2026-10-21 06:00:00`（Asia/Shanghai），数据库边界为
`2026-10-19 22:00:00`–`2026-10-20 22:00:00` UTC。总体仍是 **OPEN / REPEATED /
RUNTIME_REPLACED / OBSERVING / WAITING_DISTRIBUTED_ISOLATION**，不能写成已解决。

最高优先级根因继续恶化：36 个 Publisher 只有 16 个 completed、20 个 failed，失败率 55.6%，失败累计
5,053.635 秒。20 个失败中 17 个属于同一日志外执行者证据：10 个
`required_material_unavailable` 和 7 个“找不到已生成的视频文件”都缺 writer actor；其引用的当前节点文件
17/17 实际存在，且 7 个视频的 mtime 均早于失败关闭时刻。17 个误关累计 3,830.170 秒，P50/P95/max
229.447/254.078/263.605 秒，占全部失败的 85.0%。上一窗为 8/24（33.3%），本窗为 17/36（47.2%），
因此是明确复发和恶化，不是路径字符串 `old-root=0` 所能抵消的改善。

本轮发现 upload-only 的“视频不存在”终态此前没有持久化 deployment/hostname/PID/code identity，日快照也
只覆盖 queue-repair 素材门禁，因而漏计 7 条同根证据。已按 RED→GREEN 为该终态增加 durable actor 审计、错误
日志和日快照归因，并把“文件已在失败前存在”作为防止事后生成误判的 mtime 契约。生产只读重放从旧口径的
`foreign_actor_evidence=10` 修正为 17。该改动增强归因、未掩盖失败，也不能撤销另一部署的 DB/Redis/Celery
写权限；物理隔离仍需人工停旧栈，或轮换凭据并隔离 broker namespace。

数据库链路方面，慢 SQL 数量和累计耗时较上一窗下降，但高尾仍反复：13 条/37.683 秒，P95/max
8.041/8.181 秒。audio catalog 单指纹 3 条累计 24.138 秒、P95/max 8.181 秒，而上一轮同形生产 executor
仅 5.182ms；三次连接持有的非 SQL 合计只有 916.57ms。本窗 PgBouncer 八类错误、应用 SQL error、锁等待、
statement/lock timeout 与 idle-in-transaction 均为 0。这是一个无错误但尾部仍高的窗口，不足以关闭
pool/driver/fetch/私网根因，也没有索引依据。

## 与上一轮基线对比

| 指标 | 上一窗 | 本窗 | 变化与判定 |
|---|---:|---:|---|
| 应用慢 SQL | 30 / 62.847s | 13 / 37.683s | 数量 -56.7%，累计 -40.0%，改善 |
| P50 / P95 / 最大 | 1.243 / 8.421 / 9.808s | 1.310 / 8.041 / 8.181s | P50 +5.4%，P95 -4.5%，max -16.6%；高尾仍反复 |
| PgBouncer query-wait | 1 episode | 0 | 本窗改善，需完整连续后窗 |
| statement/lock timeout、deadlock、SQL error | 0 | 0 | 本窗未捕获，不等于物理断点已根治 |
| Publisher foreign actor | 8/24 | 17/36 | 占比 33.3%→47.2%，P0 恶化 |
| HTTP | 782 完成、771 成功 | 928 完成、918 成功 | 10 个失败均 favicon 404；业务 5xx=0 |
| B站情报 | 24/24、168/168，P95 16.084s | 24/24、168/168，P95 15.067s | 成功率保持，P95 改善 6.3% |
| YouTube channel | 43/51 | 45/51 | 瞬时失败改善；固定 id21/56 第 18 窗仍复发 |
| YouTube video | 49 处理、errors=2 | 49 处理、errors=2 | 固定 21/56 未改善 |

## 主要问题排序

1. **Publisher 跨部署误关**：17/36 命中同一 foreign-actor 复发键，累计 3,830.170 秒，P95
   254.078 秒，影响至少 4 个 MP3 父任务及 17 个发布 child；失败率、受影响任务数和复发次数均为本窗最高。
2. **远程 DB fetch 尾延迟**：全部慢 SQL 累计 37.683 秒；audio catalog 一指纹占 24.138 秒（64.1%），
   P95/max 8.181 秒，executor 毫秒级且无锁，根因仍在 PostgreSQL executor 外。
3. **无慢 SQL 的页面/API 长尾**：18 次 B站 upload 请求累计 33.358 秒、P95 2.231 秒；4 次背景图重生
   P95 3.310 秒；单次 Web submit 8.232 秒，均没有匹配慢 SQL，需按文件处理、broker 和外部 API 分段。
4. **后台外部调用失败**：upload-only 15 次成功、3 次失败；其中两次 21173 定时时间余量耗尽、一次 21523
   充电档位不可用。三条合计 1,223.465 秒；失败均 durable 记录，未发生无限重试，但 21173 是已知复发。
5. **YouTube 永久失效来源**：channel 45/51、video errors=2；id21/56 连续第 18 窗出现，其余四个 channel
   失败按瞬时上游链路单独观察，禁止扩大停用范围。

## 页面、API、后台任务与 SQL 关联

HTTP 共 928 个完成请求、68 个端点，918 成功、10 失败，P50/P95/max 为
3.06ms/1.269s/9.415s，累计 257.814 秒。10 个失败全部是 `/favicon.ico` 404，业务 5xx 为 0。

- `audio-assets` 18/18 成功，累计 27.441 秒、P95/max 9.013/9.415 秒；其中 3 个 request id 关联同一
  `services/mp3_video_workflow.py:495` SQL，取 1,369 行，SQL 累计 24.138 秒。三次连接持有 25.055 秒，
  非 SQL 仅 916.57ms；没有文件 I/O 跨 Session、旧附件 N+1 或逐行 lazy-load 证据。
- B站上传记录列表 109/109，累计 46.735 秒、P95 738ms；18 次上传 POST 累计 33.358 秒、P95
  2.231 秒且无慢 SQL。业务终态中的 17 条误关来自共享日志外 actor，而不是这些前台 POST 失败。
- MP3 job detail 22/22，P95 955ms；背景图、财经封面、封皮重生各 4 次，P95
  3.310/3.114/2.405 秒；job create 4 次 P95 2.560 秒，均无匹配慢 SQL，属于低频非 DB 长尾。
- Web submit 1/1 为 8.232 秒且无慢 SQL；内容统计 3/3 为 200，P95/max 410ms，旧大 JSON 指纹和 timeout
  继续为 0。
- 固定窗没有自然 `zsxq-insights/overview`、`topics/local`、`topics/local/tabs`、sync status 请求，必须写
  “未捕获”。重启后只读验收分别为 200/876ms、200/542ms、200/382ms、200/221ms，仅证明新栈可用。
- Celery 完成 227、失败 3、retry 0；成功累计 5,099.235 秒。Publisher pipeline 18/18 成功、累计
  2,016.930 秒；upload-only 15 成功/3 失败、累计 1,767.483 秒；MP3 97/97 成功、ZSXQ publish 1/1、
  notification 95/95、audio clean 1/1。长任务壁钟不能冒充 DB Session 持有。
- durable 通知 73/73 sent、attempt 总数 73、max attempt 1，没有通知失败风暴。

## 修复前证据、变更与验证链

### upload-only 终态执行者审计

- 修复前：7 条“找不到已生成的视频文件”均无 actor；关联表给出的 7 个视频当前都存在，mtime 早于终态；
  本地 `platform_upload.log` 没有这些 task id。旧快照只报 10 条 queue-repair 证据，漏掉这 7 条。
- 代码变更：新增统一 `deployment_actor_identity()`；upload-only 缺视频终态把 reason、原路径、closed_at 和
  actor 以 JSONB patch 原子持久化，并写同一 actor 错误日志。快照关联结构化上传投影，只有文件存在且 mtime
  不晚于 completed_at 才记作历史 foreign actor 证据。
- 定向回归：两个新增测试先分别因汇总字段缺失、actor helper 缺失而 RED；实现后 2/2 GREEN，相关两模块
  90/90 GREEN。
- 完整验证：unit 1,740/1,740 通过，`compileall`、`git diff --check` 通过；生产只读同窗重放得到
  material 10 + upload 7 = `foreign_actor_evidence=17`。
- 运行态：重启安全门确认无不可中断任务后标准重启；backend bootstrap 17/17 worker、Watchdog、6/6 scheduler
  均 ok、`errors=[]`，platform_upload worker ready，health healthy。重启后尚无新的自然缺视频终态，因此审计
  防线为 RUNTIME_REPLACED/OBSERVING，根因仍 OPEN。

## 表规模与索引证据

`publication_tasks` 150MB（live/dead 14,640/2,073，索引命中 99.83%）、`zsxq_topics` 132MB
（13,847/1,621，99.52%）、`zsxq_topic_comments` 119MB（45,350/5,546，99.85%）、
`zsxq_attachments` 79MB（35,870/71，99.94%）、`zsxq_sync_runs` 37MB（1,074/58，99.91%）、
`external_documents` 7,016kB（1,817/146，98.74%）。未发现完全重复索引。

真实 EXPLAIN：Publisher 列表 0.053ms、内容任务列表 0.167ms、all-tabs config 0.036ms、任务主键
0.051ms、ZSXQ sync runs 顺扫 1.635ms。Insights canonical 选择顺扫 13,847 行中的 9,224 行、返回 7,701，
40.755ms；现有 preferred index 已扫描 8,961 次。audio catalog 上轮同形 executor 5.182ms。上述证据均不能
解释应用 1–8 秒尾部；低使用索引也没有写放大、约束替代和完整业务窗口证据，禁止自动删除。

本轮 **不创建/删除索引，不生成待执行 DDL**。若未来 executor 本身成为瓶颈，必须重新取得真实 SQL、
`pg_stat_user_*` 与 `EXPLAIN (ANALYZE, BUFFERS)` 三证，再生成带 timeout、验证和回滚的 DBeaver 脚本。

## 待人工动作与风险

1. 物理隔离旧运行体：在旧 Mac 停止 backend/Celery/Watchdog/scheduler/手工脚本；若无法证明全停，轮换
   PostgreSQL/Redis 凭据并隔离 Celery namespace。风险是误中断合法消费者，需先列举部署与安排安全窗口。
2. 既有 `docs/sql/disable_unavailable_youtube_channels_20260920.sql` 只允许在 DBeaver 逐段只读复核 id21/56
   后执行；其余四个 channel 本窗失败不能加入脚本。执行后要求连续两个自然周期 channel 49/49、video errors=0。
3. B站 21173 已再次出现两次，需实现排队/上传耗时预算与执行前再校验，禁止静默改用户定时点。21523 需刷新
   充电档位后由自然投稿验收，不能靠重试风暴绕过。

## 未捕获边界与下一轮计划

`pg_stat_statements` 未安装；固定窗没有重点 ZSXQ 自然页面流量，也没有重启后新的同类 upload-only 缺文件终态。
慢 SQL 阈值以下的 pool acquire、fetch bytes/deserialize、provider HTTP、broker 排队和文件处理分段仍不完整；
当前 `pg_stat_activity` 只看到本机 PgBouncer source `100.96.3.12` 和 localhost gunicorn，但空
`application_name` 仍是归因缺口，瞬时快照不能代表整窗。未捕获不能写成未发生。

下一轮必须优先：1）用新口径复核 material/upload 两类 actor，要求完整窗 `foreign_actor_evidence=0`；
2）确认 old-root/远端 actor 为 0，并取得当前根 PDF→ZSXQ、MP3→Publisher 自然成功；3）audio catalog 冷建
补 pool/fetch/bytes/deserialize 分段，暖请求目录 SQL=0；4）PgBouncer 八类错误继续为 0；5）核验 YouTube
21/56 人工动作与 B站 21173；6）若任一同类再现，继续追上游部署/调用链，不得重复只修计数症状。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
|---|---|---|---|---|---|---|
| `foreign_actor_evidence` material/upload | 日志外旧部署共享 DB/Redis/Celery，文件系统视角不同 | 两类终态 actor 审计与 mtime 快照门禁 | 重放 17；新 worker ready，无修复后自然样本 | 是，P0 恶化 | 人工停旧栈或轮换凭据/namespace | 完整 24h foreign actor、old-root、远端 actor 全 0；双链自然成功 |
| `be891b5c1af362da` | 1,369 行远程 fetch 遇 pool/driver/私网尾部 | O(1) 投影、Session 外文件校验 | 应用 P95 8.181s，executor 5.182ms | 是 | 增加 pool/fetch bytes/deserialize 分段 | 冷建分段完整，暖请求 SQL=0，完整窗无 >5s 尾部 |
| PgBouncer query-wait | 私网或 pool server 可用性瞬断 | 启动/路由门禁已存在 | 本窗八类错误 0 | 本窗否，历史反复 | 下次尖峰同秒抓 pool/socket/route/activity | 连续完整窗 0；再现时定位物理断点 |
| B站定时 21173 | 入口五分钟校验未预算队列与上传耗时 | durable 失败保留，根因修复未落地 | 本窗 2 次真实失败 | 是 | 上传维护者补执行前契约，不静默改时点 | 定向合同 + 自然定时投稿成功 + 后窗 0 |
| YouTube id21/56 | 永久不可用来源仍启用 | 受保护人工脚本已存在 | channel 45/51；video errors=2 | 第 18 窗 | DBeaver 只读复核后人工停用 | 连续两周期 channel 49/49、video errors=0 |
| content statistics old JSON | 活动态轮询曾放大重统计 | 轻量轮询已运行替换 | 3/3 200、P95 410ms、旧指纹/timeout 0 | 本窗否 | 保持自然后窗门禁 | 下一完整窗继续 0 timeout/0 旧指纹 |
