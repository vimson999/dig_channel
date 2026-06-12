# 2026-06-07 Project Status

## 研报导图视频工厂：TTS 文案入口

用户反馈进入「音频匹配」下一步时，不清楚用于 TTS 的口播文案在哪里。

本次调整：

- 在 `report-video-factory.html` 的「音频匹配」页签顶部新增「TTS 文案」区域。
- 按当前任务的报告和 segment 展示每段口播稿。
- 每段显示：
  - 报告编号
  - 段落标题
  - 预计时长
  - `focus_node_id`
  - 推荐音频文件名，例如 `001_seg_001.mp3`
  - `voice_text`
- 增加「复制本段」和「复制全部文案」功能，方便后续接入 TTS 或手动生成音频。
- 前端脚本版本更新到 `20260607-001`，避免浏览器缓存旧逻辑。

## 校验

- `node --check src/frontend/js/report-video-factory.js` 通过。
- 浏览器打开 `http://localhost:8888/report-video-factory.html` 验证：
  - `TTS 文案` 容器存在。
  - `复制全部文案` 按钮存在。
  - 页面加载脚本为 `report-video-factory.js?v=20260607-001`。
  - 切换到「音频匹配」页签后，当前测试任务显示 `12 段` TTS 文案。
  - 第一段显示推荐文件名 `001_seg_001.mp3`，并能看到对应口播文本。

## TTS 复制格式修正

用户反馈「复制全部文案」复制出来包含文件名、报告名、`focus_node_id`，这更像制作清单，不是可直接配音的视频口播稿。

本次修正：

- 「复制视频口播稿」默认只复制可直接朗读的 `voice_text`，并按多报告视频自动加简单开场、报告过渡和结尾。
- 「复制本段」只复制当前段落口播文本。
- 新增「复制制作清单」按钮，专门保留文件名、报告名、段落名和 `focus_node_id`，用于批量 TTS 制作和音频文件命名。
- 前端脚本版本更新到 `20260607-002`。

校验：

- `node --check src/frontend/js/report-video-factory.js` 通过。
- 浏览器确认 `report-video-factory.js?v=20260607-002` 已加载。
- 页面存在「复制视频口播稿」和「复制制作清单」两个按钮。
- 第一段 TTS 文案 DOM 内容不包含 `文件名` 和 `focus_node_id`。

## AI 文案 Prompt 修正

用户追问研报口播文案由谁生成、提示词是什么，并指出整片口播稿开头应该有开场白。

当前生成链路：

- 优先使用 DeepSeek：`DEEPSEEK_MODEL`，默认 `deepseek-chat`。
- 如果没有 `DEEPSEEK_API_KEY` / `REPORT_VIDEO_LLM_API_KEY`，或 DeepSeek 失败，则使用本地抽取式 fallback。
- 入口函数：`services.report_video_ai_service.generate_report_ai_content`。

本次修正：

- DeepSeek prompt 新增 `opening_text`，要求 30-60 字，像财经短视频开头，点明今天看什么和为什么值得看。
- DeepSeek prompt 新增 `closing_text`，要求 20-50 字，做简短收束，不做投资建议承诺。
- `voice_text` 明确要求不要重复开场白。
- 本地 fallback 也补充 `opening_text` / `closing_text`。
- 前端「复制视频口播稿」在单篇报告时优先使用 AI 返回的 `opening_text` / `closing_text`；老数据没有时继续使用兜底逻辑。

校验：

- `venv/bin/python -m py_compile src/backend/services/report_video_ai_service.py` 通过。
- `node --check src/frontend/js/report-video-factory.js` 通过。

## 研报导图视频工厂：整片音频 + 时间轴/SRT

用户提供 test-4 的完整口播文案和 `00:00-00:10 | 文案` 格式时间轴，确认实际工作流更接近“一次性生成整片 TTS 音频”，而不是每个 segment 单独生成音频。

本次调整：

- 「音频匹配」页签新增音频模式：
  - 分段音频
  - 整片音频 + 时间轴
- 整片模式支持上传 1 个完整音频，并上传 SRT 文件或直接粘贴 `00:00-00:10 | 文案` 时间轴。
- 后端新增时间轴解析逻辑，兼容标准 SRT 和简易时间戳格式。
- 后端会按字幕句子与 AI segment 的 `voice_text` 做顺序匹配，把真实 `start_sec/end_sec/duration_sec` 写入 segment。
- 开场白、报告过渡句、结尾等非 segment 内容会作为 context span 保留，生成视频时不会丢失时长。
- 整片时间轴模式生成 task 级完整视频，不再拆成单篇研报视频。
- 上传的时间轴会统一转成标准 SRT，并在整片视频生成时烧录字幕。
- 前端脚本版本更新到 `20260607-003`。

校验：

- `venv/bin/python -m py_compile src/backend/apis/v1/report_video.py src/backend/services/report_video_render_service.py` 通过。
- `node --check src/frontend/js/report-video-factory.js` 通过。
- 使用代表性时间轴文本验证 `_parse_timeline_text`，可正确解析 `start_sec/end_sec/text`。
- 浏览器打开 `http://localhost:8888/report-video-factory.html` 验证：
  - 页面加载脚本为 `report-video-factory.js?v=20260607-003`。
  - 存在「分段音频」和「整片音频 + 时间轴」两个模式。
  - 存在 SRT 文件输入和时间轴粘贴框。

