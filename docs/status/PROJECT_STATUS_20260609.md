# Project Status - 2026-06-09

## 今日重点

- report-video 从单篇研报视频链路前置扩展出“批量研报主题策划层”。
- 新增主题策划数据暂存于 `ReportVideoTask.result.topic_planning`，避免第一版引入新表和迁移风险。
- 外部实时热搜暂未启用，返回 `search_status: not_enabled`，排序先基于上传研报内部热度。

## 已完成

- 后端新增三类 DeepSeek JSON prompt：
  - 单篇研报快速摘要卡。
  - 多研报主题聚类。
  - 主题级视频脚本。
- 后端新增 report-video Celery 任务：
  - `generate_topic_cards_task`
  - `generate_topic_clusters_task`
  - `generate_topic_video_script_task`
- 后端新增 API：
  - `GET /api/v1/report-video/tasks/<task_id>/topic-planning`
  - `POST /api/v1/report-video/tasks/<task_id>/topic-planning/cards`
  - `POST /api/v1/report-video/tasks/<task_id>/topic-planning/clusters`
  - `POST /api/v1/report-video/tasks/<task_id>/topics/<topic_id>/script`
- 前端 `report-video-factory.html` 新增“主题策划”tab，支持查看摘要卡、今日主题榜、主题详情和主题脚本。
- ASR 抽样校准 prompt 改为按任务动态生成，并把 prompt hash 纳入 ASR 缓存 key，避免不同上下文复用旧识别结果。
- 针对扫描版 PDF 增加主题策划降级链路：
  - 单篇摘要卡不再强依赖 `parse_status=parsed`。
  - PDF 正文不可用时，从文件名提取机构、主题关键词和标题，生成 `metadata_fallback` 摘要卡。
  - 主题聚类接口允许未解析成功的研报参与策划，避免批量扫描版 PDF 卡死在 OCR 前置步骤。
- 新增研报视频自动扫描能力：
  - 新增本地配置 `data/report_video_auto_scan.json`，支持配置扫描目录、每天执行时间、测试间隔分钟、单次最多处理文件数、输出比例。
  - 新增 `scan_report_video_folder_task` 和 `auto_process_report_task`，统一进入 `report_video` Celery 队列。
  - 使用 PDF SHA256 做去重；已完成视频或正在处理的 PDF 会跳过。
  - 新增独立调度进程 `src/backend/run_report_video_auto_scheduler.py`，开发脚本为 `scripts/dev/run-report-video-auto-scheduler.sh`。
- 新增 AI Prompt 可视化配置：
  - 新增 `data/report_video_ai_prompts.json` 保存页面自定义 Prompt。
  - 页面新增“AI 提示词”tab，支持查看、编辑、保存、恢复默认。
  - 已接入单篇研报 AI 文案、研报摘要卡、批量主题聚类、主题级视频脚本、ASR 抽样校准提示词。
- 新增 PDF 解析 Redis 缓存第一版：
  - 在 `parse_pdf_file()` 内部接入缓存，所有上传解析、重新解析、自动扫描链路复用同一入口。
  - Redis key 使用 PDF 原始文件名 hash，value 保存解析结果 JSON、raw text、cleaned text。
  - Redis 地址优先读取 `REPORT_VIDEO_PARSE_REDIS_URL`，其次读取 `REDIS_URL/CELERY_BROKER_URL`，避免误连默认 localhost。
  - 命中缓存时会把 `raw_text.txt`、`cleaned_text.txt` 重新落到当前任务目录，保持数据库路径仍指向当前任务。
  - 缓存校验 `file_name + file_size + parser_version`，避免同名不同大小或解析版本变化时误命中。
  - 只缓存有效解析结果，低质量/失败解析不写缓存，避免 OCR 环境修复后仍命中旧失败结果。
- 梳理主题策划到视频制作的操作链路：
  - 新增 `POST /api/v1/report-video/tasks/<task_id>/topics/<topic_id>/apply-script`。
  - 主题脚本生成后可点击“应用为视频脚本”，把主题级脚本写入现有 `analysis/mindmap/segments`。
  - 应用主题脚本会替换当前任务旧 AI 产物，避免单篇 segments 与主题 segments 混在 TTS/视频生成里。
  - 前端将旧 “AI 文案” tab 改为“单篇 AI 文案”，并在主题详情区明确提示“应用后直接进入音频匹配”。
  - 主题脚本应用成功后自动切换到“音频匹配”tab。

## 验证

