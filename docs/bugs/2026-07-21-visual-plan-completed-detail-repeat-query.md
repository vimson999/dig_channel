# 已完成视觉方案详情重复查询远端数据库

## 基本信息

- 状态：已部署并完成跨后端进程真实复验
- 严重级别：中
- 影响模块：视觉方案 / 分镜详情 / publication_tasks / PostgreSQL
- 首次发现：2026-07-21 新 PID 日志复查
- 修复提交：待提交
- 根因分类：已完成任务缓存未跨重启、缺少按任务缓存、串行多次远端往返、投影过宽
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；已完成详情轮询重复打数据库簇的新入口
- 关联记录：`2026-07-10-report-audio-projection-and-parent-roundtrips.md`、`2026-07-21-stick-figure-asset-owner-query-amplification.md`
- 防线层级：shared_guardrail / sql_contract_test / runtime_acceptance / observability

## 现象与影响

- `_get_owned_visual_task` 历史出现 4 次慢查询，耗时 `1.05-4.32s`；新 PID 下仍有一次 `609ms`。
- 同一详情请求随后串行查询 output `345ms` 和 31 条 segment `1519ms`，接口总耗时 `3653ms`。
- `report_visual_plan_segments(task_id, sequence)` 索引已经存在，继续堆索引不能消除重复远端往返。

## 根因

按 source 查询已有 5 分钟进程内终态缓存，但服务重启后会完全丢失；本次 `3653ms` 正是新 PID 的第一次冷访问。直接按 job ID 访问详情也没有缓存。完成后的分镜结果不可变，旧接口仍在冷访问时依次读取任务、输出和全部分镜；ORM 的 `load_only` 还会自动带出 segment 主键和未使用的任务列。

## 实际方案

- 新增按“用户 ID + job ID”隔离的 5 分钟进程内终态详情缓存，最多 512 项并返回深拷贝。
- source 与 job 两种终态快照同时写入 Redis 24 小时持久层；进程内缓存丢失后直接从 Redis 恢复，避免每天重启后重新制造一次冷查询。
- 强制重生成会按当前用户精确删除 source 持久键；活跃任务不缓存，状态新鲜度不受影响。
- 任务与 output 查询缩窄到实际响应列；segment 改为 tuple 投影，不再物化 ORM 主键。
- 共享 Redis 客户端改为读取 `REDIS_URL`，不再忽略项目配置并错误回退到 `localhost:6379`。

## 验证

- 行为测试预先缓存完成结果，再强制数据库入口抛错；详情接口仍正确返回，证明命中时为 0 次数据库查询。
- 测试同时验证用户隔离和深拷贝，避免跨用户泄漏或调用方污染缓存。
- 跨进程行为测试清空全部内存快照，仅保留 Fake Redis，再次读取仍返回完整终态结果。
- 定向回归 `89 passed, 3 subtests passed`，编译通过。
- 真实 Redis 中确认 source/job 两个键均已写入，TTL 约 24 小时。
- 新 PID `94639` 清空全部进程内缓存后首次访问：`363.93ms`、相关数据库 SQL 为 0；同进程第二次访问 `1.08ms`、SQL 仍为 0。修复前相同接口为 `3653ms`。
- 同一页面跨重启后仍完整加载 31 张图片、31 段音频和 1 个视频。

## 复发预防

1. 已完成且不可变的详情接口必须同时覆盖 source 与 job 两种访问键。
2. 详情接口 SQL 预算按一次页面请求计算，不能只验证每条 SQL 是否有索引。
3. 缓存契约必须有“数据库入口抛错仍可命中”的行为测试，不能只做源码字符串断言。
