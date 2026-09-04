# 2026-09-04 每日数据库性能与错误巡检

## 根因闭环结论

固定窗口：2026-09-03 06:00:00–2026-09-04 06:00:00，Asia/Shanghai。
慢SQL累计耗时改善83.3%，但**根因未全部闭环**：

1. 父任务唤醒的非主键整列JSON读取再次出现8次，按查询形态而不是1秒阈值重开P0；本轮已改为窄候选、
   命中主键后延迟物化、版本校验及短事务行锁，保留完整审计与失败恢复。已安全运行替换，待自然完成事件。
2. 巡检逐行解析漏掉无时间戳traceback，错误地报告server_closed=0。本轮按日志块解析并加回放门禁，
   同一窗口纠正为1个断连episode；该错误流与专用SQL日志必须分开计数。
3. 网络/PgBouncer仍有一次15秒query_wait_timeout。失败SQL占连接持有99.95%，不是外部I/O持有；
   同形executor仅0.086ms，不支持加索引。缺少故障当刻的pool/socket/远端状态，最终排队原因仍OPEN。
4. 上轮最小app factory与事务外哈希获得自然业务正向证据，但不把成功样本、无告警或一次后窗当作全面根治。

本轮没有DDL、数据回填、生产配置修改、删除原始JSON或人工业务补投。未创建新的同症状Bug重置复发次数，
更新了08-10父任务查询记录、08-18巡检误报记录、网络记录与recurrence registry。

## 前后量化

| 指标 | 上轮09-03 | 本轮09-04 | 判断 |
| --- | ---: | ---: | --- |
| 应用慢SQL数 | 154 | 29 | -81.2% |
| 慢SQL累计 | 234.642s | 39.148s | -83.3% |
| P50/P95/max | 1.235/2.145/19.644s | 1.285/1.932/1.937s | P50恶化4.0%，P95改善9.9%，max改善90.1% |
| HTTP请求/失败 | 1664/43 | 672/2 | 失败率2.58%→0.30%；流量下降59.6%，不可全归因修复 |
| 连接持有>5秒 | 29 | 1 | 15.024s，scheduler；Web/worker未捕获长hold |
| Celery成功/失败/retry | 154/30/0 | 205/0/0 | delivery口径；含续跑与no-op，不等于205个业务工作流 |
| PgBouncer query timeout | 3组 | 0 | 改善 |
| PgBouncer connect timeout | 1 | 0 | 改善 |
| PgBouncer query_wait_timeout | 1 | 1组/2行 | 反复，不能关闭 |

29条慢SQL中publication_tasks20条，其余五个重点表本窗没有>1秒慢SQL；不代表没有查询或风险。
HTTP总累计257.530s，P50/P95/max 5.77/1592.42/10164.56ms。672包含OPTIONS，故总体P50不能代表页面加载。
两次HTTP失败为06:25的runtime-status参数探针400（734a0692、8a9c2250），不是数据库500；
上轮竖屏文件缺失31次失败，本窗竖屏下载31次均成功，但历史文件删除/恢复因果不在本轮改动范围。

修正后应用server_closed=1；结构化SQL错误=0、traceback SQL episode=1，不能相加成另一次错误。
statement timeout、lock timeout、deadlock、日志idle-in-transaction均未捕获。人工诊断曾误用
provider.circuit_state导致一次UndefinedColumn，修正为模型真实state后重跑；发生在固定窗后、独立只读诊断，
不是业务故障，也不进入本窗数字。

## 主要问题排序与SQL关联

综合累计耗时、P95/max、失败、业务影响和复发历史排序；不按出现次数单独排序：

