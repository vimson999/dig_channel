# Knowledge Planet Sidebar HTML Bridge Design

**Date:** 2026-08-17

## Goal

让 ChatGPT Pro 的 Chrome 侧边栏在无法直接导航本地 JSON API、也不能可靠读取 Downloads 目录时，仍能通过可渲染 HTML 页面完成知识星球 MP3/PDF 的最近三天去重查询和幂等 manifest 提交，并继续进入现有入库、清洗、发布和邮件通知流程。

计划任务在人工验证两个 skill、随后完成一次 Codex 调用验证之前继续保持暂停。

## Evidence and constraints

- 侧边栏可以打开 `http://localhost:8888/index.html`。
- 侧边栏已经实测可以打开 `http://localhost:8888/zsxq-sidebar-bridge.html`，并读取 `healthy` 和版本号。
- 直接导航原始 JSON 路径会被 Web preview 以 `ERR_BLOCKED_BY_CLIENT` 拦截，即使 8888 实际已经返回 HTTP 200。
- 5001 已经实现固定业务范围、最近三天历史、内联 manifest、持久化幂等、Celery、清洗、发布和通知。
- 8888 只负责浏览器兼容与安全收口，不复制 5001 的导入业务逻辑，也不成为通用本地代理。

## Chosen architecture

在现有 8888 前端服务器中增加两个可渲染 HTML 能力，并保留现有健康页：

1. `GET /zsxq-sidebar-history.html?kind=mp3|pdf`
   - 校验 `kind` 只能是 `mp3` 或 `pdf`。
   - 由 8888 使用禁用环境代理的本机连接，向 5001 的 history API 发送服务端固定请求。
   - `days` 固定为 3；`group_id`、`tab_id` 由 8888 根据 `kind` 选择，侧边栏不能覆盖。
   - 把后端状态、完整性、下载记录和原始响应转义后渲染为 HTML；不得把原始 JSON 作为导航响应。

2. `GET /zsxq-sidebar-submit.html?kind=mp3|pdf`
   - 返回一个原生 HTML 表单。
   - 表单只包含固定媒体类型、隐藏 CSRF 令牌、manifest JSON 文本框和明确的提交按钮。
   - 页面不通过 JavaScript `fetch` 调用本地接口，避免再次触发 Web preview 的请求策略。

3. `POST /zsxq-sidebar-submit.html`
   - 只接受原生表单提交并限制请求体大小。
   - 校验 CSRF、`kind`、manifest JSON 对象、稳定 `run_id`、固定 `group_id/tab_id/tab_label` 和最多 40 个文件。
   - 由 8888 生成 `Idempotency-Key: zsxq-sidebar:<kind>:<run_id>`。
   - 由 8888 强制加入 `dispatch_clean=true` 和 `auto_publish.enabled=true`，侧边栏不能关闭或改写。
   - 转发给 5001 的内联 browser-import API，并把 HTTP 状态、任务 ID、重放状态、manifest 路径和完整转义响应渲染为 HTML。

## Fixed scopes

- MP3：`group_id=28888112822211`、`tab_id=88844545452542`、`tab_label=调研纪要`。
- PDF：`group_id=28888112822211`、`tab_id=51184248544214`、`tab_label=外资研报`。
- 历史窗口固定为最近 3 天。
- 单次 manifest 最多 40 个文件；空 `files` 合法并且必须提交。

下载阈值、48 小时扫描窗口和最多扫描 40 组仍由对应 sidebar skill 执行；5001 保持最终业务校验权。

## Data flow

### History

1. sidebar skill 打开对应 history HTML URL。
2. 8888 组合固定请求并调用 5001 history API。
3. 8888 返回 HTML 结果。
4. skill 只在页面显示后端 `success=true`、`complete=true` 时继续；否则失败关闭，不扫描或下载。

### Submit

1. skill 完成扫描和浏览器下载后，保留本轮唯一 `run_id` 和 manifest。
2. skill 打开对应 submit HTML URL，把同一份 manifest JSON 填入表单并提交。
3. 8888 校验请求，生成稳定幂等键并调用 5001。
4. 5001 持久化 manifest 和 submission receipt，按现有机制投递 Celery。
5. 8888 返回 HTML 结果；skill 从页面读取 `task_id`、`replayed`、`manifest_path` 和错误详情。

如果首次提交的响应丢失，skill 必须复用完全相同的 `run_id` 和 manifest 再次提交。5001 返回同一个逻辑任务；同一 `run_id` 搭配不同内容返回 HTTP 409，不产生第二条工作流。

