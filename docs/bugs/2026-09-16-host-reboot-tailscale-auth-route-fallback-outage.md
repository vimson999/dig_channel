# 主机重启后 Tailscale 注销使数据库私网路由落入代理 TUN

## 基本信息

- 日期：2026-09-16
- 状态：OPEN / RECOVERED / OBSERVING（2026-09-17 复核；仍缺完整后窗和启动依赖防线）
- 严重级别：P0
- 影响模块：全站 API / Celery / 调度器 / PgBouncer / Tailscale / Clash TUN
- 首次发现：2026-09-16 每日数据库性能巡检
- 修复提交：无产品代码改动；等待用户恢复 Tailscale 身份后按标准门禁恢复运行态
- 根因分类：运行拓扑依赖、私网身份丢失、代理 TUN 路由回退、启动依赖缺口
- 复发键：`DB-SESSION-LIFECYCLE`、`RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：是；2026-08-15 已出现数据库路由回退到 `198.18.0.1`，本次由主机重启与 Tailscale 注销触发并持续停机
- 关联记录：`2026-08-08-postgres-tunneled-route-query-timeout.md`、`2026-08-15-db-audit-missed-pgbouncer-route-fallback.md`、`2026-07-21-restart-guard-unconditional-direct-db.md`
- 防线层级：runtime_dependency / fail_closed_guard / observability

## 现象

主机在 `2026-09-15 17:39:04` 重启。重启前后端、Celery 与调度器日志在约 17:38 停止；重启后只有
PgBouncer 恢复，其他 17 个服务 PID 文件、Watchdog 与调度器 PID 均已失效，`127.0.0.1:5001/health`
不可达。固定窗口余下 `12 小时 20 分 56 秒` 没有应用服务，因此 HTTP 日志中的“0 个 5xx”不能解释为健康。

PgBouncer 自 17:46:39 起连接数据库私网地址 `100.92.99.39:5432` 时，本地 source 全部变为代理 TUN
`198.18.0.1`；每条连接约 5 秒后崩溃，再约每 15 秒重试。至窗口结束共记录 2,889 次
`server conn crashed?`、2 个去重 `query_wait_timeout` episode，且没有一条恢复到 Tailscale source
`100.96.3.12`。

## 影响

- 全站后端、Celery worker、Watchdog 和调度器在重启后未恢复，页面、API 与任务处理持续不可用。
- PgBouncer 虽监听本机 6432，但远端 PostgreSQL 握手无法完成；TCP 表面可达会误导普通端口 smoke test。
- 查询等待、重连风暴和零吞吐来自不可用路由，不是数据库执行器、锁或索引。
- 无人值守流程不能代替用户完成 Tailscale 登录；强制重启只会把失败扩散到全部服务。

## 根因

第一层是主机重启：`kern.boottime` 为 `2026-09-15 17:39:04`，重启直接终止了原有应用进程与
Tailscale 会话。

第二层是依赖启动顺序和身份状态：Clash Verge service 在 17:39:17 启动，Clash/Mihomo 在
17:40:33–17:40:36 启动；Tailscale 组件更晚启动，且当前 `BackendState=NeedsLogin`、无
`TailscaleIPs`、self inactive/offline。系统因此没有可用的 Tailscale 私网路径。

第三层是路由回退：`route -n get 100.92.99.39` 显示 gateway `198.18.0.1`、interface `utun0`，
`ifconfig utun0` 同样为 `198.18.0.1`。这条私网目的地址被代理 TUN 捕获，PgBouncer 建连后无法完成
PostgreSQL 握手并持续崩溃重试。

第四层是启动依赖缺口：PgBouncer 能在 Tailscale 未登录、路由错误时启动并循环重连，但应用整栈没有进入
可观测的“等待私网身份”状态。现有重启安全门能阻止进一步破坏，却不能自行恢复外部身份。

## 解决思路

先恢复身份和正确路由，再恢复数据库连接池与应用；顺序不能颠倒。恢复必须由用户登录 Tailscale，随后验证
私网 IP、route 与 PgBouncer source，最后运行既有双重安全门标准重启。禁止使用 FORCE、提高 timeout、
把数据库私网地址继续交给代理 TUN，或将一次 TCP connect 当作 PostgreSQL 可用。

## 实际方案

- 本轮仅执行只读取证与 fail-closed 门禁，没有修改系统路由、代理、Tailscale、PgBouncer 或数据库配置。
- `PYTHONPATH=src/backend venv/bin/python scripts/dev/check-restart-safety.py` 在连接
  `127.0.0.1:6432` 超时时以 exit 3 拒绝重启；未设置 `FORCE_RESTART_ACTIVE_TASKS=1`。
- 未执行产品代码修复、生产 DDL、历史任务重写或自动重投。
- 待用户登录 Tailscale 后，必须依次验证 `BackendState=Running`、本机 Tailscale IP 存在、数据库 route
  不再指向 `198.18.0.1`、PgBouncer 新 server connection source 恢复为 Tailscale 地址，再运行安全门和
  `scripts/dev/restart-dev-stack.sh`。
- 建议的后续结构性防线：启动编排在 PgBouncer/应用前检查 Tailscale 已登录且目标 route 符合预期；失败时
  输出可操作的 blocked 状态并停止重连风暴。该改动涉及系统启动与外部身份契约，本轮未在无运行验收条件下盲改。

## 验证

- `sysctl kern.boottime`：`Tue Sep 15 17:39:04 2026`。
- 进程日志：重启前最后活动约 17:38，自动调度器 17:38:48 收到 SIGTERM；重启后仅 PgBouncer 存活。
- `tailscale status --json`：`BackendState=NeedsLogin`、`TailscaleIPs=null`，健康信息明确提示已注销且无法连接协调服务。
- route/接口：数据库目标走 `gateway=198.18.0.1 interface=utun0`，`utun0=198.18.0.1`。
- PgBouncer：17:46:39 至窗口结束 2,889 次 server crash、2 个 query-wait episode、3,029 次新 server
  connection；其中 2,889 次 source 为代理 TUN，重启前仅 140 次为 Tailscale source。
- 应用：`127.0.0.1:5001/health` 不可达；17 个 worker PID、Watchdog 与调度器 PID 文件均为 stale。
- 重启门禁：数据库握手超时并 exit 3，明确输出 `Refusing the restart`；证明 fail-closed 生效，但运行态尚未恢复。

## 复发预防

- 主机启动时把 Tailscale 身份和数据库目标 route 作为 PgBouncer、API、worker、Watchdog、scheduler 的显式前置依赖。
- 启动前同时校验 Tailscale 状态、目标 gateway/interface 和实际 PostgreSQL 握手；禁止只检查 TCP SYN。
- PgBouncer 发现连续 source=`198.18.0.1` 或 server crash 时应退避并发出单一高优先级告警，避免无限重连淹没日志。
- 每日巡检继续将主机 boot time、服务 PID、新连接 source 和 HTTP 日志覆盖时长联合判断，不能把日志中断后的空白算作零错误。

## 后续事项

- 用户需在本机完成 Tailscale 登录；这是当前唯一需要人工权限的阻塞项。
- 登录后先验证 route/source，再执行安全门和标准整栈重启；确认 `/health`、bootstrap 17/17、全部 worker/
  scheduler ready 以及 PgBouncer `SHOW POOLS/SERVERS` 正常。
- 至少执行一条真实页面/API 和一条真实后台任务，并在随后完整 24 小时要求 proxy TUN fallback、server crash、
  query-wait/connect/query timeout 均为 0，才可从 `OPEN/BLOCKED_USER_AUTH` 转入 `OBSERVING`；不得直接关闭。

## 2026-09-17 恢复后复核

09-16 06:00–09-17 06:00 固定窗内，代理 TUN source 和 server crash 各 571 次，最后一条错误连接在 08:24:59，
08:25:16 首条新连接已从 Tailscale `100.96.3.12` 成功 TLS 握手。后端 08:28:11 启动，08:28:55 完成 17/17
Celery、Watchdog 和六个 scheduler 的 bootstrap；当前 `/health=healthy`、Tailscale `Running`、DB route 为
`utun6`，PgBouncer 六条 server 的 local_addr 均为 `100.96.3.12`，`cl_waiting=sv_login=0`。

08:25:16 后约 21.58 小时，261 次新建 server connection、0 proxy source、0 crash、0 query/wait/connect timeout。
自然流量 133 个 HTTP 请求、151 个 Celery 成功完成；三条网页 PDF 提交均 completed/done，31/31 资产 cleaned。
恢复由外部身份/路由状态变化实现，本轮没有产品代码、生产配置或 SQL 变更，也没有重启。启动时如何等待身份恢复、
拒绝错误路由并在恢复后自行启动应用仍缺系统性防线；现有 PgBouncer 在错误 route 下持续重连。下一次要先做
启动依赖与自动恢复方案，再以可控重启故障注入验收；当前仅属运行恢复，不是根因闭环。09-17 08:25:16 后才
达到完整 24 小时观察窗，下一轮还需确认窗口后续未复发。详见
`reviews/2026-09-17-db-performance-audit.md`。

## 2026-09-18 完整后窗复核

09-17 06:00–09-18 06:00 的 289 条 PgBouncer 新服务连接全部来自 Tailscale `100.96.3.12`，
没有代理 TUN source；当前 Tailscale `Running`、DB route=`utun6`、backend health=200，
主机重启后的身份/路由恢复持续超过 24 小时。但 01:36:30 发生一次独立的
`query_wait_timeout`，随后 TLS 接收超时、server crash、重连失败和 ZSXQ 调度器
`server closed the connection unexpectedly`。这是正确私网 route 上的短时连接故障，
不能把“无代理 fallback”扩写为数据库链路无复发。其失败 SQL 15.208 秒且不在完成慢 SQL 日志中。
上游 Tailscale/远端网络原因尚未定位；整体继续 OPEN/REPEATED/OBSERVING。
本轮同时发现故障时 ZSXQ 停用计划被错误 fallback 启用，另见
`2026-09-18-zsxq-schedule-db-error-enabled-disabled-targets.md`。
