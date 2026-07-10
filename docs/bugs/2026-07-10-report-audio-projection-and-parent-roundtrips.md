# 2026-07-10 研报音频列表大载荷与父编排重复数据库往返

## 基本信息

- 状态：worker 已部署并通过真实流程；Web API 待 5001 手动重启后验收
- 严重级别：高
- 影响模块：report audio API / report audio worker / Doubao worker / publisher / PostgreSQL
- 首次发现：2026-07-10 12:00 后慢查询复盘
- 修复提交：待提交
- 根因分类：热点大字段、ORM 隐式懒加载、SELECT-before-UPDATE、结构化投影缺失
- 复发键：`HOT-PATH-PAYLOAD-POLLING` / `DB-SESSION-LIFECYCLE`
- 是否复发：同类问题多次复发
- 关联记录：`2026-06-23-report-audio-history-timeout-large-result.md`、`2026-07-10-report-audio-tts-cache-full-json-scan.md`、`2026-07-10-celery-runtime-leak-and-mp3-db-roundtrips.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

- 12:00 后慢日志中，旧式主键整行读取 68 次，平均 4.15 秒，最大 62.29 秒。
- TTS 缓存 JSON 扫描 22 次，平均 22.61 秒，最大 31.61 秒。
- 研报列表每页 10 条时，普通子任务、竖版和预览竖版分多次查询；页面一次冷刷新可累计到 4-7 秒。
- 子任务查询虽然 PostgreSQL 执行计划仅 0.309ms，但 96 行中的 `error_message` 共约 125KB，最长单条 22KB，客户端取回中位数约 1.44 秒。
- 父编排续跑期间，`_emit`、`_update_result`、`_record_child` 等每次都先读取父任务 `result`，再合并写回；一次 13.6 秒续跑包含大量无业务必要的父任务 SELECT。

## 影响

- 页面冷加载和编排状态推进受远端数据库链路延迟成倍放大。
- JSON 中已经有字段，但缺少结构化投影，导致新数据仍沿用历史慢路径。
- 单次数据库瞬断可能把豆包朗读任务直接判失败。
- ORM 未加载属性在提交后被访问时会隐式补查整行，使代码审查中看似轻量的查询重新退化。

## 根因

1. `publisher` 竖版类型、方案、预览时长和产物路径只存在于 `params/result`。
2. 豆包文本哈希、分段序号、重试序号和 WAV 路径只存在于 JSON，缓存匹配必须解析 JSON。
3. 多处 `Query.get()` 与提交后过期对象破坏 `load_only` 的预期边界。
4. 列表为同一批父任务分别执行 parent、source、竖版和预览查询，并返回完整历史错误堆栈。
5. 父编排状态写入采用“SELECT result -> Python 合并 -> UPDATE”，远程链路往返次数随阶段和分段数增长。
6. 模型投影钩子读取未加载的 `publisher_vertical_enabled`，触发隐式整行 SELECT。

## 实际方案

- 新增 10 个结构化投影列，覆盖 publisher 竖版属性与豆包 TTS 哈希/分段/WAV 路径。
- 入库时由 `PublicationTask` 事件同步投影；publisher 的直接 bulk update 同步维护对应产物列。
- 回填 3508 条历史任务中的相关记录，并创建 3 个部分/组合索引。
- TTS 匹配改用 `doubao_tts_text_hash`、`doubao_tts_chunk_index` 和状态列，不再用 JSON 过滤。
- 研报列表把总数并入窗口查询，把 parent/source/竖版/预览子任务合并为一次轻量查询。
- 列表错误只投影前 500 字符；数据库仍保存完整错误，详情与审计不丢失。
- 所有 `PublicationTask.query.options(...).get()` 改为显式主键过滤，避免 identity map/过期对象触发整行刷新。
- report audio 父状态更新改为作用域快照加 PostgreSQL JSONB 原子补丁；热更新不再前置 SELECT。
- 豆包进度改为带 3 次短重试的原子 UPDATE；重试参数在长任务开始前读取，异常路径不再从过期对象懒加载 `params`。
- 慢查询调用位置跳过 `<string>` 伪帧，日志能定位到真实业务函数。
- 增加 `publication_tasks` P0 专项查询守卫：500ms 独立阈值，自动识别 JSON 条件、批量 `params/result`、慢载荷和失败 SQL，并记录稳定 SQL 指纹、PID、进程启动时间与调用位置。
- 增加按时间范围聚合专项/旧慢日志的审计脚本，避免把参数差异和重复日志误算成不同问题。
- 横向检查时发现独立豆包接口仍会扫描最多 500 条完整 JSON 做文本复用；已切换为 `doubao_tts_text_hash/doubao_tts_wav_path` 精确查询，命中后才读取单条详情。
- 新守卫在 PID 87422 的首个真实批次立即捕获 `_completed_gemini_task/_active_gemini_task` 两个批量载荷查询；新增 `gemini_response_chars` 入库投影，完成候选先按长度列筛选，活动候选只读轮询列，最终只按主键读取命中详情。
- publisher watchdog 的 pending/processing/business repair 扫描改为轻量候选列；仅对达到修复条件的单条任务按主键加载 `params/result`。
- publisher 视频复用新增 `publisher_landscape_enabled/publisher_render_mode` 投影；候选列表通过 render mode、preview seconds、状态和投影产物匹配，不再批量读取最多 30 条 JSON。通用子任务详情也移除了不再使用的 `params`。

## 验证

- DDL、回填和索引在真实 PostgreSQL 16 上执行成功，共耗时 0.82 秒。
- 回填结果：竖版子任务 110 条，其中 94 条有视频路径；TTS 哈希 192 条；已完成 WAV 路径 516 条。
- 竖版与 TTS 哈希索引计划执行时间分别为 0.036ms 和 0.017ms；网络中位数约 31-33ms。
- 子任务载荷从约 155KB 降至约 40KB，驱动层中位数从约 1.44 秒降到约 0.50 秒；首次冷连接仍有约 1.4 秒尖峰。
- 真实 PostgreSQL 临时任务连续执行 `_emit`、`_update_result`、`_record_child`，3 次 UPDATE 共 517.51ms，中间无 SELECT；字段合并正确，临时行已删除。
- 72 项 report audio / Doubao / publisher / 投影纪律专项测试通过。
- 15 个 Celery 服务、watchdog 和原运行 scheduler 已重启；第二轮又单独重启 report audio 与 Doubao worker 加载原子写入和懒加载修复。
- 真实批次 `44b47706-a388-4c1e-acfa-1ad2158d72b4` 于 14:23:22 成功收口；WAV、横屏视频、竖屏视频、父子状态和完成时间均完整，publisher 子任务为 `e60e9eb5-2733-4ebf-aa97-0a74da75a322`。
- 首轮原子写入 worker（PID 44022）的真实收口仍有 4 条慢查询：一次父 `params/result` 冷加载 4.46 秒、一次 JSONB 原子 UPDATE 1.18 秒、一次提交后 child 过期回表 1.01 秒、一次最终父 result 重读 1.13 秒。
- child 过期回表和最终父 result 重读已继续移除并部署到 PID 54377；该最终版本尚缺第二个完整真实批次，不能宣布全部解决。
- 4.46 秒父整行查询最终定位为初始 commit 后读取过期 `task.current_step/progress`；改为提交前保存标量并部署到 PID 57596。真实 PostgreSQL 受控集成段只产生 `SELECT, UPDATE` 各 1 次，没有提交后的第二次 SELECT，测试行已删除。
- 新代码进程对真实列表连续执行 5 次强制冷加载，每次固定 2 条 `publication_tasks` SELECT、均返回 10 条完整摘要；耗时为 1001/595/974/544/976ms，中位数 974ms。缓存命中为 6ms 且不访问 `publication_tasks`。旧后端同场景为 4-7 秒和 5 次数据库往返。
- 专项审计脚本对 12:00-14:41 的旧慢日志聚合出 374 条记录、49 个 SQL/来源签名；其中竖版子任务批量 JSON 指纹出现 128 次，最后一次仍来自旧 Web PID 95691，证明代码修改后未重启 5001 不能作为运行验收。
- 29 项数据库投影纪律与运行时初始化测试通过；专项守卫覆盖快速单行详情放行、JSON 条件识别、批量大字段识别、慢单行载荷识别和监听器幂等注册。
- `gemini_response_chars` 已部署到真实库并回填 628 条 Gemini 任务，其中 538 条具有有效响应长度，最大 49789 字；父任务/状态/创建时间部分索引已创建。
- 守卫首个真实样本：`_completed_gemini_task` 批量 `params/result` 查询 909.67ms，`_active_gemini_task` 同类查询 27.83ms。两条均已定位并改为结构化候选查询，等待当前真实批次结束后重启 report worker 验收。
- publisher 新投影已回填 699 条任务，其中 567 条横屏启用；`source_task_id/render_mode/preview_seconds/status/created_at` 组合索引已创建。
- 113 项模型投影、report audio、script material、Doubao、publisher、queue repair、运行时守卫测试通过；真实批次 `d639ba5f-50c9-46e1-8790-7123d8afb6be` 已依次通过 Gemini 与多段 TTS，并进入 publisher 视频阶段。
- 真实批次 `d639ba5f-50c9-46e1-8790-7123d8afb6be` 后续成功收口：父任务为 `succeeded/done/100%`，横屏视频、竖屏视频、普通封面、竖屏封面和播客封面均已生成；publisher 子任务为 `ded12885-0c68-427f-ad28-22f7bb039a54`。
- publisher 渲染阻塞定位为进程内 Playwright 没有操作系统级硬超时；workbench 渲染已改为独立进程，默认 70 秒后终止整个进程组，并通过真实 1400x1400 播客封面冒烟验证。
- report worker 的 deferred retry 增加最多 8 个活动任务的有界恢复快照；同一 worker 内等待 publisher 的 60 秒轮询只读取结构化状态列，不再每轮重读父任务 `params/result`。worker 重启后的首次恢复仍允许一次完整载荷读取。
- report audio 详情和音视频/封面 Range 请求改为读取结构化路径列；竖版子任务、Bilibili 子任务查询改为轻量投影，详情缓存只在父子 `updated_at` 版本变化时重建。
- publisher 消费消息先以轻量列校验 Celery ID 与终态，再加载任务载荷；重复或过期消息不再先读取完整 `params/result`。
- 最新 83 项投影纪律、report audio API/worker、渲染隔离与 publisher 专项测试通过；`py_compile` 与 `git diff --check` 通过。
- render worker 已于 16:05:17 重启为 PID 43044。专项审计中 15:57 后剩余的 API 批量载荷样本均来自未重启的旧 Web PID 15354；publisher 的 2.43 秒样本来自已停止的旧 render PID 21274，不能计入新进程验收。

## 复发预防

- 新增 JSON 热字段必须同时回答：是否需要投影列、谁负责入库同步、历史如何回填、查询如何切换。
- `load_only` 后禁止使用旧式 `Query.get()`；静态契约测试扫描该模式。
- ORM 事件不得直接读取可能未加载的属性，必须通过 SQLAlchemy state 检查。
- 列表错误、日志和堆栈必须有载荷预算；完整信息放详情或专用事件表。
- 父编排热更新禁止 SELECT-before-UPDATE；新增写路径必须通过原子写入静态测试和真实 PostgreSQL 集成验证。
- 性能验收必须区分 PostgreSQL 执行时间、网络传输时间和 Python/ORM 时间。
- `publication_tasks_query.log` 中的 `json_predicate` 或 `multirow_payload` 在新进程出现 1 次即视为复发，不再等待累计成“大量慢查询”。
- 缓存复用必须使用结构化键和产物路径；禁止任何入口以扩大 JSON 扫描上限作为兼容方案。

## 后续事项

- 5001 Web 进程仍由用户手动管理，当前仍为旧 PID 15354；需重启后用真实页面连续冷加载至少 5 次，确认详情、媒体和竖版/Bilibili 子任务查询不再批量读取 JSON。
- 用下一个完整研报批次验证 PID 39542 report worker 与 PID 43044 render worker；目标是除任务或 worker 首次恢复的一次父 JSON 冷加载外，不再出现父 result SELECT-before-UPDATE、过期 child 整行回表、最终 result 重读和过期 publisher 消息整行读取。
- `publication_tasks.error_message` 中的长堆栈后续应拆到任务事件/错误详情表，主表仅保留摘要和错误码。
- publisher 完整 `result` UPDATE 偶发约 1.4 秒，后续应把字幕/渲染明细拆为产物表或文件引用。
- Web 新 PID 上线后运行 `python3 scripts/dev/analyze-publication-task-queries.py --minutes 60`，专项分类目标为 `json_predicate=0`、`multirow_payload=0`、失败 SQL=0；未达到前状态不得改为“已解决”。
