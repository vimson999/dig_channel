# 知识星球侧边栏下载与本地编排交接入口

最后更新：2026-08-19

## 一句话状态

PDF 正式 Gate A 已通过。2026-08-19 13:47 的最新 MP3 正式复验在第 41 个候选上因外层 90 秒先于 51.5 MB 文件完整落盘而触发 `kernel reset`；报告确认 1 个、实际落盘 2 个，均非历史重复，2 文件 Gate R 已提交。MP3/PDF 已部署 180/210 秒受控期限，并将异常边界改为“单候选隔离，已确认文件照常提交”：单候选超时、缺路径/文件名或可隔离事件冲突跳过后继续；跨候选污染或在途连接重置才停止后续点击，且不丢弃已成功文件。仍需再执行一次全新的 MP3 正式 Gate A，`mp3-2` 继续暂停。

## 当前门禁

| 项目 | 当前状态 |
| --- | --- |
| 后端去重与 manifest 校验 | 已实现并通过测试 |
| MP3/PDF 项目 skill | 已加入可审计下载事务、单候选异常隔离、已确认文件继续提交、“一候选一浏览器调用”副作用屏障和审计来源冻结规则 |
| 本机安装的 MP3/PDF skill | 已与项目副本逐字节一致；MP3 `33ed54…`，PDF `c56dd5…` |
| 后端服务 | 11:16 仅重启 5001 并关闭 bootstrap 自动启动；5001 与 8888 均 healthy |
| 失败批次的 6 个有效 MP3 恢复 | 已入库，清洗和发布均完成 |
| 第一次正式参数人工验收 | **已执行、未通过**；真实落盘 2 MP3 + 8 PDF，均无 manifest/task |
| Gate R 只读核对 | 已完成；8 个可恢复、2 个不合规并从 `files` 排除 |
| Gate R 首批恢复提交 | **已完成**；两份 submission、两条清洗与两条自动发布均成功，可信历史命中 8/8 |
| 排除项重查 | MP3 仍不合规；平安银行 PDF 已变为 31 次下载且可下载 |
| Gate R 补充恢复 | **已完成**；单文件已入库、清洗、自动发布，并命中可信历史 1/1 |
| 8888 提交桥接 | 已修复 3 秒误超时；提交使用独立 30 秒等待预算，桥接回归 20 项通过 |
| 修复技术复验 | **已完成**；MP3/PDF 均用 24/10/5 通过，未决与意外下载均为 0 |
| 点击后不完整回执修复 | **已完成并部署**；单候选不确定结果隔离后继续，跨候选污染才停止后续点击；已确认 `files` 仍提交，明确终态的单文件错误跳过后继续 |
| 第二批 Gate R（17 个 MP3） | **已完成**；submission `57e865fe…`、browser task `10783685…`，清洗/发布 17/17、失败 0，可信历史命中 17/17 |
| 下载事件适配修复 | **已完成**；使用同一 `PlaywrightDownload` 对象的 `download.path()` 绑定真实路径，filename 取该路径 basename；原适配相关回归 79 项/103 子检查通过，当前技能契约共 12 项通过 |
| 2026-08-19 PDF 正式复验 | **已通过**；40/40 下载、入库、清洗、发布、回查及可信历史均通过，失败/待确认/重复均为 0 |
| 2026-08-19 MP3 正式复验 | **未通过**；报告确认 8 个，实际落盘 22 个；连接重置后仍有 14 个继续落盘，无 manifest/task |
| 第三批 Gate R（22 个 MP3） | **已完成**；submission `bea02e59…`、workflow `ff0268af…`，入库/清洗/发布/回查及可信历史均 22/22，失败/待确认 0 |
| 副作用屏障修复 | **已部署**；一次浏览器工具调用最多处理 1 个候选，必须回到会话完成账本检查后才能处理下一个；技能契约 12 项通过 |
| 10:46 MP3 新轮次 | 下载/终态 2/2、下游 2/2，无重复或游离；但 3 条候选失败中 2 条详情错配被汇总为 0，历史统计也被错误重算，未批准 |
| 审计来源门禁修复 | **已部署**；失败计数从 `candidate_failures` 派生、历史四项复制 `data.summary`；真实 API 自测对矛盾值返回 HTTP 400；相关回归 82 项/113 subtests 通过 |
| 13:47 MP3 新轮次 | **未通过**；第 41 个候选的 51.5 MB 文件约 115 秒完成，外层 90 秒先触发 `kernel reset`；实际新增 2 个、历史命中 0 |
| 第四批 Gate R（2 个 MP3） | **已提交**；submission `f7c3e5f5…`、browser task `d465c2af…`，预检 2/2、重复/未知/warning 均为 0 |
| 下载完成期限修复 | **已部署**；事务期限 180 秒、外层工具至少 210 秒，`download.path()` 纳入 `Promise.race`；技能契约 14 项/92 subtests 通过 |
| 单候选失败与部分成功提交 | **已部署**；旧技能的“一项异常阻断整批 manifest”已移除，后端现有 manifest 接口可直接接收成功 `files` 与 `candidate_failures` 的混合结果；候选级超时/缺路径/可隔离事件也按跳过后继续处理；契约/后端回归 51 项通过 |
| 正式 Gate A 验收 | **PDF 子门禁通过，整体尚未通过**；当前需在新规则下再执行一次全新的 MP3 正式复验 |
| 每日三次计划任务 | `mp3-2` 已改为北京时间 `11:30、16:00、23:30`，当前仍为 `PAUSED` |

