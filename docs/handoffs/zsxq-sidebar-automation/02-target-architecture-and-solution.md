# 目标架构与修复方案

## 1. 目标

目标不是替换原有知识星球 API 同步方式，而是增加一个可靠的浏览器侧边栏上游，使它导入后与旧上游产生相同的系统数据，从而继续复用已有的入库、清洗、发布、音频转文字、视频编排等下游能力。

需要同时满足：

- 人可以在正确页面的 ChatGPT 侧边栏手动调用 skill；
- Codex 计划任务可以先定位正确 Chrome profile、窗口和标签页，再唤起同一个侧边栏 skill；
- 两种入口互不冲突，实际下载逻辑始终只有一套；
- 重试不会重复入库或重复创建 Celery 任务；
- 零文件也是一次正常同步，仍产生编排记录和通知；
- 失败时保留可审计信息，但不污染后续去重历史。

## 2. 职责边界

```mermaid
flowchart LR
    A[Codex 或人工] -->|定位正确 profile/标签页并调用| B[Chrome ChatGPT 侧边栏 skill]
    B -->|Web preview 读取| C[8888 HTML 历史桥接]
    C --> D[后端可信历史]
    B -->|严格串行扫描与下载| E[Chrome Downloads]
    B -->|冻结 manifest 后提交| F[8888 HTML 提交桥接]
    F --> G[浏览器导入服务]
    G -->|接收凭证与幂等| H[(数据库)]
    G -->|移动/入库| I[原有知识星球数据模型]
    I --> J[清洗]
    J --> K[自动发布及其他下游]
```

### Codex 或人工入口

- 人工入口：用户先打开准确标签页，再在同一 Chrome 窗口侧边栏发送一句 skill 调用。
- 计划任务入口：Codex 只做桥接预检、识别 `vimson5000` profile、复用现有 Chrome 窗口、定位/刷新目标标签页、打开侧边栏并发送一句调用。
- Codex 不直接扫描知识星球、不点击附件、不生成或提交 manifest。

### 侧边栏 skill

- 只使用当前附着的标签页；不自行创建浏览器上下文，不切换 profile/窗口/标签页，不导航或刷新。
- 通过 Web preview HTML 页读取历史与提交 manifest。
- 根据阈值、时间窗口、主题上限和下载上限完成业务扫描。
- 负责浏览器下载；先建账本并确认完成监听器 active，才发出唯一点击，再确认真实完成事件、路径和文件名。
- 历史页读取成功时冻结 `data.summary` 审计快照；候选结束后只从最终 `candidate_failures` 数组派生失败计数，不使用临时口头汇总。

### 8888 HTML 桥接

这是为侧边栏浏览器能力提供的同源网页入口，避免直接访问 5001 被浏览器策略阻断：

- 健康页：`http://localhost:8888/zsxq-sidebar-bridge.html`
- MP3 历史页：`http://localhost:8888/zsxq-sidebar-history.html?kind=mp3`
- PDF 历史页：`http://localhost:8888/zsxq-sidebar-history.html?kind=pdf`
- MP3 提交页：`http://localhost:8888/zsxq-sidebar-submit.html?kind=mp3`
- PDF 提交页：`http://localhost:8888/zsxq-sidebar-submit.html?kind=pdf`
- 历史/普通请求保留 3 秒快速失败；会预留幂等凭证并派发下游任务的提交使用独立 30 秒等待预算，避免后端已经 202 接收、页面却因过早断开误报不可用。

侧边栏不得改用 curl、终端、`exec_command`、直接端口 5001 或 Downloads 中的历史 JSON。

### 后端

- 返回规范化、完整性明确的三日历史。
- 对新式 manifest 查询数据库接收凭证，过滤失败孤儿文件。
- 在预留 run_id 前完成确定性校验。
- 在持久化和排队前校验 `candidate_failure_count` 及各失败阶段计数与 `candidate_failures` 数组一致。
- 以 run_id/幂等键保证同一冻结 manifest 重放不产生第二个任务。
- 完成文件入库后，进入既有清洗和发布编排。

## 3. 历史去重契约

在固定媒体类型、group_id、tab_id 和最近 3 个自然日范围内，主去重键是：

```text
normalized_filename
```

规范化规则：

1. Unicode NFKC；
2. 删除不可见字符；
3. 折叠多余空白；
4. 拉丁字母不区分大小写；
5. 移除扩展名前 Chrome 自动生成的 ` (1)`、` (2)` 等后缀。

`published_at_canonical` 只用于审计，不参与拆分身份。`source_url` 不参与去重，因为已观察到同一文件 URL 序列化不稳定。只有缺失有效文件名时才退化使用 file_id 或 SHA-256。

历史接口返回：

- `normalized_filename`
- `published_at_canonical`
- `dedupe_key`
- `summary.trusted_source_files`
- `summary.ignored_untrusted_manifests`
- `complete`

当后端无法核验数据库接收凭证时，历史接口必须 fail closed：`complete=false`，skill 不得盲目下载。

审计字段必须直接来自同一次历史响应：

- `history_records_seen = data.summary.records_seen`
- `history_unique_files = data.summary.unique_downloads`
- `history_duplicates_collapsed = data.summary.duplicates_collapsed`
- `history_ignored_untrusted_manifests = data.summary.ignored_untrusted_manifests`

skill 只能用 `data.files` 做去重；不得用 `data.files.length` 重算记录数或折叠重复数。`data.files.length` 只允许与 `summary.unique_downloads` 做一致性核对，不一致则按历史不完整在首次点击前停止。

## 4. 可信 manifest 契约

