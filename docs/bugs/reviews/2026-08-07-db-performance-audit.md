# 2026-08-07 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 2026-08-06 06:02:19 至 2026-08-07 06:02:19（Asia/Shanghai），覆盖 `logs/slow_query.log`、`logs/app.log`、`logs/backend.log`、`logs/scheduler.log`、`logs/celery/*.log`、PgBouncer 及重叠轮转文件。应用慢 SQL 2,125 条，P50/P95/最大 2.083/7.006/60.645 秒，累计 6,332.341 秒；HTTP 6,236 次、146 个端点，成功/失败 6,115/121，失败率 1.94%，P50/P95/最大 0.239/4.029/63.788 秒，累计 5,200.974 秒。

本轮定位并修复一个应用根因：火柴人 workflow 分镜轮询被错误分类，活动态每次先读窄投影、再回读 `publication_tasks.params/result`；前端同时每 4 秒持续轮询且后台标签页不停。它在上一轮代码加载后的窗口产生 1,759 个请求、累计 1,529.272 秒，并出现两个约 62 秒的 500。代码、契约测试、完整安全重启和真实 API 已落地；新进程调用不再读宽 JSON。由于修复后还没有完整 24 小时活动任务窗口，状态为 `RUNTIME_REPLACED/OBSERVING`，不是 CLOSED。

剩余的 60 秒长尾不能归因缺索引：相同 `publication_tasks` 主键全行查询实时 executor 仅 0.032ms，目标表索引扫描占比 99.42%–99.95%，无等待锁或 idle-in-transaction；但修复前代码窗口仍有 9 个去重 PgBouncer query timeout。MP3 和 continue-full-video 也出现“应用持有约 60 秒、SQL 记录很少或 executor 毫秒级”的同类事件，连接池/网络风险保持 OPEN。本轮未执行 DDL、数据回填、生产配置或破坏性操作。

## 与上一轮基线对比

| 指标 | 本轮 | 上轮 | 判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 2,125 | 4,978 | -57.3%，改善 |
| P50 / P95 / 最大 | 2.083 / 7.006 / 60.645s | 2.046 / 6.756 / 60.772s | P50 +1.8%、P95 +3.7%，尾部未改善 |
| 应用慢 SQL 累计 | 6,332.341s | 14,245.447s | -55.5%，改善 |
| HTTP 请求 | 6,236 | 4,431 | +40.7% |
| HTTP 失败率 | 121 / 1.94% | 25 / 0.56% | 恶化；含业务失败与连接错误 |
| HTTP P50 / P95 / 最大 | 0.239 / 4.029 / 63.788s | 0.009 / 8.900 / 82.233s | P95/max 改善，中位受高频轮询构成变化影响 |
| HTTP 累计 | 5,200.974s | 6,381.035s | -18.5% |
| Celery completed/retry/failed | 774 / 135 / 38 | 889 / 306 / 176 | retry/failed 显著改善 |
| PgBouncer query/connect timeout | 10 / 1 个去重事件 | 38 / 4 | 改善但未归零 |

24 小时内 59 个 HBA/server-login-failed 均发生在 10:29 的上一轮早期故障段；从 2026-08-06 12:55:42 后为 0。它只能标记改善/观察，不能用“后半窗未捕获”宣布根治。

## 页面/API、request id 与 SQL/连接关联

上一轮修复代码后的 2026-08-06 12:55:42 至 2026-08-07 06:02:19 用于排除旧进程噪声：HTTP 5,184 次，成功/失败 5,148/36，失败率 0.69%，P50/P95/最大 0.254/2.277/63.788 秒，累计 3,248.548 秒；应用慢 SQL 554 条，累计 1,876.880 秒。

| 页面/API | 请求 | 成功/失败 | P50 / P95 / max | 累计 | 主要 SQL/根因 |
| --- | ---: | ---: | ---: | ---: | --- |
| 分镜 job 详情 | 1,759 | 1,757/2 | 0.329 / 2.505 / 62.447s | 1,529.272s | 114 条慢 SQL/427.470s；48 条宽任务回读累计 318.223s |
| 其他全部 HTTP | 3,425 | 3,391/34 | — | 1,719.276s | 文件/外部 I/O、业务错误和连接长尾混合 |