## 研报导图视频工厂：时间轴粘贴区显性化

用户反馈在「选择 SRT/时间轴」旁边需要一个可以直接贴文本的地方。

本次调整：

- 将原先隐藏较深的 `textarea` 改成独立的「时间轴文本」输入块。
- 输入块说明支持直接粘贴 `00:00-00:10 | 文案` 格式。
- 仅在「整片音频 + 时间轴」模式下显示，避免干扰分段音频旧流程。
- CSS 链接增加 `v=20260607-004`，避免浏览器缓存旧样式导致粘贴区不显示。
- 前端脚本版本更新到 `20260607-004`。

校验：

- `node --check src/frontend/js/report-video-factory.js` 通过。
- 浏览器确认 `report-video-factory.css?v=20260607-004` 和 `report-video-factory.js?v=20260607-004` 已加载。

后续修正：

- 用户截图反馈页面仍只看到上传按钮，没有直接粘贴时间轴的地方。
- 将「时间轴文本」粘贴区改为默认显示，不再依赖「整片音频 + 时间轴」模式 class 才出现。
- 布局改为占满一整行，顺序在 SRT 文件选择之后、上传按钮之前。
- CSS/JS 版本更新到 `20260607-005`。
- 浏览器确认 `report-video-factory.css?v=20260607-005` 和 `report-video-factory.js?v=20260607-005` 已加载，`.rv-timeline-paste` 与 `#timeline-text-input` 均为 `display: block`。

## 研报导图视频工厂：异步拆分与 test-4 真实验证

用户要求不再等待 Web 请求内完成长耗时工作，基于 `test-4` 自行创建测试用例，并使用根目录音频 `doubao朗读_20260607_200545.wav` 和已给出的时间轴做真实验证。

本次拆分：

- 新增独立 Celery 队列 `report_video`，用于研报解析、AI 文案生成、导图视频生成。
- 新增任务模块 `src/backend/tasks/report_video.py`：
  - `parse_report_pdf_task`
  - `generate_ai_scripts_task`
  - `generate_videos_task`
- `report-video` Web API 不再同步执行长任务：
  - 上传 PDF 后只保存文件并投递解析任务。
  - AI 文案生成只投递 Celery 任务。
  - 视频生成只投递 Celery 任务。
  - 重新解析也改为投递 Celery 任务。
- 新增开发脚本：
  - `scripts/dev/run-celery-report-video.sh`
  - `scripts/dev/stop-celery-report-video.sh`
- Celery 控制台新增「研报导图视频工厂」worker 卡片，队列名 `report_video`。
- `report-video-factory.html` / `report-video-factory.js` 版本更新到 `20260607-006`。
- 前端任务列表在 `parsing`、`ai_generating`、`video_generating` 状态下会自动轮询刷新。

测试用例：

- 新增 `tests/fixtures/report_video_test4_timeline.txt`，保存用户提供的 `test-4` 时间轴。
- 新增 `tests/integration/test_report_video_full_audio_timeline.py`：
  - 验证时间轴可解析出 37 条 span。
  - 使用云端 PG 中的 `test-4` 任务上传完整音频 + 时间轴。
  - 验证 12 个 AI segment 能匹配到真实时间段。
  - 验证整片时间轴模式写入 `task.result.full_audio`。
  - 验证整片 16:9 导图聚焦视频可真实渲染。

关键发现与修正：

- 根目录音频实际时长约 `247.687s`，而用户给出的时间轴结束于 `06:22`，即 `382s`。
- 如果直接使用原时间轴，字幕和导图聚焦会明显对不上音频。
- 后端新增时间轴重定时逻辑：当时间轴总时长和音频真实时长差异超过 2 秒时，按比例缩放所有 `start_sec/end_sec`。
- 重定时信息写入 `task.result.full_audio.retime`，方便排查。

验证结果：

- `venv/bin/python -m py_compile src/backend/apis/v1/report_video.py src/backend/tasks/report_video.py src/backend/services/report_video_service.py src/backend/services/report_video_render_service.py src/backend/celery_config.py src/backend/apis/v1/celery_monitor.py` 通过。
- `node --check src/frontend/js/report-video-factory.js` 通过。
- `node --check src/frontend/js/celery-monitor.js` 通过。
- `PYTHONPATH=src/backend venv/bin/python -m unittest tests.integration.test_report_video_full_audio_timeline.ReportVideoFullAudioTimelineTest.test_parse_test4_simple_timeline` 通过。
- `RUN_REPORT_VIDEO_INTEGRATION=1 PYTHONPATH=src/backend venv/bin/python -m unittest tests.integration.test_report_video_full_audio_timeline.ReportVideoFullAudioTimelineTest.test_test4_upload_full_audio_with_timeline` 通过。
- `RUN_REPORT_VIDEO_RENDER=1 PYTHONPATH=src/backend venv/bin/python -m unittest tests.integration.test_report_video_full_audio_timeline.ReportVideoFullAudioTimelineTest.test_test4_render_full_audio_video` 通过。

真实输出：

- 任务：`test-4`
- Task ID：`3cd39d26-30d9-499d-9c56-6a3c5dc469ed`
- 输出文件：`data/tasks/3cd39d26-30d9-499d-9c56-6a3c5dc469ed/outputs/task/16x9/test-4_16x9_aa6ecdcd.mp4`
- `ffprobe` 验证：
  - 视频：`1920x1080`
  - 音频：存在
  - 时长：`252.834s`
