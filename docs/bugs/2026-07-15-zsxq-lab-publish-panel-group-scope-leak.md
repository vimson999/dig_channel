# ZSXQ Lab 发布入口泄漏到非君君播客组

## 基本信息

- 状态：已修复
- 严重级别：P1
- 影响模块：ZSXQ Lab / 发布入口 / 分组切换
- 首次发现：2026-07-15
- 修复提交：当前工作区
- 根因分类：Group 专属能力缺少统一作用域守卫
- 复发键：ZSXQ-GROUP-SCOPE-GUARD
- 是否复发：是
- 关联记录：`2026-07-14-zsxq-lab-special-group-controls-leaked.md`
- 防线层级：shared_guardrail / contract_test / browser_regression

## 现象

选择任意知识星球 Group 后，右侧都会出现“发布”面板；实际产品规则是该入口只属于“君君播客”（Group ID `15554855514112`）。

## 影响

非目标 Group 暴露了具有外部副作用的发布能力，用户可能把正文和附件发到错误星球。仅隐藏界面也不足以防止旧页面或直接函数调用越过业务边界。

## 根因

`loadTopics()` 在每次切组时无条件移除发布面板的 `hidden` 类。发布函数本身也只检查“是否选择了 Group”，没有检查该 Group 是否具备发布能力。这与前一天专属下载操作区泄漏属于同一复发键，说明 Group 约束仍分散在各业务组件中。

## 解决思路

用稳定 Group ID 定义能力作用域，并把约束同时放在展示和操作入口；中文名称只用于显示，不能作为权限判断。对目标组、非目标组和往返切换建立契约及浏览器回归验证。

## 实际方案

- 增加发布目标 Group 常量 `PUBLISH_GROUP_ID = '15554855514112'`。
- 增加统一的 `isSelectedPublishGroup()` 和 `renderPublishPanelVisibility()`。
- `loadTopics()` 每次切组都通过统一函数计算面板可见性，不再无条件显示。
- `publishTopic()` 增加 fail-closed 守卫，非目标 Group 不执行发布请求。
- 更新前端脚本版本，避免浏览器继续命中旧缓存。
- 增加前端契约测试，约束常量、切组显示逻辑和发布入口守卫。

## 验证

- `node --check src/frontend/js/zsxq-lab.js` 通过。
- `python3 -m unittest tests.unit.test_frontend_polling_discipline` 通过，共 4 项。
- 浏览器实测：选择“前沿信息收录”时隐藏，选择“君君播客”时显示，再切回“前沿信息收录”后重新隐藏。
- `git diff --check` 通过。

## 复发预防

任何 Group 专属能力必须同时具备三道防线：稳定 ID/能力配置驱动的统一可见性函数、执行入口的 fail-closed 守卫、目标与非目标 Group 的往返切换测试。后续若增加更多专属能力，应收敛为统一的 Group capability map，禁止在加载或重绘函数中直接无条件 `remove('hidden')`。
