-- Knowledge Planet sync tables.
-- Run once with a PostgreSQL role that has CREATE privilege on schema public.
-- Replace your_app_db_user with the database user used by this Flask app if needed.

CREATE TABLE IF NOT EXISTS public.zsxq_sync_runs (
    id varchar(36) PRIMARY KEY,
    group_id varchar(32) NOT NULL,
    group_name varchar(255),
    tab_id varchar(128),
    tab_label varchar(255),
    mode varchar(20) NOT NULL DEFAULT 'manual',
    status varchar(20) NOT NULL DEFAULT 'running',
    requested_tabs jsonb DEFAULT '[]'::jsonb,
    result jsonb DEFAULT '{}'::jsonb,
    error_message text,
    started_at timestamp without time zone NOT NULL,
    completed_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone
);

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_group_id ON public.zsxq_sync_runs (group_id);
CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_tab_id ON public.zsxq_sync_runs (tab_id);
CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_status ON public.zsxq_sync_runs (status);
CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_created_at ON public.zsxq_sync_runs (created_at DESC);

CREATE TABLE IF NOT EXISTS public.zsxq_topics (
    id varchar(36) PRIMARY KEY,
    group_id varchar(32) NOT NULL,
    group_name varchar(255),
    tab_id varchar(128) NOT NULL,
    tab_label varchar(255),
    topic_id varchar(64) NOT NULL,
    topic_uid varchar(64),
    title varchar(500),
    author_name varchar(255),
    content text,
    create_time varchar(64),
    raw_json_path varchar(600),
    raw_payload jsonb DEFAULT '{}'::jsonb,
    first_sync_run_id varchar(36),
    last_sync_run_id varchar(36),
    first_synced_at timestamp without time zone NOT NULL,
    last_synced_at timestamp without time zone,
    CONSTRAINT uq_zsxq_topics_group_tab_topic UNIQUE (group_id, tab_id, topic_id)
);

CREATE INDEX IF NOT EXISTS ix_zsxq_topics_group_id ON public.zsxq_topics (group_id);
CREATE INDEX IF NOT EXISTS ix_zsxq_topics_tab_id ON public.zsxq_topics (tab_id);
CREATE INDEX IF NOT EXISTS ix_zsxq_topics_topic_id ON public.zsxq_topics (topic_id);
CREATE INDEX IF NOT EXISTS ix_zsxq_topics_create_time ON public.zsxq_topics (create_time);
CREATE INDEX IF NOT EXISTS ix_zsxq_topics_group_tab_create_time_desc
    ON public.zsxq_topics (group_id, tab_id, create_time DESC, last_synced_at DESC);
CREATE INDEX IF NOT EXISTS ix_zsxq_topics_first_sync_run_id ON public.zsxq_topics (first_sync_run_id);
CREATE INDEX IF NOT EXISTS ix_zsxq_topics_last_sync_run_id ON public.zsxq_topics (last_sync_run_id);

CREATE TABLE IF NOT EXISTS public.zsxq_attachments (
    id varchar(36) PRIMARY KEY,
    group_id varchar(32) NOT NULL,
    tab_id varchar(128) NOT NULL,
    topic_id varchar(64) NOT NULL,
    attachment_key varchar(255) NOT NULL,
    attachment_type varchar(20) NOT NULL DEFAULT 'file',
    name varchar(500),
    file_id varchar(128),
    download_count integer,
    local_path varchar(600),
    download_status varchar(20) NOT NULL DEFAULT 'pending',
    error_message text,
    publish_status varchar(32),
    publish_task_id varchar(36),
    publish_source_task_id varchar(36),
    publish_topic_id varchar(64),
    publish_submitted_count integer NOT NULL DEFAULT 0,
    publish_verified_count integer NOT NULL DEFAULT 0,
    publish_failed_count integer NOT NULL DEFAULT 0,
    publish_updated_at timestamp without time zone,
    raw_payload jsonb DEFAULT '{}'::jsonb,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone,
    CONSTRAINT uq_zsxq_attachments_group_tab_topic_key UNIQUE (group_id, tab_id, topic_id, attachment_key)
);

CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_id ON public.zsxq_attachments (group_id);
CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_tab_id ON public.zsxq_attachments (tab_id);
CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_topic_id ON public.zsxq_attachments (topic_id);
CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_file_id ON public.zsxq_attachments (file_id);
CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_download_status ON public.zsxq_attachments (download_status);
CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_publish_status ON public.zsxq_attachments (publish_status);
CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_publish_task_id ON public.zsxq_attachments (publish_task_id);
CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_tab_topic_created
    ON public.zsxq_attachments (group_id, tab_id, topic_id, created_at);

ALTER TABLE public.zsxq_attachments
    ADD COLUMN IF NOT EXISTS error_message text,
    ADD COLUMN IF NOT EXISTS publish_status varchar(32),
    ADD COLUMN IF NOT EXISTS publish_task_id varchar(36),
    ADD COLUMN IF NOT EXISTS publish_source_task_id varchar(36),
    ADD COLUMN IF NOT EXISTS publish_topic_id varchar(64),
    ADD COLUMN IF NOT EXISTS publish_submitted_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS publish_verified_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS publish_failed_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS publish_updated_at timestamp without time zone;

CREATE TABLE IF NOT EXISTS public.zsxq_sync_cursors (
    id varchar(36) PRIMARY KEY,
    group_id varchar(32) NOT NULL,
    tab_id varchar(128) NOT NULL,
    tab_label varchar(255),
    newest_topic_id varchar(64),
    newest_create_time varchar(64),
    last_end_time varchar(64),
    total_topics integer DEFAULT 0,
    updated_at timestamp without time zone,
    CONSTRAINT uq_zsxq_sync_cursors_group_tab UNIQUE (group_id, tab_id)
);

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_cursors_group_id ON public.zsxq_sync_cursors (group_id);
CREATE INDEX IF NOT EXISTS ix_zsxq_sync_cursors_tab_id ON public.zsxq_sync_cursors (tab_id);

-- Optional, if the creator role is different from the application role:
-- GRANT SELECT, INSERT, UPDATE, DELETE ON
--     public.zsxq_sync_runs,
--     public.zsxq_topics,
--     public.zsxq_attachments,
--     public.zsxq_sync_cursors
-- TO your_app_db_user;
