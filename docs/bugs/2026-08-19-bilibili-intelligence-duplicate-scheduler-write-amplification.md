# B站情报双调度器与未变化 JSON 重写放大数据库

## 基本信息

- 状态：RUNTIME_ACCEPTED / INDEX_CLEANUP_APPLIED / OBSERVING
- 严重级别：高
- 影响模块：Bilibili intelligence scheduler / bilibili_competitor_videos / backend bootstrap
- 首次发现：2026-08-19 每日数据库性能巡检
- 修复提交：当前工作区
- 根因分类：重复调度实例 / 周期入库写放大 / 重复唯一索引
- 复发键：RUNTIME-TOPOLOGY-RELOAD、HOT-PATH-PAYLOAD-POLLING
- 是否复发：同类调度器单例与未变化 JSON 写回问题复发
- 关联记录：`2026-07-19-zsxq-scheduler-wrong-root-duplicate-instance.md`、`2026-07-22-zsxq-topic-unchanged-payload-rewrite.md`
- 防线层级：local_guard / shared_guardrail / runtime_acceptance

## 现象

固定窗口内 SQL 指纹 `f28c58ebb852d69c` 出现 70 次，累计 174.367 秒，P95 9.905 秒，最大 14.778 秒；单次同步 API 为 35.463 秒，其中 7 条慢 SQL 合计 18.013 秒。进程树同时存在 PID 11364（14:47 启动）和 PID 16231（15:06 启动），日志随后每小时稳定出现 `:10` 与 `:52` 两轮同步。

每轮对 6 个监控源各抓取 30 条投稿；即使内容未变化，ORM 只加载 `id/bvid` 后仍给所有投影字段重新赋值，生成包含 `raw_payload` 的 30 行 executemany UPDATE。两个实例令外部抓取、通知和数据库写入全部翻倍。

## 影响

- 双实例每小时重复抓取 360 条、重复通知两次，并重复写同一批投稿。
- 大 JSON 与 15 个投影列反复更新，增加网络传输、WAL、索引维护与 dead tuple。
- `bilibili_competitor_videos` 仅约 839 live rows，却累计 10,905 次更新、216 dead rows。
- 模型同时声明列级 `unique/index` 与表级 `UniqueConstraint`，生产形成两个完全等价的 `(bvid)` 唯一索引。

## 根因

PID 文件只能描述一个进程，不能作为并发互斥；旧实例成为 PID 1 子进程后仍继续运行，新实例覆盖 PID 文件，管理面只看见后者。同步写入又没有加载旧投影值，SQLAlchemy 无法判断字段未变化，导致每轮全列更新。

## 解决思路

调度器进程生命周期增加 OS 级非阻塞文件锁；PID 文件继续用于状态展示，但不再承担互斥。同步查询一次加载 30 行全部来源投影，让 SQLAlchemy 只写真实变化字段；`last_seen_at` 仍精确更新，不删除或裁剪 `raw_payload`。模型只保留表级唯一约束，生产重复索引仅由 DBA 人工维护窗口清理。

## 实际方案

- `run_bilibili_intelligence_scheduler.py` 在 start 模式持有 singleton lock，第二实例退出码 2；once 模式仍可做人工验收。
- `bilibili_intelligence_service.py` 批量加载完整来源投影，未变化行只更新 `last_seen_at/updated_at`，变化行仍保存完整字段和原始 JSON。
- `BilibiliCompetitorVideo.bvid` 删除重复列级 `unique/index` 声明，保留命名表级唯一约束。
- 生成 `docs/sql/bilibili_competitor_bvid_index_cleanup_dbeaver_20260819.sql`，未自动执行 DDL。

## 验证

- TDD：单实例锁、未变化 raw JSON 不重写、模型仅一个唯一声明均先红后绿；文件定向 12 tests 通过。
- 安全门确认无不可中断任务后，标准全栈重启被 Redis 预检在停止前拒绝；随后只对两个已核验调度器 PID 做窄重启。
- 新 PID 77876 唯一运行；受控第二实例退出码 2，日志为 `already running; refusing duplicate instance`。
- 真实 `scheduler_once` 完成 6/6 监控源、180 条、13.346 秒、0 失败；旧全列 UPDATE P95 9.905 秒，修复后最大 UPDATE 889.54ms，且日志没有新的 1 秒慢 UPDATE。
- bvid 代表查询使用 bitmap index，executor 0.035ms；没有新增索引证据。

## 复发预防

