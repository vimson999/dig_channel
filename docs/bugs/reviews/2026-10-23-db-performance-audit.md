# 2026-10-23 数据库性能与错误巡检

## 结论

固定窗口为 2026-10-22 06:00:00 至 2026-10-23 06:00:00（Asia/Shanghai，数据库边界
2026-10-21 22:00:00 至 2026-10-22 22:00:00 UTC）。本轮不能写成“已解决”：PostgreSQL executor、锁与
PgBouncer 错误面保持绿色，但 P0 的共享旧执行器复发从上一窗 `foreign_actor_evidence=1/9` 恶化为
`12/30`；Publisher 30 条中 14 条失败，失败率 46.7%。音频目录 SQL 次数下降，但单次 P95/max 升至
8.271/8.380 秒，仍是 executor 外 driver/fetch/PgBouncer/私网尾部。另新增两条 B站 21523，根因是任务
保存的充电档位 ID 已从账号权威选项中消失，不能用重试或静默替档规避。

本轮落地的是根因证据、复发门禁、Bug 与纪律更新；没有足够证据做索引 DDL，也没有安全的自动业务配置变更。
旧执行器物理隔离、B站档位重选和 YouTube 两源停用都需要人工动作，相关项保持 OPEN。

## 与上一轮的量化对比

| 指标 | 2026-10-22 基线 | 本轮 | 变化与判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 16 / 41.431s | 26 / 51.023s | 数量 +62.5%，累计 +23.2%，恶化 |
| 慢 SQL P50 / P95 / max | 1.322 / 8.006 / 8.269s | 1.151 / 8.271 / 8.380s | P50 -12.9%，P95 +3.3%，max +1.3% |
| audio catalog | 3 / 24.109s | 2 / 16.652s | 次数 -33.3%、累计 -30.9%，但单次尾部略恶化 |
| 完成 HTTP | 592，成功 585 | 726，成功 717、失败 9 | 流量 +22.6%，失败率 1.24% |
| HTTP P50 / P95 / max | 2.67ms / 1.207s / 68.938s | 2.94ms / 1.618s / 48.920s | P95 +34.0%，max -29.0% |
| Publisher durable | 9：8 成功/1 失败 | 30：15 成功/14 失败/1 取消 | 失败率 11.1%→46.7%，严重恶化 |
| foreign actor | 1/9 | 12/30 | 11.1%→40.0%，反复且恶化 |
| B站情报周期 | 23/23，161/161 | 24/24，168/168 | 成功率稳定；P95 16.356→16.785s |
| YouTube | channel 49/51，video errors=2 | channel 49/51，video errors=2 | id 21/56 第 20 窗复发 |

## SQL、页面/API 和后台任务关联

### SQL 与连接

- 26 条慢 SQL 累计 51.023 秒，P50/P95/max 为 1.151/8.271/8.380 秒；13 条涉及
  `publication_tasks`，`zsxq_topics` 1 条，其余为任务、通知和配置点查/聚合。
- 首位指纹 `be891b5c1af362da` 来自
  `services/mp3_video_workflow.py:495:_build_audio_asset_catalog_snapshot`：2 次累计 16.652 秒，P95/max
  8.271/8.380 秒。对应 2 次连接持有 P95/max 8.758 秒，SQL 16.652 秒、非 SQL 558ms；没有文件 I/O
  跨 Session、旧附件 N+1 或 deferred lazy-load 证据。
- 其他主要指纹为 all-tabs 配置 2.815 秒、B站情报 competitor sync 2.320 秒、workflow notification 两次
  2.211 秒、stick repair 2.200 秒、report-audio recovery 两次 2.177 秒、finalization 2.172 秒、Insights
  aggregate 2.087 秒、Publisher repair 2.030 秒。
- SQL error、statement timeout、lock timeout、connection timeout、deadlock、持续 idle in transaction 均为 0；
  runtime guard 只有 23 个短暂观测，最大 2 个连接、事务龄 0.821 秒、idle 0.783 秒，没有 pressure defer。
