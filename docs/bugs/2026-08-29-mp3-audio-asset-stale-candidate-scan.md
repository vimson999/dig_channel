# MP3 音频素材目录从失效附件候选反复扫描并放大远端往返

## 基本信息

- 状态：RUNTIME_REPLACED / OPEN / REPEATED / OBSERVING；查询预算已运行替换，但远端 driver/fetch/网络尾延迟在 2026-09-25 后窗再次出现
- 严重级别：P0
- 影响模块：MP3 视频编排音频素材目录 / `content_pipeline_assets` / `zsxq_attachments` / `zsxq_topics` / PgBouncer
- 首次发现：2026-08-29 方案 1 运行态追根
- 修复提交：当前工作区
- 根因分类：错误候选源优先级、失效文件后过滤、重复远端往返、目录快照缺失、数据库 Session 跨本地文件 I/O
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；属于热点列表把历史全量候选跨远端搬到应用后再过滤的同类问题
- 关联记录：`2026-08-29-mp3-audio-search-ime-request-overlap.md`、`2026-08-27-pdf-canonical-miss-n-plus-one.md`、`2026-08-09-zsxq-insights-cold-fulltext-transfer.md`
- 防线层级：shared_guardrail / query_budget / contract_test / runtime_observation

## 现象与影响

重启前真实页面默认请求 `request_id=53df69ee` 返回 200，但耗时 23.347 秒；Session 持有 22.590 秒，7 条 SQL 累计 22.455 秒。三个附件 chunk 查询每次只取 40 行，却分别耗时 3.432、16.844 和 1.151 秒。快速输入中的 `q=t` 请求 `request_id=b9ac5005` 又耗时 20.108 秒，其中 canonical 查询 19.576 秒，Session 持有 19.838 秒；即使前端只保留首尾请求，首个请求本身仍足以占住连接和 drain 约 20 秒。

真实数据进一步表明旧候选源不具备产出能力：`zsxq_attachments` 有 34,458 行，其中 3,886 行看似音频且标记已下载；`content_pipeline_assets` 有 726 行，其中 280 行是 cleaned audio。最近 120 个附件候选与 canonical source 的精确匹配、归一化路径匹配、cleaned ready、原始本地文件存在均为 0。280 个 cleaned 记录中有 126 个文件仍实际存在、154 个已被保留策略或历史清理移除；后者应过滤并保留数据库审计记录，不能伪造成可用素材或删除原始数据。

## 根因

旧实现先分页扫描 `zsxq_attachments`，关联 `zsxq_topics` 取展示元数据，再在应用层验证文件与 canonical 资产；当前历史附件记录几乎不能产出页面项，因此每一页都继续取下一 chunk。页面真正可用的权威源已经是 `content_pipeline_assets(status='cleaned', media_type='audio')`，候选顺序与数据所有权相反。

只读 `EXPLAIN (ANALYZE, BUFFERS)` 排除了盲目加索引：旧附件 chunk 在 offset 0/40/80 的 executor 分别为 172.228/175.074/176.175ms，计划扫描 3,886 个附件候选并顺扫 12,637 个 topic，shared hit 5,298、无磁盘读或临时文件；新 canonical 查询 executor 仅 1.875ms、72 shared hits、280 行。相同新查询在应用日志中却达到 19.576 秒，约为 executor 的一万倍，根因边界是重复远端/PgBouncer 往返与驱动等待，不是 PostgreSQL 缺简单索引。

实现过程中还捕获到一次未部署的 ORM 投影缺口：`load_only` 漏掉 `media_type` 会对 280 行逐项 lazy-load，产生 283 条 SQL、Session 12.48 秒。查询预算测试先失败后补齐投影，因此该诊断缺陷没有进入最终运行进程。

## 解决思路与实际方案

