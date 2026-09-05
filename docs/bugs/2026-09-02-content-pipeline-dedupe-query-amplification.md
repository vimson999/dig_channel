# 内容管线批量去重逐文件查询与插入放大

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING
- 严重级别：P0
- 影响模块：内容管线 Web 提交 / PDF 与音频清洗 / `content_pipeline_assets` / PgBouncer
- 首次发现：2026-09-02 每日数据库性能与错误巡检
- 修复提交：当前工作区
- 根因分类：批量入口逐文件 N+1、远端数据库往返放大
- 复发键：`HOT-PATH-PAYLOAD-POLLING`、`DB-SESSION-LIFECYCLE`
- 是否复发：是；与历史附件逐项状态、PDF canonical miss 和批量任务逐项提交属于同一根因簇
- 关联记录：`2026-08-27-pdf-canonical-miss-n-plus-one.md`、`2026-08-06-zsxq-attachment-state-roundtrip-amplification.md`、`2026-09-01-script-video-image-submit-query-amplification.md`
- 防线层级：shared_guardrail / contract_test / observability / runtime_replaced

## 现象

固定窗口 `2026-09-01 06:00:00` 至 `2026-09-02 06:00:00` 中，request `b6534a0b` 的
`POST /api/v1/content-pipeline/web-submit` 提交 20 个互不重复的 MP3，返回 202 但耗时
`39,474.32ms`。它在一次连接租约中执行 82 条 SQL，SQL 累计 `3,881.15ms`，连接持有
`5,809.12ms`；相邻 PDF 任务提交 4 个唯一文件。只读对账确认两个任务均已完成且没有内容重复，排除了“重复文件
触发额外判断”的解释。

逐语句对账显示每个新 hash 都先执行一次 `SELECT ... FOR UPDATE`，随后各自建立 savepoint、插入并释放
savepoint。20 个文件因此把正常 admission 放大为约 20 次存在性查询、20 次插入和 40 次事务控制语句。

## 影响

- 批次文件数与远端数据库往返数线性增长；即使 PostgreSQL executor 很快，也会被 PgBouncer/网络长尾放大。
- Web 提交已经 durable 并返回 202，但用户要等待近 40 秒，容易误以为提交失败并重复操作。
- 同一实现同时服务 PDF 和音频入口，任何更大批次都会复用同一放大路径。

## 根因

`reserve_files()` 在输入循环内对每个 SHA256 单独查询 canonical row；未命中后，
`_insert_or_load_canonical()` 再为该文件建立一个 nested transaction 并立即 flush。数据库唯一约束虽然保证了并发
正确性，但正常无竞争路径完全没有利用调用方已知的整批 hash 集合。

这是查询形状问题，不是索引缺失。`content_pipeline_assets(media_type, content_sha256)` 已有唯一约束；本轮数据库
审查没有精确重复索引、等待锁或 idle-in-transaction，也没有新增 DDL 的证据。

## 解决思路

先在内存按 hash 保序去重，再一次性锁定所有已存在 canonical rows；对缺失项一次 `add_all + flush`。唯一约束仍
作为最终并发保护：只有真实竞态导致批量 savepoint 回滚时，才进入既有逐项冲突解析路径。可回收失败状态继续在
同一事务内更新，不为每个文件单独 flush。

## 实际方案

- `reserve_files()` 先构建 `first_by_hash`，一次 `IN (...) FOR UPDATE` 加载整批 canonical rows。
- 缺失项预分配 UUID 与时间戳，使用一次 `add_all + flush` 批量插入；正常路径数据库执行数不随文件数增长。
- 并发冲突继续由 `(media_type, content_sha256)` 唯一约束裁决，批量 savepoint 失败后使用旧的单行
  insert-or-load 兜底，不改变 canonical ownership。
- stale/failed/cancelled/released 的可回收语义、同批重复顺序和跨入口重复返回结构保持不变。
- 未执行 CREATE/DROP INDEX、VACUUM FULL、REINDEX 或生产数据回填。

