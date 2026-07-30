-- 2026-07-29 ZSXQ attachment publish-status redundant-index cleanup.
--
-- Evidence at audit time (PostgreSQL stats since 2026-07-17):
--   ix_zsxq_attachments_publish_status: idx_scan=0, size=352 KiB
--   ix_zsxq_attachments_publish_status_updated_at: idx_scan=3
-- The compound index has publish_status as its leftmost key.
--
-- Run manually in DBeaver with auto-commit ON. Execute each statement
-- separately. DROP/CREATE INDEX CONCURRENTLY must not run inside a transaction.
-- Do not include the two topic-type indexes from the older cleanup script:
-- one of them has 104 observed scans and needs a separate plan comparison.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

-- 1. Preflight: both indexes must be valid and ready.
SELECT
    index_row.relname AS index_name,
    meta.indisvalid,
    meta.indisready,
    stats.idx_scan,
    stats.idx_tup_read,
    pg_size_pretty(pg_relation_size(index_row.oid)) AS index_size,
    pg_get_indexdef(index_row.oid) AS index_definition
FROM pg_class AS index_row
JOIN pg_index AS meta ON meta.indexrelid = index_row.oid
LEFT JOIN pg_stat_user_indexes AS stats ON stats.indexrelid = index_row.oid
WHERE index_row.oid IN (
    to_regclass('public.ix_zsxq_attachments_publish_status'),
    to_regclass('public.ix_zsxq_attachments_publish_status_updated_at')
)
ORDER BY index_row.relname;

-- 2. Confirm no invalid concurrent-index artifact exists.
SELECT indexrelid::regclass AS index_name, indisvalid, indisready
FROM pg_index
WHERE indrelid = 'public.zsxq_attachments'::regclass
  AND (NOT indisvalid OR NOT indisready);

-- 3. Manual DDL. Stop here if the compound index is absent/invalid or if the
-- single-column index has gained meaningful scans since the preflight.
DROP INDEX CONCURRENTLY IF EXISTS public.ix_zsxq_attachments_publish_status;

-- 4. Validation: only the compound index should remain.
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'zsxq_attachments'
  AND indexname LIKE 'ix_zsxq_attachments_publish_status%'
ORDER BY indexname;

-- 5. Rollback (execute only if a post-change plan regression is demonstrated).
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_attachments_publish_status
ON public.zsxq_attachments (publish_status);
