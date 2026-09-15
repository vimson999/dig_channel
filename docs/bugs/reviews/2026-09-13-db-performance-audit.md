# 2026-09-13 每日数据库性能与错误巡检

## 结论

固定窗口 `2026-09-12 06:00:00` 至 `2026-09-13 06:00:00`（Asia/Shanghai）内，应用 SQL 错误、
traceback、五类数据库 timeout/deadlock、idle-in-transaction 事故与 PgBouncer 六类错误均为 0。慢 SQL
从上一窗口 27 条/70.433 秒降至 14 条/26.437 秒；代表查询的 PostgreSQL executor 为
0.082–37.050ms，现场无锁等待，仍不支持新增索引或调整超时。

本轮发现并修复一个确定性状态一致性缺陷：Publisher upload-only 在 Watchdog 清理竞态后成功完成时没有清除
旧 `error_message`。修复已按 RED→GREEN 落地，完整 1674 项单测通过，并经双安全门标准整栈重启加载。
Publisher queued/unacked 修复则在窗口中出现旧语义运行证据，上一轮运行验收判定为假绿；本轮已重新完整替换，
仍待自然消息验收。

## 与上一窗口对比

| 指标 | 2026-09-12 | 本窗口 | 变化 |
| --- | ---: | ---: | ---: |
| 应用慢 SQL | 27 | 14 | -48.1% |
| 慢 SQL 总耗时 | 70.433s | 26.437s | -62.5% |
| SQL P50 / P95 / max | 1.708 / 7.605 / 13.839s | 1.093 / 3.400 / 4.951s | 全部下降 |
| HTTP 完成 / 成功 | 426 / 408 | 758 / 758 | 失败 18 → 0 |
| HTTP P50 / P95 / max | 8.87ms / 1.538s / 11.784s | 3.06ms / 1.471s / 7.647s | 改善 |
| Celery 完成 / 失败 | 135 / 1 | 205 / 3 | 吞吐上升；3 次为外部平台参数 |
| PgBouncer wait P95 | 55.60ms | 44.62ms | -19.7% |
| PgBouncer query P95 | 98.90ms | 71.85ms | -27.3% |

## 慢 SQL、Session 与页面链路

- 14 条慢 SQL 总计 26.437 秒。按总耗时排序：MP3 音频素材目录 3 次/9.563 秒，通知到期扫描
  2 次/6.028 秒，ZSXQ Insights 聚合 2 次/3.483 秒，Publisher processing 扫描 2 次/2.076 秒；其余
  5 条各约 1.0–1.12 秒。
- 唯一超过 5 秒门槛附近的 Session hold 为通知 scheduler 5.033 秒，其中 SQL 4.951 秒、非 SQL 82.45ms；
  没有跨外部 I/O 持有证据。runtime guard 20 个观测 episode，最大 2 连接、事务龄 0.944 秒、idle 龄
  0.295 秒、defer 0，均不是 60 秒事故。
- 758 个 HTTP 请求全部成功，P50/P95/max 为 3.06ms/1.471s/7.647s。最重端点包括 Bilibili upload
  POST 11 次/33.334 秒、upload 列表 32 次/24.458 秒、MP3 audio-assets 18 次且其中 3 次关联慢 SQL；
  多个上传、封面和 job 端点的秒级耗时没有关联慢 SQL，继续按外部 I/O/网络链路观察。
- 固定窗没有 TTS 404；但也没有足够自然 TTS 分段请求，故只记“未观测”，不关闭旧问题。两次 Insights
  慢查询来自后台冷聚合，没有对应页面 request id。

## Executor、索引与膨胀

- MP3 素材目录真实形状：顺序扫描 1,748 行、返回 671 行、181 buffers，execution 1.951ms；应用为
  3.055–3.400 秒。
- 通知到期扫描命中 bitmap indexes，返回 0 行，execution 0.105ms；应用为 1.077–4.951 秒。
- Publisher processing 扫描 execution 0.082ms；应用为 1.034–1.042 秒。ZSXQ 聚合代表查询返回
  6,707 行，execution 37.050ms；应用为 1.678–1.805 秒。
