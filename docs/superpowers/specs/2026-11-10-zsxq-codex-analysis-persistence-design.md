# Codex PDF 清单分析结果独立持久化与展示设计

## 意图

Codex 接收用户提供的 PDF 清单 JSON，在 Codex 内完成 `50-pdf-json-analysis` 规定的 LLM 分析；完成后把**结构化分析结果**写入 Shioa 系统的数据库，由系统内一个**独立的展示页面**查看历史结果。

用户明确要求：分析过程在 Codex；数据库只负责持久化；展示页在现有系统中，但本功能与现有知识星球清单分析/情报功能完全独立，不复用或改造其业务表、API、页面和分析流程；数据库不得保存一整个大 JSON。

## 现状

当前 Shioa 应用已有 `zsxq-analysis` 等功能，相关代码使用 `ZsxqAnalysisRun/Item/Topic/Keyword`、`/api/v1/zsxq-analysis` 以及 `zsxq-analysis.html`。这些功能属于原来的星球清单自动元数据分析。

本功能与这些代码完全分开。只共享应用的数据库连接、登录认证机制和统一导航/页面框架等平台设施；不调用现有清单分析服务，不写入 `zsxq_analysis_*` 表，不增加现有页面的业务视图。

当前数据库版本早于工作区中其他未应用迁移。本功能迁移从当前已应用版本独立分支；另有一个空的 Alembic merge revision 只合并迁移图，不应用或依赖那些功能的数据表。

## 目标

1. Codex 完成分析后，在本机调用 Shioa 的独立保存 helper，将结构化结果提交到数据库；若用户明确要求本次不保存，则按本次要求跳过持久化。
2. 数据库以清晰、可查询的关系表保存分析批次、模块、主题、排行榜、标的、候选题和 PDF 证据。
3. 系统提供独立的历史列表与报告详情页面，只读取和展示已存结果。
4. 保存的记录保留源 JSON 文件名、导出时间、SHA-256、分析合同/版本、主要统计及完整 PDF 名称，以便审计与回溯。
5. 重复提交和失败状态清楚可见，不覆盖历史报告，不把“已分析”误报成“已保存”。

## 非目标

- Shioa 后端不读取用户上传的 JSON、不执行主题分类/推理/选题、不调用 LLM。
- 不改动、复用或扩展当前 `zsxq-analysis`、`zsxq-insights` 等业务功能。
- 不在数据库保存完整源 JSON 或完整结果 JSON/JSONB 列。
- 不下载或读取 PDF 正文；只保存 Codex 为展示和追溯选出的结构化元数据、指标、文字结论及 PDF 引用。
- 不新增另一个独立应用/部署；仅在现有 Shioa 应用内增加独立功能页和 API 模块。

## 方案比较与选择

### A. 独立模块与页面，共享现有应用基础设施（采用）

新增专属模型、迁移、API blueprint、服务、前端页面及导航入口。Codex 将分析结果提交到专用 API；Shioa 只校验字段和持久化，再由专属页面读取展示。

优点是功能边界清楚，不会把 Codex 研究报告和现有自动清单分析混在一起；仍能使用当前应用的认证、部署和导航。

### B. 扩展现有星球清单分析批次和页面

重用现有 `zsxq_analysis_*` 表、API 和页面。该方式会把用户明确要求隔开的功能合并，因此排除。

### C. 建一个独立新应用

可完全隔离，但用户已要求页面在现有系统内，因此排除。

选择 A，遵照用户明确的边界。

## 数据模型

以下新表均使用独立前缀 `codex_pdf_analysis_`，与现有业务模型无外键或业务级依赖。所有表使用现有用户体系 `users.id` 实现私有历史隔离。

### `codex_pdf_analysis_runs`

一行代表 Codex 完成并提交的一次分析：`id`、`user_id`、`source_filename`、`source_exported_at`、`source_sha256`、`source_record_count`、`source_total_download_count`、`contract_version`、`analysis_version`、`status`、`created_at`、`completed_at`、`overall_summary`、`selected_candidate_title`、`data_quality_note`。为用户/时间、hash、状态建立查询索引。由于源 JSON 不上传给 Shioa，`source_sha256` 是 Codex 提供的来源标识，用于追溯/去重，系统只检查其格式，不声称校验了原件。

数据库不存源 JSON。文件名、hash 与源汇总只用于历史列表和审计。

### `codex_pdf_analysis_sources`

