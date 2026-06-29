# 豆包浏览器朗读 Profile 池与分段并发方案

## 背景

研报转语音编排会把最终口播稿拆成多段交给豆包网页朗读捕获。旧链路按分段串行执行，稳定但慢。为了先验证 2 路并行，同时为未来 N 路扩展打基础，豆包朗读改为 profile 池和窗口并发模式。

## 原则

1. 不在同一个 Chrome profile 内并发打开多个豆包页面。
2. 每个 worker 固定绑定一个独立 `user-data-dir`。
3. 父编排只控制“最多多少段处于排队/运行”，默认 `REPORT_AUDIO_DOUBAO_PARALLELISM=2`。
4. 最终合并 WAV 只按 `chunk_index`，禁止按完成时间、创建时间或返回列表顺序合并。
5. 合并前必须校验 `1..N` 分段完整、每段文本与原始 chunk 一致、WAV 文件存在。
6. 同一段文本已有可用 WAV 时可以复用，避免重复生成音频。
7. 某段失败只重跑该段；已成功段不重复跑。

## Profile 池

准备两个 worker profile：

```bash
DOUBAO_BROWSER_TTS_PROFILE_COUNT=2 ./scripts/dev/prepare-doubao-browser-profile-pool.sh
```

启动两个 worker：

```bash
DOUBAO_BROWSER_TTS_PROFILE_COUNT=2 ./scripts/dev/run-celery-doubao-browser-tts-pool.sh
```

维护某个 worker 登录态：

```bash
./scripts/dev/open-doubao-browser-profile.sh worker_1
./scripts/dev/open-doubao-browser-profile.sh worker_2
```

## 正式编排接入

研报音频编排的豆包阶段使用窗口并发：

- 默认并发：`REPORT_AUDIO_DOUBAO_PARALLELISM=2`
- 上限保护：`REPORT_AUDIO_DOUBAO_MAX_PARALLELISM=8`
- 子任务队列：`DOUBAO_BROWSER_TTS_QUEUE=doubao_browser_tts`

父任务每轮最多补齐 `parallelism` 个 active 子任务。若子任务仍在运行，父任务按现有 deferred resume 机制延后重试；下一轮会识别已有 active/completed 子任务，不重复投递。

## 音频缓存

缓存命中优先级：

1. 当前父任务下，同一 `chunk_index`、同一文本、WAV 存在的成功子任务。
2. 全局最近成功的 `doubao_browser_tts` 子任务，文本 hash 或精确文本匹配，且 WAV 存在。

复用时仍把当前分段标签记录为 `doubao_browser_{index}`，最终合并由当前 `chunk_index` 映射决定。

## 测试页面

测试入口：

```text
http://localhost:8888/doubao-browser-tts-test.html
```

页面会显示：

- worker id
- chunk index / chunk total
- text hash
- 单段 WAV 下载和播放

先用两条短文本验证两个浏览器是否同时启动，再用 5 条文本观察窗口补位和队列积压。

## 风险与观察点

- 同一账号复制多个 profile 仍可能触发账号级风控或人机验证。
- 捕获插件有页面级音频缓存，必须确保每个 worker 独立 profile。
- 如果 `worker_id` 都显示为 `default`，说明没有通过 pool 脚本启动。
- 如果最终音频顺序异常，优先检查 `doubao_browser_order`，而不是子任务完成时间。

## 后续扩展

稳定跑通 2 路后，再复制 `worker_3` 并把 `DOUBAO_BROWSER_TTS_PROFILE_COUNT`、`REPORT_AUDIO_DOUBAO_PARALLELISM` 同步提到 3。扩容必须逐步进行，不要直接拉高到大量 profile。
