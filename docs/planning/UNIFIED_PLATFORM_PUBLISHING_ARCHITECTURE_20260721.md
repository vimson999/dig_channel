# 全平台投递分发架构（2026-07-21）

## 目标

将 B站、YouTube、抖音、视频号和知识星球的异步发布统一到一个“平台注册与任务分发”能力中，同时保留各平台不同的命令模型、凭据解析和远端执行逻辑。

## 设计边界

### 统一能力

- 平台 ID、别名和中文名称。
- Celery 队列、任务加载器和运行时 feature。
- 是否在提交时执行容量准入。
- `apply_async` 调用、队列选择和分发回执。
- feature 到唯一平台任务的反向解析，供孤儿任务恢复使用。

### 平台适配能力

- B站和 YouTube：视频元数据、封面、分区、充电、合集等平台参数。
- 抖音和视频号：Cookie 浏览器自动化、页面选择器和可见范围。
- 知识星球：清洗目录、主题文本、附件批次和 Cookie 池。

不设计接受任意 `**kwargs` 的“万能任务创建器”。不同平台先由自己的命令构造器完成校验和持久化，再把任务 ID 交给统一分发门面。

## 调用链

```text
业务页面/API/自动编排
        ↓
平台命令构造器（各平台所有）
        ↓
PublicationTask + 平台结构化投影（如需要）
        ↓
platform_publish_dispatch.dispatch_platform_publish
        ↓
平台注册表选择队列、Celery task、准入策略
        ↓
平台执行适配器
```

## 队列策略

- `platform_upload`：B站、YouTube、抖音、视频号。它们都属于视频上传，受同一上传 worker 的资源边界约束。
- `zsxq_publish`：知识星球。它发布主题和附件，并有独立的批处理、Cookie 池及容量约束。

统一分发不等于统一队列；队列按资源模型和故障域隔离。

## 成功经验复用

- 复用 `PublicationTask` 状态模型与 `guard_publication_task_start` 运行时保护。
- 复用知识星球现有的 `maybe_guarded_apply_async` 提交准入。
- 复用 `platform_upload` 单 worker 和 Celery Monitor 的保护边界。
- 保留 B站结构化上传投影，避免重新把热路径退回大 JSON。
- 使用惰性任务加载器，避免 service 与 Celery task 之间形成循环导入。

## 兼容策略

- 保留现有 API 地址、Celery task 名称、feature 名称和页面协议。
- `douyin_upload_test`、`wechat_channels_upload_test` 暂时作为历史 feature 名称保留，避免破坏已有任务、看板和恢复逻辑。
- 知识星球实验室的同步直连接口仍作为低层诊断能力；正式清洗产物发布走统一异步分发。

## 验收

- 注册表覆盖 B站、YouTube、抖音、视频号和知识星球。
- 四类现有异步入口不再直接决定 Celery task/queue。
- 知识星球仍执行提交准入，视频上传仍投递到 `platform_upload`。
- 既有任务状态、失败收口、结构化投影和 API 响应保持兼容。
- 单元测试覆盖别名、未知平台、队列选择、普通分发、准入分发及恢复反查。
