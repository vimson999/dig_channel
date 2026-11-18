# 2026-10-20 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 `2026-10-19 06:00:00`–`2026-10-20 06:00:00`（Asia/Shanghai），数据库边界为
`2026-10-18 22:00:00`–`2026-10-19 22:00:00` UTC。总体仍是 **OPEN / REPEATED /
WAITING_DISTRIBUTED_ISOLATION**，不能写成已解决。

本轮最严重的证据不是 old-root 字符串，而是 24 个 Publisher 中 8 个被写成
`required_material_unavailable` 失败（失败率 33.3%）：8/8 缺少上轮已上线的 writer actor 审计，且其声称
“不可用”的全部素材在当前节点实际存在、mtime 早于关闭时刻。本地业务日志没有执行这些关闭动作；旧 MacBook
Pro 的 Tailscale 最后在线时间又晚于 8 次误关。由此确认共享 PostgreSQL/Redis/Celery 的日志外旧运行体仍以不同
文件系统视角写共享库，只看 `old_root=0` 会假绿。代码内防线无法撤销另一部署的写权限，必须人工停旧栈，或轮换
DB/Redis 凭据并隔离 broker namespace；完成后还要经过自然任务和完整后窗才能闭环。

第二个反复根因是远程 DB 链路：15:42 出现 1 个真实 PgBouncer `query_wait_timeout` episode，客户端等待恰好
15 秒后断开，对应 Publisher repair scan；同窗 audio catalog 3 次应用 SQL 累计 26.178 秒、P95/max
9.808 秒，而相同生产 SQL 的 `EXPLAIN (ANALYZE, BUFFERS)` executor 仅 5.182ms。没有锁、statement timeout、
idle-in-transaction 或外部 I/O 跨 Session 证据，因此根因在 pool acquire/driver/fetch/私网链路，不是
PostgreSQL executor 或缺索引。本轮不执行、不建议生产 DDL。

## 与上一轮基线对比

| 指标 | 上一窗 | 本窗 | 变化与判定 |
|---|---:|---:|---|
| 应用慢 SQL | 16 / 23.116s | 30 / 62.847s | 数量 +87.5%，累计 +171.9%，恶化 |
| P50 / P95 / 最大 | 1.220 / 2.170 / 2.292s | 1.243 / 8.421 / 9.808s | +1.9% / +288.1% / +327.9% |
| PgBouncer query-wait | 0 | 1 episode | 新增、反复 |
| statement/lock timeout、deadlock | 0 | 0 | 当前日志未捕获，不等于永不发生 |
| durable old-root | 2/2 | 0/32 | 字符串改善，但 8 条 foreign-actor 证据使拓扑根因继续复发 |
| Publisher | 无自然样本 | 16/24 完成、8/24 误关 | 失败率 33.3%，P0 恶化 |
| HTTP | 自然流量 0 | 782 个完成请求 | 覆盖改善；771 成功、1 个业务 502、10 个 favicon 404 |
| B站情报 | 23/23、161/161，P95 31.667s | 24/24、168/168，P95 16.084s | 成功率保持，P95 改善 49.2% |
| YouTube | channel 49/51，video errors=2 | channel 43/51，video errors=2 | 固定两源第 17 窗；channel 瞬时失败显著恶化 |

## 主要问题排序

1. **Publisher 跨部署误关**：8/24 失败，累计失败壁钟 1,707.053 秒，失败 P50/P95/max
   208.638/236.063/236.372 秒；影响 4 个真实 MP3 父流程的 8 个发布 child。8/8 actor 缺失、8/8 本地素材实际
   存在，命中分布式复发键。
2. **远程 DB pool/driver/fetch 尾延迟**：慢 SQL 累计 62.847 秒；audio catalog 单指纹 3 次累计
   26.178 秒、P95/max 9.808 秒；另有一次 15 秒 PgBouncer 等池失败。executor、锁和事务证据排除索引根因。
3. **无慢 SQL 的低频高延迟 HTTP**：`POST /api/v1/channels/sync/all` 106.605 秒、
   `POST /api/v1/videos/sync` 61.105 秒，均成功且没有匹配慢 SQL；根因在外部 YouTube API 调用链。
   channel 自然周期 43/51，除固定 21/56 外另有 6 个瞬时失败。
4. **后台工作流长壁钟**：217 个 Celery 任务全部成功、累计 5,423.870 秒；Publisher pipeline 16 个累计
   1,742.961 秒、upload-only 8 个累计 1,597.415 秒、ZSXQ publish 2 个累计 836.139 秒。无重试/失败风暴，
   这些 wall time 不能冒充 DB Session hold。
