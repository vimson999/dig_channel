# ZSXQ 全部 Tab 调度器重复读取远端配置

## 基本信息

- 状态：代码、回归与运行进程重载完成，待下一完整观测窗口验收
- 严重级别：P1
- 影响模块：ZSXQ 全部 Tab 自动同步 / scheduler / system_configs / PgBouncer
- 首次发现：2026-08-03
- 修复提交：当前工作区
- 根因分类：高频状态轮询、进程内稳定配置未复用
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；此前已要求热点轮询使用短 TTL、新鲜缓存和写入失效，但全部 Tab 调度器仍在每轮读取远端配置
- 关联记录：`2026-08-02-request-auth-duplicate-user-query.md`、`2026-07-22-pgbouncer-cold-pool-connect-timeout.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

- 最近 24 小时同一 `system_configs` 查询出现 91 次，累计 132.085 秒，P50/P95/最大 1.122/2.696/4.274 秒。
- `EXPLAIN (ANALYZE, BUFFERS)` 显示查询 PostgreSQL executor 仅 0.020ms；慢耗时来自远端 PgBouncer/网络往返，而不是缺少索引。
- 调度循环约每 30 秒调用 `load_all_tabs_auto_sync_configs()`，即使配置没有变化也重新读取数据库。

## 影响

- 稳定配置轮询持续占用远端连接和网络往返预算，并在连接池抖动时被放大到秒级。
- 调度器属于无前台端点的后台流量，单看页面请求无法发现这部分累计耗时。

## 根因

配置读取函数没有进程级缓存；调度频率远高于人工配置变更频率。现有唯一/条件索引已足够，增加索引不能消除网络往返。

## 解决思路

- 对已规范化配置使用 60 秒进程级缓存；返回深拷贝，禁止调用者污染共享值。
- 保存配置或补入默认配置后立即失效缓存。
- 数据库读取失败时沿用原兜底语义，不缓存失败结果。

## 实际方案

- `load_all_tabs_auto_sync_configs()` 在 TTL 内复用完整配置快照，再按 `include_disabled` 过滤。
- `save_all_tabs_auto_sync_config()` 和默认配置插入成功后调用 `_invalidate_config_cache()`。
- 新增测试约束 TTL 内只查询一次且返回值相互隔离。

## 验证

- 相关定向回归包含在 `130 passed` 中；完整 unit tests 为 `1006 passed, 3 skipped, 1 failed`，唯一失败是既有火柴人页面 cache-bust 断言漂移。
- 重启安全门禁通过，后端、Celery 与 scheduler 已重载；健康检查返回 `healthy`。
- 本轮没有执行 DDL、数据回填或删除原始 JSON。

## 复发预防

- 配置/状态轮询必须记录调用周期与数据变化周期；稳定配置应使用有界 TTL，并在写入口失效。
- executor 小于 10ms 而应用查询超过 500ms 时，优先减少往返并检查 PgBouncer/网络，不得盲目增加索引。
- 下一窗口按 SQL origin 统计该指纹，验收次数与累计耗时至少下降约一半。

## 后续事项

- 下一轮用新 scheduler PID 的完整 24 小时日志验收查询次数、累计耗时和异常回退。
- 若远端链路仍频繁出现 60 秒 query timeout，单独处理 PgBouncer/网络长尾。
