# 终态轮询与图片孤儿修复再次放大任务表载荷

## 现象

- `2026-07-22 00:00` 至修复前，`publication_tasks` 审计共记录 332 条事件、56 个 SQL 指纹。
- 研报音频历史列表 64 次慢查询，平均 `1918ms`、P95 `7342ms`、最大 `16488ms`；已完成历史日期仍被定时刷新。
- 基础文案尝试记录和任务快照再次读取完整 `params/result`，最高 `18980ms`；尝试记录单次 `17145ms`。
- 火柴人图片任务批量入队后，worker 每个任务读取 `params`，进度回调同时更新任务表和场景资产表。
- watchdog 将已经被 worker 预取、但仍在等待浏览器资源的图片任务误判为消息消失，每约 25 秒读取一次完整 `result`；最高 `29614ms`。
- 同一压力窗口，场景素材接口两次在约 30 秒后收到数据库连接关闭并返回 500。
- ZSXQ 同步旧 worker 使用未转换类型的集合更新，触发 `integer = text` 的 PostgreSQL `ProgrammingError`。
- 首次运行态修复后又发现两个遗漏入口：旧火柴人详情页每 15 秒读取 45 行动态状态；ZSXQ 调度器启动检查只使用 3 个字段却完整读取 `requested_tabs/result`，单次达到 `23311ms`。
- watchdog 每 75 秒为了展示附加业务提示读取 ZSXQ 活动任务；服务器执行计划仅 `0.029ms`，客户端仍偶发约 `1s`，说明该处是远端链路抖动被无必要轮询放大。
- TTS 批量提交逐镜头 flush/commit，worker 又把 9 个浏览器进度事件全部写入 `publication_tasks`；提交后的状态接口每轮反向读取任务 JSON 修补场景状态。
- 服务重启后发现一条 TTS 任务长期停在 `pending / auto_retry`：Celery 的 ETA 重试消息已不可见，但 watchdog 的 pending 状态白名单遗漏 `auto_retry`，因此不会自动恢复。
- 新进程真实工作流又暴露第五个同族入口：清洗产物创建发布子任务后先 `commit`，随后访问已过期 ORM 对象的 `id`，触发一次完整 `publication_tasks.params/result` 主键回读，耗时 `3905ms`。
- `14:06` 重启后的尾部审计又捕获四个入口：通用 SQLAlchemy Inspector 目录查询 `1312ms`、单条附件窄更新 `1245ms`、同步完成后访问已过期 `ZsxqSyncRun` 触发含 `result` 的整行刷新 `1105ms`，以及发布完成时为恢复附件映射读取清洗任务 `params/result` `984ms`。

## 影响

- 已完成页面继续制造远端数据库请求，空闲页面也会积累压力。
- 图片生成突发时，同一业务进度被两张表重复写，watchdog 又反向加载大字段并可能重复投递任务。
- 数据库压力被放大后，正常的结构化小表查询也可能等待连接或收到服务端断连，页面表现为“数据库操作失败”。

## 根因

1. 前端轮询只按定时器运行，没有把历史日期和全终态作为停止条件。
2. 尝试历史沿用 Python 端完整 JSON 读改写，没有使用数据库端原子 JSONB patch。
3. 图片任务的场景资产关系和 Celery ID 没有在首次提交前一次性持久化，worker 只能回读任务 JSON；每个浏览器进度事件又重复写 `publication_tasks`。
4. 通用孤儿修复器使用统一的 3 分钟宽限期，没有考虑浏览器图片任务会被预取后长时间等待独占资源；二次确认还加载不需要的 `params`。
5. 终态缓存、结构化关系和宽限期规则没有覆盖新工作流，因此旧问题以新入口再次出现。
6. SQL 优化验收只看了语句形状，没有同时区分 PostgreSQL executor 时间和应用端往返时间；即使索引正确，无业务价值的高频轮询仍会把网络抖动写成慢 SQL。
7. TTS 仍以通用任务 JSON 作为状态页事实源，提交和进度写入次数随镜头数增长；重试状态机与 watchdog 可恢复状态集合又分别维护，新增 `auto_retry` 时没有同步契约测试。
8. 清洗产物发布入口没有纳入既有的“子任务创建后禁止访问过期 ORM”门禁；broker ID 在 commit 后才生成，迫使代码再次触碰 ORM 实例，说明此前只修了已知调用点，没有覆盖所有任务生产者。
9. 清洗输出与原附件之间的映射虽然在清洗进程内已经完整存在，却没有在入库端作为工作流事件传递；发布完成端只能重新读取通用任务 JSON 反向重建关系。这是“只修出库查询、没有修入库契约”导致的同类复发。
10. ZSXQ 同步在多次 commit 后仍继续把 SQLAlchemy ORM run 当跨阶段上下文使用；远端 schema readiness 又沿用通用 Inspector 的多目录往返，分别放大了过期对象刷新和网络延迟。

## 修复

