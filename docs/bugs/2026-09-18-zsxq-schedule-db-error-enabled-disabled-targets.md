# 数据库瞬断把已停用的 ZSXQ 全部 Tab 计划临时启用

## 基本信息

- 日期：2026-09-18
- 状态：RUNTIME_REPLACED / OBSERVING
- 严重级别：P0
- 影响模块：ZSXQ 全部 Tab 自动同步 / 独立调度器 / PostgreSQL / PgBouncer
- 首次发现：2026-09-18 每日数据库性能巡检
- 修复提交：当前工作区
- 根因分类：数据库配置读取失败后的 fail-open 回退
- 复发键：`DB-SESSION-LIFECYCLE`、`RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：数据库网络故障复发；错误配置回退是本轮新发现的上游放大器
- 防线层级：shared_guardrail / contract_test / observability / runtime_replaced / recurrence_acceptance_pending

## 现象与影响

2026-09-18 01:36:15–01:36:30，`run_zsxq_auto_scheduler.py` 读取两条
`system_configs` 时持有连接 15.218 秒，其中失败 SQL 15.208 秒、非 SQL 10 毫秒。
同一时刻 PgBouncer 记录一次 `query_wait_timeout`，随后 TLS 接收超时和 server crash。
失败 SQL 不会写入只记录完成慢查询的 `slow_query.log`。

异常分支把数据库读取失败当作“尚无配置”，用 `enabled=True` 的 bootstrap 默认值注册
生财有术和洪灝全部 Tab 两条 180 分钟计划。只读数据库对账确认两条真实配置的 `status=0`。
01:36:30 错误回退计划注册，01:37:00 数据库恢复后才移除；期间未见任务投递。
若故障持续到下一触发时间，这条路径可能违背操作者的停用设置并投递同步任务。

## 根因与解决思路

`load_all_tabs_auto_sync_configs()` 的数据库异常分支忽略已持久化的启停状态，
把两个固定目标无条件构造成启用。数据库短暂不可达时无法证明操作者仍允许自动同步，
因此异常分支必须 fail closed；恢复后由下一轮正常读库重建计划。

## 实际方案

- 数据库读取失败时 rollback/remove Session，记录明确告警，并返回空计划集合；不创建任何启用的默认计划。
- 保留首次正常读库时插入缺失默认配置的既有行为，不修改数据库现有行、原始 JSON、生产配置或索引。
- 增加故障注入回归：两条数据库配置均停用，模拟读库异常，断言运行计划为空；恢复后仍读到两条停用配置。

## 验证与复发预防

- 修复前同一回归返回两条 `enabled=True`、`source=bootstrap_fallback`，按预期红灯；修复后 ZSXQ 服务、调度器与分析器最终定向 58/58 通过。
- 06:17 安全门确认无不可中断任务后标准重启；06:18 后端引导报告 17/17 worker、6/6 scheduler、errors=[]，新调度器从 DB 读到两条停用配置，`startup_trigger queued=False targets=[]`，后端 `/health` 为 200。
- 完整测试及其独立失败见 `reviews/2026-09-18-db-performance-audit.md`；尚未在新进程上发生可观察的真实数据库故障，后续完整 24 小时窗口前保持 OBSERVING。
- 下一次故障必须证明 `DB schedule config unavailable` 时 all-tabs job 数为 0，恢复后按数据库 `status` 重建且没有意外投递；同时核对 PgBouncer、任务表与调度日志。

## 2026-09-19 后窗复核

新 PID 72404 在 09-18 06:18 启动，读取两条 `status=0` 配置，保持
`targets=[] queued=False`；本次固定窗（09-18 06:00–09-19 06:00）未见生产配置读取故障，
仅新进程后窗不足完整 24 小时。窗后 `/api/v1/zsxq/system-sync-scheduler/status` 返回
200/0.471 秒。06:07/06:15 的故障注入与恢复文本是测试污染，不得作为生产故障验收。
仍为 RUNTIME_REPLACED/OBSERVING；待真实或受控故障中证明 0 job、0 投递与恢复后仍停用。

## 2026-09-20 完整后窗复核

固定窗内没有生产 `DB schedule config unavailable`、意外 all-tabs 投递或 ZSXQ sync run；
两条停用配置继续保持 `status=0`。四个重点 API 的主动验收均为 200：overview 281.61ms、
topics/local 390.60ms、topics/local/tabs 213.16ms、sync/status 441.20ms。overview 返回陈旧缓存后
在同一 Web 进程异步刷新 3.636 秒，未把刷新 Session 跨到前台请求。

本窗仍没有真实数据库故障样本，0 投递只能证明正常路径尊重停用配置，不能证明故障注入路径
已在运行态触发；保持 RUNTIME_REPLACED/OBSERVING。下一次数据库异常仍须验证 0 job、0 投递和恢复语义。
