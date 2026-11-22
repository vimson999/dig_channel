# 图书带货情报：实现细则与验收标准

状态：2026-11-18 按用户确认范围修订并已实现。与[架构设计](2026-11-17-book-commerce-design.md)一起作为内部约束记录；实际外部文件字段见[导入 JSON 约定](../../operations/book-commerce/import-contract.md)。

## 1. 模块职责与文件边界

| 拟议位置 | 职责 | 不应承担 |
|---|---|---|
| `models/book_commerce.py` | 13 张模型、约束、轻量序列化 | 网络采集、自动提交事务、规则计算 |
| `services/book_commerce/validation.py` | 类型、指标白名单、身份引用、时间/范围校验 | 数据库隐式创建实体 |
| `services/book_commerce/ingest.py` | 文件预览、批次幂等、关联写入、冲突拒绝 | 对外格式适配和上传平台 |
| `services/book_commerce/metrics.py` | 可单测的日度选择、时间窗、增量质量 | Flask、Celery、网络 |
| `services/book_commerce/recommendations.py` | 规则版本、原因码、证据构造 | 模型猜测成交数 |
| `services/book_commerce/analysis.py` | run 执行、输入版本、投影批量落库、发布 | 前端请求内无限计算 |
| `services/book_commerce/queries.py` | 有界列表、投影、详情、权限过滤 | GET 时写库或补采集 |
| `services/book_commerce/decisions.py` | 收藏、忽略、待研究、备注及本人权限 | 修改原始数据或机器结论 |
| `apis/v1/book_commerce.py` | 认证、输入解析、错误映射、响应 | 重复领域计算 |
| `tasks/book_commerce.py` | 调用分析服务，重试和失败状态 | 借用媒体渲染队列 |
| `frontend/book-commerce.html`、对应 js/css | 工作台、图表、导入和决策控件、只读详情 | 重新实现增量/规则 |
| `tests/unit/test_book_commerce_*.py` | 规则、权限、事务、回归 | 连接实际业务库 |

既有文件改动限于模型注册、蓝图注册、导航；不改文案转视频或发布工作流。所有拟议路径需在实施时检查最新分支，不覆盖他人的改动。

## 2. 数据库约束与索引规范

- 枚举用 VARCHAR + CHECK，避免原生 ENUM 绑定数据库；应用层也验证。模型与显式 Alembic 迁移需一致，迁移不 import 未来可能变化的应用模型。
- `bc_observations.kind=account` 必须有 account_id 且其他目标为空；showcase 必须有 account_id/account_listing_id；video 必须有 account_id/video_id；平台和关联账号一致由服务事务内检查。
- 在 observations 增加非空 target_key。account=`account:{id}`；video=`video:{id}`；account_listing scope=`account_listing:{id}`；platform_listing scope=`listing:{id}`；unknown scope=`unknown:{account_listing_id}`。目标串由服务从实际 FK 计算，不接收自由文本。
- observations/daily_metrics 增加 listing_id 可空 FK；showcase 必填且必须与 account_listing.listing_id 一致，用于平台商品跨账号去重。平台级 daily_metrics 的 account_id/account_listing_id 为空，避免把 canonical 来源入口误当销量归属。
- 原始平台商品观测保留 account_id/account_listing_id 表示“从哪个橱窗看见”，计算目标则是 listing_id；不同入口相同计数同时间可去重，不同时刻取日内最后记录，同时间不同计数视冲突。
- `series_key` 长度 128，由 source/provider、metric_scope、计数定义版本规范化计算；同一 provider 的不同采集账号不是不同统计口径，采集账号/方法仅放 evidence。
- `content_hash` CHAR(64) 唯一，基于目标、时点、series、指标和质量等规范化内容，不含批次和传输时间；原始入口证据按批次留存。另设非空 observation_slot_key=hash(target_key+series_key+observed_at) 唯一，阻止同一时点不同内容覆盖。
- 写入先按content_hash识别重复，再检查slot冲突；通过bc_state串行锁及数据库唯一约束保证并发一致。不存在supersedes_id、voids_id或修正事件类型。
- `bc_daily_metrics` 唯一 `(generation_id,kind,target_key,series_key,business_date)`。推荐唯一键见架构文档；不依赖 NULL 的跨数据库唯一性行为。
- 原始计数范围 0..9223372036854775807，拒绝 bool、浮点数、科学计数、负数和溢出。JSON 边界使用十进制数字字符串保存/返回计数，服务转任意精度整数校验后写 BIGINT 投影；前端不得 Number() 后再比较大整数。
- `showcase_rating` 精度为两位十进制，合法 0..5；price 两位小数且非负。未知价格为 NULL，不用 0 表示缺失。
- ISBN-13 非空时必须通过校验位验证，否则整批拒绝；书籍可没有ISBN，但必须有来源稳定键。禁止同名自动合并；已确认的版本映射冲突拒绝，不提供管理员修复接口。
- 账号/视频/商品/书籍均有source、source_key，唯一(source,source_key)；平台标识非空时另有平台唯一键，书籍另有ISBN唯一键。若两个键解析到不同已有实体，拒绝，不自动合并；同平台标识的新增来源键如需多别名，第一版明确拒绝并报告unsupported_identity_alias，不临时增加别名表。
- 主数据带last_observed_at，仅较新导入更新描述；较早补录保留原文不覆盖最新描述。同时间不同描述拒绝；未知字段不清空。一个来源键后来新增平台ID，仅在ID未属于其他实体时允许补全。批次保存原始实体片段与实际更新摘要，不保存Cookie或凭据。
- 挂载关系仅由导入形成。相同video/account_listing/valid_from且内容相同为重复；未结束区间可由较新观测中的明确结束证据补齐valid_to，已有终点不可重写。新关系不得重叠；关系缺席不推断解绑；身份、类型、已确认起点冲突均拒绝。这是新时刻证据的追加，不是历史纠错。

