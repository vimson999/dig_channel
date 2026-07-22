# 2026-07-16 ZSXQ 任务提交后过期 ORM 触发整行大字段刷新

## 基本信息

- 状态：第 117 轮完整业务成功；2026-07-19 第五轮补齐自动分发与双清洗提交后的过期对象刷新，已部署待下一自然轮次复验
- 严重级别：高
- 影响模块：ZSXQ 发布 / ZSXQ 同步 / 内容清洗 / 研报视频 / 平台凭据 / PostgreSQL
- 首次发现：2026-07-16 慢查询日志
- 修复提交：待提交
- 根因分类：ORM Session 生命周期、过期对象刷新、SELECT-before-UPDATE、热点大字段
- 复发键：`orm-expired-projection-refresh-after-commit`
- 是否复发：同类问题第 3 次进入生产路径
- 关联记录：`2026-07-13-mp3-hot-path-json-payload-regression.md`、`2026-07-10-report-audio-projection-and-parent-roundtrips.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

- `tasks/zsxq_publish.py` 完成长耗时发布后访问已提交的 `PublicationTask`，按主键查询仍刷新几乎全部宽字段，单次达到 61.82 秒。
- 2026-07-17 新 worker 验收时，发布后的显式“轻量重载”仍因列集合包含 `params/result` 达到 9.96 秒，证明只把隐式刷新改成显式查询仍未根治。
- ZSXQ 重复投递判断只需要 `status`，却读取 `zsxq_sync_runs.result` 和整行字段，单次达到 5.39 秒。
- topic 创建/更新在同步循环提交后读取 `run.id`，同样触发 `zsxq_sync_runs` 整行刷新，现场多次达到 1-5 秒。
- 平台凭据成功标记只需要更新时间和状态，却读取 `encrypted_payload` 等整行字段，单次达到 3.90 秒。
- 附件下载结果回写只需要状态、路径和展示标识，却通过 `session.get()` 读取 `raw_payload`。
- 2026-07-17 复核发现内容清洗进度回调仍在每次进度事件中读取 `PublicationTask.result`，两次主键查询达到 8.06 秒和 8.82 秒；近期清洗结果 JSON 最大约 132KB。
- 研报 PDF 解析在标记 `parsing` 并提交后继续读取同一个 `ReportVideoReport`，连续触发两次整行刷新；随后通过 `report.task` 回读含 `result` 的父任务整行，三次分别达到约 1.03 秒、1.02 秒和 30.24 秒。
- 2026-07-18 继续追踪发现三个同源漏口：自动视频/文案分发先加载附件 ORM 集合、循环内每创建一个任务就 `commit`；附件下载先加载全部附件 ORM、每处理一条就 `commit`；已有 topic 在附件提交后继续赋值。三条路径都会让“下一条”实体过期并隐式整行回读 `raw_payload`。
- 2026-07-18 13:03 的真实同步批次再次证明首轮验收结论过早：同步完成后，`sync_tabs` 的 `run.to_dict()`、研报资产扫描的两次 `session.get(ZsxqSyncRun)`、`downloaded_pdf_files_for_run()` 和工作流收尾分别读取同一份约 68KB `result`，单次达到 1.51-10.94 秒。
- 同一收尾链路提交 PDF 清洗任务后又调用 `PublicationTask.to_dict()`，触发包含 `params/result` 及全部宽投影的整行刷新，达到 1.02 秒；资产批次创建也在 commit 后组装响应，存在相同的下一次复发机制。
- 2026-07-18 13:37 新进程真实同步进一步暴露：PDF、音频清洗提交分别读取同一个 `zsxq_sync_runs.result`，两次为 1.38 秒和 1.49 秒；错峰资产扫描另有一次必要读取 1.51 秒。
- 同一批次的清洗轮询和发布轮询虽然仅耗时 54-55ms，但专项监控明确记录为 `multirow_payload`：前者读取 `params/result`，后者甚至读取 `publication_tasks` 全部宽投影。这两条未超过慢查询阈值，却是数据增长后的确定性复发入口。
- 2026-07-18 14:06 后第 106 轮完整成功，清洗/发布宽轮询大部分已消失，但仍各有一次 224142-byte `zsxq_sync_runs.result` 清洗交接与资产扫描读取，并残留一条清洗终态 `multirow_payload`；因此第三轮不能判为已解决。
- 2026-07-19 第 117 轮完整成功后又发现两个同源漏口：自动基础文案任务首次 commit 后回写 Celery ID，触发 `PublicationTask` 全宽刷新 3.87 秒；PDF 清洗提交 commit 后，第二个音频清洗读取过期 `workflow.id`，触发 `ZsxqWorkflowRun` 全行刷新 1.97 秒。

## 影响

这些查询都使用主键，增加普通索引无法解决。大 JSON、加密凭据和宽字段被反复从 PostgreSQL 搬到 worker，放大数据库 IO、连接占用和对象反序列化成本，并进一步拖慢本来已经使用轻量列的其他请求。

## 根因

1. SQLAlchemy 默认在 `commit` 后过期 ORM 对象。ZSXQ 发布执行外部流程后继续读取同一个任务对象，触发隐式整行刷新。
2. 状态标记和下载结果回写沿用了“先加载对象再修改”的通用 ORM 写法，没有按业务所需字段收窄。
3. 之前的投影纪律主要覆盖 `PublicationTask.query.get()` 和 MP3 路径，没有覆盖 `session.get()`、其他大字段模型以及 commit 后继续使用 ORM 对象的跨工作流模式。
4. 首轮防回归测试按已知 ZSXQ 路径编写，没有把“进度回调禁止 SELECT-before-UPDATE”提升为内容工作流的通用约束，因此同类机制在 `content_pipeline` 中被漏检。
5. `report_video` 的 PDF 解析仍沿用提交后继续操作 `report` 及其 `task` 关系的旧写法，没有进入前几轮以 `PublicationTask` 为中心的排查范围。
6. 首轮纪律只约束了“单个对象在 commit 后不得复用”，没有覆盖“预先加载的一组 ORM 对象在循环中 commit 后，尚未处理的后续对象也会全部过期”。这是本次同类问题反复出现的直接漏检点。
7. 首轮真实验收只观察到重启后短暂无慢 SQL，尚未等待一个完整同步批次就写入“13:01 后未见慢 SQL”；缺少“同步完成消费者扇出”这一独立验收阶段。
8. 同步运行结果同时作为数据库审计载荷、Celery 收尾消息和资产扫描输入，缺少一次物化、多消费者复用和消息体预算，导致同一 JSON 在结束瞬间被重复搬运。
9. 工作流把 PDF 与音频提交当成两个独立服务调用，各自在入口加载同步结果；阶段轮询则沿用 ORM 整体查询。之前只验收 1 秒以上慢 SQL，没有把低于 1 秒的 `multirow_payload` 查询形状作为失败条件。

## 解决思路

- 长耗时调用前只保留字符串 ID、工作流 ID 和必要参数快照，提交后主动脱离旧 ORM 对象。
- 长耗时调用完成后按明确列集重新加载任务，不允许继续访问过期对象。
- 纯状态写入改为单条 `UPDATE`，完全取消前置 `SELECT`。
- 主键终态判断只查询 `status`；附件回写只查询写入和结果组装需要的轻量列。
- 评论同步仍完整写入 `raw_payload`，但判断已存在记录时只读取 `id/comment_id`，不回读旧 JSON。

## 实际方案

- ZSXQ 发布任务初次读取一次参数/结果快照；启动、终态和失败状态全部直接 `UPDATE`，发布完成后不再执行第二次任务 SELECT。
- 后续附件状态同步和工作流通知只传任务 ID/工作流 ID，不再传已提交 ORM 对象。
- `mark_credential_success/error` 使用单条 SQLAlchemy bulk update。
- ZSXQ 发布凭据不再在“开始使用”和“最终成功”各写一次；数据库凭据只按最终成功或失败结果记账。
- `_terminal_delivery_result` 改为仅查询 `ZsxqSyncRun.status`。
- topic 创建、更新、人工计划和进度回写统一使用缓存 run ID/SQLAlchemy identity，不读取过期的 `run.id`。
- 两套附件下载流程统一通过轻量投影 helper 回写结果。
- `_upsert_topic_comments` 只读取评论主键标识，继续完整覆盖结构化列和 `raw_payload`。
- 外链文档回填移除循环中的 `PublicationTask.query.get()`，统一轻量列加载并在外部扫描前释放 session。
- 内容清洗进度只直接更新 `current_step/progress`；审计消息和 cleaner job 状态保存在任务内存快照中，完成或失败时与最终结果一次性完整落库。
- 内容清洗初次状态提交后不再使用 ORM task，自动发布交接只传字符串 task ID。
- watchdog 不再每分钟强制绕过 75 秒业务快照缓存；publisher 父任务清理在 SQL 中直接连接并筛选终态父任务，取消无效候选扫描和逐条父任务查询。
- 研报 PDF 解析在首次投影后复制 report/task ID、路径、文件名和 `raw_meta` 快照；解析前后的状态与结果均定向 `UPDATE`，父任务状态只查询子报告的 `parse_status`，失败保护也不再加载父子整行。
- 自动视频和基础文案分发改用 `with_entities` 不可变行快照，并把 sync run 转成只含 ID/组信息的 `SimpleNamespace`，循环内提交不会再使后续附件或 run 过期。
- 附件下载首轮只读结构化列；只有确实需要判型或下载的单个附件才延迟读取一次 `raw_payload`，可复用文件不再读取 JSON。
- 已有 topic 在附件提交前保存结构化快照，更新时构造投影对象并执行定向 `UPDATE`；外链扫描使用该投影对象，不再碰已过期的原 ORM 实体。
- 同步终态响应不再对过期 run 调用 `to_dict()`；改为只查询结构化公共列，并注入内存中已知的最终结果，不从数据库重复读取 `result`。
- 研报资产扫描通过 `downloaded_pdf_files_for_run()` 一次取得文件和 run 元数据快照；任务名复用同一快照，topic/date 候选只投影业务列，不读取 `raw_payload`。
- PDF/音频清洗提交在首次 flush 后、commit 前生成完整业务响应；入队和 celery ID 回写只传字符串 ID、使用定向 UPDATE，commit 后不再访问宽表实体。准入延期状态由 runtime guard 直接回传，业务状态没有被性能优化覆盖。
- ZSXQ 完成消息只携带计数、tab 摘要和必要子任务状态，不再把下载文件列表和重复 run result 发送到 finalizer/Redis；研报资产解析默认延后 90 秒，避免与工作流清洗、列表请求同时扇出。
- 清洗阶段建立单次运行上下文：PDF 首次读取并写回后更新上下文，音频复用更新后的结果；既消除第二次 `zsxq_sync_runs.result` 查询，也防止两次 `content_pipeline` 写回互相覆盖。
- 清洗与发布轮询统一先读取结构化状态 `Row`。清洗只在全部终态时读取已完成任务的 `result`；发布优先使用运行时对账和已持久化阶段摘要，仅在旧工作流兼容、运行时恢复或运行时摘要缺失时按主键读取一次必要载荷。
- 清洗完成到发布提交直接复用工作流中的 `local_output_dir/file_type`，新流程不再回查清洗任务；旧版本进行中的工作流保留单任务兼容读取，避免性能优化牺牲发布完整性。
- 发布恢复投影补齐 `feature/source_task_id/updated_at`，防止看似使用 `load_only` 却因下游访问漏列再次触发隐式整行刷新。
- 第四轮新增 `zsxq_sync_run_downloads` 批次文件台账；下载状态与台账在同一事务入库，清洗和资产扫描改读有界关系行，不再从同步审计 JSON 推导文件集合。
- 清洗终态新增五个普通列投影，ORM 和 bulk UPDATE 均同步维护；清洗轮询、发布交接不再批量读取 `PublicationTask.result`。
- PDF/音频清洗结果改为数据库端 JSONB 子文档原子补丁，保留其余审计字段并避免两个结果相互覆盖；工作流自动分发摘要删除重复配置与逐项明细。
- 自动基础文案与 MP3 任务在首次 commit 前保存标量 task ID，入队失败和 Celery ID 回写改为按主键直接 `UPDATE`，绕过 mapper `before_update` 对过期 `params/result` 的访问。
- PDF 与音频清洗作为一个阶段提交时临时关闭当前 session 的 `expire_on_commit`，退出阶段立即恢复；子服务仍各自 commit，但父 workflow 不再在第二次提交和通知中整行刷新。

## 验证

- 相关 Python 文件通过 `py_compile`。
- ZSXQ 同步重试和发布失败保护共 10 项测试通过。
- 数据库投影纪律新增凭据、同步附件、评论和 ZSXQ 发布过期对象守卫。
- 2026-07-17 新增内容清洗进度直接更新、publisher 终态父任务 SQL 筛选和 watchdog 缓存守卫；相关投影/队列修复/看门狗测试 65 项通过。
- 2026-07-17 17:30 启动的新 `zsxq_publish` worker 暴露发布后第二次 JSON 重载，已升级为无 SELECT 终态更新，并新增真实状态流测试。
- 研报 PDF 解析新增成功/失败业务流测试及 SQL 捕获断言，确认只读取一次必要 `raw_meta`，不读取 `report_video_tasks.result`；与投影纪律测试合计 37 项通过。
- PDF 和音频清洗业务级测试确认：原有结果字段、进度消息、最终产物和工作流通知均完整保留。
- 远端库 `EXPLAIN (ANALYZE, BUFFERS)` 显示两个监控查询服务端执行时间分别为 0.075ms 和 0.025ms，偶发 1 秒属于远端链路或同期数据库压力，不继续为此盲目叠加索引。
- 2026-07-17 18:10 已滚动重启 `zsxq_sync`、`zsxq_workflow`、`report_video` 和 watchdog；新进程均 ready，首轮监控 SQL 为 27-116ms，18:10 后暂无慢查询或启动错误。内容清洗与 ZSXQ 发布 worker 也已分别在 17:51、17:57 加载新代码。
- 2026-07-18 新增 SQL 捕获业务测试，分别模拟分发循环提交、附件复用循环提交和 topic 更新前附件提交；断言附件仅有一次结构化 SELECT、可复用路径不读取 `raw_payload`、topic 更新不产生任何 `SELECT zsxq_topics`。ZSXQ/投影相关 53 项测试通过。
- 2026-07-18 13:01 完整重启后，16 个 Celery 服务、watchdog 和 2 个 scheduler 均启动成功，bootstrap `errors=[]`。但 13:03-13:07 的真实同步收尾出现 1.02-10.94 秒重复载荷读取，原“暂无慢 SQL”样本被判定无效，问题重新打开。
- 第二轮 SQL 捕获测试覆盖：进度 JSON 节流但结构化列实时更新、终态强制落库、同步公共响应不读 `result`、资产扫描仅一次 run result、topic 不读 `raw_payload`、PDF/音频提交后零次 `SELECT publication_tasks`、资产响应 commit 后零次刷新。定向测试 65 项通过。
- 13:36:51 新后端与 13:37 新 worker 完成真实第 105 轮：同步 78 个 topic、下载 3 个附件、工作流最终完成，未出现 statement timeout、QueryCanceled 或数据库操作失败；同时该样本把两次清洗结果读取和两条低耗时宽轮询重新判为未完成项。
- 第三轮 SQL 捕获测试证明：PDF+音频连续提交只查询一次 `zsxq_sync_runs.result`，两次写回均保留；清洗运行中不读 JSON、终态仅投影完成任务结果；发布运行中及运行时证据完整的终态均只查询一次状态列。聚焦 65 项、ZSXQ/清洗/资产/运行时相关 176 项测试通过。
- 第 106 轮实测同步结果 224142 bytes、工作流阶段结果 49190 bytes，而对应主键计划仅 0.017-0.037ms；第四轮 84 项测试证明新批次台账读取、终态投影、原子补丁、历史兼容和文件缺失不回退均符合契约。迁移和新进程负载尚未执行。
- 2026-07-19 第 117 轮完成同步、双清洗、双发布，最终 `completed/done`，活动任务为 0。该轮暴露的两条过期刷新已新增 SQL 形状和 session 生命周期测试；测试第一次确实复现全宽 SELECT，改为直接 UPDATE 后消失。
- 最终完整回归 `602 passed, 5 skipped, 37 subtests passed`。`zsxq_sync`、`zsxq_workflow`、`report_script_material_workflow`、watchdog 和后端均已滚动加载新代码。watchdog 两轮真实巡检 SQL 为 29-164ms；2026-07-20 00:04 最终边界后生产慢查询、statement timeout 和数据库错误均为 0。

## 复发预防

1. 任意外部网络、浏览器、媒体处理或长循环前必须结束数据库事务，只跨边界传标量 ID 和不可变快照。
2. `commit` 后不得继续读取 ORM 对象属性；需要继续操作时，按明确列集重新加载。
3. 只写状态的函数默认使用直接 `UPDATE`，禁止先读取 JSON、Text、加密载荷或宽行。
4. `session.get()` 不得用于包含 JSON、Text 或大量投影列的热点模型。
5. 每次新增 Celery 工作流必须通过数据库投影纪律测试，覆盖首次加载、长调用后的重新加载和失败收口三条路径。
6. 进度回调默认只能直接更新结构化状态列；需要保留的 JSON 审计信息在内存合并，任务终态一次落库。
7. SQL 慢日志必须结合服务端 `EXPLAIN ANALYZE` 判断。服务端执行低于 1ms 时，优先减少轮询和检查网络/连接等待，不以新增索引作为默认答案。
8. 禁止让 ORM 实体列表跨越循环内的 `commit/rollback`；批量候选必须先转成 `Row`、字典或 DTO。即使当前尚未访问的下一条实体，也会被 commit 过期。
9. 大 JSON 必须延迟物化：先用结构化列完成绝大多数筛选，仅对最终命中的单行按主键读取一次载荷。
10. 真实验收必须覆盖“主任务执行”和“完成后消费者扇出”两个窗口；只观察重启后空闲窗口不得写成已解决。
11. 一个结果被多个消费者使用时，生产者只能物化一次并传不可变紧凑快照；Celery 消息禁止携带数据库中已持久化的完整重复审计载荷。
12. 工作流阶段轮询必须以不可变状态 `Row/DTO` 为入口；同一阶段的大字段最多物化一次。低于 1 秒但被归类为 `multirow_payload` 的查询同样视为验收失败，不能等它变慢后再修。

## 后续事项

- 以第 118 个自然同步轮次审计自动基础文案创建、双清洗提交、发布和完成后 3 分钟窗口；不得用当前空闲窗口替代该负载验收。