| 优先项 | 指纹/origin | 次数/累计/P95/max | 影响与归因 |
| --- | --- | --- | --- |
| P0失败与复发 | c1e57cc097d3e051 / workflow_notification_outbox.due_provider_drains | 失败1，15.017s；另成功慢SQL1/1.285s | 后台通知恢复中断一轮；PgBouncer排队失败，不是executor |
| P0查询形态 | 71b8b6d6cc87d1f3 / report_audio_parent_wakeup:83 | 8/约0.253s/42.28/42.28ms | Publisher完成消费者，早在08-14已OPEN；本轮必须修而非再次仅记录 |
| 慢SQL累计首位 | be891b5c1af362da / mp3_video_workflow:494 | 5/8.867s/1.937/1.937s | 音频目录冷快照；真实428行，窄列，不是循环点查 |
| scheduler修复扫描 | 25b2a425193817e3 / publisher_queue_repair:1428 | 3/3.423s/1.241/1.241s | 后台，无前台request；现有窄查询，持续观察网络往返 |
| ready视频修复 | b2e38145daa34074 / report_stick_figure_video:272 | 2/2.879s/1.448/1.448s | 后台自动续跑，不能只查页面 |
| MP3详情/动作 | 590220bb0dd49238 / api/mp3_video_workflow:1677 | 2/2.670s/1.653/1.653s | 点查冷载荷仍有长尾；非循环列表宽扫 |

publication专用500ms/形态日志另有227条、35个签名、20个跨源副本被去重、72条测试被排除。
其中multirow_payload为上述8条；常规进度持续JSON写放大未发现新的可归因根因。
slow_payload_write包含终态、开始状态和用户显式素材重生成，不能仅凭分类名断言都属进度风暴。
原始全部指纹及端点排名保存在artifacts/db-performance/2026-09-04-*.txt。

## 页面/API/后台端到端

日志发现83种method+归一化路径，全部纳入统计，未仅筛选知识星球。

| 路径（均保留原API前缀） | 请求/失败 | 累计s | P50/P95/max ms | SQL及调用链 |
| --- | ---: | ---: | --- | --- |
| POST /api/v1/content-pipeline/web-submit | 3/0 | 25.744 | 9288/10165/10165 | 最慢16dea173，批量文件+通知outbox；无>1s SQL |
| GET /api/pdf-merge-workflows/jobs | 27/0 | 21.135 | 693/1186/1595 | 57ff1828，一次窄列表慢SQL1391ms |
| GET /api/mp3-video-workflows/jobs/:id | 22/0 | 18.676 | 566/1703/1708 | 56f29dbe，load_task；旧版本前缀需归一后才直接比率 |
| POST .../artifacts/xiaojunjun_cover/regenerate | 5/0 | 16.566 | 3207/4335/4335 | 790d5c6b，外部素材I/O，无匹配慢SQL |
| POST .../bilibili-upload | 8/0 | 16.361 | 2024/3182/3182 | 2642d6b7，窄父/凭据快照，首事务稳定broker ID |
| GET /api/mp3-video-workflows/audio-assets | 15/0 | 13.428 | 7/2886/2989 | 5bc32d36；5个冷慢SQL累计8.867s，暖缓存明显更快 |
| GET .../bilibili-uploads | 19/0 | 12.703 | 686/1183/1264 | 窄child+上传关系表，无>1s SQL |
| POST .../artifacts/finance_cover/regenerate | 3/0 | 12.363 | 3783/5758/5758 | 4eed4655，显式生成+CAS单次写回，不是自动轮询 |
| GET /api/v1/platform-credentials | 22/0 | 11.748 | 373/1121/1154 | 上轮P95 3745ms，本轮明显改善 |
| POST .../continue-full-video | 4/0 | 9.341 | 1523/5356/5356 | 4111df24，一次load_task慢SQL1653ms，继续复核post-commit宽回读预算 |

其他端点包括data/table、chart-data、channels、videos、rankings、favorites、B站charging/seasons、
ZSXQ analysis与音频/PDF预览下载，详见完整端点表。data/table两次最大966.62ms；
ZSXQ analysis POST一次2848.38ms、GET两次最大636.08ms，无失败。

**三类容易漏掉的异常必须分列：**

- 低频高延迟：content提交最大10.165s（3次）、finance封面5.758s（3次）、continue5.356s（4次），
  PDF merge创建4.381s（1次）。数量少不能从排行排除。
