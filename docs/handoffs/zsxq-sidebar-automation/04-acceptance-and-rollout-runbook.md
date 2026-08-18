# 人工验收与上线运行手册

## 当前阶段

当前停在 Gate A。Gate A 未经用户明确确认通过前，不得进入 Gate B；Gate B 未通过前，不得恢复正式参数或计划任务。

## Gate 0：验收前检查

1. 确认后端健康。
2. 确认项目 MP3/PDF skill 与 `/Users/v9/.codex/skills/` 安装副本一致。
3. 确认两个 skill 仍为正式参数 48/40/40。
4. 确认自动化 `/Users/v9/.codex/automations/mp3-2/automation.toml` 中为 `status = "PAUSED"`。
5. Chrome 使用已登录知识星球的 `vimson5000` profile。
6. 尽量只保留一个目标 vimson5000 Chrome 窗口，避免旧计划任务遗留的多个同 profile 窗口造成定位歧义。
7. 不要用 Codex 直接下载文件；本阶段由用户在侧边栏主动调用两个 skill。

## Gate A：正式参数人工验收

### A1. MP3

打开并刷新：

```text
https://wx.zsxq.com/tags/调研纪要/88844545452542
```

确认主题列表加载后，在同一窗口 ChatGPT 侧边栏新建对话，只发送：

```text
请调用技能：知识星球浏览器侧边栏mp3下载。
```

不需要授权码。

### A2. PDF

MP3 完整结束后，打开并刷新：

```text
https://wx.zsxq.com/tags/外资研报/51184248544214
```

在同一窗口 ChatGPT 侧边栏新建对话，只发送：

```text
请调用技能：知识星球浏览器侧边栏 PDF 下载。
```

不需要授权码。

### A3. 必须收集的证据

每个 skill 的最终报告至少需要：

- 实际扫描的唯一主题组数，且不超过 40；
- 发现的附件数；
- 新下载数，且不超过 40；
- 历史重复数；
- 低于阈值、超过 48 小时、时间未知的数量；
- `download_count_unavailable_after_2s`；
- `download_button_missing_after_2s`；
- `detail_file_mismatch_after_2s`；
- `downloaded_file_mismatch`；
- `duplicate_candidate_in_same_run`；
- `duplicate_actual_file_in_same_run`；
- 下载失败数；
- `history_complete` 和 `history_ignored_untrusted_manifests`；
- `manifest_path`；
- `task_id`；
- `replayed`；
- 是否已交给清洗与自动发布流程。

### A4. 通过条件

- 最近三日可信历史中的文件没有再次下载；
- 本轮没有同一真实文件的 `(1)/(2)` 副本进入 manifest；
- `new_download_success == files.length`；
- 每条 `filename` 与 `absolute_path` 的真实基名一致；
- 没有同轮重复内容；
- 得到 task_id，或得到同一 task_id 的合法 `replayed=true`；
- 后续清洗/发布成功排队，零文件时也成功建立一次同步编排；
- 侧边栏没有改用 Downloads JSON、curl 或直接 5001。

### A5. 立即停止并调查的条件

- 下载了可信历史中已经存在的文件；
- 出现新的 Chrome `(1)/(2)` 重复并被计入 manifest；
- 候选文件名、弹层文件名和落盘文件名不一致仍继续下载；
- 提交失败后修改 manifest 却复用同一个 run_id；
- 同时出现多个下载在途；
- 历史 `complete=false` 仍继续下载；
- 自动打开新的 Chrome profile 或不断新建 Chrome 窗口。

## Gate B：人工验收通过后的 24/10/5 小流量测试

只有用户明确说 Gate A 通过后才执行：

1. 同时修改项目 MP3/PDF skill：48 小时改为 24 小时；40 个主题组改为 10；最大 40 个下载改为 5。
2. 不改阈值：MP3 仍严格大于 10，PDF 仍严格大于 30。
3. 不改历史窗口：仍读取最近 3 个自然日。
4. 将两个项目 skill 同步到 `/Users/v9/.codex/skills/`。
5. 运行技能契约测试，并逐字节比较项目与安装副本。
6. 仍保持 `mp3-2` 暂停，由用户各运行一次 MP3 和 PDF。
7. 使用与 Gate A 相同的证据和通过标准，另确认扫描不超过 10、下载不超过 5、窗口为 24 小时。

## Gate C：恢复正式参数并接入计划任务

只有 Gate B 明确通过后才执行：

1. 把两个 skill 恢复为 48 小时、40 个主题组、最多 40 个下载。
2. 再次同步项目和安装副本并验证哈希一致。
3. 重新运行技能契约和关键后端回归。
4. 更新/检查 `mp3-2` 的提示词与 skill 当前契约一致。
5. 确认计划任务只复用现有 `vimson5000` Chrome 窗口，不为每轮新建窗口；如果找不到唯一目标窗口，则本轮停止并通知，不得猜测。
6. 恢复每 4 小时执行一次。当前计划为每天 `00:00、04:00、08:00、12:00、16:00、20:00`。
7. 人工观察第一轮计划任务：窗口数量、页面定位、MP3/PDF 两轮结果、manifest、task_id 和下游状态。
8. 第一轮计划任务通过后，才能宣布进入持续运行。

## 失败处理与回滚

### 侧边栏业务失败

- 记录准确失败码与当前 manifest 状态。
- 不重新调用整个 skill，避免再次扫描和下载。
- 若提交结果未知，只能按同一 run_id、完全相同的冻结 manifest 重试一次。

### 桥接、登录或 profile 失败

- 停止本轮，不调用下载 skill。
- 保持计划任务暂停或立即重新暂停。
- 不切换到其他 Chrome profile，不要求扫码登录一个新 profile。

### 新增重复或错误绑定

- 立即暂停自动化。
- 保存侧边栏最终报告、manifest 路径、浏览器下载完成记录和 task_id。
- 不删除原文件，不手工修改已接收 manifest。
- 先核对数据库接收凭证和可信历史，再决定是否构建新的恢复 manifest。

### 计划任务重复新建窗口

- 暂停 `mp3-2`。
- 关闭多余窗口前先确认它们没有正在运行的侧边栏任务。
- 修复前置定位逻辑为“查找并复用唯一现有目标窗口”；找不到或找到多个无法判定时 fail closed。

## 给新会话的当前下一步

除非用户已经在新会话中提供 Gate A 的两份最终报告，否则下一步只能是：

1. 核对 Gate 0；
2. 请用户按 A1/A2 手动测试；
3. 分析两份最终报告；
4. 由用户明确确认是否通过。

不得自行跳到 24/10/5 或恢复计划任务。
