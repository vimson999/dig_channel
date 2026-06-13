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
```

`WORKFLOW_EMAIL_PASSWORD` 填 QQ 邮箱生成的 SMTP 授权码，不是 QQ 登录密码。

通知级别：

- `milestone`：启动、同步、清洗、发布、整轮结束都通知。
- `summary`：只通知整轮结束。
- `failure_only`：只通知整轮失败。
- `off`：关闭通知。

配置后需要重启后端服务和 `zsxq_workflow` Celery worker。

测试接口：

```bash
curl -X POST http://localhost:5001/api/v1/zsxq/workflow-notifications/test
```
