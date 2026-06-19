-- Performance indexes for the ZSXQ Lab local-list read path.
-- Run after docs/sql/zsxq_sync_tables.sql has created the base tables.

CREATE INDEX IF NOT EXISTS ix_zsxq_topics_group_tab_create_time_desc
    ON public.zsxq_topics (group_id, tab_id, create_time DESC, last_synced_at DESC);

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_sys_group_created_desc
    ON public.zsxq_sync_runs (group_id, created_at DESC)
    WHERE mode LIKE 'sys%';

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_sys_created_desc
    ON public.zsxq_sync_runs (created_at DESC)
    WHERE mode LIKE 'sys%';

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_system_batch_created_desc
    ON public.zsxq_sync_runs (system_batch_id, created_at DESC)
    WHERE mode LIKE 'sys%'
      AND system_batch_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_system_attempt_created_desc
    ON public.zsxq_sync_runs (system_attempt_id, created_at DESC)
    WHERE mode LIKE 'sys%'
      AND system_attempt_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_tab_topic_created
    ON public.zsxq_attachments (group_id, tab_id, topic_id, created_at);

CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_publish_status
    ON public.zsxq_attachments (publish_status);

CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_publish_task_id
    ON public.zsxq_attachments (publish_task_id);

CREATE INDEX IF NOT EXISTS ix_publication_tasks_created_at_desc
    ON public.publication_tasks (created_at DESC);
