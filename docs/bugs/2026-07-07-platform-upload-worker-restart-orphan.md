# 2026-07-07 平台上传 worker 重启导致 B站上传任务假运行

## 基本信息

- 状态：已修复
- 严重级别：高
- 影响模块：MP3 详情页 B站上传 / `platform_upload` Celery worker / publisher queue repair
- 首次发现：B站上传记录显示 `运行中`，但 `platform_upload` worker 没有 active 任务

## 现象

MP3 详情页上传记录里出现：

```text
状态：运行中
账号：此刻此声0707
充电：1424170587911438656
```

数据库对应任务：

```text
task_id=1227f221-f245-4a86-a1ab-730ddaea112f
status=processing
current_step=upload_bilibili
celery_task_id=a12f769e-f854-49c9-b1b2-f1c58230957a
```

但 Celery 结果后端为 `PENDING`，`platform_upload` worker inspect active/reserved/scheduled 均为空。B站日志只记录到 `Starting Bilibili upload...`，没有成功或失败回写。

## 影响

- 页面会误导用户以为还在上传。
- B站上传属于外部副作用任务，不能盲目自动重投，否则可能重复投稿。
- 如果不及时收口，会持续占用业务视图的“运行中”状态。

## 根因

`platform_upload` worker 在 B站上传已经开始后被停止/重启，任务进程没有机会把失败状态写回数据库。旧的 orphan repair 使用通用 `processing` 任务 10 分钟 grace，导致上传任务被打断后会长时间停留在假 `processing`。

同时，监控页的 stop/restart/restart-all 没有区分“可重跑的渲染任务”和“不可随便中断的平台上传任务”。

## 实际方案

- 将 `upload_bilibili` / `upload_youtube` 识别为平台上传不可中断步骤。
- `platform_upload` 普通 stop/restart/restart-all 遇到上传中任务时直接跳过，返回 `active_non_interruptible_tasks`，除非显式 `force=true`。
- 上传步骤使用独立 orphan grace：`PUBLISHER_UPLOAD_PROCESSING_ORPHAN_GRACE_SECONDS`，默认 90 秒。
- 上传 orphan 只收口为失败，不自动重投，避免重复投稿。
- 现场任务 `1227f221-f245-4a86-a1ab-730ddaea112f` 已收口为 `failed/orphaned_processing`。

## 验证

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_publication_task_queue_repair -q
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_celery_monitor_watchdog -q
PYTHONPATH=src/backend venv/bin/python -m py_compile src/backend/apis/v1/celery_monitor.py src/backend/services/publisher_queue_repair.py
```

现场验证：

- `platform_upload` worker online，active 为空。
- 数据库中没有 `upload_bilibili` / `upload_youtube` 的 `processing` 残留任务。
- 后端重启后 bootstrap 只显示 `already_running`，没有重启已有 worker。

## 复发预防

- B站/YouTube 上传这种外部副作用任务默认不可中断。
- 自动/手动重启队列前，必须先看是否存在不可中断业务步骤。
- 上传任务断点恢复不能默认重投；只能失败收口后由用户手动确认再投递。
