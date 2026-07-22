# 研报子任务创建后重复回读整条 JSON 任务

## 基本信息

- 状态：四个创建入口已统一修复、全量回归并部署到新 worker
- 严重级别：高
- 影响模块：基础文案 / 研报音频 / Gemini / 豆包 TTS / Publisher / publication_tasks
- 首次发现：2026-07-21 第 138 轮真实清洗任务
- 修复提交：待提交
- 根因分类：commit 后 ORM 过期刷新、复制逻辑遗漏、性能回归验收只覆盖单入口
- 复发键：`DB-SESSION-LIFECYCLE` / `HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；同类签名从 2026-07-10 至 2026-07-21 在 Gemini、豆包和 Publisher 创建入口反复出现
- 关联记录：`2026-07-10-report-audio-projection-and-parent-roundtrips.md`、`2026-07-16-zsxq-expired-orm-full-row-refresh.md`、`2026-07-21-stick-figure-asset-owner-query-amplification.md`
- 防线层级：shared_guardrail / sql_contract_test / runtime_acceptance / observability

## 现象与影响

- 第 138 轮基础文案任务创建 Gemini 子任务后，`_create_gemini_task` 又查询整条 `publication_tasks`，包含 `params/result`，耗时 `1004ms`。
- 历史日志中，研报音频的豆包子任务创建入口在 7 月 10 日至 21 日持续出现相同整行回读，单次最高 `8238ms`；Publisher 子任务创建也有同类记录。
- 这些查询不是业务读取，而是为了在入队后写入 `celery_task_id` 并构造返回值，属于可以完全消除的额外往返。

## 根因

1. 子任务 INSERT 后先 commit，ORM 对象过期；入队完成后又用 `_load_task(..., *TASK_CHILD_COLUMNS)` 回读含 JSON 的整条记录。
2. 同一模式复制在基础文案 Gemini、研报音频 Gemini、豆包 TTS、Publisher 四个入口，过去的修复没有按行为模式全局搜索。
3. 原有契约只限制轮询和历史列表，没有限制“创建 -> commit -> 入队 -> 回写 Celery ID”这条路径的 SQL 预算。

## 实际方案

- flush 后、第一次 commit 前构造脱离 Session 的任务快照，保留原有对象式访问契约。
- 入队后使用无前置 SELECT 的窄 `UPDATE` 只写 `celery_task_id/updated_at`。
- 四个入口一起迁移，不再逐个等待日志复发。
- 新增静态契约：创建函数中禁止 `_load_task(child_id, *TASK_CHILD_COLUMNS)`，统一调用无回读更新 helper。

## 验证

- 定向回归：`117 passed, 29 subtests passed`。
- 完整已知问题回归：`729 passed, 5 skipped, 42 subtests passed`。
- SQL/重启纪律：`81 passed, 3 subtests passed`；编译、TypeScript、迁移单头和 diff 检查通过。
- 新 worker 已加载代码；部署后的日志中四个创建入口未再出现整 JSON 回读。仍保留下一次真实新建子任务的运行态复验门禁。

## 复发预防

1. 发现 commit 后整行刷新时，必须按“业务行为 + SQL 形状”搜索全部同类入口，不能只修改当前堆栈。
2. 新建记录需要异步入队 ID 时，固定采用“commit 前快照 + 入队 + 无 SELECT 窄 UPDATE”。
3. 性能验收除轮询和列表外，必须覆盖子任务创建、重试和失败收口路径。
