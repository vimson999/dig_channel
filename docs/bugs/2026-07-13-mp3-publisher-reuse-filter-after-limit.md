# 2026-07-13 MP3 publisher 复用查询在 limit 后追加 filter

## 基本信息

- 状态：已修复
- 严重级别：P1
- 影响模块：MP3 视频编排 / publisher 资产复用
- 首次发现：2026-07-13
- 修复提交：待提交
- 根因分类：SQLAlchemy 查询构造顺序错误
- 复发键：`OUTPUT-SEMANTIC-VALIDATION`
- 是否复发：首次
- 防线层级：local_patch / contract_test

## 现象

数据库断连任务续跑后在 90% 失败，错误为 `Query.filter() being called on a Query which already has LIMIT or OFFSET applied`。已完成横屏视频未被正常识别。

## 根因

publisher 候选查询先调用 `.limit(30)`，随后根据字幕开关和音频签名追加 `.filter()`。SQLAlchemy 禁止在已有 LIMIT/OFFSET 的 Query 上追加过滤条件。

## 实际方案

- 先完成父任务、角色、画幅、字幕开关和音频签名的全部过滤。
- 最后统一执行 `.limit(30).all()`。
- 不改变精确匹配和视频复用语义。

## 验证

- MP3 service 测试通过。
- 原任务续跑时复用已完成横屏，只重新创建竖屏子任务。

## 复发预防

- ORM 查询必须按 `filter -> order_by -> limit -> materialize` 顺序构造。
- 新增可选过滤条件时必须检查查询是否已经应用 LIMIT/OFFSET。
