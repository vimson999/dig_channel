# 2026-07-07 MP3 B站后投上传被误取消且标题混入占位模板

## 基本信息

- 状态：已修复
- 严重级别：中高
- 影响模块：MP3 视频详情页 / B站上传 / publisher 子任务清理 / DS 标题清洗
- 首次发现：用户在 MP3 视频详情页看到 B站发布标题包含 `xxxx / 当日日期`，同时新上传记录被标记为 `已取消`

## 现象

详情页 B站发布表单标题出现：

```text
内资闭门会 / xxxx / xxxx / xxxx / 当日日期) 内资闭门会 / Micro-TEC光模块核心器件 / 国产突破内挤压工艺 / 出口管制误解消除 / 2026-07-07
```

上传记录中新增任务显示：

```text
已取消
父编排任务已终止(completed)，视频子任务自动收口。
```

## 影响

- B站标题可能带着提示词模板投递，影响发布质量。
- 用户手动触发的 B站后投上传任务会被自动清理逻辑误取消。
- 用户会误以为 B站上传流程不可用，或者反复重投。

## 根因

标题问题：

- DS 返回值里可能同时包含提示词原句和真正的第 6/7 项标题。
- 详情页和后端 API 的 `prefer_closed_door` 清洗逻辑直接匹配第一个 `内资闭门会 / ...`，没有排除 `xxxx / 当日日期` 这类模板占位。

误取消问题：

- `cleanup_publisher_children_of_terminal_parents_once()` 会取消父编排已终态的 active publisher 子任务。
- 这个规则原本用于视频合成子任务，避免父任务取消/失败/完成后遗留旧 publisher 占用并发槽。
- MP3 详情页的 B站后投上传任务也是 `publisher` 子任务，但它本来就是在父 MP3 编排完成后由用户手动触发，因此不应该套用“父终态即取消”的规则。

## 解决方案

代码修复：

- B站上传标题提交前在后端再次清洗，不能只相信前端表单。
- 前后端 DS 标题清洗都先移除 `内资闭门会 / xxxx / xxxx / xxxx / 当日日期` 模板，再选择真正的闭门会标题。
- publisher 父终态清理逻辑新增例外：跳过 `metadata.source=mp3_video_workflow:bilibili_upload` 的后投上传任务。
- 对 `existing_video + video.enabled=false + bilibili/youtube.enabled=true` 的上传型 publisher 子任务也不做父终态清理。

## 验证

```bash
node --check src/frontend/js/mp3-video-workflow-detail.js
PYTHONPATH=src/backend venv/bin/python -m py_compile src/backend/api/mp3_video_workflow.py src/backend/services/publisher_queue_repair.py
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_publication_task_queue_repair -q
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_mp3_video_workflow_service.Mp3VideoWorkflowServiceTest.test_api_closed_door_title_ignores_prompt_placeholder -q
```

结果：

```text
test_publication_task_queue_repair: Ran 14 tests, OK
test_api_closed_door_title_ignores_prompt_placeholder: OK
```

## 复发预防

- 父编排终态清理只适用于“父流程创建并等待的生成型子任务”，不能覆盖用户在详情页后续手动触发的上传/发布型子任务。
- 外部平台投递的标题必须后端再清洗一遍，前端清洗只作为体验优化。
- 解析 DS 返回值时必须把提示词模板、占位符、格式说明和真正答案分开处理。
- 新增详情页派生任务时，要明确它是父编排生命周期内任务，还是父完成后的独立操作。
- 修改 watchdog / queue repair 这类长驻进程依赖的代码后，必须重启 `celery_watchdog_loop.py`；只重启后端不会让已运行的 watchdog 重新加载新逻辑。

## 运行态补充

2026-07-07 12:46 再次发现一条 B站后投上传记录被标记为 `已取消`。代码与单测已包含后投上传跳过清理的保护，现场排查发现 `celery_watchdog_loop.py` 仍是 09:03 启动的旧进程；它继续使用旧清理逻辑，把 `metadata.source=mp3_video_workflow:bilibili_upload` 的上传任务按“父任务 completed 后的视频子任务”取消。

处置：

- 重启 Celery watchdog，让它重新加载当前 `publisher_queue_repair.py`。
- 将误取消任务 `21e2dd8d-1c5d-4007-b8e2-b7d243ecb1c3` 恢复为 `pending/queued`，清除 `orphan_child_cleanup` 错误状态，并重新投递到 `render` 队列。
- 验证恢复后的任务在下一轮 watchdog 后仍保持排队状态，没有再次被取消。
