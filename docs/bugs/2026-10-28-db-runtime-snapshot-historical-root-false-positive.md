# 2026-10-28 durable root 快照把历史去重引用误判为旧 actor

## 基本信息

- 状态：RUNTIME_ACCEPTED（诊断器已生产只读重放，业务拓扑根因仍 OPEN）
- 严重级别：中
- 影响模块：每日 DB 巡检 / `daily_db_runtime_snapshot.py` / 分布式运行体归因
- 首次发现：2026-10-28
- 修复提交：待提交
- 根因分类：运行态诊断分类互斥性缺失
- 复发键：`REGRESSION-ACCEPTANCE-INTEGRITY` / `RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：同类验收假绿/假红再次出现
- 关联记录：`2026-10-17-db-runtime-snapshot-timezone-window.md`、`2026-09-27-remote-old-runtime-production-db-sessions.md`
- 防线层级：diagnostic_guardrail / contract_test / observability

## 现象

固定窗唯一 `content_audio_clean` 任务同时被统计为 `old_root=1` 与 `current_root=1`。逐字段只读对账后发现，
任务的实际输入、输出、worker 日志和当前路径都属于当前 checkout；旧路径只存在于原始审计 JSON 的
`result.dedupe.duplicates[].duplicate_of`，表示过去某个重复文件的来源位置，不表示本任务由旧 actor 执行。

## 影响

旧口径会把一个历史引用误报成旧运行体自然复发，导致分布式隔离状态、复发次数和责任动作失真。反向地，
如果简单忽略所有混合路径，又可能漏掉真正由旧 actor 写入但引用了当前路径的任务，因此不能用删除 JSON 或
“优先 current”掩盖。

## 根因

`summarize_task_roots()` 对 `old_root` 与 `current_root` 分别独立累加，没有把两者同时命中的行建模为第三种
状态。诊断 SQL 为了保留审计证据会扫描 `params/result` 文本，因此历史 dedupe 引用天然可能与实际当前路径
共存；布尔计数缺少互斥分类合同。

## 解决思路

保留原始 JSON，不改变业务数据，只修正诊断语义：纯旧、纯当前、混合引用和未分类必须互斥。混合引用单独列
task id 供人工沿结构化 actor、输入输出和日志追溯，既不自动算旧 actor，也不自动算当前 actor。

## 实际方案

- `summarize_task_roots()` 新增 `mixed_root_reference`，并令四类计数互斥。
- “old-root rows”只输出纯旧行；新增“mixed-root reference rows”逐行输出混合引用。
- 增加合同测试，固定 `old_root=True/current_root=True` 必须得到 `old=0/current=0/mixed=1`。
- 在 Session 生命周期纪律中补充混合路径的诊断与验收规则。

## 验证

- TDD 红灯：旧实现返回 `old_root=1/current_root=1` 且没有 mixed 字段。
- 定向测试：`tests/unit/test_daily_db_runtime_snapshot.py` 7/7 通过。
- 生产只读重放：`content_audio_clean` 变为 `old=0/current=0/mixed=1`；纯旧明细为空，任务
  `b29893ec-ba1f-436c-834e-701e42c7bf54` 被保留在混合明细中。
- 实际 dedupe 审计 JSON 未删除或改写；业务输入输出和完成状态未变。

## 复发预防

- durable root 四类统计必须互斥，并满足 `total = old + current + mixed + unclassified`。
- 混合引用必须继续核对结构化 actor、实际输入输出、worker 日志与时间线，不能直接作为 actor 身份。
- 测试通过只证明诊断口径；远端生产会话和共享凭据隔离仍按独立根因验收。

## 后续事项

下一轮继续列出所有 mixed 行及其 JSON 路径来源；若同一业务字段反复混入历史根路径，再把 root 识别从全文
扫描升级为结构化 producer/consumer identity，但不得删除审计数据。