- API慢而无匹配慢SQL：content提交、素材重生成、PDF merge创建、ZSXQ analysis创建。
  16dea173内25PDF+10音频两批的canonical各1SELECT+1INSERT；任务INSERT到canonical SELECT约33ms，
  旧6.1s哈希空档消失。整个请求还顺序写多个通知事件并进行broker往返，不能把10.165s全部归于哈希或DB。
  不同批次规模/网络状态不同，43.201→10.165s不是受控基准。
- 后台长耗时而无前台端点：通知恢复15s hold；Celery PDF清洗2次累计578.771s/max344.706s，
  ZSXQ发布5次累计789.920s/max274.967s，Publisher9次累计1491.726s/max251.778s，
  MP3 delivery50次累计912.573s/max227.565s，upload-only8次累计876.484s/max134.625s。
  通知127次累计323.280s/P95 5.612s/max7.075s；音频清洗3次累计67.250s、PDF合并1次8.565s。
  合计205次成功5048.568s，不含消息排队与浏览器首屏时间；这些外部处理不能充当数据库executor耗时。

## 上轮优先复核结果

- app factory：自然发布9、upload-only8、MP3 delivery50次成功，目标ImportError0；数据库新建publisher16项、
  MP3父4项当前均completed/done。已有真实业务正向证据，整个拓扑簇仍OBSERVING。
- 事务外哈希：25PDF与10音频分别25/25、10/10成功，canonical批量预算成立且Web长hold0。
  生产没有直接SHA入口事务事件，所以不能把旁证写成in_transaction=False的直接运行探针；该断言由SQL回归覆盖。
- 通知：当前422/422 sent、provider closed、consecutive_failures=0、无open_until；排队超时后恢复成功。
  未验证邮箱客户端回执，也未制造SMTP故障。
- revoke：自然捕获4组不同broker ID的before/after事件，均session_in_transaction=false、durable=true、
  outcome=success，耗时32.42/33.23/657.16/664.81ms；scheduler/celery_watchdog副本不重复计数。
  覆盖superseded repair及child cleanup，不等于所有取消/配额/失败revoke入口都已运行验收。
- watchdog展示聚合未再形成慢SQL/形态日志；没有全量SQL计数，不能从慢日志0证明所有亚阈值执行0。
- Insights overview、topics/local、topics/local/tabs、sync/status本窗没有匹配请求；因此是未观测，
  不是“健康”。数据库canonical查询EXPLAIN35.060ms不能替代这些真实页面/API验收。

## 物理层、索引与网络

| 表 | 总大小 | live/dead | HOT% | 索引扫描占比% |
| --- | --- | --- | ---: | ---: |
| publication_tasks | 130MB（TOAST101MB） | 13176/1033 | 1.27 | 99.78 |
| zsxq_topics | 129MB | 12637/1584 | 0.71 | 99.55 |
| zsxq_topic_comments | 118MB | 41333/5644 | 70.46 | 99.89 |
| zsxq_attachments | 78MB | 33906/37 | 0.12 | 99.95 |
| zsxq_sync_runs | 37MB | 1062/46 | 0.33 | 99.93 |
| external_documents | 7016kB | 1817/146 | 0 | 98.79 |

publication dead tuple491→1033、总大小129→130MB，写放大仍需观察；其余主要统计稳定。
精确重复索引0；低使用项仍有external_documents tts_status/summary_status与publisher_mp3_match scan0。
不能仅凭0删索引，缺对应真实查询参数、完整业务周期与替代索引比较。未输出新的DBeaver整改SQL。
Insights canonical索引valid/ready/live，累计scan3930；本次选择顺扫+sort，35.060ms且只读取必要列，
不是强迫命中索引的理由。sync_runs120行executor1.564ms，publication点查0.034ms、chart3.418ms、
配置0.021ms、publisher列表0.079ms、content列表0.243ms。

父唤醒新旧同样命中ix_publication_tasks_waiting_child，executor0.034→0.014ms；该样本返回0行，
不能把估算width或时间差当作真实传输改善。真正修复点是无匹配/重复事件不再请求JSON，已有实际SQL测试。
notification失败指纹现场executor0.086ms，与历史应用15.017s显著分离。

