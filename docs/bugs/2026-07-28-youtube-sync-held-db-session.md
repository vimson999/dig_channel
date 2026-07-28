# 2026-07-28 YouTube 频道与视频同步跨远端请求持有数据库 Session

## 基本信息

- 状态：已修复，待新计划任务首次真实执行验收
- 严重级别：P1
- 影响模块：首页频道管理同步 / 视频管理同步 / YouTube 调度计划 / PostgreSQL
- 首次发现：2026-07-28
- 修复提交：当前工作区
- 根因分类：数据库 Session 生命周期
- 复发键：DB-SESSION-LIFECYCLE
- 是否复发：同类问题复发
- 关联记录：`2026-07-24-db-session-crossed-broker-and-remote-io.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

首页和视频管理的手动“同步”能够完成，但同步服务先加载 `Channel` 或检查现有
视频，再调用 YouTube API。网络请求期间 SQLAlchemy Session 的只读事务没有主动
结束。原有旧 scheduler 也直接复用这些路径，且没有节点邮件或独立运行状态。

## 影响

同步频道较多或 YouTube API 响应变慢时，多个并行线程可能同时长期占用数据库
连接，放大连接池和 PgBouncer 压力。计划任务若直接照搬旧实现，会把这一风险
固定成每日自动发生。

## 根因

`YouTubeSyncService.sync_channel_data()` 和
`OptimizedVideoSyncService._get_channel_videos_cached()` 将数据库读取、YouTube
HTTP 请求和结果写入串在同一调用链，却没有在 HTTP 请求前将 ORM 数据转换为标量
快照并释放 Session。两个并行服务在创建线程池前也没有统一清理主线程 Session。

## 解决思路

保留首页手动同步的业务契约和并行服务，只调整资源边界：

1. 短查询只读取频道 ID、YouTube channel ID 和启用状态。
2. 转为标量后释放 Session，在事务外调用 YouTube。
3. 请求完成后重新加载当天和上一期数据，在短事务中写入。
4. 独立 scheduler 每次工作单元结束统一 rollback/remove。

## 实际方案

- `youtube_sync_service.py` 在远端请求前调用统一
  `release_db_session_quietly()`，请求后重新查询并写入。
- `optimized_channel_sync_service.py` 在频道列表转换成字典后释放主线程 Session。
- `optimized_video_sync_service.py` 使用频道 ID/名称标量投影，并在读取首次同步状态
  后、远端视频请求前释放 Session；每个线程结束再次统一清理。
- 新增 00:03 独立计划任务，使用 scheduler 角色连接池、独立 PID/日志、数据库
  配置和启动/完成/失败节点邮件。
- 新增 01:00、最近 3 天的视频同步计划，复用页面优化服务和相同运行保护。

## 验证

- 单元测试在模拟 YouTube 请求回调中断言 Session 释放函数已经执行。
- 配置测试覆盖 00:03、Asia/Shanghai、5 线程和无效配置安全回退。
- 后端 bootstrap 测试覆盖计划任务默认自启动。
- 迁移、真实新进程、Celery Monitor 和 PostgreSQL `idle in transaction` 将在
  本次上线步骤中现场检查。

## 复发预防

- 新的 scheduler 不再复用旧的 08:00/12:00/20:00 和每小时补抓逻辑。
- 外部 API 同步统一遵循“短读 → 释放 → 远端请求 → 短写”。
- Celery Monitor 固定展示任务进程、日志和每日执行说明。

## 后续事项

- 首次 00:03 自动执行后核对成功/失败数量、邮件投递与数据库连接状态。
