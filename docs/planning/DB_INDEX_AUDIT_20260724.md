# 数据库重点表索引审计与整改

审计日期：2026-07-24

## 验收原则

索引整改必须同时检查查询计划、等价定义、累计扫描、表写入频率和业务代码查询形状。单纯看到慢日志就新增索引，不再视为完成。

## 现场结论

| 表 | 规模与写入特征 | 结论 |
| --- | --- | --- |
| `publication_tasks` | 6,205 行，累计更新 161,884 次，索引扫描约 196 万 | 现有核心宽字段索引已覆盖热点；删除 4 个旧 JSON/前缀重复索引 |
| `zsxq_topics` | 10,759 行，累计更新 45,697 次 | 2 个按 `topic_type` 的零/低扫描索引应删除；因表属主为 `postgres`，仍待管理员执行 |
| `zsxq_topic_comments` | 37,565 行，累计更新 21,524 次 | 根因是无变化 JSON 重写；新增载荷哈希，删除 5 个无查询证据的单列索引 |
| `zsxq_attachments` | 24,003 行，累计更新 214,645 次 | 保留同步、下载、发布组合索引；被复合索引覆盖的单列索引仍待管理员删除 |
| `zsxq_sync_runs` | 539 行，索引总量约 664KB | 体量小且查询形状稳定，本轮不删，继续观察 |
| `external_documents` | 1,484 行，累计更新 5,756 次 | 删除与唯一约束完全等价的 `url_hash` 唯一索引，保留约束索引 |
| `video_data` | 733,660 行，总计约 130MB | 本轮慢日志没有指向该表，不做无证据改动 |

## 已验证查询计划

- `publication_tasks` 父/来源子任务查询：`BitmapOr` 命中父、来源索引，执行约 `0.234ms`。
- `zsxq_sync_runs` 按 group 最新 120 条：命中 `group_id, created_at DESC`，执行约 `0.388ms`。
- `external_documents` 按 `url_hash` 点查：命中唯一索引，执行约 `0.035ms`。
- 研报音频“精确总数 + 20 行列表”合并查询：总数与列表分别命中结构化索引，执行约 `0.725ms`；常规页面从 3 次数据库往返降为 2 次。

这些 SQL 在数据库 executor 内是毫秒级。应用端 1-30 秒延迟应归因于连接池、网络等待和同期写压力，不应继续盲目加索引。

## 保留策略

- `publication_tasks`：保留父子关系、`feature/user/status/created_at`、等待子任务、报告音频和 MP3 工作流的结构化部分索引。
- `zsxq_topics`：保留 group/tab/topic 唯一约束及按 group/tab/create_time 的列表索引。
- `zsxq_topic_comments`：保留 group/topic/comment 唯一约束、group/topic/create_time、owner/create_time 以及业务清理所需 group 索引。
- `zsxq_attachments`：保留下载定位唯一约束、group/topic/tab、下载状态、发布状态时间线和来源任务索引。
- `zsxq_sync_runs`：暂时全部保留；表小，当前删除收益低于误删风险。

## 删除清单

- `publication_tasks`：`ix_publication_tasks_feature_status_updated`、`ix_publication_tasks_feature_created_at_desc`、`ix_publication_tasks_publisher_created_at_desc`、`ix_publication_tasks_publisher_pending_updated_at`
- `zsxq_topics`：`ix_zsxq_topics_group_tab_type_create_desc`、`ix_zsxq_topics_group_type_create_desc`
- `zsxq_topic_comments`：`ix_zsxq_topic_comments_tab_id`、`ix_zsxq_topic_comments_parent_comment_id`、`ix_zsxq_topic_comments_owner_user_id`、`ix_zsxq_topic_comments_owner_name`、`ix_zsxq_topic_comments_create_time`
- `zsxq_attachments`：`ix_zsxq_attachments_publish_status`
- `external_documents`：`ix_external_documents_url_hash`

执行状态：`cappa_rw` 所有的 10 个冗余索引已删除。以下 3 个索引属于
`postgres`，当前仍存在，不能记为完成：

- `ix_zsxq_attachments_publish_status`
- `ix_zsxq_topics_group_tab_type_create_desc`
- `ix_zsxq_topics_group_type_create_desc`

## 落地与防复发

- 迁移：`20260724_comment_hash_indexes`
- 管理员 SQL：`docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql`
- 在线维护脚本：`scripts/maintenance/apply_comment_hash_index_cleanup.py`
- 启动门禁：缺少评论哈希列、哈希回填未完成、权限或关键索引缺失时拒绝启动；Alembic 标记和纯冗余索引清理作为显式管理员告警，不阻挡兼容代码上线。
- 模型定义已删除对应 `index=True/unique=True`，避免 `create_all` 重新生成重复索引。
- 2026-07-24 14:05 已滚动切换 `zsxq_sync` worker；14:09 已滚动切换 Flask 后端。两次操作均未中断正在运行的研报视觉任务。
- 真实库回滚验收：重放 4 条未变化评论，`comments_updated=0`，实际评论 `UPDATE` SQL 为 0。
- 下一轮审计必须使用重置后的 `pg_stat_user_indexes` 时间窗；累计零扫描只作为候选，不单独作为删除依据。