- 以 cleaned `content_pipeline_assets` 为唯一目录候选源，单次有界加载不超过 2,500 行，并显式投影目录构建所需全部列。
- 对 canonical source path/filename 只做一次有界附件元数据补充查询；最终用归一化绝对路径匹配，兼容 macOS `/var` 与 `/private/var` 的同一文件别名，不再分页扫描失效附件集合。
- 目录构建时验证 cleaned 文件真实存在；请求时再次验证当前页文件，保留 154 条历史审计记录但不返回失效素材。
- 增加进程内、加锁 single-flight 的目录快照，默认 TTL 300 秒、上限 3,600 秒；搜索、group/tab/hot 过滤、排序和分页都在有界快照内完成。并发冷建只有一个 builder，普通搜索不再重复访问数据库。
- 页面刷新显式传 `refresh=1` 强制重建快照；返回 telemetry 包含 catalog size、build time、cache hit/age/TTL，避免缓存掩盖失效。未删除字段、未回填假数据、未修改生产索引。
- 增加真实 SQLAlchemy 查询预算契约：连续搜索复用一个 catalog snapshot；目录构建只能有一条 canonical SELECT，防止 deferred-column N+1 回归。
- canonical 与 metadata 查询各自完成后立即结束只读事务并 remove Session，之后才进入 `os.path.isfile` 路径归一化和文件校验；缓存命中也先释放请求鉴权留下的 Session。查询异常先清理失败 Session 再抛出，目录路径不再让连接跨本地文件 I/O。

## 验证

- TDD 红灯 1：旧实现为获得 1 个结果扫描 121 条附件候选，违反 authoritative-first 契约。
- TDD 红灯 2：连续两个关键词各自重新查询 canonical 目录，共 2 条 SELECT；加入快照后只构建一次。
- TDD 红灯 3：初版投影遗漏 `media_type`，预算测试看到 2 条 canonical SELECT；补齐显式投影后恢复为 1 条。
- TDD 红灯 4：旧边界在 `os.path.isfile` 中仍观察到 `db.session().in_transaction() == True`；显式释放后 canonical/metadata/cache-hit 三条路径的文件校验均不再携带事务。
- 定向回归：MP3 audio asset/service/projection 共 186 项通过；前端 canonical 与 backpressure 契约通过。
- 真实 service：冷目录 1.689 秒、2 条 SQL，扫描 280 个 canonical、保留 126、跳过缺失 154；缓存搜索 `q=天` 0.980ms、`q=天风` 0.758ms，均为 0 条 SQL；强制刷新 1.231 秒、2 条 SQL。
- 最终安全重启：门禁读取 47 条 processing 记录和 0 条活动 ZSXQ sync，未发现不可中断任务；未使用 FORCE。新 backend PID 52574，health healthy；bootstrap 于 17:32:32 完成，17 个 Celery 服务全部 `ok=true`、watchdog 正常、`errors=[]`。Bilibili intelligence scheduler 按本机 disabled 配置保持未运行，其启动结果仍为 `ok=true`。
- 真实浏览器冷请求 `request_id=ea2496e3` 为 1.009 秒，canonical/附件 SQL 分别 717.46/195.15ms，无长 Session 告警；暖搜索 `request_id=15b783b2` 为 1.59ms、0 条目录 SQL，返回 2 个真实文件。
- 真实快速输入与强制刷新：`request_id=499a067d` 冷刷新 1.219 秒完成，随后最终 `q=天风` 的 `request_id=faabd527` 为 1.53ms、0 条目录 SQL；两请求没有重叠，中间关键词没有发出，页面最终显示 2 项。
- Session 边界修复加载后的真实页面强刷 `request_id=3708b3b0` 为 1.094 秒，canonical/metadata SQL 分别 562.22/248.34ms；随后暖搜索 `q=天风总` 的 `request_id=108830e2` 为 3.02ms、0 条目录 SQL并返回 1 个真实文件。两请求均无慢查询或长 Session 告警。
- 完整 unit 1,456/1,456 通过；compileall、JavaScript 语法、recurrence JSON、Bug strict 和 diff check 的最终结果记入当日巡检报告。

## 复发预防与后续事项

