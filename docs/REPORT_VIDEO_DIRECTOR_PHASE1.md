# 研报视频阶段一：端到端导演编排验证

> 本文保留阶段一盘点与最初原型记录。阶段二至四的最终实现、生产安全策略和连续三份真实 PDF 验收见 `REPORT_VIDEO_DIRECTOR_PHASE4_ACCEPTANCE.md`；本文后半部分的“下一步”和早期样片限制已由该最终记录取代。

## 阶段目标与安全边界

阶段一不重做 PDF 选题、Gemini 文案、标题、配音、字幕、PDF 画面、单风格分镜或视频合成。新增层只负责把已有结果编译为“混合媒介导演包”，并用低成本代表性预览验证编排。

开发/测试模式与未来生产模式分开：

- `test`：明确的 `test:` 任务、隔离输出目录、禁发布、禁改生产任务；导演包和代表预览通过后可自动运行隔离测试合成，不等待人工；
- `production`：关键帧和样片完成后仍必须人工批准，且批准绑定当前 package hash。

生产模式禁止：

- 导演包生成后自动投递全部火柴人/哆啦A梦图片；
- 自动生成整篇 TTS、完整素材或整片；
- 自动评审替代人工批准；
- 通过 `force`、直接调用 Remotion 或直接调用发布接口绕过关口；
- 在导演包修改后复用旧批准。

## 现有链路与可复用节点

### 1. PDF 选题池与新鲜度/下载量

- `src/backend/api/report_top10_workflow.py`
  - `/api/report-top10-workflows/pdf-assets` 读取已下载 PDF；
  - `scope=hot` 使用下载量阈值 `30`；
  - 按 topic 创建时间、最近同步时间、附件更新时间倒序，提供“新鲜优先”的列表。
- `src/backend/services/zsxq_sync_service.py`
  - 自动视频和基础文案旁路都有 `pdf_min_download_count`；
  - 只处理本轮已下载、文件存在且达到阈值的 PDF；
  - 可自动创建 `report_script_material_workflow`。
- `src/backend/services/report_video_asset_service.py`、`docs/REPORT_VIDEO_ASSET_SCAN.md`
  - 将同步得到的 PDF 按 SHA256 去重进入 `ReportVideoTask/ReportVideoReport`；
  - 可只解析 PDF，不进入 AI、聚类或视频。

准确边界：当前“新鲜度”主要体现为列表排序和同步批次范围，没有统一的硬性最大文龄契约。阶段二如果要全自动选题，应把最大文龄、下载量阈值和选中理由一起固化到总控输入。

### 2. Gemini 基础文案、标题与来源

- `report_script_material_workflow`
  - `PublicationTask(feature=report_script_material_workflow)` 是可复用单篇物料；
  - `tasks/report_script_material_workflow.py` 会复用同 PDF 已完成物料，避免重复跑 Gemini；
  - 产出 `structured_result.final_script_for_tts`、横竖标题、副标题、`sources`、风险提示。
- `report_audio_workflow`
  - 共用 Gemini 浏览器链路和结构化整理器；
  - 已有标题候选、标题复核、横竖标题契约。

准确边界：当前结构化结果保留 `sources`，TTS 清洗会移除 `[cite:n]` 标记，但没有稳定持久化“口播段落 → PDF 页码/bbox/原文摘录”的强类型映射。导演包将页码或来源说明视为必需证据；缺失时校验失败，不允许进入预览/完整渲染。建议下一步从 Gemini 原始返回或 PDF 文本缓存补一层确定性 evidence adapter，而不是重新解析或重写文案。

### 3. 音频、字幕和封面关键帧

- `services/report_audio_workflow.py`、`tasks/report_audio_workflow.py`
  - 豆包 TTS、分块与合并、完整 WAV；
  - PDF 首屏截图；
  - `render_mode=keyframes` 可只生成横屏/竖屏/播客/B站封面关键帧，跳过 MP4；
  - 已有视频与发布元数据投影。
- `services/report_stick_figure_video.py`
  - 支持整段音频或逐镜 TTS；
  - 生成 ASS 字幕并用 FFmpeg 合成；
  - 横屏渲染参数已有版本化契约。
- `ReportVideoAudio` 与 `process_audio_timeline_task`
  - 支持 ASR/完整音频与段落时间线对齐。

### 4. PDF 画面

- `services/report_audio_workflow.render_pdf_first_page_image`：低成本 PDF 首屏截图。
- `services/report_visual_plan_service._render_pdf_preview`：生成导演预览可复用的 PDF 预览。
- `/api/v1/documents/pdf-scroll-video`：PDF → 长图 → 匀速滚屏 MP4，已有 `pdf-scroll-video.html` 测试页。