- 研报音频历史日期在全终态后停止自动刷新；基础文案历史页只在存在活动任务时轮询。
- 基础文案尝试历史在 PostgreSQL 使用单条 `jsonb_set` 原子更新，不再先读取整条任务。
- 基础文案和火柴人终态详情增加用户作用域的内存加 Redis 缓存；原始 JSON 查看仍显式读取数据库，业务字段不裁剪。
- 图片任务在首次 commit 前写入场景资产关系和预生成 Celery ID；worker 通过 `image_task_id` 查结构化资产，不读取 `params`。
- 图片浏览器的中间进度只更新场景资产表，`publication_tasks` 只保留开始和终态写入。
- 图片 pending 孤儿修复宽限期提升到 30 分钟，扫描投影移除未使用的 `params`，防止合法预取任务被高频深读和重复投递。
- 内容保留清理先按结构化 `feature/status/time` 读取候选 ID，再按主键逐条读取少量在途任务载荷，移除批量 JSON 条件与载荷查询。
- ZSXQ 集合更新对 VALUES 别名中的全部字段按目标表类型转换，覆盖 integer、varchar、UUID 和 JSONB。
- 火柴人场景状态改为自适应快照：活动状态最多缓存 20 秒，静止状态缓存 5 分钟；提交、重试和资产写入路径立即失效缓存。旧页面兼容接口在首次完整加载后只合并状态快照，不再反复加载场景结构。
- ZSXQ 系统调度器的 pending run 检查改为 `id/status/updated_at` 三列投影，删除 `requested_tabs/result` 隐式整行读取。
- watchdog 的恢复判定只使用进程、队列和 Celery inspect；移除仅用于 UI 提示、不会改变重启决策的业务任务快照读取。监控页面仍保留按需业务详情。
- TTS 批量提交改为一次 flush、一次场景投影更新和一次 commit；broker 返回 ID 或失败信息再做一次有界窄回写，不再逐镜头提交。
- TTS worker 将数据库进度写入压缩为 5 个业务里程碑，完整浏览器日志仍保留在终态结果中；同一事务同步更新场景资产的 TTS 状态、进度、错误和音频字段。
- 场景状态轮询直接读取场景资产投影，不再每轮查询 `publication_tasks.params/result` 后做兼容修补；历史完整详情路径仍保留一次性兼容对账。
- watchdog 发布修复消息后使用 `id + status + celery_task_id + updated_at` CAS 更新，不再二次读取并覆盖任务结果；CAS 失败立即撤销刚发布的消息。
- `doubao_browser_tts` 的 `pending / auto_retry` 纳入受控孤儿修复：只有超过宽限期且 worker、scheduled/reserved 和 Redis 均不可见时才重排。
- ZSXQ 历史回填按页建立 topic/attachment 快照，删除逐 topic 的 `raw_payload` 点查；系统同步待办检查只读取 `id/status/updated_at`。
- 清洗产物发布在 commit 前预生成并持久化 broker task ID，同时保存响应快照和 `updated_at` 版本；commit 后只传字符串和快照，发布成功或失败均使用窄字段条件更新，禁止刷新 ORM 大字段。
- PDF/音频清洗在源文件与输出文件同时位于内存时生成有界 `source_refs`，通过清洗完成事件写入 workflow summary，再随发布任务 metadata 传递；新任务更新附件发布状态时直接使用该结构化映射，不再读取清洗任务 `params/result`。历史任务继续保留一次性兼容回退，业务数据不裁剪。
- ZSXQ 同步在第一次 commit 前冻结 `run id/group id/group name` 不可变身份快照，后续自动视频和文案派发只使用快照，禁止跨 commit 访问 `ZsxqSyncRun` ORM。
- schema readiness 改为一条 PostgreSQL `information_schema.columns` 紧凑查询，一次返回所需表列集合，移除通用 Inspector 的多轮目录探测。

## 回归纪律