- PgBouncer 八类错误仍全 0，1,440 个统计样本、288 次建连（12/h）都来自 100.96.3.12；wait
  P50/P95/max 28.96/52.05/510.44ms，query 65.26/80.96/449.93ms。P95 正常但 max 尖峰较上轮恶化，
  仍需与自然冷建同秒关联，不能把“错误为 0”解释为链路无尾延迟。

### 页面与 API

固定窗解析到 726 个完成请求、95 个端点，另有 1 个不完整请求；717 成功、9 失败，累计 307.480 秒。

- 低频高延迟且无匹配慢 SQL：`POST /api/v1/videos/sync` 1 次 48.920 秒、
  `POST /api/v1/channels/sync/all` 1 次 22.031 秒、`POST /api/v1/content/web-submit` 1 次 12.922 秒。
  它们是外部 API/工作流等待，不是 PostgreSQL executor 慢。
- `GET .../audio-assets` 5/5 成功，累计 18.647 秒、P95/max 9.405 秒；其中 2 条慢 SQL 累计
  16.652 秒，页面尾部可直接关联到 catalog 指纹。
- jobs detail 31/31 成功，累计 21.885 秒、P95 1.190 秒，仅 1 条 1.008 秒慢 SQL；uploads 45/45
  成功，累计 19.475 秒、P95 0.946 秒，匹配 1 条 1.147 秒慢 SQL。
- B站 upload job 创建 13/13 成功，累计 21.538 秒、P95 1.872 秒；充电选项 10 次有 1 次失败，固定窗内
  没有数据库错误证据。两个 Publisher 21523 出现在真正投稿阶段。
- 重点 ZSXQ 页面在固定窗没有足够自然样本，不能宣称“没有问题”。窗后真实验收：health 200/47ms、overview
  200/3.658s、topics/local 200/0.912s、topics/local/tabs 200/0.295s、sync/runs 200/0.406s、
  scheduler status 200/0.632s。它们只证明当前可用性，不替代下一自然窗统计。

### 后台任务与无前台端点异常

- Celery 日志识别 202 个成功、0 retry、2 个失败。Publisher pipeline 17 个成功，累计 1,903.425 秒、
  P95/max 304.845/319.228 秒；upload-only 10 成功/2 失败，成功累计 1,427.152 秒、P95/max 273.025 秒。
- durable 快照比日志更完整：Publisher 30 条，15 completed、14 failed、1 canceled，累计 7,806.352 秒，
  P50/P95/max 254.122/436.801/551.027 秒。14 个失败中 9 个是素材不可达、3 个是缺视频、2 个是 21523。
- 9 个素材不可达和 3 个缺视频终态都缺 actor identity，但引用文件在当前节点存在且 mtime 不晚于关闭时间，
  所以 `foreign_actor_evidence=12`。本地日志只有 queue-visible/领取后已终态，没有失败写入；这是“后台任务
  很久/失败但没有前台端点”的最高优先级异常。
- MP3 workflow 81 个成功，累计 636.049 秒、P95 26.299 秒；ZSXQ publish 1 个成功 590.267 秒；audio
  clean 1 个成功 197.228 秒；notification 92 个成功 249.103 秒。durable 通知为 71/71 sent、max attempt 1。
- ZSXQ publish 四轮 1059 后最终 30/30 selected/uploaded/submitted/verified、4 topics verified；这是有界恢复，
  不是失败风暴，保持 OBSERVING。

## 根因复核与已落地处置

### 1. 共享旧执行器 / 日志外 writer

修复前证据是上一窗 1/9，本窗直接恶化为 12/30。12 条终态都绕过了当前代码已要求的 actor 审计，且当前
节点素材真实存在；当前路径字符串、当前进程快照和巡检时远端连接为 0 都不能证明 writer 归属。Tailscale
在巡检时显示旧 MacBook Pro offline、last seen 约 8 小时，恰晚于本窗 16:49–17:36 的失败段；这仍只是
窗后状态，不是物理停用证明。

现有两类 actor identity、路径与 mtime 日快照门禁正确发现复发，属于 RUNTIME_REPLACED；但代码无法撤销旧机
共享的 DB/Redis 凭据。状态保持 **OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**。必须人工停止旧 Mac
backend/Celery/Watchdog/scheduler/脚本；不能证明全停时，轮换 PostgreSQL/Redis 凭据并隔离 broker namespace。

