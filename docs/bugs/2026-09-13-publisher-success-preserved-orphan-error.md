# Publisher 上传成功后保留孤儿清理错误

## 基本信息

- 日期：2026-09-13
- 状态：RUNTIME_REPLACED / OBSERVING
- 严重级别：P1
- 影响模块：Publisher upload-only / Watchdog orphan cleanup / 业务终态一致性
- 首次发现：2026-09-13 每日数据库性能巡检
- 修复提交：当前工作区
- 根因分类：并发终态写入、成功状态未清理旧错误
- 复发键：`ORCH-PARENT-CHILD-STATE`
- 是否复发：是；父子/修复器并发状态机已有防线，但 upload-only 成功分支没有完整终态合同
- 关联记录：`2026-06-24-publisher-processing-orphan-capacity-block.md`、`2026-09-11-publisher-queue-repair-visible-message-starvation.md`
- 防线层级：shared_guardrail / contract_test / runtime_acceptance

## 现象

固定窗口内 Publisher task `e9fc559e…` 的数据库终态是 `completed/done/100%`，但
`error_message` 仍为 Watchdog 写入的“orphan publisher auto-closed”。时间线显示清理器先写失败标记，真实
upload-only worker 随后成功完成；状态与错误字段互相矛盾。

## 影响

- 页面、告警和后续对账可能把成功上传误判为失败。
- 以 `status` 与 `error_message` 分别统计时会产生相互冲突的业务口径。
- 孤儿清理和真实 worker 的合法竞态会留下永久脏状态，无法靠重试或索引修复。

## 根因

`run_bilibili_upload_only` 的成功窄写会更新 `status/current_step/progress/result/completed_at`，却没有显式写
`error_message=None`。Watchdog 在上传期间留下错误后，后到的真实成功只覆盖状态字段，因此旧错误被保留。
完整 Publisher 成功路径已经清理错误，upload-only 分支遗漏了同一终态合同。

## 解决思路

成功写入必须是自洽的完整业务终态，不能依赖进入成功分支时错误字段为空。保持现有窄写与并发流程，只在
upload-only 成功事务中原子清除旧错误，并用真实执行测试注入竞争写入。

## 实际方案

- 在 `run_bilibili_upload_only` 成功 `_update_task_columns` 中加入 `error_message=None`。
- 扩展现有上传完整调用测试：Fake uploader 在返回成功前写入 orphan cleanup 错误，随后断言成功终态和空错误
  同时成立。
- 将“成功终态必须清旧错误”的竞态合同写入数据库 Session 生命周期纪律。

## 验证

- TDD RED：旧实现执行完整上传后仍保留注入错误，`assertIsNone(task.error_message)` 失败。
- GREEN：最小修改后目标测试通过；Publisher API 与 queue-repair 交叉套件 134/134 通过。
- 完整 unit：`Ran 1674 tests in 92.417s / OK`。
- 双安全门整栈重启完成，新 backend PID `46428` healthy，bootstrap 17/17、`errors=[]`；render worker
  running、queue 0。
- 2026-09-14 后窗出现两条自然 upload-only 最终成功但仍保留错误，未满足本记录的运行验收。它们都先被
  Watchdog 在约 90 秒误收口，已沿新 P0 记录修复上游孤儿判定；共享 nodename/消费者运行版本仍只作为待查
  拓扑线索，本记录继续 `OBSERVING`，不据此宣称成功清错已完成自然验收。

## 复发预防

- 所有 `completed`/`done` 写入必须同时清理旧 `error_message`，并保持在同一短事务中。
- 终态测试必须注入 watchdog/retry/cleanup 的竞争错误，不能只从干净初态覆盖 happy path。
- 每日巡检继续对账 `status='completed' AND error_message IS NOT NULL`，自然 upload-only 成功样本归零后再关闭。

## 后续事项

- 当前没有重启后的自然 upload-only 成功样本，保持 `RUNTIME_REPLACED/OBSERVING`。
- 下一完整窗口要求 completed+error 矛盾新增为 0，并核对真实 Bilibili 上传产物。

### 2026-09-15 后窗复核

固定窗 8 条结构化 Bilibili Publisher 任务均成功、带有效 BVID 且 `error_message=NULL`，但都是完整 Publisher
pipeline；本机唯一 upload-only 完成日志是 stale-message skip，不是自然投稿。因此“成功终态无错误”的全局
业务口径本窗正常，专属 upload-only 竞态分支仍缺自然样本，状态保持 `RUNTIME_REPLACED / OBSERVING`。
