# 2026-08-11 数据库性能与错误巡检

## 结论

本轮不能结论为“全部已解决”。固定窗口 `2026-08-10 06:02:19` 至 `2026-08-11 06:02:19` 内，应用慢 SQL 816 条，P50/P95/最大为 1.550/5.525/16.206 秒，累计 1,784.288 秒；较上一轮数量增加 21.4%、累计耗时增加 14.0%，但 P95 改善 3.9%。HTTP 5296 次，失败 48 次（0.91%），P95 2.398 秒、最大 25.865 秒、累计 2729.635 秒；失败率改善 0.16 个百分点，P95 却恶化 39.2%。

主要结论：

1. **新增并已落地根因修复：Celery 状态接口跨 broker inspect 持有鉴权 Session。** 两次真实请求耗时 22.773/25.865 秒，SQL 累计仅 159.93/110.18 毫秒，非 SQL 持有 22.135/24.655 秒；均在约 20 秒后遭 PgBouncer 断链。入口已在外部 I/O 前释放 Session，回归和新 PID 真实请求证明不再跨 inspect 持有连接、也未再出现 server closed；但真实请求仍为 24.369 秒，暴露第二层串行 inspect 放大。
2. **新增第二层修复：三次 Celery inspect 串行广播改为独立连接并行。** 修复前新 PID 真实端点 24.369 秒；修复后的全新进程真实控制面基准为 3.573 秒。因再次重启门禁被 PDF 清洗和生财同步阻断，端点进程尚未加载第二层修复，状态保持 `CODE_CHANGED / RUNTIME_COMPONENT_VERIFIED / OBSERVING`。
3. **明确复发：共享模块变更后旧 Celery worker 未重载。** `mp3_video_workflow` worker 连续 5 次报 `DEFAULT_INITIAL_SCRIPT_MIN_CHARS` ImportError。这是 `RUNTIME-TOPOLOGY-RELOAD` 的第 2 次确认复发。5 条业务任务后来均为 `completed/done/100%`，但不能据此把混版根因视为未发生。
4. **改善但未闭环：** PgBouncer unique query timeout 从 46 降至 6（-87.0%）；连接长持有 P95 从 75.166 降至 18.826 秒、最大从 76.160 降至 58.939 秒；失败 SQL 时长已能正确区分尾部失败和前置非 SQL 持有。
5. **反复/仍未闭环：** `publication_tasks` 慢 SQL 从 224 增至 341（+52.2%）；HTTP P95 恶化；PostgreSQL/PgBouncer 网络长尾仍使毫秒级 executor 查询在应用侧达到秒级。索引证据不支持盲目新增索引。

## 基线、范围与边界

- 上一轮基线：`2026-08-09 06:02:19` 至 `2026-08-10 06:02:19`。
- 本轮固定窗口：`2026-08-10 06:02:19` 至 `2026-08-11 06:02:19`；避免巡检过程中新增的重启和验收日志污染同比。
- 扫描：`logs/slow_query.log`、`logs/app.log`、`logs/backend.log`、`logs/scheduler.log`、`logs/celery/*.log` 和 PgBouncer 原始事件。
- 数据库：只读 `pg_stat_user_tables`、`pg_stat_user_indexes`、会话/锁和代表查询 `EXPLAIN (ANALYZE, BUFFERS)`；未执行 DDL、回填或生产配置变更。
- 未启用 `pg_stat_statements`，无法以 PostgreSQL 端长期累计视角覆盖所有 SQL；日志没有 SQL/request id 的事件只能按时间、进程和 origin 近似关联。
- “没有匹配慢 SQL”表示当前日志中未匹配，不表示数据库、PgBouncer、网络或外部 I/O 没有发生等待。

## 与上一轮量化对比

