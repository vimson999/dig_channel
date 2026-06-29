# 2026-06-24 Content Pipeline 过期大文件因父目录 mtime 较新未被清理

## 基本信息

- 状态：已修复，需重启历史文件清理调度器后生效
- 严重级别：中
- 影响模块：`content_pipeline` 历史文件清理
- 首次发现：用户检查 `runtime/content_pipeline/batches` 时发现仍存在超过 7 天的 MP3 文件
- 修复提交：待提交

## 现象

`runtime/content_pipeline/batches/f0642db7-9103-4726-ac37-818b61210de0` 下仍存在 2026-06-15 生成的 MP3 文件，例如：

```text
runtime/content_pipeline/batches/f0642db7-9103-4726-ac37-818b61210de0/audio_cleaned/.../260615-油价回落航空修复预期增强持续关注中东协议签署情况.mp3
```

该批次大小约 `415 MB`。

计划任务配置显示：

```text
dry_run=false
content_pipeline_batch_days=3
last_run_at=2026-06-24T00:43:01Z
last_result.deleted_human=4.6 GB
```

也就是说，清理任务确实执行过一轮，但仍漏掉了这个旧批次里的大文件。

## 影响

- 用户会看到“超过 7 天的大文件仍然存在”，与“历史文件已清理”的预期冲突。
- 长期运行时，旧 MP3/PDF 可能因为目录被后续流程碰过而继续占用磁盘。
- 当前实现还可能删除整个 batch 目录，和“保留 JSON/manifest 元数据，只清理大文件”的约定不一致。

## 根因

旧实现对 `runtime/content_pipeline/batches/*` 使用目录级清理：

1. 扫描 batch 目录。
2. 计算目录内所有子文件/子目录的最新 mtime。
3. 最新 mtime 超过保留期后，删除整个 batch 目录。

现场批次里 MP3 文件是 2026-06-15，但其中 `files` 子目录 mtime 是 2026-06-21 16:17。

因此服务判断整个批次最新年龄只有约 `2.94` 天，未进入 `3` 天清理候选，导致 6 月 15 日的大文件被漏掉。

## 解决思路

清理规则应该和业务目标一致：

- 大文件按文件自身 mtime 进入候选。
- JSON、manifest、小文本元数据默认保留。
- 近期未收尾任务引用的路径仍受保护窗口保护。
- 删除大文件后只裁剪空目录，不强删仍包含元数据的 batch 目录。

## 实际方案

`src/backend/services/content_pipeline/retention_cleanup_service.py`：

- 新增 `content_pipeline_batch_file` 候选类别。
- `runtime/content_pipeline/batches` 改为递归扫描大文件后缀：
  - PDF
  - MP3/M4A/WAV/AAC/FLAC/OGG
  - Office 文档
  - ZIP
- 以文件自身 `st_mtime` 判断是否超过 `content_pipeline_batch_days`。
- 删除时只删除文件，并向上裁剪空目录，停止在 `runtime/content_pipeline/batches`。
- 保留 JSON、manifest 等元数据文件。

测试：

- 更新旧测试，断言 content pipeline 清理删除的是过期大文件而不是整个 batch 目录。
- 新增回归测试：父目录 mtime 很新，但内部 MP3 很旧时，旧 MP3 仍必须进入候选。

文档：

- `docs/CONTENT_PIPELINE_RETENTION.md` 明确记录 content pipeline 批次按大文件自身 mtime 清理，并保留 JSON/manifest。

## 验证

单元测试：

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_content_retention_cleanup_service
```

结果：`Ran 7 tests ... OK`。

语法检查：

```bash
venv/bin/python -m py_compile src/backend/services/content_pipeline/retention_cleanup_service.py src/backend/services/content_pipeline/retention_scheduler_service.py src/backend/run_content_retention_cleanup_scheduler.py
```

现场 dry-run：

```text
candidates 164 2.0 GB
target_hits 19
target_total 435573674
```

旧批次 `f0642db7-9103-4726-ac37-818b61210de0` 下的 2026-06-15 MP3 已进入候选，类别为 `content_pipeline_batch_file`；JSON/manifest 不进入候选。

现场实际清理：

```text
deleted_count=113
deleted_human=1.6 GB
errors=0
```

清理后复查：

```text
runtime/content_pipeline/batches/f0642db7-9103-4726-ac37-818b61210de0: 68 KB
content_pipeline_candidates=0
```

该批次下 MP3/PDF/WAV/M4A 等大文件已清空，仅保留 `audio_clean_manifest.json`、`manifest.json` 和小文本清单。

调度器：

```text
running=True
next_run_at=2026-06-25T06:55:59Z
```

调度器已重启，后续计划任务会加载新规则。

## 复发预防

- 文件清理类需求必须明确“按文件时间”还是“按工作区目录时间”。
- 用户明确要求保留元数据时，不能用整目录删除作为主要策略。
- 每次新增 retention 规则时必须包含“父目录较新、内部大文件过期”的回归测试。

## 后续事项

- 由用户确认后，可立即执行一次真实清理；否则等待下一次计划任务按新规则执行。
