# 2026-10-27 MP3 等待子任务被 processing orphan 修复器误重投

## 基本信息

- 状态：OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION（11/11 两条现场任务再次出现跨运行体素材误判；本机续跑成功不代表隔离根因关闭）
- 严重级别：高
- 影响模块：`mp3_video_workflow` / `publisher_queue_repair` / Celery Watchdog / 多运行节点
- 首次发现：2026-10-27
- 修复提交：待提交
- 根因分类：父子任务生命周期误判 / 跨运行节点本地素材不可见
- 复发键：`MP3-PARENT-WAITING-CHILD-PROCESSING-ORPHAN-REQUEUE`
- 是否复发：首次确认该精确指纹；属于 `RUNTIME-TOPOLOGY-RELOAD` 分布式旧运行体集群的同类复发
- 关联记录：`2026-09-27-remote-old-runtime-production-db-sessions.md`、`2026-06-23-mp3-video-parent-long-wait.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

三个 MP3 视频编排在关键帧、封皮和 DS 结果均已生成后，页面同时变为 `failed / 0% / 音频准备`，错误均为“音频文件不存在”：

- `0fc4788f-649d-487b-bd7a-b02e4e0a5106`
- `97e24029-c6f7-4a5a-b51f-d18521914f3a`
- `0979c176-fd10-4b64-b75d-6838873f671a`

现场核对时，三份原始音频均存在；前两条的音频编辑产物也存在。第一个任务的横屏视频已经完成，说明报错并非原始音频真实缺失。

## 影响

- 三个父任务被错误终止，两个横屏 publisher 子任务和一个竖屏 publisher 子任务随后被终态清理器取消。
- 已完成的 DS、关键帧和封皮虽然保留，但完整视频生成被中断。
- 父任务错误回到 `0% / 音频准备`，掩盖了它实际已推进到横屏或竖屏视频等待阶段。
- 如果每天只重试父任务而不修复 repair 判定，同一状态机会再次被 Watchdog 误判。

## 根因

MP3 父编排等待 publisher 视频子任务时，会主动让出 worker，并把父任务保持为：

```text
status=processing
current_step=landscape_video_waiting / vertical_video_waiting
waiting_child_id=<publisher child>
```

`publisher_queue_repair.maybe_repair_stale_business_processing_task()` 只检查父任务自身的 Celery 消息是否仍在 worker 或 Redis 队列可见，却没有识别“父任务正在等待一个真实 active 子任务”。三个父任务因此都被写入：

```text
reason=processing_not_visible_in_worker
```

并以仅含父任务 ID 的参数重新投递。重投实例随后从头执行音频预检；在共享 DB/Redis/broker 且本地素材不共享的运行拓扑下，另一个文件系统视角把实际存在的绝对路径判为缺失，最终把父任务写成失败。

时间线证明误判发生时子任务仍然 active：

- `97e24029...`：15:55:48 重投；等待子任务到 15:57:46 才因父失败被取消。
- `0979c176...`：15:56:06 重投；等待子任务到 15:57:47 才因父失败被取消。
- `0fc4788f...`：15:56:24 重投；等待子任务到 15:57:48 才因父失败被取消。

`processing_orphan_repair` 仍缺少 writer actor，故不能把这次失败归因到某一具体主机；共享运行体隔离问题仍保持 OPEN。

## 解决思路

- 不能把 MP3 父任务的 `*_video_waiting` 等同于父 Celery 丢失。
- repair 必须读取结构化 `waiting_child_*` 投影，并以子任务数据库终态为准。
- 当等待子任务仍为 `pending/processing` 时，父任务保持原状态；publisher 子任务自己的 orphan repair 负责其消息恢复。
- 只有子任务已终态或等待投影失效时，父任务 repair 才可继续走原来的 orphan 判定。

## 实际方案

- 将 `waiting_child_label/id/status/step/updated_at` 加入 queue repair 窄投影与 detached snapshot。
- 新增 `_mp3_waiting_child_is_active()`，只保护 MP3 的横屏/竖屏等待阶段，并重新读取真实子任务状态。
- active 子任务存在时返回 `waiting_child_active`，不扫描父消息、不改变父状态、不生成新的父 Celery ID。
- 增加真实 SQLite 模型回归：构造 stale MP3 父任务与 pending publisher 子任务，证明父任务仍保持 `processing/landscape_video_waiting`。
- business processing repair 真正发生重投时写入 `actor`，记录 deployment root、hostname、PID 与 code identity，后续可以定位实际写入者。

## 验证

TDD 红灯：

```text
旧逻辑返回 repaired=True / action=requeued
```

定向绿灯：

```text
tests/unit/test_publication_task_queue_repair.py
55 passed，12 subtests passed
```

完整单元测试（在允许 loopback bind 的运行环境执行）：

```text
1827 passed，4 skipped，374 subtests passed
```

静态门禁：

```text
compileall 通过
git diff --check 通过
recurrence-registry.json 解析通过
Bug 严格检查通过：214 records / 9 clusters / latest 2026-10-27
```

运行态验证：

- 重启门禁先后因两个真实 publisher 合成任务 fail closed；等待它们自然完成后，`scripts/dev/check-restart-safety.py` 才返回安全。
- 未使用 `FORCE`，通过 `scripts/dev/restart-dev-stack.sh` 标准双门禁重启到 backend PID 19426；17 个 worker、Watchdog 与 6 个 scheduler 均 ready，bootstrap `errors=[]`，`/health` healthy。
- 三条现场任务从失败处续跑，期间真实观察到父任务保持 `landscape_video_waiting` / `vertical_video_waiting`，其 active publisher 子任务继续推进；未再次出现 `processing_not_visible_in_worker -> 音频文件不存在`。
- 最终数据库与文件验收：
  - `0fc4788f...` 于 16:47:32 完成；横版 769,484,379 bytes，竖版 582,994,233 bytes。
  - `0979c176...` 于 16:54:17 完成；横版 624,106,652 bytes，竖版 500,868,902 bytes。
  - `97e24029...` 于 17:04:46 完成；横版 450,863,742 bytes，竖版 346,038,434 bytes。
- 三个真实 HTTP 详情接口均返回 `200 / succeeded / 100%`；编排台页面显示“本页 3 个，运行中 0，成功 3，失败 0”，三张任务卡均为“成功 done 100%”。

## 复发预防

- 父任务是否“孤儿”必须联合判断父消息、结构化等待投影和子任务状态，不能只看父 Celery 可见性。
- 父任务主动让出 worker 的等待状态不得被 generic processing repair 从头重投。
- 本地绝对路径参与的任务仍须隔离共享 broker/DB 的旧运行体，代码门禁不能代替运行节点隔离。
- 下一轮巡检优先复核此复发键、三个现场任务后续状态和带 `actor` 的 `processing_orphan_repair` 写入者身份。

## 后续事项

- 完成旧运行体停机或 DB/Redis/broker namespace 隔离；否则跨主机本地素材不可见仍可能从其他入口复发。
- 取得完整后窗前保持 `OBSERVING`，不得仅凭单次续跑成功标记 CLOSED。

## 2026-11-11 再次复发与视频运行态验收

- `3a68528d-d87a-40b0-82ad-20d27e002e01` 在 Watchdog 将不可见父消息重投后，于 16:59:10 报“音频文件不存在”；对应本地 MP3 为 10,432,476 bytes，且文件 mtime 早于失败时间。
- `e69652da-aa51-4d03-aaf3-a799ea6c3b33` 出现同样的缺文件错误；其本地 MP3 为 21,306,296 bytes，任务已有 DS 规划、四张视觉素材和横屏视频。
- 两条记录都缺少能确认失败写入主机的 actor 信息。数据库快照仍看到来自 `100.119.238.48` 的旧 Mac 空 `application_name` 会话；这证明共享运行体仍在线，但不能单独证明它就是这两次写入者。
- 使用现有任务续跑入口在本机恢复：第一条从保存的 DS 结果和计划生成四张视觉素材并合成横竖屏；第二条复用四张视觉素材与已完成横屏，只补齐竖屏。两条父任务均为 `completed / done / 100%`。
- 成片文件与 `ffprobe` 验收：第一条横屏 395,958,993 bytes、1920×1080、767.900 秒；竖屏 287,083,184 bytes、1080×1920、767.867 秒。第二条横屏 721,450,315 bytes、1920×1080、1397.833 秒；竖屏 588,439,307 bytes、1080×1920、1397.833 秒；四份文件均含 H.264 视频与 AAC 音频。
- 结论：运行态视频已恢复；跨主机写入者未定位、共享 DB/Redis/Celery 旧运行体仍未隔离，本复发键保持 `OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION`。不得把本次人工续跑成功标成系统性 CLOSED。