PgBouncer窗口1433个stats样本，wait P50/P95/max22.14/56.41/259.31ms，query53.12/112.74/499.33ms；
287次建连，11.958/h。source100.96.3.12→100.92.99.39:5432，现场路由utun5（私网隧道）；
source未回退代理地址不等于无隧道/无网络问题。08:53:55 client51224登录，08:54:10排队15秒后关闭，
对应watchdog PID22426的唯一长hold。没有当刻pool occupancy，无法最终区分连接饱和、后端登录阻塞或链路停顿。
当前只读snapshot active1/idle7、等待锁0、idle-in-transaction0；硬保险60s/5s/60s。
06:15额外核对SHOW POOLS：业务池transaction模式、cl_waiting=0、sv_login=0、maxwait=0；
SHOW SERVERS为6条used后端连接，均100.96.3.12→100.92.99.39，没有把sv_idle=0误写成热池空。
pg_stat_statements未安装，不以当前快计划代替历史executor完整分布。

## 验证、部署与安全边界

- 父唤醒两项新SQL回归在旧代码明确失败；修改后8项通过，覆盖成功、失败回滚、重复、无匹配与终态父。
- 日志新增多行/跨文件回放，旧代码缺日志块能力；修复后23项通过，真实历史窗纠正漏报。
- 跨内容批量去重、提交、app factory、Publisher DB retry等定向56项通过。
- 完整unit1593项通过；sandbox首轮36项为loopback bind限制，获准且内存broker隔离重跑全绿。
  最终交付版本再次完整执行：1593 tests / 75.165s / OK。
  本轮测试不发送真实邮件、不生成真实业务工作流，不把测试SQL计入自然统计。
- compileall、worker app factory27文件、revoke314文件、git diff --check、195条Bug strict通过。
- 06:07安全门读取47条近期任务、0活动sync；标准restart脚本再次检查、冻结17队列、关闭旧API并二次检查，
  均无不可中断任务，未使用FORCE。新backend38129监听且health healthy。
- 06:10:05 render ready、06:10:15 report_audio ready、06:10:18 MP3 ready、06:10:26 content ready；
  06:10:42 bootstrap17/17成功、6个scheduler启动成功、errors=[]。进程已替换不是自然任务验收。
- 重启后数据库仍无等待锁/idle-in-transaction，通知422条sent。父唤醒尚无新自然Publisher完成事件，
  未人为重放生产完成钩子或补投任务冒充验收。

## 根因闭环状态表

| 指纹/复发键 | 根因 | 已落地修复 | 运行态证据 | 复发/状态 | 剩余风险、责任动作、下一轮验收 |
| --- | --- | --- | --- | --- | --- |
| 71b8… / HOT-PATH-PAYLOAD-POLLING | 父候选提前物化result | 窄Row→有效主键版本快照 | 新worker ready，尚无目标自然事件 | 是；RUNTIME_REPLACED/OBSERVING | 本自动化优先对账首条Publisher完成，旧指纹0、有效唤醒/失败恢复、后续完整窗 |
| traceback / REGRESSION-ACCEPTANCE-INTEGRITY | 忽略异常续行 | 日志块+episode去重+独立错误流 | 原始历史回放恢复1个真实SQL失败 | 是；OBSERVING | 本自动化下一窗三源对账，带无时间戳/重复/窗口边界；不能相加重复流 |
| c1e57… / DB-SESSION-LIFECYCLE网络分支 | 15s pool排队，底层成因未完全定位 | 保留现有连接治理，未调大timeout | SQL15.017s而executor0.086ms；当前恢复 | 是；OPEN/REPEATED | 下次故障需同期SHOW POOLS/SERVERS、socket与远端活动；路由/生产配置由人工决策 |
| RUNTIME-TOPOLOGY-RELOAD | worker导入完整Web图 | 上轮50调用最小工厂+AST | 发布/上传/MP3自然成功，目标ImportError0 | 本窗未捕获；OBSERVING | 后续完整窗与新增API/model契约变化继续验收 |
| DB-SESSION-LIFECYCLE哈希 | flush后文件哈希 | 上轮事务外指纹 | 35文件成功、1查1插、旧非SQL空档消失 | 本窗未捕获旧症状；OBSERVING | 下一自然批次继续核对；缺直接生产入口事务事件 |
| revoke/轮询/图片/ZSXQ旧阻塞项 | 多入口生命周期与重复访问 | 保留历史已落地防线 | 4组自然revoke正向；其他样本不完整 | 仍未闭环 | 不从registry批量移除；自然入口逐一验收，图片提交/ZSXQ页面本窗未观测 |

