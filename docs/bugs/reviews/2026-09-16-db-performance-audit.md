# 2026-09-16 每日数据库性能与错误巡检

## 结论

固定窗口 `2026-09-15 06:00:00` 至 `2026-09-16 06:00:00`（Asia/Shanghai）内发现 P0 持续停机：
主机于 `2026-09-15 17:39:04` 重启，Tailscale 重启后处于 `NeedsLogin` 且没有私网 IP，数据库目标
`100.92.99.39` 被路由到 Clash/Mihomo 代理 TUN `198.18.0.1`。PgBouncer 从 17:46:39 起持续连接崩溃，
后端、17 个 worker、Watchdog 与调度器均未恢复，固定窗口最后 `12:20:56` 没有应用服务。

因此本窗口较低的慢 SQL 与 HTTP 失败数不代表性能改善：它们只覆盖重启前约 11 小时 39 分钟。根因状态为
`OPEN / BLOCKED_USER_AUTH / REPEATED`。重启安全门已因数据库握手超时 fail closed；本轮未绕过门禁、未 FORCE、
未修改外部网络配置、未执行生产 DDL，也未改产品代码。

## 与上一窗口对比

| 指标 | 2026-09-15 | 本窗口已观测部分 | 变化与解释 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 132 | 18 | -86.4%；服务在 17:39 后停机，不能视为改善 |
| 应用慢 SQL 总耗时 | 223.626s | 29.122s | -87.0%；同上 |
| SQL P50 / P95 / max | 1.271 / 2.933 / 19.799s | 1.333 / 1.861 / 4.704s | 仅重启前样本 |
| HTTP 完成 / 成功 / 失败 | 350 / 342 / 8 | 1,784 / 1,759 / 25 | 25 个均为 404/405；17:39 后无服务日志 |
| HTTP P50 / P95 / max | 50.16ms / 1.890s / 13.900s | 163.59ms / 423.28ms / 5.112s | 只覆盖在线部分 |
| Celery 成功 / 失败 | 110 / 1 | 83 / 0 | 仅 workflow notification，重启后 worker 全停 |
| PgBouncer server crash | 0 | 2,889 | 17:46 后持续代理 TUN 握手失败 |
| PgBouncer query-wait episode | 4 | 2 | 两个客户端在停机初段耗尽等待预算 |

## P0 根因证据链

1. `kern.boottime` 为 17:39:04；重启前日志约 17:38 停止，`zsxq_auto_scheduler` 在 17:38:48 收到 SIGTERM。
2. Clash service/Mihomo 在 17:39–17:40 先恢复；Tailscale 组件更晚启动，当前状态为 `NeedsLogin`、无
   `TailscaleIPs`、self inactive/offline。
3. 数据库目标 route 当前为 `gateway=198.18.0.1 interface=utun0`；这正是代理 TUN，而不是重启前的
   Tailscale source `100.96.3.12`。
4. PgBouncer 17:46:38 启动。17:46:39 至窗口末 2,889 次新 server connection 均从 `198.18.0.1`
   发出，约 5 秒后 crash，再约每 15 秒重试；没有恢复样本。
5. 后端健康端口不可达，所有 worker/Watchdog/scheduler PID 均 stale。重启安全门经 6432 无法完成数据库
   握手并 exit 3，明确拒绝重启。

这是外部身份与运行拓扑依赖故障，不是 SQL executor、锁、索引或连接池容量问题。TCP 端口可建立只是 TUN
截获结果，不能证明 PostgreSQL 协议可用。

## 重启前慢 SQL 与页面链路

- 18 条应用慢 SQL 合计 29.122 秒。ZSXQ Insights `_aggregate_themes` 6 次/10.503 秒；图表数据 1 次/
  4.704 秒；通知 provider drain 3 次/3.828 秒；其余为 report audio finalization、下载器列表与进度、
  通知 enqueue、排行榜、Publisher/业务 repair，各 1.081–1.375 秒。
- 重启前发生 10 次 Flask 开发重载。每次启动都会触发 ZSXQ Insights warmup，解释 6 条聚合慢样本；这是
  开发改动导致的冷缓存放大，不是自然页面请求或新产品缺陷。窗口内没有 Insights/local/tabs/sync-status
  实际 API 请求，不能据此宣称页面已健康。
- HTTP 共 1,784 次：1,759 次成功；20 个 favicon 404、3 个 channel profile 404、1 个 yearly revenue
  404、1 个 downloader cancel 405，无已记录 5xx。但这些数字仅到重启前。
