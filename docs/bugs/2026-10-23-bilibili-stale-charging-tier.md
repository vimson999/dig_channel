# B站投稿复用已失效充电档位

## 基本信息

- 日期：2026-10-23
- 状态：OPEN / REPEATED / RUNTIME_REPLACED / OBSERVING
- 严重级别：P1
- 影响模块：Publisher upload-only、B站投稿、平台凭据充电配置
- 根因分类：外部可选项 ID 生命周期未在执行前复核
- 复发键：`BILIBILI-STALE-UPOWER-TIER`
- 是否复发：2026-07-07、2026-10-20 和本轮均出现上游错误码 21523
- 防线层级：durable_failure / runtime_validation_missing / user_choice_required

## 现象与影响

2026-10-22 06:00 至 2026-10-23 06:00 固定窗内，upload-only task
`a2562250-43cd-47d0-b6a6-0d93cfef84bb` 与
`a69c7082-7d65-4ccb-baac-60f809afe3e6` 均在视频上传后被 B站拒绝，返回 21523：
“选择的充电档位状态不可用，请重新选择”。两条任务均 durable `failed/failed`，原始错误被保留，没有
伪造成功或无界重试；但这使本窗 Publisher 失败率进一步上升，并浪费了已经完成的媒体上传时间。

两条任务保存的 `upower_level_id` 都是 `1424170587911438656`。巡检时通过当前账号真实
charging-options 接口复核，可选档位只有 `1357406026810593731`、`1381310463006055021` 和
`1431031586035418859`；保存值已不在远端权威集合中。因此不是 PostgreSQL executor、连接池或媒体文件问题，
而是持久化外部选项在真正投稿前已经失效。

## 根因与解决思路

充电档位是 B站账号侧可变资源，创建任务时合法不代表执行时仍合法。当前执行链直接复用 durable ID，缺少
“提交前重新拉取权威选项并验证所选 ID”的合同。21523 属确定性业务拒绝，增加 timeout、限流或重试不会
使已删除/停用档位恢复；把它静默替换成其他收费档位又会改变用户付费语义，也不允许。

正确闭环需要：执行前释放数据库 Session，读取当前充电档位；若原 ID 不存在，保持原始任务参数与上游错误，
以可重选状态明确要求用户选择；只有用户确认新的档位后才可提交。后续可在任务创建和执行两个边界共享同一
验证器，并用“创建后档位被删除”的契约测试固定行为。

## 本轮处置与验收

本轮完成真实远端选项复核、错误链定位、复发键和巡检门禁登记；没有自动替换档位，也没有修改生产配置。
状态保持 **OPEN / REPEATED / WAITING_USER_RESELECT**。责任动作是用户在发布配置中重新选择仍可用档位；代码
责任动作是补执行前权威复核与可重选错误合同。只有定向回归通过、真实重新选择后的投稿成功，并在后续完整
窗口 21523 为零，才能标记 CLOSED/RUNTIME_ACCEPTED。

## 2026-10-24 根因修复与真实恢复证据

固定窗再次出现同一持久旧档位 `1424170587911438656`：task
`a2eb9e78-4ae9-49dd-8445-0f4012c545ec` 在完整媒体上传后以 21523 失败、壁钟 127.235 秒。随后同一父任务、
同一视频和凭据由用户改选当前档位 `1357406026810593731`，task
`ab6c29db-f5da-4371-a0ce-eda5b200cce8` 成功并返回 `aid=117489484700356`、`bvid=BV16Dnt6eE46`。
当前 charging-options 仍列出新档位且不含旧档位，证明用户恢复有效，也确认根因不是媒体、凭据或数据库。

已用 TDD 在 `BiliUploader` 增加执行时权威选项校验，并在 `run_bilibili_upload_only` 的
`db.session.remove()` 之后、任何媒体上传之前调用。旧 ID 不再发起媒体上传，而是保留任务参数，持久化
`failed/waiting_user_reselect`、`bilibili_error_code=stale_upower_level` 和
`bilibili_reselect_required=true`。回归合同证明校验时 `in_transaction=False` 且上传调用为 0；没有静默替换
收费档位，也没有把确定性错误交给重试。

双安全门确认无不可中断任务后，标准重启到 backend PID 80782；bootstrap 17/17 worker、Watchdog、6/6
scheduler 均 `ok=true`、`errors=[]`，platform_upload PID 80869 来自当前 checkout。状态更新为
**OPEN / REPEATED / RUNTIME_REPLACED / OBSERVING**：真实成功样本发生在代码修复前的人工重选，不能冒充新
防线的自然运行态验收。后续须由自然失效档位或受控非上传校验证明新错误合同，且完整窗口 21523=0，方可升级
RUNTIME_ACCEPTED/CLOSED。

## 2026-10-25 运行替换后仍无自然 upload-only 验收样本

固定窗没有 upload-only 任务，21523 为 0 仅代表未触发，不是新预校验合同已被自然验收。当前 backend 与
17/17 Celery service 运行正常，platform-upload 运行体未见拓扑冲突，但没有失效档位进入
`waiting_user_reselect` 的真实样本，也没有修复后用户重选投稿成功样本。

状态保持 **OPEN / REPEATED / RUNTIME_REPLACED / OBSERVING**。下一轮继续要求失效 ID 在媒体上传前被拦截、
上传调用为 0、原参数与错误完整保留；随后由用户重选的自然投稿成功，并在完整窗口确认 21523=0。

## 2026-10-27 八条自然投稿成功，但没有失效档位验收样本

固定窗 upload-only 8/8 succeeded，21523 为 0；charging-options 7 个 GET 中 6 成功、1 个外部 502，随后同凭据
恢复 200。没有失效档位进入 `waiting_user_reselect`，因此不能证明新预校验在自然运行中做到媒体上传调用为 0；
成功样本也不能替代用户重选闭环。

状态保持 **OPEN / REPEATED / RUNTIME_REPLACED / OBSERVING**。下一轮仍要求真实失效 ID 在媒体上传前被
拦截、原参数/错误保留，随后用户重选投稿成功，并在完整窗口确认 21523=0。
