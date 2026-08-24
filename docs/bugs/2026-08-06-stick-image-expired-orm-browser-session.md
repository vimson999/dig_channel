# 火柴人图片生成在浏览器等待期间持有数据库连接

## 基本信息

- 状态：新 worker 已加载，待真实图片任务验收
- 严重级别：P0
- 影响模块：火柴人视觉资产 / Gemini 浏览器图片生成 / publication_tasks / SQLAlchemy Session
- 首次发现：2026-08-06
- 修复提交：待提交
- 根因分类：commit 后过期 ORM 回读 / Session 跨外部 I/O / 高频进度写放大
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：是
- 关联记录：`2026-07-16-zsxq-expired-orm-full-row-refresh.md`、`2026-07-18-publication-task-progress-json-write-amplification.md`、`2026-07-24-db-session-crossed-broker-and-remote-io.md`、`2026-07-29-pdf-evidence-bulk-selection-held-db-transaction.md`
- 防线层级：local_patch / shared_guardrail / contract_test / observability

## 现象

2026-08-05 06:02:19 至 2026-08-06 06:02:19，火柴人图片进度更新
`_update_progress` 出现 387 条慢 SQL，P95 4.891 秒、最大 11.472 秒、累计
874.985 秒，是应用 SQL 累计耗时第一名。连接日志同时有大量没有匹配 executor 慢 SQL 的长持有。

## 影响

- 每个图片生成任务会把浏览器自动化的分钟级等待叠加到数据库连接占用上。
- 高频进度事件把公网数据库往返放大，挤压 Web、scheduler 和其他 Celery 任务可用连接。
- 进度 SQL 看起来很慢，容易被误诊为缺索引；真实问题发生在 SQL 之后的 Session 生命周期。

## 根因

图片生成分支先更新进度并 `commit()`，随后访问 `scheme_output.image_gemini_url`。
SQLAlchemy 默认在 commit 后过期 ORM 属性，这次访问触发一次新的 SELECT/checkout；代码接着进入
`GeminiBrowserWorker.generate_image()`，使同一个 Session 跨越浏览器外部 I/O。

PDF 证据分支也把 ORM claim/output 对象传过 commit 边界，在本地文件渲染阶段存在同类风险。
进度回调的 4/8/15/24/32/65/96 等事件又逐条 commit，形成额外写放大。

## 解决思路

1. commit 前冻结浏览器 URL 和证据渲染所需标量，commit 后禁止访问 ORM 实例。
2. 证据查询只投影渲染字段并返回不可变 DTO。
3. 每次进度写后主动结束 scoped Session。
4. 合并中间进度写，但必须保留阶段切换和最终进度。

## 实际方案

- 图片生成在 commit 前快照 `image_target_url`，浏览器 worker 只接收该标量。
- `evidence_visual_for_segment` 返回冻结的 output/claim render snapshot，不再把 ORM 带入 PDF 文件渲染。
- `_update_progress` 成功、异常两条路径都清理 Session；异常先 rollback。
- 进度持久化设置最小 20 点增量，95 以上强制落盘；典型七次回调收敛到约三次中间写并保留最终态。
- 连接生命周期日志新增 request/Celery task/origin、SQL 累计和 non-SQL 持有，后续可直接证明连接是否仍跨浏览器等待。

## 验证

- 新增 Session 边界合同测试，证明 commit 后只使用标量/DTO，且进度合并不丢 95+ 最终事件。
- 连接生命周期测试覆盖 SQL 与 non-SQL 持有拆分、request/task/origin 归因。
- 本轮相关定向回归 123 项通过；完整 unit 和新 worker 真实负载验收见巡检报告最终结果。

## 复发预防

- 任何外部浏览器、网络、文件渲染调用前，只允许标量、Row 或冻结 DTO，禁止 commit 后 ORM 访问。
- 长连接日志必须同时显示 SQL 与 non-SQL 时间；executor 很快而 non-SQL 很长时禁止建议索引。
- 高频进度生产者必须有写入预算测试，并强制终态落盘。

## 后续事项

- 安全重启后用一条真实图片任务验收 `checkout_origin` 与 `non_sql_hold_ms`，确认浏览器生成期间无数据库 checkout。
- 下一 24 小时窗口复核 `_update_progress` 次数和累计耗时是否按事件合并比例下降。
