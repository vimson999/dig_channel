# 分镜提交成功后断连被误写为失败

## 基本信息

- 状态：新 backend/worker 已加载，指定任务端到端验收完成；`RUNTIME_REPLACED/OBSERVING`，待下一巡检窗口确认不复发
- 严重级别：P0
- 影响模块：火柴人视觉视频 / 标题与正文分镜 / PostgreSQL / 父子任务状态机
- 首次发现：2026-08-07
- 修复提交：当前工作区
- 根因分类：数据库 ambiguous commit、失效 Session 恢复、父子状态投影分裂
- 复发键：`ORCH-PARENT-CHILD-STATE`
- 是否复发：是；已有纪律要求数据库异常后先释放 Session 并用 reconciler 收口，但该分镜写入器仍把提交结果不确定直接映射成业务失败
- 关联记录：`2026-06-24-report-top10-failure-marker-pending-rollback.md`、`2026-07-29-stick-scene-media-required-manual-recovery.md`、`2026-08-06-stick-image-expired-orm-browser-session.md`
- 防线层级：shared_guardrail / contract_test / runtime_verification

## 现象

- workflow `826dcf81-d2a7-4906-abed-221418d019ce` 页面上半区显示 36 个分镜、正文覆盖完整，素材区却返回 409 并提示“请先完成标题与正文分镜”。
- 子任务 `68d9acc2-2e0d-45b7-a967-370124a659c6` 与父任务都被标成 failed，错误为 `Can't reconnect until invalid transaction is rolled back`。
- 数据库实际已有 1 条完整 output、36 条连续 segment 和 36 条 scene asset；但图片状态全部 pending，图片文件 0/36。

## 影响

- 用户被误导为分镜没有完成，可能再次请求 Gemini，产生重复外部调用和不同版本分镜。
- 已持久化的正确分镜无法进入图片生成，整个视觉视频工作流停在 35%。
- 页面分镜 API 与素材 API 使用不同成功判据，造成同一任务同时显示成功与失败。

## 根因

`_persist_success` 在一个事务中写 output、segments 和 completed 状态。PostgreSQL 端已经提交成功，但客户端连接在确认提交结果时失效；外层异常处理没有先在新 Session 中核对持久化结果，而是调用 `_fail`，把子任务和父任务覆盖为失败。素材 API 又只接受 `PublicationTask.status=completed`，没有验证已经存在的完整产物，因此拒绝真实可用分镜。

## 解决思路

- 以持久化 output 和 segments 的完整性为唯一可恢复成功证据，不以一次 commit 返回值或任务投影字段作为最终真相。
- 数据库异常后先 rollback/remove，再用新 Session 核对；只有来源字数完整覆盖、segment 数量匹配、序号从 0 连续且文案/提示词非空，才允许恢复成功。
- 恢复必须幂等，复用原分镜与已有资产行，只投递 pending 图片，不重新请求 Gemini。

## 实际方案

- 新增共享 `durable_storyboard_integrity` 契约，供 worker、复用入口和素材 API 使用。
- `_process_job` 与 normalize 续跑在持久化异常时先执行 durable reconciliation；验证通过后恢复子任务 completed、父任务 storyboard_done，再继续自动图片投递。
- 分镜复用入口和 `_completed_storyboard` 不再只信 task.status；失败投影下的完整 durable storyboard 可被复用，不完整缓存仍会被清除并返回 409。
- 新增完整/缺段/断序/空提示词和失败投影缓存的回归测试。

## 验证

- 分镜与素材定向测试 85 tests 通过。
- DB 投影、Session 生命周期和分镜缓存测试 87 tests 通过。
- 完整 unit 1,052 tests/41.270s 全部通过；新增文件及相关模块 compileall、`git diff --check`、recurrence registry JSON 和 Bug 严格检查均通过。
- 历史任务使用新代码执行幂等 reconciler 后，子任务恢复为 completed/done/100%，父任务继续自动图片与视频阶段；没有重新请求 Gemini、没有重建或覆盖已有分镜。
- 36 个场景图片任务全部完成，磁盘存在 `000_title.png` 与 `001.png`–`035.png` 共 36 个文件；序号 7 曾出现一次“等待 Gemini 生成图片超时”，自动重试后成功，不再缺图。
- 安全门禁确认当前无不可中断任务后完成开发栈重载；新 backend PID 45237，health healthy，两个 visual-plan worker 及相关 worker 均 ready。
- 重载后的真实页面验收为“全部完成”100%、图片 36/36、正文对齐 35/35；最终视频 308.2 秒、1280×720、8.2 MB，已加载图片端点均返回 200，原“请先完成标题与正文分镜”提示及素材 409 消失。

## 复发预防

- 任何“写产物并标记成功”的提交遇到断连时，禁止直接写业务 failed；必须先用新 Session 验证 durable completion contract。
- API、worker、复用入口必须共享同一产物完整性定义，禁止分别以任务状态和产物存在性推断成功。
- 历史修复器必须幂等，并验证不会重复调用 Gemini、不会重建或覆盖现有分镜。

## 后续事项

- 指定任务的历史对账、36/36 图片、素材 API、页面和最终视频运行态验收已完成。
- 下一完整巡检窗口按该任务 ID、`Can't reconnect until invalid transaction is rolled back` 指纹和素材 409 端点复核；在后续窗口确认无同类 ambiguous commit 假失败前保持 `RUNTIME_REPLACED/OBSERVING`，不标记 CLOSED。
