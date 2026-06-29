# 2026-06-26 豆包朗读 processing 子任务卡死导致父编排无限等待

## 基本信息

- 状态：已修复
- 严重级别：P1
- 影响模块：report audio workflow / doubao browser tts / celery worker
- 首次发现：研报音频编排中豆包子任务长时间停在 `download_wav 88%`
- 修复提交：当前工作区

## 现象

研报音频编排的豆包朗读子任务停在 `processing / download_wav / 88%`，父编排持续显示等待同一子任务。实际 worker 已经可以继续处理其它任务，但数据库中的旧子任务仍是非终态，导致父任务不断等待，不会主动重新投递该分段。

## 影响

- 父编排无法进入音频合并和视频合成。
- 同一 PDF/同一分段可能被人工重复补投，增加浏览器/Gemini/豆包链路成本。
- 新增多 profile worker 后，旧 processing 记录仍会阻塞父任务，不会自动交给新 worker 接管。

## 根因

豆包浏览器链路属于外部网页自动化，可能在导出 WAV、捕获音频或页面状态异常时停止更新。原父编排只根据子任务 `status not in terminal` 判断“仍在执行”，缺少针对豆包子任务的 stale 判断，因此无法区分“真实运行中”和“数据库孤儿 processing”。

## 解决思路

把豆包朗读子任务纳入编排层自愈：

- 对非终态豆包子任务检查 `updated_at`。
- 超过 `REPORT_AUDIO_DOUBAO_STALE_SECONDS` 未更新时，标记为 failed。
- 复用现有分段重试机制重新投递该 chunk。
- 新任务继续按 `chunk_index` 顺序合并，避免并行完成顺序影响最终音频。

## 实际方案

- 在 `report_audio_workflow` 中新增豆包 stale 判断。
- `_active_tts_task_for_chunk` 查询到 stale processing 子任务时自动释放，不再把它当活跃任务。
- `_wait_child` 等待豆包子任务时也会触发 stale 释放。
- 默认 stale 阈值为 `DOUBAO_BROWSER_TTS_TOTAL_TIMEOUT + 180` 秒，最低 60 秒，可通过 `REPORT_AUDIO_DOUBAO_STALE_SECONDS` 调整。
- 对当前卡住任务 `e046904c-0068-4567-bef3-eba47167786d` 执行了一次同规则释放，父编排随后自动创建第 6 段重试任务。

## 验证

- 单测新增 stale processing 子任务释放用例。
- 第 6 段重试任务 `50909074-5038-41ee-8e63-6e5be83e6d8e` 由 `worker_2` 完成。
- 第 7 段任务 `76163cfd-9323-47fc-868e-30f85b888eea` 由 `worker_1` 完成。
- 父编排已越过豆包阶段，进入 `publisher_submit 95%`。

## 复发预防

- 不再人工重复补投同一分段；先看子任务是否 stale。
- 多 profile worker 必须记录 `worker_id/user_data_dir/profile_directory`，方便定位是哪一路浏览器出问题。
- 音频合并只信任 `chunk_index` 顺序，不信任完成先后。

## 后续事项

- 重启 `report_audio_workflow` worker 后，新任务会使用 2 路豆包投递窗口。
- 重启 watchdog/backend 后，monitor 的豆包 worker 启动脚本会切到 profile 池脚本，避免旧单 worker 被再次拉起。