最低索引：

| 表 | 索引 |
|---|---|
| accounts/listings/videos | 外部/来源标识唯一键；videos(account_id,published_at,id) |
| account_listings | 唯一账号商品；(listing_id,account_id) |
| video_listing_links | (video_id,account_listing_id,valid_from,valid_to) |
| observations | (target_key,series_key,business_date,observed_at,id)、batch_id、observation_slot_key |
| daily_metrics | 唯一业务键；(generation_id,kind,business_date,sales_delta,id)；(generation_id,kind,business_date,shares_delta,id) |
| analysis_runs | (status,created_at,id) |
| recommendations | 唯一候选键；(run_id,business_date,status,shares_delta,id) |
| candidate_decisions | owner逻辑键唯一；(owner_user_id,decision,updated_at,id) |

身份或引用无法解析时整批拒绝，返回具体字段与原因；不提供人工实体选择、临时新建、合并或停用入口。

## 3. 内部接口约定

公共前缀 `/api/v1/book-commerce`。成功沿用 `{success:true,data:...}`；错误 `{success:false,message,code,errors:[{path,code,message}]}`。生产响应不能泄漏 SQL、连接串和堆栈。

| 方法 / 相对路径 | 权限 | 约定 |
|---|---|---|
| GET `/accounts`、`/books`、`/listings`、`/videos` | 登录 | 分页、搜索；视频支持 account_id |
| GET `/account-listings`、`/video-listing-links` | 登录 | 只读挂载关系、证据和历史 |
| GET `/{resource}/{id}` | 登录 | 单实体详情；逐字稿/原始证据不随列表返回 |
| POST `/observations/preview` | 管理员 | 上传文件、内部DTO校验，返回hash/input_revision及新增/重复/冲突逐行结果 |
| POST `/observations/commit` | 管理员 | 重验 hash/revision，Idempotency-Key 必填；全批事务 |
| GET `/observations/{id}`、`/batches`、`/batches/{id}` | 管理员 | 原始证据和批次审计；普通用户只读脱敏候选证据 |
| GET `/dashboard` | 登录 | 账号概览、覆盖和最新数据日期，不混合异日总量 |
| GET `/rankings/products`、`/rankings/videos` | 登录 | date、scope、account_id、排序白名单；默认只含 eligible_delta |
| POST `/analysis-runs` | 管理员 | 日期范围 <=31 日；幂等提交；202 返回 run ID |
| GET `/analysis-runs/{id}` | 登录 | 状态/进度/覆盖；失败给可读原因 |
| GET/PATCH `/rules` | 读登录/写管理员 | schema 固定；expected_version；修改即 stale |
| GET `/recommendations`、`/recommendations/{id}` | 登录 | 最新发布 run 或显式历史 run；个人 decision 联查 |
| GET `/decisions` | 登录，仅本人 | 按个人状态分页，含已不在当前候选池的记录及旧证据 |
| PUT `/recommendations/{id}/decision` | 登录，仅本人 | saved/dismissed/to_research/pending、可选note；expected_version，证据run_id由服务校验 |

