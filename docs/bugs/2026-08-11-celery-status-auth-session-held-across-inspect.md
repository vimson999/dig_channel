# 2026-08-11 Celery 状态接口跨 broker inspect 持有鉴权 Session

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING（2026-08-13 再次打开并修复并发强刷锁队列；单次刷新仍高于 10 秒）
- 严重级别：高
- 影响模块：Celery 状态页 / Flask 鉴权 / SQLAlchemy Session / Redis / Celery inspect / PgBouncer
- 首次发现：2026-08-11 日常数据库巡检
- 修复提交：工作区变更，尚未提交
- 根因分类：数据库 Session 生命周期
- 复发键：DB-SESSION-LIFECYCLE
- 是否复发：同类问题再次出现
- 关联记录：`2026-07-24-db-session-crossed-broker-and-remote-io.md`、`2026-08-10-failed-sql-lifecycle-duration-misclassified.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

最近 24 小时有两次 `/api/v1/celery/status?inspect=1&refresh=1` 请求分别耗时 22.773 秒和 25.865 秒。两个请求都只在开头执行了一次鉴权用户查询，随后约 20 秒没有应用 SQL；PgBouncer 报 `query timeout` 后，接口尾部 `_business_tasks_snapshot` 的第一次 SQL 立即以 `server closed the connection unexpectedly` 失败。

对应 request id 为 `26c6ee37` 和 `027d227f`。数据库 checkout 生命周期分别为 22.295 秒和 24.765 秒，其中 SQL 累计仅 159.93 毫秒和 110.18 毫秒，非 SQL 持有分别为 22.135 秒和 24.655 秒。checkout origin 都是 `utils/auth_utils.py:155:_get_local_bypass_user`。

## 影响

- Celery 状态页低频但 P95 达 22.773 秒、最大 25.865 秒。
- 请求占用数据库连接跨越 Redis ping、Celery inspect、进程和文件系统检查。
- PgBouncer 在外部等待期间关闭连接，尾部业务快照查询失败；接口虽返回 200，但状态内容存在降级和误判风险。
- 两次事件贡献了本窗口全部 2 条应用 SQL 错误，并产生 2 次 PgBouncer query timeout。

## 根因

鉴权装饰器先通过 ORM 查询本地 bypass 用户，导致请求作用域 Session checkout。`_status_payload()` 随后进入 Redis、Celery inspect、进程和文件系统 I/O，却没有先结束只读事务或归还连接。外部 I/O 本身才是 20 秒级长尾；PostgreSQL executor 并不慢。尾部再次访问数据库时复用了已经被 PgBouncer 关闭的连接，因此报错。

2026-08-10 加入的失败 SQL 计时修复把尾部失败 SQL 正确记录为 1.21 毫秒和 0.23 毫秒，排除了“失败 SQL 自身执行 20 秒”的误归因，并把根因收敛到跨外部 I/O 的 Session 持有。

新 PID 加载 Session 修复后的真实请求仍耗时 24.369 秒，但鉴权查询后直到 inspect 结束都没有数据库 checkout。这进一步定位到第二层根因：`active`、`reserved`、`scheduled` 三类 Celery inspect 被串行广播；忙碌的 solo worker 会延后 control reply，三轮尾延迟被顺序累加。

## 解决思路

在状态聚合函数进入任何 broker、Redis、进程和文件 I/O 之前释放请求作用域 Session。后续如需读取业务快照，由 SQLAlchemy 新建短 checkout；不能把鉴权 ORM Session 跨 inspect 复用。

## 实际方案

- `src/backend/apis/v1/celery_monitor.py`：`_status_payload()` 入口首先调用 `_release_db_session_quietly()`，然后才调用 `_ensure_dirs()` 和 `_broker_ping()`。
- 同文件将 `active/reserved/scheduled` 改为三个独立 Inspect/连接并行发起，并以服务数作为 reply limit，保留原状态字段完整性。
- `tests/unit/test_celery_monitor_watchdog.py`：新增顺序契约测试，断言 broker I/O 发生前已经释放数据库 Session。
- 新增三方同步屏障契约测试；串行实现无法通过屏障，并行实现必须同时进入三类 inspect。
- 按重启纪律先运行 `scripts/dev/check-restart-safety.py`；首次检查通过，完整重启的冻结后二次检查捕获到刚领取的 ZSXQ 同步任务，脚本等待任务自然完成，未强制覆盖门禁。

## 验证

- 定向测试：`PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_celery_monitor_watchdog`，34 项通过。
- TDD 证据：测试在修复前因 `_broker_ping()` 先于 Session release 被调用而失败；修复后通过。
- 运行态第一层：新 PID `10022` 的真实 refresh 请求为 200；Session 未跨 inspect 持有，无 server closed，但端点仍耗时 24.369 秒。
- 运行态第二层：全新 Python 进程连接真实 Redis/17 个队列，并行 inspect 3.573 秒、`error=None`。再次重启被 PDF 清洗和生财同步安全门禁阻断，因此 HTTP 端点尚未加载第二层代码。
- 完整测试：第二层修复后 1115 项 unit tests 通过。

## 复发预防

- 鉴权可能触发 ORM checkout 的接口，在进入 broker、Redis、浏览器、网络、进程或文件系统等待前必须显式结束只读 Session。
- 契约测试必须验证调用顺序，不能只验证最终 JSON。
- 巡检继续把 checkout 总时长拆成成功 SQL、失败 SQL和非 SQL 持有，并以 request id 关联 PgBouncer 事件。

## 后续事项

- 下一完整窗口要求该端点 PgBouncer query timeout、server closed、`_business_tasks_snapshot` SQL 错误均为 0。
- 安全窗口加载第二层代码后，真实 `inspect=1&refresh=1` 请求最大耗时应低于 10 秒，并确认 active/reserved/scheduled 字段完整。
- 后续 24 小时无复发前保持 `OBSERVING`，不能标记“已解决”。

## 2026-08-13 第三层根因：并发强制刷新锁队列

本轮固定窗口内该端点 74 次请求的 P50/P95/max 为 `0.720/69.753/88.103s`，累计耗时 `631.004s`，成为全站累计耗时最高端点。06:49:16 至 06:51:24 的 8 个 `inspect=1&refresh=1` 请求相互重叠，耗时从 `26.648s` 逐级堆高到 `88.103s`。最慢 request `71e90ffd` 在发起后约 `76.5s` 才出现第一条业务 SQL，SQL 累计约 `6.65s`，证明主要等待不是 PostgreSQL executor。

第二层并行 inspect 已加载，但 `status()` 的全局缓存锁对 `force_refresh` 请求始终忽略刚完成的缓存：等待锁的每个请求拿到锁后仍再次执行完整 broker/进程/数据库快照，形成串行刷新队列。

本轮按 TDD 增加并发强刷契约；旧实现返回两个不同 revision，测试先失败。修复记录每次缓存真实完成时间，仅当等待中的强刷请求发现同 cache key 已在其请求开始后刷新时复用结果。独立的新强刷请求仍会真实刷新，不改变普通缓存、字段完整性或 inspect 语义。

完整重启经两次安全门确认无不可中断任务后完成，新 backend PID `64827` 与 17 个 worker、watchdog、调度器均启动成功。真实 8 并发强刷全部返回 200 和同一 payload hash，P50/max 为 `17.541/17.542s`，不再出现 26→88 秒逐级排队；日志中只有 request `470017de` 执行业务快照 SQL，其余 7 个等待者复用结果。

单次刷新仍耗时约 `17.4s`，其中已记录 SQL 约 `2.9s`（publication_tasks `1.259s`、report_video 聚合 `1.168s`、其余数百毫秒），剩余主要在 broker/control-plane 与网络等待。故状态只能保持 `RUNTIME_REPLACED/OBSERVING`，下一窗口必须继续要求真实强刷 P95 <10 秒、无 PgBouncer timeout/server closed，并把 inspect、进程扫描和各业务快照分别打点后才能闭环。

## 2026-08-16 后台页面轮询放大

本轮 `/api/v1/celery/status` 783 次，累计 1835.735s，P95 10.117s、最大 26.202s，
成为全站累计耗时第一端点；118 次请求匹配慢 SQL，SQL 累计 261.746s，说明主要耗时仍有
broker/control-plane 与网络段。与 watchdog 778 次成对、12.8 小时中位间隔 60s 的证据定位到
未关闭的后台 Celery Monitor 页面，而不是人工强刷。

本轮已给页面加载共享 Page Visibility/in-flight controller，后台隐藏时停止 status/watchdog
轮询、恢复可见时立即刷新且不允许 overlap；真实 Chrome 已加载新静态资源。Session 跨 inspect
旧根因本窗口未出现对应 server-closed 证据，但单次 P95 刚超过 10s、最大仍 26.202s，故本记录
保持 OBSERVING。下一轮要同时验收后台请求归零和可见前台单次 P95 <10s。

## 2026-08-20 物料页横向遗漏与 Redis pidbox 长尾

固定窗口 `/api/v1/celery/status` 再增至 809 次、累计 1750.319 秒、P95 9.352 秒、
最大 21.372 秒；上轮只有 5 次。时间序列定位到研报基础文案物料页仍直接使用 12 秒
`setInterval`，hidden 后 Chrome 节流为每小时 60 次。共享 Visibility controller 已扩展到该页，
并新增读取实际 HTML/业务脚本的契约；但已加载旧 JS 的浏览器实例不会被 backend 重启替换，
本轮后续仍有分钟级请求，因此保持 OBSERVING。

窗口后 06:24–06:25 捕获两条无消费者的临时 `reply.celery.pidbox` 消息；Kombu 尝试恢复时
Redis socket read timeout，记录一次 `Could not restore message`。消息 `delivery_mode=1` 且属于
Celery control reply，不是持久任务投递；随后 broker status 为 ok、17/17 服务运行、队列无已知
业务丢失，但一次 status 探针仍耗时 22.876 秒。该证据确认剩余根因是 Redis/control-plane 与
网络长尾，而非 PostgreSQL executor；未完成下一窗口前不得关闭。
