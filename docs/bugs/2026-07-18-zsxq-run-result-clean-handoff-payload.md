# ZSXQ 批次结果被清洗与资产扫描重复搬运

## 基本信息

- 状态：代码已修改，数据库迁移与新进程真实全流程待验收
- 严重级别：P0
- 影响模块：ZSXQ 同步 / 内容清洗 / 研报资产扫描 / 工作流编排 / PostgreSQL
- 首次发现：2026-07-18 第 106 轮真实系统同步
- 修复提交：未提交
- 根因分类：审计 JSON 同时承担热路径关系表、终态投影缺失、重复结果嵌套
- 复发键：`ZSXQ-RUN-RESULT-DOWNSTREAM-PAYLOAD`
- 是否复发：同类数据库大字段热路径第 4 次修复
- 关联记录：`2026-07-16-zsxq-expired-orm-full-row-refresh.md`、`2026-07-18-publication-task-progress-json-write-amplification.md`
- 防线层级：shared_guardrail / schema_migration / contract_test / observability

## 现象

2026-07-18 14:06 重启后的第 106 轮真实工作流完整成功，但收尾窗口仍出现三个大字段读取：

- 清洗交接按主键读取 `zsxq_sync_runs.result`，耗时 2.075 秒。
- 研报资产扫描再次读取同一字段，耗时 1.438 秒。
- 清洗终态轮询批量读取 `publication_tasks.id/result`，被专项监控归类为 `multirow_payload`。

现场载荷测量显示该同步运行的 `result` 为 224142 bytes；工作流 `stage_results` 为 49190 bytes，其中同步摘要 42648 bytes，`auto_script_material_dispatch` 单项 40431 bytes。相同主键查询的数据库执行计划仅 0.017-0.037ms，说明秒级耗时不是缺少主键索引，而是大 JSON 的 TOAST 读取、传输和同期连接抖动。

## 影响

- 每轮下载完成后，清洗、资产扫描和工作流收尾会重复搬运同一份 200KB 级审计 JSON。
- `publication_tasks` 清洗轮询随文件审计明细增长，重新形成批量载荷查询。
- 工作流又把自动分发的 `config/items` 嵌入同步摘要，重复保存已存在于同步结果和子任务表中的明细。
- 继续增加索引不会减少返回字节，也不能防止新批次按旧结构入库后复发。

## 根因

1. “本次同步下载了哪些附件”只存在于 `zsxq_sync_runs.result.downloaded_files`，缺少结构化的批次到附件关系。
2. 清洗任务终态只有完整 `result`，工作流为取得输出目录和计数只能读取 JSON。
3. `content_pipeline` 使用 bulk UPDATE，ORM 投影钩子不会运行；只加模型列不足以保证新数据从入库端同步维护。
4. 工作流同步摘要复制了自动分发的配置和逐项明细，没有载荷预算。
5. 前三轮主要消除了过期 ORM、重复加载和轮询宽行，但仍把 JSON 当作“批次文件关系”的唯一事实来源，修到查询端而没有彻底修入库端。

## 解决思路

以“入库事实 -> 结构设计 -> 历史迁移 -> 查询切换 -> 真实验证”为固定顺序：

1. 下载成功时，在同一事务写入批次文件台账。
2. 清洗终态写完整 JSON 的同时维护有界投影列。
3. 历史批次从 JSON 回填台账，历史清洗任务回填投影。
4. 清洗、发布交接和资产扫描只读结构化事实；旧批次台账覆盖不足时才允许单行兼容回退。
5. 工作流只保留分发状态、计数、错误和有界任务 ID，不再重复保存配置与 items。

## 实际方案

- 新增 `zsxq_sync_run_downloads`，以 `(run_id, attachment_id)` 为主键，保存批次、附件、类型和本地路径；`zsxq_sync_runs.download_ledger_complete` 明确区分新结构完整批次与旧 JSON 兼容批次。
- 系统下载成功、人工计划下载成功或明确复用时，同一数据库事务写附件状态与台账；`ON CONFLICT` 使重试幂等。
- 新增 `content_clean_output_dir/files_count/success_count/failed_count/message` 五个投影列。
- ORM 写入和 `content_pipeline` bulk UPDATE 两条入库路径都同步维护五个投影，完整文件数组仍保留在 `result`。
- 清洗轮询和 clean-to-publish 交接只读取状态与五个投影，不再批量读取 `PublicationTask.result`。
- 清洗提交和研报资产扫描按 `run_id` 一次读取最多 100 条台账并在内存筛选扩展名；台账覆盖完整后，即使文件被删除也不会退回读取大 JSON。
- 新批次只有在成功终态提交时才原子设置 `download_ledger_complete=true`；旧批次由迁移按 JSON 项、结构化计数和台账覆盖三方核对，不能证明完整时按主键读取一次旧结果，保证历史业务完整性。
- `content_pipeline` 子结果使用 PostgreSQL JSONB 原子补丁，只更新 `result.content_pipeline.<key>`，避免先读再重写 224KB 文档并防止 PDF/音频结果互相覆盖。
- 工作流自动视频/文案分发摘要删除重复 `config/items`，保留状态、计数、错误、最多 20 个任务 ID 和 PDF/MP3 桶计数。
- 新增 DBeaver/Alembic 迁移、历史回填、权限授予、结构校验和可重复执行验证。
- DBeaver 脚本使用显式事务和 `SET LOCAL` 超时，任一 DDL/回填失败时整单回滚，禁止留下半迁移结构。
- 删除与规范索引定义完全相同的 `ix_publication_tasks_parent_created_asc` 和 `ix_publication_tasks_source_created_asc`；保留 Alembic 管理的无后缀索引，停止用重复索引掩盖远程链路抖动。