列表参数 page>=1、page_size 默认20上限100，order_by 白名单，稳定排序以 id 收尾；全文搜索长度<=100，转义通配符，禁止客户端传 SQL 列表达式。观测证据大小<=64 KiB/条、全文逐字稿<=100000 字；个人备注<=2000字，不作为脚本或生产输入。

日期必须 YYYY-MM-DD；输入时间必须带时区。不使用隐式 local timezone。观测时间晚于服务器 UTC 当前时间超过 5 分钟拒绝；历史补录允许；视频发布时间未知留空。source_url 仅允许 http/https，首版不由服务器自动抓取，前端文字和链接需安全渲染。

HTTP：401 未登录、403 无角色、404 不存在或私有资源不可见、409 版本/幂等冲突、413 超限、422 字段错误、503 数据结构未就绪/分析基础设施不可用。现有 app 全局错误约定如需统一，适配器兼容后保留稳定 code。

前端仅保留筛选及未提交的个人备注状态；不在URL/localStorage中保存全文逐字稿、凭据或个人备注。决策owner取登录用户，禁止由客户端指定。默认expected_version=0表示尚无决策行；更新按CAS，过期版本返回409。重复提交完全相同的状态/备注且指向同一证据run返回原结果，不递增版本。

## 4. 事务、异步和一致性

固定锁顺序：bc_state → 主数据行 → 相关事实。个人决策写入只锁本人的决策行，不改input_revision；并发首次创建由owner逻辑键唯一约束保护。

提交批次前持久化 accepted 审计记录；业务写入和 committed 状态同一事务。失败回滚业务事务后，独立短事务将业务校验失败标 rejected，基础设施故障标 failed，保存有限错误和 attempt_count；服务崩溃遗留 accepted 可按同 idempotency key 恢复。预览 hash 不作为授权令牌，提交重新认证和验证。

幂等处理顺序固定：认证/角色/owner检查 → 规范化操作内容计算hash → 查同key。committed且hash相同直接返回原结果，即使input_revision后来改变；hash不同409。rejected且hash相同返回原拒绝，不自动重试，修改被拒绝文件后提交必须新key。failed或无活动处理者的accepted允许同key同内容恢复；仍需重新预览并提交当前expected_revision。payload_hash只包含业务事实/操作，不含preview_token、expected_revision、请求接收时间等并发控制元数据。

新导入才检查expected_revision。文件导入包含主数据、关联与观测，按依赖顺序在同一事务内保存；任何冲突整批回滚。导入只能追加新观测及更新新时刻描述，不能覆盖旧观测，也不能通过新幂等key绕过slot唯一约束。

analysis-runs 采用 Celery 新任务类型和明确队列 `book_commerce`，并发 1；运行时检查此队列有 worker，无 worker 不假装运行。run 先落库，再按现有 `runtime_guard` 提交/释放数据库后 enqueue；发布失败保留 pending + enqueue_failed，可幂等重试。worker 领取通过状态 CAS，重投不重复运行已完成 run。

run 中原始输入读取使用可重复读快照；所有需要计算的输入先冻结读取到有界内存结构，尽快释放读取事务。业务写入可继续，最终 input_revision 改变则拒绝发布。预计超过 100000 条观测的单次运行先拒绝并提示缩短日期范围；不无限载入历史。

worker 失败更新 failed，保留错误码和阶段；不切换 published_run_id。超时 120 秒、最多 2 次仅对可重试基础设施故障重试；校验失败不重试。若进程被杀，启动/管理员重试时将超过超时且无活动 worker 的 running 标失败后新建 run，不永久锁死状态。

