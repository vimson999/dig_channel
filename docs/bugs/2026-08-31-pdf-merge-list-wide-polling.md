# PDF 合并任务列表宽读与终态轮询放大数据库网络长尾

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING；标准安全重启和真实 API/浏览器终态短窗已通过，待活动任务与完整 24 小时后窗
- 严重级别：P0
- 影响模块：PDF 合并工作台 / `GET /api/pdf-merge-workflows/jobs` / `publication_tasks` / PgBouncer
- 首次发现：2026-08-31 第三次安全重启后的自然页面流量
- 修复提交：当前工作区
- 根因分类：任务全实体宽读、分页双查询、终态无条件轮询、慢请求缺少 single-flight、Session 持有
- 复发键：`HOT-PATH-PAYLOAD-POLLING`、`DB-SESSION-LIFECYCLE`
- 是否复发：是；与历史终态轮询、隐藏页轮询及 `publication_tasks` 大 JSON 宽读属于同一根因簇
- 关联记录：`2026-07-22-terminal-polling-image-repair-payload-recurrence.md`、`2026-08-28-mp3-history-polling-overlap.md`、`2026-08-08-postgres-tunneled-route-query-timeout.md`
- 防线层级：lightweight_projection / one_query_pagination / session_release / active_only_polling / single_flight / runtime_observation

## 现象

自然 request `112cdfd9` 请求任务列表时返回 200，但端到端耗时 `18,024.02ms`。任务 SELECT 指纹
`358df92aaae36a2d` 耗时 `17,328.44ms` 且返回 0 行；连接租约持有 `17,644.18ms`，其中两条 SQL
累计 `17,602.43ms`，非 SQL 仅 `41.75ms`。后续同一指纹 request `c8a7d2f7`、`a3d9164e`
分别为 `4,401.99/2,863.31ms`，任务 SELECT 为 `2,922.75/2,623.85ms`，各只返回 2 行。

10:53:52 至 10:57:45 的短窗共捕获 27 次列表 GET，全部成功但累计 `42,235.27ms`，P50
`395.64ms`、P95 `4,727.94ms`、最大 `18,024.02ms`。页面在没有任务时仍以 12 秒周期刷新；活动任务
期间用户提交/刷新与周期请求又会叠加，旧代码没有在途请求所有权。

同一页面还在 10:54:52–10:56:42 产生 8 次 PDF 资产目录 GET，累计 `5,767.44ms`、P50
`451.60ms`、P95/最大 `2,072.59ms`；其中 6 次集中在 8 秒内，符合页面构造自动加载与输入 debounce
反复回源的放大模式，而不是 8 次明确的人工刷新意图。

## 影响

- 每次轮询都实例化完整 `PublicationTask`，跨网络读取 `params`、`result` 和大量与 PDF 合并无关的
  媒体投影字段；非空页还由 ORM `paginate()` 追加 count 查询。
- 空列表也会持续占用远端 PgBouncer/数据库往返预算；网络尾延迟出现时，单个后台页面即可持有连接
  17.6 秒，并提高其他 API 排队、超时和失败风暴风险。
- 页面隐藏时虽然旧回调跳过请求，但定时器仍存在；终态列表不停止，慢请求也没有 single-flight。
- 资产目录在构造时无条件加载，搜索每次输入变化都隐式回源；短窗 8 次请求又增加 5.767 秒 API 预算。

## 根因

底层根因与应用放大器必须分开：

1. 生产只读 `EXPLAIN (ANALYZE, BUFFERS)` 对新等价过滤形状仅执行 `0.201ms`、规划 `0.289ms`，
   返回 3 行、48 个 shared hit、0 read；应用驱动等待却为 `867.57ms`，连 `SET TRANSACTION READ ONLY`
   都曾等待 `1,669.09ms`。现有 `(feature, user_id, created_at DESC)` 索引已覆盖过滤与主排序，17.3 秒
   不是 PostgreSQL executor 或缺索引，而是 PgBouncer/网络往返长尾。
2. 应用用完整 ORM 实体服务列表摘要，造成大字段和无关列传输；`paginate()` 让正常页固定执行列表 +
   count 两次数据库往返，且响应构建前没有显式归还 Session。
3. 前端构造时立即启动永久 interval，只检查可见性，不判断是否仍有 pending/processing，也不复用
   在途 Promise。终态/空列表持续回源，慢请求可能与人工刷新、提交后的刷新形成并发放大。

## 解决思路

保持详情接口、原始 JSON 审计数据和页面字段合同不变，只收窄列表读取和刷新所有权：列表用单条窗口计数
查询返回必要标量及 `workflow.title/sources`、`result.output/logs` JSON 子树；读取后立即 release/remove
Session。前端只在存在活动任务且页面可见时运行共享轮询控制器，并让所有触发方复用同一个在途 Promise。

## 实际方案

- `list_jobs` 改为显式轻投影和 `count(id) OVER()`，正常页只执行一次任务查询；仅 `page > 1` 且越界
  无行时使用一次窄 count 恢复分页总数。
