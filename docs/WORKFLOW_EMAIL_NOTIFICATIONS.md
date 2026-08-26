# 工作流邮件通知配置

知识星球端到端工作流支持通过 SMTP 发送里程碑通知。QQ 邮箱推荐配置如下：

```env
WORKFLOW_NOTIFY_PROVIDER=email
WORKFLOW_NOTIFY_LEVEL=milestone
WORKFLOW_NOTIFY_TO=your_receive_email@qq.com

WORKFLOW_EMAIL_SMTP_HOST=smtp.qq.com
WORKFLOW_EMAIL_SMTP_PORT=465
WORKFLOW_EMAIL_USER=your_send_email@qq.com
WORKFLOW_EMAIL_PASSWORD=QQ邮箱SMTP授权码
WORKFLOW_EMAIL_FROM=your_send_email@qq.com
WORKFLOW_EMAIL_FROM_NAME=内容流水线
WORKFLOW_EMAIL_RETRY_ATTEMPTS=3
WORKFLOW_EMAIL_RETRY_DELAY_SECONDS=2
WORKFLOW_EMAIL_FALLBACK_STARTTLS=true

# Gemini / 豆包登录失效或安全验证告警的同状态冷却时间
BROWSER_HUMAN_INTERVENTION_EMAIL_COOLDOWN_SECONDS=1800
```

`WORKFLOW_EMAIL_PASSWORD` 填 QQ 邮箱生成的 SMTP 授权码，不是 QQ 登录密码。

通知级别：

- `milestone`：启动、同步、清洗、发布、整轮结束都通知。
- `summary`：只通知整轮结束。
- `failure_only`：只通知整轮失败。
- `off`：关闭通知。

## 知识星球三种上游的统一编排通知

以下三种入口在进入内容流水线后使用同一套通知节点：

1. 计划任务：调度器触发知识星球同步/下载。
2. 侧边栏：浏览器侧边栏提交 MP3/PDF manifest。
3. 手动投递：直接提交本地下载文件。

直接进入内容流水线的任务会补齐统一的 `workflow_started`、`clean_*` 和
`publish_*` 事件；已经由 `ZsxqWorkflowRun` 管理的计划/侧边栏任务继续由工作流状态机发出相同事件，避免重复邮件。邮件中会标记“上游接入”和内容流水线任务 ID。

清洗成功节点以“规范清洗产物已登记”为准：只有写入并校验过 `cleaned_path` 的文件才会进入发布节点。单个文件清洗失败不会阻断同批其他已登记文件的清洗/发布通知；只有整批没有任何可用清洗产物时，任务才进入失败通知。原始下载文件必须保留到该校验完成，之后才可删除 Downloads 原件。

洪灝和生财有术的“全部 Tab”轻量计划复用同一套 SMTP 配置：

- `milestone` 会发送任务提交、同步完成和最终失败。
- `summary` 会发送同步完成和最终失败。
- `failure_only` 只发送最终失败。
- 可在 `zsxq-system-sync.html` 的“洪灝 / 生财全部 Tab 计划”中为每条计划单独关闭节点邮件。

配置后需要重启后端服务、调度器、`zsxq_workflow`、`zsxq_sync` 和通知 Celery worker。

Gemini 与豆包真实浏览器 worker 会额外检测“登录状态失效”和“安全验证/验证码”。命中后会立即使用同一套 SMTP 配置发邮件，内容包含任务 ID、Worker、浏览器 Profile、页面地址与失败截图。同一个平台、Profile 和事件类型在冷却期内只发送一次，避免多个并发任务重复轰炸邮箱；冷却期后若问题仍然存在会再次提醒。

测试接口：

```bash
curl -X POST http://localhost:5001/api/v1/zsxq/workflow-notifications/test
```
