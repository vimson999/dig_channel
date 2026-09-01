# 2026-08-26 数据库性能与错误巡检

## 根因闭环结论

本轮固定窗口为 `2026-08-25 06:02:19` 至 `2026-08-26 06:02:19`，与上一轮固定窗口逐项比较。扫描覆盖 `logs/slow_query.log`、`logs/app.log`、`logs/backend.log`、`logs/scheduler.log`、`logs/celery/*.log` 及轮转文件，并将 HTTP、Celery、SQL、Session 生命周期与 PgBouncer 事件按时间、request id、task id、进程角色和 SQL origin 关联。

结论不是“已解决”。本轮应用慢 SQL 273 条、累计 742.191 秒，P50/P95/最大 1.747/8.590/19.738 秒；相对上一轮 226 条、598.820 秒、1.627/7.922/17.576 秒分别恶化 20.8%、23.9%、7.4%、8.4% 和 12.3%。PostgreSQL 现场执行计划多数为 0.029–29.720ms，音频资产冷读代表计划也仅 224.979ms，无法解释应用侧 8–20 秒长尾。PgBouncer 窗口内有 9 个唯一 `connect_timeout`、1 个唯一 `query_timeout`，因此全站主根因仍是远端 PostgreSQL 链路/后端连接建立与往返尾延迟，状态保持 `OPEN/OBSERVING`。

本轮定位并落地一个复发修复：MP3 详情页首次 B站凭据请求超过浏览器 15 秒预算后，共享发布面板会成为第二 owner 自动再发一次。已用失败态共享与 single-owner 契约修复并由本地 backend 提供 v2 静态资源；由于修复发生在窗口后，只有代码、测试和静态运行边界证据，还没有修复后真实 job request id，状态只能是 `RUNTIME_REPLACED/OBSERVING`。

本轮没有执行 `CREATE/DROP INDEX`、`ALTER TABLE`、回填、生产配置或数据删除；没有裁剪原始 JSON 审计字段。

## 与上一轮基线的变化

| 指标 | 上一轮 | 本轮 | 判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 226 | 273 | 恶化 20.8% |
| 应用慢 SQL 累计 | 598.820s | 742.191s | 恶化 23.9% |
| 慢 SQL P50/P95/最大 | 1.627/7.922/17.576s | 1.747/8.590/19.738s | 全部恶化 |
| HTTP 请求 | 1,607 | 1,157 | 流量下降 28.0%，不能据此判定改善 |
| HTTP 累计/P95/最大 | 771.742s / 2.231s / 18.284s | 815.026s / 3.636s / 26.030s | 累计仍增 5.6%，P95 恶化 63.0% |
| HTTP 原始失败 | 2/1,607 | 21/1,157 | 17 条为根路径/favicon 未授权扫描；业务失败 4 条、均小于 7ms |
| Session 长持有 | 39，P95/最大 20.439/20.490s | 37，P95/最大 19.810/25.082s | 次数和 P95小幅改善，最大恶化 22.4% |
| PgBouncer 唯一 connect/query timeout | 5/5 | 9/1 | query 改善，connect 恶化 80%，未闭环 |
| PgBouncer wait/query P95 | 94.61/149.32ms | 106.70/150.29ms | 等待恶化 12.8%，查询近似持平 |
| Celery 完成/重试/失败 | 232/0/3 | 230/13/0 | 永久失败改善；13 次瞬断重试为新增 |
| 隐藏抖音/视频号历史请求 | 50/50 | 0/0 | 上轮 lazy mount 运行态有效 |
| 平台凭据/MP3 detail | 188/36 | 43/36 | 扇出显著改善，但超时后第二 owner 仍复发 |

明确分类：