- `venv/bin/python -m py_compile src/backend/services/report_video_ai_service.py src/backend/tasks/report_video.py src/backend/apis/v1/report_video.py src/backend/services/asr_service.py src/backend/services/asr_cache_service.py`
- `node --check src/frontend/js/report-video-factory.js`
- 使用项目根目录 `伯恩斯坦/` 下 13 份 PDF 做快路径测试：
  - 13 份 PDF 的 PyMuPDF 文本层均为 0 字，属于扫描版/图片型 PDF。
  - 完整解析会进入 OCR，耗时不可控；测试中已停止长时间运行的 OCR 进程。
  - 降级链路成功生成 13 张摘要卡，provider 均为 `metadata_fallback`。
  - 成功生成 10 个主题聚类；当前测试环境无法访问 DeepSeek，主题脚本自动走 `local_fallback`。
- `venv/bin/python -m py_compile src/backend/services/report_video_prompt_service.py src/backend/services/report_video_auto_service.py src/backend/tasks/report_video.py src/backend/apis/v1/report_video.py src/backend/celery_config.py src/backend/run_report_video_auto_scheduler.py`
- `node --check src/frontend/js/report-video-factory.js`
- 自动扫描 dry-run 测试：
  - 沙箱无法连接云端 PostgreSQL，真实查库去重预览被阻止。
  - 使用不查库的安全 dry-run 验证 `伯恩斯坦/` 可扫描到 13 份 PDF，并可为每份生成 SHA256。
  - 使用临时 SQLite + Flask test client 验证 `/api/v1/report-video/ai-prompts`、`/api/v1/report-video/auto-scan/preview`、`/api/v1/report-video/auto-scan/run` dry-run 返回正常。
  - 自动扫描预览优化为一次性加载最近 1000 条自动扫描报告 hash，避免每个 PDF 重复查库。
- Prompt 配置测试：
  - 默认 Prompt 列表读取成功，共 5 个。
  - `asr_calibration` 保存自定义、渲染变量、恢复默认均通过。
- PDF 解析缓存测试：
  - 使用临时 PDF + fake Redis 跑两次 `parse_pdf_file()`。
  - 第一次解析 `cache.hit=False` 且写入缓存。
  - 第二次同文件名和大小命中缓存 `cache.hit=True`。
  - 命中后成功在新的输出目录生成 `raw_text.txt` 和 `cleaned_text.txt`。
  - 当前 Codex 环境真实 Redis 未监听 `localhost:6379`，未做真实 Redis 写入验证；代码会按环境变量连接运行时 Redis。
- 主题脚本应用测试：
  - 使用临时 SQLite + Flask test client 构造任务、两份研报、主题榜和主题脚本。
  - 调用 `/api/v1/report-video/tasks/<task_id>/topics/T001/apply-script` 返回 200。
  - 任务状态变为 `script_ready`，主题策划状态变为 `topic_script_applied`。
  - 成功生成 1 条 analysis、1 个 mindmap、2 个 segment，可被后续 TTS/音频匹配读取。
- 主题级 16:9 视频模板优化：
  - `sampled_asr` 不再因总时长接近而跳过抽样；上传整片音频 + 时间轴时会真实抽样、尝试生成锚点。
  - 新增标准字幕校准报告 `subtitle_calibration`，写入 `task.result` 和 `full_audio.subtitle_calibration`，Celery 渲染日志也会输出。
  - 校准报告包含 `sample_count / anchor_count / confidence_avg / average_offset_sec / max_offset_sec / calibration_method / status / fallback_reason / anchor_details`。
  - 每个 ASR 锚点单独输出日志，包含抽样窗口、匹配时间轴句子、估算时间、实际时间、偏移和置信度。
  - 主题脚本归一化新增“主题总开场”兜底 section，`focus_node_id=node_overview`，并生成 `theme_flow` 供视频主线链路使用。
  - `topic_video_script` 默认提示词升级为主题级结构，要求多研报共同主线、黄金三秒开场、主题总开场 section、`theme_flow`。
  - 16:9 整片主题视频新增多研报联合背书开场，展示多份研报/机构/主题标签和主题钩子；主题视频不再插入旧的逐篇来源卡。
  - 16:9 主题正文页左侧切换为“主题主线链路 + 当前章节卡”，不再使用抢眼白色节点按钮堆。
  - 右侧卡片继续保留详细说明和核心要点，优先读取 `visual_explanation` 与 `bullet_points`，避免只有几个关键词或大量省略号。
  - PDF 预览在报告路径为空或不是文件时直接跳过，避免异常数据写错预览缓存目录。
- 浏览器冒烟：
  - Codex 内置浏览器策略拒绝访问 `http://localhost:8888`，未能做页面点击验证。
  - 前端本轮以 `node --check`、Flask test client API dry-run 和静态 DOM 结构改造为主要验证。
