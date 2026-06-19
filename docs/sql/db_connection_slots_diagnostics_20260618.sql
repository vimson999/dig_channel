-- Diagnose PostgreSQL connection-slot pressure.

SELECT
    current_setting('max_connections')::int AS max_connections,
    current_setting('superuser_reserved_connections')::int AS superuser_reserved_connections,
    COUNT(*) AS current_connections,
    current_setting('max_connections')::int
        - current_setting('superuser_reserved_connections')::int
        - COUNT(*) AS non_superuser_slots_remaining
FROM pg_stat_activity;

SELECT
    usename,
    application_name,
    client_addr,
    state,
    COUNT(*) AS connections,
    MIN(backend_start) AS oldest_backend_start,
    MIN(state_change) AS oldest_state_change
FROM pg_stat_activity
GROUP BY usename, application_name, client_addr, state
ORDER BY connections DESC, oldest_state_change NULLS LAST;

SELECT
    pid,
    usename,
    application_name,
    client_addr,
    state,
    wait_event_type,
    wait_event,
    backend_start,
    state_change,
    LEFT(query, 300) AS query_preview
FROM pg_stat_activity
WHERE state <> 'active'
ORDER BY state_change ASC NULLS LAST
LIMIT 30;
