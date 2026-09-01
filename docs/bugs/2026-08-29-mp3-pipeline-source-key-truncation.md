# MP3 内容流水线资产引用截断导致历史编排不可见

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING；历史兼容查询已在真实页面恢复，未来写入仍待下一次自然投递验收
- 严重级别：P1
- 影响模块：MP3 视频编排创建 / 音频资产 workflow-stats / `publication_tasks.source_task_id`
- 首次发现：2026-08-29 workflow-stats 604.96ms 指纹追根
- 修复提交：当前工作区
- 根因分类：带类型引用误写入有界标识字段、静默截断、来源所有权投影错误、历史兼容缺失
- 复发键：`ORCH-PARENT-CHILD-STATE`
- 是否复发：是；生产中已有 11/11 个内容流水线来源任务带相同截断形态
- 关联记录：`2026-08-29-mp3-audio-asset-stale-candidate-scan.md`、`2026-07-13-mp3-hot-path-json-payload-regression.md`
- 防线层级：source_identity_contract / compatibility_projection / query_budget / runtime_observation

## 现象与影响

真实页面搜索内容流水线资产时，`request_id=ad569087` 的 workflow-stats 返回 0 行，页面因而看不到已经成功的历史编排。数据库却存在任务 `a344af22-a913-445e-8369-77b7a506aa56`，其完整资产引用是
`pipeline:7a1f5b5e-eed3-4959-a676-d1a52fc2938d`，对应页面音频《英伟达AI融资平台的机遇与风险260827.mp3》。页面误判会诱导用户重复投递并重复触发昂贵的外部模型与渲染链路。

只读生产核验发现 MP3 工作流共 296 条，其中 11 条来自 `pipeline:` 资产；这 11 条的
`source_task_id` 全部是 36 字符前缀，完整的 45 字符类型引用均未保留在该列，且
`source_feature` 被误写为 `zsxq_attachment`。当前样本中 11 个历史截断键互不碰撞，但这不能把静默截断视为合法标识合同。

## 根因

创建任务时把 API/JSON 层的带类型引用 `pipeline:<uuid>` 直接写入 `PublicationTask.source_task_id`，同时把所有非上传来源统一标记成 `zsxq_attachment`。`PublicationTask.sync_projection_fields` 为兼容 36 字符列宽会压缩该值，于是 45 字符引用被静默截断；workflow-stats 又用页面传来的完整引用做等值过滤，必然返回 0 行。

这不是索引缺失。真实 `EXPLAIN (ANALYZE, BUFFERS)` 均走现有
`ix_publication_tasks_mp3_source_user_created_desc`：完整引用返回 0 行、执行 0.032ms；历史截断键返回 1 行、执行 0.028ms；规范化 UUID 在修复前没有新格式记录、执行 0.022ms。索引累计扫描 6,208 次，未生成新 DDL。

## 解决思路与实际方案

- 新增单一来源身份解析：`pipeline:<uuid>` 在结构化投影中保存原始 UUID，并标记
  `source_feature=content_pipeline_asset`；上传和知识星球来源保持各自既有语义。
- 工作流 JSON 中继续保存完整带类型 `audio_asset_id`，用于 API 回显和审计；不能为了列宽删除类型信息或修改原始 JSON。
- workflow-stats 为每个页面引用建立有界、无歧义的 source-key 映射：内容流水线同时匹配未来的原始 UUID 和历史 36 字符截断键，再映射回完整页面引用。
- 兼容查询仍是一条有界、窄投影 `publication_tasks` 查询，不扫描 `params/result` JSON，不做 N+1，也不执行生产数据回填。
- 若多个完整引用会映射到同一历史截断键，则拒绝用该歧义键归属结果，避免错误串单。
- 继续追到共享模型层：`PublicationTask.sync_projection_fields` 不再对任意超长
  `source_task_id` 静默 `_compact_text(..., 36)`。新写入超过 36 字符时直接 fail closed；只有已经持久化、
  且同时满足 `mp3_video_workflow + zsxq_attachment + pipeline:` 与当前 36 字符前缀完全一致的已知历史
  指纹才能原样通过，从而兼顾 11 条旧记录可更新与所有未来调用方不可再制造同类数据。

## 验证

- TDD 红灯：旧创建路径得到截断的 `pipeline:` 前缀而不是 UUID；旧 workflow-stats 对历史截断记录返回 0；
  共享模型仍会把任意 45 字符来源键静默截断，新增模型契约先失败为“未抛出 ValueError”。
- 定向绿灯：`test_mp3_video_upload_api.py` 与 `test_task_projection_models.py` 合计 23/23；模型/MP3/
  本地 PgBouncer/数据库投影子集 108/108；完整 unit **1,462/1,462**。历史兼容测试通过 Core 插入精确
  旧指纹，证明已持久化旧行仍可更新；新对象超宽身份一律拒绝。compileall 与 diff check 通过。契约还
  覆盖同 UUID、不同 `source_feature` 不得串单，以及 API 必须回显完整 `pipeline:` 类型引用。
- 全库候选优先级只读盘点确认：所有超过 36 字符的来源候选仍只有这 11 条，均为同一已知 MP3 历史
  指纹，11/11 静默截断，候选长度均为 45；没有发现其他 feature 的未知超宽身份。用新模型代码加载真实
  历史任务 `a344af22-a913-445e-8369-77b7a506aa56` 后同步投影，前后 36 字符键完全不变并回滚。
