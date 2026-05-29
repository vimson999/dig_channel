# 2026-05-26 工作归档

## 知识星球实验台

- 根据 Proxyman/HAR 抓包结果，继续完善 `zsxq-lab` 页面。
- 新增上传发布入口：选中星球后可填写正文、选择多个文件或图片，并发布到当前星球。
- 发布时默认跟随当前官方 tab 的 hashtag，例如在“外资研报”tab 上传时，会自动带上对应标签。
- 优化主题列表展示：创建时间从接口原始 ISO 字符串转换为 `YYYY-MM-DD HH:mm`；附件列表展示 `download_count` 下载次数。
- 优化 Cookie 输入区：已保存 Cookie 时默认收起，只显示脱敏摘要；需要更新时点击“修改”展开。
- 修复“外资研报”tab 数据不准：移除本地关键词分类和本地菜单兜底，tab 只使用官方 `/menus` 返回；官方菜单失败时页面明确报错，不再展示猜测数据。
- Cookie 管理改为多账号 profile：支持保存多个 Cookie、下拉切换、新增、删除；输入框内容必须点击“保存当前 Cookie”才会写入本地。
- 修正 Cookie 保存策略：当当前选中一个 Cookie，但输入框粘贴的是不同 `zsxq_access_token` 时，保存会自动追加新 profile，不再覆盖当前 profile。
- 知识星球上传失败排查：七牛上传失败时返回并记录上游响应 body、文件名、大小、content-type、token policy 摘要，避免只看到 `400 Bad Request`。
- 使用 `/Users/v9/Downloads/0526 花旗 全球/花旗-全球视点...20260522.pdf` 复测上传接口，定位到七牛返回 `incorrect region, please use up-z1.qiniup.com, bucket is: files`。
- 修复七牛上传 endpoint：`files` bucket 优先使用 `https://up-z1.qiniup.com`，并支持从七牛错误响应里提取建议区域 endpoint 自动重试。
- 区域修复后再次复测，七牛区域错误消失；当前卡在知识星球 `/v2/uploads` 返回 `code=1059: 内部错误`，疑似短时间重复请求触发限流/风控，未继续高频重试。
- 修复 `zsxq-lab.html` 对 CDN 的依赖：移除 `cdn.tailwindcss.com` 和 Font Awesome CDN，新增本地 `css/zsxq-lab.css`，避免网络/代理导致 `tailwind is not defined`。
- 补齐 `shared-navigation.js` 通用头在 `zsxq-lab` 页面依赖的本地样式和图标 fallback，避免移除 Tailwind CDN 后通用头/桌面导航不可见。
- 修复本地 CSS 缺少 Tailwind preflight 导致的通用头样式变形：重置 button 默认边框、背景和链接下划线。
- Chrome 本地页面已通过前端保存流程新增一个“备用 Cookie”profile；Cookie 内容未写入代码仓库。
- 视频编辑器背景视频适配策略调整：当背景视频短于音频时循环补足，不再慢放拉伸；背景视频长于音频时仍按音频时长压缩适配。
- 后端新增 `/api/v1/zsxq/publish`：
  - 调用 `/v2/uploads` 申请上传 token。
  - 将文件上传到七牛上传入口。
  - 根据返回结果拆分 `file_ids` / `image_ids`。
  - 调用 `/v2/groups/{group_id}/topics` 发布主题。
- 保持 Cookie 只从前端请求传入，不写入后端日志或数据库。

## 验证

- `python3 -m py_compile src/backend/apis/v1/zsxq.py src/backend/services/zsxq_service.py`
- `python3 -m py_compile src/backend/services/video_editor.py`
- `python3 -m py_compile src/backend/services/zsxq_service.py`
- `node --check src/frontend/js/zsxq-lab.js`

## 后续观察点

- 实际上传依赖知识星球返回的七牛 callback 结构；如果线上返回字段与 HAR 推断不同，需要根据错误日志补充 ID 解析规则。
- 上传会真实发布到星球，测试时建议先用小文件和明确测试正文。
