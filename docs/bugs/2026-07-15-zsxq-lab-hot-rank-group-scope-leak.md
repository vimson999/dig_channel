# ZSXQ Lab 热度排行泄漏到非前沿信息收录组

## 基本信息

- 状态：已修复
- 严重级别：P2
- 影响模块：ZSXQ Lab / 热度排行 / 分组切换
- 首次发现：2026-07-15
- 修复提交：当前工作区
- 根因分类：Group 专属组件缺少统一作用域守卫
- 复发键：ZSXQ-GROUP-SCOPE-GUARD
- 是否复发：是
- 关联记录：`2026-07-14-zsxq-lab-special-group-controls-leaked.md`、`2026-07-15-zsxq-lab-publish-panel-group-scope-leak.md`
- 防线层级：shared_guardrail / contract_test / browser_regression

## 现象

“热度排行”面板在所有知识星球 Group 下始终显示，即使普通组没有该业务需求和排行数据。产品规则是只有“前沿信息收录”（Group ID `28888112822211`）需要该面板。

## 影响

普通组长期显示无意义的空排行，占用右侧空间并错误暗示该能力适用于所有 Group；异步主题重绘还可能覆盖切组时设置的显示状态。

## 根因

排行面板在 HTML 中默认可见，`renderHotRankList()` 只负责填充数据，没有验证当前 Group 的能力作用域。这是同一复发键下连续出现的第三个 Group 专属组件泄漏，说明每个组件必须在初始化、切组和异步重绘三个入口都执行统一守卫。

## 解决思路

默认 fail closed，并复用既有的稳定 Group ID 判断；切组时立即计算可见性，排行重绘时再次校验，避免后执行的异步逻辑恢复错误状态。

## 实际方案

- HTML 中将 `hot-rank-panel` 默认设为隐藏，避免页面加载闪现。
- 增加统一的 `renderHotRankPanelVisibility()`，仅在 `isSelectedSystemSyncGroup()` 为真时显示。
- `loadTopics()` 切组时立即刷新排行面板可见性。
- `renderHotRankList()` 每次重绘都重新校验作用域，普通组直接返回。
- 更新前端脚本版本并增加目标组、普通组守卫契约测试。

## 验证

- `node --check src/frontend/js/zsxq-lab.js` 通过。
- `python3 -m unittest tests.unit.test_frontend_polling_discipline` 通过，共 5 项。
- 浏览器实测页面初始隐藏，选择“前沿信息收录”后排行面板显示。
- 契约测试验证切组入口和排行重绘入口都调用作用域守卫。
- `git diff --check` 通过。

## 复发预防

Group 专属组件必须默认隐藏，并在初始化、切组、异步重绘三类入口统一校验稳定 Group ID；仅在一次切组事件里控制显示不算完整修复。下一步新增 Group 专属能力时，应先登记到统一 capability map，再由通用渲染器管理可见性。