- 只读真实 service 在未回填历史数据的情况下，用完整引用返回 `total=1/succeeded=1`，最新任务为
  `a344af22-a913-445e-8369-77b7a506aa56`，查询 28.15ms。
- 两次重启前门禁均确认 47 条 processing 记录中没有不可中断任务、活动 ZSXQ sync 为 0；均未使用 FORCE。首次标准整栈重启到 backend PID 59946；补上 `source_feature` 归属和完整类型引用回显契约后再次标准重启到最终 PID 62310。最终 health healthy，18:44:25 bootstrap 的 17/17 服务均 `ok=true`、`errors=[]`。
- 真实登录页面搜索目标音频后，卡片显示“编排 1 次 / 最近已完成 / 成功 1”。精确接口
  `request_id=7835cf1d` 返回 1 行，端点 297.64ms、SQL 204.16ms；后续精确复测
  `49c4512a/cf2c7e3d/ccc58ac6` 分别为 544.38/310.83/176.60ms。最终 PID 62310 上
  `request_id=6487357a` 为 178.68ms、SQL 112.67ms；五次均返回 1 行，无慢查询、长 Session 或 SQL 错误。
- 共享模型防线经第三次标准整栈重启加载到 backend PID **65497**；重启前两次安全检查均为 47 条
  processing、0 条活动 sync、无不可中断任务，未使用 FORCE。19:07:29 bootstrap 17/17 服务
  `ok=true`、`errors=[]`，health healthy。真实页面再次搜索同一音频，仍显示“编排 1 次 / 最近已完成 /
  成功 1”；暖搜索 `a4462def` 5.54ms、0 条目录 SQL，精确 stats `1bbf928e` 244.05ms、SQL
  177.05ms、返回 1 行。
- 新进程首次目录冷建 `8a54a58e` 为 13.260s，其中 canonical SQL 12.362s；同时 jobs
  `f36e0af5` 为 8.291s，连无业务扫描的 `SET LOCAL statement_timeout` 都耗时 5.726s。只读生产
  EXPLAIN 证明 canonical 表 726 行/目标 280 行，三次 executor 仅 0.396–0.568ms、64–67 个 shared-hit、
  0 read/temp block，直接客户端 30.4–31.9ms；因此这是既有 PgBouncer/Tailscale 返回长尾反复，
  不是本 source-identity 修复复发，也不构成新增索引证据。

## 复发预防与后续事项

- API 层的带类型引用不得直接写入定长 UUID/任务标识列；必须在所有者边界拆成 `source_feature` 与规范化 `source_task_id`，完整引用留在审计 JSON。
- 历史兼容只能基于窄结构化投影，并对截断键碰撞 fail closed；禁止重新引入 `params/result` JSON 扫描。
- 为避免凭验收制造昂贵任务，本轮没有额外投递真实 MP3。下一次自然的内容流水线 MP3 投递必须验证：新任务顶层
  `source_task_id` 是 36 字符原始 UUID、`source_feature=content_pipeline_asset`，JSON 仍保留完整 `pipeline:` 引用，页面只计数一次。
- 从最终 backend **2026-08-29 19:06:43** / 真实页面 **19:08:39** 边界继续完整 24 小时：旧完整引用
  查 0 行指纹不得再出现；历史 11 条均可见且无串单；新写入不再产生截断 `pipeline:` 前缀；任何未知
  超宽 source 写入必须 fail closed。当前没有自然新投递，证据不足前保持 OBSERVING；网络长尾另按
  `DB-SESSION-LIFECYCLE` 保持 OPEN/REPEATED。

## 2026-08-30 后窗复核

最终进程后没有新的自然 MP3 pipeline 任务；生产仍为 11 条已知历史截断前缀、0 条新规范 owner
样本、0 个未知超宽持久化候选。真实页面继续显示目标资产“编排 1 次 / 最近已完成 / 成功 1”，
workflow-stats 与暖目录没有重建 JSON 全表扫描。历史兼容和 fail-closed 防线仍有效，但没有自然新写
证据，故继续 OBSERVING；下一轮必须优先验收首条自然新任务的 36 字符 UUID、正确
`source_feature`、完整审计 JSON 与唯一页面计数。

## 2026-08-31 后窗复核

固定窗口与巡检结束时仍没有新的自然 `mp3_video_workflow` 任务，也没有新的内容流水线 source identity
样本；因此未来写入合同不能升级为已验收。真实 MP3 页面加载 10 个 canonical cleaned 资产，扫描
280 条、跳过缺失 210 条；冷目录 request `2ceea551` 为 18.386 秒，其中唯一 canonical SQL
17.433 秒，但同形 executor 仅 0.626ms。暖请求 `add7d05f` 命中 279.622 秒快照，7.93ms、0 目录 SQL，
workflow-stats 286.54ms；没有回退 JSON 扫描或逐项候选 N+1。

历史兼容与缓存/查询预算仍为 `RUNTIME_REPLACED / OBSERVING`；冷 miss 的恶化归入网络复发键。
下一轮仍必须优先验收第一条自然 pipeline 任务的规范 UUID、owner、完整审计引用和唯一计数。