| 指标 | 上一轮 | 本轮 | 变化 | 判断 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL | 672 | 816 | +21.4% | 恶化 |
| 慢 SQL P50 | 1.590s | 1.550s | -2.5% | 小幅改善 |
| 慢 SQL P95 | 5.748s | 5.525s | -3.9% | 改善但仍高 |
| 慢 SQL 最大 | 15.047s | 16.206s | +7.7% | 恶化 |
| 慢 SQL 累计 | 1564.712s | 1784.288s | +14.0% | 恶化 |
| HTTP 请求 | 5057 | 5296 | +4.7% | 流量增加 |
| HTTP 失败 | 54 | 48 | -11.1% | 改善 |
| HTTP 失败率 | 1.07% | 0.91% | -0.16pp | 改善 |
| HTTP P95 | 1.722s | 2.398s | +39.2% | 恶化 |
| HTTP 最大 | 33.577s | 25.865s | -23.0% | 改善但仍高 |
| HTTP 累计 | 2180.839s | 2729.635s | +25.2% | 恶化 |
| 连接长持有事件 | 154 | 148 | -3.9% | 小幅改善 |
| 长持有 P95/最大 | 75.166/76.160s | 18.826/58.939s | 显著下降 | 改善未闭环 |
| PgBouncer unique query timeout | 46 | 6 | -87.0% | 显著改善，仍非 0 |

按重点表统计，`publication_tasks` 341 条（+52.2%）、`zsxq_sync_runs` 3 条（-40.0%）、`zsxq_topics` 103 条（+10.8%）、`zsxq_topic_comments` 94 条（-14.5%）、`zsxq_attachments` 37 条（+76.2%）、`external_documents` 5 条（+66.7%）。

## 主要问题排序

排序综合累计耗时、P95、最大耗时、失败率、用户/页面影响和复发次数，不只看出现次数。

| 优先级 | 问题/指纹 | 次数 | 累计/P95/最大 | 影响与复发 | 状态 |
| --- | --- | ---: | --- | --- | --- |
| P0 | Celery status：`26c6ee37`、`027d227f` | 2 | 48.638s；22.773/25.865s | 状态页、PgBouncer 断链；DB-SESSION 同类复发 | 修复已落地，第二层待端点加载 |
| P0 | `RUNTIME-TOPOLOGY-RELOAD` ImportError | 5 | 五条消息立即失败 | MP3 新任务；第 2 次明确复发 | worker 已替换，继续观察 |
| P0 | scheduler config `60ff7467696400d3` | 79 | 142.700s / 3.896s / 6.557s | scheduler 轮询；executor 0.023ms | OPEN，网络/往返主导 |
| P0 | topic batch update `0b4ccc892361a813` | 47 | 134.002s / 5.957s / 11.579s | ZSXQ 同步；写入与网络长尾 | OPEN/OBSERVING |
| P1 | comments `1f91e2f6c30e317a` | 29 | 53.832s | ZSXQ 评论对账 | OBSERVING |
| P1 | topic update `548d10cff60cb3fd` | 17 | 52.029s | ZSXQ 同步 | OBSERVING |
| P1 | report audio `_wait_child` `40b27e0226933f0c` | 22 | 38.432s | 后台父子轮询 | OBSERVING |
| P1 | external relation scan `072a6f7264c52333` | 1 | 16.206s | 后台批次，无前台端点 | OPEN，孤立网络长尾 |

## 页面/API 全链路

| 端点 | 请求/失败 | P95/最大 | 累计 | 匹配慢 SQL | 判断 |
| --- | ---: | ---: | ---: | ---: | --- |
| report audio status | 362/0 | 4.951/10.675s | 539.937s | 34 条/68.748s | 轮询与多段数据访问仍占主导 |
| stick scene assets | 204/0 | 5.848/15.868s | 371.186s | 有匹配 | 低频长尾仍高 |
| report audio jobs | 403/0 | 2.592/9.764s | 237.029s | 有匹配 | finalization 旧指纹部署后为 0，继续观察 |
| platform credentials | 320/0 | 1.781/6.801s | 182.050s | 有匹配 | 远端数据库往返长尾 |
| MP3 jobs | 454/0 | 1.657/9.252s | 176.263s | 有匹配 | publication_tasks 热路径 |
| MP3 detail | 87/3 | 4.338/9.596s | 110.800s | 有匹配 | 失败率 3.45%，大字段点查仍需后续窗口 |
| report audio detail | 60/0 | 7.399/7.968s | 109.512s | 有匹配 | 子摘要已批量化，旧窗口仍含部署前样本 |
| visual plan detail | 68/0 | 6.057/11.702s | 93.028s | 有匹配 | 网络长尾/投影继续观察 |
| Celery status | 12/0 | 22.773/25.865s | 63.458s | 原窗口无匹配慢 SQL | 外部 I/O 持 Session 根因已定位并修复 |

低频高延迟单列：podcast cover 4 次，P50 11.567 秒、P95 19.226 秒；Bilibili sync 1 次 15.693 秒但仅匹配 1.010 秒 SQL；retry 4 次最大 14.105 秒；visual plan create 3 次最大 13.604 秒。