关键时序：

- `f3a215e9`：分镜请求先执行 0.574 秒窄任务和 0.989 秒输出查询，随后宽 `params/result` 查询约 60 秒 `server closed`；Session 持有 61.686 秒，HTTP 500/61.789 秒，同秒 PgBouncer query timeout。
- `8b7976fc`：相同调用链，HTTP 500/62.447 秒，Session 61.354 秒，其中日志可归因 SQL 仅 1.279 秒、未归因等待约 60.075 秒，同秒 PgBouncer timeout。
- `013da1cc`：MP3 audio-assets HTTP 500/63.513 秒，Session 63.443 秒，其中已记录 SQL 3.215 秒、未归因约 60.229 秒；同秒 PgBouncer timeout。查询最多 40 行但仍超时，保持连接层 OPEN。
- `0d34b836`、`9f0ad4ea`：MP3 详情/列表在约 60 秒后 query timeout；详情用 fallback 返回 200，列表 503。Session 已记录 SQL 0.595/0.135 秒，未归因约 60 秒。
- `82beddb5`：continue-full-video 的任务主键宽查询 60.645 秒，HTTP 200/63.788 秒；相同 SQL 实时 executor 0.032ms，指向连接/PgBouncer/网络停顿。

修复后真实 request `a564329e`：分镜历史失败任务返回 200/898.91ms；users 鉴权 546.51ms、任务窄投影 26.09ms、output 26.18ms，没有宽 `params/result` 回读。应用根因已经由新进程替换，但鉴权仍有约 0.55 秒链路等待，需要继续按全站基线观察。

## SQL 指纹、Session 与后台任务

- 修复代码后分镜宽指纹 `api/report_visual_plan.py:_get_owned_visual_task` 48 次，P50/P95/最大 3.579/29.418/60.192 秒，累计 318.223 秒；它是本轮按累计、P95、最大和用户影响综合排序的首要应用问题。
- 同窗口 Session 持有 105 次，P50/P95/最大 9.876/61.635/441.052 秒；12 次来自 ad-hoc。托管进程 17 次长持有没有匹配慢 SQL，说明慢 SQL 日志不能覆盖连接获取和网络停顿。
- PgBouncer 后半窗 9 个去重 query timeout；wait P95 87.55ms、query P95 167.78ms、最大 query 5.060s。没有后半窗 HBA、server-login-failed 或 connect timeout。
- statement timeout、lock timeout、deadlock、日志型 idle-in-transaction 均为 0；实时 `pg_stat_activity` 也没有 idle-in-transaction 或等待锁。这只是当前证据边界，不表示从未发生。
- Celery 后半窗 569 completed、132 retry、33 failed；workflow notification 148 success/127 retry/31 failure，多为邮件外部依赖，不是数据库 executor。ZSXQ sync 16 次全部成功、累计 4,954.780 秒。

## 三类独立异常

### 低频高延迟

- continue-full-video 单次 63.788 秒，任务主键查询 60.645 秒；executor 复测 0.032ms。
- MP3 audio-assets、详情和列表各出现约 60–63 秒事件，部分返回 fallback 200，存在成功码掩盖退化。
- 后台 ZSXQ 16 次成功但 P95 长尾仍高；最长业务任务不能用前台端点统计覆盖。

### 页面/API 很慢但没有匹配慢 SQL

- 分镜 `8b7976fc`、MP3 `0d34b836`/`9f0ad4ea` 都有约 60 秒未被慢 SQL duration 覆盖的 Session 时间；优先检查 PgBouncer server connection、TCP/公网和连接获取。
- 下载、外部平台、邮件通知的耗时可能发生在文件或外部 I/O；没有 SQL 匹配不能报告“无问题”。

### 后台任务或连接持有很久但没有前台端点

