# 数据表页面先全量读取再用 Python 分页

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING；新 backend 已加载并通过真实 API 冷/热验收，待完整 24 小时后窗确认旧指纹不复发
- 严重级别：P1
- 影响模块：频道数据表 / `GET /api/v1/data/table` / `channel_data` / PgBouncer
- 首次发现：2026-08-31 每日数据库巡检后的真实页面流量
- 修复提交：当前工作区
- 根因分类：超量读取、应用层分页、O(n²) 行列组装、网络结果传输放大
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；8 月 17、23、26、28、29、31 日均有同端点秒级样本
- 关联记录：`2026-08-09-zsxq-insights-cold-fulltext-transfer.md`、`2026-08-27-pdf-canonical-miss-n-plus-one.md`
- 防线层级：query_shape / hydration_budget / contract_test / runtime_acceptance

## 现象与影响

真实页面 request `16574fad` 请求 2026-08-01 至 2026-08-31、`page=1&per_page=10`，最终只展示
10 个日期行；旧 service 却先把整月 1,515 个 `ChannelData` ORM 对象搬到应用。该 SQL 等待
2,126.32ms，端点 2,909.63ms。随后同形 request `332c606f` 再取 1,515 行，SQL 3,226.26ms、
端点 4,457.99ms。此前同端点还出现 1.615–11.154 秒样本，因此不是一次偶发页面抖动。

旧代码随后才在 API 层执行 `rows[start:end]`。service 组装每个“日期 × 频道”单元格时又对
`all_data` 执行一次线性 `next(...)`，把超量 ORM 物化进一步放大成 O(日期 × 频道 × 记录) 的
Python 扫描。远端 PostgreSQL 经 PgBouncer/Tailscale 返回时，超量传输又会放大既有网络长尾。

## 根因

分页边界位于 JSON 响应层而不是数据所有者层。SQL 只按日期范围和频道筛选，没有先选择当前日期页；
因此任意 `per_page` 都无法限制数据库返回量。查询本身并非缺索引：相同生产数据上，旧全量计划
`EXPLAIN (ANALYZE, BUFFERS)` planning/execution 为 0.200/2.008ms，返回 1,515 行、68 个 shared hit、
0 read/temp；网络/驱动物化耗时远大于 executor。盲目加索引不会减少返回行，也不会消除 O(n²) 组装。

## 解决思路与实际方案

- 把日期明确为表格的行维度：先用 distinct-date CTE 过滤日期，再用窗口计数得到 `total_days`，
  在 SQL 中应用 `LIMIT/OFFSET`，最后只 join 当前日期页对应的频道数据。
- 普通页面保持两条 SQL（频道列、当前日期页数据）；只有越界空页才补一次日期总数查询。
- 用 `(channel_id, date)` 字典替代每个单元格对 `all_data` 的线性搜索，将组装降为 O(返回记录数 +
  日期 × 频道)。
- API 在调用 service 前校验分页参数，并直接返回已分页 rows；不再对完整结果做 Python slice。
- `/data/export` 保留显式不分页合同，仍可导出完整日期范围；字段完整性与原始数据未删减。
- 没有执行或建议生产 DDL。

## 验证

- TDD 红灯：旧 `DataService.get_table_data` 不接受 SQL 分页参数；新增合同先失败。
- 定向绿灯 2/2：第二页准确返回 2 个日期、总数 5；3 个频道只物化 6 个 `ChannelData`，正常页
  恰为两条 SELECT 且包含 `LIMIT/OFFSET`；不分页导出仍返回完整 5 天。
- 只读生产 service 基准（owner 50 个 active channel、31 天、page 1/per_page 10）：两次总耗时
  562.144/443.851ms；频道 SQL 119.754/67.207ms，分页数据 SQL 431.400/310.790ms；只物化
  490 行并返回 10 个日期，总数仍为 31。
- 新计划 planning/execution 0.361/1.473ms，返回 490 行、80 个 shared hit、0 read/temp；相较旧计划
  返回行从 1,515 降至 490（-67.7%）。改善来自正确的查询形状和更少的传输/ORM/CPU，而不是索引。
- 最终完整 unit 为 `1,533 passed, 3 skipped, 305 subtests passed`；`compileall`、Bug strict 与
  `git diff --check` 均通过。
- 09:10:24 重启安全门读取 47 条任务记录、0 条活动 sync，确认无不可中断任务；随后未使用 FORCE
  执行标准整栈重启。新 backend PID 58871 healthy，bootstrap 17/17 Celery 服务成功、`errors=[]`。
- 新进程真实 request `a1c0d10e` 冷态 200/5,394.01ms：频道 SQL 1,111.45ms/50 行，分页 SQL
  3,209.08ms/490 行；旧 1,515 行 SQL 指纹已被替换。紧接着热态 request `35876fbb`
  200/1,020.31ms：频道 SQL 413.75ms、分页 SQL 518.29ms/490 行；API 返回 10 行、`total=31`、
  `pages=4`、50 个频道，字段与分页语义完整。
- 冷态仍超过 5 秒，且 executor 基准只有 1.473ms，故应用过量读取根因可记为运行替换，但网络往返
  长尾仍归 `DB-SESSION-LIFECYCLE` 的 OPEN/REPEATED 问题，不能把整个页面性能写成已解决。

## 复发预防与后续事项

- 表格/列表的分页必须由权威行维度在 SQL 层执行；禁止 `.all()` 全量物化后再用 Python 切片。
- 契约同时锁定响应总数、页语义、SQL 数量和 ORM hydration 数量，不能只断言 JSON 最终只有 10 行。
- 已用本机真实鉴权绕过路径重放同一 URL，SQL 返回 490 行、页面合同 10/31 天；in-app Browser 对
  loopback URL 返回 `ERR_BLOCKED_BY_CLIENT`，因此本轮没有把浏览器 DOM 观察冒充页面验收，UI 视觉
  仍列为未捕获边界。
- 后续完整 24 小时需统计该端点请求量、失败率、累计耗时、P50/P95/max、SQL 返回行和长 Session；
  在真实 API 加载新代码且后窗确认旧指纹未复发前保持 OBSERVING，不得标记已解决。