当前正式参数为 `36/30/40`：回溯 36 小时、最多扫描 30 个唯一主题组、最多成功下载 40 个新文件。MP3/PDF 下载次数阈值仍分别为严格大于 10 和严格大于 30，最近 3 个自然日历史窗口不变。

## 阅读顺序

1. [01-现状、事故与根因](01-current-state-and-incident.md)
2. [02-目标架构与修复方案](02-target-architecture-and-solution.md)
3. [03-实现、验证与恢复证据](03-implementation-validation-and-recovery.md)
4. [04-人工验收与上线运行手册](04-acceptance-and-rollout-runbook.md)

更底层的设计与逐项计划仍保留在：

- [设计说明](../../superpowers/specs/2026-08-18-zsxq-sidebar-dedup-and-manifest-integrity.md)
- [实施计划](../../superpowers/plans/2026-08-18-zsxq-sidebar-dedup-and-manifest-integrity.md)

## 新会话的建议首条消息

```text
请先完整阅读：
docs/handoffs/zsxq-sidebar-automation/README.md
以及其中链接的 01～04 文档。

这是知识星球 Chrome ChatGPT 侧边栏 MP3/PDF 下载、本地导入、清洗发布和计划任务的续接工作。
PDF 正式 Gate A 已通过。13:47 最新 MP3 正式复验在第 41 个候选上触发 js execution timed out / kernel reset：51.5 MB 文件约 115 秒才完成，外层工具仅给 90 秒；报告确认 1 个、实际落盘 2 个，均非历史重复，2 文件恢复清单已提交。MP3/PDF 已部署 180 秒内层事务期限和至少 210 秒外层工具期限，项目/安装副本一致，计划任务 mp3-2 仍暂停。不要重跑 PDF；下一步等待本批下游完成后，再执行一次全新 MP3 正式 Gate A。
不要重新设计既有方案，不要再次运行已通过的 PDF，也不要恢复计划任务。下一步在新侧边栏对话再执行一次全新的 MP3 正式 Gate A；只有该轮明确通过后，才评估启用计划任务。
```

## 接手时绝对不要做

- 不要把当前阶段误判为“已经上线”；PDF 子门禁已通过，但 MP3 正式 Gate A 仍未通过。
- 不要把剩余 MP3 当成已经进入可信历史；它当前受内容保护且无下载按钮。平安银行 PDF 的现有落盘文件已经通过单文件补充恢复进入可信历史，下一轮 skill 不能再次下载它。
- 不要把侧边栏报告的“MP3 1 个、PDF 1 个”当作实际落盘数；文件系统证据为 MP3 2 个、PDF 8 个。
- 不要删除、改名或移动正式 MP3 轮次留下的 17 个文件；它们已经通过第二批 Gate R 进入可信历史，下一轮不能再次下载。
- 2026-08-19 09:35～09:46 实际落盘的 22 个 MP3 已完成第三批 Gate R 并进入可信历史；新的 MP3 正式复验不得再次下载它们。
- 当前在审计来源门禁修复后再运行一次 MP3 正式复验；PDF 已通过，不要再次运行 PDF；计划任务仍不得恢复。
- 不要让 Codex 直接点击知识星球附件；实际扫描和下载必须由 Chrome ChatGPT 侧边栏 skill 完成。
- 不要让 skill 自己切换 Chrome profile、窗口或标签页。
- 不要从 Downloads 中的旧 JSON 直接去重；历史以本地 HTML 桥接返回的后端可信历史为准。
- 不要在提交结果未知时重新扫描和下载；只能用同一 run_id、完全相同的冻结 manifest 幂等重试一次。
- 不要因为工作区存在其他未提交改动而执行 `git add -A`；这些无关改动属于其他工作。

## 关键位置

- 项目目录：`/Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel`
- 当前分支：`agent/pdf-media-workflow-reliability`
- 远端：`https://gitee.com/vim999/dig_ytb_channel.git`
- 当前 HEAD/远端：`be3d0f6`；第二层下载事务修复仍是本地未提交改动
- 既有相关提交：`d779c2f`、`6855d4d`、`fcac0e2`、`be3d0f6`
- 计划任务配置：`/Users/v9/.codex/automations/mp3-2/automation.toml`
- 下载与 manifest 目录：`/Users/v9/Downloads/知识星球下载/inbox/`
- MP3 项目 skill：`plugins/zsxq-sidebar-sync/skills/zsxq-mp3-sidebar-download/SKILL.md`
- PDF 项目 skill：`plugins/zsxq-sidebar-sync/skills/zsxq-pdf-sidebar-download/SKILL.md`
- MP3 本机 skill：`/Users/v9/.codex/skills/zsxq-mp3-sidebar-download/SKILL.md`
- PDF 本机 skill：`/Users/v9/.codex/skills/zsxq-pdf-sidebar-download/SKILL.md`

当前项目/安装 skill SHA-256：

- MP3：`a3b6563f468058e0c1a54db7320ba6f487849d149a6eee72b2b4041795c12e64`
- PDF：`29c694382cf30a7bd8e7a53c5a774119d8626c8b7d0e573329a0e6510880019e`
