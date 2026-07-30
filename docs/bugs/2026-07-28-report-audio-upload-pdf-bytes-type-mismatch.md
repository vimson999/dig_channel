# 2026-07-28 上传 PDF 字节数字段类型不一致阻断方案三

## 基本信息

- 状态：已修复，运行态验收进行中
- 严重级别：P1
- 影响模块：研报音频详情 / 视觉工作流联动 / 方案三竖屏
- 首次发现：2026-07-28
- 修复提交：待提交
- 根因分类：数据库投影类型契约
- 复发键：PUBLICATION-TASK-PROJECTION-TYPE
- 是否复发：首次
- 关联记录：`2026-07-21-media-projection-bulk-update-drift.md`
- 防线层级：shared_guardrail / contract_test

## 现象

上传 PDF 的研报任务已完成横屏视觉工作流，但详情状态接口和方案三联动查询返回 PostgreSQL 错误：`operator does not exist: character varying = integer`。

## 影响

详情页轮询 500，用户选择方案三时无法解析同一 PDF 的视觉分镜和图片，必须绕过正常接口才能生成竖屏成片。

## 根因

`publication_tasks.report_audio_pdf_bytes` 是 `varchar(40)` 投影列；三个同 PDF 联动查询把 ORM 值强转为 Python integer 后再与 varchar 列比较。SQLite 测试不会暴露 PostgreSQL 的严格类型错误。

## 解决思路

同一 PDF 身份仍使用“附件 ID 优先，否则文件名 + 精确字节数”，但字节数必须按数据库投影的规范字符串比较，不能依赖数据库隐式转换。

## 实际方案

- 抽取共享 `_linked_pdf_bytes_identity`，仅接受正整数并返回规范十进制字符串。
- 场景时间轴、视觉视频路径和视觉工作流列表三个入口统一复用。
- 无效或非正字节数继续拒绝文件名兜底，避免错误关联。

## 验证

- 单元测试覆盖 integer、numeric string 和非法字符串三种输入。
- 定向回归 154 passed、9 subtests passed。
- 待新后端加载后用真实上传 PDF 的 status API 和方案三接口复验。

## 复发预防

- 跨任务投影字段比较必须对齐模型列类型。
- PostgreSQL 特有类型契约用规范化函数和参数类型测试固定，不以 SQLite 隐式转换通过作为完成条件。

## 后续事项

- 新进程中复验两个上传 PDF 的详情轮询、视觉工作流识别和方案三投递。