- 后半窗 17 次托管长 Session 无匹配慢 SQL，另有 12 次 ad-hoc 长持有；最长 441.052 秒。
- ZSXQ sync 任务累计近 4,955 秒，workflow notification retry/failure 为外部邮件风暴；均需独立于 HTTP 统计。

## ZSXQ / 生财有术重点链路

本窗口没有捕获 `zsxq-insights overview`、`topics/local`、`topics/local/tabs`、`sync/status` 的真实前台请求，因此不能声明这些端点健康或改善。

上一轮页内 N+1 修复获得新运行态证据：旧 `_update_existing_topic`、`_update_attachment_download_state`、逐条 `_upsert_topic_comments` origin 在 12:55:42 后消失；新 `_apply_existing_topic_updates` 21 条慢 SQL/45.970 秒，评论批量更新主要形态 18 条/24.746 秒。真实外链批次出现 43 个 URL hash、108 条关系，证明不再逐 topic N+1；但一次 108 行关系批读仍耗时 8.671 秒，保留远端长尾观察。

最新完整任务 `44d1539a-ead8-4426-992c-56d7a73d6c35` 于 04:42:48 完成：34 页、610 topics、3 新增、368 更新、891 attachments、0 failure。旧指纹未复发且真实完整任务通过，页内 N+1 可标 `RUNTIME_REPLACED/OBSERVING`；仍需下一窗口确认远端批量查询 P95。

## 程序模式复核

- 超大分页/大字段：首要新增证据是分镜活动轮询的宽任务回退，已去除；原始 JSON 和业务字段完整保留。MP3 部分详情/继续任务仍有必要的宽读取，但 60 秒连接长尾需继续拆解。
- N+1：ZSXQ 评论、主题和外链已显示页级批量运行态；分镜原本是每请求重复宽回读，不是数据库行级 N+1，但在页面维度形成高频往返放大。
- 重复聚合：Insights canonical 对当前规模的 Seq Scan + Sort 为 34.026ms，暂无索引收益证据。
- 缓存/轮询：分镜前端从 4 秒改为 15 秒，加入 single-flight 和 Page Visibility；真实活动页面隐藏/恢复尚待下一任务验收。
- 外部 I/O 与 Session：后半窗 60 秒未归因时间和邮件 retry 继续证明必须遵守“事务快照 → 释放 Session → 外部 I/O → 窄写回”。

## 表规模、扫描、写放大与索引证据

统计为 PostgreSQL 启动后的累计值：

| 表 | 表 / TOAST / 索引 / 总大小 | live / dead | seq / idx scan | 索引占比 | HOT |
| --- | --- | ---: | ---: | ---: | ---: |
| `zsxq_topic_comments` | 91MB / 4.24MB / 23MB / 118MB | 40,927 / 3,335 | 175 / 239,217 | 99.93% | 63.28% |
| `zsxq_topics` | 13MB / 74MB / 31MB / 118MB | 11,147 / 911 | 2,272 / 671,904 | 99.66% | 0.97% |
| `publication_tasks` | 17MB / 86MB / 11MB / 114MB | 11,467 / 1,557 | 2,487 / 2,502,148 | 99.90% | 1.44% |
| `zsxq_attachments` | 30MB / 0.04MB / 36MB / 66MB | 30,739 / 3,176 | 446 / 959,023 | 99.95% | 0.13% |
| `zsxq_sync_runs` | 1.07MB / 31MB / 0.864MB / 32MB | 719 / 17 | — | 99.94% | 0.34% |
| `external_documents` | — / — / — / 6.912MB | 1,727 / 146 | — | 99.42% | 0% |

实时只读 EXPLAIN：任务主键全行 0.032ms、频道图表 1.635ms、scheduler config 0.032ms、publisher list 0.070ms、content pipeline 0.569ms、近期任务含/不含 JSON 0.024/0.044ms、ZSXQ system runs 1.249ms、Insights canonical 34.026ms。没有精确定义重复索引，`pg_stat_statements` 未安装。

