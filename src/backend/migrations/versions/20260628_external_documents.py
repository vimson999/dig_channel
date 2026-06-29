"""add external document enrichment tables

Revision ID: 20260628_external_documents
Revises: 20260623_report_audio_cover_subtitle
Create Date: 2026-06-28

"""
from alembic import op


revision = "20260628_external_documents"
down_revision = "20260623_report_audio_cover_subtitle"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.external_documents (
            id varchar(36) PRIMARY KEY,
            provider varchar(40) NOT NULL,
            document_type varchar(40),
            source_url text NOT NULL,
            canonical_url text NOT NULL,
            url_hash varchar(64) NOT NULL UNIQUE,
            title varchar(500),
            author_name varchar(255),
            content_text text,
            content_html text,
            content_hash varchar(64),
            content_chars integer NOT NULL DEFAULT 0,
            fetch_status varchar(32) NOT NULL DEFAULT 'pending',
            fetch_error text,
            fetched_at timestamp,
            fetch_credential_id varchar(36),
            raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            summary_status varchar(32) NOT NULL DEFAULT 'pending',
            summary_text text,
            summary_model varchar(120),
            summary_error text,
            summarized_at timestamp,
            tts_status varchar(32) NOT NULL DEFAULT 'not_submitted',
            tts_task_id varchar(36),
            tts_audio_path varchar(600),
            tts_error text,
            tts_submitted_at timestamp,
            tts_completed_at timestamp,
            created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_external_documents_url_hash "
        "ON public.external_documents (url_hash)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_external_documents_provider_status "
        "ON public.external_documents (provider, fetch_status, updated_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_external_documents_summary_status "
        "ON public.external_documents (summary_status, updated_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_external_documents_tts_status "
        "ON public.external_documents (tts_status, updated_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_external_documents_content_hash "
        "ON public.external_documents (content_hash)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.zsxq_topic_external_documents (
            id varchar(36) PRIMARY KEY,
            group_id varchar(32) NOT NULL,
            group_name varchar(255),
            tab_id varchar(128) NOT NULL,
            tab_label varchar(255),
            topic_id varchar(64) NOT NULL,
            topic_title varchar(500),
            provider varchar(40) NOT NULL,
            external_document_id varchar(36) NOT NULL
                REFERENCES public.external_documents(id) ON DELETE CASCADE,
            source_url text NOT NULL,
            canonical_url text NOT NULL,
            url_hash varchar(64) NOT NULL,
            link_text varchar(500),
            link_source varchar(80),
            created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_zsxq_topic_external_documents_topic_url
                UNIQUE (group_id, tab_id, topic_id, url_hash)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zsxq_topic_external_documents_topic "
        "ON public.zsxq_topic_external_documents (group_id, tab_id, topic_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zsxq_topic_external_documents_group_provider "
        "ON public.zsxq_topic_external_documents (group_id, provider, updated_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zsxq_topic_external_documents_document "
        "ON public.zsxq_topic_external_documents (external_document_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zsxq_topic_external_documents_url_hash "
        "ON public.zsxq_topic_external_documents (url_hash)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_topic_external_documents_url_hash")
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_topic_external_documents_document")
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_topic_external_documents_group_provider")
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_topic_external_documents_topic")
    op.execute("DROP TABLE IF EXISTS public.zsxq_topic_external_documents")
    op.execute("DROP INDEX IF EXISTS public.ix_external_documents_content_hash")
    op.execute("DROP INDEX IF EXISTS public.ix_external_documents_tts_status")
    op.execute("DROP INDEX IF EXISTS public.ix_external_documents_summary_status")
    op.execute("DROP INDEX IF EXISTS public.ix_external_documents_provider_status")
    op.execute("DROP INDEX IF EXISTS public.ix_external_documents_url_hash")
    op.execute("DROP TABLE IF EXISTS public.external_documents")