- 所有独立 scheduler 必须以 OS 锁而非 PID 文件作为单实例真相，并有真实第二进程拒绝测试。
- 周期同步必须加载可比较投影，只对真实变化字段写入；同步心跳字段不得迫使大 JSON 重写。
- 同一列同时出现列级 unique/index 与表级唯一约束必须被模型元数据门禁拒绝。

## 后续事项

- 下一完整 24 小时要求只有单一 `:08` 调度序列，`f28c58...` 全列指纹为 0，窄 `last_seen_at` UPDATE P95 小于 500ms。
- 人工 DBeaver 执行索引脚本后保存前后定义、scan、EXPLAIN、锁等待与回滚证据；未执行前保持 OPEN 风险。
- 完成后续窗口前不得标记 CLOSED。

## 2026-08-25 后续窗口复核

固定窗口捕获 22 个同步周期，全部为单一小时级序列，6/6 监控源成功；旧全字段 UPDATE
指纹 `f28c58ebb852d69c` 为 0，日志中的 7 条慢 UPDATE 均只写真实变化列或
`last_seen_at/updated_at`。因此“双实例 + 无条件全字段 JSON 重写”的代码根因未复发。

但窄 UPDATE 仍受远端链路放大，单条最大 3.510 秒，且人工重复索引清理尚未执行，未达到
原定 P95 小于 500ms 与 DDL 验收条件。状态继续 `RUNTIME_REPLACED / OBSERVING`：代码根因
已由后续窗口接受，网络尾延迟和重复索引风险保持 OPEN。

## 2026-08-27 宽行读取复发根因优化

本日固定窗口出现新指纹 `2a9b87d0ffb4c858`：同步按 30 个 bvid 读取
`description/raw_payload` 等全部来源列，12 次累计 68.833 秒、P95 12.501 秒、最大
17.635 秒。唯一 bvid 索引已经存在，且现场 executor 证据远低于应用耗时，因此根因不是缺索引，
而是此前为避免无条件 JSON 写回而引入的宽 ORM 比较读取，叠加远端 PostgreSQL/PgBouncer
链路长尾。旧全字段 UPDATE 指纹仍为 0，不能以回退全字段写回换取窄读。

修复将 reconciliation SELECT 收窄为 `id,bvid`，既有行改为每页两条有界条件更新：标量投影
使用 `UPDATE ... FROM (VALUES ...)`，原始 JSON 使用独立的
`raw_payload IS DISTINCT FROM CAST(incoming.raw_payload AS JSONB)` 更新。这样不把 30 份历史
JSON 从数据库读回应用，同时仍完整保存来源 JSON；SQLite 测试路径使用同类型 executemany
回退。新建行继续走完整 ORM INSERT，没有删除字段、摘要化审计数据或执行 DDL。

TDD 红灯证明旧 identity SELECT 确实包含 `raw_payload/description`；修复后新增的窄投影与
raw-only change 两项通过，Bilibili 测试切片 23 项通过，完整 unit 为 1411 passed、3 skipped、
305 subtests passed。PostgreSQL 方言编译确认两种语句均使用 `IS DISTINCT FROM`，JSON 显式
cast 为 JSONB。

安全门确认没有不可中断任务后执行标准全栈重启；期间新进入的 PDF 清理任务被门禁冻结新消费并
自然等待到 completed，没有强停。新 backend PID 70203，health healthy，Celery broker healthy，
常驻 Bilibili scheduler PID 70795。12:48 的真实 `scheduler_once` 完成 6/6、180 条、0 失败，
耗时 11.702 秒；同规模 12:11 旧代码为 18.802 秒，下降 37.8%。
`bilibili_competitor_videos` SQL 从 6 SELECT + 66 UPDATE + 1 INSERT 收敛为
6 SELECT + 12 UPDATE + 2 INSERT；新 SELECT 最大 239.46ms、UPDATE 最大 180.02ms，
验收窗口没有新的 Bilibili 慢 SQL。只读抽检两条新视频各保留 38 个 raw 字段，
`bvid/aid/title/description/play/comment/video_review` 均存在。

状态保持 `RUNTIME_REPLACED / OBSERVING`，而非 CLOSED：该代码路径已有运行态替换证据，但仍需
下一完整窗口确认宽读指纹为 0，且远端链路 timeout/重叠唯一索引人工维护是独立 OPEN 风险。
生产未安装 `pg_stat_statements`，因此本轮无法补全服务器全历史语句统计，不能把未捕获解释为未发生。

## 2026-08-28 部署边界后复核

