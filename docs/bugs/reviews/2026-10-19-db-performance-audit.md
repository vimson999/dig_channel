# 2026-10-19 数据库性能与错误巡检

## 结论

固定窗口为 `2026-10-18 06:00:00`–`2026-10-19 06:00:00`（Asia/Shanghai），数据库查询边界为
`2026-10-17 22:00:00`–`2026-10-18 22:00:00` UTC。全局结论是 **OPEN / REPEATED /
WAITING_DISTRIBUTED_ISOLATION**，不能写成已解决：旧 MacBook Pro 在本窗再次创建并完成 1 个
`content_pdf_clean` 和 1 个 `zsxq_clean_output_publish`，2/2 都只携
`dig_ytb_data_claude_v1` 旧 checkout，累计壁钟 1,818.552 秒。当前节点对应 content/publish worker 日志在任务
期间没有更新，只有共享 outbox 的通知 worker 看到了开始/完成事件；旧主机在任务时段仍在线，窗后离线不构成
物理隔离。上轮新增的 Publisher 终态 actor 审计本窗没有 Publisher 自然样本，仍为 OBSERVING。

本窗慢 SQL 从 6 条/8.533 秒恶化到 16 条/23.116 秒，但实时 `EXPLAIN (ANALYZE, BUFFERS)` 显示
notification、report-audio 与 credential 代表查询 executor 只有 0.041–0.142ms；Insights executor
35.100ms、all-tabs executor 0.025ms。PgBouncer 八类错误、SQL error、statement/lock timeout、等待锁、
长 Session 和 idle in transaction 均为 0。证据继续指向应用到远程 PostgreSQL 的 driver/PgBouncer/私网 fetch
尾延迟，而不是 PostgreSQL executor 或缺索引；本轮没有生产 DDL，也没有足够证据生成索引变更脚本。

## 与上一轮基线对比

| 指标 | 2026-10-18 | 本窗 | 变化与判定 |
|---|---:|---:|---|
| 慢 SQL 数 | 6 | 16 | +166.7%，恶化 |
| 慢 SQL 累计 | 8.533s | 23.116s | +170.9%，恶化 |
| P50 / P95 / 最大 | 1.177 / 2.126 / 2.126s | 1.220 / 2.170 / 2.292s | +3.7% / +2.1% / +7.8% |
| SQL/PgBouncer 八类错误 | 0 | 0 | 持平；不是“未发生”的充分证据 |
| old-root durable task | 29/29 | 2/2 | 数量下降但复发键仍命中，不是改善闭环 |
| Publisher | 12/16 完成 | 0 个自然样本 | 未捕获，actor 审计无法验收 |
| B站情报 | 24/24，168/168；P95 15.146s | 23/23，161/161；P95 31.667s | 成功率保持，P95 +109.1%、max +100.9% |
| YouTube | 固定两源第 15 窗 | channel 49/51、video errors=2 | 第 16 窗复发 |
| 自然业务 HTTP | 0 | 0 | 仍为覆盖缺口 |

## 主要问题排序

排序同时考虑累计耗时、P95、最大耗时、失败率、用户/页面影响和复发次数。

1. **旧运行时跨主机消费本地文件任务**：两条任务累计 1,818.552 秒；PDF clean 1/1 completed、
   1,081.439 秒，ZSXQ publish 1/1 completed、737.113 秒。虽然失败率为 0，但 36/36 文件由错误部署根执行，
   输出目录、提交根和结果路径均落在旧 checkout，影响真实 Web 提交数据的归属、可见性及后续可重放性。该簇自
   09-27 起反复出现，上一窗 29/29 old-root，本窗继续命中复发键。
2. **B站外部 I/O 尾延迟**：23 个自然周期、161/161 成功，累计 418.509 秒，P50/P95/max 为
   13.955/31.667/37.877 秒。最慢周期数据库 SQL 仅 33–453ms，credential/BVID 阶段之间存在约
   5.3–12.7 秒日志空档，因此推断主要耗时在外部 HTTP/DNS/TUN 边界；缺少逐请求网络阶段埋点，不能进一步
   臆断单一断点。成功率保持，但性能显著恶化，维持 OPEN/OBSERVING/WAITING_NETWORK_TRACE。
3. **YouTube 固定失败源**：00:03 channel sync 51 个来源，49 成功/2 失败；01:00 video sync 处理
   49 个来源、`errors=2`。固定为 `id=21 Smartpet Video AI` 和 `id=56 数字指南针`，失败率 3.92%，
   已连续第 16 个巡检窗复发。没有匹配慢 SQL、长 Session 或无界重试；受保护停用 SQL 尚待人工执行。
4. **远程 SQL fetch 尾延迟**：notification due-provider 5 次/6.852 秒、P95/max 2.292 秒；
   report-audio recovery 2 次/2.524 秒；finalization 2 次/2.318 秒；Publisher 三类扫描共 3.386 秒；
   all-tabs 2.151 秒、credential 2.089 秒、Insights 1.698 秒。代表 executor 0.025–35.100ms、PgBouncer
   query P95 73.19ms，排除 executor 慢与索引缺失，保留网络/驱动尾部复发状态。