- 目录/搜索 API 必须先明确 authoritative source；历史关联表只能补充元数据，不能反向成为无限候选源。
- 有界目录快照必须同时具备 TTL、single-flight、显式 refresh、真实文件二次校验和可观测命中信息；不能用永不过期缓存掩盖数据库或文件变化。
- ORM `load_only` 必须覆盖后续所有展示/判定列；目录构建的真实 SELECT 数固定为 O(1)，任何 deferred-column lazy-load 都应由测试拒绝。
- 数据库结果必须在 Session 释放后转换为冻结 DTO，再执行文件系统、网络或其他外部 I/O；缓存命中不能假设当前请求没有鉴权事务。
- executor 小于 10ms 而应用/驱动达到秒级时，优先追网络、PgBouncer、fetch 和 Session 生命周期；本问题没有新增索引建议。
- 下一轮从最终 backend 17:31:43 边界统计完整 24 小时：旧附件 chunk/逐项 canonical 指纹为 0；audio-assets 同页同时在途不超过 1；暖搜索目录 SQL 为 0；冷/强刷只允许 2 条目录 SQL且 P95 明显低于 20.029 秒；文件 I/O 期间无活动事务、无 20 秒 Session 持有。任一条件缺证据都继续 OBSERVING，不得 CLOSED。

## 2026-08-29 最终复发、页面冷启动放大器与 v6 验收

PID 55314 上的真实页面初次加载再次捕获底层长尾，而不是缓存假绿：目录
`request_id=7c7c4e07` 为 11.812 秒，canonical SQL 11.423 秒、连接持有 11.461 秒；同页任务历史
`f84a8c62` 为 16.918 秒，其中本应轻量的 `SET LOCAL statement_timeout=5000` 自身等待
16.354 秒、连接持有 16.644 秒。同期全量 Celery 状态 `092d1145` 为 14.404 秒，并跨
`publication_tasks`、`zsxq_sync_runs`、`zsxq_workflow_runs` 等表执行多条慢 SQL。17:52:34–17:57:19
共有 7 条慢 SQL、累计 40.669 秒、P95 16.354 秒和 2 条长 Session；21 个 HTTP 请求虽全部 200，
用户仍经历目录/历史/worker 徽标同时冷阻塞。

该样本把两个根因边界拆开：authoritative catalog 已让目录固定为 O(1) SQL，旧附件 chunk 与逐项
canonical 没有复发；但页面初始化并发把目录、history 和不必要的全量 Celery 业务快照同时压到远端
链路，放大了一个网络长尾窗口。页面只需要两个 worker 的 running/queue 徽标，不应为此读取所有业务
任务。因此新增有界 `runtime-status`：服务 id 去重、校验且最多 8 项，只做进程枚举、精确队列长度和
paused/PID 检查；鉴权 Session 在这些外部 I/O 前释放，明确不调用业务任务快照或 broker ping。
前端 v6 只请求 `mp3_video_workflow,render`，超时预算由全量接口的 30 秒收紧为轻量端点的 5 秒。

TDD 先在旧代码上确认 `_queue_lengths_for`/runtime-only 路径不存在、前端仍请求全量 status；实现后
`test_celery_monitor_watchdog` 38 项、MP3 相关 110 项和前端契约通过。最终安全门再次确认
47 条 processing 记录、0 条活动 ZSXQ sync 且无不可中断任务；标准重启未使用 FORCE，backend
PID 57273、bootstrap 18:05:49 完成，17 个 Celery 服务均 `ok=true`、`errors=[]`、health healthy。

真实页面 18:06:17 后的首次 v6 运行证据为：目录 `b95c18bf` 1.503 秒（2 SQL：829.17/392.35ms），
runtime worker status `fbe6f3a8` 377.78ms/0 业务 SQL，jobs `45311060` 377.36ms；12/12 请求成功，
累计 2.698 秒、P95 377.78ms、最大 1.503 秒，慢 SQL/长 Session/SQL error 均为 0。页面显示
10 个真实文件、扫描 280/跳过缺失 154、3 条真实成功任务及 MP3/render worker 正常；随后暖搜索
`7c4e30ab` 5.8ms、cache hit、0 目录 SQL并显示真实 `天风总量每周论势...`。完整 unit 最终为
1,459/1,459 OK。

