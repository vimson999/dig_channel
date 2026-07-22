# B站内容情报台

## 目标

跟踪指定 B站 UP 主的最新投稿，为研报转视频和 MP3 转视频两类内容提供以下判断：

1. 对手最近发布了什么。
2. 本地是否存在可能对应的 PDF 或音频。
3. 对应素材是否已经进入编排、完成编排或失败。
4. 人工是否认为该线索值得继续制作。

首版只做情报、匹配和人工决策，不自动创建编排任务。

## 成功经验复用

- B站能力：复用 `bilibili-api-python==17.4.1`、`resolve_bilibili_auth()` 和平台凭据成功/失败标记，不解析动态网页 DOM。
- 本地素材：复用 `ZsxqAttachment`、`ZsxqTopic`、`ReportVideoReport/Analysis` 和 `PublicationTask` 的结构化投影列。
- 编排状态：复用 `PublicationTask.source_task_id`、`report_audio_pdf_attachment_id` 和现有工作流状态，不让 AI 猜测任务是否已经编排。
- DS 调用：复用项目现有 OpenAI-compatible `/v1/chat/completions`、超时配置、严格 JSON 收口和低温度策略。
- 数据库纪律：外部 B站/DS 请求前释放 SQLAlchemy session；列表只读取结构化列，完整 `raw_payload/match_payload` 仅由详情接口读取。
- 前端：复用 `env.js`、统一导航、登录校验、分页和状态展示模式。

未复用编排父任务、子任务、通知和失败续跑契约，因为首版没有自动创建长耗时编排。后续如果把同步/匹配迁入 Celery 或从情报台自动发起编排，必须完整接入 `WORKFLOW_ORCHESTRATION_STANDARD.md`，不能只调用底层 worker。

## 数据流

```text
Bilibili User.get_videos(uid)
        ↓ bvid 去重、增量更新
bilibili_competitor_videos
        ↓ 标题归一化、机构别名、字符相似度
本地候选 Top 8
        ↓ DS v4 flash 保守裁决
same_source / same_event / same_topic / unrelated
        ↓ 确定性查询
PublicationTask 编排状态
        ↓
人工：值得做 / 忽略 / 确认匹配 / 否定匹配
```

## 匹配口径

- `matched`：同一份报告、同一场音频，或同一事件且证据充分。
- `possible`：很可能对应，但仍需人工确认。
- `related`：只是同一行业或同一主题，不代表本地已有同一素材。
- `not_found`：召回候选中没有对应内容。
- `insufficient_evidence`：标题或本地元数据不足，不能强行判断。
- `failed`：DS 请求或结果校验失败。

模型只能选择代码召回列表中真实存在的 candidate key。模型返回列表外 ID 时，服务强制降级为 `insufficient_evidence`。低于 0.75 置信度的 `matched` 强制降级为 `possible`。

## 默认监控源

| 类型 | 名称 | UID |
| --- | --- | --- |
| report | 小白投资笔记 | `1221628467` |
| report | 美股研报局 | `3546604889967195` |
| audio | 滚雪球之路 | `1938835498` |
| audio | 星辰财经- | `3494372945431057` |

## API

- `GET /api/v1/bilibili-intelligence/overview`
- `GET/POST /api/v1/bilibili-intelligence/competitors`
- `PATCH /api/v1/bilibili-intelligence/competitors/<id>`
- `POST /api/v1/bilibili-intelligence/competitors/<id>/sync`
- `POST /api/v1/bilibili-intelligence/sync`
- `GET /api/v1/bilibili-intelligence/videos`
- `GET /api/v1/bilibili-intelligence/videos/<id>`
- `POST /api/v1/bilibili-intelligence/match`
- `PATCH /api/v1/bilibili-intelligence/videos/<id>/decision`

页面入口：`/bilibili-intelligence.html`。

## 配置

- `BILIBILI_INTELLIGENCE_MATCH_API_KEY`：可选；缺省复用 `DEEPSEEK_API_KEY`。
- `BILIBILI_INTELLIGENCE_MATCH_BASE_URL`：可选；缺省复用 `DEEPSEEK_BASE_URL`。
- `BILIBILI_INTELLIGENCE_MATCH_MODEL`：可选；其次使用 `DEEPSEEK_FLASH_MODEL`、`DEEPSEEK_MODEL`，最后默认 `deepseek-v4-flash`。
- `BILIBILI_INTELLIGENCE_MATCH_TIMEOUT`：默认 120 秒，范围 10～300 秒。
- `BILIBILI_INTELLIGENCE_LOCAL_CANDIDATE_LIMIT`：每类本地来源默认读取最近 500 条，范围 50～2000；避免一次匹配把数千行素材搬进 Web 进程。
- `BILIBILI_INTELLIGENCE_SECONDARY_CANDIDATE_LIMIT`：已有编排和旧研报视频管线各补充最近 100 条，范围 20～500；命中本地附件后仍会单独查询真实编排状态。
- `BILIBILI_INTELLIGENCE_CANDIDATE_CACHE_SECONDS`：候选快照缓存默认 60 秒，范围 1～600 秒。

调用外部匹配模型时只发送对手标题和召回候选的标题、文件名、主题等元数据，不发送 PDF/MP3 文件正文。即便如此，这些仍可能属于内部业务元数据；部署方应在确认数据边界与供应商政策后再配置并启用真实模型密钥。

## 当前边界与后续

- 当前同步和每批最多 10 条 DS 匹配由人工按钮触发；没有自动创建视频编排。
- 自动周期同步应新增受管 scheduler，并补齐状态、重试、数据库压力准入、watchdog/monitor 和运行验收。
- “值得做”目前只保存人工决策。后续发起编排时必须先让用户选择匹配素材，并调用现有研报或 MP3 编排入口。
- 音频只有日期编号、没有 topic title 或转录摘要时，应保持 `insufficient_evidence`；不得让模型凭空推断。

## 验收

- 四个默认 UID 均能通过项目 B站凭证读取最新投稿。
- 重复同步不重复插入相同 `bvid`。
- 列表查询不读取 `raw_payload/match_payload`。
- AI 不能选择候选列表之外的本地 ID。
- `same_topic` 不会显示为“找到同一素材”。
- 编排状态来自数据库，不来自模型输出。
- Python 语法、前端 JavaScript 语法和匹配服务单元测试通过。
