# MP3 媒体下载在全局缓存锁等待时持有数据库 Session

## 基本信息

- 状态：新后端已加载，待真实并发下载验收
- 严重级别：P0
- 影响模块：MP3 视频详情 / 媒体下载 / B站上传状态轮询 / publication_tasks / PgBouncer
- 首次发现：2026-08-06
- 修复提交：待提交
- 根因分类：鉴权 Session 跨线程锁与文件传输 / 状态轮询放大
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是
- 关联记录：`2026-07-13-mp3-hot-path-json-payload-regression.md`、`2026-07-18-publication-task-progress-json-write-amplification.md`、`2026-08-05-report-audio-hidden-tab-polling-n-plus-one.md`
- 防线层级：local_patch / shared_guardrail / contract_test / observability

## 现象

新增连接归因后抽取的 5 分钟窗口中，MP3 B站状态 23 次请求累计 157.127 秒，P50
6.302 秒、最大 11.305 秒；MP3 详情 3 次累计 33.748 秒、最大 18.627 秒。四个图片下载在
同一秒并发发起，单个约 8.496 秒，却没有匹配的慢 SQL。

同一窗口 27 次 `load_task` 慢日志累计 91.775 秒；PgBouncer query P50/P95 为
1.434/1.753 秒，连接 wait P50/P95 为 0.735/0.953 秒。

## 影响

- 同页并发图片下载互相串行等待，并把已由鉴权打开的数据库连接一起占住。
- 文件发送阶段可能继续占用请求 Session，进一步降低连接池吞吐。
- 前端每 5 秒状态轮询在请求耗时超过周期时持续施压，后台标签页仍会消耗数据库往返。

## 根因

`_download_result_for_owned_task` 用一个全局缓存锁包住缓存 miss 查询。请求进入路由前，鉴权通常已
打开数据库 Session；并发下载等待全局锁时没有执行 SQL，却仍持有 checkout。命中或查询完成后，
返回 `send_file` 前也没有明确结束 Session。

MP3 B站状态前端固定每 5 秒刷新，没有 in-flight 单飞和 Page Visibility 暂停，放大了同一条
任务状态 SQL 与 PgBouncer 往返。

## 解决思路

1. 锁等待和文件发送都不得携带数据库 Session。
2. 权限判断所需用户 ID 在释放 Session 前快照，避免依赖 detached 用户 ORM。
3. 缓存内保存深拷贝 JSON，不保存 ORM 或后续延迟加载对象。
4. 状态轮询改成单飞、后台暂停和更合理周期。

## 实际方案

- 下载入口先快照可见用户 ID，再 rollback/remove 鉴权 Session，然后才进入缓存锁。
- 缓存 miss 查询的 `finally` 再次释放 Session，保证 `send_file` 只处理文件。
- `_get_owned_task` 接收预计算权限集合，返回结果写缓存前深拷贝 JSON。
- MP3 状态轮询从 5 秒改为 15 秒，增加 `refreshInFlight`，页面隐藏时停止，恢复可见且任务活跃时再刷新。
- 更新前端缓存版本，避免浏览器继续执行旧轮询逻辑。

## 验证

- 自定义锁测试断言进入锁前 Session 已释放；缓存 hit 不查库；miss 在查询后再次释放并深拷贝结果。
- 前端合同测试断言 15 秒、in-flight 单飞和 `document.hidden` 门禁。
- JavaScript 语法检查和本轮相关定向回归 123 项通过；完整 unit 与运行态验收见巡检报告最终结果。

## 复发预防

- 媒体接口进入线程锁、对象存储、文件流前必须释放数据库 Session。
- 并发媒体下载的权限只使用短生命周期快照，缓存禁止保存 ORM。
- 所有状态轮询统一具备单飞、页面隐藏暂停、终态停止和最小周期合同。
- 巡检单独排名“页面很慢但没有匹配慢 SQL”，不把它误报成数据库无问题。

## 后续事项

- 后端安全重载后执行同页多图并发下载，确认下载期间 `non_sql_hold_ms` 不再增加。
- 下一窗口核对 MP3 B站状态请求量理论上约下降三分之二，并观察 P95/累计耗时而非只看次数。