准确边界：滚屏接口当前是独立同步工具，尚未接受“页码区间/bbox/口播时间段”的导演契约；阶段二应增加 adapter，不重写渲染实现。

### 5. 视觉规划与单风格分镜

- `report_visual_plan /preview`
  - 已有低成本 `opening_cover`、`logic_card`、`pdf_preview`；
  - 适合代表关键帧，不需要生成全量图片。
- `report_visual_plan` storyboard
  - 将完整口播逐段转成标题镜头和分镜提示词；
  - 严格检查口播覆盖并持久化 `ReportVisualPlanOutput/Segment`。
- `report_visual_scheme.py`
  - 内置 `stick_figure` 与 `doraemon`；
  - 可扩展 scheme registry。
- `report_stick_figure_video_workflow`
  - 可复用基础文案或研报音频；
  - 生成分镜、单镜图片、TTS、字幕和整片。

安全风险：当单风格工作流作为 parent 时，分镜完成会自动调用 `_dispatch_auto_scene_images`，批量投递全部镜头图片。新导演总控不能直接把此工作流当成上层导演；只能对选中的 `character_explainer` 片段使用新的“单段/代表预览 adapter”，并把完整下发放在人审之后。

### 6. 关键帧、代表样片与双模式放行

- `ReportVideoRenderPreview`
  - 支持 `keyframes` 与 `preview_clip`；
  - 保存比例、文件、片段、renderer version、批准/退回时间与原因。
- `generate_render_preview_task`
  - `render_task_keyframes` 生成代表关键帧；
  - `render_task_preview_clip` 支持选定 milestones，可生成若干独立短片。
- `report-video-factory.html`
  - 有生成关键帧、确认/退回关键帧、生成样片组、确认/退回样片组的 UI。
- `generate_videos_task`
  - 默认要求每个比例的关键帧和样片都已批准，适合作为未来生产模式的基础。

安全风险：

- 旧整片 API 接受 `force`，可绕过 preview gate；
- Remotion 试验版接口没有复用同一 preview gate；
- 旧 gate 面向 `ReportVideoTask`，未绑定导演包 hash，导演包修改后无法自动让旧批准失效。

新总控不得复用这些绕过路径；阶段一原型使用内容 hash 绑定批准。

### 7. 合成能力

- `report_video_render_service.py`：PIL/FFmpeg 横竖屏、关键帧、代表短片和完整视频。
- `report_video_remotion_service.py` + `src/remotion_renderer`：独立 Remotion 试验线，已有 16:9/9:16、音频、时间线和 EvidenceStack。
- `report_stick_figure_video.py`：单风格静帧 + 音频 + 字幕合成。
- HyperFrames：
  - HTML 是 source of truth；
  - 原生支持 composition、音频、字幕、转场和横竖尺寸；
  - 适合把导演包映射到定时 clip/track，作为后续整片或有界样片 adapter；
  - 接入前必须先建立项目 `DESIGN.md`，并执行 lint/validate/inspect；阶段一不初始化、不渲染。

## 第一版总控输入契约

总控只接受已经存在的上游结果：

```json
{
  "schema_version": "report_video_master_input_v1",
  "material": {
    "job_id": "PublicationTask.id",
    "feature": "report_script_material_workflow",
    "title": "内容标题",
    "pdf": {
      "attachment_id": "ZSXQ attachment id",
      "filename": "source.pdf",
      "path": "existing local path"
    },
    "structured_result": {
      "final_script_for_tts": "权威口播正文",
      "landscape_title": "横屏标题",
      "vertical_title": "竖屏标题",
      "sources": []
    },
    "audio_task_id": "可选，已有音频任务",
    "audio_path": "可选，已有完整音频",
    "subtitle_path": "可选，已有字幕"
  },
  "evidence_map": [
    {
      "evidence_id": "ev-001",
      "kind": "pdf_quote|pdf_chart|pdf_table",
      "page": 6,
      "bbox": [0.1, 0.2, 0.9, 0.6],
      "quote": "PDF 原文或图表说明",
      "script_excerpt": "对应的权威口播连续片段",
      "asset_id": "PDF asset id",
      "confidence": 1.0
    }
  ],
  "options": {
    "source_task_id": "PublicationTask.id",
    "revision": 1,
    "target_segment_chars": 130
  }
}
```

规则：

- 导演层不得改写 `final_script_for_tts`；
- evidence adapter 必须提供页码或明确来源说明；
- 音频/字幕存在时只复用；不存在不自动生成；
- 选题阈值、文龄和选中理由在生产总控中应作为 source snapshot 固化。

## 第一版导演包输出契约

`services/report_video_director.py` 生成：