- `/api/v1/downloader/tasks` 765 次/194.474 秒，P95 392.74ms；chart 19 次/16.579 秒，最大 5.112 秒并匹配
  4.704 秒慢 SQL；downloader submit 6 次/11.246 秒，最大 2.044 秒且无匹配慢 SQL。
- channel profile 11 次/9.243 秒、3 个 404；monthly revenue 33 次/11.211 秒；data table 5 次/4.163 秒、
  最大 1.093 秒且无匹配慢 SQL。新排行榜/profile 样本与当前未提交开发改动重叠，本轮不改用户工作区。

## Celery、Session 与 SQL 错误

- Celery 83 次成功、0 retry、0 failure，全部为 workflow notification；总耗时 201.565 秒，P95/max
  4.488/5.498 秒。重启后的零任务是 worker 不存在，不是队列健康。
- 重启前应用侧 connection hold、server closed、statement/lock/deadlock/query/connect timeout、
  idle-in-transaction 均为 0。
- 唯一 SQL 错误是巡检 ad-hoc 诊断使用了不存在的函数，已与应用口径分栏；应用 SQL error/traceback 为 0。
- PgBouncer 全窗新建 server connection 3,029 次：重启前 Tailscale source 140 次，重启后代理 TUN
  source 2,889 次。全窗 wait/query P95 49.97/80.25ms 被停机后的零吞吐稀释，不可用于证明健康。

## Executor、索引与膨胀

数据库当前不可完成协议握手，不能安全采集本窗口的 `pg_stat_activity`、表膨胀、索引使用、重复索引或
`EXPLAIN (ANALYZE, BUFFERS)`。上一窗口的 0.025–34.993ms executor、无等待锁、无 idle-in-transaction、
精确重复索引为 0，只能作为历史基线，不能冒充当前证据。

本轮没有新增/删除索引建议。连接链路未恢复前，任何 DDL、VACUUM 或 timeout 调整都与根因无关且不安全。

## 根因闭环状态

| 层级 | 状态 | 证据 / 下一门槛 |
| --- | --- | --- |
| 触发事件 | 已定位 | 主机 17:39:04 重启 |
| 身份状态 | 已定位、未恢复 | Tailscale `NeedsLogin`，需用户登录 |
| 路由 | 已定位、未恢复 | DB 目标走 `198.18.0.1/utun0` |
| 连接池 | 持续失败 | 2,889 次 server crash、2 个 query-wait episode |
| 应用运行态 | 停机 | health 不可达，worker/scheduler PID stale |
| 安全门 | 正常 fail closed | 数据库握手超时，exit 3，未 FORCE |
| 完整验收 | 未开始 | 登录后 route/source、整栈 readiness、真实 API/任务与 24h 后窗 |

## 验证状态

- 数据库日志分析器定向回归 `Ran 30 tests / OK`。
- worker app-factory 边界检查通过，扫描 28 个文件；Celery revoke 边界检查通过，扫描 321 个 Python 文件。
- 严格 Bug registry 200/200 条已归类，JSON 解析、`compileall` 与 `git diff --check` 通过。
- 完整 unit 在允许本地回环后运行 1,689 项，34 个沙箱端口权限错误已消失；剩余 1 个失败为既有排行榜
  工作区改动的日期断言：实现返回巡检当天 `2026-09-16`，测试仍硬编码 `2026-09-15`。该文件族在本轮开始前
  已有未提交改动，且与数据库停机根因无关，本轮未覆盖或顺手修正用户工作区。
- 运行态验收被外部身份阻塞：`/health` 仍不可达，不能把静态/单测通过写成服务已恢复。

## 恢复计划与下一轮

1. 用户在本机登录 Tailscale；确认 `BackendState=Running`、本机 Tailscale IP 存在。
2. 确认数据库目标 route 不再是 `198.18.0.1/utun0`，PgBouncer 新 server connection source 恢复为
   Tailscale 地址，且 PostgreSQL 握手成功。
3. 重新运行 `scripts/dev/check-restart-safety.py`；只有门禁成功才执行 `scripts/dev/restart-dev-stack.sh`，
   全程不使用 FORCE。
4. 验收 `/health`、bootstrap 17/17、全部 worker/scheduler ready、PgBouncer `SHOW POOLS/SERVERS`，再完成
   一条真实 API 和一条真实后台任务。
5. 下一完整 24 小时要求 proxy TUN fallback、server crash、query-wait/connect/query timeout 均为 0；达到前
   保持 `OPEN/BLOCKED_USER_AUTH/OBSERVING`，不得标记关闭。

详细缺陷记录：[`2026-09-16-host-reboot-tailscale-auth-route-fallback-outage.md`](../2026-09-16-host-reboot-tailscale-auth-route-fallback-outage.md)。