Insights 索引 `ix_zsxq_topics_group_topic_preferred_synced_desc` scan=1,064，较上轮 917 增加 147，继续保留。附件 group-tab PDF、topic group-type/group-created、publication waiting-label 等低使用索引的统计窗口仍短且可能服务低频路径；没有真实 SQL + 双周期使用量 + EXPLAIN 支持 DROP。

## 已落地修复与验证

1. 后端按实际 workflow feature 识别分镜任务，无输出活动态不再回读 `params/result`。
2. 前端加入 single-flight、隐藏暂停、可见恢复，活动分镜周期 4 秒调整为 15 秒；cache-bust 已更新。
3. 定向 150 tests 与 JavaScript syntax check 通过；完整 unit、compileall、diff check 和 Bug 严格检查见本报告最终验证结果。
4. 重启安全门确认 47 个活动 publication task、0 个 ZSXQ sync 均可安全重启；开发栈完整重载，backend health 正常，两个 visual-plan worker、ZSXQ worker 及相关 worker 均 ready。
5. 真实 API request `a564329e` 验证新查询形态，无宽 JSON 回读。
6. 重载后 06:08:26–06:15:30 小窗口：13 个 HTTP 请求、分镜 1 次/200/898.91ms，`publication_tasks` 慢 SQL 为 0，PgBouncer timeout/HBA/SQL error 均为 0；但 ZSXQ 有 9 条批量慢 SQL（最大 7.314s）、6 个 `server closed` 事件和 2 次 retry。该短窗证明分镜代码已替换，不能替代连接层和 ZSXQ 的完整窗口验收。
7. 完整 unit 1,049 tests/41.018s 全部通过；`compileall`、JavaScript syntax check、`git diff --check`、registry JSON 校验和 Bug 严格检查全部通过。Bug 记录 152 条均已归类，最近回顾日期为 2026-08-07。

## 索引整改建议、待人工 SQL 与风险

- 本轮没有任何 CREATE/DROP INDEX、ALTER TABLE 或回填证据，禁止无人值守 DDL，也不生成新 SQL 脚本。
- 启动 precheck 仍提示 `20260724_comment_hash_indexes` 未记录且存在等价 topic/comment 索引。只允许管理员在 DBeaver 复核既有 `docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql` 的 owner、等价定义、使用量、lock timeout 和回滚步骤后逐条执行；当前巡检不代执行。
- 低 HOT 和大 TOAST 显示写放大风险，但删除原始 JSON 不被允许；治理方向应是减少无变化写入和不必要物化。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `VISUAL-STORYBOARD-WIDE-POLL` | workflow feature 漏入宽 JSON fallback；4 秒隐藏页轮询 | feature 集合、窄 DTO、15 秒 single-flight、Visibility | 新 PID API 200/898.91ms；任务/输出 26.09/26.18ms，无宽回读 | 本轮复发；修复后单次未复发 | OBSERVING；需要真实活动页和完整窗口 | 宽 origin=0，端点无 60s timeout，隐藏页停止请求 |
| `PGBOUNCER-QUERY-TIMEOUT-60S` | PgBouncer/server connection/公网等待，非 executor | 应用乘数已降低；未用重试掩盖 | 后半窗 9 个去重 timeout；executor 0.032ms | 是 | OPEN；管理员/下一轮采集 pool/server/network 时序 | query timeout=0，长 Session 与 socket 证据闭合 |
| `MP3-60S-NETWORK-STALL` | 窄/有限行查询仍遭 60 秒连接停顿 | 本轮未改业务语义 | 三个 request id、同秒 PgBouncer timeout | 新增 | OPEN；继续定位 PgBouncer server socket | 详情/列表/audio-assets 无 60 秒事件并有真实调用验收 |
| `ZSXQ-PAGE-RECONCILIATION-NPLUSONE` | 主题/评论/外链逐条往返 | 上轮页级批量 DTO/更新 | 34 页/610 topics 成功；真实 43 URL/108 relation 批次；旧 origin=0 | 未见旧指纹 | RUNTIME_REPLACED/OBSERVING；远端批次仍 8.671s | 下一完整同步旧 origin=0，批次 P95 可控 |
| `DB-IDLE-IN-TRANSACTION` | 历史 Session 生命周期复发 | 上轮观测门禁保留 | 本窗口日志0、实时0 | 未捕获 | OBSERVING；未捕获不是未发生 | 连续完整窗口=0；若复发有 offender/origin/tx age |
| `POSTGRES-HBA-EGRESS` | 出口 IP 白名单变化 | 本轮无配置权限 | 24h早段59；12:55后0 | 早段反复，后段未见 | OBSERVING；管理员维护白名单 | 连续窗口 HBA/server login=0 |