- 终态页、历史日期和无活动任务页面必须停止自动数据库轮询；轮询测试必须断言停止条件。
- 任务入队前必须一次性持久化业务 ID、关系表外键和 broker task ID；禁止“先发消息、后补关系”。
- 高频进度只能有一个结构化事实表；通用任务表不得复制每个细粒度进度事件。
- 孤儿修复宽限期必须按任务资源模型配置；浏览器独占任务不得套用普通短任务阈值。
- 修复器首次扫描禁止读取 `params/result`；只有确认需要执行修复时才允许点读最小状态。
- 任何 PostgreSQL VALUES/UNION 临时表与真实列比较前，必须由 PostgreSQL 方言编译测试证明类型一致。
- 验收阶段固定为 `code_changed -> deterministic_passed -> restarted -> load_passed -> accepted`，不能跳级。
- watchdog/修复器查询必须逐项证明会影响判定结果；仅供展示的快照不得进入分钟级后台循环。
- 批量任务提交的数据库事务数必须为 `O(1)`，不得随子任务数线性增长；行为测试必须断言 flush、commit、批量投影和缓存失效次数。
- 高频状态必须由状态生产者在同一事务维护结构化投影；轮询接口不得通过读取通用任务 JSON 来持续修复投影。
- 任何会把任务写回 `pending` 的新状态都必须同时登记“可见消息、丢失消息、重启窗口”三类 watchdog 契约测试。
- broker 发布后的数据库确认只能使用版本/CAS 条件；禁止重新读取旧 payload 后无条件覆盖可能已被 worker 或取消操作更新的状态。
- 所有任务生产者必须由行为测试捕获 commit 后 SQL，并断言成功、失败和延迟提交分支都不会回读 `publication_tasks.params/result`；新增生产入口没有该测试不得合入。
- 源文件到产物的映射必须在清洗入库端、两侧信息仍在内存时产生并沿工作流传递；发布端只允许消费映射，只有明确标记的历史兼容分支可以回读任务 JSON。
- 任何跨 commit 使用的 run/task/asset 身份必须在第一次 commit 前冻结为标量或不可变 DTO；禁止把 ORM 实例当跨阶段上下文。
- 远端数据库 schema readiness 必须用一次紧凑、显式列白名单的目录查询完成；禁止热点 worker 启动或任务阶段调用通用 Inspector 枚举目录。

## 验证

- PostgreSQL 原子 JSONB 更新已用真实数据库语法验证，空目标更新成功，无 SELECT。
- 定向回归覆盖批量提交、源端投影、watchdog CAS、历史页快照、重试消息恢复、清洗事件映射贯穿和发布完成零任务表查询；最新相关定向测试为 `44 passed`。
- PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)`：ZSXQ 活动任务查询 executor `0.029ms`；火柴人状态查询 executor `0.036ms`，确认无需继续堆索引。
- 完整确定性回归最终结果为 `778 passed, 5 skipped, 42 subtests passed`；数据库纪律门禁另有 `87 passed, 3 subtests passed`，Python 编译、前端类型检查、单 Alembic head 和 `git diff --check` 均通过。
- 安全重启完成，新后端 PID `91094`；17 个托管服务、6 个 TTS worker、watchdog 和两个 scheduler 均由 bootstrap 成功拉起。
- 旧火柴人页面真实轮询：首次建立完整结构后，第二次建立状态快照；后续请求由 `1600ms/315ms` 降到 `134-181ms`，且不再查询 45 行场景资产。
- ZSXQ 调度器窄投影真实执行 `30.1ms`，同一轮正确识别已有 running run 并跳过重复提交。
- 新 watchdog 连续多轮未再调用 `_zsxq_business_tasks_snapshot`；孤儿任务 `12704fbf-...` 由 CAS 路径重排后完成，发布后没有再次读取完整 `result`。
- 真实重启窗口暴露的 `cfffda51-...` 曾停在 `pending / auto_retry`；新增规则经可见性检查后将其重排为 `bd39c374-...`，新 worker 已领取并同步更新场景投影。watchdog 单独重载为新 PID `45417`。
- 新后端启动后正式慢查询只有一次冷场景详情 `1153ms`、一次 scheduler 启动恢复 `1482ms` 和一次 TTS 主键进度更新 `1512ms`；没有服务端 statement timeout、QueryCanceled、锁超时或 SQL 错误。诊断进程产生的单条 `1107ms` 全载荷点读不计入业务负载。
- 终态基础文案详情跨重启读取磁盘快照，真实请求 `200 / 318.85ms`；请求内只有用户鉴权查询，没有读取 `publication_tasks.params/result`。
- 通用 Inspector 目录查询从 `1312.18ms` 降为一条紧凑查询 `179.94ms`，端到端耗时下降约 `86%`。
- `2026-07-22 14:35:57` 起通过 Celery Monitor 托管接口完成增量重启：`content_pipeline=93072`、`zsxq_workflow=94748`、`zsxq_publish=95034`、`zsxq_sync=95318`、watchdog=`889`；四个 worker 均连接 Redis 并进入 `ready`。
- 新 PID 边界后 `publication_tasks` 审计为 `records=0 / signatures=0`；全量当前 Celery、backend、scheduler 和 slow-query 日志未发现慢 SQL、statement timeout、QueryCanceled、锁超时或数据库错误。重启 API 的 2.9-3.5 秒响应是等待 worker 停启，不是慢 SQL；`pidbox No consumers` 是控制回复队列噪声。
- 单条 `zsxq_attachments` 主键窄更新 `1245ms` 未重复，语句形状已经是结构化主键更新，暂按瞬时锁或远端 I/O 抖动观察；若同一指纹再次出现，直接进入锁等待和服务器 I/O 诊断，不再追加无依据索引。
- 当前状态：`runtime_replaced`。已知复发入口已经完成代码、契约测试、全量回归和新 PID 日志验收；真实新业务批次仍作为最终 `accepted` 的持续负载证据，不用短观察窗口宣称永久不会出现新的 SQL 指纹。