页面/API 很慢但没有匹配慢 SQL：Celery status 是最明确样本，真实根因是 Celery control plane 等待；修复前还跨持有鉴权 Session。新 PID 第一层修复后，鉴权 SQL 95.83 毫秒，23 秒 inspect 后才新 checkout 执行业务快照，证明 Session 已分段，但串行 inspect 仍使端点慢。

重点 ZSXQ 链路：

- `zsxq-insights overview` 1 次，2.710 秒，匹配慢 SQL 1.620 秒；单样本符合 `<5s` 目标但不足以形成 P95 或闭环结论。
- `zsxq topics/local` POST 1 次，638 毫秒。
- `topics/local/tabs` POST 1 次，245 毫秒。
- scheduler status 3 次，P50 3.837 秒、P95 5.280 秒，累计 9.553 秒；1 条 SQL 1.574 秒。
- 固定窗口未捕获通用 `sync/status` 请求，不能据此断言该页面没有问题。

## 后台任务

| 任务族 | 完成/重试/失败 | 成功累计/P95/最大 | 判断 |
| --- | ---: | --- | --- |
| 全部 Celery | 562/83/17 | 24174.354s | 失败风暴主要为 SMTP 与旧 worker ImportError |
| ZSXQ sync | 24/6/0 | 6789.952s / 765.308s / 807.152s | 长任务；数据库与外部页面混合等待 |
| publisher | 32/0/0 | 4441.866s | 媒体外部 I/O 主导 |
| report audio | 53/6/0 | 3278.543s / 460.392s / 755.774s | 父子等待仍需窗口复核 |
| visual plan | 7/0/0 | 1775.035s / 1269.148s / 1269.148s | AI/浏览器外部 I/O |
| PDF clean | 5/0/0 | 1655.984s / - / 905.342s | 本地 CPU/I/O，无前台端点 |
| MP3 workflow | 63/0/5 | 1016.940s | 5 失败均为运行时混版 ImportError |
| notify | 232/71/11 | - | SMTPServerDisconnected 外部依赖 |

后台/连接持有但无前台端点：42 条 managed checkout 长持有没有匹配慢 SQL；连接累计 SQL 1046.116 秒、非 SQL 245.973 秒。单独的外部关系扫描最大 16.206 秒，PDF 清洗最大 905.342 秒，均不能从前台 HTTP 统计观察到。

## 根因证据链

### 1. Celery status 鉴权 Session 跨外部 I/O

- 修复前证据：request `26c6ee37` / `027d227f`，HTTP 22.773/25.865 秒；checkout origin `_get_local_bypass_user`，总持有 22.295/24.765 秒，SQL 仅 159.93/110.18 毫秒，非 SQL 22.135/24.655 秒；约 20 秒时 PgBouncer query timeout，尾部 `_business_tasks_snapshot` 以 1.21/0.23 毫秒失败。
- 代码变更：`_status_payload()` 在 Redis/Celery/process/filesystem I/O 前调用 `_release_db_session_quietly()`。
- 定向回归：顺序契约测试先红后绿；Celery monitor 34 项通过。
- 完整测试：完整 unit 1114 项通过；第二层修复后将再次全量执行。
- 运行态：新 PID `10022` 真实请求 `c4e93d19` 为 200；鉴权 SQL 后 23 秒无 checkout，业务快照用新连接执行，无 server closed、无长持有事件。
- 下一轮：24 小时内同端点 PgBouncer timeout/server closed/业务快照 SQL error 均为 0 才可关闭第一层根因。

### 2. Celery inspect 串行控制面放大

- 修复前证据：新 PID 已释放 Session，但真实端点仍 24.369 秒；代码依次调用 `active()`、`reserved()`、`scheduled()`，每次都等待忙碌 solo worker 回应。
- 代码变更：三类 inspect 使用三个独立 Inspect/连接和线程并行发起，并设置回答上限为服务数，保留完整 active/reserved/scheduled 字段。
- 定向回归：三方 `threading.Barrier` 测试在串行实现上失败，在并行实现上通过。
- 运行态组件验收：全新进程连接真实 Redis/17 个队列，3.573 秒完成，`error=None`。
- 阻塞：第二次重启门禁最初发现 PDF 清洗和生财同步；06:24 最终复核 PDF 已完成，但生财同步 run `91079a6f-ce49-47cd-a8c7-51b6a583bee9` 仍运行，未强制重启；真实 HTTP 端点尚未加载第二层代码。
- 下一轮：安全重启后真实 refresh 请求 `<10s`，并确认字段完整及无 control-plane 错误。

