# 2026-06-08 Project Status

## 研报导图视频工厂：时间轴智能校准

用户反馈整段音频与外部时间轴经常对不上，希望不要默认整段 ASR 跑满，而是采用更节省资源的智能校准方式。

本次实现：

- 在 `src/backend/apis/v1/report_video.py` 新增 `smart` 时间轴校准链路。
- 上传 `full_audio_srt` 模式时，默认执行两段式校准：
  - 第一步：按 `音频总时长 / 时间轴总时长` 先做全局比例缩放。
  - 第二步：若总时长差异明显，再抽样 3 个 60 秒窗口做本地 ASR，使用文本匹配找到时间轴锚点，并对整条时间轴再做一次线性修正。
- 校准元数据会写入 `task.result.full_audio.retime`，包含：
  - `strategy`
  - `duration_scale`
  - `anchors`
  - `correction`
  - `calibration_mode`

实现说明：

- 默认模式：`smart`
- 可通过表单字段控制：
  - `timeline_calibration_mode`
  - `timeline_calibration_model`
- 当前默认 ASR 抽样模型使用 `small`
- 失败自动回退到单纯 `duration_scale`，不会阻塞上传流程

## 研报导图视频工厂：节点切换平滑化

用户反馈导图视频里各节点切换太硬。

当前旧方案：

- 每个 segment 先生成一张静态导图图像
- 再用 concat 方式按时长直接拼接
- 本质上是硬切，不是真正的镜头漫游

本次改造：

- 在 `src/backend/services/report_video_render_service.py` 的 `make_concat_video()` 中加入 `xfade`
- 多帧场景下使用交叉淡入淡出替代硬切
- 过渡时长默认 `0.6s`
- 仍保留现有静态导图出图逻辑，不改任务数据结构
- 单帧或不适合转场时自动回退原 concat 方案

说明：

- 这次是“低风险平滑版”，先把硬切改顺
- 还不是完整的导图镜头漫游
- 后续如继续升级，可把横屏方案改为真正的 `pan + zoom + easing`

## 测试

- `venv/bin/python -m py_compile src/backend/apis/v1/report_video.py src/backend/services/report_video_render_service.py` 通过
- `PYTHONPATH=src/backend venv/bin/python -m unittest tests.integration.test_report_video_full_audio_timeline` 通过
- 新增单测覆盖：
  - 时间轴抽样窗口生成
  - 线性校准参数拟合
  - fake ASR sampler 下的 `smart` 重定时

真实验证：

- 使用根目录音频 `doubao朗读_20260607_200545.wav`
- 使用 `test-4` 时间轴与云端 PG 中的 `test-4` 任务
- `RUN_REPORT_VIDEO_INTEGRATION=1 ... test_test4_upload_full_audio_with_timeline` 通过
- `RUN_REPORT_VIDEO_RENDER=1 ... test_test4_render_full_audio_video` 通过

真实校准结果：

- `strategy`: `smart_asr`
- `calibration_mode`: `smart`
- `anchor_count`: `2`
- `duration_scale.scale_factor`: `0.6483952879581152`
- `correction`: `{"slope": 0.933771, "intercept": -2.695063}`

真实输出视频：

- `data/tasks/3cd39d26-30d9-499d-9c56-6a3c5dc469ed/outputs/task/16x9/test-4_16x9_ade8675a.mp4`
- 时长：`247.126s`
- 状态：`completed`

## Celery 控制台：补充研报工厂业务阶段统计

用户指出“批量上传 PDF 并解析”已经拆成 Celery，但监控页看不出来。

排查结果：

- `src/backend/apis/v1/report_video.py` 上传 PDF 后会立即 `apply_async(..., queue="report_video")`
- `src/backend/tasks/report_video.py` 已有独立任务：
  - `parse_report_pdf_task`
  - `generate_ai_scripts_task`
  - `generate_videos_task`
