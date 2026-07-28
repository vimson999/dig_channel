# 优化视频缓存误连本机导致数据库并发回源

## 基本信息

- 状态：根因代码、运行依赖门禁、数据库端批插入和完整重启已落地，待下一次真实多频道同步验收
- 严重级别：P0
- 影响模块：optimized video sync / Redis / video_data / PostgreSQL / PgBouncer
- 首次发现：2026-07-23 日志回顾
- 修复提交：当前工作区
- 根因分类：运行依赖配置漂移、缓存失效放大、批量入库往返
- 复发键：`DB-SESSION-LIFECYCLE` / `HOT-PATH-PAYLOAD-POLLING` / `RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：第 N 次；慢 SQL 再次由数据库外部依赖和并发回源放大
- 关联记录：`2026-07-22-pgbouncer-cold-pool-connect-timeout.md`、`2026-07-18-publication-task-progress-json-write-amplification.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

- 2026-07-23 当天慢查询日志出现 558 条 1 秒以上 SQL，PgBouncer 有 30 次服务端 query timeout。
- 后端重启后的 50 频道同步反复记录 `Error 61 connecting to localhost:6379`。
- 同一轮同步随后出现 optimized video 批次创建、视频清理和频道同步数据库超时。

## 影响

- Redis 缓存完全失效后，5 个同步线程同时对远端 PostgreSQL 执行频道、视频存在性和每日快照查询。
- `video_data` 已有约 73 万行，旧实现把整频道视频和已有快照拉回应用再做集合差，放大连接占用、网络往返和内存。
- 表面上很多不同窄查询一起变慢，容易再次被误判为逐条缺索引。

## 根因

1. `.env` 已配置带认证的远端 `REDIS_URL`，但 `BaseConfig` 无条件用缺省的 `REDIS_HOST=localhost` 重建 URL。
2. `VideoSyncCache` 使用 host/port/db 构造客户端，绕过了项目统一的认证 URL。
3. 缓存每次失败都立即重试并逐条告警，没有熔断；缓存失效时仍保留 5 个数据库回源线程。
4. 每日快照使用三阶段“查视频、查已有记录、应用端求差后 bulk insert”，没有数据库侧幂等写入和并发锁。
5. 同步中的状态锁属于每个临时 service 实例，两个 API 请求不会看到彼此，重复点击可叠加两轮线程池。
6. 启动脚本只检查数据库迁移，不检查 Redis；错误配置可以静默启动并在业务高峰才暴露。

## 解决思路

- 先恢复单一 Redis 配置来源，再把缓存失败设计成可控降级，而不是继续并发打数据库。
- 每日快照的数据差集交给 PostgreSQL 完成，事务级锁保证同频道同日期只有一个写入者。
- Redis、数据库迁移和投影一致性都作为启动前硬门禁；依赖不健康时拒绝启动。

## 实际方案

- `BaseConfig.REDIS_URL` 优先读取环境变量，`VideoSyncCache` 改用 `Redis.from_url`。
- 新增 30 秒共享熔断器；失败窗口内不再重复建连或刷屏，整轮同步强制降为单线程。
- 同一用户的同步改为进程级 single-flight，并把正常并行度默认硬上限收窄为 3。
- `_create_video_data_records_batch` 改为 advisory lock 后单条 `INSERT ... SELECT ... NOT EXISTS`。
- 新增 `check-runtime-redis.py`，完整重启和单独启动后端都在加载业务代码前验证缓存、broker 和 result backend。
- 添加配置、熔断、数据库端批插入和启动顺序契约测试。

## 验证

- 真实远端应用 Redis 与 Celery broker PING 均成功。
- 完整确定性回归 `819 passed, 5 skipped, 42 subtests passed`。
- 最大频道 1465 个活跃视频的真实 PostgreSQL 回滚验收：advisory lock `76.42ms`，`INSERT ... SELECT` `56.96ms`，SQL 成功且事务回滚。
- 2026-07-23 13:42 完整重启后，后端、全部 Celery worker、watchdog 和 scheduler 均由新进程替换；应用缓存实际连接远端 Redis DB 14 且 PING 成功。
- 重启后的两轮日志观察中没有再出现 `localhost:6379`、Redis `Connection refused`、PgBouncer query timeout、statement timeout 或 `optimized_video_sync_service._process_videos_with_cache` 慢查询。
- 待完成：下一次真实多频道同步结束后继续保留一次业务级验收；当前未为验证而主动触发外部 YouTube API 和业务写入。

## 复发预防

1. Redis 客户端必须使用统一 URL，禁止业务服务自行用 host/port/db 重新拼接并丢失认证信息。
2. 缓存是减载依赖时，失败必须有熔断、日志抑制和数据库并发上限，禁止无限制 fail-open。
3. 用户可重复触发的批同步必须使用跨 service 实例的 single-flight；实例字段不构成并发保护。
4. 大批量“查全量后应用求差再插入”必须优先改为数据库端集合语句，并提供并发幂等约束。
5. 启动验收必须先检查 Redis、broker、schema 和投影契约；任何一项失败都 fail closed。
6. 多种无关窄 SQL 同时变慢时，先关联 Redis、PgBouncer 和网络事件，不得直接批量加索引。

## 后续事项

- 新进程真实同步后补充一次完整业务批次的慢查询和 PgBouncer timeout 对比。
- `video_data` 现有少量历史重复记录需单独审计业务语义，再决定是否清理并升级为唯一约束。