### 2. audio catalog executor 外尾延迟

单次有界投影、DTO 冻结、查询后释放 Session、Session 外文件校验与 300 秒缓存仍生效；旧 N+1 没有复发。
同形生产 EXPLAIN 的 executor 基线为 5.182ms，而本窗应用单次约 8.3 秒，差距约 1,600 倍；hold 的非 SQL
只有 558ms。状态保持 **RUNTIME_REPLACED / OPEN / REPEATED / OBSERVING**，具体 driver fetch、网络和
PgBouncer socket 分段仍缺失。本轮没有用加 timeout、延长缓存或盲目索引掩盖它。

### 3. B站已失效充电档位

两条失败任务都保存 `upower_level_id=1424170587911438656`。当前账号真实 charging-options 只返回
`1357406026810593731`、`1381310463006055021`、`1431031586035418859`，原值已失效；B站准确返回
21523。已新增 Bug `BILIBILI-STALE-UPOWER-TIER` 并把“提交前释放 Session 后复核权威 ID、失效时要求重选、
禁止静默替换收费档位”加入纪律。由于新档位涉及用户收费选择，本轮没有自动改参数。状态
**OPEN / REPEATED / WAITING_USER_RESELECT**。

### 4. 外部来源复发

- YouTube id 21/56 第 20 窗仍为 channel 49/51、video errors=2；已有受保护 SQL，只能人工 DBeaver 复核执行。
- B站情报 24/24 周期、168/168 competitor 成功，累计 331.544 秒，P50/P95/max
  13.480/16.785/17.171 秒；P95 较上轮 +2.6%、max -0.9%，无失败和匹配慢 SQL。
- 21173 本窗为 0，是第二个绿窗；执行前余量合同尚未落地，因此原根因保持 OPEN/OBSERVING。

## 表规模、索引与 EXPLAIN

| 表 | 总大小 | live/dead | 索引命中 | 结论 |
| --- | ---: | ---: | ---: | --- |
| `publication_tasks` | 151MB（table 18MB、TOAST 118MB、index 14MB） | 14,585 / 15 | 99.83% | 大 JSON/TOAST 是宽行风险，当前代表点查正常 |
| `zsxq_topics` | 132MB | 13,847 / 1,621 | 99.52% | Insights 聚合可顺扫，但 executor 40.605ms |
| `zsxq_topic_comments` | 119MB | 45,350 / 5,546 | 99.85% | 无本窗慢指纹 |
| `zsxq_attachments` | 79MB | 35,870 / 71 | 99.94% | 无本窗慢指纹 |
| `zsxq_sync_runs` | 37MB | 1,074 / 58 | 99.90% | 代表顺扫 1.531ms |
| `external_documents` | 7.0MB | 1,817 / 146 | 98.74% | 无本窗慢指纹 |

没有完全重复索引。低使用索引中 `zsxq_attachments` 的 PDF/group/tab 索引约 5MB、scan=1，
`zsxq_topics` group/type 索引约 3.8MB、scan=1，但统计自 8 月以来且缺真实写放大/替代计划，不能据此删除。

本轮生产只读 `EXPLAIN (ANALYZE, BUFFERS)`：publication PK 含 result 0.043ms、chart 2.982ms、all-tabs
0.024ms、Publisher list 0.040ms、内容列表 0.188ms、recent tasks 宽/窄 0.024/0.035ms、sync runs
1.531ms、Insights 顺扫/窗口 40.605ms。当前无 waiting lock，`pg_stat_statements` 未安装。应用 2–8 秒与
executor 毫秒级不一致，故本轮不生成 CREATE/DROP INDEX、ALTER TABLE 或回填脚本。