- 之前监控页只展示队列积压和 inspect 的 active/reserved/scheduled
- 当解析任务很快被 worker 取走时，卡片可能显示 `积压 0 / 执行中 0`，但业务上其实仍处于 `parsing` 或 `ai_generating`

本次补充：

- `src/backend/apis/v1/celery_monitor.py`
  - 为 `report_video` 服务增加数据库业务阶段统计
  - 返回：
    - `task_status`
    - `parse_status`
    - `ai_status`
    - `totals`
- `src/frontend/js/celery-monitor.js`
  - 为“研报导图视频工厂”卡片增加业务阶段面板
  - 显示：
    - 待解析
    - 解析中
    - 已解析
    - 解析失败
    - AI 待处理
    - AI 生成中
    - AI 阻塞
    - 脚本就绪
    - 视频生成中
- `src/frontend/celery-monitor.html`
  - 更新前端资源版本，避免浏览器继续使用旧脚本

这样之后，Celery 控制台可以同时回答两件事：

- worker / 队列当前是否正常
- 研报工厂业务当前卡在哪个阶段

## 研报导图视频：版式重构与字幕安全区

用户反馈当前导图视频有两个明显问题：

- 详细描述和字幕都放在下方，字幕会挡住正文说明
- 导图本身形态偏朴素，不够像成片

本次改造：

- `src/backend/services/report_video_render_service.py`
  - 横屏 `render_landscape_frame()` 改为双栏结构：
    - 左侧：导图焦点面板
    - 右侧：当前节点解读卡片
    - 底部：独立字幕安全区
  - 原先“上下结构”改为“主内容区 + 底部字幕区”彻底分离
  - 导图节点样式升级：
    - 中心主题强化
    - 当前节点高亮
    - 其他节点卡片化
    - 背景加入轻网格和阴影层次
  - 右侧说明卡片增加：
    - 当前聚焦
    - 中心主题标签
    - 本段解读
    - 关联节点 chips
  - 字幕样式改为更靠下的安全位置：
    - `FontSize=30`
    - `MarginV=56`
  - 竖屏 `render_portrait_frame()` 也同步预留底部字幕安全区，避免说明卡片压到字幕区域
  - 竖屏说明文本行数收敛，优先保证不遮挡、不溢出

本地样张验证：

- `tmp/report_video_preview/landscape.jpg`
- `tmp/report_video_preview/portrait.jpg`

验证方式：

- 使用伪造 report / segment 对象直接调用渲染函数
- 通过本地图片检查确认新版布局已经把字幕区和正文区分开

## 研报导图视频模板：16:9 二次优化

本轮目标：

- 不重构 PDF / AI / 音频 / 合成主流程
- 只优化视频模板、导图聚焦、字幕、信息层级和专业感

主要改动：

- `src/backend/services/report_video_render_service.py`
  - 16:9 横屏重新收敛为三段结构：
    - 顶部标题区
    - 中间内容区
    - 底部独立字幕区
  - 左侧导图区改为“中心主题 → 当前节点 → 子节点”主聚焦结构
  - 当前节点更大、更亮，并带更强外发光和高亮连线
  - 非当前节点明显弱化
  - 当前节点子节点单独成排显示，并支持在 segment 内部轮流高亮
  - 右侧从“大段正文”改成“要点卡片”：
    - 当前节点 / 当前子节点
    - 当前段落标题
    - 2-3 条核心要点
    - 节点关系
    - 子节点 chips
  - 9:16 竖屏改成单列卡片式布局：
    - 顶部标题
    - 中心主题
    - 当前节点
    - 要点卡片
    - 底部字幕安全区
  - segment 内部支持轻量导图变化：
    - 若当前节点有子节点，按时长切成若干视觉状态
    - 子节点可在当前 segment 内依次高亮