- 新增：`content_pipeline_assets` 统一下载历史指纹 4 次/32.199 秒；53 个 MP3 任务中出现 13 次可恢复重试；首次凭据超时后的第二 owner 竞态被首次精确关联。
- 恶化：应用慢 SQL 全部分位数、HTTP P95/最大、PgBouncer connect timeout、MP3 宽详情指纹、B站竞争对手读取和音频资产端点累计耗时。
- 改善：PgBouncer 唯一 query timeout 5→1；隐藏抖音/视频号历史 50/50→0/0；永久 Celery failure 3→0；旧 B站全字段 JSON UPDATE 指纹保持 0。
- 反复：`HOT-PATH-PAYLOAD-POLLING` 的多 owner 请求、数据库隧道断连、音频/任务宽载荷经远端往返放大。
- 仍未闭环：网络链路、MP3 v2 真实浏览器窗口、Celery status 的 broker/control-plane 长尾、B站重复物理唯一索引人工清理、SMTP 真实失败注入和最终送达。

## 主要问题排序与根因证据

排序综合累计耗时、P95、最大耗时、失败率、受影响页面/用户与复发次数，不按出现次数单排。

| 优先级/指纹 | 量化影响 | 调用链与根因 | 状态 |
| --- | --- | --- | --- |
| P0 数据库链路 / `DB-SESSION-LIFECYCLE` | 应用慢 SQL 742.191s；PgBouncer connect/query 9/1；1 条应用 SQL 失败 20.649s | `services/mp3_video_workflow.py:3496` 在 task `ed2d424b...` 精确命中 16:49:36 PgBouncer query timeout；Celery 15s 后重投并于 1.174s 成功。现场 executor 毫秒级、无 waiting lock，根因为 PgBouncer 到远端 PostgreSQL 的连接/网络尾延迟，不是 executor 或缺索引 | `OPEN/OBSERVING`；应用瞬断恢复有效但链路未根治 |
| P0 MP3 详情/资产/凭据 | jobs 206次/154.625s；audio-assets 37次/104.209s、P95 16.096s；detail 36次/65.312s；credentials 43次/59.602s、最大24.465s | `c96aecff` detail 14.071s 后，`4987d108` credentials 24.465s 超过浏览器15s，旧面板再发 `f74541d5`。首次请求 owner 的失败状态没有共享给子面板，网络长尾把竞态暴露为重复请求 | single-owner v2 已落地；`RUNTIME_REPLACED/OBSERVING` |
| P0 Celery status / control-plane | 13次/63.946s，P95 19.582s、最大24.658s；仅6条慢 SQL合计8.645s | 鉴权 Session 已释放，但剩余主要时间在 broker inspect/worker control-plane；属于“端点很慢但无匹配慢 SQL” | `OPEN/OBSERVING`，不得加 DB 索引 |
| P1 B站竞争对手同步 | 读取指纹10次/44.942s，P95/最大12.790s；10个上传任务累计1,149.436s；旧全字段 UPDATE 为0 | 外部 B站 I/O 前已释放 Session；bvid 查询有唯一索引且上一现场 executor 0.035ms。本轮长尾发生在网络往返，旧双 scheduler 与未变化 JSON 回写未复发 | 代码根因 `RUNTIME_ACCEPTED`；网络与重复索引 `OPEN` |
| P1 浏览器导入历史 `5d0c7947...` | 4条 SQL/32.199s，P95/最大19.738s；端点21次/50.213s，1个400 | 真实 SQL 为近3日 `content_pipeline_assets` 轻量投影。现场 250 行表 Seq Scan 99 行，executor 0.258ms；失败请求是参数/业务校验且很快，不是失败风暴 | 网络 `OPEN`；查询实现有界，无加索引证据 |
| P1 音频资产 `861525...` | 14条/58.857s，P95 10.548s、最大10.829s；最差 request `3d9084b9` 26.030s | SQL origin `list_audio_assets:520`，每批上限120、总扫描上限2500，逐批后校验本地文件，不是逐项 SQL N+1。冷读 executor 224.979ms，扫描11,077 downloaded 附件、返回3,886候选并 join 12,637 topics；可优化空间存在，但仍不足解释10.5s SQL | `OPEN/OBSERVING`；先复核热缓存计划与链路，不盲加索引 |
| P2 scheduler 配置 `60ff7467...` | 26条慢样本/57.106s，P95 6.144s；上一轮24条/55.681s | `system_configs` 仅18行，executor 0.030ms；约5分钟稳定读取是已接受业务节奏，慢样本增加来自网络 | 频率 `RUNTIME_ACCEPTED`；网络仍 `OPEN` |