### 3. 旧 worker 与新共享模块混跑

- 修复前证据：worker 16:38 启动，18:05 后磁盘代码新增共享符号；03:28 连续 5 条消息在 `create_app()` 注册新 API 时从旧 `sys.modules` 导入失败。
- 现场处置：安全完整重启替换全部 worker；新进程导入符号成功。
- 业务验收：故障时段 5 条 MP3 业务任务最终均为 `completed/done/100%`，无孤儿。
- 防复发：复发登记重开 `RUNTIME-TOPOLOGY-RELOAD`；共享 service/API 导入变更必须完整滚动重启所有 `create_app()` worker，并增加代码版本指纹/导入契约。
- 下一轮：ImportError 为 0、worker 启动时间晚于代码版本；此前不能 CLOSED。

### 4. 上轮 finalization JSON/N+1

- 固定窗口仍包含旧 PID 的 `latest_finalization_for_audio` 和完整 `params/result` 指纹。
- 2026-08-10 18:01:43 新代码部署后，旧 finalization origin 为 0，report audio API 完整 `payload=params,result` 为 0。
- 这是半个观察窗口，保持 `RUNTIME_REPLACED/OBSERVING`；下一完整 24 小时仍为 0 才可关闭。

## 数据库与索引证据

### 表规模与维护状态

| 表 | 总大小 | live/dead | HOT update | index hit | 判断 |
| --- | ---: | ---: | ---: | ---: | --- |
| `publication_tasks` | 119MB（17MB 表/90MB TOAST/12MB 索引） | 12247/1643 | 1.38% | 99.81% | JSON/宽行写放大仍需治理，不是缺普通索引 |
| `zsxq_topics` | 124MB（13/77/34MB） | 11723/2185 | 0.79% | 99.66% | TOAST 与低 HOT 明显 |
| `zsxq_topic_comments` | 118MB | 41156/5192 | 68.40% | 99.93% | 索引命中良好 |
| `zsxq_attachments` | 71MB | 32736/6503 | 0.12% | 99.95% | 写放大/死行需观察 |
| `zsxq_sync_runs` | 35MB | 937/58 | 0.34% | 99.94% | result 宽字段主导 |
| `external_documents` | 6.944MB | 1771/146 | 0% | 99.01% | 表小，孤立长尾非缺索引证据 |

当前无锁等待、无 idle in transaction；7 个会话均为 idle。数据库超时为 statement 60 秒、lock 5 秒、idle-in-transaction 60 秒。`pg_stat_statements` 未安装。

### EXPLAIN (ANALYZE, BUFFERS)

- `publication_tasks` 主键点查含 result：0.028 毫秒。
- chart 查询：2.007 毫秒。
- scheduler config 17 行：0.023 毫秒。
- publisher list：0.070 毫秒；content list：0.619 毫秒。
- recent full JSON 20 行：0.055 毫秒；窄投影：0.043 毫秒。
- `zsxq_sync_runs` 120 行：1.511 毫秒。
- Insights 6543 行：29.493 毫秒；专用索引有效、ready/live，本轮累计 scan 3075。

这些 executor 数据与应用 1~16 秒形成数量级差异，证明本轮主要长尾来自 PgBouncer/网络、连接等待、往返和数据搬运，不支持凭应用耗时盲目增加索引。

### 索引整改建议与人工 SQL

- 未发现定义完全相同的新重复索引，低使用索引也没有足够证据无人值守删除。
- schema preflight 仍确认两个等价 topic 索引并存：`ix_zsxq_topics_group_tab_type_create_desc`、`ix_zsxq_topics_group_type_create_desc`。
- 仅保留人工 DBeaver 维护脚本 `docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql`。脚本带 `lock_timeout`、`statement_timeout`、验证和回滚说明；本轮未执行。
- 风险：DROP/迁移记录涉及生产所有权和锁，必须在人工维护窗口逐条执行；完成前 schema preflight 会继续提示。
- external relation scan 16.206 秒的同指纹其它样本为 1.383~2.366 秒，且关系表已有 topic/url hash 与唯一组合索引，判定为孤立传输长尾；暂不新增索引。

## 验证

