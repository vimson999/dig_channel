# 2026-07-13 MP3 热路径继续批量读取任务 JSON

## 基本信息

- 状态：代码已修复，待执行投影 SQL并重启验收
- 严重级别：高
- 影响模块：MP3 workflow API / MP3 worker / publisher reuse / report script material / watchdog
- 首次发现：2026-07-13 慢查询复盘
- 修复提交：待提交
- 根因分类：热点大字段、入库投影缺失、ORM 过期刷新、过滤下推缺失
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：同类 publication_tasks JSON 热路径再次复发
- 关联记录：`2026-07-10-report-audio-projection-and-parent-roundtrips.md`、`2026-07-10-celery-runtime-leak-and-mp3-db-roundtrips.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

- 2026-07-13 00:00 至 10:08，`publication_tasks` 去重后有 616 条专项记录、94 个查询签名。
- MP3 列表批量读取 `params`：86 次未超过 500ms，另有 63 次慢载荷，平均 2.97 秒、最大 62.41 秒。
- MP3 publisher 复用候选批量读取 `params/result` 34 次。
- MP3 worker 多次单条整行刷新，部分查询达到 63.71 秒。
- watchdog 在 09:56 对 pending publisher 单条载荷查询等待 112.77 秒后遇到数据库连接关闭。

## 影响

- 新入库 MP3 数据仍迫使列表从 JSON 还原音频摘要。
- 长任务提交后访问过期 ORM 父对象，导致同一任务阶段反复读取完整载荷。
- publisher 复用与 B站上传轮询把少量候选扩展为批量 JSON 传输。
- 数据库整体波动时，大载荷和重复往返放大页面与 worker 延迟。

## 根因

1. MP3 音频文件名、主题、标签、路径、下载次数和时长没有结构化投影。
2. publisher 子任务的角色、宽高比、字幕开关和音频签名只存在于 `params/result`。
3. MP3 `process_job` 首次提交后继续使用 `task.id`，触发 SQLAlchemy 对过期对象的隐式整行刷新。
4. 历史兼容路径继续扫描最多 300 条 MP3 `params`，使已完成回填无法成为硬边界。
5. watchdog 在判断 180 秒宽限期之前就加载完整修复载荷。

## 解决思路

将列表展示和 publisher 复用合同变为明确宽字段；新写入由模型事件同步，历史一次回填。候选阶段只读普通列，完整 JSON 仅允许单条详情、选中的 publisher 子任务和真正过期的 repair 任务读取。

## 实际方案

- 新增 6 个 MP3 音频摘要投影和 4 个 publisher 匹配投影。
- MP3 列表移除 `params`，由投影列构建与原字段一致的音频摘要。
- publisher 复用在 SQL 中按角色、宽高比、字幕开关和音频签名筛选，命中后才单条读取完整载荷。
- MP3 历史 `source_task_id` 从嵌套音频资产字段回填，并删除 JSON fallback 扫描。
- B站上传候选先查 ID，再按实际命中单条加载详情。
- MP3 长任务在第一次提交前保存 `task_key`，提交后不再访问父 ORM 的 `task.id`。
- 研报物料子任务保持 3 秒进度轮询，父取消状态独立为默认 15 秒轮询。
- watchdog 将 publisher 过期时间条件下推到候选 SQL。

## 验证

- 68 项投影纪律、模型投影、队列修复、研报物料 API/worker 测试通过。
- 相关模块通过 `py_compile` 和 `git diff --check`。
- 完整 MP3 服务测试 65 项中 62 项通过；3 项既有封面文案语义断言失败，与本次数据库路径无关，未在本修复中篡改预期。
- DDL/回填脚本已生成，但按部署纪律尚未在当前运行数据库执行，也未重启服务。

## 复发预防

- MP3 列表列集合禁止出现 `PublicationTask.params/result`。
- publisher 匹配候选列集合禁止出现 JSON；匹配条件必须在 SQL 中完成。
- 长任务第一次 commit 后禁止访问原始父 ORM 标量，使用提交前快照。
- 历史投影完成后不得长期保留批量 JSON fallback。
- repair 宽限期、状态和关联条件必须先在候选 SQL 中过滤。

## 后续事项

- 执行 `docs/sql/mp3_hot_projection_dbeaver_20260713.sql`。
- 使用可靠全栈重启脚本加载模型与查询代码。
- 以新 PID 为边界跑真实 MP3 列表与完整工作流，验收相关 `multirow_payload=0`、数据库错误为 0。
- 单独处理 3 项 MP3 封面文案测试基线，不与数据库性能修复混合。