- 主题级模板验证：
  - `venv/bin/python -m py_compile src/backend/apis/v1/report_video.py src/backend/services/report_video_ai_service.py src/backend/services/report_video_prompt_service.py src/backend/services/report_video_render_service.py src/backend/tasks/report_video.py`
  - 使用假 sampler 单元测试 `_smart_retime_timeline_entries()`，确认在总时长接近时 `sampled_asr` 仍会执行，并生成 `shift` 校准报告和锚点明细。
  - 使用无数据库的假主题任务渲染 `tmp/report_video_theme_template_test/opening.jpg` 和 `tmp/report_video_theme_template_test/theme_frame_fixed.jpg`，确认多研报开场帧与主题主线正文帧可生成。
- 音频匹配与主题开场继续升级：
  - 研报视频“整片音频 + 时间轴”模式新增“未上传 SRT 时，强制 ASR 生成字幕”选项。
  - 前端上传音频时会提交 `force_asr_timeline`，后端在没有上传/粘贴时间轴时走整片 ASR，生成真实字幕时间轴。
  - ASR 生成的时间轴写入 `timelines/*_asr_generated_timeline.txt`，并继续走 `_apply_full_audio_timeline()` 的文本相似度匹配链路，用于字幕和画面节点映射。
  - 字幕校准报告增加 `subtitle_source`，可区分 `uploaded_or_pasted / script_estimate / full_audio_asr`。
  - 整片 ASR 返回结构做兼容：支持 list、dict 包裹 `segments`、纯文本字符串，避免不同 ASR 包装层导致上传失败。
  - 多研报联合背书开场不再额外插入静音时间；证据链视觉会消耗第一个 `topic_opening/node_overview` 口播段时长，字幕时间线不再因此整体偏移。
  - 证据链视觉时长改为根据开场口播段倒推，最多 10 秒；如果开场段本身达到 5 秒以上，则至少展示 5 秒。
  - 主题脚本 Prompt 和兜底归一化已强化“黄金三秒”开场：要求 5-10 秒短钩子、强调多机构共同主线、避免普通摘要式开头。
  - 开场 span 识别兼容 `start/end` 与 `start_sec/end_sec` 两种结构，方便测试数据和真实任务复用。
- 最新验证：
  - `venv/bin/python -m py_compile src/backend/apis/v1/report_video.py src/backend/services/report_video_ai_service.py src/backend/services/report_video_prompt_service.py src/backend/services/report_video_render_service.py src/backend/tasks/report_video.py`
  - `node --check src/frontend/js/report-video-factory.js`
  - 使用 fake ASR 单元测试 `_generate_timeline_entries_from_asr()`，确认整片 ASR 分支可生成 2 条字幕时间轴，并输出 `subtitle_source=full_audio_asr` 的校准报告。
  - 使用假主题 span 单元测试 `_theme_opening_visual_states()` 与 `_theme_opening_span_info()`，确认 8 秒开场可被拆分为 4 段证据链视觉状态，且正确识别 `topic_opening` span。
  - 测试过程中 `.env` 第 209-248 行仍有 dotenv parse warning；不影响本轮功能验证，但后续建议清理环境文件格式。
- 长音频 ASR 队列化：
  - 确认当前 ASR 默认优先使用 `mlx_whisper`，在 Apple Silicon 上走 MLX Whisper；找不到 MLX 时才回退 faster-whisper。
  - 新增 `tasks.report_video.process_audio_timeline_task`，专门处理“整片音频无 SRT，强制 ASR 生成字幕时间轴”的后台任务。
  - 该任务复用 `asr_cache_service.transcribe_with_cache()`，因此整片音频 ASR 结果会继续进入现有本地缓存，重复测试不会每次完整重跑。
  - 上传接口在勾选“强制 ASR 生成字幕”且未上传/粘贴 SRT 时，不再同步执行 ASR；会先保存音频，设置 `audio.match_status=asr_queued`、`task.status=audio_processing`，然后投递 Celery。
  - Celery 执行时状态推进为 `asr_processing`，完成后继续调用现有 `_apply_full_audio_timeline()`，匹配成功后进入 `waiting_video`。
  - 失败时写入 `audio_failed/unmatched` 和错误信息，页面可以直接看到原因。
  - 前端状态字典新增 `audio_processing / audio_failed / asr_queued / asr_processing`，并把 `audio_processing` 加入 5 秒自动刷新列表。
  - Celery 路由新增 `tasks.report_video.process_audio_timeline_task -> report_video`，不需要额外新增 worker 脚本，继续使用 `run-celery-report-video.sh`。