## 页面、API、后台任务与 SQL 关联

71 个端点共 1,157 次请求，全部完成、无 incomplete。原始失败 21 条中有 17 条是 `/` 或 favicon 的 401 扫描；业务失败为 history client 的 401/404、browser history GET 401 和 POST 400，各 1 条且均在 1.69–6.71ms，未形成失败重试风暴。按累计耗时前列如下：

| 页面/API | 请求/成功/失败 | 累计 | P50/P95/最大 | SQL 关联 |
| --- | --- | ---: | --- | --- |
| MP3 jobs | 206/206/0 | 154.625s | 0.248/3.396/13.381s | 24条/73.851s；`load_page` 宽列表与网络 |
| MP3 audio-assets | 37/37/0 | 104.209s | 1.027/16.096/26.030s | 19条/67.832s；附件+主题批量查询，无 N+1 |
| MP3 job detail | 36/36/0 | 65.312s | 0.813/5.233/14.071s | 11条/31.112s；宽 task 点查 |
| Celery status | 13/13/0 | 63.946s | 1.874/19.582/24.658s | 6条/8.645s，其余 broker/control-plane |
| 平台凭据 | 43/43/0 | 59.602s | 0.400/4.681/24.465s | 6条/30.366s；仅小表，网络+第二 owner |
| B站上传提交 | 10/10/0 | 50.318s | 3.357/22.307/22.307s | 4条/22.091s；其余投递/外部路径 |
| ZSXQ browser history POST | 21/20/1 | 50.213s | 0.536/7.135/20.256s | 5条/34.815s；轻量统一历史 |
| MP3 continue-full-video | 5/5/0 | 43.180s | 2.042/21.032/21.032s | 6条/32.363s；低频高延迟 |
| MP3 create job | 5/5/0 | 28.572s | 2.526/13.657/13.657s | 5条/7.902s；低频高延迟 |
| ZSXQ topics/local | 6/6/0 | 24.411s | 1.133/16.819/16.819s | 3条/17.639s；最差为上一轮巡检探针 |

重点 ZSXQ 链路：Insights overview 2 次，一次冷 4.253s、一次热 0.56ms，1 条慢 SQL 1.997s；topics/local/tabs 1 次 158.31ms；system-sync status 1 次 445.80ms；topics/local 6 次如上。这些请求集中在 06:08 的上一轮自动化探针，不能当作自然业务 24 小时流量，也不能由“未见更多样本”推断未发生。

单列异常：

- 低频高延迟：continue-full-video 和 create job，均成功但最大 21.032/13.657s。
- 页面/API 很慢但没有匹配慢 SQL：xiaojunjun cover regenerate 5次/16.096s、keyframe 文件并发下载若干组 P95约4.0–4.35s、B站 charging/seasons 以及 Celery status 的大部分耗时；根因落在文件/渲染、外部 B站或 broker，不在 PostgreSQL executor。
- 后台任务或连接持有很久但没有前台端点：2个托管样本。MP3 task `ea67abac...` 持有8.529s，其中 SQL 0.624s、非SQL 7.905s；Publisher task `9d1636c1...` 持有5.963s，其中 SQL2.051s、非SQL3.912s。两者均需下轮按 task id 优先复核外部/本地 I/O 是否仍与 Session 交叉。

