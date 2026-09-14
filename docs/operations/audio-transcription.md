# 音频转文字

入口：`/audio-transcription.html`，统一导航 → AI 创作 → 音频转文字。

单次上传一个通用中文音频，默认文件上限 2 GiB。`AUDIO_TRANSCRIPTION_MAX_UPLOAD_MB` 可配置 1–8192 MiB；修改后须同步调整上传路由的 Nginx body 上限，另留 multipart 开销。仓库模板默认 2050M，应用实际文件上限仍为 2048 MiB。模板修改不等于外部生产 Nginx 已重载。

后台使用 `report_audio_workflow` 队列、`tasks.audio_transcription.process_job` 消费者和 `PublicationTask.feature=audio_transcription`。API 前缀 `/api/audio-transcriptions`。新增消费者后需要安全重启对应 Worker；新增 API 后需要重启 Flask。

输入位于 `data/uploads/audio_transcription/<id>/`，结果位于 `outputs/audio_transcription/<id>/`。失败可从最近任务重试，输入发生变化则要求重新上传。输出为原始识别文本，不做润色。当前识别模型为 medium，使用通用简体中文提示词。

## 已执行验证

2026-09-14：服务、API/任务集成、任务投影与导航鉴权回归共 31 项通过；Worker app factory 与 Celery revoke AST 门禁通过。

本地 Flask 和 report_audio_workflow Worker 已安全重载。真实测试任务 `f086c7db-b187-489b-ae8d-15f4ccdb1b59` 在 2026-09-14 16:34 完成，`status=completed`、`progress=100`、`error_message=null`。日志确认调用本地 ASR，生成 4 条字幕。浏览器打开任务链接后恢复历史记录，分别显示 TXT 与标准 SRT。

测试音频为人工合成的 7.58 秒通用中文句子，不包含用户私人资料。识别存在同音字误差（“转文字”识别为“拽文字”），应人工校对重要内容。

2026-09-14 16:37：浏览器文件选择并上传同一测试 WAV，新任务 `ab3f6bde-017a-42ed-81ce-081ccad29a5f` 显示完成及纯文本；点击复制显示“已复制”，TXT 下载捕获到浏览器 download 事件。同任务文件锁并发测试通过，第二次领取不调用 ASR。

2026-09-14 16:41：最终版后端与 Worker 再次安全重载，任务 `928e85a9-9b7f-4a87-8a66-b3dc7bae8a39` 已完成，字幕接口返回 4 条 SRT。恢复保护和重试审计已在该运行版本中加载。

边界验证：独立测试实际流式写入 2 GiB 文件成功，多 1 字节拒绝并清理半成品；处理中状态重新投递能完成并复用缓存。常规回归 34 项通过，大文件专项单独执行通过。尚未对生产代理执行 2 GiB HTTP 压力测试，也未杀死线上 Worker 做故障注入；这些验证不能由当前测试替代。

2026-09-14 16:49 收尾验证：含实际 2 GiB 写盘边界的后端回归共 40 项通过；前端自动测试覆盖单飞轮询、隐藏暂停、终态停止、TXT/SRT 切换、复制内容与下载链接。Worker app factory（28 文件）及 Celery revoke（321 文件）门禁通过。安全预检通过后重启本地 Flask（启动器 PID 43756），真实历史接口返回 3 个已完成任务和各自 338278 字节大小；刷新浏览器后显示 0.3 MB、100% 及已有结果。

复现命令：

```sh
RUN_LARGE_UPLOAD_TEST=1 venv/bin/python -m pytest tests/unit/test_audio_transcription_service.py tests/unit/test_audio_transcription_workflow.py tests/unit/test_task_projection_models.py tests/unit/test_shared_navigation_auth_guard.py -q
node --test tests/frontend/audio-transcription.test.cjs
venv/bin/python scripts/dev/check-worker-app-factory-boundary.py
venv/bin/python scripts/dev/check-celery-revoke-boundary.py
```

历史列表在数据库连接释放后，对本页最多 20 个受控源文件读取大小，不为展示大小读取任务 JSON。源文件不可读时显示“原文件不可用”，不影响已有结果预览。

2026-09-14 16:59 最终验收：独立只读评审发现数据库瞬断可能遗留活动任务，已修复并复核。新增故障注入覆盖首次查询、失败写回、恢复后同投递继续完成、预算耗尽收口，以及真实 Celery retry 超过默认重试次数时仍保留投递。后端 44 项测试通过，前端与两项 AST 门禁通过。数据库重试执行预算为 5 次，退避 15–300 秒；预算用尽后不再执行 ASR，只等待数据库恢复以记录失败，随后允许用户手动重试。

最终运行边界：Flask PID 46802、report_audio_workflow Worker PID 47138，均通过项目安全检查后重载，无强制终止。新任务 `cffb6c7f-4cf4-42a4-b1ac-21fea2ac9306` 于 16:59:11 完成，100%、无错误、4 个片段；TXT 102 字节、SRT 234 字节。冷详情仅返回输出字节数与统计，`poll=1` 不读取输出 JSON。浏览器 SRT 下载已捕获 download 事件。