## 验证

- TDD 红灯：8 个唯一文件在旧实现中捕获到 8 次 `content_pipeline_assets` SELECT，断言要求 1 次并明确失败。
- 修复后同一场景严格为 1 次 canonical SELECT 与 1 次批量 INSERT 执行；同批重复、跨入口重复、混合批次和
  terminal cleaned 不降级共 5 项通过。
- 内容管线 admission、Web API、canonical asset、checkpoint、通知、部分成功、进度和失败保护交叉套件
  `36/36` 通过；加入 workflow notification/outbox/retry 交叉后最终 `76/76` 通过，目标文件 compileall 与
  `git diff --check` 通过。
- 真实重启安全门两次读取 47 条近期任务、0 个活动 sync、0 个不可中断任务；未使用 FORCE。标准整栈重启后
  backend PID 78814 healthy，bootstrap 17/17、`errors=[]`，content_pipeline worker 于 06:13:36 ready。
- 运行切点后尚未出现自然批量提交，因此只能标记 `RUNTIME_REPLACED / OBSERVING`；下一条自然 PDF/音频批次和
  后续完整窗口前不得 CLOSED。

## 复发预防

2026-09-04 提交前补充：批量 canonical 锁定和缺失项插入均按 SHA256 稳定排序，返回 unique/duplicate
仍按输入顺序；新增真实唯一约束冲突的 savepoint 回退、外层任务审计保持及 failed canonical 回收测试。
这是隔离 SQLite 的约束/事务回归，不是 PostgreSQL 并发压测或生产流量证明。

- 批量 canonical admission 必须有与文件数无关的存在性 SELECT 和 INSERT execution 预算。
- 同能力的 PDF、音频及后续媒体类型必须复用共享 `reserve_fingerprints()`，不得各自复制逐项 existence check。
- executor 小于 10ms 而应用请求超过 500ms 时，先检查批量往返、连接持有和网络，不得用新索引掩盖 N+1。
- 运行验收必须关联 request id、received/unique/duplicate 数、SQL count、connection hold 和最终任务状态。

## 2026-09-03 自然流量验收

- request `dc99bae7` 的 12 音频提交确认 canonical admission 为 1 次 SELECT 与 1 次多行 INSERT，上一轮正常路径
  查询预算获得运行态证据。
- 同一请求暴露了下一层 Session 边界：任务 INSERT 后仍在事务内计算文件 SHA256，造成约 6.1 秒非 SQL 持有。
  该问题已独立登记为 `2026-09-03-content-pipeline-hash-inside-transaction.md`，不得把查询数验收误写成整条请求 CLOSED。

## 后续事项

- 捕获 PID 78814 后第一条自然多文件 Web 提交，要求 canonical SELECT=1、INSERT execution=1、无长连接持有，
  并对账所有文件的 canonical task ownership。
- 下一固定窗口继续比较接口成功率/P50/P95/max、慢 SQL、PgBouncer 六类错误和 idle-in-transaction；短窗或
  单次测试不能替代运行窗口。

## 2026-09-05 运行版本边界

固定窗 5 次 Web submit 均返回成功，但 P50/max 为 10.201/37.742 秒；最慢 request `285c2245`
约 13 条 SQL 累计 8.925 秒，约 28.816 秒在 SQL 外。该样本来自 06:10 旧进程，早于 18:49 的
`dec217e` 稳定排序、真实唯一约束 savepoint 回退与外层审计补强，不能作为最终代码验收。

完整 unit 1607 项与定向交叉 114 项通过；两次安全门通过，但标准重启被自动审批拒绝。状态保持
`OBSERVING`，最终补强为 `CODE_VERIFIED / WAITING_RUNTIME_REPLACEMENT`。重启后首个自然多文件批次仍需
验证 canonical SELECT/INSERT O(1)、输入保序、冲突回退、连接持有与最终 ownership。
