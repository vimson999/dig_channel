# PostgreSQL 白名单拒绝变化后的出口 IP

## 基本信息

- 状态：已定位，等待数据库管理员恢复白名单
- 严重级别：P0
- 影响模块：全部页面/API / Celery / scheduler / PgBouncer / 远端 PostgreSQL
- 首次发现：2026-08-04 01:38:53
- 修复提交：不适用；生产访问控制需人工处理
- 根因分类：数据库网络访问控制与出口 IP 稳定性
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：同类第三次观测；PgBouncer 归档曾在 2026-07-27 捕获另一出口 IP 的相同 HBA 拒绝，
  2026-08-06 10:29 起再次拒绝当前出口
- 关联记录：`2026-07-22-pgbouncer-cold-pool-connect-timeout.md`、`2026-07-21-restart-guard-unconditional-direct-db.md`
- 防线层级：observability / operational_guardrail

## 现象

### 2026-08-06 运行态复现

- 10:29:45 起 PgBouncer 连续记录 `no pg_hba.conf entry for host "223.80.177.164"`；
  10:30:34 起本地客户端开始命中 `cached error`，平台凭据、MP3 详情等业务 API 返回 500。
- `GET /health` 仍为 200，但这只证明 Flask 存活，不证明数据库 readiness；当前后端 PID `47594`
  和 ZSXQ worker `48560` 没有被重启脚本停止。
- 10:27:45 的重启安全门禁曾通过，但随后 `restart-dev-stack.sh` 在 schema 预检阶段因
  `server closed the connection unexpectedly` 中止；未执行强制重启，也未改 HBA/安全组。

- `2026-08-04 01:38:53` 起，PgBouncer 持续记录 `no pg_hba.conf entry for host 223.80.177.167`。
- 巡检结束时累计 1,054 次远端 server login/HBA 拒绝，另有 901 次客户端命中 PgBouncer 缓存失败。
- Watchdog 的终态父子清理查询累计 176 次 OperationalError；首次两次等待 15.16 秒/1.14 秒，后续约每 90 秒快速失败。
- `/api/v1/celery/status` 在故障后 5 次请求全部返回 500；`/health` 仍返回 200，形成表面存活、业务数据库不可用的观测差异。

## 影响

- 所有依赖远端数据库的页面、API、worker 和 scheduler 无法可靠读写。
- 当前无法取得 `pg_stat_user_tables/indexes`、锁、活动事务与 EXPLAIN 证据，也无法核验活动任务，因此禁止重启和生产 DDL。
- 失败发生在连接登录阶段，不是 PostgreSQL executor 慢；增加索引无效。
- 本次必须先由数据库管理员恢复 `223.80.177.164` 的最小白名单或稳定出口；在恢复前，任何“慢查询
  下降/索引有效”的结论都不可信，因为连接登录本身失败。

## 根因

本机/网络出口 IP 变化为 `223.80.177.167`，但远端 PostgreSQL 的 HBA/安全组未授权该地址。PgBouncer 进程和本地 6432 监听仍在，拒绝来自远端访问控制。

## 解决思路

由数据库管理员在确认来源后恢复最小范围白名单，或把应用迁移到稳定出口/VPN/SSH 隧道；恢复后先执行只读连接和活动任务门禁，再决定是否重启应用栈。

## 实际方案

本轮没有修改生产 HBA、安全组、数据库配置或应用连接串，也没有使用 `FORCE_RESTART_ACTIVE_TASKS`。分析器已增加 HBA 拒绝和 PgBouncer 缓存失败的独立统计，避免继续被归类为普通慢 SQL。

## 验证

- PgBouncer 日志精确定位首个当前出口 IP 拒绝时间和来源地址。
- 沙箱外运行 `scripts/dev/check-restart-safety.py` 仍因 server closed 失败并 fail closed，退出码 3。
- 本地进程证据显示 Flask PID 96266 仍监听 5001、PgBouncer PID 46016 仍监听 6432；不是本地监听器消失。

## 复发预防

- 数据库入口应使用稳定出口或显式维护的最小 CIDR，不依赖易变化的临时公网地址。
- `/health` 必须和独立数据库 readiness 分开使用；运维验收不能只看 Flask 存活。
- 重启门禁查询失败继续等同于门禁失败，禁止用强制参数掩盖数据库不可达。
- HBA/server login failure 必须作为独立 P0 告警，不能等业务 SQL 错误累计后才发现。

## 后续事项

- 人工授权 `223.80.177.167` 或恢复稳定出口后，先重跑重启安全门禁、数据库统计和代表性 EXPLAIN。
- 恢复后观察至少 30 分钟：HBA 拒绝为 0、业务 SQL error 为 0、核心端点可用，再进入重启或索引维护窗口。