## Security boundaries

- 8888 和 5001 继续只监听 loopback。
- 只实现上述固定路径，不接受任意上游 URL、任意后端路径或任意本地文件路径。
- bridge 页面不返回 `Access-Control-Allow-Origin: *`；现有其他前端路由的 CORS 行为保持不变。
- 提交页使用进程级随机秘密生成并验证不可预测的 CSRF 令牌。
- HTML 页面统一设置 `Cache-Control: no-store`，所有后端内容在嵌入页面前进行 HTML 转义。
- manifest 请求体设定明确上限；超限返回 HTTP 413。
- 8888 的预校验只用于缩小攻击面；5001 仍执行完整权威校验。

## Error handling

- 非法或缺失 `kind`：HTTP 400 HTML 错误页，不调用 5001。
- history 后端不可达、超时或非 JSON：HTTP 502 HTML 错误页，skill 必须停止。
- history 返回不完整：HTML 明确显示 `complete=false`，skill 必须停止。
- CSRF 无效：HTTP 403，不调用 5001。
- 请求体过大：HTTP 413，不调用 5001。
- manifest JSON、scope 或 `run_id` 无效：HTTP 400，不调用 5001。
- 5001 返回 4xx/5xx：保留其状态语义并渲染结构化错误，不把失败伪装成成功。
- 5001 返回 200/202：显示任务 ID 和是否为幂等重放。

## Skill integration

桥接页面通过人工测试后，两个 skill 统一改为：

1. 仅检查当前知识星球标签页是否正确；skill 自身不导航或切换 Chrome profile。
2. 开始扫描前打开对应 history HTML 页面，并以 3 天完整历史去重。
3. 回到当前知识星球标签页完成扫描和下载。
4. 打开对应 submit HTML 页面，填写并提交内联 manifest；即使下载数为 0 也提交。
5. API 暂时失败时只允许使用原 `run_id` 和原 manifest 重试一次。
6. 不读取、写入或枚举 Downloads 中的 JSON；不再使用 PDF 授权码。

## Testing strategy

所有自动化桥接测试使用真实本地 HTTP 测试服务器和假 5001 后端，不连接真实数据库、broker 或 Celery。

测试覆盖：

- history 成功时发送精确固定 payload，并渲染可读 HTML。
- MP3/PDF scope 映射正确，非法 kind 不访问后端。
- history 不可达、非 JSON、后端错误和 `complete=false` 均显式失败。
- submit GET 返回固定 kind、CSRF 和原生表单。
- submit POST 强制加入清洗/发布参数，并生成稳定幂等键。
- 相同 manifest 的两次提交使用相同幂等键并显示同一假任务 ID。
- CSRF、请求体上限、JSON、scope、`run_id` 和 40 文件边界。
- bridge 路由不暴露通配 CORS，后端文本被正确转义。
- 现有健康 JSON、健康 HTML 和普通静态首页继续通过回归测试。

每项生产行为先增加一个会因该行为缺失而失败的测试，再写最小实现。

## Rollout

1. 使用假后端完成 history 和 submit 的红绿测试，不触发真实 Celery。
2. 重启且只重启 8888 前端服务。
3. 由用户在同一侧边栏手动验证 MP3/PDF history HTML 页面。
4. 更新并验证两份 sidebar skill，保持计划任务暂停。
5. 由用户分别手动运行 MP3/PDF skill，核对任务 ID 和幂等结果。
6. 由 Codex 执行一次前置定位和短指令调用测试。
7. 三层测试均通过后，才恢复每四小时计划任务。

## Acceptance criteria

1. ChatGPT Pro 侧边栏可以通过 HTML 页面获取 MP3/PDF 最近三天完整历史，无需 Downloads 权限或 MCP Tunnel。
2. 侧边栏可以通过原生 HTML 表单提交空或非空 manifest，并获得 5001 返回的任务 ID。
3. 同一 manifest 重试不创建第二个 Celery 逻辑任务、同步 run、工作流或通知链；内容冲突明确返回 409。
4. history 或 submit 失败时不盲目下载、不报告假成功。
5. 8888 不暴露通用代理、任意路径或跨域写入口。
6. 原知识星球 API 上游和现有 `manifest_paths` 导入方式不变。
7. 两个 skill 使用完全相同的桥接机制，仅固定 scope 和下载阈值不同。
8. 计划任务在人工及 Codex 测试全部通过前保持暂停。