- 长音频 ASR 队列验证：
  - `venv/bin/python -m py_compile src/backend/apis/v1/report_video.py src/backend/tasks/report_video.py src/backend/celery_config.py src/backend/services/asr_service.py src/backend/services/asr_cache_service.py`
  - `node --check src/frontend/js/report-video-factory.js`
  - `PYTHONPATH=src/backend venv/bin/python -m unittest tests.integration.test_report_video_full_audio_timeline`，6 个测试通过，其中 2 个需要真实环境的长流程测试按原逻辑跳过。
  - 使用临时 SQLite + Flask test client + monkeypatch Celery 投递，验证上传一个 wav、勾选强制 ASR、未上传 SRT 时，接口 200 返回 `timeline.queued=true`，任务状态为 `audio_processing`，音频状态为 `asr_queued`，没有在 Web 请求里同步跑 ASR。
- 字幕提取缓存规则对齐：
  - 复查已有 `batch-transcription.html` / `/api/v1/batch-transcription/start` 逻辑：批量音频转文案默认使用 `medium` 模型，并复用 `services.asr_cache_service.transcribe_with_cache()`。
  - 将研报视频“未上传 SRT 时，强制 ASR 生成字幕”默认模型从 `small` 对齐为 `medium`，避免同一音频在批量字幕提取和研报视频中因为模型不同导致缓存不能复用。
  - `transcribe_with_cache()` 新增兼容参数 `include_metadata=False`；默认行为不变，旧调用方仍返回 segments。
  - 当 `include_metadata=True` 时，会返回 `cache_hit / cache_key_prefix / matched_cache_key_prefix / model_size / start_seconds / end_seconds / speed / initial_prompt_hash / segment_version / cache_path`，便于排查是否命中缓存。
  - 研报视频整片 ASR 时间轴生成会记录 `asr_cache` 到 `timeline_generation`、`subtitle_calibration` 和 `audio_timeline_job`，页面或接口拿任务详情时可以判断是否复用缓存。
  - 前端文案更新为“使用与批量音频转文案一致的 medium 模型和本地字幕缓存”。
- 字幕提取缓存对齐验证：
  - `venv/bin/python -m py_compile src/backend/services/asr_cache_service.py src/backend/apis/v1/report_video.py src/backend/tasks/report_video.py src/backend/celery_config.py`
  - `node --check src/frontend/js/report-video-factory.js`
  - `PYTHONPATH=src/backend venv/bin/python -m unittest tests.integration.test_report_video_full_audio_timeline`，7 个测试通过，其中 2 个真实环境长流程测试跳过。
  - 新增轻量测试确认 `_generate_timeline_entries_from_asr()` 默认传入 `model_size=medium`，并能把 ASR cache metadata 带回。
  - 使用临时 SQLite + Flask test client + monkeypatch Celery 投递再次验证强制 ASR 上传分支：接口返回 `timeline.queued=true`，任务状态 `audio_processing`，音频状态 `asr_queued`，队列参数 `calibration_model=medium`。
- `test-10` 真实任务复用自测：
  - 复用云端 PostgreSQL 中任务 `43297565-c5fa-49a2-a4a5-ec381d588773`，直接调用 `render_task_video(task, "16:9")` 做真实模板渲染。
  - 测试过程中先发现右侧说明卡底部来源信息与核心要点重叠，已改为动态计算说明行数和要点数量，并移除正文卡片内重复来源页脚。
  - 发现主题模式使用全局 xfade 时会出现正文页重影；主题级模板改为由内部多状态帧表达过渡，不再对整帧做全局淡入淡出。
  - 发现无 xfade 分支未显式映射视频流，曾生成仅音频 MP4；已补充 `-map 0:v`。
  - 发现 concat still images 直接用 `-t` 会导致视频流短于音频流；改为先生成完整临时 MP4，再按音频时长 stream copy 裁剪最终 MP4。
  - 最终输出：`data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/outputs/task/16x9/test-10_16x9_686a2ffa.mp4`。
  - `ffprobe` 验证：容器 222.600 秒，视频流 H.264 1920x1080 30fps 222.567 秒，音频流 AAC 222.487 秒。
  - `ffmpeg -v error -i ... -f null -` 完整解码无报错。
  - 抽帧检查 `1s / 6s / 45s / 120s / 220s`：多研报证据开场、主题钩子、正文主线图、右侧详细说明卡和底部字幕均可正常显示；未再看到正文重影、右侧文字撞底或尾段断画。
  - 该历史任务的字幕校准报告仍为 `fallback/no_asr_anchor`，`sample_count=3`、`anchor_count=0`，说明 sampled ASR 曾执行抽样但未匹配到有效锚点；本次修复范围是模板渲染和视频合成稳定性。

## 补充验证：test-10 整片 ASR 时间轴

