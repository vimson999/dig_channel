# 人工验收与上线运行手册

## 当前阶段

PDF 正式 Gate A 已通过。2026-08-19 13:47 的最新 MP3 新轮次因 51.5 MB 文件约 115 秒完成、外层浏览器工具仅给 90 秒而触发 `kernel reset`；报告确认 1 个、实际落盘 2 个，均非历史重复，2 文件恢复清单已提交。受控下载完成期限修复已经部署；等待本批下游完成后再执行一次全新 MP3 正式 Gate A，计划任务继续暂停。

## Gate A 第一轮结果：未通过

- MP3：报告扫描 40 个主题、发现 108 个 MP3、确认 1 个；实际落盘 2 个。无 armed 候选的意外下载发生后仍继续处理了下一个候选。
- PDF：报告扫描 40 个主题、发现 207 个 PDF、确认 1 个；实际落盘 8 个。连接重置时存在无法核对的点击/完成事件。
- 两轮都没有 manifest 或 task_id；数据库没有相应 browser submission/sync run。
- 这不是 Gate A 通过，也不是重新调用 skill 的理由。先按 03 文档的完整清单完成 Gate R。

## Gate 0：验收前检查

1. 确认后端 `5001/health` 与 8888 `/bridge/zsxq/health` 均健康。
2. 确认项目 MP3/PDF skill 与 `/Users/v9/.codex/skills/` 安装副本一致；当前预期 SHA-256：MP3 `33ed5436183a6b83ed5289d3a58c402b90d0109aaa64902149deee83da880395`，PDF `c56dd566251551ef5c0377a67625b3cfa54a87c25aa96bd40464cc33bf50e9fc`。
3. 确认两个 skill 当前均为正式 Gate A 参数 36/30/40；`mp3-2` 在正式 Gate A 通过前保持暂停。
4. 确认自动化 `/Users/v9/.codex/automations/mp3-2/automation.toml` 中为 `status = "PAUSED"`。
5. Chrome 使用已登录知识星球的 `vimson5000` profile。
6. 尽量只保留一个目标 vimson5000 Chrome 窗口，避免旧计划任务遗留的多个同 profile 窗口造成定位歧义。
7. 不要用 Codex 直接下载文件；本阶段由用户在侧边栏主动调用两个 skill。

## Gate R：游离文件核对与恢复