- 新增 `_job_list_payload`，保持 title、sources、source_count、output、logs、download URL、时间与状态字段
  完整兼容；source 路径继续脱敏，完整 `params/result` 和原始审计 JSON 留在数据库与详情合同中。
- 查询结果冻结后调用 `release_db_session_quietly()`，JSON 清洗与响应序列化不再持有数据库连接。
- 页面接入共享 `VisibilityPolling`：只在 `pending/processing` 时启动，隐藏或全部终态立即停止；构造、人工
  刷新、提交后刷新和周期 tick 统一经过 `jobsLoadPromise` single-flight。
- PDF 资产目录改为显式意图加载：不在页面构造时自动查询，也不在每次输入变化后 debounce 回源；用户
  输入关键词后点击“刷新资产”，空关键词仍可加载全部资产。资产选择与字段功能完整保留。
- 未执行 CREATE/DROP INDEX、ALTER、回填或生产配置修改；现有索引和 executor 证据不支持 DDL。

## 验证

- TDD 红灯：旧后端测试捕获 2 条任务 SELECT 且包含完整 `params/result` 与所有投影列；旧前端在空/终态
  列表后仍保留 1 个 timer。
- 修复后后端合同锁定：正常第一页恰好 1 条任务 SELECT，含窗口计数，不含整列 `params/result` 或
  `report_audio_*`；公开摘要、sources、output、logs 与 download URL 保持一致。
- 前端真实脚本 VM 测试锁定：可见构造只加载一次；空/终态 0 timer；活动态恰好 1 timer；并发刷新共享
  一次 HTTP；转终态立即清 timer。
- 页面静态合同锁定：构造阶段不自动加载资产目录，搜索输入不触发隐式请求，页面明确提示显式刷新和
  空搜索加载全部的行为，避免用隐藏回源冒充便利功能。
- PDF 合并 API、service、task、frontend 定向：`16 passed`；前端页面合同、轮询合同和共享轮询助手合同
  全部通过。
- 完整 unit：`1,535 passed, 3 skipped, 305 subtests passed`；compileall、前端合同、recurrence JSON、
  Bug strict 与 `git diff --check` 均通过。
- 生产只读 EXPLAIN：execution `0.201ms`、planning `0.289ms`、3 行、48 shared hit/0 read；进一步确认
  不应通过新增索引处理网络等待。
- 11:21 实时安全门及标准脚本冻结后的二次门禁都读取 47 条近期任务、0 条活动 sync，确认没有不可中断
  processing；未用 FORCE 完成标准重启。新 backend PID `82858` healthy，bootstrap 17/17、
  `errors=[]`；content-pipeline、MP3、ZSXQ publish 等目标 worker 均 ready。
- 新进程静态页面真实返回显式资产刷新说明，并按顺序加载 `visibility-polling.js` 与版本
  `20260831-003` 的控制器。
- 真实 API request `54fde6f1` 为 200/`459.39ms`，仅一条任务 SELECT `318.51ms`/3 行；公开字段、
  三个历史任务、sources、output、logs 与 download URL 完整。独立 Chromium 真实页面 request
  `7571d127` 为 200/`370.30ms`，唯一任务 SELECT `119.96ms`/3 行；26 秒内任务 API 恰好 1 次、资产
  API 0 次，三项任务均终态后没有第二个 12 秒 tick。新两次最大 459.39ms，较旧 18,024.02ms 最大
  下降 97.5%；旧 `358df92aaae36a2d`、第二条 count、长 Session 均为 0。
- 真实页面短窗只覆盖终态停止；未制造新的用户任务验证活动态周期刷新，因此保持 OBSERVING，不能 CLOSED。

## 复发预防

- 热点任务列表不得返回完整 ORM；必须以 SQL 数、投影列和 hydration 预算锁定。窗口计数优先消除常规
  page + count 往返，越界恢复只能是明确的窄查询。
- 状态轮询必须同时满足 Page Visibility、active-only、single-flight、终态停止；仅有“隐藏时 return”
  不算完整防线。
- 列表查询完成后必须先冻结 DTO 并归还 Session，再做 JSON 清洗和 HTTP 序列化。
- 下一窗口按旧 SQL 指纹 `358df92aaae36a2d`、新 SQL origin、request id 与轮询时间序列复核；测试通过
  和一次快速请求都不能替代完整运行窗。

## 后续事项

- 下一次真实活动任务期间确认同一页面最多一个请求在途，任务终态后立即停止；不得为了验收制造或重排
  用户生产任务。
- 完整 24 小时统计新列表端点请求量、累计/P50/P95/max、SQL 数与 Session hold；旧
  `358df92aaae36a2d`、全实体 payload、常规第二条 count 和资产输入隐式回源必须为 0。
- 基础网络仍为 `OPEN / REPEATED`：完整 24 小时内 PgBouncer timeout、连接超时、server closed、20 秒
  hold 和 5xx 均为 0，并在下一后续窗口再次确认前，不得将本问题标记 CLOSED。