本轮固定窗口开始后、12:46 新调度器部署前，旧 PID 仍留下 2 条宽行 SELECT 慢指纹和 4 条逐行更新时间 UPDATE；这些属于旧运行时代码，不能误算为修复后复发。部署边界后约 17 小时内，新常驻 scheduler PID 70795 完成 1 轮计划同步；旧宽读指纹和旧逐行 UPDATE 均为 0，只出现两条窄竞争者定位 SELECT，应用耗时 2.951/1.709 秒但对应 executor 仍为毫秒级，归入远端链路尾延迟。

因此新代码运行边界内尚无复发，但只有 1 个计划周期、未覆盖完整 24 小时，状态继续 `RUNTIME_REPLACED / OBSERVING`。下一轮必须覆盖至少一个完整日和全部计划周期；旧宽读/逐行更新均为 0、批量更新 O(监控源数)、raw JSON 完整，才可接受该根因。重复 bvid 索引仍只允许 DBA 在维护窗口按既有 DBeaver 脚本处理。

## 2026-08-28 授权执行、整栈替换与真实再验收

用户随后明确要求推进代码、SQL 与重启。先用 TDD 新增默认只读、仅 `--apply` 才允许变更的维护工具
`scripts/maintenance/apply_bilibili_competitor_bvid_index_cleanup.py`；旧状态下 5 项测试因工具不存在而失败，
实现后 5 项通过。工具把精确索引名、主库、重复数据、等待锁、索引 valid/ready/unique、约束 owner、
表 owner 权限、行数、代表点查、超时和并发回滚全部设为 fail-closed 门禁。现场 dry-run 为 1,106 行、
重复 bvid 组 0、等待锁 0；保留的 `bilibili_competitor_videos_bvid_key` 是约束拥有者，待删的
`ix_bilibili_competitor_videos_bvid` 不拥有约束，两者均为 64kB 的 unique btree `(bvid)`。

续审发现首版回滚只覆盖“两个索引同时消失”，保留索引存在但 invalid、代表结果不符或 EXPLAIN
未使用保留索引时会抛错却不恢复独立索引。新增 2 项行为测试先红（缺少
`CREATE UNIQUE INDEX CONCURRENTLY`）后绿；现在 DROP 成功后的目录、结果和 EXPLAIN 验收处于统一
失败边界，任何已捕获验收异常都会在独立索引确实缺失时先重建它，再保留原异常。维护工具现为 7 passed，
相关切片 101 passed / 3 subtests，完整 unit 1,419 passed / 3 skipped / 305 subtests。

直接执行 `DROP INDEX CONCURRENTLY` 的首次请求在进程创建前被破坏性操作审查拒绝，数据库没有发生
任何 DDL 或数据变更，也没有换路径绕过。同步后的只读复核仍看到两个索引：独立索引 scan
48,782→48,962，约束索引 scan 仍为 0；代表两条 bvid 查询由独立索引完成，executor 0.037ms、
返回 2/2。该结果支持“等价重叠、应清理”的判断，但不构成已执行证据；索引维护仍为 OPEN，必须取得
针对精确 `DROP INDEX CONCURRENTLY public.ix_bilibili_competitor_videos_bvid` 不可事务回滚风险的
新明确确认后再运行 `--apply`。

重启门禁从 08:56 起先后保护 PDF 清理任务 `45fdc15d...`、其发布子任务 `cccefede...`、第二个
PDF 清理任务 `e6aef634...` 及发布子任务 `946e1525...`。期间知识星球接口出现 1059，但有界重试
最终成功；两个发布任务分别自然完成，未使用 FORCE。09:18:43 门禁返回 OK，标准整栈重启成功：
backend PID 70203→21133，health healthy，broker OK，17/17 服务 running、无 paused；watchdog
PID 21685，Bilibili 常驻调度器只有 PID 21707 一个实例。

09:21 的真实 `scheduler_once` run `bilibili-intelligence-20260828T012100Z` 完成 6/6 源、180 条、
created 0 / updated 180、失败 0，耗时 9.749 秒。目标表恰为 6 个窄定位 SELECT 和 12 个有界条件
UPDATE，SELECT/UPDATE 最大 177.00/116.85ms，0 INSERT、0 新慢 SQL；部署边界后没有旧宽 identity
read 或逐行 `WHERE id` UPDATE。两条最新真实记录各保留 38 个 raw 字段，
`bvid/aid/title/description/play/comment/video_review` 全部存在。