- 目标表 index-scan 占比为 98.78%–99.95%；`publication_tasks` 134MB、live 13,484/dead 1,028，
  `zsxq_topic_comments` dead 5,644 为最高但尚无膨胀/查询证据。精确重复索引为 0。
- 少量低扫描索引存在，但统计期、写放大和真实查询证据不足；本轮不做 DDL、不删索引、不提高 timeout。

## 任务与 durable 状态对账

- 窗口任务：Publisher 21 completed/3 failed、MP3 workflow 5 completed、PDF clean 4 completed、audio clean
  1 completed/2 failed、ZSXQ publish 3 completed/1 failed、stick image 1 failed；sync run 0。
- 97 条 notification 全部 sent、attempt=1、dedupe 97/97。内容资产 PDF cleaned 34 个、audio cleaned 2 个，
  hash 均唯一且无缺失证据；8 个 audio failed 与现场缺失片头/间隔文件一致。
- 7 个业务失败均可归因到外部输入/配置：Bilibili 定时范围 2、充电档位 1、缺失音频资源 2、Gemini 输入
  文件 1、ZSXQ 发布配置 1；没有数据库错误或 timeout。
- 唯一 `completed` 且带错误的 task `e9fc559e…` 暴露 upload-only 成功分支缺陷，已修复并登记独立 Bug。

## Publisher 根因闭环与改动

- `91b5f0f4…` 的 `queue_repair` 审计记录可见消息仍从 `ac45…` 替换为 `039e…`；CAS loser `8973…`
  随后被正确撤销，`039e…` 最终完成。它未形成连续饿死，但证明旧语义仍在窗口运行。上一轮只看进程
  healthy/ready，未证明所有调用者加载最终文件，故重新标为验收假绿。
- `e9fc559e…` 先被 Watchdog 写 orphan 错误，真实 worker 后完成上传。`run_bilibili_upload_only` 成功窄写
  遗漏 `error_message=None`，形成 `completed + error` 矛盾状态。
- 本轮仅修改上传成功终态清错与对应竞态测试，没有更改 schema、索引、timeout 或外部业务配置。

## 验证与运行替换

- TDD：旧实现复现竞争错误残留；最小修复后目标测试通过，Publisher API + queue repair 134/134 通过。
- 完整 unit：`Ran 1674 tests in 92.417s / OK`。
- 重启前安全门读取 46 条近期 publication task、0 条活动 sync；标准脚本冻结 17 队列并二次门禁通过，
  未使用 FORCE。新 backend PID `46428` healthy，bootstrap 17/17、`errors=[]`。
- MP3、render、content pipeline runtime-status 均 `running=true`、`paused=false`、`queue_length=0`；重启短窗
  无 SQL/连接 timeout、无 Session hold。启动冷聚合一条慢 SQL 1.701 秒，属于上述 executor/网络边界。
- 06:18 最终只读现场为 active 1（本次诊断）、idle 15、idle-in-transaction 0；重启切点后新增
  `completed AND error_message IS NOT NULL` 为 0。

## 诊断边界

- 固定窗口结束后的首次内容资产只读探针误用了不存在的 `source_sha256` 列，立即改为
  `content_sha256` 后完成查询；该 ad-hoc 错误不属于生产窗口或托管服务故障。
- 重启后首次 runtime-status 探针传入了不存在的 `publisher` service id，返回预期 HTTP 400；改用
  `render/mp3_video_workflow/content_pipeline` 后三项均正常。两次探针错误不计入业务失败率。

## 当前状态与下一轮

- Publisher success 清错：`RUNTIME_REPLACED/OBSERVING`；等待自然 upload-only 成功，要求新增
  `completed AND error_message IS NOT NULL` 为 0。
- Publisher queued/unacked：`RUNTIME_REPLACED/OBSERVING`；等待自然 queued/unacked 候选，要求不换 ID、
  不增加 repair count、无 stale-skip 链与 repair-limit 失败。
- PostgreSQL/PgBouncer 网络尾延迟：`RUNTIME_MITIGATED/OPEN/REPEATED/OBSERVING`；错误虽为 0 且指标改善，
  但 executor/应用差距仍在，不以单窗或单次 smoke 关闭。
- 外部输入/平台参数失败：保持显式失败；不伪造资源、不自动改发布时间/充电档位或 ZSXQ 配置。