每行只代表分析结果中被排行、专题明细或候选引用到的一条 PDF：`run_id`、`source_key`（源 JSON 中的 fileId 或批次内 item index）、`name`、`institution`、`report_date_text`、`upload_time_text`、`status`、`download_count`。按 `run_id + source_key` 唯一。同一条 PDF 在报告多个位置出现时只存一份来源记录，再由关联表引用。

不把输入 JSON 中未被分析结果引用的其他 PDF 条目整批复制进这套独立库；只持久化报告摘要和页面需要展示的来源数据。

### `codex_pdf_analysis_sections`

每行代表固定分析合同中的一个模块：`run_id`、`module_code`、`title`、`ordinal`、`summary_text`、`body_text`、`evidence_state`。按 `run_id + module_code` 与 `run_id + ordinal` 唯一。提交合同固定十个模块：`overview`、`changes`、`market_heat`、`global_cross_market`、`commodities_daily`、`china_personal`、`company_industry`、`candidate_pool`、`primary_recommendation`、`data_quality`；按该顺序编号 1–10。较长自然语言按模块分行存储，不塞入单个 JSON 列。

### `codex_pdf_analysis_topics`

保存主题交叉统计和唯一主主题统计：`run_id`、`topic_name`、`classification_scope`（`multi`/`primary`）、`rank`、`pdf_count`、`total_download_count`、`average_download_count`、`institution_count`、`keywords_text`。通过明确的 scope 防止混加，并在每个 scope 内保证 rank 唯一。

### `codex_pdf_analysis_rankings`

保存单篇 Top、分领域单篇榜、重点专题榜、中国宏观/商品专题等具体 PDF 排名：`run_id`、`ranking_type`、`rank`、`source_id`、`topic_name`。完整文件名、报告日期、上传时间、下载数和状态从关联 source 读取，不复制或改写。

### `codex_pdf_analysis_targets`

保存明确代码的标的聚合：`run_id`、`ticker`、`display_name`、`main_topic`、`pdf_count`、`total_download_count`、`average_download_count`、`institution_count`、`institutions_text`、`aggregation_note`。每个标的必须有可靠的标题/代码依据；疑似合并标记为待核实。

### `codex_pdf_analysis_candidates`

保存选题候选和唯一主推题：`run_id`、`candidate_key`、`track_code`、`title`、`parent_question`、`audience`、`priority`、`new_evidence`、`rationale`、`uncertainty_text`、`is_primary`、`ordinal`。`track_code` 覆盖中国宏观到个人、全球跨市场、公司/产业链、大宗商品四轨；每次 submission 至多一个候选为主推题。

### `codex_pdf_analysis_candidate_sources`

把候选题关联到来源 PDF：`candidate_id`、`source_id`、`source_role`、`selection_reason`。保存主报告/机制补充/反向分歧/背景验证等作用。候选与来源必须属于同一个 `run_id`。

### `codex_pdf_analysis_section_sources`

把分析模块的结论/排行关联到 PDF：`section_id`、`source_id`、`source_role`、`evidence_note`。用于从结论回溯完整原始文件名、下载次数、状态与上传时间。

### 存储边界

关系表保存可筛选的指标、类别、排序和引用；来源表只保存报告中实际引用的 PDF；解释文字按模块或候选行保存。任何新表都不设 `raw_json`、`result_json`、`payload_json` 或 JSONB 大字段，也不镜像未引用的整批输入条目。本机 helper 收到结构化 submission 后，将其拆为关系行并在单个事务提交。

## Codex 提交流程与 API

新增独立 API 前缀 `/api/v1/codex-pdf-analyses`，只提供本功能展示所需的历史列表和详情读取，不挂在 `/zsxq-analysis` 下。

