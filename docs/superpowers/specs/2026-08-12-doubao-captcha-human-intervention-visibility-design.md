# 豆包人机验证人工接管与通知可见性设计

## 背景与根因

豆包浏览器朗读遇到验证码时已经会把子任务终止为 `blocked_by_captcha`，并通过工作流通知服务发送邮件。现场任务 `bf552469-b7af-496e-aecd-39a885c32357` 的邮件发送结果也是成功的。当前故障体验来自两个缺口：

1. 邮件只有浏览器页面和通用步骤，没有父工作流直达链接，处理人仍需自行定位任务。
2. 子任务的 `result.notifications.doubao_captcha_blocked` 没有进入列表和状态接口的轻量投影，前端只能显示“人机验证阻塞”，无法告诉用户邮件是否已经发出、是否发送失败以及下一步如何操作。

验证码属于第三方交互式安全机制，程序无法可靠且合规地自动完成。系统正确的恢复边界是停止自动重试、保留现场、通知人处理，并在人工验证完成后由人明确触发父工作流续跑。

## 方案选择

采用“可操作邮件 + 结构化人工接管状态 + 前端可见反馈”的方案。

- 仅发邮件：改动最少，但邮件丢失、配置失败或冷却去重时页面仍像没有动作，不能解决可观测性根因。
- 自动识别或绕过验证码：不稳定，会持续触发风控，也突破了系统能安全自愈的边界，不采用。
- 推荐方案：验证码仍是终止型人工阻塞；邮件附父工作流链接、浏览器 Profile、页面、截图、分段和三步处理方法；后端只投影所需 JSON 分支；列表卡片、子任务弹窗和工作流详情页显示通知结果及续跑入口。

## 状态与数据契约

子任务保持现有终态：

- `status = blocked_by_captcha`
- `current_step = blocked_by_captcha`
- 不再自动重试，防止重复触发第三方风控。

`result.captcha_blocked` 增补：

```json
{
  "blocked": true,
  "reason": "captcha",
  "requires_human_action": true,
  "parent_task_id": "<父工作流 ID>",
  "workflow_url": "http://localhost:5001/report-audio-workflow-detail.html?job_id=<父工作流 ID>",
  "profile": "<浏览器 Profile>",
  "worker_id": "<Worker>",
  "page_url": "<被阻塞页面>",
  "screenshot_path": "<现场截图>",
  "resolution_steps": [
    "使用通知中指定的浏览器 Profile 打开豆包页面",
    "完成验证码或安全验证，并确认页面可以正常发起朗读",
    "返回工作流详情页，点击“从失败处续跑”"
  ]
}
```

`result.notifications.doubao_captcha_blocked` 继续保存邮件结果。公开接口只暴露处理状态所需字段：`sent`、`reason`、`sent_at`、`retry_after_seconds`、`event_type`、`error`、`missing`，以及脱敏后的 `recipient_hint`；不向列表接口暴露完整收件地址。

通知状态语义：

- `sent=true`：已发送邮件。
- `reason=cooldown`：同一浏览器环境在冷却期内已有告警，本次被去重。
- `reason=missing_config`：邮件配置不完整。
- `reason=disabled` 或 `unsupported_provider:*`：通知未启用。
- `reason=send_failed`：邮件发送异常。
- 没有通知记录：邮件动作尚未产生可见结果。

## 后端数据流

1. 豆包 Worker 发现验证码，抛出 `DoubaoBrowserTTSCaptchaError`。
2. 任务保存人工接管上下文并提交 `blocked_by_captcha`。
3. 重新加载完整子任务负载，构造父工作流直达链接和处理步骤，调用去重邮件服务。
4. 不论发送成功、冷却、缺配置或异常，都把结果持久化到子任务通知分支。
5. 父编排的公开摘要保留人工接管和通知摘要。
6. 报告音频 API 的轻量子任务查询只增加 `captcha_blocked` 与单个通知事件两个 JSON 投影，不读取完整 `result`，避免恢复列表接口的 JSON 大字段负担。

直达链接基址按顺序读取：`REPORT_AUDIO_WORKFLOW_FRONTEND_BASE_URL`、`REPORT_WORKFLOW_FRONTEND_BASE_URL`、`FRONTEND_BASE_URL`，默认 `http://localhost:5001`。

## 前端行为

增加一个无 DOM 依赖的共享解析器，把后端字段归一为统一人工接管视图模型。两个页面复用它：

- 工作流列表：被阻塞的豆包子任务卡片显示“已邮件通知 / 冷却期已通知 / 未配置 / 发送失败 / 等待结果”；点击子任务后，弹窗展示处理步骤、豆包页面和父工作流入口。
- 工作流详情：顶部错误区下方展示人工接管面板；用户完成验证后使用已有“从失败处续跑”按钮，不新增绕过验证码或自动恢复按钮。

所有外部值在插入 HTML 前继续经过页面现有转义方法。完整邮箱地址不渲染，只显示脱敏提示。

## 错误处理与幂等

- 邮件发送失败不能覆盖原始验证码错误，也不能让任务退出人工阻塞状态。
- 邮件服务异常转换为 `sent=false, reason=send_failed` 并持久化，页面明确提示检查配置或日志。
- 已发送事件保持现有任务级幂等；跨任务同 Profile 继续由冷却文件去重。
- 人工验证后只续跑父工作流；已完成的文案和朗读分段由现有重用逻辑保留。

## 验收与测试

- 后端单元测试证明直达链接、结构化解决步骤、公开通知脱敏以及轻量 API 投影。
- Node 行为测试直接加载共享解析器，覆盖发送成功、冷却去重、缺配置和无关成功任务。
- 定向运行相关 Python/Node 测试，再运行完整 `tests/unit`、JavaScript 语法检查、Python 编译检查和 `git diff --check`。
- 不修改现场历史任务、不补发邮件、不重启服务；代码部署或本地服务重启后新逻辑生效。

