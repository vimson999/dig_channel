# 媒体宽字段被批量 JSON 更新绕过并持续漂移

## 基本信息

- 状态：入库代码已修复、3 条现场数据已回填、结构门禁已通过，待新进程真实任务验收
- 严重级别：P0
- 影响模块：report audio / publisher / publication_tasks / 启动结构门禁
- 首次发现：2026-07-21 14:40 完整重启预检
- 修复提交：待提交
- 根因分类：批量更新绕过 ORM 事件、宽字段写入契约不完整、维护脚本类型假设
- 复发键：`ASSET-PROJECTION-CONSISTENCY`
- 是否复发：是；历史迁移完成后，新任务再次产生不一致
- 关联记录：`2026-07-10-report-audio-bilibili-cover-not-promoted.md`、`2026-07-18-bilibili-upload-json-polling-and-online-ddl-lock-convoy.md`
- 防线层级：shared_write_contract / runtime_gate / repair_tool / contract_test

## 现象

- 完整回归通过后，`restart-dev-stack.sh` 的启动前门禁仍拒绝启动，报告媒体封面投影不一致。
- 审计最终找到 3 条当天续跑完成的 `report_pdf_audio_workflow`：JSON 中竖屏、播客和 B站封面路径完整，但对应 3 个结构化列仍为空。
- 三条记录均由旧 `report_audio_workflow` worker 在 `14:35-14:43` 生成封面并完成，证明问题来自新数据写入，而非历史迁移遗漏。

## 根因

1. 模型的 `before_update` 会同步投影，但 `Query.update()` 和原生 SQL 不触发 ORM mapper 事件。
2. `_update_result` 为避免 SELECT-before-UPDATE 改用了 PostgreSQL JSONB 原子合并，却只写 `result`，没有在同一 SQL 写媒体投影列。
3. publisher 的批量投影 helper 覆盖竖屏和播客封面，但漏掉 `publisher_bilibili_cover_path`，存在同类潜在复发。
4. 历史回填和结构门禁能发现漂移，却不能替代入库维护者；只运行一次迁移必然会被后续错误写入再次污染。
5. 新增维护脚本第一次把 `publication_tasks.id` 假设为 `uuid`，目标列实际为 `varchar`。事务回滚避免了半修复，但暴露出脚本参数类型没有按数据库契约验证。

## 实际方案

- report audio 的 PostgreSQL JSONB 合并先在 CTE 生成最终 payload，再在同一 `UPDATE` 同步三个封面投影列。
- SQLite/兼容回退的批量更新显式从最终 result 计算相同投影，不依赖 ORM 事件。
- publisher 的批量结果投影补齐 `publisher_bilibili_cover_path`。
- 新增 `repair_publication_task_media_projections.py`：默认只审计，`--apply` 只修复当前不一致主键，保留 JSON、任务状态和原 `updated_at`。
- 修正维护脚本主键数组为 `text[]`，并增加静态契约测试禁止退回 `uuid[]`。

## 验证

- 行为与纪律定向回归：`119 passed, 26 subtests passed`。
- 首次错误脚本事务未提交；修正类型后精确修复 3 行，复查 `media projection mismatches: 0`。
- `check-runtime-schema.py` 返回 `OK: runtime schema contract 20260718_bili_upload_exec is complete.`
- 待完成：新进程运行至少一个会生成三种封面的真实 report audio 任务，并确认结构门禁仍为 0。

## 复发预防

1. 宽字段的写入契约必须覆盖 ORM、bulk UPDATE、原生 SQL、重试和维护补写，不能把 mapper 事件当作唯一维护者。
2. 任何原子 JSONB patch 如果会改变结构化字段来源，必须在同一 SQL 基于合并后 payload 写投影。
3. 启动门禁持续检查“JSON 来源与结构化列相等”，一条漂移也拒绝替换进程。
4. 维护脚本必须从模型或 `information_schema` 验证 ID 类型，并有错误路径回滚和第二次归零复查。
5. 历史回填完成不等于入库已完成；新进程真实任务后仍需再次运行投影审计。
