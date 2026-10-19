# 知识星球 API 采集 JSON 导入

入口：独立页面 `zsxq-import.html`，也可从知识星球内容分析页顶部「导入 JSON」或导航「选题情报 → 星球数据导入」进入。选择文件，点击「预览导入」，检查星球、时间范围和数量后点击「确认入库」。最近 20 次导入可在「最近导入记录」中查询。

支持浏览器采集格式 `schemaVersion: 1`、`groupId`、`exportedAt`、`records[]`，单文件最多 12 MiB、1000 条请求记录。仅提取目标星球的成功且未截断的 `https://api.zsxq.com/v2/groups/<groupId>/topics` 和 `/topics/sticky` 响应；忽略动态流、推荐、个人资料等其他接口。支持预设 scope 和 topics 请求的 hashtag_id。置顶帖归入最新标签页。

帖子按现有 `(group_id, tab_id, topic_id)` 唯一键保存，评论按现有全星球评论唯一键合并。旧采集不覆盖新快照，缺失字段不删除已有字段；空值和空列表采取保留策略，因此该入口不用于同步内容删除。采集时间来自 recordedAt（缺省 exportedAt）；已存在的在线同步记录使用最近同步时间作为保护基线。

数据保存到既有帖子、评论、附件元数据表。导入批次保存在 `zsxq_sync_runs`，mode 为 `json_import`，包含内容来源摘要、导出/导入时间、响应页范围和新增/更新/重复/受保护计数。请求头和完整网络采集文件不保存到数据库，亦不调用知识星球远端接口。附件不会自动下载，评论只涵盖采集文件中提供的评论。

预览统计是当前数据库的估算；确认入库时重新计算。导入事务失败则回滚，同一星球并发 JSON 导入在 PostgreSQL 中串行执行。与独立在线同步竞争造成唯一键冲突时会回滚，可重新预览后重试。

时间跨度只说明采集到的最早/最新帖子，不承诺中间历史完整，不推进在线同步游标。

API（管理员权限）：

- `POST /api/v1/zsxq/json-import/preview`：multipart `file`。
- `POST /api/v1/zsxq/json-import/commit`：multipart `file` 和预览返回的 `file_sha256`，验证文件未被替换。
- `GET /api/v1/zsxq/json-import/history`：最近 20 次已完成批次。

验证：`venv/bin/python -m unittest discover -s tests/unit -p test_zsxq_json_import.py`。