- `source`：物料任务、PDF、口播 hash、复用声明；
- `titles`：横竖标题差异；
- `chapters`：章节、段落引用、章节时长和转场；
- `segments`：
  - 口播段落和估算时长；
  - PDF 证据引用；
  - 素材类型；
  - 风格、镜头、运动、覆盖层、转场；
  - 下发模块、动作和输入；
  - 16:9 与 9:16 的构图、安全区、证据布局和字幕行数；
  - `full_asset_generation_allowed=false`；
- `preview_gate`：
  - 导演包校验；
  - 代表关键帧计划；
  - 2–3 段代表性短片计划；
  - 生产模式人工决定，测试模式显式隔离授权；
  - 默认阻止完整渲染；
- `package_hash`：批准绑定的不可变内容指纹。

素材类型：

| 素材类型 | 下发目标 |
| --- | --- |
| `title_card`、`logic_card`、`chart` | 现有 `report_visual_plan`/卡片渲染 |
| `original_pdf`、`pdf_highlight` | PDF 页面/局部高亮 adapter |
| `pdf_scroll` | 现有 PDF 滚屏 adapter |
| `character_explainer` | 火柴人或哆啦A梦的“单段” adapter |

导演层决定媒介；风格模块只处理收到的片段，不能反向把整片变成单一风格。

## 低成本预览关口

共同的前三步严格按顺序执行：

1. `director_package_valid`
   - schema、章节/段落、证据、素材、转场、横竖差异、下发模块全部通过；
2. `representative_keyframes_completed`
   - 从开头/中段/结尾抽取最多 3 个代表段落；
   - 至少覆盖两类媒介，包含一个竖屏差异样本；
3. `representative_preview_clips_completed`
   - 生成 2–3 段、每段 4–10 秒的有界样片；
   - 优先覆盖标题、PDF 证据、角色/逻辑解释和一个竖屏差异；
4. 模式放行
   - `production`：`human_approved | modify | terminated`；自动评审只写建议，`approved` 必须记录 reviewer、时间和当前 `package_hash`；
   - `test`：不等待人工，但必须显式提供 `test:` 任务 ID、隔离输出目录、`publish_allowed=false` 和 `production_task_mutation_allowed=false`；
   - `modify` 回到导演包并增加 revision，`terminated` 终止；
5. 只有 `evaluate_full_render_gate(...).allowed=true` 才能创建完整素材/整片子任务。

任意导演包修改都会改变 hash，使旧的生产批准失效。阶段二的总控和所有完整渲染入口都必须在服务端重新调用同一 gate，不能相信前端按钮状态。测试自动放行只授权当前隔离运行，不能复用到生产任务。

## 阶段一最小原型

纯函数：

- `build_director_package(material, evidence_map)`；
- `validate_director_package(package)`；
- `human_approval(package, ...)`；
- `evaluate_full_render_gate(package, preview_artifacts, human_review)`。

CLI：

```bash
PYTHONPATH=src/backend python scripts/report-video-director-phase1.py \
  build --input /path/to/input.json --output /tmp/director-package.json

PYTHONPATH=src/backend python scripts/report-video-director-phase1.py \
  validate --input /tmp/director-package.json

PYTHONPATH=src/backend python scripts/report-video-director-phase1.py \
  run-test --input /path/to/input.json --pdf /path/to/test.pdf \
  --output-root /tmp/report-director-e2e --test-task-id test:director-e2e
```

`build/validate` 只读写 JSON。`run-test` 复用现有 `report_visual_plan` 的本地预览渲染和 `report_video_render_service.make_concat_video`，真实生成关键帧、2-3 段 MP4 样片和一条隔离测试合成片；不访问数据库、Celery、Gemini、图片生成或发布接口。

## 受控回归验证

2026-07-29 曾使用代码生成的 4 页受控测试 PDF 和模拟的既有 Gemini 文案/证据输入执行 `run-test`。这组数据只用于契约与安全回归，不作为成片效果验收：

- 导演包：校验通过，3 个段落，媒介类型覆盖 `title_card`、`chart`、`character_explainer`；
- 关键帧：3 张；
- 代表样片：3 段真实 MP4，时长分别为 5.033、5.033、4.033 秒，其中一段为 9:16；
- 测试关口：`test_auto_authorized`，没有人工等待；
- 隔离合成片：13.667 秒、547,380 bytes，复用 `report_video_render_service.make_concat_video`；
- 安全断言：`publish_attempted=false`、`production_task_mutated=false`；
- 视觉抽帧：三段均进入合成片，9:16 段以安全留边适配 16:9。

这次验证真实覆盖了导演契约、现有预览渲染、横竖适配、FFmpeg 样片与合成、媒体时长探测和测试 gate；Gemini、TTS、图片生成和发布未在本轮重复调用。现有本地卡片渲染器在英文长句上会截断，属于低成本预览的视觉质量限制，不影响流程闭环；接入中文真实研报或 HyperFrames adapter 时需重新做排版验收。

