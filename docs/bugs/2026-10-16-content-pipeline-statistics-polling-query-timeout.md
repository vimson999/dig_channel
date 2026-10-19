# 内容管线统计被活跃任务轮询放大并触发查询超时

## 基本信息

- 日期：2026-10-16
- 状态：RUNTIME_REPLACED / OBSERVING（未关闭）
- 严重级别：P0
- 影响模块：内容管线投递页、发布统计 API、`publication_tasks`、PgBouncer
- 根因分类：大 JSON 初始投影缺失 + 重统计与状态轮询耦合 + executor 外网络/驱动尾延迟
- 复发键：`HOT-PATH-PAYLOAD-POLLING` / `DB-SESSION-LIFECYCLE`
- 影响：45 次统计请求中 6 次 500；成功请求 P95 仍为 20.628 秒，页面最长 20.674 秒

## 修复前证据

固定窗 `2026-10-15 06:00:00`–`2026-10-16 06:00:00` 内，旧运行时的统计 SQL 直接读取
`PublicationTask.result`。6 个真实请求均在 20.06–20.33 秒后被 PgBouncer `query_timeout`
关闭，SQL 指纹为 `749030375197552e`。同窗早期另有 2 次 404，属于路由尚未加载的运行替换间隙，
不能与查询失败混为一类。

随后加载的 JSONB 投影把同一批 16 行从约 684,588 字节降至约 74,414 字节，消除了 500，
但投影指纹 `a7bfc8355f1ebf38` 仍出现 19 次、累计 131.459 秒，P50/P95/max 为
6.596/9.216/9.383 秒。14:54–15:04 期间，只因页面存在活动投递，前端每约 10 秒同时刷新历史和
统计；统计并不是任务状态，却被状态轮询放大。

生产只读 `EXPLAIN (ANALYZE, BUFFERS)` 显示该投影执行时间 6.600ms、planning 0.794ms、
`shared hit=1429`、无物理读，并命中既有 `ix_publication_tasks_web_submission_history` 与
`ix_publication_tasks_feature_source_created_desc`。因此本问题不是 PostgreSQL executor、锁等待或缺索引；
长尾发生在 PgBouncer/私网/驱动 fetch 边界，重复轮询把单次尾延迟变成累计负载和长 Session 租约。

## 已落地修复

- 活跃任务的 10 秒轮询只刷新轻量历史，不再每轮重复请求发布统计。
- 历史状态从活动态转为终态时仅刷新一次统计，使完成结果仍及时可见。
- 手工刷新、日期/周期/投行筛选和成功提交后的统计刷新保持不变，没有删减字段、原始 JSON 或审计数据。

## TDD 与运行态证据

行为回归先启动真实前端脚本并模拟活动历史：修复前第二个 tick 多出统计请求而红灯；修复后证明：

1. 活动态 tick 只请求历史；
2. 转终态只额外刷新一次统计；
3. 终态下一 tick 不再请求历史或统计。

定向合同 `tests/frontend/content_pipeline_submission_stats_contract.js` 已通过。当前 backend 直接提供修改后的
静态 JS（HTTP 200、33,095 bytes，内容包含新终态刷新合同）；统计 API 真实请求 HTTP 200、0.586 秒。
无需重启 backend/Celery/scheduler，因此没有调用重启安全门。

完整验证另通过 1,736 项 Python unit、103 项前端 Node 合同、compileall、210/210 Bug strict、registry JSON
和 `git diff --check`。沙箱内首轮全量 unit 因 Redis socket 权限限制中断，受信环境同命令全绿。

## 为什么仍未闭环

运行态只证明新浏览器会取得修复后的静态脚本，尚没有完整后窗证明统计指纹数量、累计耗时和 500 归零；
网络尾延迟簇本身也仍未定位到同秒 PgBouncer/socket/route 断点。故本项只能是
`RUNTIME_REPLACED / OBSERVING`，不能写成已解决。

## 下一轮验收条件

1. 完整窗口统计 API 500、`749030375197552e` 和 PgBouncer query timeout 均为 0；
2. 活跃任务期间统计请求数只来自首屏、手工筛选/刷新、成功提交或终态转换，不再按 10 秒线性增长；
3. 统计投影 SQL 的累计耗时、P95 与 Session hold 显著下降，并继续提供 executor 对照；
4. 若仍出现应用 SQL 超过 500ms 而 executor 小于 10ms，保存同秒 pool/socket/route/activity，不加无证据索引；
5. 至少一个后续完整窗口无相同复发键，方可升级为 CLOSED/RUNTIME_ACCEPTED。

## 2026-10-17 完整后窗验收

固定 24 小时内统计 API 14 次请求全部 200，P50/P95/max 为 401.79/579.45/663.98ms；上一窗的
`749030375197552e` 全 JSON 指纹、投影慢指纹、6 个 query timeout 和 500 均为 0。同期轻量 history
请求 145 次、web submit 8 次，而统计仅 14 次，证明统计量没有继续按活动任务的 10 秒 tick 线性放大。

该专项满足“代码修复、定向合同、完整回归、真实静态/API、后续完整窗口无复发”的关闭条件，状态升级为
**CLOSED / RUNTIME_ACCEPTED**。executor 外的 audio catalog/私网尾延迟属于独立根因，仍保持 OPEN，不能
用本项关闭掩盖。