把窗口继续延长到 18:14:01 后，分析器统计 17/17 请求成功、累计 3.461 秒、P95 723.55ms、最大
仍为目录冷建 1.503 秒，慢查询日志阈值、长 Session、SQL error 和六类 PgBouncer 错误仍为 0。
暖搜索随后触发的单项 workflow-stats `request_id=ad569087` 为 723.55ms，其中窄
`publication_tasks` 查询 604.96ms，被 publication_tasks 500ms guard 单独标记；该查询只返回 0 行、
无大 JSON，不属于目录 SQL，也没有新增索引/EXPLAIN 证据。它作为低频次秒级网络尾延迟继续观察，
不能用较短的 18:06:17–18:06:54 零警告片段隐去。

后续追根推翻了“只有低频网络尾延迟”的不完整判断。11 个历史 `pipeline:` 工作流的完整引用有 45 字符，
却被直接写入 36 字符 `source_task_id` 并由投影同步静默截断；workflow-stats 使用完整引用等值查询，因此
`ad569087` 必然返回 0 行。真实 EXPLAIN 显示完整、历史截断和规范 UUID 三种参数都走现有 MP3 source
索引，执行仅 0.022–0.032ms，未新增索引。创建端现把 pipeline 引用规范为原始 UUID 与
`content_pipeline_asset`，JSON 保留完整引用；读取端按 `(source_feature, source_task_id)` 归属一条窄投影，
同时兼容 UUID 和无歧义历史截断键，避免同 UUID 的跨来源串单；列表 API 重建完整 `pipeline:` 引用。

安全门通过后先标准重启到 PID 59946；补齐 source owner 防串单与完整类型引用回显契约后，再次通过门禁并
标准重启到最终 PID 62310，bootstrap 17/17、`errors=[]`、health healthy。真实登录页面搜索
《英伟达AI融资平台的机遇与风险260827.mp3》后显示“编排 1 次 / 最近已完成 / 成功 1”，关联任务
`a344af22-a913-445e-8369-77b7a506aa56`。精确 stats 的 4 次端点为
176.60/297.64/310.83/544.38ms，SQL 为 112.17/204.16/211.70/354.34ms，全部返回 1 行且没有慢查询、
长 Session 或 SQL error；最终 PID 62310 上 `6487357a` 为178.68ms、SQL112.67ms、返回1行，结果一致。
最终 18:43:41–18:49:51 窗口 8/8 请求成功、累计1.495秒、P95/max 1.285秒；冷目录
`7a303b88` 1.285秒（两条业务目录 SQL 602.14/202.76ms），随后 cache-hit 搜索 `eb4e1d98`
3.36ms/0目录SQL。该短窗0条慢查询日志、0长Session、0 SQL error、0六类PgBouncer错误。历史兼容已
运行替换；为避免制造昂贵任务，未来规范写入仍待下一次自然投递。

authoritative catalog、Session/文件 I/O 边界、轻量 worker 状态和 source identity 兼容均已运行替换；
底层 PgBouncer/Tailscale 长尾在 17:54 明确复发，故总体仍为 `RUNTIME_REPLACED / OBSERVING`，不能写成
已解决。下一完整窗口从最终 backend 启动/页面边界（18:43:41/18:44:48）计：旧附件 chunk、逐项
canonical 和该页 `/api/v1/celery/status` 调用均为 0；冷目录最多 2 条 SQL、暖搜索 0；runtime status
无业务 SQL；历史 stats 不再返回假 0，下一自然任务写入规范 UUID；同时六类网络错误、失败 SQL 和长
Session 为 0。

## 2026-09-17 冷目录长尾复核

