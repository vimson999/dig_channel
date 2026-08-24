-- 仅供人工在 DBeaver 维护窗口逐段执行；自动化不得执行。
-- 证据（2026-08-19）：两个索引均为 UNIQUE btree(bvid)，各约 56 kB；
-- ix_bilibili_competitor_videos_bvid idx_scan=10980，约束索引
-- bilibili_competitor_videos_bvid_key idx_scan=0。保留约束所有者，删除冗余独立索引。

-- 1. 执行前门禁：必须返回 0 行。
SELECT bvid, count(*)
FROM public.bilibili_competitor_videos
GROUP BY bvid
HAVING count(*) > 1;

-- 2. 执行前证据：确认两个定义仍均为 UNIQUE btree (bvid)，且约束归属未漂移。
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'bilibili_competitor_videos'
  AND indexname IN (
    'bilibili_competitor_videos_bvid_key',
    'ix_bilibili_competitor_videos_bvid'
  )
ORDER BY indexname;

SELECT conname, pg_get_constraintdef(oid), conindid::regclass AS owned_index
FROM pg_constraint
WHERE conrelid = 'public.bilibili_competitor_videos'::regclass
  AND contype = 'u';

-- 3. DDL：CONCURRENTLY 不能放在显式事务中。若超时或报锁错误，停止并保留现场。
SET lock_timeout = '5s';
SET statement_timeout = '15min';
DROP INDEX CONCURRENTLY IF EXISTS public.ix_bilibili_competitor_videos_bvid;

-- 4. 验证：必须只剩约束索引；代表查询必须使用它且结果正确。
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'bilibili_competitor_videos'
  AND indexdef ILIKE '%(bvid)%';

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, bvid
FROM public.bilibili_competitor_videos
WHERE bvid = ANY (ARRAY['BV1xx411c7mD', 'BVSAME']);

-- 5. 回滚（仅当验证失败时人工执行；同样不能放在显式事务中）。
-- SET lock_timeout = '5s';
-- SET statement_timeout = '15min';
-- CREATE UNIQUE INDEX CONCURRENTLY ix_bilibili_competitor_videos_bvid
--   ON public.bilibili_competitor_videos USING btree (bvid);