1. 以 03 文档中的 10 条 `absolute_path`、文件大小和 SHA-256 为冻结清单；不删除、不改名、不移动。
2. 在正确的知识星球标签页只读定位同名附件，逐条核对候选身份、`download_count`、`published_at`、`topic_title` 和 `source_url`；不得点击“下载文件”。
3. 无法唯一核对的条目继续保留为游离文件，不得猜测或进入 manifest。
4. 可唯一核对的条目分别构造全新的 MP3/PDF recovery manifest；每条必须绑定实际路径、真实基名和 SHA-256，并通过现有完整性校验。
5. 恢复提交会排队入库、清洗和自动发布。执行前向用户展示最终两份清单并取得明确确认；不得因为“解决测试问题”而默认为已授权发布。
6. 步骤 1～5 已完成：8 个合规文件进入冻结清单并经用户明确授权提交；下载数仅为 10 的 MP3，以及内容保护且无可用下载数/按钮的 PDF，均留在 `recovery_exclusions`，没有进入 `files`。
7. 两条 submission、sync、clean、publish 均 `completed`，可信三日历史确认 8 个合规规范化文件名全部命中；完整 ID 见 03 文档 5.4。
8. Gate A 复验前已再次只读检查 2 个排除项：MP3 仍受内容保护且无下载按钮；平安银行 PDF 已变为 31 次下载且可下载。
9. 平安银行 PDF 的单文件补充 recovery 已单独获用户授权并完成：submission、同步、清洗、发布均 `completed`，可信历史命中 1/1；完整回执见 03 文档 5.6。
10. 首批 Gate R 已完成；24/10/5 修复技术复验现已完成，48/40/40 已按用户确认恢复。
11. 正式 MP3 轮次新增的 17 个游离 MP3 构成第二批 Gate R；文件名、大小、SHA 和下载次数的冻结清单见 03 文档 5.11，全程没有删除、改名、移动或再次下载。
12. 第二批已在正确标签页只读补齐 `published_at`、`topic_title`、`source_url`，恢复草案经用户明确确认后提交；submission、清洗、发布均完成，可信历史命中 17/17，完整回执见 03 文档 5.12。
13. `长江大金融… (1).mp3` 与旧无后缀文件同 SHA；恢复清单只能选择一份内容，另一份列入排除项，不得双重发布。
14. 第二批 submission、sync、clean、publish 与可信历史已经全部核对通过，可以进入下节正式 Gate A 复验。
15. 2026-08-19 09:28 的 MP3 正式复验构成第三批 Gate R：报告确认 8 个，但 09:35:08～09:46:14 实际落盘 22 个；22 个 SHA 互不重复，可信历史名称/SHA 命中均为 0/22。
16. 第三批恢复草案为 `docs/handoffs/zsxq-sidebar-automation/gate-a-20260819-mp3-connection-reset-recovery-draft.json`，run_id 为 `sidebar-mp3-20260819T100543+0800-recovery-connection-reset-22`；页面只读核对和后端纯内存预检均为 22/22 有效，无重复、未知下载次数或 warning。
17. 用户明确确认后，第三批只提交原冻结清单一次；submission、sync、clean、publish 均 `completed`，3 帖上传/提交/回查 22/22，失败/待确认 0，可信历史命中 22/22。完整 ID 见 03 文档 5.14，现在可以进入 A1。

## 当前手动复验：正式 Gate A（36/30/40）

技术复验已完成。本轮执行完整业务范围的正式 Gate A：两个 skill 当前为回溯 36 小时、最多扫描 30 组主题、最多成功下载 40 个文件；阈值和最近 3 天历史去重规则不变。

状态：**PDF 正式 Gate A 已批准；MP3 的下载副作用屏障已通过现场验证，但最新一轮审计证据不自洽，门禁修复后仍需一次新复验**。不要再次运行 PDF；现在按 A1 新开对话执行一次全新的 MP3 正式复验。

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
- 低于阈值、超过 36 小时、时间未知的数量；
- `download_count_unavailable_after_2s`；
- `download_button_missing_after_2s`；
- `download_metadata_capability_unavailable`；
- `detail_file_mismatch_after_2s`；
- `downloaded_file_mismatch`；
- `duplicate_candidate_in_same_run`；
- `duplicate_actual_file_in_same_run`；
- `download_clicks_issued`；
- `download_events_confirmed`；
- `download_result_incomplete_after_retry`；
- `unresolved_download_count`；
- `unexpected_download_without_armed_candidate`；
- `browser_connection_reset_with_inflight_download`；
- `abort_latch`；
- `manifest_blocked_by_download_integrity`；
- 下载失败数；
- `history_complete` 和 `history_ignored_untrusted_manifests`；
- `history_records_seen`、`history_unique_files`、`history_duplicates_collapsed`，并确认逐字段来自同一次历史页 `data.summary`；
- `candidate_failures` 完整数组、`candidate_failure_count`，以及每个 `failure_stage` 对应的汇总计数；
- `manifest_path`；
- `task_id`；
- `replayed`；
- 是否已交给清洗与自动发布流程。

### A4. 通过条件