- 定向 TDD：Session release 顺序测试先红后绿；inspect 并发屏障测试先红后绿。
- `tests.unit.test_celery_monitor_watchdog`：34 项通过。
- 完整 unit：第二层代码变更后 1115 项通过（40.260 秒）。
- `compileall -q src/backend tests`：通过。
- `review_bug_records.py --strict`：159 条全部归类，9 个复发簇，检查通过。
- `git diff --check`：通过。
- 安全重启：首次/冻结后门禁捕获竞态 ZSXQ 任务并等待其自然结束，随后 backend PID `10022`、17 个 worker 和 schedulers 启动成功；第二次重启被活动任务正确阻断。
- health：200，`healthy`。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `CELERY-STATUS-AUTH-SESSION` | 鉴权 checkout 跨 Redis/Celery I/O | I/O 前 release + 顺序契约 | PID 10022 无跨 inspect checkout/断链 | 同类复发 | 24h 证据不足 | timeout/crash/SQL error=0 |
| `CELERY-INSPECT-SERIAL-3X` | 三次广播串行等待 solo worker | 独立连接并行 + limit + 屏障测试 | 真实控制面 3.573s | 首次 | endpoint 未加载 | 安全重启后 HTTP refresh <10s |
| `RUNTIME-TOPOLOGY-RELOAD` | 旧 worker `sys.modules` 与新 API 混版 | 完整安全重启、registry 重开 | 5 业务任务均 completed | 第 2 次 | 缺版本指纹门禁 | 24h ImportError=0，worker 版本一致 |
| `58f8cd43a85581b3` | 断链后尾部业务快照失败 | failed SQL 计时 + Session 分段 | 新 PID 查询成功，无断链 | 本轮出现 | 网络长尾仍在 | 失败数=0，应用耗时与 executor 分离 |
| `AF15F9B3` finalization JSON | completed JSON 扫描/N+1 | 结构化 finalization + 批量摘要 | 部署后旧 origin=0 | 窗口含部署前样本 | 仅半窗口 | 完整 24h 指纹=0 |
| `60ff7467696400d3` | scheduler 轮询跨网络往返 | 既有投影/缓存 | executor 0.023ms | 反复 | 应用累计 142.700s | 应用 P95<1s 且累计下降 |
| `0b4ccc892361a813` | ZSXQ 批写与远端网络长尾 | 既有页级批量 | executor/索引正常 | 反复 | 最大 11.579s | 下一批 P95<3s，无失败重试风暴 |
| `072a6f7264c52333` | 大 IN 批次 + 网络波动 | 暂无盲目 DDL | 其它样本 1.383~2.366s | 未确认 | 单样本 16.206s | 再现时带 batch size/EXPLAIN 归因 |
| comment hash index cleanup | 等价索引/迁移所有权 | 人工 SQL 已备 | preflight 仍提示 | 持续 | 需 DBeaver 窗口 | 执行、验证、回滚证据齐全 |

## 下一轮明确验收计划

1. 第一优先检查安全窗口；加载 inspect 并行代码后，连续 3 次真实 `inspect=1&refresh=1`，记录总耗时、request id、数据库 checkout 和字段完整性，目标最大 `<10s`。
2. 固定新 PID 后 24 小时确认 Celery status 的 PgBouncer query timeout、server closed、`_business_tasks_snapshot` SQL error 和跨外部 I/O checkout 均为 0。
3. 检查所有 worker 启动时间/代码版本，确认 `DEFAULT_INITIAL_SCRIPT_MIN_CHARS` ImportError 为 0；如再现，补 worker 版本指纹 fail-closed 启动门禁。
4. 复核 finalization 旧 origin、report audio API 完整 JSON、父详情大载荷在完整窗口均为 0；否则继续追上游调用链。
5. 对 `publication_tasks` 341 条慢 SQL按 origin 与 payload 分类，优先处理累计耗时最大的 scheduler/topic/parent wait，而非按次数加索引。
6. 继续采样 Insights 冷请求；至少形成 5 个冷样本并保持 P95 `<5s`、最大 `<10s`。
7. 人工执行索引脚本前再次采集 `pg_stat_user_indexes` 和 EXPLAIN；无人值守巡检不执行 DDL。

在上述运行窗口和人工维护证据完成前，本轮所有主要根因保持 `OPEN/OBSERVING` 或 `RUNTIME_REPLACED/OBSERVING`，不标记 CLOSED。
