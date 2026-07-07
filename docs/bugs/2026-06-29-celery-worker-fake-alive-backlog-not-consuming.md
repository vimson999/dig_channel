# 2026-06-29 Celery worker 假活但不消费队列

## 基本信息

- 状态：已修复，已重启 watchdog 生效
- 严重级别：高
- 影响模块：Celery watchdog / report audio workflow / Redis broker / 任务监控
- 首次发现：2026-06-29 用户反馈豆包浏览器朗读“停了半个小时”
- 修复提交：待提交

## 现象

研报转语音任务页面长时间不再推进，用户直观看到豆包浏览器朗读像是停住。

排查时发现：

```text
report_audio_workflow worker 进程仍存在
Celery inspect active/reserved/scheduled 均为空
Redis report_audio_workflow 队列长度为 8
数据库中 2 个父任务卡在 doubao_*_waiting，2 个父任务仍 pending queued
```

现场重投 4 个父任务后，Redis 队列仍然堆积，说明不是单个任务消息丢失，而是 `report_audio_workflow` worker 处于“进程在线但不消费队列”的假活状态。

重启 `report_audio_workflow` worker 后队列开始下降，父任务继续推进：

```text
cb0bfa49... processing doubao_3_waiting
d3dbedee... processing doubao_3_queued / doubao_2_waiting
doubao_browser_tts 队列重新出现待处理子任务
```

## 影响

- 用户会看到任务长期停在某个百分比，误以为豆包浏览器朗读完全失效。
- Redis broker 中业务消息堆积，但监控层只看到 worker 进程活着，无法自动恢复。
- 父任务不会自然进入后续阶段，后续 pending 任务还会被容量保护延迟，形成“看起来系统很忙但实际没人消费”的状态。
- 现场只能靠人工查 Redis 队列、重投父任务、重启 worker 才能恢复。

## 根因

代码层有两个缺口：

1. `celery_monitor._service_status()` 已经能识别“worker 在线、队列有积压、inspect 无 active/reserved/scheduled”的消费异常，但如果业务任务近期有更新，会把这个异常降级成 `consumer_note`，不再设置 `consumer_warning`。
2. `run_watchdog_once()` 只把 `consumer_warning` 视为 stuck 条件，因此 recent business task 会让假活 worker 逃过 watchdog 自愈。
3. `_watchdog_config()` 中 `CELERY_AUTO_HEAL_ENABLED` 默认是 `False`，本地未显式设置时 watchdog 只告警/等待，不会自动重启异常 worker。

架构层根因是：watchdog 把“业务状态近期有变化”和“worker 是否正在领取 broker 消息”混为一类健康信号。业务状态可能来自人工重投、旧消息、父任务 DB 写回或延迟任务，不代表当前 worker 消费连接健康。

## 解决思路

消费健康应以 broker / inspect 为主：

- 只要 worker 进程存在、队列有积压、inspect 里没有 active/reserved/scheduled，就应持续给出 `consumer_warning`。
- 业务任务近期有更新只能作为辅助说明，不能压掉 warning。
- watchdog 默认应具备自动恢复能力；本地开发栈本来就是用它兜住 Celery worker 假活、孤儿任务和队列异常。

## 实际方案

在 `src/backend/apis/v1/celery_monitor.py` 中：

- `CELERY_AUTO_HEAL_ENABLED` 默认值从 `False` 改为 `True`。
- `_service_status()` 遇到“worker 在线但未领取积压任务”时始终设置 `consumer_warning`。
- 如果业务任务近期有更新，只追加 `consumer_note`，说明 watchdog 仍会按连续确认策略重启 worker。

在 `tests/unit/test_celery_monitor_watchdog.py` 中：

- 将 `test_recent_business_task_suppresses_consumer_warning` 改为 `test_recent_business_task_keeps_consumer_warning`。
- 断言 recent business task 场景仍然保留 `consumer_warning`，同时保留提示性的 `consumer_note`。

运行态处理：

- 手动重投 4 个卡住的 `report_audio_workflow` 父任务。
- 重启 `report_audio_workflow` worker，使 Redis 队列恢复消费。
- 重启 `celery_watchdog_loop.py`，确认新配置 `enabled=True` 已生效。

## 验证

单测与编译检查：

```text
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_celery_monitor_watchdog
PYTHONPATH=src/backend venv/bin/python -m py_compile src/backend/apis/v1/celery_monitor.py tests/unit/test_celery_monitor_watchdog.py
```

运行态验证：

```text
Redis report_audio_workflow 队列长度从 8 降到 6，后续受容量保护 retry 回升属预期。
report_audio_workflow worker 重新收到任务。
cb0bfa49... 从 doubao_1_waiting 推进到 doubao_3_waiting。
d3dbedee... 从 doubao_1_waiting 推进到 doubao_2_waiting / doubao_3_queued。
watchdog 配置输出 enabled=True。
```

## 复发预防

- 监控 worker 健康时，不能只看进程是否存在；必须同时看 broker 队列长度和 Celery inspect 的 active/reserved/scheduled。
- “业务任务最近更新”不能证明 worker 正在消费 broker 消息，只能作为诊断备注。
- watchdog 对假活 worker 必须走连续确认 + cooldown 的自动重启路径。
- 后续新增队列 worker 时，必须把服务加入 `DEFAULT_WATCHDOG_SERVICES`，并确保 `SERVICES` 中 queue、hostname_prefix、run_script 都完整。
- 排查“任务不动”时固定三件套：DB 任务状态、Celery inspect、Redis 队列长度。

## 后续事项

- 继续观察 `publication_tasks feature/status` 查询慢的问题；本次日志中 watchdog/运行保护查询曾出现 14-17 秒慢查询。
- 评估是否为 `publication_tasks(feature, status, updated_at)` 或业务投影列补更合适的热路径索引，减少 watchdog 轮询对数据库的压力。
