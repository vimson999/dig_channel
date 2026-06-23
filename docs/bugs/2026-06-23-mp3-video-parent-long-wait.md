# 2026-06-23 MP3 视频编排父任务长等待 render 子任务

## 基本信息

- 状态：已优化
- 严重级别：中高
- 影响模块：`mp3_video_workflow`
- 首次发现：排查研报音频编排父任务长等待问题时横向扫描发现
- 修复提交：待提交

## 现象

MP3 视频编排在提交横屏/竖屏 publisher 视频合成子任务后，会在父编排 worker 内循环等待子任务完成。旧默认等待上限为 `MP3_VIDEO_CHILD_TIMEOUT_SECONDS=2400` 秒。

这不是 detached ORM 报错的同一个直接 bug，但属于同一类生产风险：父 worker 在子任务执行期间长期占坑，降低队列吞吐，也放大数据库连接、session 生命周期和 worker 守护压力。

## 影响

- 单条视频合成耗时较长时，`mp3_video_workflow` worker 可能长时间不能处理后续任务。
- 多条 MP3 编排并发时，父任务和 render 子任务同时占用 worker 资源，系统更容易出现排队和连接池压力。
- 父任务等待逻辑越长，越容易出现旧 ORM 对象、连接释放、Celery 失败状态恢复等边界问题。

## 根因

MP3 视频编排已经具备 `waiting_child`、`deferred_waits` 和延迟恢复机制，但旧策略只有等满长超时后才进入恢复调度。对 publisher 视频这种分钟级重子任务来说，父编排不应该把“等待完成”当成自己的执行内容。

## 解决思路

将 publisher 视频子任务等待改为短暂 inline 检查。若短时间内未完成，父编排写入等待状态并让出 worker，由延迟任务恢复推进。恢复时重新从数据库加载子任务状态，完成则汇总产物，未完成则继续延迟恢复。

## 实际方案

- 新增 MP3 视频编排等待策略默认值：
  - `MP3_VIDEO_PUBLISHER_INLINE_WAIT_SECONDS` 默认 5 秒。
  - `MP3_VIDEO_PUBLISHER_PARENT_RETRY_DELAY_SECONDS` 默认 60 秒。
  - `MP3_VIDEO_PUBLISHER_PARENT_MAX_DEFERS` 默认 180 次。
- 横屏 `landscape_video` 和竖屏 `vertical_video` 使用短等 + 延迟恢复。
- 非 publisher 子任务继续使用 `MP3_VIDEO_CHILD_TIMEOUT_SECONDS`，避免一次性改变所有阶段。
- `waiting_child` 记录补充 `inline_wait_seconds`，便于排障时看清等待策略。

## 验证

- 单元测试覆盖 publisher 视频等待默认值和环境变量覆盖。
- 跑 MP3 视频编排服务相关单元测试，确保标题抽取、Gemini 配置、视频子任务恢复 ID 等既有行为不变。
- 后端语法检查通过。

## 复发预防

- 新增编排不得在父 worker 内长时间等待重子任务。
- 已知重子任务必须默认短等后进入恢复调度。
- 编排标准中维护重子任务等待纪律，新增类似阶段时同步补测试。

## 后续事项

- MP3 Gemini 上传与第一轮响应仍在 MP3 编排 worker 内执行。它不是子任务等待问题，但也是重阶段；如后续 MP3 编排量上来，应评估拆成独立 Gemini 子任务并复用同一套恢复调度。