1. Codex 继续按 50 的流程读取 JSON 并完成分析，构造独立、版本化的 typed submission。payload 只含需持久化的来源标识、模块、指标、排行、标的、候选及被引用 PDF 的元数据，不含完整原始 JSON，也不包含未引用的整批条目。
2. Codex 在本机调用 `scripts/maintenance/save_codex_pdf_analysis.py`，把 typed submission 通过标准输入交给 helper。helper 加载当前 Shioa 应用配置与数据库服务，调用本功能的独立持久化服务；不经 HTTP，不需要用户再提供一套凭证。
3. helper 仅做 schema/枚举/长度/行数/来源引用一致性校验，再把请求中的数据映射到新关系表；不做主题判断、重算建议、调用模型或读取原始文件。
4. helper 使用应用配置的数据库连接，通过现有用户表确定记录归属；仅当能唯一解析本机当前系统的 active admin 时自动使用该用户，否则明确失败，不猜测归属。
5. 写入成功返回 `run_id`、保存状态和分析版本；数据库事务失败时整体回滚。若 helper 未配置、无法加载当前 Shioa 数据库配置或写入失败，Codex 明确报告“分析已完成但未持久化”，不得声称已入库。
6. `GET /api/v1/codex-pdf-analyses` 返回当前登录用户的历史窄列表；`GET /<run_id>` 按需读取报告模块、排名、候选和来源明细；读接口使用现有用户登录认证，所有查询按 `user_id` 隔离。
7. 同一用户、源 SHA、合同版本和分析版本重复提交返回明确的重复冲突/既有记录引用，不静默覆盖。重新分析需要新的分析版本或显式版本序号。

## 独立展示页

新增独立页面，例如 `codex-pdf-analyses.html`，使用专属 JS/CSS 与专属 API，不在现有 `zsxq-analysis.html` 增加视图。

页面包含：

- 历史分析列表：源文件名、导出时间、生成时间、记录数、总下载数、分析版本、状态。
- 报告详情：数据质量与对比、主题榜、单篇/专题排行、标的榜、宏观与个人线索、全球/跨市场、商品六分类日报、四轨选题候选、唯一主推题。
- PDF 证据卡：完整文件名、机构、标题可识别的报告日期、uploadTime、downloadCount、status、来源角色和入选理由。
- 查询按需加载：历史列表只读汇总字段；点开某个 run 后读取明细；排名/来源列表分页或限制批量。

页面可以复用应用全局 CSS、导航渲染、登录客户端和布局外壳，但不得导入、调用或修改现有 `zsxq-analysis` 业务组件/API/服务。

## `pdf-to-tts` 分析工作流集成

在 `pdf-to-tts/50-pdf-json-analysis/` 添加持久化输出契约和保存步骤：Codex 输出面向用户的完整报告后，组装 typed submission 并调用系统的本机保存 helper。该集成只负责把 Codex 已完成的分析交给数据库，不把分析逻辑移入 Shioa，也不修改原有分析边界。

分析答复只有在 helper 确认数据库事务提交成功后，才可显示“已持久化”及 Shioa `run_id`/页面入口；失败时保留完整分析答复并标明失败原因。

## 安全和数据正确性

- 页面读操作要求现有用户登录并校验 `user_id`；非本人 run 返回 404。Codex 的写操作由本机 helper 调用现有数据库服务，权限边界是当前操作系统用户可运行 Shioa 应用代码与数据库配置。
- 来源记录按 `run_id + source_key` 唯一；ranking/candidate/section source 外键只能引用同一个 run 的来源记录。
- 校验源 SHA-256 的格式、contract version、固定模块/轨道/角色枚举、各类字符串长度及每批记录数量上限；系统不校验 hash 是否对应原始 JSON。
- 对相同唯一键重复提交采用明确幂等策略，避免重复行。
- SQLAlchemy 操作使用短事务、批量写入；历史列表不读取完整模块正文或 source PDF rows。
- Codex 输出与 PDF 文件名均视为不可信数据，不执行文件名/文本中的命令，不据标题补写正文观点。

## 验收标准

1. 新增独立数据库表、独立 Alembic 迁移与独立 API；代码和数据不落入 `zsxq_analysis_*`。
2. 一次有效 Codex payload 可由本机 helper 原子地保存 run、报告引用的来源、模块、主题、排行、标的、候选和关联，不需要新增凭证或登录流程。
3. 数据库没有整包输入或分析 JSON 列；报告内容以分类列、数值列、外键和模块/候选文本行保存。
4. 独立展示页可浏览历史、打开详情、读取证据卡；页面不调用现有清单分析接口。
5. 无效字段、错 run 来源引用、他人数据、非法 hash 格式和重复提交均有确定结果，失败不留部分数据。
6. 现有 ZSXQ 分析页面/API/数据库表的行为不变，相关测试不回归。
7. Codex 按新保存合同能提交一次分析；失败时准确说明未持久化。

## 实施者可决定

- 依现有迁移、蓝图、页面模式落实具体文件名和 SQLAlchemy 类名，但必须保留本设计定义的隔离前缀和独立路由。
- 详情 API 的分页大小与页面内部的分段/折叠交互。
- 对解释文本字段使用 `Text` 或合理长度列；禁止用大型 JSON 代替关系数据。