- 复用真实任务 `43297565-c5fa-49a2-a4a5-ec381d588773` 和音频 `data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/audios/891c9164_doubao朗读_20260609_142534.wav` 做整片 ASR 验证。
- 本机实际使用 Apple Silicon 友好的 MLX Whisper medium 8bit 模型：
  - `/Users/v9/.cache/huggingface/hub/models--mlx-community--whisper-medium-mlx-8bit/snapshots/a6c1faae87aac3407e2bd14aeed9b9205d9fbef5`
  - 首次完整识别耗时约 51.39 秒，后续命中 `data/processed/asr_cache/47e16d9a79*.json` 缓存。
- 整片 ASR 原始片段 119 条，合并清洗后生成 44 条字幕时间轴，避免 Whisper 把 `%`、英文单词、短数字拆成大量碎字幕。
- 新增并验证连续 ASR 窗口匹配逻辑：
  - 6 个业务 segment 全部匹配成功，`matched_segment_count=6`，`unmatched_segment_count=0`。
  - 主要匹配得分：0.9926、0.9653、1.0、0.8652、0.8771、0.9802。
  - 音频 `40.82s-80.58s` 存在一段重复开场，被系统识别为 context，不强行绑定业务 segment。
- 修复全 ASR 时间轴渲染漂移：
  - 原因：ASR 生成的短字幕 span 在视频帧渲染中被强行拉到最少 1 秒，导致画面总时长被撑到 252.777 秒，而音频只有 222.487 秒。
  - 修复后正文帧允许 0.05 秒级短时长，最终帧总时长 222.537 秒，音频 222.487 秒，误差约 0.05 秒。
- 修复 SRT 输出重叠：
  - `_write_timeline_srt()` 会按时间排序并做单调化处理，保证后一条字幕不会覆盖前一条。
  - 最终验证 `subtitle_overlap_count=0`。
- 最终输出测试视频：
  - `data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/outputs/task/16x9/test-10_16x9_dcd03f06.mp4`
  - `ffprobe`：H.264 1920x1080 30fps，视频流 222.567 秒，音频流 222.487 秒，容器 222.600 秒。
  - `ffmpeg -v error -i ... -f null -` 完整解码无报错。
- 抽帧检查：
  - `45s`：仍处于重复开场 context/过渡页，字幕存在短暂空窗，属于开场重复段的边界残留。
  - `50s`：开场 context 字幕恢复显示。
  - `130s / 153s / 160s / 180s`：章节卡、右侧说明、底部字幕均已跟随当前主题段落，没有再出现此前 10-30 秒级累计漂移。

## 补充验证：整片 ASR 定时 + 脚本文案字幕

- 修复主题级 16:9 模板的字幕最终生成策略：
  - ASR 只负责提供真实时间轴和 segment 匹配窗口。
  - 最终 SRT 文本优先使用 AI 口播脚本文案，避免直接烧录 ASR 误识别文本。
  - 校准报告中的 `subtitle_source` 更新为 `full_audio_asr_timing_script_text`，便于后续排查是否真的采用了“ASR 定时 + 脚本文案”模式。
- 修复主题级 16:9 的字幕烧录方式：
  - 对 theme 模式不再依赖 ffmpeg `subtitles` filter 在 concat still images 上做时间匹配。
  - 改为在合成前逐帧读取 SRT 并把当前时间点字幕直接绘制到帧图上，避免 42s/45s 等位置出现字幕空白或错位。
  - 非 theme 模式仍保留原有 ffmpeg 字幕流程，兼容旧调用方。
- 修复字幕换行细节：
  - 保留英文词组空格，例如 `Quanta Services` 不再变成 `QuantaServices`。
  - 避免把 `50%`、`770港元`、`800元`、`2.6TWh` 这类数字/单位 token 从中间拆行。
  - 清理中文 ASR 片段中的异常空格，减少 `电 力`、`基 础设施` 一类显示瑕疵。
- `test-10` 最终候选输出：
  - 视频：`data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/outputs/task/16x9/test-10_16x9_57e8e5f5.mp4`
  - SRT：`data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/timelines/8a5260e6_timeline.srt`
  - 本地验证 JSON：`tmp/test10_scriptclean_latest_result.json`
- 最终验证结果：
  - ASR 缓存命中：`data/processed/asr_cache/47e16d9a79*.json`。
  - `matched_segment_count=6`，`unmatched_segment_count=0`。
  - `ffprobe`：H.264 1920x1080 30fps，视频流 222.567 秒，音频流 222.487 秒，容器 222.567 秒。
  - `ffmpeg -v error -i ... -f null -` 完整解码无报错。
  - SRT 搜索确认不再出现 `书店`、`铃电池` 等 ASR 错词；正确出现 `输电`、`锂电池`、`Quanta Services`。
  - 抽帧检查 `42s / 50s / 130s / 153s / 160s / 180s`：左侧章节高亮、右侧详细说明、来源缩略图和底部字幕均正常；字幕不遮挡正文内容。