Celery 共230次成功、13次 retry、0次永久失败、累计7,624.343s。按累计耗时：ZSXQ publish 6次/1,924.254s、Publisher 11次/1,821.002s、PDF clean 3次/1,253.637s、B站上传10次/1,149.436s、MP3 53次成功+13次retry/1,034.179s、audio clean 3次/238.541s、workflow notify 144次/203.294s。通知本窗无 retry/failure，只能证明成功路径，不能替代 SMTP 断连失败路径验收。

## Session、事务、PgBouncer 与 PostgreSQL executor

- 37个长持有的 SQL 累计387.915s，非SQL持有22.027s；35个可匹配慢SQL，2个托管未匹配样本已在上文列出。
- 角色：web 19个、worker 10个、scheduler 2个、unknown/诊断6个。无 idle in transaction、无 waiting lock、无 statement timeout、lock timeout 或 deadlock。
- 现场 role hardening 为 `statement_timeout=60s`、`lock_timeout=5s`、`idle_in_transaction_session_timeout=60s`。
- PgBouncer 24小时统计：9个唯一 connect timeout、1个唯一 query timeout；wait P50/P95/max 27.26/106.70/1,126.59ms，query 54.51/150.29/1,127.68ms。server local address 为 `100.96.3.12`，未观察代理 TUN fallback。
- 06:10 运行快照 `SHOW POOLS`：业务池 `cl_waiting=0`、`maxwait=0`、6个 `sv_used`；`SHOW SERVERS` 的6条远端连接 wait 均为0、TLS1.3。当前健康不否定窗口内9次连接建立失败。
- 应用 SQL 唯一失败为 `OperationalError`，origin `services/mp3_video_workflow.py:3496:_find_existing_publisher_video_task`，耗时20.649s；精确关联 PgBouncer query timeout，并由同一 Celery task 下一 delivery 成功，不是 PostgreSQL 语法/约束错误。

## 大分页、N+1、大字段、缓存与轮询复核

- 超大分页：未发现无上限端点；音频资产每页最大100、批次最大120、扫描上限2500。它会在缺失本地文件时分批 offset 扫描，但本轮没有逐行 SQL N+1。
- 大字段：`load_task` 指纹12次/68.789s、P95 17.697s，仍读取任务 `params/result`；PK 点查含 result 的 executor 0.029ms，当前主因是大 JSON 经远端链路传输。不得删除审计 JSON；下一步应只在调用链证明不需要时扩大有界投影。
- 重复聚合：Insights 现场 executor 29.720ms，命中既有 canonical 业务索引的累计 scan 3,513；冷4.253s/热0.56ms说明缓存有效，没有新增去重聚合修复证据。
- 缓存：Insights 热命中有效；平台凭据页面与子面板此前没有共享失败态，本轮已修复。未观察因缓存失效造成数据库失败风暴。
- 频繁轮询：隐藏抖音/视频号历史已归零；scheduler 约5分钟节奏保持；Celery status 13次没有连续分钟级隐藏序列。本轮不能关闭一般 polling 复发键。
- 外部 I/O 与 Session：B站抓取前已有 `release_db_session_quietly()`；仍有两个无匹配慢SQL的 worker 长持有样本，保持观察。
- 重复投递/失败重试：MP3 13次 retry 中精确验证一条 DB 瞬断重试后成功；未见永久失败或相同业务结果重复创建证据，但下轮需按task id去重计数，不能把retry本身当作闭环。

## 表、索引、顺序扫描与写放大

| 表 | 总大小 | live/dead | seq/idx scan | idx占比 | 判断 |
| --- | ---: | ---: | ---: | ---: | --- |
| zsxq_topics | 129MB | 12,637/1,584 | 3,508/889,132 | 99.61% | TOAST 79MB；Insights executor 29.72ms |
| publication_tasks | 126MB | 12,839/765 | 6,890/2,977,300 | 99.77% | TOAST 97MB；08-24 autovacuum、08-25 autoanalyze |
| zsxq_topic_comments | 118MB | 41,333/5,644 | 300/356,989 | 99.92% | dead约13.7%，本窗无慢SQL，不据此自动VACUUM/DDL |
| zsxq_attachments | 78MB | 33,906/37 | 593/1,134,090 | 99.95% | 音频冷计划扫描较多，但主长尾仍是网络 |
| zsxq_sync_runs | 37MB | 1,062/46 | 233/337,044 | 99.93% | system runs executor 0.851ms |
| external_documents | 7MB | 1,817/146 | 1,028/84,947 | 98.80% | 本窗无慢SQL |