## 下一轮明确验收与未捕获边界

1. 从09-04 06:10新进程切点优先检查71b8旧指纹与所有multirow_payload；捕获首条自然Publisher完成消费者，
   校验无匹配/重复零JSON、有效主键一次载荷读取、CAS与broker失败恢复、最终父任务业务产物完整。
2. 对比网络八类事件，尤其query_wait_timeout与server_closed；将traceback指纹与lifecycle、PgBouncer同秒关联。
   若再次发生，需同刻pool/socket/远端活动证据，不能继续按每日快EXPLAIN宣布网络已解决。
3. 上轮app factory、哈希、通知、revoke在下一完整窗口继续复核；不因为本轮无ImportError关闭整个簇。
4. 继续覆盖全量自然端点，并优先补Insights/topics/local/tabs/sync/status真实样本；本轮没有浏览器首屏、
   客户端渲染、所有用户唯一标识或Celery排队时间。影响用户数不可从loopback IP推算。
5. 持续观察MP3冷目录428行传输、content提交串行outbox往返与publication死元组增长；达到真实SQL+
   pg_stat+EXPLAIN证据门槛才建议索引。任何未来DDL仅提供逐条执行、超时、验证与回滚的DBeaver脚本。

本次没有待人工执行的新SQL；人工当前无需执行索引操作。下一轮读取本报告和registry续验收，不从零重扫。

## 2026-09-04 提交前应用层补强与验收

对应计划：`docs/superpowers/plans/2026-09-04-db-root-cause-fixes.md`。

- 父任务失败恢复：重新锁定仍属于本次 claim 的最新主键快照，保留并发写入审计，不倒退取消/终态/已推进步骤。
  准入守卫自身的三种 deferred 步骤仍能在 broker 失败时恢复旧延迟投递；真实 guard 与独立 Session 测试共 11 项通过。
- 文件边界：两类清洗服务释放继承的只读事务后才哈希；Web 上传在 multipart/暂存前释放认证事务。
  待提交 ORM 写入拒绝进入 I/O，不静默丢弃；真实预开事务测试在旧版为 True、新版为 False。
- 批量去重：锁定和插入按 hash 稳定排序，输出仍保序；真实唯一约束冲突只回滚 savepoint，保留外层任务及审计。
- 日志：修复彩色日志/UI START 边界、毫秒精度、同源重复事件、跨文件镜像及测试/诊断来源分列。
  固定历史窗再次对账保持上文 29 慢 SQL、672 请求、205 Celery 成功及一次断连，明确保留两个日志来源。
- 独立只读审查无 Critical/Important；最终定向 72 项通过。当前工作区首轮全量 1606 项通过后再补一项准入回归；
  实际待提交树单独导出，用无效 YouTube/Google 占位配置与内存 broker，全量 **1587 项 / 89.966s / OK**。
  初次未带占位配置的导入失败单独排查，未复制 `.env` 或真实密钥来获得绿灯。
- 导出树 917 个 src/scripts/tests Git blob 与暂存逐个一致；worker 27 文件、revoke 310 文件、
  Bug strict 195/195、compileall、暂存 diff check 均通过。其他功能、前端、配置和原始业务 artifacts 未收录。

本节补强仅完成代码与隔离测试，没有再次重启长期进程；06:10 的部署记录不代表本节版本已加载。
PgBouncer 排队的底层网络/资源原因仍 OPEN；无新 DDL 或生产连接参数变更。
