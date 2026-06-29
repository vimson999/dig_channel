# 2026-06-26 独立豆包朗读短音频失败后没有自动重试

## 基本信息

- 状态：已修复
- 严重级别：中
- 影响模块：doubao browser tts / 测试页 / Celery worker
- 首次发现：`doubao-browser-tts-test.html` 批量投递 3 条文本，其中 1 条因音频过短失败
- 修复提交：待提交

## 现象

独立豆包朗读测试页中，任务 `9004973d-3637-4b2e-aa7c-a35d45c2ee7d` 在 `save_file` 后被校验为失败：

`豆包音频时长异常：文本 25 字，音频 0.447s，低于最低预期 1.0s，疑似朗读未完成或捕获到了空音频`

页面直接显示失败，没有自动重试。

## 影响

- 偶发的浏览器音频捕获不完整会直接变成终态失败。
- 用户只能重新投文本，无法对原任务原地重试。
- 独立豆包测试页和研报编排里的豆包子任务重试体验不一致。

## 根因

`tasks.doubao_browser_tts.process_job` 捕获异常后直接调用 `_mark_failed()`。研报编排父任务有自己的子任务重试逻辑，但独立测试页直接投递的是单个 `doubao_browser_tts` 任务，没有父编排兜底。

短音频、空音频、浏览器捕获超时这类错误属于可恢复错误，应该在任务层自动重试。

## 解决思路

- 在独立 `doubao_browser_tts` Celery 任务内增加可恢复错误自动重试。
- 验证码、取消、空文本等不可恢复错误不自动重试。
- 已经失败的历史任务提供页面原地“重试”能力。

## 实际方案

- `tasks.doubao_browser_tts`：
  - 增加 `DOUBAO_BROWSER_TTS_RETRY_LIMIT`，默认 2 次。
  - 增加 `DOUBAO_BROWSER_TTS_RETRY_DELAY_SECONDS`，默认 20 秒。
  - 对短音频、空音频、音频捕获、浏览器超时等错误调用 Celery `self.retry()`。
  - 重试等待期间任务回写为 `pending / auto_retry`，并在 `result.retry_state` 记录次数和下次重试时间。
- `api.doubao_browser_tts`：
  - 新增 `POST /api/doubao-tts/jobs/<job_id>/retry`，支持失败/阻塞/取消任务原地重投。
- `doubao-browser-tts-test.js`：
  - 失败、阻塞、取消任务卡片显示重试按钮。

## 验证

- 自动化验证：
  - `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_doubao_browser_tts_task tests.unit.test_doubao_browser_tts_service`
  - `PYTHONPATH=src/backend venv/bin/python -m py_compile src/backend/tasks/doubao_browser_tts.py src/backend/api/doubao_browser_tts.py`
  - `node --check src/frontend/js/doubao-browser-tts-test.js`

- 真实任务验证：
  - 失败任务 `9004973d-3637-4b2e-aa7c-a35d45c2ee7d` 已原地重投。
  - 重投后状态：`completed / done / 100`
  - 输出音频：`4.807s / 230780 bytes`
  - WAV：`data/processed/doubao_browser_tts/20260626/9004973d-3637-4b2e-aa7c-a35d45c2ee7d.wav`

## 复发预防

- 所有浏览器自动化捕获类任务都要区分可恢复错误和不可恢复错误。
- 独立测试页不能只依赖父编排兜底；基础任务本身也要有最小自恢复能力。
- 页面需要保留原地重试入口，避免用户为了同一输入重复创建任务。
