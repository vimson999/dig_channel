# 视觉视频端到端流程停在素材就绪并误阻重启

## 基本信息

- 状态：代码已部署；历史任务自动对齐和最终视频补投已开始，待全部产物完成
- 严重级别：P1
- 影响模块：火柴人视觉视频 / 重启安全门禁 / 父子任务状态机
- 首次发现：2026-07-30
- 修复提交：待提交
- 根因分类：端到端编排缺少最终渲染迁移，且 ready 状态与 active 状态混用
- 复发键：`ORCH-PARENT-CHILD-STATE`
- 是否复发：同类父子状态收口问题再次出现
- 关联记录：`2026-07-24-auto-image-replay-downgraded-completed-video.md`、`2026-07-29-stick-scene-media-required-manual-recovery.md`
- 防线层级：shared_guardrail / contract_test / runtime_acceptance

## 现象

完整重启门禁持续报告 11 个 `report_stick_figure_video_workflow` 为不可中断
`processing` 任务，阶段为 `images_done` 或 `whole_audio_aligned`。这些记录从
7 月 28–29 日起不再更新。工作流页面和任务名称承诺的终点是视频，但系统只自动
完成分镜图片和整篇音频对齐，最终视频渲染必须再次点击按钮才会入队。

## 影响

- 11 个端到端任务没有生成最终视频，也没有明确失败，长期停留在处理中。
- 这些无活动 worker 的父容器又被安全门禁当作正在计算，阻止正常代码重载。
- 用户无法从状态区分“正在执行”“等待手工操作”和“编排漏掉下一跳”。
- 图片完成回调还可能把已完成的音频阶段倒退到较早的 `images_done`。

## 证据

- 11 个父任务均没有 `celery_task_id`，也没有
  `report_stick_figure_video_render` 子任务。
- Celery 23–26 个 worker 的 active、reserved、scheduled 总数均为 0。
- 435/435 条分镜图片记录 completed，435 个图片文件全部存在。
- 11 个来源研报均 completed/done，完整 WAV 和输出文件全部存在。
- 4 个新任务已有 completed 整篇音轨和完整时间轴；7 个旧任务创建于自动对齐
  能力部署前，尚无音轨记录。
- 前端流程文案明确为
  `PDF → 基础文案 → 按视觉方案生成分镜 → Gemini 图片 → 语音 → 视频`；
  父任务 feature 也明确为视频工作流，因此图片或音频就绪不是合法终点。

## 根因

1. 自动编排只串联到图片与整篇音频对齐，最终视频渲染创建逻辑只存在于 HTTP
   `POST /jobs/<id>/video` 的手工入口。两个异步完成回调都没有触发最终渲染。
2. 没有 ready-workflow reconciler；部署前遗留任务即使图片、WAV 和时间轴齐全，
   也不会自动创建视频子任务。
3. 视觉父任务把素材 ready 继续表示为 `processing`。重启安全门禁只识别通用
   waiting/deferred 阶段，把所有其他 processing 阶段
   都当成不可中断计算，因而误拦截 `images_done` 和 `whole_audio_aligned`。
4. 图片完成回调无条件写回 `images_done/75%`；当音频对齐先完成时，后完成的图片
   会把 `whole_audio_aligned/82%` 倒退回较早阶段。

## 解决思路

- 把“默认端到端生成”放进共享任务层，而不是让 worker 伪造 HTTP 请求。
- 用数据库 CAS 抢占 ready 父任务，并在创建子任务前检查图片、整篇 WAV、时间轴和
  已有视频任务，保证图片先完成与音频先完成两种竞态都只投递一次。
- 保留页面手工重做入口；自动首轮使用服务端默认渲染参数。
- 用 watchdog 对账历史 ready 任务，复用已有图片/WAV，不删除或重写业务素材。

## 实际方案

- 重启门禁明确把 `images_done`、`whole_audio_aligned` 和
  `whole_audio_alignment_failed` 归类为无活动 worker 的 ready 状态。
- 图片聚合完成只允许推进仍处于素材/图片阶段的父任务；音频对齐、视频排队、渲染和
  终态不再被图片回调覆盖。
- 图片完成进度使用单调更新，不再把已有 80% 降到 75%；恢复图片失败状态时清除旧
  `completed_at`。
- 新增幂等自动续跑器：素材齐全时 CAS 抢占父任务，创建
  `report_stick_figure_video_render` 子任务，提交前释放数据库 Session，并把父任务
  推进到 `video_queued`。
- 图片完成和整篇音频对齐完成都会调用同一个续跑器，覆盖两种完成先后顺序。
- Watchdog 每轮有界检查历史 ready 工作流：已有完整时间轴的直接补投视频；缺少
  音轨记录的旧任务复用既有自动对齐入口，完成后再自动成片。
- render 服务被人工暂停时，Watchdog 不补投视频；失败按任务收口，避免静默卡住。

## 验证

- 第一阶段定向回归：`91 passed`。
- 端到端补丁定向回归：`122 passed`，覆盖真实内存数据库中的“只创建一个最终视频
  子任务”、图片/音频两种竞态续跑入口、Watchdog 修复入口和重启状态分类。
- 完整 unit：`983 passed, 3 skipped, 53 subtests passed, 1 failed`；唯一失败为
  既有前端 cache-bust 标记断言，与本修复无关。
- `compileall`、`git diff --check` 和 Bug 记录严格检查通过。
- 修复后真实门禁不再列出 11 个视觉 ready 容器；当前只拦截一个刚启动的 ZSXQ
  系统同步，证明门禁仍能阻止真实活动任务的重启。
- 10:08 完整重启成功，后端新 PID `31911`，`/health` 返回 healthy。
- 10:12 真实对账：原 11 个 ready 任务已有 4 个进入 `video_queued`、5 个进入
  `whole_audio_alignment_queued`、1 个进入 `transcribe_whole_audio`，剩余 1 个
  `images_done` 等待下一轮有界 reconciler；重启后已创建 4 个最终 render 子任务。

## 复发预防

1. 长生命周期父容器必须明确区分 active、ready/waiting 和 terminal。
2. 重启门禁应按阶段是否存在不可中断 worker 工作判定，不能只看
   `status=processing`。
3. 所有异步子阶段回写必须满足状态单调性，较早阶段不得覆盖音频、视频或终态。
4. 名称或页面承诺“端到端”的编排必须用契约测试证明最终业务产物自动落地；中间
   产物 ready 不能被当成成功终点。
5. 每个自动阶段迁移必须同时有实时 completion hook、幂等 CAS 和历史 reconciler，
   避免竞态或部署窗口永久丢失下一跳。

## 后续事项

- 验收 11 个历史任务按已有产物自动补齐音频时间轴与最终视频；活动任务期间不得
  强制重启。
