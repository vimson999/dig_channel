# 研报横屏关键帧投影再次被核心写入器绕过

## 基本信息

- 状态：两个 Core 写入器已修、4 条现场数据已回填、结构门禁归零，待新 worker 真实任务验收
- 严重级别：P0
- 影响模块：report audio / publication_tasks / 媒体下载 / 启动门禁
- 首次发现：2026-07-23 启动结构门禁
- 修复提交：当前工作区
- 根因分类：宽字段写入合同遗漏、批量更新绕过 ORM 事件
- 复发键：`ASSET-PROJECTION-CONSISTENCY`
- 是否复发：是；2026-07-21 同类问题宣称修复后再次产生新漂移
- 关联记录：`2026-07-21-media-projection-bulk-update-drift.md`、`2026-07-22-pgbouncer-cold-pool-connect-timeout.md`
- 防线层级：shared_write_contract / runtime_gate / repair_tool / contract_test

## 现象

- 4 条 2026-07-23 新完成的 `report_pdf_audio_workflow` 中，JSON 的 `video.keyframes.landscape_cover.path` 完整，但 `report_audio_landscape_keyframe_path` 为空。
- 横屏关键帧下载接口因此返回 404。
- `check-runtime-schema.py` 拒绝启动并报告媒体投影回填不完整。

## 影响

- 业务产物实际存在却无法通过轻量投影接口下载，用户看到假缺失。
- API 若为兼容而回退读取完整 `result`，会重新引入已治理的大 JSON 热查询。
- 一次性历史迁移会被后续新任务继续污染，造成“每天修、每天复发”。

## 根因

1. 模型 mapper 钩子已维护新列，但 Core worker 的 `Query.update()` 不触发 mapper 事件。
2. PostgreSQL 原子 `_update_result` 已同步竖屏、播客和 B 站投影，新增横屏列时漏掉该 SET。
3. 任务完成阶段的第二次批量状态更新又写封面等列，也漏掉横屏投影。
4. 维护修复脚本和测试白名单未随新列同步扩展。
5. 后端单独重启采用 ensure 模式，不会替换仍运行旧代码的 worker；写入版本与读取版本可能不一致。

## 解决思路

- 不依赖 ORM 事件兜底，所有改变 JSON 来源的 Core 写入器必须在同一事务同步投影。
- 修复脚本、启动门禁和测试使用同一个来源路径语义，任何新漂移阻断启动。
- 完整重启必须替换全部 worker，不能用后端监听成功代替代码版本已部署。

## 实际方案

- PostgreSQL `_update_result` 在合并 payload 后同步 `report_audio_landscape_keyframe_path`。
- 最终任务 `Query.update()` 从 `video.keyframes.landscape_cover.path` 同步写入该列。
- SQLite/兼容写入白名单和维护修复脚本补齐 landscape 字段。
- 修复脚本精确回填 4 条漂移记录并保留原 `updated_at`。
- 增加两个 Core 写入器、修复脚本和模型行为的契约测试。

## 验证

- 修复前精确识别 4 条记录，且都只漂移 `landscape`。
- `--apply` 更新 4 行，第二次审计 `media projection mismatches: 0`。
- 运行时结构门禁返回 `OK: runtime schema contract 20260722_zsxq_topic_payload_hash is complete.`
- 完整确定性回归 `819 passed, 5 skipped, 42 subtests passed`。
- 2026-07-23 13:42 完整重启已替换后端和全部 Celery worker；重启后再次审计仍为 `media projection mismatches: 0`。
- 待完成：由新 worker 完成至少一个包含横屏关键帧的新任务后，再次审计必须仍为 0。

## 复发预防

1. 新增投影列必须同时枚举 ORM、Query.update、原生 SQL、重试、完成收口、维护脚本和启动门禁。
2. 任何 JSONB patch 的契约测试必须断言所有结构化投影的 SET，而不是只断言旧字段子集。
3. 历史回填归零只算数据修复；新 worker 真实写入后仍归零才算入库修复验收完成。
4. 后端单独重启不代表 Celery 代码已更新；涉及 worker 写入逻辑必须走完整安全重启。
5. 启动门禁发现新数据漂移时必须重新打开原复发簇，禁止把它解释成“迁移没跑”。

## 后续事项

- 完整重启后跑真实 report audio 任务，并验证 landscape 下载 200 与投影审计归零。
