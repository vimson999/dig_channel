# 2026-06-24 Celery Watchdog 留下 idle in transaction 连接

## 基本信息

- 状态：已修复，待重启后复查 `pg_stat_activity`
- 严重级别：高
- 影响模块：`celery_watchdog_loop.py`、数据库连接池、PostgreSQL 事务清理
- 首次发现：Top10 修复后检查 `pg_stat_activity`，发现 watchdog 连接处于 `idle in transaction` 约 48 分钟
- 修复提交：待提交

## 现象

数据库健康检查显示：

```text
state = idle in transaction
application_name = dig_ytb_scheduler_celery_watchdog_loop_py_45218
xact_age ≈ 48 分钟
```

查询内容指向 `publication_tasks` 的 worker 状态检测。

## 影响

- 长事务会占用数据库连接。
- 长事务会保留 MVCC 快照，影响 vacuum 和数据库整体健康。
- 在连接数紧张时，会放大“数据库又被打挂”的风险。

## 根因

`scripts/dev/celery_watchdog_loop.py` 每轮使用：

```python
with app.app_context():
    result = run_watchdog_once()
```

但循环结束后没有显式 `db.session.rollback()` / `db.session.remove()`。

Flask 请求生命周期会自动清理 session，但独立常驻脚本没有请求生命周期，必须自己释放。某轮查询后 session 保留在事务中，最终形成 `idle in transaction`。

二次复查发现，只在外层循环末尾释放还不够硬：`run_watchdog_once()` 内部先查询业务任务快照、自动收口和研报视频统计，随后还会继续做 Redis、Celery inspect、进程扫描和文件状态检查。在这些非 DB 步骤执行期间，前面的只读查询事务可能短暂停留为 `idle in transaction`。

因此根因不是“是否有长 idle 连接”，而是后台脚本和监控函数把 DB 查询事务带到了后续非 DB 工作里。`idle` 可以受控存在，`idle in transaction` 不能长期存在。

## 解决思路

所有常驻脚本每轮任务都必须显式释放数据库 session：

1. 正常结束也 rollback/remove。
2. 异常结束也 rollback/remove。
3. 不依赖 Flask 请求 teardown。

## 实际方案

在 `scripts/dev/celery_watchdog_loop.py` 中：

- 引入 `from models import db`。
- 在每轮 `run_watchdog_once()` 的 `finally` 中执行：
  - `db.session.rollback()`
  - `db.session.remove()`

在 `src/backend/apis/v1/celery_monitor.py` 中：

- 增加本地 `_release_db_session_quietly()`。
- `_watchdog_repair_stale_business_tasks()` 执行自动修复后立即释放 session。
- `_business_tasks_snapshot()` 读取业务任务和自动收口后释放 session。
- `_zsxq_business_tasks_snapshot()` 分别读取同步/编排运行记录后释放 session。
- `_report_video_stage_stats()` 读取统计后释放 session。
- `run_watchdog_once()` 获取业务任务快照后再次兜底释放 session，避免后续 inspect/进程检查阶段挂住事务。

## 验证

验证方式：

```bash
PYTHONPATH=src/backend venv/bin/python -m py_compile scripts/dev/celery_watchdog_loop.py
PYTHONPATH=src/backend venv/bin/python -m py_compile src/backend/apis/v1/celery_monitor.py
```

运行态验证：

- 重启 `celery_watchdog`。
- 再次查询 `pg_stat_activity`，不应再出现长时间的 watchdog `idle in transaction`。

## 复发预防

- 所有 scheduler、watchdog、常驻 loop 都必须在每轮结束释放 `db.session`。
- DB 查询函数如果后续还会执行 Redis、文件、进程、浏览器或外部请求，必须在查询后立即释放 `db.session`，不能只依赖外层循环。
- 健康检查不只看连接数，还要看 `idle in transaction` 年龄。
- 排障脚本同样不能长期持有事务；查询后应 `db.session.remove()`。
- 新增专项纪律：[数据库 Session 生命周期纪律](../rules/database-session-lifecycle-discipline.md)。