- `src/backend/apis/v1/report_video.py`
  - `timeline.srt` 改为短句字幕输出
  - 长句按中文标点拆分
  - 每条字幕最多两行
  - 行宽按约 18 个中文字符控制
  - 多个短句在原时间段内均匀分配

- 字幕烧录样式同步调整：
  - 字号从 `30` 收到 `24`
  - `MarginV` 下移到独立字幕区
  - outline 变轻，避免压画面

验证产物：

- 横版样张：
  - `tmp/report_video_template_v2/landscape_1.jpg`
- 竖版样张：
  - `tmp/report_video_template_v2/portrait.jpg`
- 短预览视频：
  - `tmp/report_video_template_v2/preview.mp4`

预览视频说明：

- 使用伪造 report / segment 数据生成
- 带字幕
- 含 4 个状态帧和轻量切换
- 时长约 `7.6s`

## 2026-06-08 晚间补充：16:9 模板二次收敛

本轮继续只动模板表现，不改业务主流程。

主要补充：

- `src/backend/services/report_video_render_service.py`
  - 当前聚焦节点继续强化：
    - 当前主节点保持更大尺寸与发光描边
    - 当前子节点高亮从“偏白块”改成蓝色体系，避免抢眼白块
    - 非激活子节点改成更深的低对比卡片
    - 子节点连线进一步弱化，只有当前活跃支路更亮
  - 左侧导图视觉继续收敛：
    - 不再显示无关侧边节点
    - 只保留当前节点和直接子节点
    - 背景网格透明度继续降低
  - 右侧要点卡片继续收敛：
    - 要点优先取当前子节点标签，避免从正文硬截断导致大量省略号
    - 每段稳定展示 2-3 条短要点
    - 节点关系区域保持弱化，不再与正文竞争
  - 标题继续收敛：
    - 主标题 / 副标题拆分逻辑生效
    - 过长标题优先压缩到顶部两层信息里
  - 字幕继续收敛：
    - ffmpeg 烧录字幕字号继续下调到更适合底部安全区的大小
    - 底部字幕带与中间内容区保持明确分隔

验证产物（v3）：

- 横版关键帧：
  - `tmp/report_video_template_v3/landscape_1.jpg`
  - `tmp/report_video_template_v3/landscape_2.jpg`
  - `tmp/report_video_template_v3/landscape_3.jpg`
  - `tmp/report_video_template_v3/landscape_4.jpg`
- 竖版样张：
  - `tmp/report_video_template_v3/portrait.jpg`
- 带字幕预览：
  - `tmp/report_video_template_v3/preview.mp4`
- 预览抽帧：
  - `tmp/report_video_template_v3/preview_frame_1.jpg`
  - `tmp/report_video_template_v3/preview_frame_5.jpg`

## 2026-06-08 晚间补充：16:9 模板三次修正

根据新一轮反馈，继续只修正模板表现，不改 PDF / AI / 音频匹配主流程。

本轮修正点：

- 删除顶部无意义模板标签：
  - 去掉 `AI 自动生成`
  - 去掉 `16:9 横屏`
  - 顶部改为对观众有意义的结构：`研报解读｜当前主题` + 主标题 + 副标题

- 恢复右侧“详细说明”：
  - 不再只剩 2-3 个关键词
  - 新增 `详细说明` 区块，优先保留 60-100 个中文字符的解释性内容
  - 同时保留 `核心要点` 2-3 条
  - 去掉右下角容易像网页控件的白色 chips

- 增加真实导图过渡帧：
  - `segment` 开始阶段自动插入过渡状态
  - 当前节点从上一个分支位置向焦点位推进
  - 当前节点 scale 逐步放大到焦点态
  - 主连线、发光描边、子节点显隐随进度变化
  - 子节点继续支持后续依次高亮
  - 保持现有视频合成主流程不变，只增强模板渲染状态

新增验证产物（v4）：

