-- Add publish-status columns to the existing ZSXQ attachment table.
-- Run with the owner of public.zsxq_attachments or a database admin role.

ALTER TABLE public.zsxq_attachments
    ADD COLUMN IF NOT EXISTS publish_status varchar(32),
    ADD COLUMN IF NOT EXISTS publish_task_id varchar(36),
    ADD COLUMN IF NOT EXISTS publish_source_task_id varchar(36),
    ADD COLUMN IF NOT EXISTS publish_topic_id varchar(64),
    ADD COLUMN IF NOT EXISTS publish_submitted_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS publish_verified_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS publish_failed_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS publish_updated_at timestamp without time zone;

CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_publish_status
    ON public.zsxq_attachments (publish_status);

CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_publish_task_id
    ON public.zsxq_attachments (publish_task_id);
