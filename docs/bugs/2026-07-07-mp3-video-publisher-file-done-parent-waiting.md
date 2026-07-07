# 2026-07-07 MP3 视频文件已生成但父编排仍显示运行中

## 基本信息

- 状态：已修复，已为当前任务完成收口
- 严重级别：中高
- 影响模块：`mp3_video_workflow` / `publisher` / `render` worker / 任务详情页状态
- 首次发现：用户在 MP3 视频编排页面看到横屏和竖屏视频文件都已生成，但父任务仍显示 `运行中 landscape_video_waiting 81%`
- 修复提交：待提交

## 现象

用户在页面看到任务 `0a38df18-5d11-4d56-811c-641c13943d95` 仍处于：

```text
status=processing
current_step=landscape_video_waiting / vertical_video_waiting
progress=81/94
```

但本地 `data/processed` 已存在视频文件：

```text
内资闭门会__Micro-TEC光模块核心器件__国产突破内挤压工艺__出口管制误解消除__2026-07-07_707dc9.mp4
v_内资闭门会__Micro-TEC光模块关键材料用量极小__复兴科技良品率追赶日系__1.6T送样预计50元单价__2026-07-07_ed8d84.mp4
```

数据库进一步确认：

- 父任务最初仍有 `waiting_child` / `deferred_waits`。
- 横屏旧子任务 `8e218260...` 已完成，但属于旧的带字幕版本。
- 新横屏无字幕子任务 `707dc97d...` 生成了文件后，曾停留在 `processing/synthesis`，后续才回写完成。
- 旧竖屏 `bdef9403...` 对应带字幕版本，不能用于无字幕父任务收口。
- 新竖屏无字幕子任务 `ed8d84de...` 完成后，父任务还需要再次恢复收口。

## 影响

- 用户看到“视频已经有了”，但任务仍显示运行中，容易误判为任务卡死。
- 父编排停在等待态时，详情页下载入口和后续 B 站投递入口可能不完整或不稳定。
- 如果错误复用旧的带字幕子任务，会破坏“未选择生成字幕”的投递语义。
- 如果只重启 worker 或盲目 requeue，可能重复生成大视频，浪费大量时间和计算资源。

## 根因

直接根因：

- publisher 子任务的视频文件已经由 ffmpeg 生成并落盘，但子任务 DB 状态没有及时进入 `completed/done/100`。
- MP3 父编排只信任子任务 DB 状态，不会从已存在的 MP4 文件恢复子任务完成态。
- 历史父任务的 `params.mp3_video_workflow.generate_subtitles` 缺失，只有 `result.generate_subtitles=false`，导致续跑时容易回退成默认 `true`，从而误判旧的带字幕子任务可复用。

架构层根因：

- “文件已落盘”和“业务任务已完成”是两个状态源，旧逻辑缺少对账和恢复。
- 父编排的复用逻辑没有把字幕开关作为足够严格的任务语义边界处理历史缺失字段。
- 排障时容易只看页面或只看文件，忽略父任务、子任务、文件系统三方状态必须一致。

## 解决思路

正确恢复边界：

- 不能因为看到了同标题视频文件就直接判定任务完成，必须匹配子任务短 ID、横竖屏类型和字幕开关。
- 对于已稳定落盘的 MP4，可把对应 publisher 子任务恢复为 completed，而不是重跑视频合成。
- 父编排恢复时必须重新读取最新子任务状态，清理 `waiting_child` / `deferred_waits`，并写回最终横屏与竖屏结果。

## 实际方案

代码修复：

- `process_job()` 读取字幕开关时，新增从父任务 `result.generate_subtitles` 回退读取，兼容旧任务 `params` 缺字段的情况。
- 新增 publisher 子任务磁盘恢复逻辑：
  - 根据 publisher 子任务 ID 前 6 位在 `data/processed` 查找对应 `.mp4`。
  - 要求文件大小超过下限且 mtime 超过稳定等待窗口。
  - 按 `aspect_ratio` 区分横屏/竖屏，避免误拿旧文件。
  - 恢复时写入 `video_path`、`landscape_video_path` 或 `vertical_video_path`、`completed_at`、`disk_recovered_at`、`generate_subtitles`。
  - 将子任务置为 `completed/done/100`，并写入 `task_video_path`。
- 父任务等待子任务时先尝试磁盘恢复，再按正常 completed 路径收口。
- 查找可复用 publisher 子任务时，也先尝试磁盘恢复，避免已落盘但 DB 未完成的子任务被忽略。

现场恢复：

- 新横屏无字幕子任务 `707dc97d-c009-4b41-b732-d262d76fdb8e` 已收口为 completed。
- 新竖屏无字幕子任务 `ed8d84de-dcf9-46e5-8380-a5296fa4364c` 已收口为 completed。
- 父任务 `0a38df18-5d11-4d56-811c-641c13943d95` 已收口为：

```text
status=completed
current_step=done
progress=100
waiting_child=None
deferred_waits=None
```

运行恢复：

- 已重启 `mp3_video_workflow` worker，使新恢复逻辑对后续任务生效。
- 确认 `mp3_video_workflow` 队列积压为 0，worker 运行正常。

## 验证

代码验证：

```bash
PYTHONPATH=src/backend venv/bin/python -m py_compile src/backend/services/mp3_video_workflow.py
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_mp3_video_workflow_service -q
git diff --check -- src/backend/services/mp3_video_workflow.py
```

结果：

```text
Ran 47 tests
OK
```

现场验证：

- 父任务 `0a38df18...` 已变为 `completed/done/100`。
- 横屏结果指向 `..._707dc9.mp4`，并记录 `generate_subtitles=false`。
- 竖屏结果指向 `..._ed8d84.mp4`，并记录 `generate_subtitles=false`。
- `waiting_child` 和 `deferred_waits` 均已清空。

## 复发预防

- 判断编排是否完成时，必须同时确认父任务终态、子任务终态、输出 artifact 存在，不能只看页面或只看文件。
- 任务语义字段必须进入 `params` 和 `result`，历史任务缺字段时必须有清晰回退策略。
- 复用历史 publisher 子任务时必须比较关键语义字段：角色、横竖屏比例、字幕开关。
- 大视频任务恢复优先使用已稳定落盘的 artifact 补收口，避免盲目 requeue 重跑。
- 修改 worker 代码后要定向重启对应队列 worker，并复查队列积压和业务任务状态。

## 后续事项

- 可考虑把 publisher 文件落盘后的最终 DB 回写改成更小、更强重试的事务块，减少“文件已生成但 DB 未完成”的窗口。
- 可在 Celery 监控页或详情页增加“文件已存在但 DB 未收口”的提示和一键恢复入口。