待人工 SQL 只有既有
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`：应在 DBeaver 逐条确认只作用于 id 21/56，按
脚本内 lock/statement timeout、验证与回滚说明执行。B站档位是用户语义选择，不应通过 SQL 猜测替换。

## 验证、重启判断与未捕获边界

- 重启安全门 `scripts/dev/check-restart-safety.py` 返回 OK，无不可中断任务。当前 backend 已监听且 health 200；
  本轮没有生产代码变更，所以没有用重启制造无意义风险。
- 窗后重点只读 E2E 六个端点全部 200；当前 DB 无 waiting lock，实时会话仅本机/当前 Mac mini 的 idle
  ClientRead。旧 Mac 当前离线和当前远端连接为 0 只算窗后观察，不能关闭同窗 foreign actor。
- 定向回归 90/90、完整 unit 1,740/1,740 通过；`compileall`、Celery revoke 边界（326 个 Python 文件）、
  worker app factory 边界（28 个文件）、Bug strict（212/212 已归类、9 个复发簇）、recurrence JSON 与
  `git diff --check` 全部通过。完整 unit 首轮在受限沙箱内仅因 34 个 loopback bind `PermissionError` 未通过，
  允许本机临时端口后同一命令全绿；任何测试通过都不替代下一完整运行窗。
- HTTP 关联只覆盖日志可识别请求与 >=1 秒慢 SQL；亚秒 N+1、driver 内部 fetch/deserialize、浏览器真实渲染、
  窗口外请求与未写日志的旧 actor 均可能未捕获。固定窗无重点 ZSXQ 自然流量、`pg_stat_statements` 不可用，
  因此不能把“未匹配慢 SQL”误报为“没有数据库/链路问题”。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `foreign_actor_evidence` / shared runtime | 旧 Mac 仍可共享生产 DB/Redis/Celery，文件本地性语义不一致 | 两类终态 actor 审计、mtime recurrence gate | 12/30，失败文件当前节点存在，本地无 writer 日志 | 是，严重恶化 | 人工停旧栈；否则轮换凭据并隔离 namespace | 完整 24h remote/foreign/old-root 全 0 + 当前根 PDF→ZSXQ、MP3→Publisher 自然成功 |
| `be891b5c1af362da` | executor 外 driver/fetch/PgBouncer/私网尾部，物理断点未拆开 | O(1) 投影、Session 外文件校验、300s cache | 2 次/16.652s；executor 5.182ms；hold 非 SQL 558ms | 是 | 增加 pool/fetch/bytes/socket 分段；禁止盲目索引 | 自然冷建分段齐全、暖请求 SQL=0、连续完整窗高尾消失 |
| `BILIBILI-STALE-UPOWER-TIER` | durable 外部档位 ID 在执行前已失效 | 原错 durable 化；Bug/纪律门禁已加 | 2 个 21523；保存 ID 不在当前权威集合 | 是 | 用户重选；代码补执行前权威复核与可重选合同 | 定向回归 + 真实重选投稿成功 + 后续完整窗 21523=0 |
| YouTube id 21/56 | 远端长期 0 results，但本地仍启用 | 受保护停用 SQL 已生成 | 第 20 窗 channel 49/51、video errors=2 | 是 | 人工 DBeaver 仅停 21/56 | 连续两个自然周期 channel 49/49、video errors=0 |
| `SCHEDULED-UPLOAD-TIME-MARGIN` | 入口五分钟校验未包含排队/上传预算 | 失败 durable 化，根因代码未修 | 本窗 21173=0，第二绿窗 | 否（本窗） | 补执行前余量合同，禁止静默改时点 | 定向测试 + 边界自然投稿 + 后续完整零复发窗 |
| ZSXQ 1059 | 外部上传瞬时失败 | 有界轮次、逐文件最终验证 | 四轮后 30/30 verified，durable completed | 瞬时发生 | 监控最终失败率，禁止无界 retry | 下一窗最终 failed=0 且不形成失败风暴 |

下一轮先查 `foreign_actor_evidence` 与 16:49–17:36 同类终态；若旧机仍无物理隔离证据，继续追稳定 host/
deployment identity 和 shared broker 投递，不重新从零扫描。随后补 audio catalog 分段 telemetry 证据，复核
21523 重选/合同、YouTube 21/56 人工动作，并用自然重点页面而非仅窗后探针完成验收。
