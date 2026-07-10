# 2026-07-10 Report Audio Gemini 完成后快照被父任务提交过期

## 基本信息

- 状态：已修复
- 严重级别：P0
- 影响模块：report audio workflow / Gemini child wait / SQLAlchemy session
- 首次发现：2026-07-10
- 修复提交：待提交
- 根因分类：数据库 Session 生命周期与父子任务状态快照
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：第 10 条关联事故
- 关联记录：`2026-06-23-report-audio-detached-session.md`、`2026-06-29-report-audio-doubao-child-detached-after-failure-record.md`、`2026-07-10-report-audio-gemini-defer-detached-id.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

Gemini 子任务数据库记录已经是 `completed/done/100%`，并保存了 3211 字最终返回；父编排却在 52% 失败，错误为：

```text
Gemini 子任务失败: 子任务状态异常: - / -
```

列表卡片通过真实子任务记录显示成功，父任务 `result.children.gemini` 却被写成空 `job_id/status/step`，形成互相矛盾的状态。

## 影响

- 已成功的 Gemini 调用被误报失败。
- 豆包朗读和视频合成没有继续执行。
- 用户可能重复提交 PDF，浪费浏览器自动化和 AI 调用时间。
- 父任务摘要覆盖掉真实子任务 ID，降低自动恢复能力。

## 根因

`_wait_child()` 在发现终态子任务后，先加载 `completed_child` ORM 对象，再调用 `_record_child()` 更新父任务。`_record_child()` 会 `commit`，SQLAlchemy 默认使同一 Session 中的 ORM 对象过期。

函数随后把已经过期的 `completed_child` 返回给调用方。安全快照函数为了避免 detached refresh，不会读取 `state.unloaded` 字段，因此把 ID、状态、步骤全部转换为 `None`。Gemini 重试函数又把这份空快照写回父任务并判为失败。

此前的修复已经要求跨等待保存 ID/快照，但终态分支仍在父任务提交之后返回 ORM，说明纪律尚未覆盖共享等待函数的真实 commit 过期场景。

## 解决思路

- 子任务终态信息必须在任何父任务写库之前冻结。
- 父任务摘要写入完成后，如调用方确实需要 ORM，必须按 ID 重新加载。
- Gemini 重试函数对外只返回普通字典快照，不允许 Session 绑定对象越过内部记录提交。
- 使用真实 SQLAlchemy Session 测试 commit 过期，而不是只依赖不会过期的 `SimpleNamespace`。

## 实际方案

- `_wait_child()` 在 `_record_child()` 前创建完整终态快照。
- 完成父任务摘要和 waiting 状态清理后，按子任务 ID 重新加载再返回。
- `_run_gemini_task_with_retries()` 从创建阶段开始只向记录函数传快照，并在成功时返回快照。
- 延迟等待分支也先冻结 `status/step` 再写父任务，避免恢复记录出现 `- / -`。
- 新增真实数据库回归测试，验证父任务提交后返回的子任务仍有 ID、状态、步骤和结果。
- 新增返回值契约测试，保证 Gemini runner 不再返回 ORM。
- 横向检查发现 Top10 的 `_wait_child()` 存在同构返回旧 ORM 风险，已同步改为父任务提交后按 ID 重新加载，并增加真实 Session 回归测试。

## 验证

- 确认现场 Gemini 子任务 `161fc1bc-cc40-4110-9777-63482aa32ace` 为 `completed/done/100%`，最终返回 3211 字且无错误。
- 单元测试覆盖真实 Session commit 过期和 Gemini runner 快照契约。
- Top10 同构等待路径通过终态子任务重新加载测试。
- 当前父任务 `44b47706-a388-4c1e-acfa-1ad2158d72b4` 通过失败续跑复用已完成 Gemini 子任务，没有重新上传 PDF。
- 四段豆包朗读全部完成，合并音频约 316.749 秒；publisher 子任务 `e60e9eb5-2733-4ebf-aa97-0a74da75a322` 为 `completed/done/100%`。
- 父编排最终为 `succeeded/done/100%`，音频、横屏视频和竖屏视频下载入口均已生成。

## 复发预防

- `DB-SESSION-LIFECYCLE` 根因簇更新为 10 条关联事故。
- 所有共享等待函数必须返回快照，或者在最后一次提交之后重新加载 ORM。
- 复发第三次以上不能再以单点修复关闭；需要继续推进统一工作单元/状态快照 API。

## 后续事项

- 横向检查其他工作流是否存在“加载子任务 -> 更新父任务并 commit -> 返回旧 ORM”的同构代码。
- 将 wait-child 结果逐步统一为不可变 DTO，移除调用方对 ORM 类型的兼容需求。