目标六表无 exact duplicate index。低使用候选包括附件 group-tab PDF 5,088kB/scan1、topic group-type 3,720kB/scan1、external TTS/summary scan0，以及若干低频 sync-run 索引；统计自 2026-08-08 server start 累计且对应功能低频，没有足够证据无人值守删除。

代表性 `EXPLAIN (ANALYZE, BUFFERS)`：publication PK宽点查0.029ms、channel chart3.115ms、scheduler config0.030ms、Publisher列表0.081ms、content pipeline列表0.210ms、recent tasks宽/窄0.026/0.025ms、ZSXQ system runs0.851ms、Insights29.720ms、recent download history0.258ms、audio-assets冷读224.979ms。数据库没有 `pg_stat_statements`，历史 executor 分布仍是未捕获边界。

## 已落地修复与验证

修复前证据：详情请求 `c96aecff`、首次凭据 `4987d108` 和自动第二凭据 `f74541d5` 构成同一浏览器时序；旧 `mountBilibiliPanel()` 只在无错误时标记 panel 已加载，因此首次请求超时后 `setJob()` 会调用面板自己的 `loadCredentials()`。

代码变更：详情页成为首次凭据请求的唯一 owner，无论成功/失败都向面板传播 `credentials`、`credentialsError` 与 `credentialsLoaded=true`；静态资源升级为 `20260826-publish-credential-single-owner-v2`。没有隐藏错误、删除字段或无限重试。

验证链：

1. TDD 红灯：新增超时场景在旧代码明确得到 `duplicateCredentialLoads=1`。
2. 定向回归：Node 行为契约通过；`test_publish_platform_tabs.py` 6项通过；`node --check` 通过。
3. 运行边界：本地 `/health` 返回 healthy；真实 `/mp3-video-workflow-detail.html` 已返回 v2资源。
4. 完整 unit 首次在受限沙箱运行得到1,357通过、34失败、3跳过；34项全部因禁止绑定 `127.0.0.1`，集中在三个loopback测试文件。随后在允许本地回环socket的环境重跑完整套件：`1,383 passed, 3 skipped, 305 subtests passed`，0失败。`compileall -q src/backend`、`git diff --check`、recurrence JSON解析和 Bug strict 均以0退出。
5. 修复后尚无真实 job页面 request id；下一完整流量窗前保持 `OBSERVING`。

Bug 记录与 `HOT-PATH-PAYLOAD-POLLING` recurrence registry 已更新，防复发契约升级为：首次请求只有一个 owner，并共享 loading/success/failure 三态；浏览器超时不能让第二组件成为隐式 retry owner。

## 索引整改、待人工 SQL 与风险

本轮证据不支持新增索引。音频资产冷 executor 224.979ms 虽高于其他查询，但应用 P95 10.548s，且排序依赖 topic `create_time` 与跨表 coalesce；在没有热缓存重复计划、返回行减少和真实 predicate 命中证据前，新增单表索引很可能只增加附件写放大。

待人工动作仍为 `docs/sql/bilibili_competitor_bvid_index_cleanup_dbeaver_20260819.sql`：在 DBeaver 维护窗逐段确认两个 `(bvid)` UNIQUE 索引的定义、owner、scan和锁后，按脚本设置 `lock_timeout`/`statement_timeout`，并发删除独立重复索引，再执行验证；脚本含重建回滚。未执行前风险保持 OPEN。既有 comments hash 清理脚本风险更高，本轮 comments 无慢SQL，不提高优先级、不自动执行。

## 未捕获边界与下一轮验收计划

