# 知识星球 Insights 冷缓存预热与 stale 刷新设计

## 1. 背景与问题

2026-09-07 数据库巡检在窗口后发起了一次真实 Insights 请求：

- `GET /api/v1/zsxq-insights/overview?group_id=1824528822&page=1&page_size=5`
  服务端耗时 3369.27ms。
- 其中 `_aggregate_themes` 驱动侧耗时 2043.92ms，但同形只读
  `EXPLAIN (ANALYZE, BUFFERS)` 约 34.953ms；其余三次读取分别约
  312.79ms、172.17ms 和 87.22ms。
- 相同请求随后 fresh cache hit 仅 0.97ms，强制刷新在进程内聚合快照已热时为
  592.16ms。

这些数据不支持新增索引或重写 SQL。主要问题是：一个此前未出现的外层响应缓存键会把完整聚合冷路径暴露给请求；
应用重启后进程内聚合缓存也会丢失。审计探针使用 `page_size=5`，真实页面使用 `page_size=48`，因此审计冷键不能被
当作前端缓存键错误，但同样证明了真实默认页面在全冷状态下存在首次加载尾延迟。

当前 stale 响应还带有 `Warning: 110 - "Response is stale; refreshing in background"`，实际却只有浏览器 JavaScript
会追加 `_refresh=1` 发起刷新。直接 API 客户端不会触发后台刷新，服务端声明与真实行为不一致。

## 2. 目标与验收边界

本期目标：

1. 后端启动后异步预热真实页面的默认首屏，使预热完成后的首次页面请求直接命中共享响应缓存。
2. stale 命中立即返回，由服务端安排一次后台刷新；多个进程或并发请求不得重复执行相同刷新。
3. 规范化语义等价的查询参数，避免参数顺序、显式默认值或 `_refresh` 产生不同缓存身份。
4. Redis、后台线程或刷新查询失败时保持页面可用，并保留现有同步冷 miss 和浏览器强制刷新作为降级路径。
5. 让日志和响应头准确说明缓存层级及刷新责任，不记录搜索文本、凭据或响应正文。

本期不是以下事项：

- 不增加、删除或修改数据库索引、表、物化视图或 SQL 查询。
- 不清空现有 Redis；缓存版本升级自然隔离旧数据。
- 不承诺进程启动后的第一个毫秒内已经预热完成。若请求早于预热线程完成，仍走现有同步冷 miss，以正确性优先。
- 不改变 Insights 业务筛选、排序、分页、统计或返回 JSON 结构。
- 不把缓存刷新投递到单并发的 `zsxq_sync` 业务队列，也不新增常驻服务。
- 不在本期改变同步完成后的缓存失效上限；fresh/stale TTL 继续沿用现有配置。

## 3. 已选方案及备选方案

### 3.1 已选：Web 后台刷新器 + Redis 单飞锁

应用复用 `_start_web_background_threads(app)` 进程角色门禁，并将它收紧为对 `app.py`、Flask `run` 和已知 WSGI server
命令的正向识别；迁移、维护脚本及未知进程均不预热。只有确认的 Web 进程才启动一个有界后台刷新入口。
每个进程最多执行一个 Insights 刷新线程；Redis 原子锁保证多个 Web 进程对同一缓存身份只有一个刷新者。

该方案不占用 ZSXQ 同步 worker，不新增队列和进程，能够同时处理启动预热与 stale 刷新，并让直接 API 客户端获得
真实的服务端 stale-while-revalidate 行为。

### 3.2 未选：Celery 刷新任务

现有 `zsxq_sync` worker 并发为 1，承担同步和浏览器导入。将缓存刷新放入该队列会造成队头阻塞；新增专用队列则需要
新的 worker、监控、重启和运行手册，超出本问题所需范围。

### 3.3 未选：SQL/DDL 优化或全量定时预热

同形 executor 约 35ms，缺少 DDL 证据。为所有主题、时间范围、分页和查询词定时预热既无法穷举，也会制造无效数据库
流量。本期只预热真实默认首屏，其他组合由正常请求和 stale 刷新按需填充。

## 4. 组件边界

### 4.1 参数规范化

`services/zsxq_insights_service.py` 提供一个公开、纯函数式的参数规范化入口，同时供 `build_insights` 和缓存层调用。
规范化结果固定包含：

- `group_id`：字符串，缺省为 `GROUP_ID_SHENGCAI`。
- `theme_id`：已知主题；未知值保持当前行为，回退到 `ai-sales`。
- `range_days`：整数并按现有限制截断，缺省为 90。
- `query`：去首尾空白并转为小写，缺省为空字符串。
- `page`：大于 0 且按现有上限截断的整数，缺省为 1。
- `page_size`：大于 0 且按现有上限截断的整数，API 缺省继续为 30。
- `sort`：`latest` 或 `signal`，缺省为 `latest`。

非法整数、非正分页和未知 sort 继续返回现有 400。缓存查找在规范化成功后进行，缓存和计算不得分别解释参数。

