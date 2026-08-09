-- ZSXQ Insights canonical-topic index.
-- HUMAN-RUN ONLY. Do not run through the application or unattended automation.
-- Execute each section separately in DBeaver as the zsxq_topics table owner or
-- a PostgreSQL administrator, with auto-commit ON. Do not edit alembic_version
-- manually; a later controlled Alembic upgrade should record the revision.
--
-- This uses CREATE INDEX CONCURRENTLY to avoid blocking normal topic writes.
-- If any preflight check is unexpected, STOP and do not execute the DDL.

-- 1) Session and ownership preflight.
SET lock_timeout = '5s';
SET statement_timeout = '10min';

SELECT
    current_database() AS database_name,
    current_user AS executing_user,
    pg_get_userbyid(t.relowner) AS table_owner,
    pg_size_pretty(pg_total_relation_size(t.oid)) AS table_size,
    (SELECT version_num FROM public.alembic_version) AS alembic_version
FROM pg_class AS t
JOIN pg_namespace AS n ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
  AND t.relname = 'zsxq_topics';

SELECT
    pid,
    usename,
    application_name,
    client_addr,
    state,
    wait_event_type,
    wait_event,
    query_start,
    left(query, 300) AS query_preview
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND state <> 'idle'
ORDER BY query_start;

-- 2) Existing-index safety check. Stop if an existing index is invalid or
-- not-ready; IF NOT EXISTS does not repair an invalid index.
SELECT
    c.relname AS index_name,
    pg_get_userbyid(c.relowner) AS index_owner,
    i.indisvalid,
    i.indisready,
    i.indislive,
    pg_size_pretty(pg_relation_size(c.oid)) AS index_size,
    pg_get_indexdef(c.oid) AS index_definition
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
JOIN pg_index AS i ON i.indexrelid = c.oid
WHERE n.nspname = 'public'
  AND c.relname = 'ix_zsxq_topics_group_topic_preferred_synced_desc';

-- 3) Baseline plan. Save this output before continuing. The group id is the
-- current Shengcai Insights group; replace it only with a real target group.
EXPLAIN (ANALYZE, BUFFERS, VERBOSE)
WITH ranked AS (
    SELECT
        topic_id,
        tab_label,
        last_synced_at,
        row_number() OVER (
            PARTITION BY topic_id
            ORDER BY CASE tab_label
                WHEN '精华' THEN 0
                WHEN '中标' THEN 1
                WHEN '生财好事' THEN 2
                ELSE 3
            END,
            last_synced_at DESC
        ) AS rn
    FROM public.zsxq_topics
    WHERE group_id = '1824528822'
)
SELECT topic_id, tab_label, last_synced_at
FROM ranked
WHERE rn = 1;

-- 4) DDL. Run only after the baseline plan and ownership checks are accepted.
-- CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    ix_zsxq_topics_group_topic_preferred_synced_desc
ON public.zsxq_topics (
    group_id,
    topic_id,
    (
        CASE tab_label
            WHEN '精华' THEN 0
            WHEN '中标' THEN 1
            WHEN '生财好事' THEN 2
            ELSE 3
        END
    ),
    last_synced_at DESC
);

-- 5) Post-DDL validation and after-plan. Save both outputs and compare with
-- section 3; a created index alone is not acceptance evidence.
SELECT
    c.relname AS index_name,
    pg_get_userbyid(c.relowner) AS index_owner,
    i.indisvalid,
    i.indisready,
    i.indislive,
    pg_size_pretty(pg_relation_size(c.oid)) AS index_size,
    pg_get_indexdef(c.oid) AS index_definition
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
JOIN pg_index AS i ON i.indexrelid = c.oid
WHERE n.nspname = 'public'
  AND c.relname = 'ix_zsxq_topics_group_topic_preferred_synced_desc';

EXPLAIN (ANALYZE, BUFFERS, VERBOSE)
WITH ranked AS (
    SELECT
        topic_id,
        tab_label,
        last_synced_at,
        row_number() OVER (
            PARTITION BY topic_id
            ORDER BY CASE tab_label
                WHEN '精华' THEN 0
                WHEN '中标' THEN 1
                WHEN '生财好事' THEN 2
                ELSE 3
            END,
            last_synced_at DESC
        ) AS rn
    FROM public.zsxq_topics
    WHERE group_id = '1824528822'
)
SELECT topic_id, tab_label, last_synced_at
FROM ranked
WHERE rn = 1;

-- 6) Rollback only if the after-plan regresses or the index causes an
-- unacceptable write/lock impact. Run separately, then re-run section 5.
-- DROP INDEX CONCURRENTLY IF EXISTS
--     public.ix_zsxq_topics_group_topic_preferred_synced_desc;
