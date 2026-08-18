# 知识星球侧边栏下载与本地编排交接入口

最后更新：2026-08-18

## 一句话状态

MP3/PDF 重复下载、错误文件绑定、失败 manifest 污染历史以及 run_id 被失败请求占用的问题已经完成代码修复、回归验证和远端推送；现在停在**正式参数人工验收**阶段。计划任务 `mp3-2` 仍为 `PAUSED`，不得提前恢复。

## 当前门禁

| 项目 | 当前状态 |
| --- | --- |
| 后端去重与 manifest 校验 | 已实现并通过测试 |
| MP3/PDF 项目 skill | 已更新 |
| 本机安装的 MP3/PDF skill | 已与项目副本逐字节一致 |
| 后端服务 | 已重启，`127.0.0.1:5001/health` 为 healthy |
| 失败批次的 6 个有效 MP3 恢复 | 已入库，清洗和发布均完成 |
| 第一次人工验收 | **待用户执行**，使用正式参数 48/40/40 |
| 临时小流量测试 | 尚未开始；人工验收通过后才切换到 24/10/5 |
| 四小时计划任务 | `mp3-2` 已暂停，尚未恢复 |

这里的参数 `48/40/40` 依次表示：回溯 48 小时、最多扫描 40 个唯一主题组、最多下载 40 个新文件。`24/10/5` 同理。

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
当前代码修复已完成，计划任务 mp3-2 仍暂停，当前阶段是按 48/40/40 进行 MP3 和 PDF 人工验收。
不要重新设计既有方案，不要直接恢复计划任务，也不要提前切换 24/10/5。请先核对仓库、服务、skill 副本和自动化状态，再继续执行 04 文档中的当前门禁。
```

## 接手时绝对不要做

- 不要把当前阶段误判为“已经上线”；人工验收与小流量测试尚未完成。
- 不要在第一次人工验收前把 skill 改成 24/10/5。
- 不要让 Codex 直接点击知识星球附件；实际扫描和下载必须由 Chrome ChatGPT 侧边栏 skill 完成。
- 不要让 skill 自己切换 Chrome profile、窗口或标签页。
- 不要从 Downloads 中的旧 JSON 直接去重；历史以本地 HTML 桥接返回的后端可信历史为准。
- 不要在提交结果未知时重新扫描和下载；只能用同一 run_id、完全相同的冻结 manifest 幂等重试一次。
- 不要因为工作区存在其他未提交改动而执行 `git add -A`；这些无关改动属于其他工作。

## 关键位置

- 项目目录：`/Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel`
- 当前分支：`agent/pdf-media-workflow-reliability`
- 远端：`https://gitee.com/vim999/dig_ytb_channel.git`
- 最新相关提交：`6855d4d`、`fcac0e2`
- 计划任务配置：`/Users/v9/.codex/automations/mp3-2/automation.toml`
- 下载与 manifest 目录：`/Users/v9/Downloads/知识星球下载/inbox/`
- MP3 项目 skill：`plugins/zsxq-sidebar-sync/skills/zsxq-mp3-sidebar-download/SKILL.md`
- PDF 项目 skill：`plugins/zsxq-sidebar-sync/skills/zsxq-pdf-sidebar-download/SKILL.md`
- MP3 本机 skill：`/Users/v9/.codex/skills/zsxq-mp3-sidebar-download/SKILL.md`
- PDF 本机 skill：`/Users/v9/.codex/skills/zsxq-pdf-sidebar-download/SKILL.md`