- 最近三日可信历史中的文件没有再次下载；
- 本轮没有同一真实文件的 `(1)/(2)` 副本进入 manifest，也没有任何实际落盘的旧游离文件被再次下载；
- `new_download_success == files.length`；
- `candidate_failure_count == candidate_failures.length`，且每个失败阶段计数等于数组中同名 `failure_stage` 的条目数；
- 明确终态的单候选失败被记录并跳过，不会中断其他候选；
- 历史四项审计字段逐字段等于下载前冻结的历史页 `data.summary`，不得把 `data.files.length` 当成记录数或折叠重复数；
- `download_clicks_issued`、`download_events_confirmed` 与 `unresolved_download_count` 的原始账本被如实报告，不补写虚假终态；
- 如果存在 `download_result_incomplete_after_retry`，当前候选不进 `files`，隔离后继续下一个；若存在无 armed 候选事件、迟到事件、在途连接重置或无法证明候选隔离的跨候选污染，后续点击才停止；此前已确认文件仍已提交；
- `manifest_blocked_by_download_integrity == false`；
- 每条 `filename` 与 `absolute_path` 的真实基名一致；
- 没有同轮重复内容；
- 得到 task_id，或得到同一 task_id 的合法 `replayed=true`；
- 后续清洗/发布成功排队，零文件时也成功建立一次同步编排；
- 侧边栏没有改用 Downloads JSON、curl 或直接 5001。

### A5. 立即停止并调查的条件

- 下载了可信历史中已经存在的文件；
- 已在可信历史中、Downloads 中已有未受信游离文件，或同一轮已存在规范化同名/同内容文件，却又出现新的 Chrome `(1)/(2)` 副本或实际落盘；
- 候选文件名、弹层文件名和落盘文件名不一致，却仍把该文件计为成功并放入 `files`；
- 提交失败后修改 manifest 却复用同一个 run_id；
- 同时出现多个下载在途；
- 下载完成监听器未确认 active 就点击；
- 第一次点击前无法建立 `waitForEvent("download")` 与同一下载对象 `download.path()` 的适配契约，却用真实下载试探能力；
- 跨候选污染、无 armed 候选事件、迟到事件或在途连接重置后仍继续下一个候选；单候选终态超时、缺路径/文件名或可隔离事件冲突跳过后继续是允许行为；
- 没有唯一 armed 候选却出现下载事件；
- 浏览器连接重置时存在在途点击，但没有隔离当前候选，或因此丢弃此前已确认文件；
- 点击数、确认终态数和未决数的账本差异被隐瞒、补写或伪造；
- 侧边栏确认/报告的下载数小于文件系统在本轮冻结时间窗内的实际新增数；
- 同一次浏览器工具或 JavaScript 调用通过循环、批处理或递归处理多个候选；
- 单候选调用的外层工具超时不大于内层下载事务期限，或由外层先触发 `kernel reset`；
- `abort_latch=true` 后仍继续扫描或点击；
- `manifest_blocked_by_download_integrity=true` 仍提交部分文件或伪装成 0 文件同步；
- 历史 `complete=false` 仍继续下载；
- `candidate_failures` 与失败阶段汇总计数不一致仍提交；
- `history_records_seen` 或 `history_duplicates_collapsed` 由 `data.files.length` 重算，或与历史页 `data.summary` 不一致；
- 自动打开新的 Chrome profile 或不断新建 Chrome 窗口。

## Gate B：正式 Gate A（36/30/40）

用户已明确决定恢复正式参数，项目与本机 skill 已同步；现在执行：

1. 确认项目 MP3/PDF skill 均为回溯 36 小时、最多 30 个主题组、最多 40 个下载。
2. 不改阈值：MP3 仍严格大于 10，PDF 仍严格大于 30。
3. 不改历史窗口：仍读取最近 3 个自然日。
4. 项目与本机 skill 已同步；运行技能契约测试，并逐字节比较项目与安装副本。
5. 仍保持 `mp3-2` 暂停，由用户各运行一次 MP3 和 PDF。
6. 使用与当前技术复验相同的证据和通过标准，另确认扫描不超过 30、下载不超过 40、窗口为 36 小时。

## Gate C：恢复正式参数并接入计划任务

