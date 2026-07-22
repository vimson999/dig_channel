# 完整重启误中断活动 render 任务

## 基本信息

- 状态：2026-07-21 再次复发；竞态修复已落代码，待新 PID 和真实负载验收
- 严重级别：P0
- 影响模块：开发栈重启 / Celery render / publisher / MP3 视频编排
- 首次发现：2026-07-18
- 修复提交：未提交
- 根因分类：重启前缺少数据库业务状态预检、Celery control 响应不能证明忙碌 worker 空闲、监控页重启旁路未复用安全门禁、优雅退出窗口过短
- 复发键：`RUNTIME-RESTART-ACTIVE-TASK-GUARD`
- 是否复发：同类问题复发
- 关联记录：`2026-07-11-backend-restart-accepted-stale-listener.md`、`2026-07-17-background-stop-residual-check-false-success.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

publisher 子任务 `3d7737e8-48e7-4490-b3e5-03b42ef038bd` 的 Celery 消息
`6ced0b30-ffbc-49de-b2fa-03f131d8a856` 于 11:09:38 开始执行。12:04 完整重启后，
同一消息在 12:09:45 以 redelivered 状态再次开始，之前约 55 分钟的媒体处理被中断并重做。

2026-07-19 20:53:08，MP3 业务任务 `06431ca1-b532-425e-a84f-a47827e4b50f`
对应消息 `59c9160c-6e47-44d2-b9e6-614214c7bcb6` 刚进入 `direct_ds_plan`，监控页重启在
20:53:10 关闭 worker。消息留在 Redis `unacked`，数据库停在 `processing/58%`，直到
3600 秒 visibility timeout 后才于 21:53:12 重新投递，并在 21:54:06 成功。期间该行占住
MP3 唯一容量槽位，其他任务每 180 秒成批执行准入检查、状态回写和 Celery Retry。

22:10 为替换 Flask 新代码单独执行 `stop-backend.sh` 时，脚本递归收集了 Flask 的全部后代。
bootstrap 使用 `start_new_session=True` 启动的 Celery、watchdog 和 scheduler 虽然是独立服务，PPID
仍暂时挂在 Flask 下，因此也被误停，并再次中断一个正在 render 的 publisher 子任务。第一版修复
尝试读取 `ps sid`，macOS 不支持；第二版改读 `ps sess`，但 Darwin 返回审计会话值 `0`，真实
进程树实验仍会误杀。最终改用 Darwin/procps 都支持的 `pgid` 作为隔离边界。

2026-07-21 13:16–13:25，首次安全预检后，2 个简笔画图片任务和 5 个豆包浏览器
TTS 任务从 `queued` 被 worker 领取。停止脚本随后对仍在执行的 solo worker 执行
`SIGKILL`，7 条 `publication_tasks` 永久停在 `processing/wait_*`。Celery 的
`active/reserved` 已为空，但数据库门禁持续阻止后续重启。

## 影响

- 长视频编码被浪费并显著延迟父任务完成。
- 父任务持续等待，进一步放大数据库轮询和 JSON 读取。
- 只看 Celery inspect 的响应节点会把未响应或忙碌节点误当成空闲。

## 根因

`restart-dev-stack.sh` 在停止后端和全部 worker 前没有任何业务任务预检。
Celery control 只能证明响应节点报告了什么，不能证明未响应的 solo/忙碌 worker 没有任务；
脚本却没有使用数据库中的结构化 `status/current_step` 做第二道 fail-closed 判断。

第一轮只修了 shell 全栈重启入口，`celery-monitor.html` 的单服务和一键重启仍直接调用
`_stop_service`；除平台上传外的 MP3、研报、内容流水线等队列没有阶段门禁。一键重启也没有
“先检查全部、再开始停止”的原子预检。worker 收到 SIGTERM 后最多只等 3 秒便 SIGKILL，
使 `acks_late` 消息来不及恢复，只能等待 Redis visibility timeout。

2026-07-21 复发证明“任何副作用前的一次原子预检”仍不够：预检与真正停止 worker
之间存在 check-then-act 时间窗。预检时仍在队列中的消息被视为可中断，但 worker 可在
预检返回后领取它；各 stop 脚本又会在很短的优雅退出窗口后强杀，最终形成孤儿状态。
同时通用 processing 恢复器未注册 `doubao_browser_tts` 和
`report_stick_figure_image`，因此 watchdog 无法自动收口这两类新子任务。

## 解决思路

完整重启前先查询数据库事实状态。`processing` 且处于真实执行阶段的任务一律阻断；
明确的 queued、deferred 和 parent waiting 状态不阻断。无法完成预检时同样拒绝重启。

## 实际方案

- 新增 `scripts/dev/check-restart-safety.py`，只读取任务 ID、feature、状态、阶段、进度、标题和更新时间。
- `restart-dev-stack.sh` 在调用 `stop-backend.sh` 之前强制运行预检。
- 发现活动任务时打印具体任务并退出，不停止任何进程。
- 仅在操作者明确设置 `FORCE_RESTART_ACTIVE_TASKS=1` 时允许绕过。
- 监控 API 的单服务重启和一键重启统一使用结构化业务阶段门禁；任何任务或门禁查询失败均 fail-closed。
- 一键重启先用一次轻量投影读取全部 `processing` 任务并完成全服务预检；存在阻塞时返回 409，且不停止 watchdog、worker 或 scheduler。
- worker 优雅退出窗口改为默认 10 秒、可配置；`unacked` 同时纳入自动修复的运行态引用，不能再被当作“消息彻底消失”。
- `stop-backend.sh` 只递归停止与 Flask 相同 `pgid` 的后代；独立服务由 `start_new_session=True`
  建立独立进程组，backend-only 停止不再越过该边界。无法读取纯数字 `pgid` 时 fail-closed。
- 2026-07-21 起，完整重启在首次门禁后先停止 watchdog，再向全部 Celery 队列广播
  `cancel_consumer`，冻结新消息领取；消息保留在 Redis，不做 purge。
- 停止后端入口后执行第二次安全检查；若消息恰好在冻结前被领取，最多等待 900 秒让其
  自然结束，超时也拒绝强杀 worker。
- processing 孤儿恢复器新增豆包浏览器 TTS 和简笔画图片任务，覆盖全部真实执行阶段。
- 恢复前释放数据库 Session，再检查 Celery/Redis；原消息仍在 queue/unacked 时只把 DB
  状态降回 `pending`，不重复发布消息；原消息确实消失时才重投。

## 验证

- 单元测试覆盖 synthesis/upload/browser 实跑阶段与 queued/deferred/waiting 状态分类。
- 契约测试确保预检调用位于 `stop-backend.sh` 之前，并保留显式强制开关。
- 2026-07-19 定向 SQL、状态机、监控和 PgBouncer 回归共 `173 passed`；追加 `unacked` 运行态引用后监控专项 `13 passed`。
- 21:55:33 shell 全栈重启二次预检发现 publisher `synthesis` 和 MP3 父等待任务，退出码非零且尚未停止任何进程。
- 21:56:20 使用真实数据库状态调用新监控 `/restart-all`，返回 409、`success=false`，仅报告真实 render 阻塞任务，`_stop_watchdog` 未执行。
- 22:24 临时真实进程树验收：root 与同组子进程被停止，`start_new_session=True` 的独立子进程
  保持存活（`root_alive=0`、`same_group_child_alive=0`、`independent_group_child_alive=1`）。
- 2026-07-19 23:38 预检确认不可中断任务为 0；目标 worker 逐个重启成功，业务计数与队列均为 0。
- 两次 backend-only 重启后 Celery、watchdog 和 ZSXQ scheduler 均保持存活，后端监听 PID 与 PID 文件一致。
- 2026-07-21 新增定向测试覆盖消费冻结、冻结后复检、两类浏览器子任务恢复、unacked
  消息降级而不重复投递，以及外部检查前释放 DB Session。当前只完成 `code_changed`，
  仍需 7 条遗留任务恢复和新 PID 真实负载验收。

## 复发预防

- “收到 N 个 empty 响应”不得表述为“全部 worker 空闲”，除非同时核对预期拓扑和数据库业务状态。
- 完整重启默认 fail-closed；预检失败不能降级为继续杀进程。
- 强制绕过必须由操作者显式设置环境变量，不能由脚本自动决定。
- 所有停止/重启入口必须复用同一阶段分类；新增 UI、API 或 watchdog 重启入口而未接入门禁，按 P0 复发处理。
- 一键操作必须执行“预检、冻结新消费、关闭入队入口、复检”四段门禁；单次预检不再视为充分条件。
- `acks_late` 任务被中断后必须同时核对普通队列、active/reserved/scheduled 和 Redis `unacked`；只看队列长度为 0 不得判定消息消失。
- 停止脚本的进程边界必须做真实子进程实验；字符串扫描单测不能证明 Darwin/Linux 的 `ps`
  字段语义一致。`start_new_session=True` 的托管服务统一以 `pgid` 验收隔离。

## 后续事项

- 将重启安全预检结果展示到 Celery monitor 的一键重启确认区。
