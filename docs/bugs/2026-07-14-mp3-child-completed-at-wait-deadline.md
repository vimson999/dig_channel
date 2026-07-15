# 2026-07-14 MP3 子任务在等待终点完成却被判超时

## 基本信息

- 状态：已修复，现场任务已完成收口
- 严重级别：P1
- 影响模块：MP3 视频编排 / publisher 父子等待 / render 队列
- 首次发现：2026-07-14
- 修复提交：待提交
- 根因分类：父子任务等待终点竞态
- 复发键：`ORCH-PARENT-CHILD-STATE`
- 是否复发：同类父子状态一致性问题复发
- 关联记录：`2026-06-23-mp3-video-parent-long-wait.md`、`2026-07-07-mp3-video-publisher-file-done-parent-waiting.md`
- 防线层级：local_patch / contract_test / observability

## 现象

MP3 父任务 `efd7185d-a819-44f8-8369-4da8b08a203b` 在 81% 显示失败：

```text
等待子任务超时: landscape_video e372cb66-dc45-4ac7-86e3-12b9b6ceca06
```

但 render 日志显示横屏子任务在 `09:25:10` 已完成并写回 `completed/done/100`，父任务在 `09:25:11` 才记录上述失败。横屏 MP4 已正常落盘。

## 影响

- 实际成功的视频被父编排误报为失败。
- 用户会重复点击续跑或重新生成，浪费约 20 分钟视频渲染成本。
- 父任务详情与子任务数据库状态相互矛盾，降低任务状态可信度。
- render 单并发排队会扩大竞态出现概率，但不是视频本身失败。

## 根因

`_wait_child()` 在短等待窗口结束后会再读取一次子任务。旧逻辑只处理“仍未终态”的情况；如果子任务恰好在最后一次循环检查与窗口结束后的读取之间进入 `completed`，代码反而直接抛出“等待子任务超时”。

因此这是等待窗口终点的明确竞态：终点读取到了成功终态，却把成功终态映射成了超时错误。排队时间和 20 分钟视频渲染使父任务多次延后续跑，最终恰好撞上这个错误分支。

## 解决思路

等待窗口只是决定父任务应当继续内联等待还是延后续跑，不能改变子任务的真实终态。窗口结束后的最后一次读取必须遵循与循环内部相同的状态分流：

- 子任务不存在：报“子任务不存在”。
- 子任务已经进入任一终态：清理父任务等待标记并返回真实子任务。
- 子任务仍在执行：抛出 `ChildTaskStillRunning`，让父任务延后续跑。

## 实际方案

- 修正 `services.mp3_video_workflow._wait_child()` 的窗口终点判断。
- `completed/failed/canceled` 在终点读取时均作为真实终态返回，不再转换为超时。
- 只有非终态子任务才记录等待状态并进入 `ChildTaskStillRunning`。
- 新增单元测试，模拟子任务恰好在等待截止时进入 `completed`。
- 现场任务复用了已完成横屏 MP4，继续创建并执行竖屏子任务，没有重复生成横屏视频。

## 验证

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_mp3_video_workflow_service.Mp3VideoWorkflowServiceTest.test_wait_child_accepts_completion_at_inline_deadline
PYTHONPATH=src/backend venv/bin/python -m py_compile src/backend/services/mp3_video_workflow.py
git diff --check
```

现场对账：

- 横屏子任务 `e372cb66...`：`completed/done/100`。
- 横屏 MP4：已存在且被父任务续跑复用。
- 父任务 `efd7185d...`：`completed/done/100`，错误已清空。
- 新竖屏子任务 `77c51965...`：`completed/done/100`。
- render 队列积压和业务进行中任务均已归零。
- MP3 编排 worker 已定向重启并加载修复，render worker 未重启。

## 复发预防

- 所有父子等待器必须对“循环内终态”和“截止时终态”使用同一状态迁移合同。
- 超时只描述仍未终态的任务，不能覆盖数据库已经确认的成功或失败终态。
- 父任务恢复必须优先复用已完成子任务及稳定落盘文件，避免重复渲染。
- 状态问题排查必须按父任务、子任务、Celery 消息、输出文件四方对账。

## 后续事项

- 横向检查其他工作流的 child wait helper 是否存在相同终点竞态。
- 考虑将父子等待终点分流抽为共享 helper，降低不同编排各自实现产生的偏差。
