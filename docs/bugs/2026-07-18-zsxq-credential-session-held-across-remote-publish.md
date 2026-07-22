# ZSXQ 发帖凭据会话跨远程等待后失效

## 基本信息

- 状态：代码已修复并完成自动化回归，待新 worker 真实发帖验收
- 严重级别：P0
- 影响模块：ZSXQ 自动发帖 / 平台凭据 / SQLAlchemy Session / PgBouncer
- 首次发现：2026-07-17 22:51，2026-07-18 多次复发
- 修复提交：未提交
- 根因分类：数据库会话跨外部 I/O、失效连接缺少幂等重试
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：是
- 关联记录：`2026-06-24-zsxq-sync-download-held-db-session.md`、`2026-06-24-zsxq-sync-db-connection-closed-mid-download.md`
- 防线层级：transaction_boundary / idempotent_retry / actual_sql_contract / runtime_acceptance

## 现象

知识星球内容已经上传并通过回查后，写入一行 `platform_credential_accounts` 成功状态时出现：

```text
[ZSXQ_AUTO_PUBLISH] credential success mark failed
psycopg2.OperationalError: server closed the connection unexpectedly
```

2026-07-17 22:51 和 2026-07-18 06:38、06:41、11:22、17:32 均出现同一错误。失败 SQL 只是按主键更新凭据状态，随后其他 SQL 很快恢复，说明它不是表扫描或索引问题。

## 根因

`resolve_zsxq_cookie_accounts()` 已把 ORM 记录转换成普通字典，但当 `last_used_at` 尚未到刷新间隔时不会提交事务。调用方随后持有这条 ORM 会话，进行上传、60 秒重试和30 秒回查，最后再用原会话写成功标记。连接在长等待期间失效时，首条状态 UPDATE 就会失败。

状态标记本身是幂等写入，但旧实现对失效连接没有内部重试，只由上层记录错误并继续，造成重复告警和凭据状态滞后。

## 实际方案

- `_db_publish_cookie_accounts()` 无论成功还是异常，都在凭据已物化后立即 `db.session.remove()`，外部上传和回查期间不再持有数据库事务或连接。
- 平台凭据成功/失败标记共用 `_update_credential_state()`，遇到 SQLAlchemy 明确标记为 `connection_invalidated` 的 DBAPI 错误时回滚并重试一次。
- 非连接失效类数据库错误不重试，避免掩盖约束、SQL 或权限错误。
- 重试写入使用相同时间戳和值，重复执行不会破坏业务状态。

## 当前验证

- 新增真实 SQLite 事务回归：第一次 commit 模拟失效连接，第二次成功，最终凭据从 `expired` 恢复为 `active`，且只回滚一次。
- 新增反例：普通非失效数据库错误只执行一次，不被错误吞掉。
- 新增会话边界回归：凭据解析返回后、远程发帖前必须调用 `db.session.remove()`。
- 平台凭据、ZSXQ 状态写和 SQL 纪律定向回归：`55 passed`。
- 生产日志与 PgBouncer 日志已确认 17:31 左右存在连接超时/断开；同一列表 SQL 的生产 `EXPLAIN ANALYZE` 实际执行仅 `0.118ms`，不能再误判为缺索引。

## 验收门槛

1. 完成待办数据库迁移并启动新 ZSXQ publish worker PID。
2. 真实执行至少一轮包含上传重试和连续回查的发布任务。
3. 新 PID 日志中不得再出现 `credential success mark failed` 或 `server closed the connection unexpectedly`。
4. 发布期间 `pg_stat_activity` 不得出现由该 worker 长时间保留的 `idle in transaction`。
5. 发布业务结果、凭据最后使用时间和错误分类必须保持完整。

## 复发纪律

- ORM 数据一旦转换为供外部调用使用的 DTO，必须立即结束数据库事务，禁止跨网络、浏览器、文件处理或 sleep 持有 Session。
- 连接失效重试只允许用于可证明幂等的单行状态写，并必须区分连接失效与业务 SQL 错误。
- 外部操作成功后的辅助状态写失败不能反向篡改业务成功结果，但必须有独立告警和可验收的重试机制。
