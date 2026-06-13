# 本地 PDF 清洗迁移说明

## 目标

把 `/Users/v9/Documents/3---work/06---dev---python/pytest` 项目里已经稳定的 PDF 清洗能力完整迁移到当前系统，最终用当前系统本地清洗替代外部 `pytest` 服务。

当前阶段只做迁移与独立测试，不把本地清洗默认接入工作流。工作流仍默认使用 `engine=external`。

## 已迁移内容

- 清洗内核：`src/backend/services/watermark/pdf_processor.py`
- 水印模板资源：`src/backend/services/resources/watermarks/`
- 深度取证：`src/backend/services/watermark/pdf_forensics.py`
- PDF 分析与策略建议：`src/backend/services/watermark/pdf_analyzer.py`
- PDF 拆分：`src/backend/services/watermark/pdf_splitter.py`
- 自主加水印与 AES-256 保护：`src/backend/services/watermark/pdf_protector.py`
- content pipeline 本地执行分支：`PdfCleanOptions(engine="local")`
- 本地健康检查：`PdfCleanerAdapter.check_service(engine="local")`
- 对比测试脚本：`scripts/dev/compare-pdf-cleaners.py`

`pdf_processor.py` 当前与 pytest 项目的 `app/services/pdf_processor.py` 保持一致，包含：

- 全局漂白
- 页眉清理
- 页脚右侧清理
- 页脚红色水印清理
- QR/固定水印模板匹配
- 自定义二维码替换
- Morgan Stanley / Nomura / UBS 识别与差异化策略
- 文本对象 redaction
- 页面并行渲染重构

`pdf_protector.py` 当前迁移了 pytest 项目的 V4 保护策略：

- 页角动态水印
- 字符级随机间距、字号、旋转、透明度
- 65% 概率 ghost layer，降低稳定裁剪/背景修复命中率
- AES-256 owner password 权限锁定
- 默认保留 `西湖醋鱼 + 日期`，也支持显式传入 `watermark_text`

## pytest 日志与设计背景

已阅读 pytest 项目：

- `PROJECT_STATUS.md`
- `HANDOVER_LOG.md`
- `README.md`
- `logs/app.log`
- 相关验证脚本：`process_ms_power.py`、`final_ms_nomura_demo.py`、`batch_clean_protect_demo.py`、`batch_clean_0513.py`

迁移时需要尊重以下历史结论：

- 这是“深度解析 + 智能拆分 + 自主加密 + 极致清洗”的组合系统，不是单一阈值漂白脚本。
- `Threshold=245` 是高盛、大摩等浅灰水印深度清理验证后的关键参数。
- GS/UBS/MS/Nomura 有机构差异化策略，不能用一个通用算法覆盖全部情况。
- QR/固定水印依赖模板资源，多尺度匹配后要扩大擦除区域，避免旧二维码纠错点残留。
- “前沿信息收录”等顽固页脚水印需要物理 white-out。
- 自主保护策略不是简单盖字，而是字符级随机熵、ghost layer、权限加密组合。
- 取证分析用于发现隐藏文字、权限限制和隐形追踪指纹，后续可驱动 text_clean 策略。

## 尚未切入工作流

这些默认值仍保持 external：

- `PdfCleanOptions.engine`
- `submit_pdf_clean_task()` 创建任务时的默认 cleaner engine
- `tasks.content_pipeline.run_pdf_clean_task` 运行时兜底 engine

所以当前自动同步、清洗、发布工作流不会因为本次迁移而改变行为。

## 单体测试

```bash
venv/bin/python -m unittest \
  tests.unit.test_content_pipeline_pdf_local_cleaner \
  tests.unit.test_migrated_pdf_processor
```

## 对比测试

先启动 pytest 项目的 PDF 清洗服务，确保 `http://127.0.0.1:5005/api/health` 或 `folder-tree` 可用。

然后在当前项目运行：

```bash
venv/bin/python scripts/dev/compare-pdf-cleaners.py /path/to/sample.pdf
```

也可以传目录：

```bash
venv/bin/python scripts/dev/compare-pdf-cleaners.py /path/to/pdf-folder --pages 3 --tolerance 1.0
```

脚本会分别运行：

- `engine=local`
- `engine=external`

然后渲染前几页，比较平均像素差异。输出目录默认在：

```text
runtime/content_pipeline/cleaner_compare/
```

## 当前验证记录

2026-06-13 已完成：

- 单体测试通过：`tests.unit.test_content_pipeline_pdf_local_cleaner`、`tests.unit.test_migrated_pdf_processor`
- 保护/取证/拆分测试通过：`tests.unit.test_migrated_pdf_security_tools`
- Nomura 样本对比通过：前 3 页 `max_mean_abs_diff=0.0`
- Goldman Sachs 样本对比通过：前 3 页 `max_mean_abs_diff=0.0`
- UBS 样本对比通过：前 3 页 `max_mean_abs_diff=0.0`

说明：当前测试覆盖了本地迁移版与 external pytest 服务的同页渲染一致性。后续切工作流前，还需要补一批近期真实同步 PDF 的批量对比。

## 切换条件

只有满足以下条件后，才建议把工作流默认切到 `local`：

- 单体测试通过。
- 至少用一批真实研报完成 local/external 对比。
- 对比测试视觉差异在可接受范围内。
- 清洗后的 PDF 能继续进入发布链路。
- content_pipeline worker 在真实任务中稳定运行。
