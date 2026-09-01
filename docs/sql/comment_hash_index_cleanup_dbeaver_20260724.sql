-- 2026-08-29 safety replacement for the historical cleanup script.
-- READ ONLY: this file intentionally contains no ALTER/UPDATE/DROP/CREATE.
-- The hash column/backfill and Alembic ancestry are already complete. Never
-- move alembic_version backwards to the 20260724 revision.

BEGIN TRANSACTION READ ONLY;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

SELECT current_database(), current_user, pg_is_in_recovery();

SELECT count(*) AS comment_rows,
       count(*) FILTER (
           WHERE raw_payload IS NOT NULL AND raw_payload_hash IS NULL
       ) AS missing_payload_hash
FROM public.zsxq_topic_comments;

SELECT i.relname AS index_name,
       t.relname AS table_name,
       x.indisvalid,
       x.indisready,
       x.indisunique,
       x.indisprimary,
       c.conname AS constraint_name,
       COALESCE(s.idx_scan, 0) AS idx_scan,
       s.last_idx_scan,
       pg_size_pretty(pg_relation_size(i.oid)) AS index_size,
       pg_get_userbyid(t.relowner) AS table_owner,
       pg_has_role(current_user, t.relowner, 'MEMBER') AS owner_member,
       pg_get_indexdef(i.oid) AS index_definition
FROM pg_class AS i
JOIN pg_index AS x ON x.indexrelid = i.oid
JOIN pg_class AS t ON t.oid = x.indrelid
JOIN pg_namespace AS n ON n.oid = i.relnamespace
LEFT JOIN pg_constraint AS c ON c.conindid = i.oid
LEFT JOIN pg_stat_user_indexes AS s ON s.indexrelid = i.oid
WHERE n.nspname = 'public'
  AND i.relname IN (
      'ix_zsxq_topics_group_tab_type_create_desc',
      'ix_zsxq_topics_group_type_create_desc'
  )
ORDER BY i.relname;

SELECT version_num FROM public.alembic_version;

ROLLBACK;

-- Decision contract as of 2026-08-29:
-- 1. ix_zsxq_topics_group_tab_type_create_desc: RETAIN. It has 2,990 scans.
-- 2. ix_zsxq_topics_group_type_create_desc: RETAIN/OBSERVING. It has one scan,
--    but pg_stat_statements is not installed, so the real SQL origin and a
--    with/without-target EXPLAIN comparison are unavailable.
-- 3. A future drop must use the evidence-gated maintenance tool and must be
--    performed by the table owner in a reviewed maintenance window. This file
--    must never be expanded back into a bulk index drop script.
