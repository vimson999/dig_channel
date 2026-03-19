# 视频生产流水线专项优化计划 (2026-03-18)

## 🏗️ 阶段一：性能深度优化 (Speed & Efficiency)

### 1.1 ASR 模型热切换 (Model Hot-Switching) [DONE]
- **现状**：目前强制使用 `large-v3-mlx-8bit`，虽然精准，但对 10 分钟以上的长视频压力较大。
- **已完成**：
    - ✅ **多级模型支持**：支持 `tiny`, `base`, `small`, `medium`, `large-v3` 动态切换。
    - ✅ **前端联动**：`publisher.html` 已上线模型选择器。

### 1.2 FFmpeg Pipe 流式合成 (Rendering Acceleration) [DONE]
- **现状**：MoviePy 逐帧处理导致 CPU/GPU 频繁交换数据。
- **已完成**：
    - ✅ **FFmpeg Direct 引擎**：通过 `_synthesize_ffmpeg_direct` 实现原生 FFmpeg 滤镜链渲染。
    - ✅ **硬件加速**：强制启用 `h264_videotoolbox`。
    - ✅ **倍速渲染**：35s 视频合成仅需约 10s。

### 1.3 并发任务与资源隔离 (Concurrency Control) [PARTIAL]
- **现状**：GPU 显存限制。
- **已完成**：
    - ✅ **线程池模式**：Celery 已切换为 `-P threads`，解决多进程 Metal 冲突。

---

## 🎨 阶段二：审美与视觉表现 (Visual & Aesthetics)

### 2.1 动态字幕特效 (Dynamic Subtitles)
- **目标**：从“能看清”进化到“好看”。
- **方案**：
    - 实现“逐词高亮”效果（Karaoke style），增强观众的注意力。
    - 增加字幕背景阴影或毛玻璃效果，适配复杂背景。

### 2.2 自动 B-roll 匹配 (Automated B-roll)
- **方案**：利用 ASR 提取的关键核心词（如“特斯拉”、“AI”、“财报”），自动从 `assets/vids` 素材库中检索对应标签的片段并作为背景插入。

---

## 🤖 阶段三：自动化与 AI 增强 (Workflow & Intelligence)

### 3.1 趋势雷达一键转视频 (Trending to Video)
- **闭环**：`Trending Radar` 抓取热点 -> `Gemini` 生成脚本 -> `ASR` 语音合成 -> `Publisher` 视频合成 -> `Upload` 发布。

---

## 🛠️ 阶段四：健壮性 (Reliability)

### 4.1 凭证状态自动预警
- **方案**：在每次启动任务前自动测试 B 站 `/nav` 接口。如果返回 403，前端立刻弹出“Cookie 已失效，请扫码更新”的提示。
