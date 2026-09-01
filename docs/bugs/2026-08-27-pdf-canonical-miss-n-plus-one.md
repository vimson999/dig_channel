# PDF 目录把“已批量确认不存在”误当成“尚未查询”并逐项补查

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING；本地新 backend 已完成三个真实 API 验收，待下一完整 24 小时窗口确认指纹不复发
- 严重级别：P0
- 影响模块：研报音频 / 基础文案物料 / Top10 PDF 目录 / `content_pipeline_assets` / PostgreSQL
- 首次发现：2026-08-27 每日数据库性能巡检
- 修复提交：当前工作区
- 根因分类：批量查询缺失态丢失、逐项回退查询、远端数据库往返放大
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；属于热点列表逐项补查簇的新入口
- 关联记录：`2026-08-05-report-audio-hidden-tab-polling-n-plus-one.md`、`2026-08-10-report-audio-finalization-json-polling-n-plus-one.md`、`2026-07-21-stick-figure-asset-owner-query-amplification.md`
- 防线层级：shared_guardrail / query_budget / contract_test / runtime_observation

## 现象与影响

固定窗口 `2026-08-26 06:02:19` 至 `2026-08-27 06:02:19` 内，`GET /api/v1/report-audio/pdf-assets` 21 次累计 275.912 秒，P50 8.165 秒、P95 30.849 秒、最大 32.365 秒，是累计耗时最高的前台端点。最差请求 `request_id=fd6212e6` 返回 200 但耗时 30.849 秒，Session 持有 30.791 秒，其中 164 条 SQL 累计 30.224 秒；另一条第一页请求 `2944ee61` 有 127 条 SQL、耗时 16.617 秒。

同窗 `content_pipeline_assets` 按 source path 的单项 canonical 查询指纹出现 6 次、累计 30.635 秒、P95 16.019 秒。它与 PDF 列表 request id、SQL origin 和 Session 生命周期直接重合，用户看到的是页面长时间等待而非业务错误。

## 根因

三个 PDF 工作台都先用 `_canonical_pdf_assets_by_source_paths()` 批量取得 `source_path -> asset` 映射。若某个源 PDF 尚无 canonical asset，调用方会明确传入 `canonical_asset=None`。旧 payload helper 使用：

```python
asset = canonical_asset or _canonical_pdf_asset_by_source_path(...)
```

这把“批量查询已经完成且结果确实不存在”与“调用方没有做过查询”合并成同一个假值，随后对每个缺失项再发一次单项 SELECT。PDF 目录扫描缺失项越多，查询数线性增长；远端 PostgreSQL/PgBouncer 的单次往返长尾又把本地很轻的 executor 工作放大到 30 秒页面耗时。

只读 `EXPLAIN (ANALYZE, BUFFERS)` 进一步排除了索引根因：PDF 附件列表冷/热执行器分别为 44.529/24.430ms；canonical 批量查询冷/热为 0.409/0.317ms，`content_pipeline_assets` 仅 418 行。无需新增索引。

## 解决思路与实际方案

- 在三个 helper 中引入私有 sentinel，区分“参数未提供”和“已知缺失 `None`”。
- 只有参数未提供时才允许兼容旧调用方执行单项查询；批量调用方传入 `None` 后直接按不可用资产过滤，不再补查。
- 不改变 canonical 解析、清洗资产选择、字段完整性或原始 JSON 审计数据，也不新增生产 DDL。
- 新增真实 SQLAlchemy SELECT 计数契约：8 个均缺失 canonical 的附件仍必须保持常数查询预算；三个 helper 对已知缺失态不得执行 SELECT。

## 验证

- TDD 红灯：旧代码下目录用例实际 11 条 SELECT（预算不超过 4），三个 helper 合计额外执行 3 条 SELECT。
- 定向回归：`tests/unit/test_pdf_canonical_asset_consumers.py` 4 项通过。
- 重启门禁：`scripts/dev/check-restart-safety.py` 确认没有不可中断任务后，标准重启 backend/Celery/scheduler 成功；新 backend PID 为 49814，`/health` healthy，17 个 worker 服务全部 ready，active/reserved 均为 0。
- 真实 API：研报音频第一页 `request_id=73859938` 返回完整 10 项，1.533 秒、7 条查询且无生命周期告警；相较旧第一页 16.617 秒/127 条，耗时下降 90.8%，查询数下降 94.5%。
- 横向入口：基础文案物料 `request_id=7ed07ba6` 1.141 秒；Top10 `request_id=481426bf` 1.933 秒，均为 200 且返回完整 10 项。
- 完整 unit、compileall、diff check 与 Bug 严格门禁结果记录在当日巡检报告；运行态窗口尚不足 24 小时，不能标记 CLOSED。

## 复发预防与后续事项

- 批量映射 API 必须保留三态语义：未查询、查到对象、确认不存在；禁止用 `x or fallback_query()` 消灭缺失态。
- 热点列表契约同时约束结果完整性和每页查询次数 O(1)，缺失/过滤路径必须纳入预算。
- 下一轮优先检索三个 API 的 canonical 单项查询指纹，并按 request id 检查每页 SQL 数；完整窗口内无逐项补查、P95 回落且结果项完整后，才可 CLOSED。
- 若仍复发，继续搜索其他 `canonical_asset or lookup` 入口并升级为共享类型/结果对象，不能只修当日出现的函数。

## 2026-08-28 后续窗口复核

固定窗口 `2026-08-27 06:02:19` 至 `2026-08-28 06:02:19` 内，旧 canonical 单项补查指纹为 0。研报音频 PDF 列表仅有 3 个样本、累计 21.592 秒、P95/最大 18.223 秒，每个请求的 canonical 查询仍保持批量 O(1)；基础文案和 Top10 各只有 1 个真实样本，分别为 1.141/1.933 秒。`content_pipeline_assets` 仅 542 live rows，批量历史查询现场 executor 0.254ms，秒级尾延迟继续指向远端链路而非索引。

代码根因在本窗口未复发，但自然流量不足“同一场景至少 5 次”的运行验收门槛，且研报音频仍出现 18.223 秒网络尾延迟，因此保持 `RUNTIME_REPLACED / OBSERVING`，不得标记 CLOSED。下一轮继续检查旧指纹为 0、每页查询 O(1)、结果完整，并补足三个入口各至少 5 次真实样本。
