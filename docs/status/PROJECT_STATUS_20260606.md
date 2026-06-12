# 2026-06-06 Project Status

## 研报导图视频工厂

本次把 `report-video-factory.html` 从 PDF 上传解析扩展成完整闭环：

- PDF 解析支持低质量文本检测与 Tesseract OCR 兜底，扫描版/图片型 PDF 可以生成 `raw_text.txt` 和 `cleaned_text.txt`。
- AI 文案生成会产出研报摘要、思维导图节点和段落级 `focus_node_id`，重新解析 PDF 时会清理旧 AI 结果，避免脏数据残留。
- 后端新增音频上传与 segment 匹配接口，支持 `mp3/wav/m4a/aac/flac/ogg`，文件名包含 `seg_001` 或 `001_seg_001` 时自动匹配。
- 后端新增视频生成与下载接口：
  - 16:9：导图聚焦画面。
  - 9:16：节点卡片画面。
  - 没有音频时按 segment 预估时长生成静音测试视频。
  - 有音频时按 segment 音频时长合成，并 mux 音频流。
  - 重复生成视频会输出独立文件名，不覆盖旧文件。
- 前端补齐「音频匹配」「视频生成」两个工作流页签，展示音频匹配状态、输出视频列表和下载入口。
- 页面文案更新为完整链路描述，不再显示“暂不 OCR”的旧说明。

## 自测记录

使用 1 个现有 PDF 做完整接口自测：

- 测试任务：`c80751ed-d99c-4101-889d-0f51c3be1de0`
- 测试报告：`001d878c-52cb-45f6-bd60-ab1dd4d8f337`
- PDF 解析：`parsed`
- OCR：启用，`cleaned_text_chars=138615`
- AI 文案：DeepSeek 成功生成 3 段，均带 `focus_node_id`
- 16:9 视频：生成成功，`1920x1080`，约 `159.967s`
- 9:16 视频：生成成功，`1080x1920`，约 `159.967s`
- 音频链路：3 个测试 m4a 全部匹配 `001_seg_001~003`
- 带音频视频：生成成功，ffprobe 检测到 `video` 和 `audio` 两条流
- 抽帧检查：生成视频非黑屏，中文字体、导图节点、聚焦节点、口播卡片均可见

输出示例：

- `data/tasks/c80751ed-d99c-4101-889d-0f51c3be1de0/outputs/001/16x9/001_codex-selftest-report.pdf_16x9_3dd2df31.mp4`
- `data/tasks/c80751ed-d99c-4101-889d-0f51c3be1de0/outputs/001/9x16/001_codex-selftest-report.pdf_9x16.mp4`

## 校验命令

- `venv/bin/python -m py_compile src/backend/models/report_video.py src/backend/services/report_video_service.py src/backend/services/report_video_render_service.py src/backend/apis/v1/report_video.py`
- `node --check src/frontend/js/report-video-factory.js`
- Flask test client 完整走通：创建任务、上传 PDF、OCR、AI 文案、16:9 视频、9:16 视频、音频上传匹配、带音频视频生成。

