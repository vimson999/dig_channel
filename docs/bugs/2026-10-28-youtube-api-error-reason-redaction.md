# 2026-10-28 YouTube 全量同步失败被泛化且异常文本可能泄露 API key

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING
- 严重级别：高
- 影响模块：YouTube channel/video scheduler / 外部 API 错误分类 / 工作流通知
- 首次发现：2026-10-28
- 修复提交：待提交
- 根因分类：外部调用错误被吞 / 敏感 URL 异常文本未净化
- 复发键：`EXTERNAL-CALL-RESILIENCE` / `REGRESSION-ACCEPTANCE-INTEGRITY`
- 是否复发：既有 YouTube 外部链路问题的新失败形态
- 关联记录：`2026-09-20-youtube-sync-unavailable-channels-repeat-failure.md`、`2026-07-28-youtube-sync-held-db-session.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

2026-10-28 00:03 的 channel 自然任务由上一窗 49/51 恶化为 0/51，12.83 秒内全部返回“无法获取频道统计
数据”；01:00 的 video 自然任务同样 0/51，21.92 秒内全部返回“无法获取频道上传播放列表”。两条独立链同时
全量失败，但日志和通知没有保留 HTTP status、Google reason 或网络异常类型。08:33 使用当前项目凭据只读探测
又成功，说明故障已恢复，无法事后从泛化文本区分瞬时网络、运行体配置、403 或 quota。

## 影响

- 全部 51 个来源在两个每日任务中失去更新，失败率 100%，固定失效的 id 21/56 也被全量故障覆盖。
- 无错误分类时容易误停正常频道、盲目加重试或把外部故障误归为数据库性能问题。
- `requests.HTTPError` 的原始字符串可能包含带 `key=` 的完整请求 URL；若直接写日志会泄露 API key。

## 根因

`YouTubeAPI.get_channel_statistics()` 与 `_get_channel_uploads_playlist()` 捕获所有异常后只 `print` 并返回
`None`，上层再统一生成泛化消息。`_make_request()` 又把 `str(RequestException)` 拼进业务异常；requests 的
异常字符串可能包含完整 URL 和查询参数。结果是“需要的 reason 被吞、不能记录的密钥反而可能被带出”。

## 解决思路

不增加重试、不改变来源状态，先建立安全且可关联的失败合同：HTTP 错误只保留 endpoint、status、Google
reason 和截断后的 message；网络错误只保留异常类型与 endpoint。使用线程本地错误槽，把当前 worker 的净化
原因传到 channel/video 结果、scheduler warning 和通知，避免并行 5 线程串线。

## 实际方案

- `_make_request()` 分开处理 HTTP 与其他 RequestException，抛出不含 URL/API key 的结构化安全消息。
- 新增线程本地 `consume_last_error()`；频道统计与上传列表失败时保留净化原因，读取后立即清空。
- channel/video 业务失败消息携带安全 upstream reason，既有 Session 释放顺序与有限并发不变。
- 保留永久失效频道 21/56 的人工 DBeaver 脚本；本轮没有扩大停用范围。

## 验证

- TDD 红灯：旧异常包含 `?key=super-secret-key`，且视频失败只有泛化文本。
- 定向测试 12/12 通过：403 `quotaExceeded` 原因保留、API key 不出现、错误槽消费后清空；频道/视频
  Session-before-I/O 合同继续通过。
- 标准重启安全门两次确认无不可中断任务，未使用 FORCE；backend PID 68271，bootstrap 17/17 Celery、
  Watchdog、6/6 scheduler `errors=[]`，YouTube 新 PID 70687/70811。
- `/health` healthy；当前只读 API 连接测试成功。由于下一自然任务尚未发生，不能写 CLOSED。

## 复发预防

- 外部 API 错误不得记录含 credential 的 URL；测试必须用伪密钥断言输出不含 `key=`。
- 批量失败必须保留 provider reason/异常类型并按任务、频道和时间聚合，不能只报失败数。
- 100% 批量失败不得自动停用全部来源；永久来源失效与瞬时 provider 故障必须分开处理。
- 下一自然 00:03/01:00 周期必须验证错误分类真实进入日志/通知，且 Session/事务没有跨远端 I/O。

## 后续事项

本次全量故障的历史根因因旧日志缺失只能保持 OPEN/UNKNOWN-UPSTREAM。下一次若复发，按 status/reason 立即
区分 quota、credential、network 和空结果，并关联运行体 PID/启动时间；至少一个后续自然绿窗后才能接受。

## 2026-10-29 自然周期运行验收

00:03 channel 与 01:00 video 两条自然任务均真实输出
`API network failure endpoint=channels type=SSLError`，分别 0/51，日志中未出现 API key。由此，安全 reason
传播、线程隔离和密钥脱敏合同已经通过自然运行验收，可将本记录的诊断修复维持 **RUNTIME_REPLACED**。

但这两条任务也证明关联的 YouTube 业务故障再次发生；窗后代理/直连及真实凭据探测恢复，只能说明故障间歇，
不能说明 TLS/代理/网络根因已修复。业务问题继续由
`2026-09-20-youtube-sync-unavailable-channels-repeat-failure.md` 以 **OPEN / REPEATED / WAITING_NETWORK_TRACE**
跟踪；下一次故障须同秒保存 proxy/direct TLS 阶段和运行体身份。