- 模板关键帧：
  - `tmp/report_video_template_v4/landscape_1.jpg`
  - `tmp/report_video_template_v4/landscape_2.jpg`
  - `tmp/report_video_template_v4/landscape_3.jpg`
  - `tmp/report_video_template_v4/landscape_4.jpg`
- 带字幕过渡预览：
  - `tmp/report_video_template_v4/preview.mp4`
- 关键抽帧：
  - `tmp/report_video_template_v4/preview_frame_a.jpg`
  - `tmp/report_video_template_v4/preview_frame_b.jpg`
  - `tmp/report_video_template_v4/preview_frame_c.jpg`

## 2026-06-08 晚间补充：整片音频自动生成时间轴

在 `report-video-factory` 的音频匹配流程中新增了“无 SRT 自动生成时间轴”分支。

行为变更：

- 当用户选择 `整片音频 + 时间轴` 模式时：
  - 如果上传了 SRT / 时间轴文本：继续优先使用手工时间轴
  - 如果没有上传 SRT，也没有粘贴时间轴文本：
    - 系统会直接基于当前任务已有的 `segments.voice_text`
    - 按整段音频总时长自动分配每段起止时间
    - 自动生成一份时间轴并继续后续匹配、字幕输出和视频生成

实现文件：

- `src/backend/apis/v1/report_video.py`
  - 新增 `_auto_generate_timeline_entries(...)`
  - 新增 `_write_timeline_source_text(...)`
  - `upload_audios` 中补充无时间轴时的自动生成分支
- `src/frontend/js/report-video-factory.js`
  - 去掉“整片音频模式必须手工提供 SRT”的前端拦截
  - 匹配完成后如果使用了自动生成时间轴，会给出明确提示
- `src/frontend/report-video-factory.html`
  - 音频匹配说明文案更新为支持“留空自动生成时间轴”

当前自动生成策略：

- 优先按 `estimated_duration_sec` 分配 segment 时长
- 如果没有估计时长，则按口播稿文本长度和标点复杂度估算权重
- 保证生成出的时间轴总时长与上传音频总时长闭合

验证：

- `py_compile` 通过
- `node --check` 通过
- 本地 helper 测试通过，生成的 timeline 总时长与目标音频总时长一致

## 2026-06-08 夜间补充：16:9 模板增加研报来源背书层

本轮继续保持主流程不动，只增强 16:9 研报解读视频模板的“真实研报来源感”。

### 新增内容

- 每篇研报开始时，插入一段独立的“来源研报”来源卡画面：
  - 展示研报封面 / 首屏截图
  - 展示机构名称、主题标签、研报标题、日期
  - 展示一句核心结论
  - 保持深色财经科技风，不做全屏 PDF 平铺背景

- 正式解读页面新增“来源信息条”：
  - 格式为 `来源研报：机构《标题》｜日期`
  - 放在标题区下方，长期提醒观众当前内容有出处
  - 不显示系统内部信息、不显示模板参数

- 正式解读页面新增“小型来源缩略图区域”：
  - 放在右侧说明卡片右上角
  - 默认显示当前研报封面页 / 首屏页
  - 如果 segment 的 `raw_payload.source_page` 存在，则正文页缩略图跟随切换到对应页
  - 该缩略图属于证据层，不抢导图和右侧说明的主视觉

### 实现方式

- 文件：
  - `src/backend/services/report_video_render_service.py`

- 新增 / 扩展能力：
  - `_report_source_meta(...)`
    - 统一抽取机构、标题、日期、主题、核心结论、来源行文案、来源页码
  - `_ensure_report_pdf_preview(...)`
    - 使用 `fitz` 渲染 PDF 页面缩略图并缓存到 `preview_cache/`
  - `_load_report_pdf_preview(...)`
    - 读取并缩放缩略图
  - `_paste_rounded_image(...)`
    - 将 PDF 预览图贴入来源卡或正文小缩略图区域
  - `_source_intro_duration(...)`
    - 控制每篇研报首段视觉时长里分配给来源卡的时长，避免破坏现有音频主流程
  - `render_landscape_source_card(...)`
    - 渲染 16:9 来源卡页面

