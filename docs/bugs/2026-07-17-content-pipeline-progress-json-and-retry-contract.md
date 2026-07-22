# 2026-07-17 内容清洗进度反复读取 JSON 且重试异常被调用契约覆盖

## 基本信息

- 状态：已修复，等待 content_pipeline worker 重启与真实任务验收
- 严重级别：高
- 影响模块：Content Pipeline / PublicationTask / Celery 重试 / PostgreSQL
- 首次发现：2026-07-17 慢查询复核与回归测试
- 修复提交：待提交
- 根因分类：SELECT-before-UPDATE、JSON 热写、ORM 生命周期、重试调用契约
- 复发键：`content-pipeline-progress-select-before-update`
- 是否复发：属于 `DB-SESSION-LIFECYCLE` 和 `HOT-PATH-PAYLOAD-POLLING` 已知根因簇的新增路径
- 关联记录：`2026-07-16-zsxq-expired-orm-full-row-refresh.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

- 音频清洗进度回调每次先按主键读取 `PublicationTask.result`，再合并消息并写回；两次查询分别耗时 8.06 秒和 8.82 秒。
- 最近内容清洗任务的 `result` 最大约 132KB，进度越频繁，JSON 读取、反序列化和整行更新越多。
- 回归测试进一步发现，通用失败保护器会给任意 runner 传入 `celery_task` 关键字。runner 不接受该参数时会抛出 `TypeError`，覆盖原本可识别、可重试的数据库连接异常。

## 影响

- 清洗任务运行期间产生不必要的数据库往返和 JSON 搬运，放大远端 PostgreSQL 链路抖动。
- 数据库瞬时断连可能不进入既定 Celery 重试，而被错误的调用契约提前改写成普通失败。
- 只优化最终查询或增加主键索引无法解决，因为问题发生在每次进度事件和异常包装层。

## 根因

1. 进度写入沿用了“加载任务、读取旧 result、合并、提交”的 ORM 模式，没有区分高频状态与终态审计结果。
2. 初次状态提交后仍保留 ORM task，并跨越长时间媒体处理与自动发布交接。
3. 通用失败保护器把专用 guarded runner 和任意测试/扩展 runner 当成同一函数签名调用，缺少调用契约测试。
4. 首轮防回归纪律只覆盖已知 ZSXQ、MP3 和 report audio 路径，没有覆盖内容清洗进度回调。

## 解决思路

- 高频进度只直接更新 `current_step/progress`，不在回调中读取 `result`。
- 审计消息和 cleaner 状态保存在内存快照，终态时和最终产物一次性完整写入。
- 外部 PDF cleaner 的 job ID 首次出现时做一次无 SELECT 检查点写入，保证 worker 崩溃后仍可追踪外部 job。
- 初次加载后只保留 task ID、参数和结果快照，长任务及自动发布交接不再访问 ORM task。
- 通用保护器只给两个明确支持该参数的 guarded runner 传 `celery_task`，其他 runner 保持原始单参数调用。

## 实际方案

- 新增 `_update_task_fields` 和 `_update_task_progress`，使用单条 SQLAlchemy bulk update 并在每次提交后释放 session。
- PDF/音频进度回调取消 `_load_task` 和 `fresh.result`；重复进度事件不再写数据库。
- 成功与失败路径都从内存快照组装完整结果，业务字段、错误和最终产物保持不变。
- 自动发布交接改为只传字符串 task ID，避免下游提交后触发旧 ORM 对象刷新。
- watchdog 复用 75 秒业务快照缓存；publisher 父任务清理在 SQL 中只筛选终态父任务，减少远端数据库无效轮询。

## 验证

- PDF 与音频清洗业务级测试确认状态、进度、原有 result、最终文件和工作流通知完整保留。
- PDF 外部 job ID 检查点在任务完成前已写入数据库，且不需要前置 SELECT。
- 数据库异常重试和重试耗尽通知测试均通过。
- 投影纪律、队列修复、watchdog 和内容清洗相关测试共 69 项通过。
- 音频输出限制 pytest 3 项通过。
- `git diff --check` 和 Bug 记录严格检查通过。

## 复发预防

1. 进度回调禁止“先读取 JSON 再更新”；默认只更新结构化状态列。
2. 需要崩溃恢复的外部 job 标识只允许低频检查点写入，不得随每次轮询重写大 JSON。
3. 媒体处理、浏览器操作和外部网络调用前必须释放 DB session，跨边界只传标量 ID 和不可变快照。
4. 通用包装器不得改变未知回调的函数签名；专用扩展参数必须由显式适配表控制。
5. 性能修复必须同时通过业务结果完整性测试和数据库访问纪律测试，不能只验证查询数量下降。
