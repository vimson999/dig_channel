-- Gemini child reuse projection for publication_tasks.
-- Safe to re-run in DBeaver with Auto-commit enabled.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

ALTER TABLE public.publication_tasks
    ADD COLUMN IF NOT EXISTS gemini_response_chars integer NOT NULL DEFAULT 0;
ALTER TABLE public.publication_tasks
    ADD COLUMN IF NOT EXISTS publisher_landscape_enabled boolean;
ALTER TABLE public.publication_tasks
    ADD COLUMN IF NOT EXISTS publisher_render_mode varchar(32);

UPDATE public.publication_tasks
SET gemini_response_chars = length(regexp_replace(
    COALESCE(result ->> 'final_response_text', result ->> 'response_text', ''),
    '\s+',
    '',
    'g'
))
WHERE feature IN ('report_pdf_gemini', 'gemini_browser')
  AND gemini_response_chars = 0;

CREATE INDEX IF NOT EXISTS ix_publication_tasks_gemini_parent_status_created
    ON public.publication_tasks (parent_task_id, status, created_at DESC)
    WHERE feature IN ('report_pdf_gemini', 'gemini_browser');

UPDATE public.publication_tasks
SET
    publisher_landscape_enabled = lower(COALESCE(params #>> '{video,enabled}', 'false'))
        IN ('1', 'true', 'yes', 'on'),
    publisher_render_mode = LEFT(COALESCE(
        NULLIF(result #>> '{metadata,render_mode}', ''),
        NULLIF(result ->> 'render_mode', ''),
        NULLIF(params #>> '{metadata,render_mode}', ''),
        NULLIF(params ->> 'render_mode', ''),
        'full_video'
    ), 32)
WHERE feature = 'publisher';

CREATE INDEX IF NOT EXISTS ix_publication_tasks_publisher_source_mode_status_created
    ON public.publication_tasks
    (source_task_id, publisher_render_mode, publisher_preview_seconds, status, created_at DESC)
    WHERE feature = 'publisher' AND source_task_id IS NOT NULL;

ANALYZE public.publication_tasks;

SELECT
    count(*) AS gemini_tasks,
    count(*) FILTER (WHERE gemini_response_chars > 0) AS projected_responses
FROM public.publication_tasks
WHERE feature IN ('report_pdf_gemini', 'gemini_browser');