- 在 `render_report_video(...)` 和 `render_task_video(...)` 中：
  - 16:9 模式下，为每篇研报首段自动插入来源卡帧
  - 来源卡时长占用首段前 1.2~1.6 秒视觉时长
  - 不改音频主链，只在视觉层增加来源卡过渡

### 视觉修正

- 来源卡使用研报封面页作为主缩略图，不使用段落页，符合“先说明是哪篇研报”的目标
- 正文页右上角缩略图可根据 `source_page` 展示对应来源页，增强段落级背书感
- 右侧卡片在详细说明较长时自动减少要点数量，避免把新增来源层挤进字幕区
- 背景网格继续保持弱化，来源层不抢主视觉

### 本地验证产物

- 来源背书预览视频：
  - `tmp/report_video_source_layer/preview.mp4`
- 关键帧：
  - `tmp/report_video_source_layer/frames/source_1.jpg`
  - `tmp/report_video_source_layer/frames/body_1.jpg`
  - `tmp/report_video_source_layer/frames/source_2.jpg`
  - `tmp/report_video_source_layer/frames/body_2.jpg`

### 验证

- `venv/bin/python -m py_compile src/backend/services/report_video_render_service.py` 通过
- 本地 helper 脚本成功生成 2 篇研报的来源卡 + 正文页短预览视频

## 2026-06-08 深夜补充：来源卡 5 秒化 + sampled_asr 默认校准

本轮继续保持主流程不动，只增强两块：

1. 来源研报卡不再一闪而过，默认拉长到 5 秒；
2. 长音频字幕默认启用低成本 `sampled_asr` 校准，减少累计偏移。

### 来源卡升级

- 文件：
  - `src/backend/services/report_video_render_service.py`

- 变化：
  - 新增 `DEFAULT_SOURCE_CARD_DURATION_SEC = 5.0`
  - 新增 `IMPORTANT_SOURCE_CARD_DURATION_SEC = 6.0`
  - 支持从 `task.result / analysis.raw_payload / report.raw_meta` 读取：
    - `source_card_duration_sec`
    - `source_card_duration_sec_for_important_report`
    - `important_report`
  - 16:9 来源卡时长默认按 5 秒处理，重点研报可扩到 6 秒
  - 新增 `_source_card_visual_states(...)`
    - 让来源卡在 5 秒内按阶段逐步显露信息：
      - 先看到封面页
      - 再看到机构 / 标题
      - 再看到日期 / 主题
      - 最后看到一句核心结论
  - 单篇视频和整片长音频视频在渲染时都会把来源卡时长插入总时间线
  - 为了保持音画同步，渲染阶段会自动：
    - 给音频插入静音段
    - 给字幕做统一偏移

### 音频 / 字幕时间线增强

- 新增：
  - `_concat_audio_with_silence_inserts(...)`
  - `_shift_srt_by_inserts(...)`
  - `_expand_timeline_spans_with_source_cards(...)`

- 作用：
  - 多篇研报串烧时，每篇研报前都能插入来源卡
  - 音频主轨会同步插入静音，避免正文音频提前说话
  - SRT 会同步整体顺移，避免来源卡阶段字幕抢主体

### sampled_asr 默认校准

- 文件：
  - `src/backend/apis/v1/report_video.py`
  - `src/frontend/js/report-video-factory.js`
  - `src/frontend/report-video-factory.html`

