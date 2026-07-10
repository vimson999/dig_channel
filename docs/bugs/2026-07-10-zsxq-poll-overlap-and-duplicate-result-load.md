# 2026-07-10 ZSXQ 轮询重叠与同步结果重复读取

## 基本信息

- 状态：修复已部署，等待真实负载验收
- 严重级别：高
- 影响模块：知识星球同步页面、`zsxq_sync` worker、清洗投递
- 首次发现：MP3 修复边界后继续观察慢查询时发现
- 修复提交：待提交

## 现象

- `/api/v1/zsxq/sync/runs` 每 5 秒轮询一次。
- 2026-07-10 11:05:12 发出的请求耗时 10.79 秒；旧请求未结束时，11:05:17 和 11:05:22 又继续发出请求，形成同类 SQL 并发堆积。
- 后续两条相同列表 SQL 分别耗时 10.55 秒和 6.23 秒。
- `zsxq_sync` worker 读取带 `result` 的 `ZsxqSyncRun` 单行时出现 15.52 秒和 3.65 秒慢查询。

## 根因

前端用 `setInterval(async () => ...)` 轮询，但没有 in-flight 门禁。请求超过 5 秒时，浏览器仍继续创建下一请求，把远程数据库链路的一次抖动放大成并发堆积。

同步完成后提交 PDF/音频清洗时，代码已经加载了 `ZsxqSyncRun.result`，随后又调用按 ID 加载函数，重复读取同一整行 JSON。

数据库执行计划排除了索引问题：20 行列表查询使用 `ix_zsxq_sync_runs_group_created_at_desc`，PostgreSQL 服务端执行时间为 0.064 ms。主要风险来自客户端到远端数据库的间歇性链路延迟和应用重复往返。

## 实际方案

- ZSXQ 状态轮询增加 `syncPollInFlight`，同一页面任何时刻最多一个请求在途。
- PDF/音频清洗投递直接复用已加载的同步结果，不再二次按 ID 查询。
- `zsxq_sync_service` 清除遗留的 `ZsxqSyncRun.query.get()`。
- 新增静态纪律测试，禁止轮询门禁和结果复用被回退。
- `zsxq_sync` worker 在 2026-07-10 11:12:18 以新代码重新 ready。

## 验证

- 前端 JavaScript 语法检查通过。
- ZSXQ 投影、轮询和重试专项 20 项测试通过。
- 查询计划确认命中组合索引，服务端执行时间 0.064 ms。
- 修复后 worker 已启动，但尚缺下一次真实同步和浏览器刷新后的运行样本，因此不标记为“已解决”。

## 复发预防

- 所有 `setInterval(async ...)` 业务轮询必须具备 single-flight 或改用完成后 `setTimeout`。
- 已持有 ORM 结果对象的调用链不得再次按相同主键加载大字段。
- 判断索引问题前必须同时记录服务端 `EXPLAIN ANALYZE` 与客户端端到端耗时。
- 性能状态遵守 [数据库性能修复验收纪律](../rules/database-performance-acceptance-discipline.md)。

## 后续事项

- 浏览器刷新加载新 JavaScript 后，观察下一轮完整 ZSXQ 同步。
- 统计修复后轮询请求是否仍出现时间重叠。
- 若远程链路仍有 10 秒级尖峰，继续减少 API/worker 数据库往返，并评估将应用运行节点靠近 PostgreSQL。
