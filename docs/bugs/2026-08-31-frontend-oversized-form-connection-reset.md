# 本机 ZSXQ 桥接拒绝超大表单时返回连接重置

## 基本信息

- 状态：RUNTIME_E2E / OBSERVING；真实 loopback HTTP 回归已通过，待后续完整测试窗口确认不复发
- 严重级别：P2
- 影响模块：`src/frontend/server.py` / ZSXQ sidebar submit bridge / HTTP 413
- 首次发现：2026-08-31 数据库巡检最终完整测试门禁
- 修复提交：当前工作区
- 根因分类：HTTP 请求体拒绝、未读 socket 数据、平台相关 TCP reset
- 复发键：独立观察；未形成重复根因簇
- 是否复发：否；同一验证中稳定复现两次后完成根因修复
- 防线层级：bounded_request_drain / explicit_413 / no_backend_forward / real_loopback_test

## 现象

`test_submit_rejects_oversized_form_without_contacting_the_backend` 原本要求超过 `512KiB` 上限 1 字节的
表单得到 HTTP 413，但完整测试和单独复核连续两次都收到
`urllib.error.URLError: [Errno 54] Connection reset by peer`。后端未被调用，拒绝动作存在，但调用方拿不到
稳定、可解释的 413 合同。

## 影响

- 合法客户端提交略超上限表单时可能只看到网络断开，无法区分负载过大、服务不可用或代理故障。
- 连接重置使原有 413 回归成为平台/时序相关的 flaky test，污染完整测试门禁。
- 若为了让测试通过而放宽请求体上限或接受 reset，会削弱服务端输入边界与用户可诊断性。

## 根因

路由只检查 `Content-Length`，发现超限后立即发送 413 并关闭处理函数，却没有消费客户端已经在发送的
请求体。macOS 在 socket 仍有未读入站数据时关闭连接会发送 RST；`urllib` 仍处于 `sendall` 阶段，因此
看见连接重置而不是服务端已经写出的 413 响应。

## 解决思路

保持 512KiB 上限和“不转发后端”合同不变；在明确超限后只消费一个严格有界的拒绝前缀，最多
`MAX_FORM_BODY_BYTES + 1` 字节，再返回 413。这样覆盖正常“刚超限”客户端，且恶意超大
`Content-Length` 不会导致无界读取。

## 实际方案

- 将超限和空请求分开处理；超限分支先读取
  `min(content_length, MAX_FORM_BODY_BYTES + 1)`，再返回 `request_body_too_large`/413。
- 不提高上限、不解析超限表单、不调用 ZSXQ backend，也不把连接重置加入允许结果来弱化测试。
- 注释记录 macOS ECONNRESET 根因和有界读取的安全约束。

## 验证

- 修复前完整套件和独立用例各稳定失败一次，均为 `[Errno 54] Connection reset by peer`。
- 修复后原症状回归 `1/1 OK`；整个 bridge 文件 `20/20 OK`，覆盖健康检查、CSRF、内容类型、后端转发
  和超大请求不触达 backend。
- 正确 `PYTHONPATH` 与 loopback 权限下完整 unit：`Ran 1496 tests in 75.952s`、`OK`。
- 该回归使用真实 `ThreadingTCPServer`、真实 loopback socket 和 `urllib`，不是 mock HTTP 状态。
- 持久前端桥接进程核对为项目 PID 文件记录的旧进程 `30654`，精确 `TERM` 后端口已释放；通过项目
  `scripts/dev/ensure-zsxq-sidebar-bridge.py` 启动新进程 `88329`，未使用强杀，也未重启 backend/worker。
- 新进程真实 loopback 验收：健康页返回 `200` 且包含 `healthy`；发送 `512KiB + 1` 字节表单得到
  `413`、188 字节响应且包含 `request_body_too_large`，没有连接重置。代码与实际常驻进程均已替换。

## 复发预防

- 任何基于 `Content-Length` 的提前拒绝都必须同时定义未读请求体策略；不得假设写出错误响应等于客户端
  一定能收到响应。
- 拒绝时的 drain 必须严格有界；不能为了稳定状态码读取攻击者声明的任意大请求体。
- 合同必须同时断言明确状态码和外部 backend 零调用，不能只接受 reset 或只检查没有转发。

## 后续事项

- 后续完整测试继续运行真实 512KiB+1 loopback 用例；若再次出现 reset，记录操作系统、协议版本和实际
  已发送字节，不得简单放宽断言。
- 未观察到后续独立窗口前保持 OBSERVING，不标记 CLOSED。