- 变化：
  - `timeline_calibration_mode` 默认从原来的 `smart` 切到 `sampled_asr`
  - 默认参数：
    - `sample_interval_sec = 180`
    - `sample_duration_sec = 10`
    - `min_anchor_confidence = 0.75`
    - `enable_vad = true`
  - 抽样窗口规则升级：
    - 短音频走开头 / 中间 / 结尾
    - 长音频按 3~5 分钟间隔抽样
  - 锚点置信度低于阈值时丢弃
  - 校准方式按锚点数自动切换：
    - 1 个锚点：整体平移
    - 2~3 个锚点：线性校准
    - 4 个及以上锚点：分段线性校准
  - 校准失败时自动回退，不阻塞视频生成
  - 明确保证：
    - 字幕文本仍然使用原文案
    - ASR 只用于修正时间戳，不污染正式字幕内容

### 校准报告字段

- `retime` / 校准报告现在会输出：
  - `status`
  - `calibration_method`
  - `sample_count`
  - `asr_total_duration_sec`
  - `asr_ratio`
  - `anchor_count`
  - `average_offset_sec`
  - `max_offset_sec`
  - `confidence_avg`
  - `message`

### 前端提示

- 音频匹配说明文案更新为：
  - 自动生成时间轴后，默认追加抽样 ASR 校准
- 上传完成后的提示会显示：
  - 自动生成时间轴是否启用
  - 抽样校准是否成功
  - 锚点数量与平均偏移

### 本地验证

- 语法检查：
  - `venv/bin/python -m py_compile src/backend/apis/v1/report_video.py src/backend/services/report_video_render_service.py`
  - `node --check src/frontend/js/report-video-factory.js`
- mock sampled_asr 校准验证：
  - 3 个锚点
  - `status = calibrated`
  - `calibration_method = linear`
  - `asr_ratio = 41.7%`
- 5 秒来源卡短预览：
  - `tmp/report_video_source_layer_v2/preview.mp4`
  - `tmp/report_video_source_layer_v2/frames/source_01.jpg`
  - `tmp/report_video_source_layer_v2/frames/source_04.jpg`
  - `tmp/report_video_source_layer_v2/frames/body.jpg`

## 2026-06-08 17:35 CST - test-8 视频失败定位与修复

### 现象

- 任务：`test-8`
- 任务 ID：`bd8bdc5f-d052-4b40-8fa2-dfa47c6ddb6e`
- 页面状态：`视频失败`
- 直接错误点：`ffmpeg concat audio_with_source_cards.m4a` 非零退出

### 根因

- 新增来源卡后，系统会在整片音频里插入静音段。
- 原始口播音频规格是：
  - `24000 Hz`
  - `mono`
- 但静音段被错误生成成：
  - `44100 Hz`
  - `stereo`
- 两种 AAC 片段被直接送进 concat demuxer 后，ffmpeg 在解码阶段报大量 `Invalid data found when processing input`，最终导致视频生成失败。

### 修复

- 文件：
  - `src/backend/services/report_video_render_service.py`

- 新增：
  - `audio_stream_profile(path)`

- 调整：
  - `_concat_audio_with_silence_inserts(...)`
    - 先用 `ffprobe` 读取原始音频的采样率、声道数、声道布局
    - 切分出的 `part_xxx.m4a` 统一按原音频规格重新编码
    - 插入的 `silence_xxx.m4a` 也统一按原音频规格生成

### 本地验证

- 语法检查通过：
  - `venv/bin/python -m py_compile src/backend/services/report_video_render_service.py`
- 使用 `test-8` 原始音频单独重跑来源卡拼接：
  - 产物：`data/tasks/bd8bdc5f-d052-4b40-8fa2-dfa47c6ddb6e/outputs/task/16x9/audio_with_source_cards_fixed.m4a`
  - `ffprobe` 结果：
    - `24000 Hz`
    - `mono`
    - 时长 `257.419s`
- 说明修复后的“来源卡插音频”链路已通过。

### 后续动作

- 需要让 `report_video` worker 重新加载代码后，再重新投递 `test-8` 的视频生成任务。
- 失败不是任务内容问题，也不是 sampled_asr 问题，核心就是来源卡静音段音频规格不一致。

## 2026-06-08 18:20 CST - 16:9 左侧导图布局修正