5. **通知后台任务**：本地 Celery 41/41 成功、累计 139.030 秒，P50/P95/max 2.935/7.033/10.241 秒；
   durable notification 34/34 sent、max attempt=1。当前没有失败风暴，但任务 wall time 明显大于其数据库
   executor，需继续按 provider I/O 与 broker 排队观察。

## 页面、API、后台任务与 SQL 关联

固定窗只捕获 9 个 HTTP，全部是上一轮 06:07 的巡检烟测，不是自然业务流量。6 个正确方法请求均成功：
`topics/local` 696.45ms、Insights overview 512.24ms、`sync/runs` 383.99ms、scheduler status
334.99ms、tabs 287.27ms、health 2.23ms；3 个 0.41–0.53ms 失败是对 POST-only 路由发 GET 产生的
自污染 405。它们均没有匹配 >=1s 慢 SQL。固定窗没有 `sync/status` 正确方法的自然样本，也没有其他页面/API
自然请求，因此 N+1、大字段、超大分页、缓存失效与频繁轮询只能记为未捕获，不能判为未发生。

窗后在当前 backend 做只读 E2E：health 200/0.133s，Insights overview 200/0.311s（43,478B，
`redis-stale` 并已排 refresh），topics/local 200/0.985s（132,951B/20 topics），tabs
200/0.364s，sync/runs 200/0.398s，scheduler status 200/0.579s。它们只证明当前栈可用，不替代自然窗口、
缓存后窗或旧运行时隔离证据。

三类必须单列的异常：

- **低频高延迟**：all-tabs、credential、Insights 和三类 Publisher/report-audio repair SQL；executor 均快，
  应用 fetch 偶发 1.1–2.3 秒。
- **页面/API 很慢但无匹配慢 SQL**：固定窗没有自然业务样本；窗后 topics/local 0.985 秒且响应 132,951B，
  只提示传输/序列化边界需要自然样本，不能据一次烟测诊断。
- **后台任务/连接很久但无前台端点**：两条 old-root durable task 共 1,818.552 秒、B站 418.509 秒、通知
  139.030 秒，均无对应前台请求；连接持有日志为 0，不能把任务 wall time 当成 DB Session hold。

## 根因证据链与本轮动作

### 旧运行时跨部署执行

- 修复前证据：任务 `cc12f0c6-d672-4c66-ab44-9dd7fbeb0d3c` 在 10:28–10:46 完成 PDF clean，
  `3735c386-9e17-41db-bab1-6e0452f50c50` 在 10:46–10:58 完成 ZSXQ publish；两者路径均为
  `/Users/v9/Documents/workspace/v9/code/dig_ytb_data_claude_v1/...`，本地业务 worker 日志无 task id，
  当前节点只有通知日志消费 shared outbox。Tailscale 显示旧 MacBook Pro 在任务结束后仍在线，当前快照虽离线，
  但没有停服务、卸凭据或 namespace 隔离证明。
- 已落地修复：既有代码已为 Publisher 素材不可达终态持久化 deployment/hostname/PID/code identity；本轮
  没有 Publisher 自然样本，且该修复无法阻止旧 backend 创建本地文件任务。本轮仅更新报告、Bug、纪律与复发
  登记，不伪造一个应用层补丁来掩盖共享凭据/共享 broker 根因。
- 定向/完整回归：见“验证”；文档与门禁只证明登记完整。
- 运行态验收：窗后当前 API/worker 可用，但 old-root=2/2 已复发，故不满足闭环。
- 下一步：人工在旧 MacBook Pro 停止并禁用 backend、Celery、Watchdog、scheduler 和手工脚本；若无法证明全停，
  轮换 PostgreSQL/Redis 凭据并隔离 Celery broker namespace。随后要求完整 24 小时远端 actor=0、所有 feature
  old-root=0、至少一组当前根 PDF→ZSXQ 和 MP3→Publisher 自然成功、任何终态 writer actor 可归因。

### SQL fetch 尾延迟

真实 SQL origin 与生产只读 EXPLAIN 已关联：notification 0.142ms、report recovery 0.060ms、finalization
0.041ms、credential 0.053ms、all-tabs 0.025ms、Insights 35.100ms。`pg_stat_activity` 无等待锁或
idle-in-transaction；连接均为本机 `100.96.3.12` 或 localhost，PgBouncer 288 次连接、12.0 次/小时，
proxy-TUN fallback=0，wait/query P95 42.47/73.19ms。应用 1–2 秒尾部无法由 executor、锁、Session 交叉
外部 I/O 或 PgBouncer 错误解释，继续归为远程 driver/fetch/私网偶发尾延迟，保持 OPEN/OBSERVING；下一次
>5 秒或 timeout 必须同秒捕获 socket、route、PgBouncer pool 和 server executor。