固定窗真实音频目录 API 2/2 成功，但耗时 5.255/4.398 秒；canonical SQL 同一指纹
`be891b5c1af362da` 为 5.042/4.002 秒、累计 9.044 秒，占本窗 10 条慢 SQL 总耗时的 47.7%。
当前 cleaned audio 773 行，仍是有界一次投影；只读 EXPLAIN 顺扫 2,100 行、返回 773，executor
2.508ms / shared read 222。唯一 5.108 秒 Session hold 中 SQL 占 5.042 秒、非 SQL 66ms，未见旧附件
N+1 或文件 I/O 跨事务证据。长尾在 executor 外，缺 request 级 pool wait/传输 marker，故旧查询预算可保持
`RUNTIME_REPLACED`，整体用户延迟继续 `OPEN/OBSERVING`；不据顺扫盲目建索引。下一自然冷/强刷记录
行数、response bytes、pool/driver 分段、P95 和 cache 命中，完整后窗仍要求暖搜索目录 SQL 为 0。

## 2026-09-18 同指纹再次复发

固定窗音频目录 3 个真实 API 累计 9.547 秒、P95 5.173 秒；canonical 指纹
`be891b5c1af362da` 仍有 2 条，累计 8.933 秒、P95 4.772 秒。
旧逐项附件 N+1 没有进入慢 SQL 样本，但并不排除低于阈值的查询；代表数据库 executor
仍为毫秒级，底层连接与驱动尾延迟继续 OPEN/REPEATED/OBSERVING。
下一自然冷/暖搜索必须补齐 request 级 pool wait、fetch bytes 和 SQL 数。

## 2026-09-25 冷目录远端尾延迟再次复发

固定窗仅一个真实冷目录请求 `07585b10`，API 6.040 秒，其中 canonical 单次有界查询
`be891b5c1af362da` 5.692 秒、影响 989 行；连接持有 5.757 秒，其中 SQL 5.692 秒、非 SQL 仅 65ms。
客户端 telemetry 为 `client_trigger=initial`、`client_inflight=1`、`cache_hit=False`，未见并发叠加；
本窗只有这一条 catalog SQL，旧附件 chunk、逐项 canonical N+1 和文件 I/O 跨事务没有复发。

同形只读 `EXPLAIN (ANALYZE, BUFFERS)` 对 2,771 行顺扫、返回约 1,002 行，shared hit 292、
quicksort 904kB，executor 2.176ms；应用耗时约为 executor 的 2,615 倍。当前 `content_pipeline_assets`
约 3.2MB，canonical/status/created 等既有索引均在；增加索引无法解释该差距，也没有 DDL 证据。
状态保持 **RUNTIME_REPLACED / OPEN / REPEATED / OBSERVING**：查询预算修复仍有效，但远端 driver/fetch/
PgBouncer/网络尾延迟及“读取近千行最终返回 0 项”的目录可用性投影缺口未闭环。下一轮需记录 response bytes、
fetch/pool 分段和实际文件命中数；不能用截断历史、删除审计行或盲目索引规避。

## 2026-09-26 固定窗复核

本窗 canonical 旧慢指纹为 0，应用慢 SQL 从 11 条降至 6 条；但固定窗自然 HTTP 请求为 0，因而没有
audio-assets 冷/暖请求可以验证查询预算、文件命中或用户时延。后窗请求解析器能正常识别 `/health` 与四个
ZSXQ 只读 API，说明这里是流量覆盖空窗而不是解析器再次失效。状态继续
**RUNTIME_REPLACED / OPEN / REPEATED / OBSERVING**；“未捕获”不能升级为未复发或 CLOSED，下一次自然
audio-assets 仍须补齐候选/存在/返回行数、response bytes、pool/fetch/deserialize 分段。

## 2026-09-29 冷目录长尾再次复发

