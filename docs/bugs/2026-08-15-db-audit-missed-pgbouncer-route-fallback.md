# DB 巡检未识别 PgBouncer 路由回退与等待错误

## 基本信息

- 状态：分析器修复并完成历史窗口回放；根因网络簇仍 `OPEN/OBSERVING`
- 严重级别：P0
- 影响模块：DB 每日巡检 / PgBouncer / Tailscale / 本机代理 TUN / scheduler
- 首次发现：2026-08-15
- 修复提交：待提交
- 根因分类：跨观测面漏报 / 网络路由来源未归因
- 复发键：`REGRESSION-ACCEPTANCE-INTEGRITY`
- 是否复发：是
- 关联记录：`2026-08-04-db-audit-skipped-gzip-rotations.md`、`2026-08-06-idle-transaction-pressure-observability-gap.md`、`2026-08-08-postgres-tunneled-route-query-timeout.md`
- 防线层级：observability / contract_test / runtime_correlation

## 现象

2026-08-14 06:02:19 至 2026-08-15 06:02:19 的初次分析只统计了客户端
`query timeout` 与 `connect timeout`，没有独立识别 `query_wait_timeout`、
`server conn crashed?`、TLS receive error，也没有报告 PgBouncer server connection
使用的本地 source address。

历史窗口回放后确认：13:07:08 至 13:10:56 有 16 次 server connection 从正常
Tailscale 地址 `100.96.3.12` 回退到代理 TUN 地址 `198.18.0.1`；同窗发生 9 个去重
`query_wait_timeout`、18 个 server connection crash、3 个 TLS receive error，并导致
8 个 scheduler repair origin 共 16 条应用 SQL 错误。

## 影响

- 每日巡检会把连接池内等待和路由回退折叠成普通数据库长尾，无法定位到网络路径。
- 只看 PostgreSQL executor 或客户端 query timeout 会低估失败风暴，诱导错误加索引或加超时。
- 路由恢复后日志数量下降容易被误写为根治，实际后续窗口仍有 query/connect timeout。

## 根因

分析器的 PgBouncer 事件分类只覆盖 `query timeout`、`connect timeout`、登录/HBA 错误；
它没有解析 server connection 的 source address，也没有把代理保留网段
`198.18.0.0/15` 标为 proxy TUN。因而原始日志已经包含完整证据，但报告没有建立
“路由来源变化 → pool 等待 → server crash/TLS 错误 → 应用 SQL 失败”的证据链。

## 解决思路

1. PgBouncer 的客户端超时、pool 等待、server crash 与 TLS 接收错误必须分栏统计。
2. 从 server connection 日志提取 source address，并显式标识 `198.18.0.0/15` 回退。
3. 保留原始行数与按时间戳去重事件两个口径，避免多行事件造成误判。
4. 用历史故障片段做契约测试，确保下一轮相同指纹自动复发升级。

## 实际方案

- `scripts/diagnostics/analyze_db_performance_logs.py` 新增 `query_wait_timeout`、
  `server_conn_crashed`、`tls_receive_error` 分类。
- 新增 server source address 解析及 proxy TUN 判定；输出连接数、首次/末次时间。
- `tests/unit/test_db_performance_log_analyzer.py` 增加分类、地址判定与主程序输出测试。
- 测试先在旧实现上得到缺分类/缺函数的 RED，再在实现后 GREEN。

## 验证

- 4 个新增定向测试通过。
- 同一固定窗口回放得到原始行：query timeout 15、query-wait 18、server crash 18、
  TLS receive error 3、connect timeout 7；按时间戳去重事件分别为 5、9、18、3、7。
- source address 为 `100.96.3.12` 1,582 次、`198.18.0.1` 16 次；后者只出现在
  13:07:08–13:10:56，并与应用 SQL 失败风暴对齐。
- 当前只读路由检查和数据库连接快照均恢复为 Tailscale `utun5` / `100.96.3.12`。
- 完整验证结果见 `reviews/2026-08-15-db-performance-audit.md`。

## 复发预防

- 每日巡检必须同时报告客户端 query/connect timeout、pool query-wait、server crash、
  TLS 错误及 server source address；任何单项为零都不能替代其他口径。
- 检出 `198.18.0.0/15` source 时立即按 `DB-SESSION-LIFECYCLE` 网络簇重新打开，
  不得以 executor 快或流量下降关闭。
- 完整 24 小时托管 Web/worker/scheduler 的上述事件、失败 SQL 全部为零，且真实页面与任务
  成功，才允许从 OPEN 转入 OBSERVING。

## 后续事项

- 路由回退导致的失败风暴已定位，但路由控制面根因尚未修复，仍需检查代理配置与 Tailscale
  path 变化；巡检不会无人值守修改系统路由。
- 即使 route 已恢复，窗口后段仍有 query/connect timeout；下一轮必须分别归因，不能把所有
  网络异常都归给 13:07 的一次回退。
