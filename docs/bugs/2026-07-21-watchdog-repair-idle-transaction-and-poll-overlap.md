# Watchdog 修复事务跨外部检查与轮询重叠

## 基本信息

- 状态：`RUNTIME_REPLACED / OBSERVING`；第 5 次轮询复发已加载，待 24 小时与后续窗口复核
- 严重级别：P1
- 影响模块：publisher watchdog / report-video 展示统计 / PostgreSQL / Celery / Redis
- 首次发现：2026-07-21
- 修复提交：待提交
- 根因分类：数据库事务跨外部 I/O / 自愈循环误加载展示统计
- 复发键：`DB-SESSION-LIFECYCLE` / `HOT-PATH-PAYLOAD-POLLING`
- 是否复发：第 5 次
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

## 2026-07-31 复发

- 新 Watchdog PID 已加载 7 月 30 日的候选扫描修复，但消息发布后的竞争失败分支仍按
  “数据库 CAS 失败 -> Celery revoke -> Session remove”执行。
- 现场 `05:26:01` 的 publisher CAS 更新返回 0 行；随后直到 `05:27:03`
  才归还连接，持有 `60.70s`。同一秒 PgBouncer 记录 query timeout，且期间没有
  匹配的慢 SQL，证明连接跨越了 Celery control I/O，而不是 PostgreSQL executor 慢。
- 通用 pending repair 的 CAS 失败和 processing repair 的状态竞争失败分支也有相同
  顺序问题，属于同一复发键的横向遗漏。
- 新增统一 `_release_then_revoke_published_repair_message` 边界，三个分支都先
  `rollback/remove`，再撤销已发布消息；运行测试在 revoke 回调内断言
  `db.session().in_transaction()` 为 `False`，结构契约拒绝旧顺序重新出现。
- `2026-07-31 08:17` 重启安全门禁确认没有不可中断任务，`08:18–08:19`
  完整重启成功；新 Watchdog PID `32805`、17 类 Celery 服务和 5 个 scheduler
  均已加载本轮代码，bootstrap `errors=[]`。
- 当前验收阶段为 `runtime_replaced`；仍需跨过真实 Watchdog CAS 竞争窗口，确认
  不再出现无匹配慢 SQL 的约 60 秒连接持有后，才能标记运行验收完成。

## 2026-08-01 第四次复发

- `08:18–08:19` 替换后的 Watchdog PID `32805` 已加载上一轮
  release-then-revoke 修复，但真实扫描仍出现无匹配慢 SQL 的连接持有：终态父任务
  清理 `67.36s/42.21s`、publisher upload processing 收口 `37.46s`，以及
  processing 可见性检查最大 `80.21s`。
- 现场 SQL 仅约 `0.1–0.8s`，长持有发生在 Celery inspect/queue scan/revoke；
  其中 `14:01:29` 的 `37.46s` 持有结束后立即记录
  `Closed orphan publisher upload task`，把调用链与生命周期告警直接对齐。
- 根因是上一轮只覆盖“消息发布后的 CAS 失败”分支，未覆盖消息发布前的
  publisher processing worker/queue 可见性检查，也未覆盖终态父任务清理中的
  `_revoke_celery_task`。这两条路径仍把 ORM 对象和事务带过 Celery control I/O。
- processing 修复现先复制 DTO 并 `rollback/remove`，再执行 worker/queue
  可见性检查；外部检查后按 ID 重载并校验 status/step/celery id，写入前后发生
  状态竞争时安全退出或撤销新消息。
- 终态父任务清理现先冻结父子标量快照并释放 Session，再 revoke，随后重载子任务
  校验状态并在新短事务中收口。上传收口的 revoke 也在无事务状态执行。
- 新增三条运行态合同测试，分别在 worker inspect、queue scan、upload revoke 和
  terminal-parent revoke 回调内断言 `db.session().in_transaction()` 为 `False`。
- 当前验收阶段再次回退到 `code_changed`；必须通过重启门禁替换 Watchdog 运行时，
  再跨真实 processing/terminal cleanup 扫描窗口验证后才能进入 `runtime_accepted`。

## 2026-08-31 第五次复发：自愈决策误拉仪表盘展示统计

- 新 PID `99802` 的后窗中，watchdog 每轮完成修复和 Celery inspect 后仍以
  `force_refresh=True` 调用通用 `_service_status()`。当服务为 `report_video` 时，该 helper 会再执行
  `report_video_tasks.status`、`report_video_reports.parse_status` 和 `ai_status` 三条全表聚合；这些字段
  只供完整控制面展示，不参与 worker 是否卡死、告警或重启的任何判断。
- `2026-08-31 13:56:44–14:38:18` 的 28 轮真实 watchdog 共执行 84 条展示聚合：task 聚合 28 条，
  累计 `7,431.62ms`、P50/P95/max `227.28/508.42/1,083.89ms`；report 聚合 56 条，累计
  `2,631.91ms`、P50/P95/max `31.32/109.13/430.97ms`。三条合计 `10,063.53ms`，其中
  task 聚合产生本后窗唯一该来源慢 SQL。
- 生产只读统计显示 `report_video_tasks` 345 行却已有 `86,112` 次 seq scan，
  `report_video_reports` 4,752 行已有 `167,541` 次 seq scan。定向
  `EXPLAIN (ANALYZE, BUFFERS)` 的 executor 仅 `0.302/4.205/2.429ms`，均为 shared hit、0 read、
  无临时 I/O；全量状态聚合采用 seq scan 是正确计划，新增状态索引不能解决调用频率或网络 RTT。
- TDD 先要求 `run_watchdog_once()` 显式传入 `include_dashboard_stats=False` 并得到红灯，随后给
  `_service_status()` 增加默认开启的展示开关。watchdog 的首次检查和业务心跳复核都关闭展示统计；完整
  `/api/v1/celery/status` 保持默认开启，公开响应字段与数值不变。
- 新契约 3/3、watchdog 模块 40/40 通过；完整确定性回归为
  `1565 passed, 5 skipped, 314 subtests passed`，compileall、TypeScript、109 项 DB/重启专项、
  Alembic 单 head 和 diff check 均通过。
- 14:37 双安全门各读取 47 条近期任务、0 条活动 sync 并放行，标准脚本未使用 FORCE，加载到 backend
  PID `4274` / watchdog PID `4810`；health healthy、bootstrap 17/17、`errors=[]`。新 watchdog 在
  `14:38:21`、`14:39:44`、`14:41:09` 三轮真实周期执行正常修复查询，但展示聚合为 `0` 条。
  完整 status 探针 `e6314206` 仍以 200/`1,670.44ms` 返回 report-video 三组统计；runtime-only 探针
  `29781f07` 为 200/`533.38ms`。
- 高频采样在旧进程查询阶段只捕获最长 `28.3ms` 的短 idle 空隙，随后外部 I/O 阶段连续 25 秒为 0；
  新运行态 5 秒采样及只读快照均为 `idle in transaction=0`。这排除了长事务复发，但短窗口与三轮周期
  不能替代 24 小时和后续窗口，因此状态为 `RUNTIME_REPLACED / OBSERVING`，不得写成 CLOSED。