固定窗 request `0817e459` 的 audio-assets 为 200/7.524 秒；canonical 指纹
`be891b5c1af362da` 单次 6.851 秒、返回 1,047 行。对应连接持有 6.897 秒，SQL 占 6.851 秒、
非 SQL 仅 46ms，因此本次没有 Session 跨文件或外部 I/O 的证据。生产只读 EXPLAIN 顺扫 2,985 行、
返回 1,047 行、326 shared read、sort 974kB、无 temp，executor 3.656ms；应用耗时约为其 1,874 倍。

旧逐附件 N+1 与逐项 canonical 未进入慢样本，一次有界投影仍有效；但远端 driver/fetch/PgBouncer/网络
尾延迟明确复发。表规模与选择性不支持为该 3MB 级、返回约三分之一行的查询新增索引。本项继续
**RUNTIME_REPLACED / OPEN / REPEATED / OBSERVING**；下一自然冷/暖请求必须补 pool acquire、fetch、
serialize、response bytes 与文件命中计数，暖请求目录 SQL 仍应为 0。

## 2026-10-20 300 秒 TTL 后的真实搜索连续触发远程 fetch 尾部

固定窗 `audio-assets` 10/10 请求成功，但其中 3 个冷建分别由初始加载和两次真实搜索触发，间隔均略超过
300 秒 TTL；三次都记录 `force_refresh=False/cache_hit=False`，每次重新获取 1,352 行。目录 SQL 指纹
`be891b5c1af362da` 累计 26.178 秒，P50 8.421 秒、P95/max 9.808 秒，对应 HTTP P95/max 10.777 秒。

同形生产 EXPLAIN 顺扫 4,370 行、返回 1,352 行，shared read 469、quicksort 1,271kB，executor 仅
5.182ms；应用耗时是 executor 的约 1,500–1,900 倍。查询仍是一次有界投影，未见旧附件 N+1、deferred
column lazy-load 或文件 I/O 跨 Session；因此不增加索引。状态继续 **RUNTIME_REPLACED / OPEN /
REPEATED / OBSERVING**。下一自然冷建必须增加 pool acquire、fetch bytes/deserialize 分段；在此证据齐全前
只能评估版本化或 stale-while-refresh 缓存，不能用任意延长 TTL 掩盖链路根因。

## 2026-10-21 目录尾延迟继续复发

固定窗 `audio-assets` 18/18 成功，其中 3 个冷建再次执行 canonical 指纹 `be891b5c1af362da`，累计
24.138 秒、P50/P95/max 8.041/8.181/8.181 秒，返回 1,369 行；HTTP P95/max 9.013/9.415 秒。三次
Session hold 合计 25.055 秒，非 SQL 仅 916.57ms，旧附件 N+1、deferred lazy-load 与文件 I/O 跨 Session
均未捕获。上轮同形生产 executor 5.182ms，仍排除盲目加索引。

状态保持 **RUNTIME_REPLACED / OPEN / REPEATED / OBSERVING**。下一自然冷建必须补 pool acquire、fetch
bytes/deserialize 与 response bytes；暖请求目录 SQL 必须为 0，不能用 API 全成功或本窗无 timeout 关闭。

## 2026-10-22 目录尾延迟持平复发

固定窗 `audio-assets` 13/13 成功，3 个冷建再次命中 canonical 指纹 `be891b5c1af362da`，累计
24.109 秒、P50/P95/max 8.006/8.006/8.269 秒；HTTP P95/max 9.044/9.170 秒。三次 Session hold
均与慢 SQL 关联，匹配 SQL 合计 24.250 秒、非 SQL 仅 352ms；旧附件 N+1、deferred lazy-load 和文件 I/O
跨 Session 均未捕获。同形 executor 仅 5.182ms，仍没有索引 DDL 依据。

状态保持 **RUNTIME_REPLACED / OPEN / REPEATED / OBSERVING**。下一自然冷建继续要求 pool acquire、
fetch bytes/deserialize、response bytes 和同秒 PgBouncer/socket/route 证据；暖请求目录 SQL 必须为 0。

## 2026-10-23 单次尾部继续恶化