## 表与索引证据

目标表大小：`publication_tasks` 148MB、`zsxq_topics` 129MB、`zsxq_topic_comments` 118MB、
`zsxq_attachments` 78MB、`zsxq_sync_runs` 37MB、`external_documents` 约 6.9MB。live/dead tuples 分别为：
publication 14,566/739、topics 12,637/1,584、comments 41,333/5,644、attachments 33,906/37、
sync runs 1,062/46、external documents 1,817/146；索引命中占比均为 98.74%–99.94%。未发现完全重复索引，
stats 未重置，无 waiting lock。

Insights canonical selection 需顺扫同组 8,229 行、输出 6,707 行，执行 35.100ms、shared hit 约 1.7k，
现有 canonical index 累计已有 6,247 次扫描；all-tabs 只有 18 行，执行 0.025ms。低使用率/重叠索引统计是
postmaster 启动后的累计值，且本窗没有能被新索引改善的 executor 慢 SQL。因此 **不创建、不删除索引，不生成
DBeaver DDL**。未来只有在相同真实 SQL、`pg_stat_user_*` 和 `EXPLAIN (ANALYZE, BUFFERS)` 同时证明 executor
瓶颈时，才生成带 `lock_timeout`、`statement_timeout`、验证和回滚的人工脚本。

## 验证、未捕获边界与重启决定

本轮无生产代码或常驻配置变更，旧运行时物理隔离也尚未执行，因此没有理由重启当前 backend/Celery/scheduler；
未调用 restart safety 或 restart 脚本。验证包括诊断器定向测试、完整 unit、compileall、worker app factory、
Celery revoke、Bug strict、registry JSON 和 `git diff --check`。结果为：定向 35/35、完整 unit 1,737/1,737；
`compileall` 通过；worker app factory 扫描 28 文件通过；Celery revoke 扫描 326 个 Python 文件通过；Bug strict
211/211 已归类、9 个复发簇、回顾日期 2026-10-19；registry JSON 与 diff-check 均通过。首次沙箱内全量运行的
35 个错误全部是禁止绑定 loopback，放宽网络后归零；第二次漏设 `PYTHONPATH` 只产生 1 个 import error，最终按
`PYTHONPATH=src/backend` 的完整运行 1,737/1,737 通过，以最后一轮为准。

未捕获边界包括：自然业务 HTTP=0；Publisher/MP3/audio 自然任务=0；没有旧主机现场进程清单和 credential/broker
隔离证明；慢 SQL 日志阈值以下的多次亚秒查询、浏览器渲染/序列化、provider HTTP 与 broker 排队未完全分段；
`pg_stat_statements` 未安装。任何“0”只代表当前日志与快照未见，不能外推为未发生。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
|---|---|---|---|---|---|---|
| old-root durable task / shared DB+Redis+Celery | 旧 Mac 仍有共享写权限和本地文件系统视角 | Publisher 终态 actor 审计；本轮强化纪律/登记 | 2/2 old-root，当前业务 worker 无 task id | 是 | 人工停旧栈；否则轮换 DB/Redis 凭据并隔离 broker | 完整 24h old-root=0、远端 actor=0、当前根自然链成功 |
| c1e57c/7c472a/59acf8/1e920b/60ff74/6462f6/674016 | driver/PgBouncer/私网 fetch 偶发尾部，非 executor/索引 | 既有短 Session 与 origin telemetry | executor 0.025–35.100ms；无锁/timeout | 是，数量/累计恶化 | 下一次异常同秒抓 route/socket/pool/server | 完整窗 P95 回落且 >5s/timeout 为 0；现场链路可归因 |
| B站情报外部 I/O | DNS/TUN/外部 HTTP 断点历史未现场捕获 | 逐 competitor 隔离和 Session 外 I/O | 161/161 成功但 P95 31.667s | 性能恶化 | 补 DNS/route/实际目标/HTTP 阶段证据 | 完整窗 24/24 且 P95 恢复；失败时有同秒链路证据 |
| YouTube id=21/56 | 永久不可用来源仍启用 | 受保护人工 SQL 已存在 | channel 49/51、video errors=2 | 第 16 窗 | 人工 DBeaver 复核并执行停用脚本 | 连续两个自然周期 49/49、video errors=0 |
| Publisher actor audit | 终态 writer 历史不可归因 | deployment/host/PID/code identity 已运行替换 | 本窗 Publisher=0 | 未捕获 | 缺自然样本 | 下一次终态字段/日志一致且 actor 可定位 |

下一轮必须优先复核 old-root 和远端 actor，而不是重新从零扫描；同时复算慢 SQL、B站尾延迟和 YouTube 固定
失败源。除非物理隔离完成并有后续自然窗口，否则旧运行时问题继续保持 OPEN/REPEATED。