GET 只读。空库返回空 items 和 coverage，不返回数据库异常；迁移未应用通过模块 health 返回 schema_not_ready/503，由功能开关隐藏导航，不能在请求中建表。

发布用短事务 compare-and-swap revision，查询只信 bc_state.published_run_id。全局当前 run 仅覆盖一个 <=31 日区间；历史查询明确传 run_id，页面切日期超出覆盖提示“该日期尚未计算”，不混接不同版本结果。

stale候选允许查看和人工标记，但显示“待重算”；决策保存当时的evidence_run_id。重算不能更改个人标记。个人列表联查当前run：存在同逻辑候选则展示当前证据与当时证据，run覆盖该日期但不存在则显示“当前规则下未入选”，未覆盖则显示“该日期未重新计算”，仍允许改标或恢复pending。

## 5. 页面与运营标准

- 日期默认最近观测日，不能把“最新”默认为“今天”；显示 selected_date、latest_observed_at、computed_at、coverage 和 stale。
- “零增长”“未采到”“不可比较”“计算失败”四种状态必须可区分；未知数不能显示 0 或画成折线零点。
- 图书归组是可选视图；同名不同 ISBN/版次不得合并。账号总销量与商品榜总和不可要求相等。
- 每条排名能查看端点值、差值、具体时间窗、来源与质量；候选必须能查看挂载证据及竞争视频数量。
- 当前日 provisional、跨日 gap、counter_decrease 等标签同时在详情与列表可见。
- 前端使用 textContent 或明确转义渲染外部标题/逐字稿；不把原始 HTML 注入 innerHTML。链接新开标签设置 noopener。
- 普通用户不显示管理员入口，但服务端仍检查权限；切换用户时不复用个人标记及备注缓存。
- 不允许页面出现“全网销量榜”“实时出单归因”“该视频成交 X 单”等超出数据能力的文案。

## 6. 测试与验收矩阵

以下是验收矩阵。实现与验证记录见[实施计划](../plans/2026-11-18-book-commerce.md)；矩阵中的每个场景仍可作为后续回归覆盖要求。