固定窗 `audio-assets` 5/5 成功，2 个冷建命中 canonical 指纹 `be891b5c1af362da`，累计
16.652 秒、P50/P95/max 8.271/8.380/8.380 秒。相较上一窗，冷建次数和累计耗时下降 33.3%/30.9%，但
单次 P95/max 又上升约 3.3%/1.3%。两次 Session hold 合计 16.903 秒，其中匹配 SQL 16.652 秒、非 SQL
仅 558ms；旧附件 N+1、deferred lazy-load 和文件 I/O 跨 Session 均未捕获。此前同形 executor 仅
5.182ms，本窗表统计和代表 EXPLAIN 仍不支持索引 DDL。

状态保持 **RUNTIME_REPLACED / OPEN / REPEATED / OBSERVING**。次数下降不能抵消单次尾部复发；下一自然
冷建仍必须补 pool acquire、driver fetch bytes/deserialize、response bytes 与同秒 PgBouncer/socket/route
证据，暖请求目录 SQL 必须为 0。

## 2026-10-24 次数与累计耗时双重恶化

固定窗 `audio-assets` 10/10 成功、HTTP 累计 39.786 秒，P95/max 10.961 秒；canonical 指纹
`be891b5c1af362da` 由上一窗 2 次/16.652 秒恶化到 4 次/35.271 秒，次数 +100%、累计 +111.8%，
P95/max 8.878/9.314 秒。4 个连接持有 P95/max 9.375 秒，SQL 合计 35.271 秒、非 SQL 仅 270ms；未见
Session 跨文件 I/O、旧附件 N+1 或逐项 lazy-load。

同形生产 executor 仍为毫秒级，目标表无锁、无 temp/大读证据，PgBouncer wait P95 54.29ms、query P95
82.38ms 且八类错误均为 0；因此本窗恶化继续归于 executor 外 driver/fetch/私网尾部，而不是缺索引。
状态保持 **RUNTIME_REPLACED / OPEN / REPEATED / OBSERVING**。下一自然冷建必须补 pool acquire、fetch bytes/
deserialize、response bytes 与同秒 socket/route；在分段证据齐全前不以延长 TTL 或盲目 DDL 掩盖根因。

## 2026-10-26 无自然目录请求，不能以零指纹判绿

固定窗没有 `audio-assets` 自然请求，canonical 指纹 `be891b5c1af362da` 为 0。相较上一窗 4 条/35.271 秒只是
覆盖消失，不是尾延迟改善或修复后证据。既有 O(1) 投影、single-flight 与 Session 外文件校验保持不变，本轮也
没有引入 TTL、超时或索引掩盖。

状态保持 **RUNTIME_REPLACED / OPEN / REPEATED / UNOBSERVED**。下一自然冷/暖配对仍须记录 pool acquire、
driver fetch bytes/deserialize、response bytes 与同秒 socket/route；暖请求目录 SQL 必须为 0。

## 2026-10-27 两次自然冷建长尾明确复发

固定窗 `audio-assets` 5/5 成功、HTTP 累计 21.557 秒，P95/max 12.106 秒；canonical 指纹
`be891b5c1af362da` 两次累计 20.033 秒，分别 11.341/8.692 秒，均返回 1481 行。request
`aa2987f3`/`2acd5aa0` 的 Session hold 为 11.426/8.749 秒，非 SQL 仅 84.73/56.54ms，证明文件校验仍在
Session 外，没有旧附件 N+1 或 lazy-load 复发。

生产同形查询返回 854,488 bytes，Seq Scan + sort executor 仅 2.869ms、504 shared hit、无物理读；应用最大值
约为 executor 的 3,953 倍。既有 2,500 行上限、字段投影、single-flight 与 300 秒缓存均有效，但 cache miss 后
driver/PgBouncer/私网 fetch 尾部再次复发。状态恢复为 **RUNTIME_REPLACED / OPEN / REPEATED / OBSERVING**；
不加索引、不延长 timeout/TTL。下一冷建必须补 pool acquire、fetch bytes/deserialize、server socket、route、
远端 activity 与 response bytes 的同秒分段，暖请求目录 SQL 必须为 0。

