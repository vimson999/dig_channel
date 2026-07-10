# 2026-07-10 已完成研报任务的竖屏后补被自动收口

## 基本信息

- 状态：已修复
- 严重级别：高
- 影响模块：研报音频详情页 / 竖屏 MP4 后补 / publisher queue repair
- 首次发现：2026-07-10，publisher 子任务 `777a06f9-58a0-490f-840f-1deccace4fd9`
- 修复提交：待提交

## 现象

用户在已完成的研报音频任务上点击“完整合成”，页面显示：

```text
完整竖屏失败：父编排任务已终止(completed)，视频子任务自动收口。
```

现场数据库同时出现矛盾状态：子任务后来已经 `completed / done / 100%`，竖屏 MP4 也真实存在，但 `error_message` 仍保留自动收口错误，因此页面继续按失败展示。

## 影响

- 已完成父任务无法可靠执行用户主动发起的竖屏后补。
- render 已经生成成功时，页面仍可能误报失败并诱导用户重复合成。
- 收口器和 render worker 并发写同一子任务，产生 `completed + 旧 error_message` 的不一致状态。

## 根因

publisher 子任务收口器默认认为父编排进入 `completed/failed/canceled` 后，所有活跃视频子任务都属于孤儿任务。它只对白名单中的 B站后投上传放行，没有识别来源为 `report_audio_workflow_vertical_backfill` 的竖屏后补任务。

同时 publisher 成功收尾只写入 `completed/done/100%`，没有显式清空此前由收口器写入的 `error_message`。

## 解决思路

区分“父流程内部遗留子任务”和“父流程完成后用户显式发起的后补动作”。只有父任务状态为 `completed` 时允许后补动作继续；父任务 `failed/canceled` 时仍执行安全收口。任何成功终态必须清空旧错误。

## 实际方案

- 将 `report_audio_workflow_vertical_backfill` 加入允许在父任务完成后运行的来源集合。
- 把白名单判断移动到父任务状态查询之后，仅对 `completed` 生效。
- publisher 主流水线写成功终态时同步设置 `error_message=None`。
- 修复现场已生成成功子任务的旧错误状态，不重复渲染现有 MP4。

## 验证

- 父任务 completed 时，竖屏后补不被收口测试通过。
- 父任务 canceled 时，同一来源仍被正常收口测试通过。
- publisher 相关编译检查通过。
- 现场竖屏 MP4 路径存在且可下载，任务状态统一为 completed 且错误为空。

## 复发预防

- 新增任何“父任务完成后可执行”的后补功能时，必须同步登记来源语义与收口策略。
- 成功终态写回必须显式清空 `error_message` 和过期失败标记。
- 状态展示不得只看历史错误；应以当前终态和当前错误字段共同判定。

## 后续事项

- 将“完成后允许动作”从散落字符串升级为集中常量或 capability 字段。
