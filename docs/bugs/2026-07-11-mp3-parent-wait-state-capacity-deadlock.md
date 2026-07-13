# 2026-07-11 MP3 父任务等待状态误占容量导致死锁

## 基本信息

- 状态：已修复
- 严重级别：P1
- 影响模块：MP3 视频编排 / publisher 子任务 / runtime guard / Celery watchdog
- 首次发现：2026-07-11
- 修复提交：待提交
- 根因分类：父子任务状态镜像与容量统计规则不一致
- 复发键：`ORCH-PARENT-CHILD-STATE`
- 是否复发：同类问题第 2 次
- 关联记录：`2026-07-07-mp3-video-publisher-file-done-parent-waiting.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

三个 MP3 视频编排任务持续显示业务进行中。一个父任务停在 `landscape_video_queued` 80%，另外两个任务反复进入 `deferred_capacity`。页面运行近一小时没有继续，但 render worker 和 MP3 worker 均为空闲。

## 影响

- 三个横屏 publisher 子任务实际已经完成，父任务却没有进入竖屏合成阶段。
- 停滞父任务被计入 MP3 编排运行并发，占满唯一容量槽位。
- 后续任务每三分钟重试一次但永远无法获得容量，形成自维持死锁。
- 监控页面把假运行状态展示为真实业务进行中。

## 根因

父编排等待 publisher 子任务时，会把子任务的当前阶段镜像为 `landscape_video_queued`、`landscape_video_synthesis` 等 `landscape_video_*` 状态。runtime guard 只把固定状态 `landscape_video_waiting` 和 `vertical_video_waiting` 视为被动等待。

因此，只要父任务镜像了任意其他子任务阶段，就会被错误计入 MP3 编排运行容量。延迟续跑消息丢失或 worker 重启后，孤儿修复重新入队也会被这个假容量拦截。

## 解决思路

容量判断应依据状态族语义，而不是列举某两个具体状态。MP3 父任务处于任何 `landscape_video_*` 或 `vertical_video_*` 阶段时，都只是在等待 publisher/render 子任务，不应占用 MP3 编排执行容量。

## 实际方案

- runtime guard 新增被动等待状态前缀配置。
- `landscape_video_*`、`vertical_video_*` 全部从 MP3 编排 start capacity 中排除。
- 规划、DS、制图等父编排真实执行阶段仍正常计入并发。
- 回归测试覆盖 waiting、queued、synthesis、wait_existing 和 done 等镜像状态。

## 验证

- `tests.unit.test_runtime_guard` 覆盖五种父任务镜像状态，断言其运行容量计数均为 0。
- 增加真实执行状态 `generate_visual_assets` 后，断言容量仍被正确占用并阻止第二个任务启动。
- 现场核对三个 publisher 横屏子任务均已 completed，修复前父任务仍为 processing/pending，符合死锁复现条件。

## 复发预防

- 父任务镜像子任务状态时，容量和 orphan repair 必须按状态族处理，禁止只枚举单一示例状态。
- 新增 publisher 子阶段时，不需要同步修改容量白名单。
- 监控发现 worker 空闲但业务任务持续 `*_video_*` 时，应触发父子任务对账告警。

## 后续事项

- 将父任务唤醒从纯延迟轮询进一步升级为“子任务完成事件 + watchdog 对账”双通道。
