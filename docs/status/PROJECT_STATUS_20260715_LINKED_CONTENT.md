# 关联内容中心落地记录（2026-07-15）

## 目标

把知识星球 item 正文中出现的飞书、知识星球 item、微信公众号文章及其他网页链接统一建模，并允许从 item 卡片进入独立页面查看完整正文。

## 数据关系

- `external_documents`：按 canonical URL 去重保存关联内容快照、抓取状态、AI 总结和 TTS 状态。
- `zsxq_topic_external_documents`：连接来源 item 与 `external_documents`；唯一键为 `group_id + tab_id + topic_id + url_hash`。
- 知识星球内部链接仍复用上述两张表：`provider=zsxq`，目标 item id 同时保存在 `raw_payload.target_topic_id`。抓取时优先读取本地 `zsxq_topics`，本地不存在才调用 topic detail API。

因此，一个外部文档可被多个 item 引用，一个 item 也可关联多个内容；正文不会重复存入每条关系记录。

## 链接处理规则

| 链接类型 | provider | 行为 |
| --- | --- | --- |
| 飞书 wiki/docx/docs | `feishu` | 使用平台凭据中的飞书 Cookie；静态 HTML 无正文时使用浏览器渲染兜底 |
| 知识星球 topic detail | `zsxq` | 优先复用本地 item；缺失时使用知识星球 Cookie 获取 |
| 微信公众号文章 | `wechat` | 抓取 `#js_content`，提取标题、作者和正文 |
| 其他 HTTP(S) 网页 | `generic_web` | 只登记原地址，不自动抓取，避免访问不受控站点 |
| 图片、脚本、样式、字体资源 | - | 忽略 |

历史全量扫描只读取 item 的正文投影列；新 item 入库时会额外检查 raw payload 中明确的正文/文章/链接字段。两条路径都不会扫描整份 raw JSON，避免将知识星球平台元数据里的 URL 误识别为内容。

## 用户入口

- `zsxq-lab.html` 的每条关联内容提供“查看更多”。
- `linked-content-detail.html?id=<document_id>` 展示完整正文、总结、引用 item 和后续操作。
- `linked-content-detail.html` 无 id 时作为关联内容中心，提供来源/状态/关键词筛选和后台补抓入口。

## 后台补抓

- Celery task：`tasks.external_documents.run_backfill`
- queue：`content_pipeline`
- API：`POST /api/v1/external-documents/backfill/tasks`
- 状态：`GET /api/v1/external-documents/backfill/tasks/<task_id>`
- 每处理一条即保存进度；失败记录在 item 结果中，重复提交会返回当前活跃任务，任务重跑只选待抓取、失败或空正文记录。

## 验证

- Python 静态编译通过。
- 前端 JavaScript 语法检查通过。
- 外链分类、飞书动态页面兜底、微信正文解析共 12 个单测通过。
- 微信大 HTML 后置正文回归测试已覆盖；正文在解析完成后再按上限截取。
- 独立详情页/补抓轮询及既有前端纪律共 6 个单测通过。
- 真实扫描：6,103 个 item，1,332 个去重内容对象，1,777 条 item 引用关系。
