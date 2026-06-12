-- Report script + mindmap video factory tables.
-- Run once with a PostgreSQL role that has CREATE privilege on schema public.
-- Replace your_app_db_user with the database user used by this Flask app if needed.

CREATE TABLE IF NOT EXISTS public.report_video_tasks (
    id varchar(36) PRIMARY KEY,
    task_name varchar(255) NOT NULL,
    video_type varchar(40) NOT NULL DEFAULT 'daily_digest',
    default_segment_count integer NOT NULL DEFAULT 4,
    output_ratios jsonb DEFAULT '["16:9", "9:16"]'::jsonb,
    style varchar(60) NOT NULL DEFAULT 'professional_finance',
    background_music_path varchar(600),
    generate_subtitles boolean NOT NULL DEFAULT true,
    status varchar(30) NOT NULL DEFAULT 'created',
    result jsonb DEFAULT '{}'::jsonb,
    error_message text,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone
);

CREATE INDEX IF NOT EXISTS ix_report_video_tasks_status ON public.report_video_tasks (status);
CREATE INDEX IF NOT EXISTS ix_report_video_tasks_created_at ON public.report_video_tasks (created_at DESC);

CREATE TABLE IF NOT EXISTS public.report_video_reports (
    id varchar(36) PRIMARY KEY,
    task_id varchar(36) NOT NULL REFERENCES public.report_video_tasks(id) ON DELETE CASCADE,
    report_code varchar(8) NOT NULL,
    file_name varchar(500) NOT NULL,
    file_path varchar(600) NOT NULL,
    page_count integer,
    raw_text_path varchar(600),
    cleaned_text_path varchar(600),
    raw_text_chars integer DEFAULT 0,
    cleaned_text_chars integer DEFAULT 0,
    is_scanned boolean NOT NULL DEFAULT false,
    parse_status varchar(30) NOT NULL DEFAULT 'uploaded',
    ai_status varchar(30) NOT NULL DEFAULT 'ai_pending',
    error_message text,
    raw_meta jsonb DEFAULT '{}'::jsonb,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone,
    CONSTRAINT uq_report_video_reports_task_code UNIQUE (task_id, report_code)
);

CREATE INDEX IF NOT EXISTS ix_report_video_reports_task_id ON public.report_video_reports (task_id);
CREATE INDEX IF NOT EXISTS ix_report_video_reports_parse_status ON public.report_video_reports (parse_status);
CREATE INDEX IF NOT EXISTS ix_report_video_reports_ai_status ON public.report_video_reports (ai_status);

CREATE TABLE IF NOT EXISTS public.report_video_analyses (
    id varchar(36) PRIMARY KEY,
    report_id varchar(36) NOT NULL UNIQUE REFERENCES public.report_video_reports(id) ON DELETE CASCADE,
    report_title varchar(500),
    institution varchar(255),
    theme varchar(80),
    core_view text,
    key_points jsonb DEFAULT '[]'::jsonb,
    video_value_score integer,
    recommended_video_type varchar(60),
    recommended_duration_sec integer,
    reason text,
    raw_payload jsonb DEFAULT '{}'::jsonb,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone
);

CREATE INDEX IF NOT EXISTS ix_report_video_analyses_report_id ON public.report_video_analyses (report_id);

CREATE TABLE IF NOT EXISTS public.report_video_segments (
    id varchar(36) PRIMARY KEY,
    task_id varchar(36) NOT NULL REFERENCES public.report_video_tasks(id) ON DELETE CASCADE,
    report_id varchar(36) NOT NULL REFERENCES public.report_video_reports(id) ON DELETE CASCADE,
    segment_id varchar(32) NOT NULL,
    segment_index integer NOT NULL DEFAULT 1,
    segment_type varchar(60),
    segment_title varchar(255),
    voice_text text,
    estimated_duration_sec integer,
    focus_node_id varchar(128),
    audio_filename varchar(255),
    audio_path varchar(600),
    actual_duration_sec double precision,
    status varchar(30) NOT NULL DEFAULT 'draft',
    raw_payload jsonb DEFAULT '{}'::jsonb,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone,
    CONSTRAINT uq_report_video_segments_task_segment UNIQUE (task_id, segment_id)
);

CREATE INDEX IF NOT EXISTS ix_report_video_segments_task_id ON public.report_video_segments (task_id);
CREATE INDEX IF NOT EXISTS ix_report_video_segments_report_id ON public.report_video_segments (report_id);
CREATE INDEX IF NOT EXISTS ix_report_video_segments_focus_node_id ON public.report_video_segments (focus_node_id);
CREATE INDEX IF NOT EXISTS ix_report_video_segments_status ON public.report_video_segments (status);

CREATE TABLE IF NOT EXISTS public.report_video_mindmaps (
    id varchar(36) PRIMARY KEY,
    report_id varchar(36) NOT NULL UNIQUE REFERENCES public.report_video_reports(id) ON DELETE CASCADE,
    center varchar(255),
    nodes jsonb DEFAULT '[]'::jsonb,
    raw_payload jsonb DEFAULT '{}'::jsonb,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone
);

CREATE INDEX IF NOT EXISTS ix_report_video_mindmaps_report_id ON public.report_video_mindmaps (report_id);

CREATE TABLE IF NOT EXISTS public.report_video_audios (
    id varchar(36) PRIMARY KEY,
    task_id varchar(36) NOT NULL REFERENCES public.report_video_tasks(id) ON DELETE CASCADE,
    report_id varchar(36) REFERENCES public.report_video_reports(id) ON DELETE SET NULL,
    segment_id varchar(32),
    file_name varchar(255) NOT NULL,
    file_path varchar(600) NOT NULL,
    duration_sec double precision,
    format varchar(20),
    match_status varchar(30) NOT NULL DEFAULT 'pending',
    error_message text,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone
);

CREATE INDEX IF NOT EXISTS ix_report_video_audios_task_id ON public.report_video_audios (task_id);
CREATE INDEX IF NOT EXISTS ix_report_video_audios_report_id ON public.report_video_audios (report_id);
CREATE INDEX IF NOT EXISTS ix_report_video_audios_segment_id ON public.report_video_audios (segment_id);
CREATE INDEX IF NOT EXISTS ix_report_video_audios_match_status ON public.report_video_audios (match_status);

CREATE TABLE IF NOT EXISTS public.report_video_outputs (
    id varchar(36) PRIMARY KEY,
    task_id varchar(36) NOT NULL REFERENCES public.report_video_tasks(id) ON DELETE CASCADE,
    report_id varchar(36) REFERENCES public.report_video_reports(id) ON DELETE SET NULL,
    ratio varchar(10) NOT NULL,
    video_type varchar(40) NOT NULL DEFAULT 'single_report',
    duration_sec double precision,
    file_path varchar(600),
    subtitle_path varchar(600),
    status varchar(30) NOT NULL DEFAULT 'pending',
    error_message text,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone
);

CREATE INDEX IF NOT EXISTS ix_report_video_outputs_task_id ON public.report_video_outputs (task_id);
CREATE INDEX IF NOT EXISTS ix_report_video_outputs_report_id ON public.report_video_outputs (report_id);
CREATE INDEX IF NOT EXISTS ix_report_video_outputs_status ON public.report_video_outputs (status);

-- Optional, if the creator role is different from the application role:
-- GRANT SELECT, INSERT, UPDATE, DELETE ON
--     public.report_video_tasks,
--     public.report_video_reports,
--     public.report_video_analyses,
--     public.report_video_segments,
--     public.report_video_mindmaps,
--     public.report_video_audios,
--     public.report_video_outputs
-- TO your_app_db_user;