| 编号 | 场景 | 期望 |
|---|---|---|
| D01 | 首次累计销量335、转发180 | delta=NULL，no_baseline，无候选 |
| D02 | D−1/D同为20:00，销量310→335、转发200→380，挂载覆盖全窗 | sales_delta=25、shares_delta=180，一条 candidate |
| D03 | shares 增100 / 增101 | 100不满足严格阈值；101满足其余条件时推荐 |
| D04 | 缺少昨天，只有前天 | daily_delta=NULL；interval_delta可显示；无日榜候选 |
| D05 | 累计计数下降 | 原始值和 signed_delta保留，counter_decrease，不进推荐 |
| D06 | `1.2万` / `1万+` / NULL / 明确0 | 前三类不算精确增量；明确0是有效基线 |
| D07 | 两个日期相邻但间隔10分钟 | irregular_window，无推荐 |
| D08 | 视频与商品窗口端点差超过2小时 | window_mismatch |
| D09 | 晚间记录缺 shares，上午记录有 shares | 不拼接；最新 shares未知 |
| D10 | UTC15:59与16:01观测 | 上海日期正确跨日；不会因日期跨日自动满足时距 |
| D11 | 视频当日发布 / 发布时间未知 | 不用0填基线；new_video或证据不足 |
| D12 | 同书不同店铺链接、不同账号 | 不串销量、不跨账号关联 |
| D13 | scope=platform_listing或unknown | 不作账号成交关联；平台商品相同事实不跨账号累加 |
| D14 | 两视频满足同商品增长25 | 两条候选共享同一销量证据，不生成50单总量 |
| D15 | 同名书不同版本，合集无book_id | 不错误合并；商品仍可独立查看 |
| I01 | 同幂等key同内容重复/并发提交 | 一份业务事实，返回同批次结果 |
| I02 | 同key不同内容 | 409，不覆盖 |
| I03 | 一批中一个坏引用/溢出/跨账号链接 | 整批业务回滚，有rejected审计 |
| I04 | 同时间不同值导入 | 409整批拒绝；旧记录不变 |
| I05 | 补录历史缺失时点 | 追加事实；相关日期及后继投影正确重算 |
| I06 | 并发导入同一时点冲突值 | 仅一批提交；另一批409，原值不覆盖 |
| I07 | name改了、来源相同ID | 同实体；不因改名重复创建 |
| I09 | 已成功提交后revision变化，再重发原请求 | 返回原结果，不因旧expected_revision报409 |
| I10 | rejected / failed同key重试 | rejected返回原拒绝；failed在重新预览后恢复，事实不重复 |
| R01 | 计算中断/worker不可用 | 未发布半成品；原结果可读且标状态 |
| R02 | 计算中新增观测/修改关系或规则 | revision校验失败，新run不发布 |
| R03 | 重复执行同一输入版本 | 内容结果相同，无重复候选；个人决策保留 |
| R04 | 历史run打开 | 显示历史版本和区间，不冒充当前结论 |
| A01 | 未登录/非管理员写入 | 401/403，数据库无业务变更 |
| A02 | A用户读取/修改B的个人标记备注 | 无法读取或写入；owner仅来自认证 |
| A03 | 恶意HTML标题、超大正文 | 安全显示或拒绝；不执行脚本 |
| C01 | 收藏、忽略、待研究及恢复pending | 状态互斥，列表筛选正确，备注独立保存 |
| C02 | 候选重算后不再入选 | 人工标记保留，可查看旧证据并改标 |
| C03 | 并发修改同一决策 | 版本冲突409，不覆盖他人会话的新状态 |
| S01 | 检查页面及路由 | 无手填、纠错、覆盖导入、草稿、生产交接入口 |
| I11 | 缺稳定标识/同名不同实体/别名冲突 | 拒绝缺标识和歧义；不同稳定键不按名称合并 |
| I12 | 新时刻改名/旧描述补录/身份变更 | 新描述可更新、旧描述不覆盖、身份变更拒绝 |
| M01 | 新迁移升级/回退演练 | 新表/FK/index与模型一致；旧表不被修改 |
| M02 | 未知revision/未迁移环境 | 明确阻止迁移或模块启用，不stamp掩盖 |

测试层次：纯规则单测；临时 SQLite 服务测试并启用 foreign_keys；临时 PostgreSQL 做唯一约束、锁、可重复读、导入幂等、迁移测试；页面交互与旧功能回归。外部采集、TTS、Gemini、发布在内部验收中不调用真实服务。

验证目标规模：100账号、10000视频、2000橱窗商品、每次计算不超过100000条观测；列表不携带全文证据，page_size<=100。以该合成数据在测试环境测量热读接口 p95<=1秒、31日有界计算<=60秒，记录硬件、索引、执行计划、样本次数。这是验收目标而非已测性能；不能通过缓存过期结果隐瞒错误。

## 7. 发布、观测与回退

- 新增 `ENABLE_BOOK_COMMERCE` 默认 false；开启前要求迁移链对齐、schema检查和关键测试通过。不开启不得启动该模块worker或展示导航。
- 记录批次数量/重复/拒绝原因、分析耗时/输入量/失败、候选数量和质量分布、人工决策保存失败率；日志包含 run_id/batch_id/decision_id，不记录原稿全文及凭据。
- 原始事实/规则版本/人工决策不自动清理；首版人工运营监测体积，后续清理必须另定保留策略，不借用现有媒体自动清理规则。
- 部署先数据库新增结构，再后端，再前端开关；已有媒体worker不承担该计算队列。
- 回退优先关闭入口与计算worker，保留数据；新功能尚无业务数据时才可考虑downgrade。任何已有生产数据的删除迁移需单独评审。
- 验收报告明确环境、commit、迁移revision、测试结果、未覆盖项；没有PostgreSQL实测不得写“兼容所有数据库”。

## 8. 设计完成与停止点

后续实现依照用户在架构设计完成后的指示开展。模块设计与实现验收已完成；真实供应方数据尚未提供，也没有写入合成样例到业务库。外部 JSON 文件约定见[导入合同](../../operations/book-commerce/import-contract.md)。