## 17:00 后全链路自测补充

- 在用户授权的高标准自测窗口内继续复用真实任务 `test-10`：
  - 任务 ID：`43297565-c5fa-49a2-a4a5-ec381d588773`
  - 音频：`data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/audios/891c9164_doubao朗读_20260609_142534.wav`
  - 音频时长：`222.487s`
- 继续修正并验证模板细节：
  - 开场文案优先使用 `topic_opening` 段完整 `voice_text`，避免 `opening_text` 过短或带省略号时造成开场钩子不完整。
  - 清理机构名称，避免“伯恩斯坦是全球...”一类长描述污染来源栏。
  - 主题开场字体会按宽度动态缩小，尽量完整展示，不再粗暴半字截断。
  - 烧录字幕时，中文换行不再转成空格，解决 `的 全面` 这类视觉瑕疵；英文词组仍保留空格。
  - SRT 换行增加保护词规则，避免把 `全面`、`数据中心`、`复合效应`、`锂电池` 等常见词从中间拆开。
  - 右侧说明卡、标题和来源卡的通用折行升级为“保护词 + 数字/英文 token”策略，避免 `2026年`、`2.6TWh`、`宁德时代` 等关键词在行尾被切碎。
- 最新候选成片：
  - 视频：`data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/outputs/task/16x9/test-10_16x9_94ebc4c8.mp4`
  - SRT：`data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/timelines/f946b7e2_timeline.srt`
  - 本地结果 JSON：`tmp/test10_final_latest_result.json`
- 最新候选验证结果：
  - `ffprobe`：H.264 1920x1080 30fps，视频流 `222.566667s`，音频流 `222.487000s`，容器 `222.567000s`。
  - `ffmpeg -v error -i ... -f null -` 完整解码无报错。
  - SRT 搜索确认没有 `书店`、`铃电池` 这类 ASR 错词；正确出现 `Quanta Services`、`输电`、`50%`、`770港元`、`锂电池`、`2.6TWh`。
  - 抽帧 `0.5s / 2s / 4s / 5.5s / 8s`：多研报封面证据、联合背书总结、黄金三秒钩子均正常展示，证据展示时间已超过 5 秒。
  - 抽帧 `130s / 160s / 180s`：正文页左侧主线高亮、右侧详细说明、来源缩略图和底部字幕均正常；未发现字幕遮挡主体、右侧文字撞底或关键数字/实体名断词。
  - 已将最新候选成片登记到云端 `report_video_outputs`，输出 ID：`ee205e51-d4dc-405c-886a-7de5c6e828cb`。
  - 读回确认：任务状态为 `video_ready`，输出记录状态为 `completed`，任务关联输出数量为 2，最新输出可被任务关联读取。
- 最新自动校验：
  - `venv/bin/python -m py_compile src/backend/apis/v1/report_video.py src/backend/services/report_video_ai_service.py src/backend/services/report_video_prompt_service.py src/backend/services/report_video_render_service.py src/backend/tasks/report_video.py src/backend/services/asr_cache_service.py src/backend/celery_config.py`
  - `node --check src/frontend/js/report-video-factory.js`
  - `PYTHONPATH=src/backend venv/bin/python -m unittest tests.integration.test_report_video_full_audio_timeline`，7 个测试通过，其中 2 个真实环境长流程测试按原逻辑跳过。

## 后续

- 主题脚本当前作为策划层产物展示，尚未直接桥接成可渲染的主题级虚拟 segment。
- 后续可在确认主题脚本结构稳定后，增加“生成主题视频”桥接逻辑。
- 外部热度搜索可在联网策略确认后接入真实搜索源。
- 扫描版 PDF 的高质量正文解析仍需要异步 OCR 任务队列，不应该在 Web 请求或同步测试里直接跑完整 OCR。
- 自动扫描真实创建任务和查库去重需要在本机正常后端环境中测试；当前 Codex 沙箱被云 PG 网络访问限制挡住。

## 20:30 研报视频模板与音频链路补充

- 根据测试反馈继续升级 16:9 主题级视频：
  - 多研报联合背书开场默认按 10 秒窗口分配。
  - 其中约 7.8 秒用于放大展示多份研报封面，约 2.2 秒用于联合背书总结。
  - 封面卡尺寸由 `330x440` 调整为 `430x560`，最多展示 4 张大封面，避免背书感太弱。