5. **B站与通知改善项**：B站 24/24 周期、168/168 competitor 成功，累计 330.420 秒、P50/P95/max
   13.428/16.084/16.850 秒；durable 通知 78/78 sent、attempt 均 1。保持观察，不标 CLOSED。

## 页面、API、后台任务与 SQL 关联

HTTP 共 782 请求、125 个端点，P50/P95/max 为 2.80ms/1.373s/106.605s，累计 414.999 秒。
失败 11 个中 10 个是 `/favicon.ico` 404，唯一业务失败为 B站 `charging-options` 外部请求 502、883.57ms；
其数据库读写成功，未命中慢 SQL。

- `audio-assets` 10/10 成功，累计 28.892 秒、P95/max 10.777 秒；3 个 request id 精确关联 3 条目录 SQL，
  SQL 累计 26.178 秒。`00bd7dfe`（initial）、`fb5a3c80` 与 `2d45c4be`（composition_end）均由
  `services/mp3_video_workflow.py:495:_build_audio_asset_catalog_snapshot` 发起；三次均为 TTL 过期后的真实搜索，
  约每 5 分钟重建 1,352 行快照。它不是 N+1，但 300 秒进程内缓存把大结果 fetch 尾部反复暴露。
- `zsxq-insights/overview` 7/7 成功，P95/max 4.207 秒；最慢请求匹配 1 条 1.865 秒聚合 SQL。
  `topics/local` 2/2，P95 912ms；`topics/local/tabs` 2/2，P95 337ms；system sync scheduler status
  2/2，P95 618ms，均无慢 SQL。
- 内容管线 statistics 6/6，P95/max 596ms，旧大 JSON 指纹、query timeout 均为 0，上一轮已关闭的问题本窗
  未复发；仍需继续自然后窗。
- `channels/sync/all` 106.605 秒、`videos/sync` 61.105 秒、web-submit 两次 P95 9.628 秒，均无匹配慢 SQL，
  属于“页面/API 很慢但无慢 SQL”。
- 4 个长 Session hold 中 3 个是 audio catalog（8.156–9.926 秒），1 个是 repair scan 等池失败
  （15.304 秒）；SQL 合计 41.474 秒，非 SQL 仅 383ms，未发现文件/网络外部 I/O 跨 Session。

## 修复前证据、变更与验证链

### 1. 跨部署 writer 假绿门禁

- 修复前证据：8 条失败均写 `required_material_unavailable`，actor 字段为空；所列全部图片/音频在当前节点存在，
  本地业务日志没有对应关闭记录，旧主机在误关后仍在线。
- 代码变更：`daily_db_runtime_snapshot.py` 新增 `material_unavailable`、actor 缺失、本地路径实际存在和
  `foreign_actor_evidence` 四项汇总，避免 `old_root=0` 再次被当成拓扑健康。
- 定向回归：先写真实临时文件合同，旧实现因缺字段 RED；实现后 4/4 GREEN。生产只读重放得到
  `material_unavailable=8`、`actor_missing=8`、`local_paths_exist=8`、`foreign_actor_evidence=8`。
- 完整测试与运行态：见“验证”。该变更只增强诊断，不会隔离旧 actor，根因仍 OPEN/REPEATED。
- 下一轮：先复核上述四项和所有 feature root，再扫描其他指标；必须完整 24 小时
  `foreign_actor_evidence=0`、old-root=0、远端 actor=0，并有当前根 PDF→ZSXQ、MP3→Publisher 自然成功。

### 2. audio catalog 与 PgBouncer 链路

目录 SQL 顺扫 `content_pipeline_assets` 4,370 行、返回 1,352 行、排序内存 1,271kB、shared read 469，执行
5.182ms；应用却为 7.949–9.808 秒。查询为一次有界投影，无 deferred-column N+1，Session 释放后才校验文件。
15:42 的 repair scan 在 PgBouncer 等待 15 秒后被关闭；同分钟 wait 指标升高，随后恢复。实时 pool 快照为
`cl_waiting=0`、`sv_idle=1`，只证明当前恢复，不抹除窗口故障。下一次尖峰必须同秒记录 pool acquire、socket/
route、fetch bytes 和 server executor；禁止用加索引、重试或放宽 timeout 掩盖。

## 表规模与索引证据

