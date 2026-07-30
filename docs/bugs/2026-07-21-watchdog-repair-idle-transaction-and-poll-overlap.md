# Watchdog 修复事务跨外部检查与轮询重叠

## 基本信息

- 状态：再次打开；代码已修复，待安全重启与运行验收
- 严重级别：P1
- 影响模块：publisher watchdog / PostgreSQL / Celery / Redis
- 首次发现：2026-07-21
- 修复提交：待提交
- 根因分类：数据库事务跨外部 I/O
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：第 2 次
- 防线层级：local_patch / contract_test / observability

## 现象

- `2026-07-21 13:08:21`，本地 PgBouncer 因 30 秒查询超时关闭服务端连接。
- `13:08:24`，watchdog 在 `runtime_guard._publication_task_active_count` 继续使用失效 Session，报 `server closed the connection unexpectedly`。
- 同一时段研报语音状态轮询、TTS 查重和轻量主键查询出现 `1-4s` 延迟；前端使用 `setInterval`，上一轮未完成时仍会发起下一轮并行请求。
- `zsxq_historical_backfill` 在提交后读取过期 `ZsxqSyncRun.id`，隐式刷新包含 `result` 的整行，单次记录 `4.7s`。
- 新增简笔画资产 worker 在进度回调提交后继续读取过期资产 ORM 对象，重复刷新整行资产记录。

## 证据

- 生产库 `publication_tasks` 约 4957 行，代表性查询 `EXPLAIN ANALYZE`：
  - TTS 父任务查重：`0.023ms`。
  - TTS 文本哈希查重：`0.016ms`。
  - watchdog 活跃任务快照：`0.095ms`。
  - 最近 20 条轻量/JSON 载荷读取：`0.024ms / 0.026ms`。
- 对应索引累计命中 `6758 / 3826 / 115152`，当前无活动锁等待；本轮慢耗时不属于缺索引或表规模问题。
- PgBouncer 记录修复循环的事务持续数秒，并在外部 Celery 检查窗口关闭连接；随后同一 Session 的容量计数失败。

## 根因

1. watchdog 先加载 ORM 任务，再同步检查 Celery worker 和 Redis 队列，数据库事务跨越外部 I/O。
2. 连接失效后，内层发布异常分支只返回失败，没有立即 rollback/remove，下一条修复 SQL 可能继续复用坏 Session。
3. 前端定时轮询没有 single-flight 门禁，数据库抖动会把一次慢请求放大为重叠请求。
4. 新代码再次在 commit 后复用 ORM 实例，说明“跨提交只传 ID/DTO”纪律尚未覆盖新增 worker 和维护脚本。

## 修复

- `publisher_queue_repair` 在 Celery/Redis 检查前复制任务快照并结束数据库事务；发布异常立即清理 Session，发布后重新读取并校验任务状态。
- `runtime_guard` 对可重试的失效连接清理 Session，并且只重试一次容量计数。
- 研报语音列表轮询增加 single-flight 门禁，阻止慢请求叠加。
- 简笔画资产 worker 在首次进度提交前保存标量快照，提交后不再访问资产 ORM。
- 历史回填在首次 commit 前保存 `run_id`，后续只传 `SimpleNamespace(id=...)`，终态使用定向 UPDATE。

## 回归纪律

- 任何 Celery/Redis/HTTP/浏览器/文件等待之前，当前 DB Session 必须 commit/rollback/remove，禁止保持 idle transaction。
- 任何 commit 之后只允许使用提交前标量、Row 或 DTO；禁止继续访问 ORM 属性。
- 所有自动轮询必须 single-flight；响应时间超过轮询周期时不得产生下一轮并发。
- 慢 SQL 修复前必须先用生产 `EXPLAIN ANALYZE` 区分执行器耗时、连接等待和事务生命周期，不允许把网络/事务延迟继续伪装成缺索引。
- 验收必须包含新 PID、真实负载和部署边界后的日志；确定性测试通过只代表 `code_changed`，不代表 `accepted`。

## 验证

- 定向回归：`95 passed, 2 subtests passed`。
- 完整已知问题回归：`684 passed, 5 skipped, 37 subtests passed`。
- SQL/重启纪律：`63 passed`。
- 当前仍有不可中断业务任务，尚未替换运行进程；状态保持 `code_changed`，待安全重启和部署后日志验收。

## 2026-07-30 复发

- `publisher` pending watchdog 仍有一条专用路径在读取任务后直接检查 Celery worker 和 Redis 队列，没有先释放 Session。
- `2026-07-30 06:01` 的现场日志显示，该路径持有数据库连接 `35.88s` 后才重排任务；24 小时内连接持有事件共 `305` 条，P95 `61.49s`、最大 `173.85s`，其中 `161` 条没有匹配到慢 SQL。
- 已把 pending publisher 任务转换成标量快照并在外部检查前 `remove()` Session；消息投递后使用窄字段 CAS 更新任务及 Bilibili 投影，CAS 失败时撤销新消息。
- 新增契约测试，明确断言 Celery/Redis 可见性检查期间 `db.session().in_transaction()` 为 `False`。
- 状态保持 `code_changed`；只有新 watchdog PID 运行并跨过真实扫描窗口后，才可改为运行验收通过。