- 调整整片音频上传策略：
  - `整片音频 + 时间轴` 模式新增音频语速选项，前端默认 `1.2x`，可切换 `1.0x / 1.2x / 1.3x`。
  - 后端在入库前通过 ffmpeg `atempo` 生成变速后的最终音频，后续 duration、ASR、字幕匹配和视频合成都基于该最终音频。
  - 如果上传/粘贴了 SRT 或时间轴，系统会按音频变速比例同步缩放时间轴，避免字幕仍按原速时间播放。
  - 如果未上传 SRT，默认强制投递整片 ASR 生成真实字幕时间轴；脚本时长推算只在用户明确关闭 ASR 时作为兜底路径。
  - 强制 ASR 失败时不再伪装成功、不再自动退回推算，会把音频与任务标记为 `audio_failed`，提示上传 SRT 或检查音频。
- 校准报告增强：
  - `subtitle_calibration_report` 增加 `audio_speed` 与 `timeline_speed_adjustment` 字段，便于判断本次是否基于变速后的音频完成字幕匹配。
  - Celery 异步整片 ASR 任务也记录 `audio_speed`，且默认 `fallback_to_script_estimate=False`。
- 前端同步：
  - 音频匹配说明改为“未上传 SRT 时默认整片 ASR”。
  - `无 SRT 时使用 ASR 生成真实字幕` 默认勾选。
  - 新增 `整片音频语速` 下拉框，并补充与现有表单一致的样式。
- 本地验证：
  - `venv/bin/python -m py_compile src/backend/apis/v1/report_video.py src/backend/tasks/report_video.py src/backend/services/report_video_render_service.py` 通过。
  - `node --check src/frontend/js/report-video-factory.js` 通过。
  - `PYTHONPATH=src/backend venv/bin/python -m unittest tests.integration.test_report_video_full_audio_timeline` 通过，7 个测试 OK，其中 2 个真实环境长流程测试按原逻辑跳过。
  - 临时 6 秒测试音频经过 1.2x 变速后输出约 5.002 秒，确认后端实际生成变速后的新音频。
  - 函数级验证：整片音频无 SRT 默认强制 ASR，显式关闭时才允许不用 ASR；SRT 时间轴在 1.2x 下从 `0-12/12-24` 缩放为 `0-10/10-20`。
- 真实任务回归：
  - 复用 `test-10` 重新渲染得到：
    `data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/outputs/task/16x9/test-10_16x9_a7617a33.mp4`
  - 该任务沿用历史上传音频与旧时间轴，因此校准报告仍显示 `fallback/no_asr_anchor`；这是旧数据状态，不代表新上传链路。
  - 抽帧 `1s / 4s / 8s` 生成 contact sheet：
    `tmp/test10_a7617_frames/contact.jpg`
  - 抽帧确认：4 秒位置已有 4 张大研报封面，开场字幕与证据画面同时展示；8 秒进入联合背书总结。

## 21:00 自测：1.2x 音频 + 整片 ASR + 画面/字幕同步

- 自测目标：
  - 验证不上传 SRT 时是否真的走“音频变速后整片 ASR”，而不是旧的脚本推算。
  - 验证生成视频的画面布局、字幕位置和音频/字幕时间是否正常。
- 自测方式：
  - 复用真实任务 `test-10`，任务 ID：`43297565-c5fa-49a2-a4a5-ec381d588773`。
  - 原始音频：
    `data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/audios/891c9164_doubao朗读_20260609_142534.wav`
  - 临时生成 1.2x 音频：
    `data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/audios/891c9164_doubao朗读_20260609_142534_speed_1_2.m4a`
  - 通过本地 MLX-Whisper medium 对 1.2x 后的音频执行整片 ASR，再用 ASR 时间轴匹配 segment，并临时渲染 16:9 视频。
  - 数据库操作最后 rollback，只留下自测文件。
- ASR 与时间轴结果：
  - 1.2x 音频时长：`185.408s`。
  - ASR 耗时约 `49.41s`。
  - ASR 原始片段 `121` 条，合并后时间轴 `49` 条。
  - ASR 缓存：本次首次识别，`cache_hit=false`，缓存 key 前缀 `cc73ed8826`。
  - 6 个业务 segment 全部匹配：`matched=6 / unmatched=0`。
  - 匹配分数：`0.9727 / 0.9653 / 0.9811 / 0.8777 / 0.8694 / 0.9802`。
  - 校准报告状态：`status=calibrated`。
  - 字幕来源：`full_audio_asr_timing_script_text`，即 ASR 提供真实时间，字幕文本仍使用脚本文案。
  - 音频变速信息进入校准报告：`audio_speed.speed=1.2`，`applied=true`。
- 自测输出：
  - 视频：
    `data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/outputs/task/16x9/test-10_16x9_068a7b01.mp4`
  - SRT：
    `data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/timelines/d4332e70_timeline.srt`
  - 自测结果 JSON：
    `tmp/report_video_selftest_speed_asr_result.json`
