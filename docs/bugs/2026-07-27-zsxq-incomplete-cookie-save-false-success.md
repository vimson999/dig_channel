# 2026-07-27 知识星球不完整 Cookie 被误报保存成功

## 基本信息

- 状态：代码与回归已完成，待新凭据真实同步验收
- 严重级别：P1
- 影响模块：平台凭据 / ZSXQ 同步 / Cookie JSON 解析
- 首次发现：2026-07-27
- 修复提交：未提交
- 根因分类：输入语义校验缺失、保存与消费解析合同不一致
- 复发键：ZSXQ-CREDENTIAL-SEMANTIC-VALIDATION
- 是否复发：首次
- 关联记录：`2026-07-10-zsxq-sync-stale-credential-binding.md`
- 防线层级：shared_guardrail / contract_test

## 现象

用户编辑同步凭据并看到“DB Cookie 已保存”，接口也返回 200，但数据库中的新内容只有
`sensorsdata2015jssdkcross`，缺少同步必需的 `zsxq_access_token`。随后所有同步入口仍然失败。

## 影响

- 页面把不可用凭据误报为保存成功。
- 调度器要等到实际触发时才暴露错误，造成重复失败邮件和同步空窗。
- 字典形式的 Cookie JSON 能通过结构提取器，却不能被 ZSXQ 运行客户端消费。

## 根因

`save_credential` 只检查 payload 非空，不执行知识星球字段语义校验。另一方面，共享凭据层
接受 `{cookie_name: cookie_value}` JSON 映射，而 `ZsxqService.normalize_cookie` 只支持
浏览器导出的 `[{name, value}]`，保存合同与运行合同不一致。

## 解决思路

保存知识星球 payload 时强制复用已有结构校验；运行解析器同时支持 Cookie header、
浏览器数组 JSON、单条 name/value JSON 和键值映射 JSON。

## 实际方案

- 缺少 `zsxq_access_token` 的知识星球凭据直接拒绝保存。
- ZSXQ Cookie 规范化支持字典映射和嵌套 `cookies` 数组。
- 保存成功后仍由真实星球菜单请求完成在线权限验收。

## 验证

- 新增不完整 Cookie 拒绝保存测试。
- 新增映射 JSON 运行时规范化测试。
- 新增完整知识星球凭据保存测试。
- 相关 ZSXQ/凭据/调度器定向回归：`44 passed`。

## 复发预防

- 凭据“非空”不能作为可用判据；每个平台保存时必须检查最低登录态字段。
- 保存层与消费层必须接受相同的凭据格式集合。
- UI 成功提示只能建立在服务端语义校验通过之后。

## 后续事项

- 用包含 `zsxq_access_token` 的完整 Cookie 更新现场凭据。
- 真实验证前沿、洪灝、生财三个星球并补跑同步。