## 未捕获边界与下一轮计划

- 没有 `pg_stat_statements`，无法从 PostgreSQL 累计 executor 视角覆盖所有历史语句；当前使用应用 SQL、request id、PgBouncer、Session 持有和实时 EXPLAIN 交叉归因。
- `overview/topics/local/tabs/sync/status` 本窗口没有真实请求；下一次出现后才建立端点基线。
- 修复后真实 API 是历史失败任务，不会触发持续活动轮询；下一条活动分镜必须验证可见 15 秒、隐藏停止和终态停止。
- 下一轮优先检查：分镜宽 origin 是否为 0；PgBouncer query timeout 和 60 秒 Session 是否归零；MP3 三条路径是否复发；ZSXQ 旧逐条 origin 是否继续为 0；HBA、idle-in-transaction 是否连续无事件。任何一项未满足都保持 OPEN/OBSERVING，不得宣称解决。

## 当日追加：分镜 ambiguous commit 假失败

- 修复前：workflow `826dcf81-d2a7-4906-abed-221418d019ce` 与分镜子任务 `68d9acc2-2e0d-45b7-a967-370124a659c6` 被写为 failed，素材端点返回 409；数据库实际已有 1 条完整 output、36 条连续 segment 和 36 条 asset，图片文件为 0/36。错误指纹为 `Can't reconnect until invalid transaction is rolled back`。
- 根因：分镜产物事务已在 PostgreSQL 端提交，但客户端在确认 commit 时断连；旧异常路径未在新 Session 中验证 durable 产物，直接用 `_fail` 覆盖成功投影。API 又只信任务状态，形成“产物完整但状态失败”的分裂。
- 已落地：worker、复用入口和 API 共享 durable storyboard 完整性契约；提交结果不确定时先释放失效 Session，再验证覆盖字数、数量、连续序号、文案和提示词，验证成立后幂等恢复并只投递缺失图片，不重复请求 Gemini。
- 定向 85 tests、DB/Session/cache 87 tests、完整 unit 1,052 tests/41.270s，以及 compileall、diff、registry JSON、Bug 严格检查全部通过。
- 运行态：历史任务对账成功；36/36 图片落盘，序号 7 的一次 Gemini 等待超时由自动重试恢复；重载后页面为 100%、图片 36/36、正文对齐 35/35，最终视频 308.2 秒/1280×720/8.2 MB，图片请求 200，原素材 409 消失。安全重载后 backend health healthy，相关 worker ready。
- 本次没有生产 DDL。该指纹状态为 `RUNTIME_REPLACED/OBSERVING`，不是 CLOSED；下一窗口必须确认相同 commit 错误不会再把完整产物写成 failed、素材 409 为 0、reconciler 不重复外部调用。

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `VISUAL-STORYBOARD-AMBIGUOUS-COMMIT` | commit 结果不确定后用失效状态投影覆盖 durable 成功产物 | 共享完整性契约、新 Session 对账、幂等恢复、API 按 durable 证据识别 | 36/36 图片、35/35 对齐、视频完成，重载后页面与资源请求正常 | 本轮确认既有纪律复发；修复后指定任务未再现 | `RUNTIME_REPLACED/OBSERVING`；仍需跨下一窗口验证 | 同错误不产生假 failed/409；reconciler 无重复 Gemini；完整产物持续可读 |