### 现象

- 用户反馈 16:9 研报解读模板左侧“思维导图聚焦”区域布局混乱。
- 当前主节点放大后，与下方子节点、斜向连线和背景网格产生了明显视觉打架。
- 结果是当前焦点虽然亮，但整个左侧构图不稳定，看起来像“挤在一起”。

### 修复方向

- 不改主流程，只修正模板渲染表现。
- 把“主节点直接斜连到底部多个子节点”的布局，改成更稳定的：
  - 主干线
  - 横向分支线
  - 子节点垂直落点
- 同时收敛当前主节点尺寸，并按文本宽度动态计算子节点卡片宽度，减少并排时的挤压。

### 修改内容

- 文件：
  - `src/backend/services/report_video_render_service.py`

- 新增：
  - `_measure_text_block(...)`
  - `_line_visual_length(...)`
  - `_rebalance_wrapped_lines(...)`
  - `_adaptive_text_layout(...)`

- 调整：
  - 左侧背景网格进一步弱化
  - 当前主节点高度与宽度略收
  - 子节点卡片按内容宽度自适应
  - 分支连线改为“主干 + 横线 + 垂线”，不再使用杂乱的多条斜线
  - 激活子节点保留高亮，非激活子节点继续保持低亮深色
  - 主节点 / 上层节点 / 子节点统一改为“自适应字号 + 更均衡折行”
  - 针对 `%`、数字尾巴、短词孤立成第二行这类典型问题，增加尾部再平衡逻辑，优先避免出现难看的“孤儿行”

### 本地验证

- 语法检查通过：
  - `venv/bin/python -m py_compile src/backend/services/report_video_render_service.py`
- 本地样张生成通过：
  - `tmp/report_video_layout_fix/left_panel_fix.jpg`
  - `tmp/report_video_layout_fix/left_panel_wrap_fix_v2.jpg`

### 结果判断

- 左侧区域已从“节点互相打架”收敛为“当前节点居中突出、下方子节点沿稳定分支展开”的结构。
- 当前节点、子节点和连线关系更容易一眼读懂，视觉层级比上一版明显清晰。
- 对长标题和数字混排节点，折行稳定性也比上一版明显更好，不再容易出现 `%` 或短尾巴单独占一行的情况。

## 2026-06-08 18:40 CST - 16:9 顶部标题区自适应修正

### 现象

- 用户继续反馈 16:9 模板顶部区域仍然存在典型排版问题。
- 当主标题较长时，主标题、副标题、来源条之间的垂直间距不够，视觉上会挤成一团。
- 这类问题不止是单个标题，而是模板需要统一兜底的“长标题 + 摘要 + 来源条”三层布局问题。

### 修复方向

- 不再使用固定 `y` 坐标去摆放顶部三层内容。
- 改成：
  - 主标题支持自适应字号与两行布局
  - 副标题根据主标题实际高度动态下移
  - 来源条根据副标题实际高度继续下推
- 同时把顶部整体高度上调，给长标题场景留出安全空间。

### 修改内容

- 文件：
  - `src/backend/services/report_video_render_service.py`

- 调整：
  - `top_h` 从 `152` 提升到 `188`
  - `content_top` 与顶部区联动，不再写死
  - 主标题改为 `_adaptive_text_layout(...)`，支持两行和动态字号
  - 副标题改为根据主标题实际行数自动定位
  - 来源条改为根据副标题实际高度自动下推

### 本地验证

- 语法检查通过：
  - `venv/bin/python -m py_compile src/backend/services/report_video_render_service.py`
- 长标题样张验证：
  - `tmp/report_video_layout_fix/header_fix.jpg`

### 结果判断

- 顶部三层信息已从“固定坐标硬摆放”改为“按实际文本高度自适应排列”。
- 长标题场景下，主标题、副标题、来源条的间距明显更稳定，不再互相顶住。
