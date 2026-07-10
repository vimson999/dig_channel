# 2026-07-10 重复 Bug 首轮系统回顾

## 回顾范围

- 时间范围：2026-06-23 至 2026-07-10。
- Bug 记录：57 条。
- 归类结果：7 个复发根因簇，4 个独立观察事件。
- 数据来源：`docs/bugs/*.md`、`docs/bugs/recurrence-registry.json`。

## 核心结论

项目并不是没有记录经验，而是经验主要停留在单点文档和单模块测试。父子任务状态、数据库 Session、watchdog 修复、热点 JSON 查询等能力仍由多个工作流分别实现，导致同一个根因换一个入口再次发生。

这类复发不能继续按“发现一个入口、补一个条件”处理。二次出现后必须升级成共享状态契约、跨工作流契约测试和运行时不变量。

## 复发根因簇

| 复发键 | 关联记录数 | 优先级 | 判断 |
| --- | ---: | --- | --- |
| `ORCH-PARENT-CHILD-STATE` | 16 | P0 | 最严重。DB、Celery、worker 和文件产物各自表达状态，父子关系又缺少明确生命周期策略。 |
| `DB-SESSION-LIFECYCLE` | 11 | P0 | 多次出现跨等待持有 ORM、释放或提交后继续读取过期对象、长事务和大 JSON 回读。 |
| `OUTPUT-SEMANTIC-VALIDATION` | 8 | P0 | 技术调用成功被误当成业务产物可用，placeholder、结构标签和垃圾 fallback 得以进入后续制作。 |
| `HOT-PATH-PAYLOAD-POLLING` | 7 | P0 | 列表、轮询和状态更新仍可能读取完整 JSON，叠加轮询后表现为超时、闪烁和数据库压力。 |
| `EXTERNAL-CALL-RESILIENCE` | 6 | P1 | Gemini、豆包、DeepSeek、CDN 的超时、重试和文件就绪策略没有统一。 |
| `RUNTIME-TOPOLOGY-RELOAD` | 6 | P1 | worker、scheduler、队列注册和代码重载缺少单一事实源与启动后验收。 |
| `ASSET-PROJECTION-CONSISTENCY` | 3 | P1 | 视觉资产的生成、持久化、父子回写、API、下载、预览或操作 resolver 没有使用同一合同。 |

同一条 Bug 可以属于多个根因簇，因此表中关联数不能直接相加为 57。

### 回顾后即时复发

首轮回顾完成后，同日再次发现 Gemini 子任务完成、父任务写摘要提交后返回旧 ORM 的问题。它被立即归入 `DB-SESSION-LIFECYCLE`，并按复发升级纪律修复共享 `_wait_child()`、增加真实 Session commit 过期契约测试，而不是只回正现场任务。

## 为什么会反复出现

1. **规则不可执行。** 文档已经写明“跨等待只传 ID”“父任务终态要收口子任务”，但新入口没有统一基类、共享 helper 或契约测试强制遵守。
2. **状态迁移分散。** API、Celery task、watchdog、repair service 都能修改 `PublicationTask`，但没有集中状态迁移 API 和合法迁移矩阵。
3. **通用修复缺少业务语义。** Watchdog 只看“父任务已终态”，不知道子任务是强绑定、完成后补生还是独立上传，于是修复旧 Bug 时误伤合法场景。
4. **测试按功能切片。** 单个工作流修复后有测试，但缺少父状态 × 子任务策略 × 消息可见性 × 产物存在性的跨工作流契约矩阵。
5. **完成标准偏单点。** 历史任务被修好、页面恢复或新增 allowlist 后就算完成，没有要求同根因在所有入口接受扫描。
6. **多控制面缺少权威顺序。** DB 状态、Celery runtime、进程状态和文件产物互相矛盾时，没有统一判定优先级和结构化对账结果。

## 系统性措施

### P0-1 父子任务生命周期策略

- 为子任务增加明确策略：`bound`、`post_completion`、`independent`。
- Cleanup/reconciler 读取策略，不再按 `source_feature` 增加零散 allowlist。
- 集中父子任务终态迁移和产物回写。
- 契约测试覆盖父任务 `completed/failed/canceled` 与三类子策略的组合。

验收：合法后补不会被取消；真正父失败的强绑定任务能够收口；已有产物成功后不会残留旧错误。

### P0-2 DB 工作单元与快照边界

- 编排跨等待只保存 ID 和不可变快照，ORM 对象不得越过 session 释放点。
- 状态轮询统一使用投影列和短事务 helper。
- 为所有核心工作流增加“强制 expire/remove 后继续执行”的契约测试。

验收：核心编排不再出现 detached、PendingRollback、idle in transaction；轮询不读取完整 `result`。

### P0-3 产物语义校验

- AI 返回必须通过结构 schema 和业务字段校验。
- 不可用的 `local_fallback` 不得进入封面、音频或视频生产。
- 页面必须区分技术调用成功、结构化通过、业务产物可用。

验收：placeholder、章节标签、解释性包装文字和缺字段响应会失败收口，不会继续生产垃圾资产。

### P0-4 热路径预算

- 列表/轮询 API 只使用投影列，设置响应体与查询次数预算。
- 前端轮询统一 single-flight，详情展开状态不因刷新重建。
- 将热点路径纪律测试从具体函数扩展成 API 契约测试。

验收：轮询不会重叠；典型列表冷请求和响应体积有固定上限；数据库压力不会随打开页面数量线性放大。

## 下次回顾

- 最迟日期：2026-07-17。
- 提前触发：新增 5 条 Bug，或任一已知复发键再次出现。
- 重点检查：`ORCH-PARENT-CHILD-STATE` 与 `DB-SESSION-LIFECYCLE` 是否已从 allowlist/局部 helper 升级为共享契约。