## 2026-10-29 三次冷建与驱动接收阶段精确分段

固定窗 `audio-assets` 8/8 成功、累计 36.317 秒，P95/max 14.269 秒；canonical 指纹三次累计
33.761 秒，P50/P95/max 11.133/12.993/12.993 秒。8 次请求只有 3 次目录 SQL，证明 300 秒缓存和
single-flight 正常；三个 hold 的非 SQL 合计仅 675.04ms，Session 外文件校验未复发。

同形生产查询返回 1,541 行/881,038 bytes，executor 6.527ms。新增同连接分段探针显示 connect 354.84ms，
三次 execute/fetch 8.967/9.365/9.411 秒，Python materialize 仅 8.95/7.48/9.17ms，排除 executor 与应用
反序列化。现有投影字段均被目录合并/解析/响应合同使用，盲目删字段会破坏完整性；服务端分页会改变全局搜索
语义。本轮不改代码、不加索引，状态保持 **RUNTIME_REPLACED / OPEN / REPEATED / OBSERVING**。下一次自然
冷建必须同步采 client/server socket、route、server activity 与 response bytes。

## 2026-10-30 自然冷建与 PgBouncer/直连同形探针

固定窗 `audio-assets` 8/8 成功，只有 1 个自然冷建命中 canonical 指纹 `be891b5c1af362da`：
SQL 9.288 秒、1,558 行，请求 10.075 秒、Session hold 9.351 秒，hold 内非 SQL 仅 63.42ms；其余
7 次缓存命中保持快速，说明 300 秒缓存、single-flight 与 Session 外文件校验仍有效。

同一只读查询分别经 PgBouncer 与 `DIRECT_DATABASE_URL` 执行，均返回 1,558 行/891,354 bytes：前者
connect 9.12ms、execute/fetch 9.105 秒、Python materialize 4.61ms、executor 6.029ms；后者 connect
236.73ms、execute/fetch 9.067 秒、materialize 6.52ms、executor 5.139ms。两条路径同慢，排除缺索引、
PostgreSQL executor、PgBouncer 排队与 Python 结果物化，公共边界收窄到 psycopg2 经 Tailscale
`100.96.3.12 -> 100.92.99.39`、MTU 1280 的结果接收链路。

状态保持 **RUNTIME_REPLACED / OPEN / REPEATED / WAITING_INFRASTRUCTURE**。字段均服务于现有完整目录合同，
本轮不删字段、不分页改语义、不延长 TTL/timeout，也不盲目加索引。下一轮须取得自然冷建的 client/server
socket 分段或在同等 payload 下完成同机房/非 Tailscale A/B，并以随后完整 24 小时自然窗确认不复发。

## 2026-11-02 四次自然冷建再次主导全窗慢 SQL

固定窗 `audio-assets` 13/13 成功，canonical 指纹 `be891b5c1af362da` 四次累计 38.203 秒，P50/P95/max
约 9.667/9.902/9.902 秒；对应四个连接持有 P95/max 约 10.025 秒，匹配 SQL 合计 38.312 秒、非 SQL
仅 0.716 秒。该指纹占全窗慢 SQL 累计耗时 64.5%，是本轮首要性能问题。

结合 10-30 同形 PgBouncer/直连 execute-fetch 9.105/9.067 秒、executor 6.029/5.139ms 的 A/B，根因仍在
共同 Tailscale/psycopg2 结果接收边界，而非 PostgreSQL executor、索引、Python materialize 或 Session
跨文件 I/O。本轮不删完整字段、不改全局搜索语义、不延长 TTL/timeout、不加索引。状态保持
**RUNTIME_REPLACED / OPEN / REPEATED / WAITING_INFRASTRUCTURE**；下一轮继续要求同机房/非 Tailscale A/B
或 client/server socket 同秒分段，随后完整自然窗确认不复发。
