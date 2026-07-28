# ZSXQ 发布收尾逐条回写附件状态

## 基本信息

- 状态：代码与查询预算测试已通过，待新 worker 和真实发布验收
- 严重级别：P1
- 影响模块：ZSXQ 发布收尾 / zsxq_attachments / PostgreSQL
- 首次发现：2026-07-22
- 修复提交：当前工作区
- 根因分类：远端数据库 N+1、批次边界丢失
- 复发键：`DB-SESSION-LIFECYCLE` / `HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；此前同步入口已做页面级批量查询，但发布终态仍按 source_ref 逐条点查
- 关联记录：`2026-07-21-zsxq-sync-point-query-amplification.md`、`2026-07-22-zsxq-topic-unchanged-payload-rewrite.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

- 一次包含 9 个附件的发布完成事件连续产生 9 条 `SELECT zsxq_attachments` 和 9 条 `UPDATE zsxq_attachments`。
- 单条通常约 29-31ms，但公网数据库往返被线性放大，其中一条达到 172ms。

## 影响

- 批次附件数越多，发布收尾延迟和连接占用越高。
- 远端连接发生抖动时，任意一次点查都可能越过慢查询阈值并拖延工作流终态通知。

## 根因

1. 发布上下文已经携带完整 `source_refs`，消费端却在循环里逐条查询。
2. 附件 ID、业务复合键、名称和路径的兼容匹配散落在单条查询函数内，没有批次候选索引。
3. 既有测试只验证不回读 publication_tasks 大 JSON，没有约束附件查询次数。

## 解决思路

- 保留附件 ID 优先、业务键回退、名称和路径精确匹配、状态不可倒退等全部语义。
- 一次加载整批候选轻量列，在内存建立 ID 与 topic 复合键索引，再逐条应用原状态规则。

## 实际方案

- `_attachment_records_for_source_refs` 将最多 100 个 source_ref 合并为一条候选查询。
- 查询显式排除 `raw_payload`；ID 命中优先，否则按 group/tab/topic/name/path 复用原回退规则。
- 同一事务修改 ORM 对象，由 SQLAlchemy 以 executemany 批量 flush 后一次提交。

## 验证

- 9 个附件契约测试固定为 1 条 `SELECT zsxq_attachments`、1 条批量 `UPDATE zsxq_attachments`。
- 测试同时断言 9 条全部匹配和更新，且 SELECT 不包含 `raw_payload`。
- `tests/unit/test_zsxq_sync_query_discipline.py`：24 passed。
- 全量确定性回归：`793 passed, 5 skipped, 42 subtests`；数据库纪律门禁：`87 passed, 3 subtests`。

## 复发预防

- 一个事件已携带集合身份时，消费者不得退化为逐项远端查询。
- 批次状态回写测试必须同时约束业务结果、SELECT 次数、UPDATE 次数和大字段列集合。
- 新增 source_ref 回退规则必须扩展批次索引，不得恢复单条查询 helper。

## 后续事项

- 部署新 ZSXQ publish worker 后，使用下一次多附件发布验收实际 SQL 数量。
