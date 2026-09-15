# 内容管线文件哈希在数据库事务内执行

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING
- 严重级别：P0
- 影响模块：内容管线 Web 提交 / PDF 与音频清洗 / 数据库 Session
- 首次发现：2026-09-03 每日数据库性能与错误巡检
- 修复提交：当前工作区
- 根因分类：文件 I/O 跨数据库事务、批量提交长连接持有
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：是；与历史文件探测、浏览器和外部 I/O 跨 Session 属同一根因簇
- 关联记录：`2026-09-02-content-pipeline-dedupe-query-amplification.md`
- 防线层级：io_db_phase_split / transaction_contract / runtime_observation

## 现象与影响

自然 request `dc99bae7` 的 `POST /api/v1/content-pipeline/web-submit` 提交 12 个音频，耗时
43,200.80ms。上一轮批量去重修复已在同一自然流量中得到验证：canonical admission 只有 1 次 SELECT 和
1 次批量 INSERT；但任务 INSERT 后到 canonical SELECT 前仍有约 6.1 秒无 SQL 空档。生命周期日志显示连接持有
6,394.61ms，其中 SQL 仅 280.49ms、非 SQL 6,114.12ms。

代码还原确认 `reserve_files()` 在调用方已经 `flush()` 打开写事务后才逐文件读取并计算 SHA256。文件越多、越大，
连接越久被无谓占用；远端 PgBouncer/网络抖动下会进一步挤压并发。

## 根因与方案

去重服务把“文件指纹计算”和“数据库 canonical reservation”绑定在一个 API 中，调用方无法在事务开始前完成
文件 I/O。修复后：

- `fingerprint_files()` 在创建/flush `PublicationTask` 之前执行。
- 新增 `reserve_fingerprints()` 只消费预计算的纯字典并执行数据库预留；旧 `reserve_files()` 保持兼容并委托。
- PDF 与音频两个入口共用同一边界，批量 SELECT/INSERT 语义、唯一约束竞态兜底和响应结构不变。

## 验证与后续

### 2026-09-04 提交前入口事务补强

只把哈希移至 task INSERT 之前仍不够：Web 认证与 ZSXQ 同步上游可以已经开启只读事务。新增真实 SELECT
预开事务回归，在旧入口中 PDF/音频哈希均捕获到 True，上传 FileStorage.save 也捕获到 True。本次在两个
提交服务的指纹入口、网页 multipart 解析/暂存之前复用 `end_db_transaction_before_external_io`；干净只读
事务释放且不使 ORM 过期，待提交 ORM 写入 fail closed，不隐式丢弃。三条路径现为 False，pending write
保护通过。旧 `reserve_files` 为兼容入口，生产提交使用分相后的 `fingerprint_files/reserve_fingerprints`。
这是新补强的代码/回归证据，尚未安全重启或获得新的自然流量验收，不能沿用下面的旧 PID 宣布已部署。

2026-09-04后窗复核：request16dea173一次提交25份PDF和10份音频，10.165s/202；两批canonical均
一次SELECT+一次批量INSERT，INSERT任务到canonical SELECT约33ms，不再出现旧6.1秒哈希空档。
任务9c43642b…与3f42d035…分别25/25、10/10清洗成功，失败0，窗口Web长hold0。
但生产未直接输出哈希函数入口in_transaction事件，以上为时序和终态旁证，不能冒充直接事务探针；
保持OBSERVING，下一批继续对账。事务入口合同仍由真实SQLite SQL捕获测试验证。

- TDD 红灯在旧代码捕获到 SHA256 执行时 `in_transaction=True`；修复后 PDF、音频均严格为 `[False]`。
- 内容去重/Web submit/通知交叉 40/40 通过；完整 unit 1589/1589 通过。
- 安全门两次确认无不可中断任务后已标准重启到 backend PID 21618；content_pipeline worker 06:24:45 ready，
  runtime-only 状态为 running、queue=0，数据库复核无 waiting lock、无 idle-in-transaction。
- 自然样本已接受上一轮 1 SELECT + 1 INSERT 查询预算，但本次事务外哈希尚待重启后的自然多文件批次验收。
- 下一批要求哈希期间 `in_transaction=False`，canonical SQL 仍为 O(1)，并按 request id 对账连接持有与最终任务状态。

## 2026-09-05 运行版本边界

固定窗 Web submit 5/5 成功且没有 Web 长连接持有被生命周期阈值捕获，但最慢请求仍为 37.742 秒；
单条慢 SQL 只有 1.474 秒，日志还原约 28.816 秒在 SQL 外。该旧进程没有加载 `dec217e` 对 Web multipart、
PDF/音频入口继承只读事务与 pending-write fail-closed 的最终加固，因此不能把“未记录长 hold”写成事务边界
已获生产验收。

真实 SQLite SQL 捕获合同、定向交叉 114 项和完整 unit 1607 项均通过。两次安全门通过后，标准重启仍被
自动审批以服务中断风险拒绝；状态保持 `OBSERVING`，最终加固为
`CODE_VERIFIED / WAITING_RUNTIME_REPLACEMENT`。下一自然批次必须直接证明哈希及暂存期间
`in_transaction=false`，同时核对 O(1) canonical、request hold 与最终任务状态。

## 2026-09-10 后窗复核

四个自然 Web submit 共 83 个资产，canonical SQL 均为 1 次 SELECT + 1 次多行 INSERT，全部 202；
窗口内 84 个清洗资产最终均 `cleaned`，没有 Web 长连接持有事件。上述时序和终态与旧 6.1 秒哈希事务
空档不再出现一致，但生产仍未输出哈希入口 `in_transaction=false` 的直接结构化事件。

因此 O(1) canonical 可独立接受，本记录仍保持 `RUNTIME_REPLACED / OBSERVING`。后续只有捕获直接事务
边界标记后才允许关闭；不因“没有慢 SQL”或“任务最终成功”替代 Session 边界证明。