旧格式历史文件为兼容性继续读取。文件名末尾含 12 位十六进制摘要的新式确定性 manifest，只有数据库 `ZsxqBrowserSubmission.manifest_path` 中存在完全匹配路径时才可信。

因此：

- 后端已接受的 manifest 会进入后续去重；
- 校验失败但残留在 inbox 的孤儿 manifest 会被忽略；
- inbox 文件本身不再等价于“已入库”。

## 5. 候选到落盘文件的绑定状态机

每个候选必须依次通过：

```text
主题卡片 expected filename
  → 当前详情弹层 filename
  → 下载次数与按钮
  → download_click_ledger / ARMED
  → 完成监听器 active
  → 唯一点击 / WAITING_FOR_COMPLETION
  → 唯一下载终态事件 / CONFIRMED
  → actual absolute_path / basename
  → manifest entry
```

正常状态路径固定为：

```text
IDLE → ARMED → WAITING_FOR_COMPLETION → CONFIRMED → IDLE
```

跨候选未绑定事件、点击后在途连接重置、无 armed 候选事件、迟到事件或无法证明候选隔离的事件冲突，才使当前候选进入 `QUARANTINED → ABORTED`；本轮不能清除 latch 或再点击后续候选，但此前已确认文件仍提交。点击前失败、浏览器明确证明下载失败且没有文件落盘，以及已取得唯一终态但实际文件错配/同轮重复的失败可安全跳过当前候选并继续；终态超时、终态缺少真实路径/文件名和限定在当前候选监听代次内的事件冲突，也先隔离当前候选并恢复 `IDLE` 后继续。

关键规则：

1. 第一次下载点击前必须从当前浏览器下载接口/监听器的公开契约证明：成功终态可返回同一事件内的非空 `absolute_path` 和 `filename`。不能用真实下载试探能力；无法证明时在 0 次点击处停止下载并提交 `files: []` 审计。
2. 本轮维护 `seen_candidate_keys`，候选重复则不再打开弹层。
3. 预期名和弹层名第一次缺失/不一致时，保持同一弹层等待至少 2 秒，再读一次。
4. 第二次仍不一致，记录 `detail_file_mismatch_after_2s`，不点击下载。
5. 同时最多一个下载在途；必须先写 `download_click_ledger`，再确认监听器 active，最后才允许点击一次。当前候选未获得唯一终态前，不处理下一个候选。
6. 每轮结束核对 `download_clicks_issued == download_events_confirmed + unresolved_download_count`；缺口全部进入 `unresolved_downloads` 并如实报告，不补写虚假终态。
7. 终态存在但固定重取后仍缺 `absolute_path` 或 `filename` 时记录 `download_result_incomplete_after_retry`，不进入 `files`，隔离当前候选并恢复 `IDLE` 后继续；只有无法证明候选隔离时才停止后续点击。
8. 无 armed 候选却出现下载事件时记录 `unexpected_download_without_armed_candidate`，停止后续点击并隔离未决事件；已确认 `files` 仍提交。
9. 真实文件名规范化后必须等于预期名，否则记录 `downloaded_file_mismatch`，排除当前条目；由于已取得唯一终态和真实路径，安全收尾后继续下一候选。
10. 本轮维护 `downloaded_actual_base_names`；同一实际基名再次出现时记录 `duplicate_actual_file_in_same_run`，排除当前条目后继续。
11. `abort_latch` 与 `manifest_blocked_by_download_integrity` 分离：前者只控制本轮是否继续页面操作/点击；后者只在准备提交的已确认 `files` 本身无法通过路径/文件名、同轮重复或内容重复自检时置位。单候选失败、未决候选或 `abort_latch=true` 不得单独阻止已确认文件提交。
12. 进入 `ABORTED` 后不得补扫、补下载或查看 Downloads 猜对应关系；冻结当前账本和 `files`，提交已确认文件，再将未决候选进入 Gate R。

## 6. manifest 与幂等契约

提交前必须满足：

- `scan_summary.new_download_success == len(files)`；
- `scan_summary.candidate_failure_count == len(candidate_failures)`；
- 每个失败阶段计数等于 `candidate_failures` 中相同 `failure_stage` 的条目数；
- 历史四项审计计数仍与下载前冻结的 `data.summary` 快照逐字段一致；
- `filename == basename(absolute_path)`，按同一规则规范化后仍相等；
- 所有规范化实际基名唯一；
- 文件内容 SHA-256 不重复；
- 每个本地文件存在且位于允许的 Downloads 范围；
- 每条记录已经通过候选、弹层、真实文件三方绑定。
- 下载点击账本数量等式成立；
- `manifest_blocked_by_download_integrity == false`。

完成自检后 manifest 被视为冻结对象。第一次提交后不能增删改字段。

重试规则：

- 明确 4xx/校验失败：本轮失败，不用同一个 run_id 提交修改后的 manifest。
- 结果未知：只允许同一 run_id、完全相同的 manifest 重放一次。
- 后端收到完全相同的重放，返回原 task_id，并标记 `replayed=true`，不得再次派发。

## 7. 当前参数与不可变项

| 项目 | MP3 | PDF |
| --- | ---: | ---: |
| 正式回溯窗口 | 36 小时 | 36 小时 |
| 正式最大主题组 | 30 | 30 |
| 正式最大下载数 | 40 | 40 |
| 临时测试参数 | 24/10/5 | 24/10/5 |
| 下载阈值 | 严格大于 10 | 严格大于 30 |
| 历史窗口 | 最近 3 个自然日 | 最近 3 个自然日 |

阈值和三日历史窗口不在本轮临时参数调整范围内。