### 4.2 独立缓存刷新服务

新增 `services/zsxq_insights_cache.py`，职责限定为：

1. 根据规范化参数生成缓存身份。
2. 读取和写入进程内 fresh、Redis fresh 与 Redis stale 响应。
3. 请求后台刷新，并报告 `scheduled`、`in-flight` 或 `client-required` 状态。
4. 为真实默认首屏安排启动预热。
5. 在后台任务结束时无条件清理数据库 session、本地执行槽和 Redis 锁。

该模块不处理 HTTP、鉴权、页面状态或业务聚合规则。API 蓝图负责把缓存结果转换为响应；聚合服务仍独立负责业务数据。

### 4.3 API 与前端

`apis/v1/zsxq_insights.py` 只执行以下编排：规范化参数、读取缓存、按需安排刷新、同步处理真正的 miss、写入响应缓存。
现有 `X-Insights-Cache` 与 `X-Insights-Cache-Tier` 保留；stale 响应新增：

- `X-Insights-Refresh: scheduled`：本进程获得单飞资格并已启动后台线程。
- `X-Insights-Refresh: in-flight`：另一个线程或进程正在刷新。
- `X-Insights-Refresh: client-required`：Redis/刷新器不可用，浏览器必须使用现有 `_refresh=1` 降级。

只有 `scheduled` 或 `in-flight` 才能保留
`Warning: 110 - "Response is stale; refreshing in background"`。`client-required` 使用不承诺后台行为的
`Warning: 110 - "Response is stale"`，确保直接 API 客户端不会收到虚假声明。

开发环境由 `localhost:8888` 跨域访问 API，因此 CORS 必须暴露 `X-Insights-Cache`、`X-Insights-Cache-Tier` 和
`X-Insights-Refresh`；否则浏览器无法读取刷新状态并进入轮询。

前端收到 `scheduled` 或 `in-flight` 时不再立即发起 `_refresh=1`。它按 500ms、1000ms、2000ms、4000ms 的有界间隔
请求普通 URL；一旦得到非 stale 响应就更新当前页面并停止。四次后仍为 stale、请求失败或用户已切换筛选条件时，静默
停止并保留当前数据。收到 `client-required` 或服务端没有新头时，继续使用现有强制刷新兼容路径。

轮询得到的 stale 响应不得反复覆盖浏览器缓存的 `savedAt`，否则会人为延长本地 fresh 窗口。

## 5. 缓存身份与数据流

缓存版本从 `v4` 升为 `v5`。缓存身份使用规范化参数的固定字段映射、排序键 JSON 和 SHA-256；Redis 键和日志只包含
摘要，不包含 `query` 明文。`_refresh` 是控制参数，不进入缓存身份。

以下请求必须共享同一身份：

- 参数顺序不同但值相同。
- 缺省值与显式传入同一缺省值。
- 有无 `_refresh=1`。
- 查询词仅存在大小写或首尾空白差异。

主题、时间范围、查询词、页码、页大小或排序真正不同的请求必须隔离。

请求流程：

1. 规范化失败：直接返回 400，不接触缓存或数据库。
2. 进程内 fresh 命中：返回 `HIT/local`。
3. Redis fresh 命中：填充进程内缓存并返回 `HIT/redis`。
4. Redis stale 命中：立即返回 `STALE/redis-stale`，并尝试安排后台刷新。
5. 无缓存：同步调用 `build_insights`，保持现有成功/错误契约，成功后同时写入 fresh 与 stale。
6. `_refresh=1`：跳过读取，但使用同一规范化身份写回；仅作为 Redis 或服务端刷新不可用时的兼容降级。

启动预热参数固定为真实页面当前默认值：

```text
group_id=1824528822
theme_id=ai-sales
range_days=90
query=""
page=1
page_size=48
sort=latest
```

只有 `register_apis=True` 且 `_start_web_background_threads(app)` 允许启动后台工作的应用实例才安排预热。启动调用只负责安排
daemon 线程，不等待 Redis 或数据库，也不改变 `/health`。线程开始后先检查 fresh 缓存；已有 fresh 值则直接结束，避免
每次进程重启重复查询。

## 6. 并发和锁契约

后台刷新同时满足两个门禁：

1. 进程内非阻塞执行槽：每个 Web 进程最多一个刷新计算线程。获取执行槽与发布活动摘要在同一临界区完成，槽已占用时
   不排队、不创建无限线程；仅当活动摘要与请求摘要相同才返回 `in-flight`，其他摘要返回 `client-required`，避免宣称
   错误的键正在后台刷新。
2. Redis 单飞锁：按 v5 缓存摘要加锁，使用随机 token、`SET NX EX` 和有界 TTL。释放时使用 compare-and-delete，禁止
   旧线程删除后来持有者的锁。进程崩溃时由 TTL 自动恢复。

