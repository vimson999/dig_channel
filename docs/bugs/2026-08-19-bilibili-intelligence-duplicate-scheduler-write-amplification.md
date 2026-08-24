# B站情报双调度器与未变化 JSON 重写放大数据库

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING
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
