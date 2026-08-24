-- 数据库会话超时护栏（仅供 DBeaver 人工审核、逐条执行）
-- 目标：show_ytb_data / cappa_rw
-- 风险：这是角色在指定数据库上的全局默认值，会影响该角色新建的所有会话。
--       请先确认迁移、批处理和维护任务使用独立角色或显式覆盖超时。
-- 要求：DBeaver 开启自动提交；使用具备 ALTER ROLE 权限的管理连接。

-- 1. 执行前确认：记录已有值，以便决定是否继续以及如何回滚。
SELECT
    d.datname,
    r.rolname,
    s.setconfig
FROM pg_db_role_setting AS s
JOIN pg_database AS d ON d.oid = s.setdatabase
JOIN pg_roles AS r ON r.oid = s.setrole
WHERE d.datname = 'show_ytb_data'
  AND r.rolname = 'cappa_rw';

-- 2. 建议值。每条 ALTER ROLE 都是独立、短事务，不锁业务表。
-- statement_timeout 与当前 PgBouncer query_timeout=60s 对齐；
-- 长任务应使用独立角色或显式 SET LOCAL。
ALTER ROLE cappa_rw IN DATABASE show_ytb_data
    SET statement_timeout = '60s';

ALTER ROLE cappa_rw IN DATABASE show_ytb_data
    SET lock_timeout = '5s';

ALTER ROLE cappa_rw IN DATABASE show_ytb_data
    SET idle_in_transaction_session_timeout = '60s';

-- 3. 验证持久化配置。配置只对新会话生效。
SELECT
    d.datname,
    r.rolname,
    s.setconfig
FROM pg_db_role_setting AS s
JOIN pg_database AS d ON d.oid = s.setdatabase
JOIN pg_roles AS r ON r.oid = s.setrole
WHERE d.datname = 'show_ytb_data'
  AND r.rolname = 'cappa_rw';

-- 4. 用 cappa_rw 新建一个 show_ytb_data 连接后逐项验证。
SHOW statement_timeout;
SHOW lock_timeout;
SHOW idle_in_transaction_session_timeout;

-- 回滚（仅在确认需要恢复为继承服务器默认值后，逐条执行）：
-- ALTER ROLE cappa_rw IN DATABASE show_ytb_data RESET statement_timeout;
-- ALTER ROLE cappa_rw IN DATABASE show_ytb_data RESET lock_timeout;
-- ALTER ROLE cappa_rw IN DATABASE show_ytb_data RESET idle_in_transaction_session_timeout;