- `pg_stat_statements` 未安装，无法取得生产历史 executor P95；现场 EXPLAIN 只能证明抽样时状态。
- HTTP日志能关联同进程 SQL，但不能直接分解每次客户端网络、浏览器渲染、Redis broker、文件I/O与外部B站耗时。
- 修复后没有真实用户打开 MP3 job 的完整请求簇；静态资源已加载不等于行为已在真实窗口不复发。
- ZSXQ重点链路主要是上一轮巡检探针，缺自然高并发流量；未捕获不是未发生。
- SMTP 本窗全成功，未覆盖断连、单预算重试和最终送达。

下一轮按以下门槛优先续查而不是从零扫描：

1. MP3 每个首次详情 request cluster 至多1条凭据请求；首次超时后0条自动第二 owner；未切换平台时抖音/视频号历史继续为0。
2. PgBouncer connect/query timeout 必须为0/0；若 connect继续出现，按精确时间核对 Tailscale路由、远端HBA、防火墙、server local address和冷连接寿命，不能用重试掩盖。
3. `load_task`、audio-assets、browser history与B站读取分别比较累计/P95/max；audio-assets补做热缓存 EXPLAIN，只有 executor持续大于500ms且能证明扫描/排序减少才提出新索引。
4. 按 task `ea67abac...`、`9d1636c1...` 复核非SQL Session持有必须归零或给出外部I/O前显式release证据。
5. Celery status P95需低于既定12s前端预算且无隐藏态序列；否则继续追 broker inspect，而非数据库索引。
6. B站旧全字段 UPDATE保持0，单一scheduler序列保持；窄读取/更新P95目标低于500ms，人工bvid索引清理完成后保存执行前后stats/EXPLAIN/回滚证据。
7. ZSXQ overview需要新的自然冷/热样本；cold executor与端到端分别记录，热命中继续小于10ms。
8. SMTP需真实失败样本证明单预算重试、无风暴且最终送达；仅成功窗口不得CLOSED。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| PostgreSQL/PgBouncer timeout | 远端连接建立与往返尾延迟 | 既有短事务、PgBouncer热池、瞬断retry | 当前池0 waiting；1次query timeout后同task成功 | 是，connect 5→9 | 运维/链路 OPEN；精确核路由/HBA/冷连接 | connect/query 0/0且应用慢SQL分位下降 |
| MP3凭据第二 owner | 首次超时失败态未共享 | single-owner v2 + 行为契约 | backend已提供v2；修复前真实request链完整 | 是 | 缺修复后真实job流量 | 首次详情≤1凭据，超时后自动补发0 |
| MP3宽任务/资产 | 大JSON/批量附件经网络放大；附件冷扫描 | 既有有界projection和扫描上限；本轮未盲改字段 | PK 0.029ms；audio 224.979ms | 是 | 网络与音频扫描仍OPEN | 热计划、SQL P95、返回行同时量化后决策 |
| Celery status | broker inspect/control-plane尾延迟 | 既有鉴权Session释放和隐藏态门禁 | 13次，SQL仅占8.645/63.946s | 未见隐藏序列，端点仍慢 | broker OPEN | P95<12s且无隐藏轮询 |
| B站同步写放大 | 历史双scheduler+未变化JSON写；现为网络尾延迟 | OS锁、变化列更新、Session外置I/O | 旧全字段UPDATE 0；读取仍P95 12.790s | 代码未复发，链路反复 | bvid重复索引待DBA | 旧指纹0、单序列、窄P95<500ms、脚本验收 |
| browser history | 新统一历史读取经网络长尾 | 有界轻量投影 | executor0.258ms，端点最大20.256s | 新增 | 网络OPEN，无索引依据 | executor与端到端分离且无重复请求 |
| SMTP通知 | 历史同步重试风暴 | 既有单预算重试 | 144成功、0失败/重试 | 本窗未复发 | 未覆盖失败路径 | 真实断连无风暴并最终送达 |