`publication_tasks` 150MB（live/dead 14,598/1,384）、`zsxq_topics` 132MB（13,847/1,621）、
`zsxq_topic_comments` 119MB（45,350/5,546）、`zsxq_attachments` 79MB（35,870/71）、
`zsxq_sync_runs` 37MB（1,074/58）、`external_documents` 约 6.9MB（1,817/146），索引命中占比
98.74%–99.94%。`content_pipeline_assets` 约 5MB，目录顺扫 executor 仅 5.182ms。目标表未发现完全重复索引。

没有真实 SQL + `pg_stat_user_*` + EXPLAIN 同时支持 DDL，因此本轮 **不创建/删除索引，不生成待执行 DDL**。
若未来 executor 本身成为瓶颈，再生成带 `lock_timeout`、`statement_timeout`、验证及回滚的 DBeaver 脚本。

## 待人工 SQL 与风险

本轮没有索引、表结构、回填或生产配置 SQL。唯一既有待人工脚本是
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`，仅针对已连续 17 窗永久失败的 channel 21/56；
必须先在 DBeaver 逐段只读复核，默认回滚并确认仍为远端 0 results 后才能提交。新增的 64/31/34/24/2/20
属于本窗瞬时失败，禁止扩大脚本范围。旧运行体隔离需要主机停服务或凭据/namespace 轮换，不应伪装成索引 DDL；
其风险是中断仍使用旧凭据的合法进程，因此需先列出当前部署消费者并安排安全窗口。

## 验证、运行态与未捕获边界

本轮仅改诊断器、测试和文档，无 backend/Celery/scheduler 常驻代码变更，不需要重启，也未调用 restart 脚本。
生产只读快照已实际连接数据库并重放固定窗；固定窗已有真实 health、页面、API、Celery 和独立 scheduler 流量。
验证结果：诊断定向 36/36、完整 unit 1,738/1,738 通过；首次沙箱运行的 34 个错误全部是 loopback/Redis
网络权限，允许本地网络后归零。`compileall` 通过；worker app factory 扫描 28 个文件通过；Celery revoke
边界扫描 326 个 Python 文件通过；Bug strict 为 211/211 已归类、9 个复发簇、回顾日期 2026-10-20；
registry JSON 和 `git diff --check` 通过。当前 backend health 实测返回 `healthy`。本轮没有常驻代码变更，
因此没有重启，也无需用一次重启后的 ready 取代固定窗已有的 217 个真实 Celery 完成样本。

未捕获边界：没有旧主机现场进程清单、启动项停用证明或凭据轮换证明；慢 SQL 阈值以下的 pool/fetch 分段、
provider HTTP、浏览器渲染和 broker 排队不完整；`pg_stat_statements` 未安装；当前 pool/会话快照不能代表整个
24 小时。无记录只能写“未捕获”，不能写“未发生”。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
|---|---|---|---|---|---|---|
| `foreign_actor_evidence` / Publisher material unavailable | 日志外旧部署共享 DB/Redis/Celery，文件系统视角不同 | actor 审计已运行替换；本轮新增诊断 fail-loud | 8/8 actor 缺失且本地文件存在 | 是，P0 | 人工停旧栈；否则轮换凭据并隔离 namespace | 完整 24h foreign actor/old-root/remote actor 均 0，当前根双链自然成功 |
| `be891b5c1af362da` | 1,352 行远程 fetch 遇 pool/driver/私网尾部；300s TTL 周期放大 | O(1) 投影、Session 外文件校验；诊断保留 | 应用 P95 9.808s，executor 5.182ms | 是 | 增加 pool/fetch bytes 分段；评估版本化 stale-while-refresh | 冷建有分段证据，暖搜索 SQL=0，完整窗无 >5s 尾部 |
| `c014bff91db5d2af` / query-wait | PgBouncer 15s 无可用 server 或私网链路不可用 | 启动/路由门禁已存在 | 1 episode；应用 15.296s 失败 | 是 | 下一尖峰同秒抓 pool/socket/route/activity | 完整窗 query-wait=0，若再发能定位物理断点 |
| YouTube id 21/56 + 瞬时六源 | 永久不可用来源仍启用，叠加外部 API 瞬断 | 受保护停用脚本已存在 | channel 43/51；video errors=2 | 第 17 窗，且恶化 | 人工 DBeaver 复核 21/56；瞬时源不得误停 | 连续两周期 channel 49/49、video errors=0；瞬时失败单独归因 |
| content statistics old JSON | 活动态轮询曾放大重统计 | 轻量轮询已运行替换 | 6/6 200，P95 596ms，旧指纹/timeout=0 | 本窗否 | 后续自然窗口继续守门 | 下一完整窗继续 0 timeout/0 旧指纹 |
