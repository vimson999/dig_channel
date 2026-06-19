-- Video sync slow-query hotfix indexes.
-- DBeaver-friendly version: no CREATE INDEX CONCURRENTLY, so do not wrap this
-- script in an explicit transaction.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

CREATE INDEX IF NOT EXISTS ix_videos_channel_active_published
    ON public.videos (channel_id, is_active, published_at DESC);

CREATE INDEX IF NOT EXISTS ix_videos_channel_video_id
    ON public.videos (channel_id, video_id);

CREATE INDEX IF NOT EXISTS idx_video_date
    ON public.video_data (video_id, date);

CREATE INDEX IF NOT EXISTS ix_video_data_date_video_id
    ON public.video_data (date, video_id);

ANALYZE public.videos;
ANALYZE public.video_data;

SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
      'ix_videos_channel_active_published',
      'ix_videos_channel_video_id',
      'idx_video_date',
      'ix_video_data_date_video_id'
  )
ORDER BY tablename, indexname;

SELECT
    relname,
    n_live_tup,
    n_dead_tup,
    seq_scan,
    idx_scan,
    last_analyze,
    last_autoanalyze
FROM pg_stat_user_tables
WHERE schemaname = 'public'
  AND relname IN ('videos', 'video_data')
ORDER BY relname;
