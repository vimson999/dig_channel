# 2026-07-07 MP3 视频合成 1.2 倍速未真正生效

## 基本信息

- 状态：已修复
- 严重级别：P1
- 影响模块：MP3 视频编排 / publisher 音频预处理 / 横竖屏视频合成
- 首次发现：用户反馈投递时设置了 1.2 倍速，但成品视频时长仍接近原片段时长
- 修复提交：待提交

## 现象

MP3 视频编排子任务参数中 `audio_segments.speed` 已经是 `1.2`，但以 Micro-TEC 任务为例，计划音频片段为 `867.9s - 2367.9s`，源片段长度约 `1500s`，1.2 倍速后应约 `1250s`；实际生成的剪辑音频和成品视频仍约 `1500s`。

## 影响

用户明确设置的倍速没有体现在最终横屏/竖屏视频中，导致视频时长、字幕节奏、B站投递时长预期都不可靠。已经生成的旧视频不会自动变短，需要重新合成。

## 根因

`tasks.publisher.run_publication_pipeline` 生成剪辑音频时，ffmpeg 命令把 `-t` 放在 `-i` 后面。该位置的 `-t` 会限制滤镜后的输出时长，而不是先截取源片段再变速。结果 ffmpeg 会多读源音频，经过 `atempo=1.2` 后仍输出约 `1500s`。

同时存在两个放大问题：

- 临时音频文件名虽然包含 `s1.2`，但没有校验真实时长，坏缓存会被继续复用。
- ffmpeg 失败时曾静默回退到原音频，可能让错误继续进入视频合成。

## 解决思路

音频预处理必须遵循“先确定源片段范围，再应用倍速”的顺序；任何需要裁切或倍速的场景都不能静默降级到原音频。复用已有视频时也必须把音频片段签名纳入匹配。

## 实际方案

- 将 ffmpeg 命令改为 `-ss <start> -t <duration> -i <source> -filter:a atempo=...`，保证 `-t` 是输入裁切语义。
- 新增音频预处理版本 `v2`，避免复用旧的错误临时文件。
- 对预处理音频进行真实时长校验，期望时长为 `(end - start) / speed`。
- 对需要裁切/倍速的任务，ffmpeg 失败或时长不符时直接失败，不再静默使用原音频。
- MP3 编排复用 publisher 子视频时增加 `audio_signature`，只有音频路径、起点、终点偏移和倍速一致才允许复用。

## 验证

- 手工验证 Micro-TEC 源音频：旧命令输出约 `1500.05s`，修正后输出约 `1250.09s`。
- `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_publisher_audio_preprocess -q`
- `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_publication_task_queue_repair -q`
- `PYTHONPATH=src/backend venv/bin/python -m py_compile src/backend/tasks/publisher.py src/backend/services/mp3_video_workflow.py src/backend/api/mp3_video_workflow.py src/backend/celery_config.py src/backend/services/publisher_queue_repair.py src/backend/services/runtime_guard.py src/backend/apis/v1/celery_monitor.py src/backend/apis/v1/publisher.py`

## 复发预防

- 任何 ffmpeg 裁切 + 滤镜组合都必须明确输入/输出选项位置，并添加命令顺序单测。
- 业务参数传入后，必须通过产物级校验确认真实生效，不能只看参数 JSON。
- 临时缓存文件必须带版本或签名，并校验可观察结果，例如时长、尺寸或帧数。

## 后续事项

已生成的旧视频仍是旧时长。需要用户重新触发视频合成，新的子任务会生成 `edit_v2` 音频并应用正确倍速。
