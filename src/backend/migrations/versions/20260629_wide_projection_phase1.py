"""add wide projection columns for hot json paths

Revision ID: 20260629_wide_projection_phase1
Revises: 20260628_external_documents
Create Date: 2026-06-29

"""
from alembic import op


revision = "20260629_wide_projection_phase1"
down_revision = "20260628_external_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS waiting_child_label varchar(80)")
    op.execute("ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS waiting_child_id varchar(36)")
    op.execute("ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS waiting_child_status varchar(40)")
    op.execute("ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS waiting_child_step varchar(80)")
    op.execute("ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS waiting_child_updated_at varchar(64)")
    op.execute("ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS retry_state_status varchar(40)")
    op.execute("ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS final_script_chars integer NOT NULL DEFAULT 0")

    op.execute("ALTER TABLE public.zsxq_topics ADD COLUMN IF NOT EXISTS topic_type varchar(32)")
    op.execute("ALTER TABLE public.zsxq_topics ADD COLUMN IF NOT EXISTS question_text text")
    op.execute("ALTER TABLE public.zsxq_topics ADD COLUMN IF NOT EXISTS answer_text text")
    op.execute("ALTER TABLE public.zsxq_topics ADD COLUMN IF NOT EXISTS article_title varchar(500)")
    op.execute("ALTER TABLE public.zsxq_topics ADD COLUMN IF NOT EXISTS article_excerpt text")
    op.execute("ALTER TABLE public.zsxq_topics ADD COLUMN IF NOT EXISTS files_count integer NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE public.zsxq_topics ADD COLUMN IF NOT EXISTS images_count integer NOT NULL DEFAULT 0")

    _backfill()

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_publication_tasks_waiting_child "
        "ON public.publication_tasks (waiting_child_id, updated_at DESC) "
        "WHERE waiting_child_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_publication_tasks_waiting_label_status "
        "ON public.publication_tasks (waiting_child_label, waiting_child_status, updated_at DESC) "
        "WHERE waiting_child_label IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_publication_tasks_material_source_script "
        "ON public.publication_tasks (source_task_id, status, final_script_chars, completed_at DESC) "
        "WHERE feature IN ('report_script_material_workflow', 'report_pdf_audio_workflow', 'report_audio_workflow') "
        "AND source_task_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_publication_tasks_material_pdf_script "
        "ON public.publication_tasks (report_audio_pdf_attachment_id, status, final_script_chars, completed_at DESC) "
        "WHERE feature IN ('report_script_material_workflow', 'report_pdf_audio_workflow', 'report_audio_workflow') "
        "AND report_audio_pdf_attachment_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zsxq_topics_group_tab_type_create_desc "
        "ON public.zsxq_topics (group_id, tab_id, topic_type, create_time DESC, last_synced_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zsxq_topics_group_type_create_desc "
        "ON public.zsxq_topics (group_id, topic_type, create_time DESC, last_synced_at DESC)"
    )
    op.execute("ANALYZE public.publication_tasks")
    op.execute("ANALYZE public.zsxq_topics")


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_topics_group_type_create_desc")
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_topics_group_tab_type_create_desc")
    op.execute("DROP INDEX IF EXISTS public.ix_publication_tasks_material_pdf_script")
    op.execute("DROP INDEX IF EXISTS public.ix_publication_tasks_material_source_script")
    op.execute("DROP INDEX IF EXISTS public.ix_publication_tasks_waiting_label_status")
    op.execute("DROP INDEX IF EXISTS public.ix_publication_tasks_waiting_child")
    for column in [
        "images_count",
        "files_count",
        "article_excerpt",
        "article_title",
        "answer_text",
        "question_text",
        "topic_type",
    ]:
        op.execute(f"ALTER TABLE public.zsxq_topics DROP COLUMN IF EXISTS {column}")
    for column in [
        "retry_state_status",
        "final_script_chars",
        "waiting_child_updated_at",
        "waiting_child_step",
        "waiting_child_status",
        "waiting_child_id",
        "waiting_child_label",
    ]:
        op.execute(f"ALTER TABLE public.publication_tasks DROP COLUMN IF EXISTS {column}")


