# HANDOVER_LOG

## 2026-11-20 — `ima-agent-team` 管线 9 项统一优化（三版三行封皮审美 / 执行日期 / 运行环境分流 / 软链 / 淡入验收）

### 今日完成内容

1. **后端管线增强（`dig_ytb_channel`）**
   - [src/backend/apis/v1/batch_transcription.py](file:///Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel/src/backend/apis/v1/batch_transcription.py)：`POST /api/v1/batch-transcription/start` 新增可选 `file_paths` 参数（`_build_explicit_audio_files`），支持按显式文件列表精确转写，彻底避免目录递归误扫，同时兼容旧版 `input_dir` 扫描。
   - [src/backend/services/mp3_video_workflow.py](file:///Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel/src/backend/services/mp3_video_workflow.py)：
     - **执行日期兜底**：新增 `SHANGHAI_TZ` 与 `_execution_date_text()`；`plan_from_direct_ds_response(..., local_only=True)`、`_clean_closed_door_video_title`、`_direct_ds_closed_door_parts`、`generate_wsj_visual` 全面将缺失日期或遗留 `"日期待确认"` 自动替换为 `Asia/Shanghai` 执行日期（标题 `YYYY-MM-DD` / 封皮 `YYYY.MM.DD`），封皮与标题不再出现 `"日期待确认"`。
     - **1–11 项解析与三版严格三行封皮**：`_direct_ds_numbered_sections` 升级支持第 `1–11` 项；新增 `_three_line_cover_copy()` 与 `_ensure_three_cover_lines()`，第 9 项直供 WSJ 封皮（`wsj_title_lines`）、第 10 项直供小君君横屏封皮（`xiaojunjun_subtitle`）、第 11 项直供此刻此声横屏封皮（`finance_cover_subtitle`），即使兼容旧版 7/9 项输入也保证三路封皮均输出三行。
     - **修复竖屏信息卡时间戳泄漏**：增强 `_is_cover_process_text` 与 `_clean_infographic_copy`，清洗金句尾部 `（时间戳无法确定）` 标注后再过滤过程文本，确保第 5 项金句干净进入竖屏白卡而不再误回退打印第 4 项 `起点：00:00:57.000`。
   - [tests/unit/test_ima_agent_video_audio.py](file:///Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel/tests/unit/test_ima_agent_video_audio.py)：补充执行日期替换、11 项三版三行封皮、信息卡纯净金句提取与 `batch_transcription` 显式 `file_paths` 单测。

2. **智能体团队技能与脚手架升级（`skills-source/ima-agent-team`）**
   - [prompts/analysis.md](file:///Users/v9/Documents/3---work/06---dev---python/skills-source/ima-agent-team/prompts/analysis.md)：升级为 **1–11 项**固定提示词，强制要求尊重并正面回应原始 MP3 文件名主线、无明确会议日期一律写执行日期（禁止 `"日期待确认"`）、三版标题审美差异化，并独立输出第 9（WSJ）/ 10（小君君横屏）/ 11（此刻此声横屏）项严格三行封皮文案。
   - [scripts/analysis.py](file:///Users/v9/Documents/3---work/06---dev---python/skills-source/ima-agent-team/scripts/analysis.py)：校验第 1–11 项、有效日期（拒绝 `"日期待确认"`）、第 9/10/11 项严格三行且不雷同/无序号引号；新增 O(N) 轻量 `detect_large_hallucinations()`（容忍小段 Whisper 复读，仅对连续 >= 15 段且跨度 >= 60s 的大段幻觉输出 `large_hallucination_spans` 提示，不阻塞）。
   - [scripts/video.py](file:///Users/v9/Documents/3---work/06---dev---python/skills-source/ima-agent-team/scripts/video.py)：同步校验第 1–11 项与有效日期；新增可选 `--transcript <TXT>` 与 `--snap-window 2.0`，在 ±2 秒内自动将 `+10s / +20s` 吸附至最近转写段落边界，并同时记录 `target_offset_seconds`、`source_offset_seconds` 与 `snapped_to_transcript`。
   - [scripts/team.py](file:///Users/v9/Documents/3---work/06---dev---python/skills-source/ima-agent-team/scripts/team.py)：
     - `init` 新增 `--runtime {auto,codex,antigravity}` 与 `execution_date` 记录；`antigravity` 运行时自动将每文件 `cover` 标记为 `skipped`（跳过小弟 5），`codex` 运行时保留小弟 5。
     - `ledger.md` 顶部新增 `## 总览` Markdown 汇总表（音频 / 转写 / 分析 / 三路视频 / 淡入验收 / 备用封面）及运行环境、执行日期展示。
     - `transcribe-start` 自动向后端提交显式 `file_paths`（忽略当日目录子文件夹中其他音频）。
     - `video-status` 在三路成片完成后自动在 `~/Downloads/MMDD/videos/` 创建指向成品 MP4 的软链接并记录 `symlink_path`。
     - 新增 `video-verify` 子命令，固化执行 `ffprobe` 音视频流检查 + `ffmpeg volumedetect`（`0–0.4s` vs `2.5–3.5s` 2 秒淡入增量验收）+ 0 社交平台上传审计并落盘台账。
   - 同步更新 [SKILL.md](file:///Users/v9/Documents/3---work/06---dev---python/skills-source/ima-agent-team/SKILL.md)、全部角色文档（`agents/coordinator.md`、`worker-2/3/4/5`）及全部 3 份单测文件（`tests/test_analysis.py`、`test_video.py`、`test_team.py`）。

3. **自动化验证与安全检查结果**
   - 后端回归单测：145 passed, 5 subtests passed (`pytest tests/unit/test_ima_agent_video_audio.py tests/unit/test_publisher_audio_preprocess.py tests/unit/test_mp3_video_workflow_service.py tests/unit/test_mp3_video_workflow_task.py tests/unit/test_mp3_wsj_output.py tests/unit/test_mp3_video_upload_api.py -q`)。
   - 技能套件单测与结构校验：37/37 `unittest` 全通过，`quick_validate.py` 输出 `Skill is valid!`。
   - 只读端到端冒烟验证通过：`plan_from_direct_ds_response(..., local_only=True)` 输出三版三行封皮、`date_text=2026.11.20`、纯净信息卡金句（零时间戳/零 `"日期待确认"`）；`analysis.py` + `video.py --transcript`（验证 `+10s -> +11s`、`+20s -> +19s` 边界吸附）+ `team.py init --runtime antigravity`（验证 `cover: skipped` 与顶部总览表）全链路通过。
   - **安全重启与线上状态确认**：原阻塞任务 `zsxq_clean_output_publish`（`9c4d002c-3db7-4289-b53b-eca4efed4c40`）已自然结束，`check-restart-safety.py` 返回 `OK`；随后执行 `bash scripts/dev/restart-dev-stack.sh` 完成全栈安全重启（`PID: 30399`，`8/8 新进程监听验收通过`），线上 `GET /api/mp3-video-workflows/status` 已返回 `ima_agent_audio_v1: true` 及三路倍速配置。

### 下一步安排

- 本地 Flask 与 Celery 栈已加载全部 9 项优化（`file_paths`、11 项三行封皮、执行日期兜底与信息卡修复），可直接运行下一批真实 MP3 验收。
- 视需要决定是否将工作区改动提交到 Git 分支。

---

## 2026-11-20 — MP3 工作流详情页 WSJ 视频展示与封皮过期策略对齐

### 今日完成内容

1. **问题定位与策略对齐（`dig_ytb_channel`）**
   - 定位任务 `e6bf48da-83e9-4ab1-a840-e25242c8e052` 在重做 `wsj_cover` 后页面隐藏已有 WSJ 4:3 视频的原因：`src/backend/api/mp3_video_workflow.py` 中 `_video_by_key_from_result(..., "wsj")` 要求 `wsj_video.cover_path == artifacts.wsj_cover.path`，导致封皮更新后接口返回 `wsj_video: null` 与 `download_wsj_video_url: null`，而小君君横屏与此刻此声竖屏不做该拦截。
   - 修改 [src/backend/api/mp3_video_workflow.py](file:///Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel/src/backend/api/mp3_video_workflow.py)：
     - `_video_by_key_from_result` 对 `wsj` 直接返回 `result.get("wsj_video")`，与 `landscape` / `vertical` 行为保持一致，已生成好的 WSJ 视频在页面「视频文件」、下载与投递入口中继续可见可用。
     - 新增 `_is_wsj_video_outdated(result)` 辅助函数并在 `_job_payload` 中使用，封皮更新后仍准确透出 `wsj_video_outdated: true`，保留页面上的「封皮已更新」提示；后台「重新生成完整视频」仍按 `cover_path` 识别旧图并重合成。
   - 更新 [tests/unit/test_mp3_wsj_output.py](file:///Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel/tests/unit/test_mp3_wsj_output.py)：将 `test_replacing_wsj_cover_invalidates_only_wsj_download` 调整为 `test_replacing_wsj_cover_marks_outdated_without_hiding_wsj_video`，同时断言 WSJ 视频保留返回且 `wsj_video_outdated` 标记为 `True`。

2. **验证与服务重载**
   - 单元测试：`PYTHONPATH=src/backend ./venv/bin/pytest tests/unit/test_mp3_wsj_output.py tests/unit/test_mp3_video_workflow_task.py tests/unit/test_mp3_video_workflow_service.py tests/unit/test_mp3_video_upload_api.py -v`（128 项全部通过）。
   - 接口验证：核对任务 `e6bf48da-83e9-4ab1-a840-e25242c8e052` 的 `_job_payload`，确认 `download_wsj_video_url` 已恢复且 `wsj_video_outdated` 为 `True`。
   - 重载本地服务栈并验证 `http://127.0.0.1:5001`。

### 下一步安排

- 用户刷新任务详情页确认 WSJ 4:3 视频卡片恢复显示；如需新版封皮成片，再触发「重新生成完整视频」。
- 视需要决定是否提交工作区改动到 Git 分支。

---

## 2026-11-19 — MP3 智能体五人执行团队管线（`ima-agent-team`）收尾与上线验收

### 今日完成内容

1. **后端能力补齐与加固（`dig_ytb_channel`）**
   - [src/backend/api/mp3_video_workflow.py](file:///Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel/src/backend/api/mp3_video_workflow.py)：
     - 校验 `agent_audio_options`（仅支持 `input_source=ds_response`、`planner_mode=local`、明确三路偏移 `0–120s` 与淡入 `0–10s`）。
     - 在任务详情 `_job_payload()["request"]` 中透出 `agent_audio_options`，便于智能体复核与断点恢复。
   - [src/backend/services/mp3_video_workflow.py](file:///Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel/src/backend/services/mp3_video_workflow.py)：
     - `status_payload()` 暴露 `ima_agent_audio_v1: true` 与 `video_speeds`（`xiaojunjun_landscape=1.18`、`ciketc_vertical=1.2`、`wsj_4_3=1.22`）。
     - `plan_from_direct_ds_response(..., local_only=True)` 严格本地解析第 1–9 项，禁止回退调用 DeepSeek；依次从第 7/6/8 项提取日期（无法确认时保留 `日期待确认`），并强制校验第 8 项 WSJ 标题与第 9 项三行文案。
     - `process_job` 将 `agent_audio_options` 持久化写回 `result["plan"]`，确保重试/查询不丢失配置。
     - `audio_segment_from_plan` 对三路分别应用原始区间后移 `0s / +10s / +20s`（超尾截断、空区间报错）并传递 `fade_in_seconds=2`，同时计入 `_audio_segment_signature`。
     - `create_publisher_video_task` 与 `_publisher_result_summary` 记录并透出 `audio_segment` 审计字段。
   - [src/backend/tasks/publisher.py](file:///Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel/src/backend/tasks/publisher.py)：
     - 音频预处理按「截取（含偏移）→ `atempo` 倍速 → `afade=t=in:st=0:d=2` 成片前 2 秒淡入」顺序执行，缓存文件名携带 `_fi<fade>` 后缀防止串用。
   - [tests/unit/test_ima_agent_video_audio.py](file:///Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel/tests/unit/test_ima_agent_video_audio.py)：补充 9 项单元测试覆盖 API 参数校验、本地解析、日期回退、三路偏移/淡入、ffmpeg 滤镜链与结果审计字段。

2. **智能体团队技能完善（`skills-source/ima-agent-team`）**
   - 统一 [scripts/analysis.py](file:///Users/v9/Documents/3---work/06---dev---python/skills-source/ima-agent-team/scripts/analysis.py) 与 [scripts/video.py](file:///Users/v9/Documents/3---work/06---dev---python/skills-source/ima-agent-team/scripts/video.py) 的第 1–9 项标题解析规则；`analysis.py` 兼容带省略号原话引用；`video.py` 新增 `--audio` 自动通过 `ffprobe` 读取音频真实时长。
   - 完善 [scripts/team.py](file:///Users/v9/Documents/3---work/06---dev---python/skills-source/ima-agent-team/scripts/team.py)：
     - `search` 同时精确匹配原始文件名与清洗后落盘文件名（如 `261116-大摩闭门会-宏观策略谈.mp3`），并将超时提升至 60s 兼容冷缓存重建。
     - `api` 支持 `--data-file` 提交大段 JSON 请求体。
     - 新增 `video-submit`、`video-status`、`video-adopt` 三个确定性视频编排命令（POST 前落盘意图、防重复投递、拒绝 keyframes-only 假成功、落盘校验 + `ffprobe` 实际时长采集）。
     - `ledger.md` 同步展示分析路径、视频任务状态/三路成片路径与备用封面路径。
   - 同步更新 [SKILL.md](file:///Users/v9/Documents/3---work/06---dev---python/skills-source/ima-agent-team/SKILL.md) 与各角色文档（统一备用封面目录 `covers/<音频名>/`），并补充 Antigravity 技能软链接 `~/.gemini/config/skills/ima-agent-team`（与 Codex 软链接 `~/.codex/skills/ima-agent-team` 保持一致）。

3. **安全重启与验收结果**
   - 后端单测：143 项全部通过（含新增 9 项）。
   - 智能体团队单测：28 项全部通过，`quick_validate.py` 校验通过。
   - `ffmpeg` 实测验证：`atempo=1.22,afade=t=in:st=0:d=2` 输出时长精确匹配，开头 2 秒音量平滑淡入。
   - 通过 `check-restart-safety.py` 与 `restart-dev-stack.sh` 安全重载本机 Flask 与 Celery 栈；线上 `GET /api/mp3-video-workflows/status` 已返回 `ima_agent_audio_v1: true` 与 `video_speeds`，只读端到端冒烟验证（`init` → `search` → `prepare --dry-run` → `/status` → `analysis.py` → `video.py --audio`）全部通过。

### 真实单文件端到端测试（`通信行业近期深度报告合集_261117.mp3`）

- **批次编号**：`20261119-153743-2dccb1e3`（目录 `/Users/v9/Downloads/1119/`，台账 `ledger.json` / `ledger.md` / `events.jsonl` 均已闭环完成）
- **小弟 1（素材准备）**：精确命中已清洗资产 `pipeline:ae2d3663-959d-4cb9-a8ed-d2b8b6a3809d`，SHA-256 核验一致后复制到 `/Users/v9/Downloads/1119/261117-通信行业近期深度报告合集.mp3`（40.4 MB，实测时长 `2526.85s`）。
- **小弟 2（批量转写）**：MLX Whisper `medium` 耗时 `8m05s` 生成 4,772 行带时间戳 TXT（`task_id: 53b5eab2-2a60-4c5f-b03d-ade705f0d3fc`）。
- **小弟 3（文案分析）**：完整通读 4,772 行输出第 1–9 项分析（黄金片段 `00:00:57.000–00:41:37.000`，连续 40 分 40 秒），通过 `analysis.py`、`video.py` 与后端 `local_only=True` 三重校验；同步发现并修复了 `scripts/video.py` 和 `scripts/analysis.py` 中 `^\s*` 跨行吃空行的正则边界问题（已补回归单测，29/29 通过）。
- **小弟 5（备用封面，与小弟 3 并行）**：基于 `wsj-video-cover-from-folder` + 固定风格参考图 + 内置 `image-generator` 生成 `1920×1080` PNG（主标题 `内资闭门会`），视觉与零二维码验收通过，已落盘至批次 `covers/` 及 `~/Downloads/1119/`。
- **小弟 4（三路完整视频）**：父任务 `job_id: e6bf48da-83e9-4ab1-a840-e25242c8e052` 全自动合成三路成片（小君君 16:9 `2067.9s` / `+0s` / `1.18x`、此刻此声 9:16 `2033.6s` / `+10s` / `1.20x`、WSJ 4:3 `2000.3s` / `+20s` / `1.22x`），`ffmpeg volumedetect` 实测确认三路前 2 秒淡入全部生效，零社交平台上传调用。

### 真实全量批次运行（第 2 批：截图中剩余 4 个 MP3）

- **批次编号**：`20261119-162828-9caf7e71`（目录 `/Users/v9/Downloads/1119/`，台账 `ledger.json` / `ledger.md` / `events.jsonl` 已记录完整流水线过程）
- **素材准备与转写（4/4 完成）**：
  - 小弟 1 唯一精确命中全部 4 份已清洗 MP3 并校验 SHA-256 落盘至 `/Users/v9/Downloads/1119/`，同时将第 1 批旧 MP3 安全移入废纸篓（保留历史台账、日志与第 1 批备用封面）。
  - 小弟 2 单次批量转写（`task_id: b1f56bd3-79bb-4d0e-ab9a-322f3bb1269d`，MLX Whisper `medium`）全部 `4/4` 成功完成。
- **全文分析与三路完整视频（4/4 完成，共 12/12 个成片全部实测通过）**：
  - 小弟 3 逐份通读全文并完成第 1–9 项分析、三重格式/边界/后端校验（均保持 `日期待确认` 不臆测会议日期）：
    1. `2027年宏观与大类资产展望_宏观瞭望东北系列电话会17_261117.mp3`：黄金区间 `98s–1645s`，父任务 `6dedd50e-c37f-4e18-af74-bbd30d1823ca`（3 路成片 `1311.4s / 1289.5s / 1268.5s`）。
    2. `WorkBuddy行业比较与轮动研判_261117.mp3`：黄金区间 `79s–2053s`，父任务 `4b8f519a-0aad-400d-ba7b-ca1e7f89c07b`（3 路成片 `1673.0s / 1645.2s / 1618.3s`）。
    3. `市场反复未来或仍需更多政策支持261117.mp3`：黄金区间 `146s–1543s`，父任务 `21453d40-5f60-4a7f-8104-0dcddadc2496`（3 路成片 `1184.4s / 1164.6s / 1145.3s`）。
    4. `新消费主义研究_261117.mp3`：黄金区间 `1002s–2742s`，父任务 `3eba2570-a6ca-4262-8de3-a8aa09a21b8e`（3 路成片 `1475.2s / 1450.6s / 1426.8s`）。
  - 全部 12 个视频均已通过 `ffprobe` 时长/参数核验及 `ffmpeg volumedetect` 前 2 秒音频淡入实测，且零社交平台上传调用。
- **独立备用封面（3/4 完成，1/4 因生图模型配额耗尽暂未合格交付）**：
  - `2027年宏观与大类资产展望`、`市场反复未来或仍需更多政策支持`、`新消费主义研究` 均已生成 1920×1080 PNG 并通过逐字视觉验收与 `cv2.QRCodeDetector` 零二维码核验。
  - `WorkBuddy行业比较与轮动研判` 初稿因右侧混入参考图投行卡片未予采纳，干净重绘时触发 `gemini-3.1-flash-image` `429 RESOURCE_EXHAUSTED`（预计 `2026-11-20T07:31:10Z` / 北京时间 `15:31:10` 重置），台账已如实标记 `cover: failed`（不阻塞视频）。

### 下一步安排

- 待图片生成配额重置（北京时间 `15:31` 之后）如仍需要 `WorkBuddy行业比较与轮动研判` 的 1080P 备用封面，可单独补生成一张；4 份音频的 12 路视频已全部可用。
- 视需要决定是否将 `dig_ytb_channel` 中的后端改动提交到 Git 分支。
