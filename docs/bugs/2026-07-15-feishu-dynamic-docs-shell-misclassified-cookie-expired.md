# 2026-07-15 飞书动态文档壳层被误报为 Cookie 失效

## 基本信息

- 状态：已修复，真实 item 已回放
- 严重级别：P1
- 影响模块：ZSXQ Lab / 外链文档 / 飞书 Cookie 抓取
- 首次发现：2026-07-15
- 修复提交：工作区修复，待提交
- 根因分类：外部动态页面抓取与业务语义校验
- 复发键：`FEISHU-DYNAMIC-DOCUMENT-EXTRACTION`
- 是否复发：第二次
- 关联记录：[2026-07-14 飞书 Docs 首页被误判为目标文档正文](2026-07-14-feishu-docs-home-misclassified-as-document.md)
- 防线层级：shared_guardrail / contract_test / observability

## 现象

ZSXQ item `82255482825284252` 的飞书 Wiki 链接在 Chrome 登录态中可以正常打开完整文档，但在 `zsxq-lab.html` 点击“抓取”始终提示 Cookie 失效、无权访问或域名不适用。

下载目录 Cookie 文件与平台凭证数据库逐字段比对后，29 个 Cookie 字段完全一致；后端也确实将该 Cookie 发往目标 `my.feishu.cn` 地址。响应为 HTTP 200，初始 HTML 标题为 `Docs`。

## 影响

- 有效 Cookie 和有权限文档被误判为认证失败。
- 凭证可能被错误标记为失效，用户反复导入同一份 Cookie 仍无法恢复。
- 飞书正文、总结和 TTS 后续流程全部被阻断。
- 失败提示把“动态页面尚未渲染”和“认证失效”混为一类，降低排障准确性。

## 根因

飞书 Wiki 是动态应用。初始 HTTP HTML 只包含标题为 `Docs` 的应用壳，正文由浏览器 JavaScript 和后续接口加载。原抓取器使用 `requests.get()` 后立即抽取初始 HTML；2026-07-14 的防污染修复把 `Docs` 壳层统一拒绝，却错误地把合法动态文档也归类为 Cookie/权限问题。

代码层根因是抓取链路缺少“静态 HTML 壳层 → 浏览器渲染正文”的第二阶段；架构层根因是响应真实性校验只负责拒绝假正文，没有配套能够取得动态正文的执行路径。

## 解决思路

保留静态 HTML 快速路径，避免所有飞书文档都无条件启动浏览器。只有检测到 `Docs` 或模板首页壳层时，才进入隔离的 Playwright 回退：

1. 将当前平台凭证 Cookie 注入目标飞书根域。
2. 等待真实文档标题和正文 block 出现。
3. 对飞书虚拟滚动容器分段滚动，以 `data-record-id` 去重并按渲染顺序收集正文。
4. 浏览器正文仍需经过同一业务真实性校验。
5. 只有明确出现登录页时才归类为认证错误；浏览器依赖、渲染超时和 DOM 合同变化使用独立错误。

## 实际方案

- `external_document_service._fetch_feishu_document()` 检测静态壳层后调用浏览器抓取共享函数。
- 新增 Cookie 根域解析、Chrome/Chromium 选择、动态正文等待、虚拟滚动 block 抽取和不可见字符清理。
- `raw_payload.extractor` 记录为 `browser_dom_v1`，并记录 `rendered_blocks`、最终 URL、初始 HTTP 状态和初始标题，便于现场判断实际使用的抓取路径。
- 保留真实静态正文的 `html_text_v1` 路径。
- 明确保持数据模型不变：正文存入 `external_documents`，item 关系存入 `zsxq_topic_external_documents`。
- 明确保持链接分类不变：知识星球自身链接不是外部文档，不进入飞书抓取表，只作为主题正文/文章链接展示。

## 验证

- `venv/bin/python -m unittest tests.unit.test_external_document_service`：8 项通过。
- 契约测试覆盖：`Docs` 壳层触发浏览器回退；真实静态正文不启动浏览器；飞书与 ZSXQ 混合链接只登记飞书外链。
- 真实 Cookie 浏览器回放：成功抽取文档标题、421 个正文 block、14,848 个规范化字符。
- 真实 item `82255482825284252` 已从 `fetching/failed` 恢复为 `fetched`，`fetch_error` 清空，`extractor=browser_dom_v1`。
- 项目 Bug 纪律检查与完整测试结果见本次交付记录。

## 复发预防

- 将 `FEISHU-DYNAMIC-DOCUMENT-EXTRACTION` 从独立事件升级为复发根因簇。
- 禁止把通用 SPA 壳层标题直接等价为凭证失效；必须先尝试平台对应的动态执行路径。
- 新增静态路径与动态路径双入口契约测试，防止未来只修其中一条路径。
- 通过 `raw_payload.extractor` 和 `rendered_blocks` 提供运行态可观测性。

## 后续事项

- 若飞书 DOM 合同再次变化，优先增加官方 API 抓取适配；内部 Cookie 浏览器抓取作为个人授权账号的兼容路径。
- 后端常驻进程需要重启后才会加载本次浏览器回退代码。
