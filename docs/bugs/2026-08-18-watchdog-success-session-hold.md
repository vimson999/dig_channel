# Watchdog 成功修复分支未归还数据库 Session

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING
- 严重级别：P0
- 影响模块：Celery Watchdog / publication_tasks / Session 生命周期
- 首次发现：2026-08-18 日常数据库巡检
- 根因分类：成功分支 Session 泄漏、修复器串行调用、后续文件与 broker 工作交叉持有
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：是；与 `2026-07-21-watchdog-repair-idle-transaction-and-poll-overlap.md` 同类
- 防线层级：finally_release / unit_contract / runtime_replacement

## 现象与根因

窗口内 70 条连接长持有，P50/P95/最大为 7.777/20.315/23.889s；watchdog 相关 scheduler 链路中，清理 Publisher 子任务 6 次累计 59.270s、非 SQL 持有 52.690s，研报视频统计 7 次累计 58.960s、非 SQL 持有 33.984s。

`_watchdog_repair_stale_business_tasks` 串行调用多个独立修复器。包装器 `_isolated_watchdog_repair` 只在异常时释放 Session；成功且无修改的只读修复器把 checkout 留给后续修复器、文件检查和 broker 控制路径。

## 实际修复

包装器改为在 `finally` 中无条件调用统一 Session 释放函数，确保每个修复器无论成功、无操作还是异常都以独立短事务结束。没有改变修复条件、任务字段或重试语义。

## 验证与未闭环

- TDD 新增“成功修复器结束后、下一个修复器开始前已经 release”的调用顺序测试；旧实现先失败，修复后 watchdog 模块 37 个测试通过。
- 重启前安全门确认无不可中断任务；标准重启后新 watchdog 已运行。新进程若干轮单条 SQL 约 25–535ms，未出现旧型跨修复器 >5s 持有。
- 修复后观察窗不足 24 小时，保持 OBSERVING；不能以几轮无告警标记 CLOSED。

## 防复发与下一轮验收

所有 watchdog repair wrapper 的成功、无操作、异常分支都必须归还 Session。下一轮按新 PID 切窗，要求跨修复器/文件/broker 的 checkout 为 0、无 idle-in-transaction，并用实际 repair round 而非单元测试完成验收。
