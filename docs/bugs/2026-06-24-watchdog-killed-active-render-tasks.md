# 2026-06-24 Watchdog 误杀活跃 render 任务

## 基本信息

- 状态：已修复
- 严重级别：高
- 影响模块：celery watchdog / publisher / render worker / video editor recent tasks
- 首次发现：视频编辑器 recent tasks 中多条 publisher 任务积压，render worker 多次 `Warm shutdown`
- 修复提交：待提交

## 现象

用户在视频编辑器 recent tasks 中看到多条任务持续提示 `publisher 同类任务达到当前并发上限 4`，同时 Celery 看板里 `render` 队列显示 worker 在线、队列或业务状态异常。

现场排查发现：

- PostgreSQL 当前连接健康，不是连接槽耗尽。
- `render` worker 日志出现多次 `worker: Warm shutdown`。
- 任务 `90803424-6a9a-4223-85dd-3a2e19714d65` 已经完成 ASR 和 ffmpeg 合成，视频文件存在，但 worker 被打断后没有完成数据库终态回写。
- 任务 `69b8b7c2-f2cb-4f9a-ad28-676e68e4b72f` 曾在 ASR 阶段被中断，后来重投后完成。

## 影响

- 活跃 render/publisher 任务被 watchdog 当作消费异常重启，长耗时任务被中断。
- 已经生成文件但未完成 DB 回写的任务会留下 `processing/pending` 残留，继续占用 publisher 并发槽。
- 后续任务被 runtime guard 拦住，用户看到的是“系统保护/并发上限”，但真正上游原因是守护进程误判和中断。
- 问题容易被误判为数据库、Redis 或队列容量问题，导致继续调并发数字却不能根治。

## 根因

代码层根因：

- `run_watchdog_once()` 调用 `_service_status()` 时没有传入业务任务快照。
- `_service_status()` 原本已经支持 `business_tasks`，并且会在业务任务近期仍有更新时给出 `consumer_note`，避免把“队列有积压但业务还在推进”误判成 consumer stuck。
- 页面状态接口会传业务任务快照，但 watchdog 轮询路径没有传，导致 watchdog 的判断比页面少一个关键事实。
- watchdog 连续确认后自动重启 `render` worker，打断了正在 ASR/ffmpeg 的长任务。

架构层根因：

- worker 健康检查、业务任务生命周期、runtime guard 容量保护是三套控制面。之前的修复把 repair 和自愈接入 watchdog，但没有把“业务任务近期仍在推进”作为 watchdog 的硬约束。
- 人工 pause `render` 后，watchdog 前置的业务 repair 仍可能绕过 pause 去修复 publisher 任务。pause 语义没有贯穿到 repair 层。

## 解决思路

- watchdog 判断 worker 是否消费异常时，必须使用和页面一致的业务任务快照。
- 只要业务任务近期仍在推进，就不能因为 `queue_length > 0` 且 Celery inspect 暂时看不到 active 而重启 worker。
- 人工 pause 某个服务时，该服务关联的业务 feature 也必须从自动 repair 中排除。
- 对已经完成文件产出但缺失 DB 终态的任务，先人工恢复结果，再修 watchdog，避免再次打断。

## 实际方案

- `src/backend/apis/v1/celery_monitor.py`
  - `run_watchdog_once()` 增加 `_business_tasks_snapshot(force_refresh=True)`。
  - 调用 `_service_status()` 时传入当前服务的 `business_tasks` 和 `business_task_error`。
  - `_watchdog_repair_stale_business_tasks()` 增加 `_paused_business_features()`。
  - 当 `render` 被 pause 时，跳过 publisher 子任务 cleanup、pending repair 和 processing repair。
  - 通用 `repair_stale_publication_tasks_once()` 接收 `excluded_features`，避免 pause 被 repair 绕开。
- `tests/unit/test_celery_monitor_watchdog.py`
  - 增加 watchdog 会把业务任务传给 `_service_status()` 的回归测试。
  - 增加 pause render 时跳过 publisher repair 的回归测试。

现场处理：

- 暂停 render watchdog 自愈，避免继续打断活跃任务。
- 恢复 `90803424-6a9a-4223-85dd-3a2e19714d65` 的 DB 终态，补上已生成视频和封面路径。
- 重投 `69b8b7c2-f2cb-4f9a-ad28-676e68e4b72f`，确认竖屏视频生成完成。
- 重启 watchdog，恢复 render，确认队列清空。

## 验证

- 单元测试：

```bash
venv/bin/python -m unittest tests.unit.test_celery_monitor_watchdog tests.unit.test_publication_task_queue_repair tests.unit.test_runtime_guard
```

结果：21 个用例通过。

- 语法检查：

```bash
venv/bin/python -m py_compile src/backend/apis/v1/celery_monitor.py
```

结果：通过。

- 现场状态确认：
  - PostgreSQL `free_connections=91`，连接健康。
  - `render` queue/active/reserved/scheduled 均为 0。
  - `watchdog` 进程在线。
  - 三条累计任务均为 `completed / done / 100`，视频和封面路径均存在：
    - `c21e7af8-02c9-46a7-92b0-ffa4a0da2a7d`
    - `90803424-6a9a-4223-85dd-3a2e19714d65`
    - `69b8b7c2-f2cb-4f9a-ad28-676e68e4b72f`

## 复发预防

- watchdog 的 worker stuck 判断必须包含业务任务快照，不能只看进程、队列长度和 Celery inspect。
- 长耗时任务的“近期业务进展”必须是自愈重启的硬保护条件。
- pause 必须是端到端语义：进程拉起、业务 repair、孤儿重投都要尊重 pause。
- 自动 repair 记录需要写入 watchdog event，说明重投、降回排队、收口和关闭分别做了什么。
- 后续继续推进父编排状态机化，减少长任务被父 worker 或守护逻辑误伤的可能性。

## 后续事项

- 为所有重任务补充更明确的业务心跳字段，避免只依赖 `updated_at`。
- 将 watchdog 的“误杀保护”纳入核心冒烟检查：有活跃业务任务时不得自动重启对应 worker。