def _backfill():
    op.execute(
        """
        UPDATE public.publication_tasks
        SET
            waiting_child_label = LEFT(NULLIF(result #>> '{waiting_child,label}', ''), 80),
            waiting_child_id = LEFT(COALESCE(
                NULLIF(result #>> '{waiting_child,child_id}', ''),
                NULLIF(result #>> '{waiting_child,task_id}', ''),
                NULLIF(result #>> '{waiting_child,id}', '')
            ), 36),
            waiting_child_status = LEFT(NULLIF(result #>> '{waiting_child,status}', ''), 40),
            waiting_child_step = LEFT(NULLIF(result #>> '{waiting_child,step}', ''), 80),
            waiting_child_updated_at = LEFT(NULLIF(result #>> '{waiting_child,updated_at}', ''), 64),
            final_script_chars = GREATEST(
                COALESCE(length(NULLIF(result #>> '{structured_result,final_script_for_tts}', '')), 0),
                COALESCE(length(NULLIF(result #>> '{final_script_for_tts}', '')), 0),
                CASE
                    WHEN NULLIF(result #>> '{script_material,chars}', '') ~ '^[0-9]+$'
                        THEN (result #>> '{script_material,chars}')::integer
                    ELSE 0
                END
            )
        WHERE result IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE public.zsxq_topics
        SET
            question_text = NULLIF(raw_payload #>> '{question,text}', ''),
            answer_text = NULLIF(raw_payload #>> '{answer,text}', ''),
            article_title = LEFT(NULLIF(COALESCE(
                raw_payload #>> '{talk,article,title}',
                raw_payload #>> '{article,title}'
            ), ''), 500),
            article_excerpt = LEFT(NULLIF(COALESCE(
                raw_payload #>> '{talk,article,article_content}',
                raw_payload #>> '{article,article_content}'
            ), ''), 1000),
            topic_type = CASE
                WHEN NULLIF(raw_payload #>> '{question,text}', '') IS NOT NULL
                  OR NULLIF(raw_payload #>> '{answer,text}', '') IS NOT NULL
                    THEN 'question_answer'
                WHEN json_typeof(raw_payload #> '{talk,article}') = 'object'
                  OR json_typeof(raw_payload -> 'article') = 'object'
                    THEN 'article'
                ELSE 'talk'
            END,
            files_count =
                CASE WHEN json_typeof(raw_payload -> 'files') = 'array' THEN json_array_length(raw_payload -> 'files') ELSE 0 END
              + CASE WHEN json_typeof(raw_payload #> '{talk,files}') = 'array' THEN json_array_length(raw_payload #> '{talk,files}') ELSE 0 END
              + CASE WHEN json_typeof(raw_payload #> '{article,files}') = 'array' THEN json_array_length(raw_payload #> '{article,files}') ELSE 0 END
              + CASE WHEN json_typeof(raw_payload #> '{talk,article,files}') = 'array' THEN json_array_length(raw_payload #> '{talk,article,files}') ELSE 0 END,
            images_count =
                CASE WHEN json_typeof(raw_payload -> 'images') = 'array' THEN json_array_length(raw_payload -> 'images') ELSE 0 END
              + CASE WHEN json_typeof(raw_payload #> '{talk,images}') = 'array' THEN json_array_length(raw_payload #> '{talk,images}') ELSE 0 END
              + CASE WHEN json_typeof(raw_payload #> '{article,images}') = 'array' THEN json_array_length(raw_payload #> '{article,images}') ELSE 0 END
              + CASE WHEN json_typeof(raw_payload #> '{talk,article,images}') = 'array' THEN json_array_length(raw_payload #> '{talk,article,images}') ELSE 0 END
              + CASE WHEN json_typeof(raw_payload #> '{question,images}') = 'array' THEN json_array_length(raw_payload #> '{question,images}') ELSE 0 END
              + CASE WHEN json_typeof(raw_payload #> '{answer,images}') = 'array' THEN json_array_length(raw_payload #> '{answer,images}') ELSE 0 END,
            content = COALESCE(
                NULLIF(
                    CONCAT_WS(E'\\n\\n',
                        CASE WHEN NULLIF(raw_payload #>> '{question,text}', '') IS NOT NULL
                             THEN '提问：' || NULLIF(raw_payload #>> '{question,text}', '')
                             ELSE NULL END,
                        CASE WHEN NULLIF(raw_payload #>> '{answer,text}', '') IS NOT NULL
                             THEN '回答：' || NULLIF(raw_payload #>> '{answer,text}', '')
                             ELSE NULL END
                    ),
                    ''
                ),
                NULLIF(raw_payload #>> '{talk,text}', ''),
                NULLIF(raw_payload #>> '{talk,article,article_content}', ''),
                NULLIF(raw_payload #>> '{article,article_content}', ''),
                content
            )
        """
    )