为防止样片来源含糊，runner 会在清单写入 `source_type`、原始物料任务、PDF 文件名/标题/路径/SHA256、口播 SHA256 和证据页，并在关键帧及样片底部固定显示 `SOURCE PDF · 文件名 · 标题`。

## 当前选题池真实 PDF 验收

效果验收改用当前选题池的真实内容，不再以合成内容证明成片质量。自动选择结果：

- PDF：`高盛-中国光伏：追踪盈利拐点——2026年7月：玻璃价格上涨，其余板块持续疲软-260727.pdf`；
- 选题池附件：`29f40ff9-7c6a-4ba9-a5ed-674dfd83fbed`，下载量 46，选题发布时间 `2026-07-28T10:35:06.360+08:00`；
- 已完成真实物料任务：`188b168c-df0e-4c97-88a9-ded5d8754c9e`，feature 为 `report_pdf_audio_workflow`，复用 1,841 字真实口播和既有标题；
- PDF SHA256：`e83ee70320e941055360ccb7d37da4a316e67b9902155e49697858118fa4ceec`；
- PDF 共 11 页、可提取文本约 39,243 字；逐段证据映射覆盖第 1、3、4 页。

最终 v5 隔离验收结果：

- 导演包 12 段，媒介覆盖标题卡、图表、逻辑卡、PDF 高亮/滚屏和角色解释；
- 3 张关键帧、3 段 5 秒真实 MP4，覆盖 16:9 标题、16:9 原 PDF 图表证据、9:16 风险解释；
- 复用物料任务已有的 325.776 秒真实 WAV，不调用 TTS；三个样片按段落在全文中的字符位置截取 0.000、77.176、312.617 秒开始的代表窗口；
- 每个样片和最终试映均经 `ffprobe` 确认为 H.264 视频 + AAC 音频；
- 合成试映 14.667 秒、1,080,610 bytes，状态 `test_auto_authorized`；
- `publish_attempted=false`、`production_task_mutated=false`。

人工视觉复核经历多轮修订：移除旧标题卡裁切；让代表抽样强制覆盖 PDF 证据；将第 3 页利润率/库存图表放大并与页码、中文解释和英文原文并排；将误用的通用逻辑卡替换为与末段口播一致的分析员风险卡；在横屏试映中明确标记 9:16 独立构图；最后复用真实配音验证代表段节奏。音频窗口是按全文字符比例对 325.776 秒 WAV 的确定性近似，因为现有任务没有逐句时间线；正式成片仍应复用 ASR/字幕时间线做精确对齐。当前结果达到阶段一低成本导演试映的可交付标准，但仍不是带完整字幕和全量动态素材的正式成片。

## 推荐落地顺序

1. 建 evidence adapter：从 Gemini 原始结果/现有 PDF 文本缓存生成稳定 page/bbox/quote 映射。
2. 用 `PublicationTask(feature=report_video_master_control)` 持久化 director package、revision、hash 和人工审核审计。
3. 给现有 `ReportVideoRenderPreview` 增加 director package id/hash 绑定，复用现有关键帧/样片 UI。
4. 为 PDF 高亮、PDF 滚屏、visual_plan 卡片和单段角色解释分别做 adapter；每个 adapter 先只允许 keyframe/preview mode。
5. 所有完整素材、FFmpeg、Remotion、HyperFrames 和发布入口统一调用服务端 gate；移除新链路中的 `force` 语义。
6. 生产模式人工批准后才扇出完整素材；开发/测试模式可在隔离授权后自动合成。整片仍优先复用现有渲染器，HyperFrames 可并行做 adapter PoC，但不是阶段一前置条件。

## 已知风险与需决定事项

- 证据映射缺少稳定 page/bbox 契约，是当前闭环的首要数据缺口。
- 现有单风格工作流自动投递全量图片，不能直接挂在导演包后面。
- 旧 `force` 和未设 gate 的 Remotion 入口会造成旁路，生产集成前必须封口。
- `PublicationTask` 与 `ReportVideoTask` 是两套任务域；建议总控使用 `PublicationTask`，预览资产暂时复用 `ReportVideoRenderPreview`，通过 adapter/外键或稳定引用关联，避免再建第三套通用任务模型。
- 横竖屏是同一导演语义的两种构图，不应复制两份独立口播和证据。
- 需要用户决定：阶段二首条真实样片采用现有 FFmpeg 预览器，还是同时做 HyperFrames 有界样片 adapter；建议先用现有渲染器验证 gate，再评估 HyperFrames 的编排收益。
