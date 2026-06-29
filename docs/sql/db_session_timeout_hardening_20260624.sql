-- 2026-06-24 DB session hardening
-- Run with a database owner/admin account after confirming the target role name.
-- This is the database-side safety net; application code must still release sessions promptly.

ALTER ROLE cappa_rw SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE cappa_rw SET statement_timeout = '120s';
ALTER ROLE cappa_rw SET lock_timeout = '10s';

-- Verify in a new connection as cappa_rw:
SHOW idle_in_transaction_session_timeout;
SHOW statement_timeout;
SHOW lock_timeout;

-- Operational check:
SELECT pid, application_name, state, now() - xact_start AS xact_age, query
FROM pg_stat_activity
WHERE state = 'idle in transaction'
ORDER BY xact_start NULLS LAST;