只有正式 Gate A 明确通过后才执行：

1. 确认两个 skill 保持为 36 小时、30 个主题组、最多 40 个下载。
2. 再次同步项目和安装副本并验证哈希一致。
3. 重新运行技能契约和关键后端回归。
4. 更新/检查 `mp3-2` 的提示词与 skill 当前契约一致。
5. 确认计划任务只复用现有 `vimson5000` Chrome 窗口，不为每轮新建窗口；如果找不到唯一目标窗口，则本轮停止并通知，不得猜测。
6. `mp3-2` 的调度已在暂停状态下预配置为每天北京时间 `11:30、16:00、23:30`；两轮正式 Gate A 修复复验均通过后才可将状态从 `PAUSED` 改为 `ACTIVE`，不得恢复旧的每 4 小时频率。
7. 人工观察第一轮计划任务：窗口数量、页面定位、MP3/PDF 两轮结果、manifest、task_id 和下游状态。
8. 第一轮计划任务通过后，才能宣布进入持续运行。

## 失败处理与回滚

### 侧边栏业务失败

- 记录准确失败码与当前 manifest 状态。
- 不重新调用整个 skill，避免再次扫描和下载。
- 核对本轮 `download_click_ledger` 与实际落盘；如果存在游离文件，先建立冻结清单并进入 Gate R，不能直接重跑。
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

当前下一步固定为：

1. Gate 0、首批 8 个合规文件及平安银行 PDF 单文件补充恢复的入库/清洗/发布与可信历史复核均已完成；
2. 排除项重查已完成：MP3 仍不合规，平安银行 PDF 已变为合规；
3. MP3/PDF 已按 A1/A2 完成 24/10/5 手动技术复验，且回执、工作流和可信历史均已核对；
4. 用户已确认调整为 36/30/40，项目与安装 skill 已同步；
5. PDF 与 MP3 正式轮次均已暴露下载回执/重复副本问题；第二层根因修复已部署并通过相关回归；
6. 第二批 17 个 MP3 已按用户确认完成提交，清洗/发布 17/17，可信历史命中 17/17；
7. PDF 0-click 能力预检误判已修复，A2 正式复验已通过；2026-08-19 09:28 的 MP3 A1 因连接重置失败，实际新增 22 个而报告只确认 8 个；
8. 第三批 22 个 MP3 已按用户确认完成原冻结清单提交；入库、清洗、发布、回查及可信历史均 22/22，失败/待确认 0；
9. 10:46 MP3 新轮次下载/终态 2/2、下游 2/2，证明没有再次出现连接失控或重复落盘；但失败阶段与历史审计字段矛盾，所以未批准；
10. 审计来源门禁已修复并部署，相关回归 `82 passed, 113 subtests passed`，真实回环 API 对矛盾计数返回 HTTP 400；
11. 13:47 MP3 轮次已确认外层 90 秒早于 51.5 MB 文件约 115 秒的完整落盘；两个实际文件的第四批 Gate R 已提交，180/210 秒内外层期限修复已部署且技能契约 14 项/92 subtests 通过。
12. 已将单候选异常改为可控处理：明确终态失败、单候选终态超时、缺路径/文件名或可隔离事件冲突均跳过后继续；只有跨候选污染或全局故障才停止后续点击，已确认文件仍提交。契约/后端回归 51 项通过，项目与安装副本已同步。
13. 按 A1 再新开侧边栏对话执行一次全新 MP3 正式复验。只有该轮无明显重大逻辑漏洞，且已确认文件全部正常提交，才进入计划任务恢复流程。

不得在正式 Gate A 明确通过前恢复计划任务。单个可控候选异常不等于 Gate A 失败；只有出现已确认文件被丢弃、未决候选串扰后续文件、历史重复仍被下载或实际 `files` 无法通过完整性自检等明显重大逻辑漏洞时，才中止验收并进入 Gate R/失败处理。
