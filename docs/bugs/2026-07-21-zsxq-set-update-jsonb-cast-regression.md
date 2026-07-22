# ZSXQ 集合更新缺少 JSONB 类型转换导致整轮失败

## 基本信息

- 状态：2026-07-22 已修复并通过新 worker 真实同步验收
- 严重级别：P0
- 影响模块：ZSXQ 同步 / PostgreSQL JSONB / SQLAlchemy Core / 真实负载验收
- 首次发现：2026-07-21 第 137 轮真实同步
- 修复提交：待提交
- 根因分类：数据库类型契约未覆盖、回归假绿
- 复发键：`REGRESSION-ACCEPTANCE-INTEGRITY`
- 是否复发：是；2026-07-22 同一 `UPDATE FROM VALUES` 在 `download_count integer` 上再次出现 `integer = text`
- 关联记录：`2026-07-21-zsxq-schema-array-text-misread.md`、`2026-07-18-regression-suite-false-green-and-live-side-effects.md`、`2026-07-21-zsxq-sync-point-query-amplification.md`
- 防线层级：local_patch / sql_contract_test / runtime_acceptance / observability

## 现象与影响

第 137 轮在首页 84 个附件刷新时立即失败，PostgreSQL 报错 `operator does not exist: jsonb = text`。工作流 `15ad9644-2f25-4aa2-a518-c53198aec337` 被正确收口为 `failed/failed`，没有伪报成功，但本轮同步、清洗和发布未完成。

## 根因

SQLAlchemy `VALUES` 表达式中的别名列没有在 PostgreSQL SQL 内带出类型声明。`raw_payload` 绑定值因此被推断为 `text`，而真实表列是 `jsonb`，`IS DISTINCT FROM` 需要相容的等值操作符。SQLite 兼容测试和只检查 `UPDATE FROM VALUES` 形状的编译测试都没有覆盖这个真实类型契约。

2026-07-22 复发证明首次修复范围过窄：代码只对已经报错的 `raw_payload` 做了 CAST，仍相信 `VALUES(column(..., Integer))` 会在 SQL 中保留整数类型。实际批次在 `download_count` 出现 NULL/混合绑定值时仍被 PostgreSQL 推断为 `text`，再次让 `IS DISTINCT FROM` 因 `integer = text` 整轮失败。现场 schema 已确认无漂移：`file_id varchar(128)`、`download_count integer`、`raw_payload jsonb`。

## 首次方案

- 在差异判断和写入表达式中对 `incoming_attachments.raw_payload` 显式执行 `CAST(... AS JSONB)`。
- 继续保留原始 JSON 完整写入，不以丢弃载荷换取性能。
- PostgreSQL 方言编译测试必须出现 `cast(incoming_attachments.raw_payload as jsonb)`。

## 2026-07-22 完整方案

- 不再按已发生故障的字段逐个打补丁。
- `incoming_attachments` 的 ID、varchar、integer、jsonb 等每一列，在比较和赋值前都显式 CAST 为目标表列的真实 SQLAlchemy 类型。
- PostgreSQL 编译契约同时断言 `id -> varchar(36)`、`file_id -> varchar(128)`、`download_count -> integer`、`raw_payload -> jsonb`。
- 真实同步验收必须覆盖附件 `download_count` 为空和非空的混合批次。

## 首次验证

- 定向回归：`123 passed`；完整已知问题回归：`729 passed, 5 skipped, 42 subtests passed`。
- 第 138 轮 workflow `1e34679b-6083-47f3-bfa3-df34284a4b68`、sync run `9a1e27db-d227-40cb-8fd2-8f581397d024` 成功完成。
- 真实负载完成 4 页、78 topics、508 attachments、2 个下载、0 个失败，耗时 `94.55s`。
- 新 worker 日志没有再次出现 `jsonb = text`；工作流继续完成清洗并提交发布。

## 2026-07-22 验收进度

- 已复现现场错误：`psycopg2.errors.UndefinedFunction: operator does not exist: integer = text`。
- 已确认失败发生在附件集合更新的 `download_count IS DISTINCT FROM incoming_attachments.download_count`。
- 定向回归通过：`52 passed, 5 warnings`；覆盖 PostgreSQL 编译类型契约、同步任务重试、失败收口、陈旧任务恢复和自动调度。
- 真实 PostgreSQL 回滚探针成功执行附件 `UPDATE FROM VALUES`，不再出现 `integer = text`。
- 仅重启 `zsxq_sync` 专用 worker；重启前确认没有进行中的 ZSXQ 同步，且未中断当时正在执行的两个研报视频任务。
- 按失败任务原参数重跑 `manual_all_tabs`：sync run `0db3445a-9b9a-4084-90c3-e69cd96b2e6e` 完成 17 个 Tab，抓取 323 topics，新增 91、已有 16、识别 160 attachments，错误为空。
- 真实 worker 日志中的附件集合更新多次成功，包括 6、3、44 等非零更新行；未再出现 PostgreSQL 类型比较异常。

## 复发预防

1. PostgreSQL 专用的集合 SQL 不得只在 SQLite 上做行为验证；必须编译并断言 varchar、integer、数组、JSONB、UUID 等所有参与比较和赋值的真实类型。
2. 性能修复的第一个真实批次属于发布门禁，失败必须重新打开 Bug，不能以单元测试通过代替。
3. 新一轮必须同时证明业务完整和 SQL 耗时改善。