锁 TTL 按四段聚合查询的 statement-timeout 总预算再加 120 秒清理余量计算，默认及上限为 600 秒；刷新存活期间由一个
不访问数据库的 daemon lease keeper 每隔 TTL 的三分之一用 token-safe Lua 续期。后台写入再使用 token 校验与 fresh/stale
写入同一段 Lua 原子操作：租约丢失后旧线程不得写入本地或 Redis 缓存。后台线程不得跨查询持有 ORM 对象或数据库连接，
继续依赖 `build_insights` 内的 `_released_read_session()` 边界。无论成功、异常、线程或 lease keeper 构造失败还是线程启动
失败，均释放本地执行槽；获得 Redis 锁后还必须在 finally 中安全释放锁。显式 `db.session.remove()` 在 Flask app context
退出前执行。

## 7. 失败处理与可观测性

- Redis fresh/stale 读取失败：视为 miss，保持现有同步计算行为。
- stale 已成功读取且同键 Redis 锁已被持有：返回 stale 和 `in-flight`，不启动第二次刷新。
- stale 已成功读取但 Redis 锁操作抛出异常：返回 stale 和 `client-required`，不把缓存故障变成 5xx。
- 后台聚合失败：保留 stale，不写半成品，不改变当前请求，记录一次结构化 warning。
- 冷 miss 聚合失败：保持现有 502；参数问题保持 400。
- 后台线程结束：调用 `db.session.remove()`，即使业务服务已经释放各次读取也不依赖隐式清理。
- 浏览器轮询失败：只记录一次 console warning，不清空页面、不显示全屏错误。

结构化日志前缀为 `[ZSXQ_INSIGHTS_CACHE]`，包含 `event`、`reason`、缓存摘要、进程 ID、耗时和结果；不得包含查询文本、
响应 JSON、cookie、token 或 Redis URL。至少记录 `warmup_scheduled`、`refresh_started`、`refresh_completed`、
`refresh_failed`、`refresh_skipped`。

## 8. 测试设计

实现严格按 TDD：每组行为先加入失败测试，再写最小实现。

### 8.1 参数与缓存身份

- 参数顺序、显式缺省值、查询大小写/空白和 `_refresh` 共享身份。
- 真正不同的筛选、页码、页大小和排序隔离。
- 未知主题沿用 `ai-sales` 回退；非法分页和 sort 在缓存读取前返回 400。

### 8.2 缓存与后台刷新

- fresh local、fresh Redis、stale Redis 和 cold miss 分别走正确分支及响应头。
- stale 响应在刷新开始前即可构造，后台线程不阻塞请求结果。
- 相同键的并发请求只执行一次 build；另一请求得到 `in-flight`。
- 本地执行槽已满时不创建额外线程。
- Redis 不可用时得到 `client-required`，冷 miss 仍可同步成功。
- 刷新成功写入完全相同的缓存身份；刷新失败不覆盖 stale。
- 成功、异常和线程启动失败均释放锁、执行槽和数据库 session。
- compare-and-delete 不会删除 token 不匹配的锁。

### 8.3 启动和前端契约

- 仅 Web、非测试进程安排默认预热；worker、scheduler、watchdog、pytest/unittest 不启动线程。
- 应用创建和 `/health` 不等待预热完成。
- fresh 已存在时启动预热不调用 `build_insights`。
- 前端对 `scheduled/in-flight` 使用最多四次普通 URL 轮询，不追加 `_refresh=1`。
- `client-required` 保留 `_refresh=1` 降级；过期 requestId 不覆盖新筛选结果。
- stale 轮询结果不刷新浏览器本地缓存时间戳。

### 8.4 验证门禁

至少运行：

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_zsxq_insights_api \
  tests.unit.test_zsxq_insights_cache \
  tests.unit.test_runtime_initialization_idempotency

PYTHONPATH=src/backend venv/bin/python -m pytest -q \
  tests/unit/test_zsxq_insights_frontend_contract.py

PYTHONPATH=src/backend venv/bin/python -m unittest discover -s tests/unit -p 'test_*.py'
venv/bin/python -m compileall -q src/backend tests/unit
git diff --check
```

测试不得依赖真实 Redis、真实数据库、sleep 或后台竞态；用可控 fake client、同步线程 runner 和事件对象验证并发与清理。

## 9. 发布和验收

本任务只编码和测试，不自动重启当前运行时。代码验证通过后，如用户另行授权部署，必须先走现有 fail-closed 重启门禁。

运行时验收需要区分代码正确性与实际延迟：

1. 新进程启动日志出现一次默认键 `warmup_scheduled` 和一次终态事件。
2. 预热完成后，真实默认页面请求得到 `X-Insights-Cache: HIT`，且不执行完整四段冷聚合。
3. 人工缩短测试环境 fresh TTL 后，stale 请求快速返回，并且同一摘要只出现一次 `refresh_started`。
4. Redis 暂时不可用时，页面仍能通过同步 miss 或 `_refresh=1` 降级工作。
5. 不以单元测试或启动日志宣称网络复发簇关闭；仍需后续真实观察窗口对账驱动耗时、executor 和 PgBouncer。