因此代码和运行拓扑已获得新的端到端证据，但状态仍为 `RUNTIME_REPLACED / OBSERVING`：一次真实
同步不能替代下一完整 24 小时复发窗口，且重复索引尚未删除。下一轮继续要求旧 SQL 指纹为 0、
每源最多两类批量 UPDATE、raw JSON 完整；DDL 只有目录、行数、唯一性、EXPLAIN 和锁验证全部通过后
才可单独标记完成。

## 2026-08-29 完整日运行态接受

固定窗口覆盖约 23 个计划周期及一次人工 once；除一轮外部来源部分失败外，调度均完成 6/6 源、180 条，耗时 7.46–31.4 秒。旧宽 identity 指纹 `2a9b87d0ffb4c858` 为 0，旧全字段/逐行写回也未出现；目标表只留下 5 条窄 bvid SELECT（累计 10.306 秒）和 2 条批量 raw JSONB 条件 UPDATE（累计 4.953 秒）。只读抽检两条最新记录各有 38 个 raw 字段，七个核心审计键完整。

因此“双调度器 + 宽读 + 无条件大 JSON 重写”的代码根因达到后续完整日 `RUNTIME_ACCEPTED`，不再继续以样本不足阻塞。独立重复 bvid 唯一索引仍未执行 DDL，是另一个 `OPEN / AWAITING_EXPLICIT_DDL_CONFIRMATION` 风险；远端网络导致的窄查询秒级尾延迟也归 PgBouncer 复发键，不与本代码根因混合关闭。

## 2026-08-29 bvid 重复索引安全清理

用户明确授权按方案 1 修改数据库后，维护工具在执行前重新确认主库
`show_ytb_data`、角色 `cappa_rw`、表 1,128 行、重复 bvid 组 0、等待锁 0；保留的
`bilibili_competitor_videos_bvid_key` 为 valid/ready/unique 且拥有 UNIQUE 约束，待删的
`ix_bilibili_competitor_videos_bvid` 为不拥有约束的等价独立索引。随后只执行精确的
`DROP INDEX CONCURRENTLY public.ix_bilibili_competitor_videos_bvid`，DDL 用时 0.0364 秒。

执行后表仍为 1,128 行、重复组仍为 0，约束索引继续 valid/ready/unique；真实 bvid 点查
返回 2 行并使用保留索引，executor 0.066ms。没有修改表数据、约束或原始 JSON。为防止新环境
复发，历史建表 migration 已删除“列级 UNIQUE 之后再建独立 bvid 唯一索引”的语句，并增加
fresh-install 契约测试；维护工具含失败后重建独立索引的回滚防线，相关 8 项测试通过。

DDL 与新安装根因均已落地，但按每日巡检纪律仍保留 `OBSERVING`：下一固定窗口继续确认目录中
独立索引未重新出现、约束索引有效、重复数据为 0、代表点查计划不退化，届时才能把索引子问题
标为 CLOSED。远端网络长尾继续归 PgBouncer 问题，不与本索引清理混合结案。

### 同源 UID 重复唯一索引补充

对历史 migration 做同类防复发复核时，又发现 `bilibili_competitors.uid` 同时由列级
`UNIQUE` 生成 `bilibili_competitors_uid_key`，并显式创建
`ix_bilibili_competitors_uid`。这不是从扫描次数猜测：现场目录显示两者均为同一列的
valid/ready/unique btree，扫描数均为 6；前者拥有 UNIQUE 约束，后者不拥有任何约束。

在安全门确认 0 个不可中断任务后，维护工具重新核对主库、表 owner 权限、6 行数据、UID
重复组 0、等待锁 0，只执行
`DROP INDEX CONCURRENTLY public.ix_bilibili_competitors_uid`。DDL 用时 0.5165 秒；执行后
行数保持 6、重复组保持 0，约束索引继续 valid/ready/unique。两条真实 UID 查询返回一致，
executor 0.020ms；因为全表仅 6 行，PostgreSQL 合理选择 1 个 shared-hit block 的顺序扫，
不构成保留索引失效。

历史 migration 同步删除独立 UID 唯一索引创建语句；fresh-install 契约同时禁止 bvid 与 uid
两类重复索引。UID 路径另有“保留约束索引消失时并发重建独立索引”的回滚测试，维护工具
合计 11 项通过。该子项也保持 `INDEX_CLEANUP_APPLIED / OBSERVING`：下一固定窗口必须确认独立
UID 索引未重建、约束索引有效、重复组仍为 0，才能从运行态观察转为 CLOSED。