## 验证

- 第 106 轮业务基线：同步 78 个 topic、下载 1 个文件、清洗成功、发布成功并确认可见、资产扫描成功；无 statement timeout、QueryCanceled 或业务失败。
- 现场 `pg_column_size`：同步结果 224142 bytes，工作流阶段结果 49190 bytes，自动文案分发摘要 40431 bytes。
- 服务端 `EXPLAIN (ANALYZE, BUFFERS)`：工作流主键查询 0.017ms，同步运行主键查询 0.037ms，附件查询 0.036ms。
- 2026-07-18 14:06 新进程后的基线共有 8 条 1 秒以上 SQL、0 条业务数据库错误、0 条 statement timeout；其中 2 条读取 `zsxq_sync_runs.result`，另有 1 条未过 1 秒但被纪律探针捕获的清洗批量 `publication_tasks.result` 查询。
- 同一现场只读执行计划：业务任务监控 0.068ms、同步任务监控 0.024ms、父子任务关联 0.142ms；这些轻量查询的偶发 1 秒日志没有索引缺口证据。
- 历史回填预检：12,654 次下载数组条目去重为 3,936 个运行-附件事实；全部带 `attachment_id`，精确关联成功 12,654 次，缺失 0。
- 21 个已完成旧运行的记录下载数大于可证明 JSON 明细，最新创建于 2026-06-12；迁移会保守保留 `download_ledger_complete=false` 和单行兼容回退，不伪造明细。2026-06-12 之后的新批次不受该历史缺口影响。
- 89 项聚焦测试及 225 项相关扩展测试通过，覆盖终态投影、bulk UPDATE、批次台账入库、PDF/音频连续原子补丁、历史兼容和禁止批量大 JSON 查询。
- 完整单测为 526 passed / 4 failed；4 项均不在本轮 DB 改造路径：1 项依赖未启动的 localhost:5000，3 项为仓库既有调度器/通知文案实现与旧测试期望不一致。
- 当前尚未执行生产迁移、尚未重启相关进程、尚未产生新结构真实批次，因此状态不能提升为“已部署”或“已解决”。

## 复发预防

1. 会被下游按批次、附件、产物或子任务消费的集合，必须在源头写结构化关系表，不得只存在于审计 JSON。
2. ORM 钩子和 bulk UPDATE 必须分别有入库测试；只验证模型构造不能代表 worker 写入正确。
3. 兼容回退必须有明确覆盖判据。已有完整台账时，目标文件缺失或目标类型为空都不得重新读取大 JSON。
4. 阶段摘要必须有字节预算；数据库审计、Celery 消息和工作流摘要不得复制同一逐项明细。
5. `multirow_payload` 出现 1 条即重新打开本记录，不受 1 秒慢查询阈值豁免。
6. 验收必须覆盖同步、清洗、发布、资产扫描及结束后 3 分钟，且核对业务状态、产物和通知。
7. 新增热索引前必须先查等价定义、`pg_stat_user_indexes` 和真实执行计划；定义完全相同的索引一律视为写放大缺陷。
8. 跨所有者结构迁移必须由管理员在单事务内完整执行；禁止用应用用户先改一半，再等待另一半权限。

## 后续事项

- 在无活动任务时执行 `docs/sql/content_pipeline_run_download_projection_dbeaver_20260718.sql`。
- 核对五列、台账覆盖、应用权限和历史工作流载荷验证结果。
- 安全重启 backend、Celery 与 scheduler，以新 PID 跑一轮完整真实工作流。
- 新批次要求：`multirow_payload=0`，清洗/资产扫描读取 `zsxq_sync_runs.result=0`，完整业务结果不变。
