# 2026-07-13 MP3 publisher 子任务等待 ID 未定义

## 基本信息

- 状态：已修复
- 严重级别：P1
- 影响模块：MP3 视频编排 / publisher 父子等待
- 首次发现：2026-07-13
- 修复提交：待提交
- 根因分类：父子任务 ID 生命周期遗漏
- 复发键：`ORCH-PARENT-CHILD-STATE`
- 是否复发：同类父子状态生命周期复发
- 防线层级：local_patch / contract_test

## 现象

父任务成功创建竖屏 publisher 子任务后立即在 91% 失败，错误为 `name 'vertical_task_key' is not defined`。父任务失败又自动取消了已经运行到 30% 的竖屏子任务。

## 根因

横屏和竖屏分支将 publisher ORM 对象写入父任务结果后，等待函数使用了从未赋值的 `landscape_task_key` / `vertical_task_key`。同时，若在 commit 后才访问 ORM 对象 ID，还可能触发过期对象整行刷新。

## 实际方案

- 无论复用活跃子任务还是新建子任务，都在任何 commit 前将 `task.id` 快照为普通字符串。
- 横屏等待只使用 `landscape_task_key`，竖屏等待只使用 `vertical_task_key`。

## 验证

- MP3 单元测试通过。
- 原任务复用已完成横屏并重新创建竖屏，父任务进入竖屏等待状态。

## 复发预防

- 创建父子任务后必须在首次 commit 前保存稳定子任务 ID。
- 等待循环不得依赖可能已过期的 ORM 实例。