- 媒体检查：
  - `ffprobe`：视频 H.264 1920x1080 30fps，视频流 `185.500s`，音频流 `185.429s`，容器 `185.500s`。
  - `ffmpeg -v error -i ... -f null -` 完整解码无报错。
  - SRT：`45` 条字幕，无重叠，最后一条结束于 `185.24s`，与音频 `185.429s` 基本对齐。
- 抽帧检查：
  - 开场抽帧 contact sheet：
    `tmp/report_video_selftest_frames/opening_contact.jpg`
    - `2s / 6s`：大封面背书正常，字幕不遮挡封面。
    - `12s`：进入正文页，左侧主线和右侧卡片正常。
  - 正文抽帧 contact sheet：
    `tmp/report_video_selftest_frames/mid_contact_1.jpg`
    `tmp/report_video_selftest_frames/mid_contact_2.jpg`
    - `70s / 90s / 112s / 132s / 150s / 180s`：章节高亮、右侧说明、底部字幕均正常；未发现字幕遮挡主体。
  - 重复开场 context 抽帧：
    `tmp/report_video_selftest_frames/context_contact.jpg`
    - `45s / 55s`：测试音频里存在重复开场，系统以“开场/过渡”画面承接，底部字幕与该段音频内容一致。
- 结论：
  - 新链路已验证为真实 `1.2x -> 整片 ASR -> segment 匹配 -> 渲染`，不是旧推算。
  - 当前自测视频画面可看，音频流、视频流、SRT 时间长度一致性正常。
  - 残留观察：ASR 文本自身仍可能出现“书店/铃电池”这类识别错词，但最终烧录字幕使用脚本文案，因此画面字幕没有直接暴露这些 ASR 错词。

## 21:40 最终自查：开场证据链安全区与字幕清理

- 自查中发现的问题：
  - 多研报封面已经放大，但开场 `6s` 位置封面底部与烧录字幕区域距离过近，底部机构标签有被字幕压住的风险。
  - 个别字幕正文中仍保留半角英文逗号，观感偏机器化。
- 修复内容：
  - 开场证据链封面卡从 `430x560` 调整为 `420x520`，整体上移，并将主题标签从 `y=944` 上移到 `y=858`，给底部 `188px` 字幕区留出安全距离。
  - 渲染层 `_display_text()` 与 SRT 生成层 `_normalize_subtitle_display_text()` 统一将半角逗号转为中文逗号。
- 最终自测输出：
  - 视频：
    `data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/outputs/task/16x9/test-10_16x9_3d7a5f81.mp4`
  - SRT：
    `data/tasks/43297565-c5fa-49a2-a4a5-ec381d588773/timelines/0b250171_timeline.srt`
  - 自测结果 JSON：
    `tmp/report_video_selftest_speed_asr_final_12s_v3.json`
  - 抽帧目录：
    `tmp/report_video_selftest_final_12s_v3_frames/`
- 最终检查结果：
  - `py_compile` 通过：`report_video.py`、`report_video_render_service.py`、`tasks/report_video.py`。
  - `node --check src/frontend/js/report-video-factory.js` 通过。
  - `PYTHONPATH=src/backend venv/bin/python -m unittest tests.integration.test_report_video_full_audio_timeline` 通过，`7` 个测试 OK，`2` 个真实环境长流程测试按原逻辑跳过。
  - `ffprobe`：H.264 `1920x1080`、`30fps`，视频流 `185.500s`，音频流 `185.429s`，容器 `185.567s`。
  - `ffmpeg -v error -i ... -f null -` 完整解码无报错。
  - SRT：`45` 条字幕，最多 `2` 行，无空字幕、无时间重叠，正文半角逗号数量为 `0`。
  - SRT 明显错词扫描结果为空：未发现 `书店 / 铃电池 / 理电池 / Gwanta / 8电 / 复核 / TW时 / 博恩斯坦 / 电器设备` 等暴露到字幕正文。
  - 抽帧确认：
    - `2s / 6s`：多研报封面背书正常，封面不再压到底部字幕安全区。
    - `10s / 11s`：仍处于联合背书总结卡，开场证据链达到 10 秒以上。
    - `12s / 14s`：进入正文模板，左侧章节主线、右侧详细说明、底部字幕布局正常。
    - `70s / 112s / 150s / 180s`：章节高亮、右侧说明卡、来源缩略图与字幕区域均未发现遮挡。
- 结论：
  - 在当前可自动检查范围内，最终版 `test-10_16x9_3d7a5f81.mp4` 没发现阻塞问题。
  - 该自测是基于 `test-10` 的本地内存渲染，数据库最终 rollback；页面新任务仍需要用户通过实际 UI 再跑一次全链路确认。
