# 2026-08-03 数据库性能与错误巡检

## 结论

窗口为 2026-08-02 06:00:58 至 2026-08-03 06:04（Asia/Shanghai），覆盖指定日志、普通文本轮转文件和全部 Celery 日志。应用慢 SQL 683 条，P50/P95/最大为 1.519/8.590/60.367 秒；另有 52 条人工只读诊断已分离。真实 EXPLAIN 均在 0.020-2.390ms，主因仍是 PgBouncer/公网网络等待与连接持有，不能用盲目加索引处理。

本轮安全落地：日志分析器补齐轮转日志、端点 E2E、异常分桶和 Celery 任务耗时；ZSXQ 调度配置增加 60 秒可失效缓存。重启安全门禁通过后完成后端、全部托管 worker 与 scheduler 重载。没有执行生产 DDL、数据回填、破坏性操作，也没有删除原始 JSON。

## 量化证据

| 指标 | 本轮 | 上轮 | 变化 |
| --- | ---: | ---: | ---: |
| 应用慢 SQL | 683 | 565 | +20.9% |
| P50 / P95 / 最大 | 1.519 / 8.590 / 60.367s | 1.514 / 15.267 / 60.692s | P95 -43.7%，数量上升 |
| 应用慢 SQL 累计 | 2,216.962s | - | 新基线 |
| 连接持有 | 106 | 78 | +35.9% |
| 托管进程无匹配慢 SQL持有 | 28 | 15 | +86.7% |
| PgBouncer query timeout | 5 个去重事件 | 7 | -28.6% |
| connect/statement/lock/idle-in-tx | 0/0/0/0 | 1/0/0/0 | connect timeout 改善 |

PgBouncer 1,446 个样本的 wait P50/P95/最大为 27.77/82.79/345.95ms，query 为 57.77/148.45/1,136.79ms。应用 SQL 错误 2 条，均为约 60 秒服务端关闭；另有 1 条人工诊断错误。

## 页面、API、SQL 与后台任务

轮转日志发现修复后，共得到 1,803 个完整请求、114 个端点。累计影响最大的是研报音频状态轮询 392 次/438.914s、研报音频列表 231 次/245.499s、平台凭据 196 次/102.108s、B站上传 81 次/92.944s。研报音频列表最大 60.518s，其 SQL executor 0.144ms；Celery status 最大 27.535s但仅匹配 6.647s 慢 SQL，剩余主要是控制面/连接等待。

低频高延迟包括封皮再生成 3 次、最大 35.897s；频道列表 3 次、最大 31.430s；Bilibili intelligence sync 单次 14.796s。慢但无匹配慢 SQL的端点包括微信上传状态、MP3 创建、工作流重试和 Celery status，需继续按外部 I/O、缓存和连接池归因。

后台任务 541 个成功、15 retry、1 failed：ZSXQ sync 21 个累计 6,670.792s；视觉方案 26 个累计 5,475.479s；基础文案 8 个累计 4,313.710s；publisher 18 个累计 2,617.044s。唯一 Celery failure 为 report video parse，在同秒 60.240s 数据库点查后连接关闭。长期业务任务失败主要由 Doubao/AI 超时和提示词契约问题导致，不能误归因 PostgreSQL executor。

特别链路：overview 3 次，P50 1.091s、最大 8.582s；topics/local 8 次，P50 0.630s、最大 3.767s，较上轮峰值恶化约 2.9 倍；tabs 4 次、最大 0.190s，改善；sync/runs 4 次、最大 0.484s。提示中名为 `sync/status` 的路径本窗口未实际请求，不能据此宣称健康。

## SQL、表与索引判断

主要指纹包括 external_documents 关系查找 10 次/累计 191.174s、scheduler 配置 91次/132.085s、研报音频列表 9次/118.704s，以及主题/评论快照。对应 EXPLAIN executor：external_documents 0.025ms、评论 0.228ms、主题 0.223ms、研报音频列表 0.144ms；应用的 10-60 秒来自池/网络，不是索引缺失。

目标表大小：comments 118MiB、topics 110MiB、publication_tasks 104MiB、attachments 66MiB、sync_runs 29MiB、external_documents 6.856MiB；索引扫描占比均约 99.4%-99.95%。没有完全重复索引，但附件表更新 237,311 次、HOT 仅 0.13%，零扫描的 `ix_zsxq_attachments_group_tab_status_file` 为 4.30MiB；另有两个近零扫描候选。`pg_stat_statements` 未安装，仍是数据库侧历史观测缺口。

## 已落地修复与验证

- 日志分析器扫描 `.1/.2` 普通轮转并去重，区分应用与 ad-hoc SQL，输出端点请求量/失败率/累计/P50/P95/最大、request id 慢 SQL关联、三类异常和 Celery 任务统计。
- ZSXQ 全部 Tab 配置增加 60 秒深拷贝缓存，保存和默认插入时失效；预计将 30 秒轮询 SQL至少减半。
- 上轮鉴权请求级复用已随本次重启加载；尚需真实登录流量验收每 request id 最多一次用户查询。
- 完整 unit：`1006 passed, 3 skipped, 1 failed`；唯一失败为既有前端 cache-bust 断言。compileall、diff check 和 Bug 严格检查结果见本轮最终报告。

## 待人工 SQL、风险与下一步

- 不建议新增索引。零扫描附件索引仅可由管理员在 DBeaver 安全窗口执行 `docs/sql/zsxq_attachment_file_index_cleanup_dbeaver_20260801.sql`；脚本含 timeout、验证与回滚，本轮未执行。
- `docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql` 仍待管理员维护；启动检查确认两个等价索引仍在且迁移未记录。存在所有者和锁风险，禁止无人值守。
- 下一轮优先验收新 scheduler PID 缓存、鉴权单查预算、PgBouncer 60 秒长尾、topics/local 回退，以及研报音频/Celery status 的无慢 SQL端到端长尾。
