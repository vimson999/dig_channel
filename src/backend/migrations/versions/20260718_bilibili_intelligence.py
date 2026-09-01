"""add Bilibili competitor intelligence tables

Revision ID: 20260718_bilibili_intelligence
Revises: 20260716_zsxq_topic_comments
"""

from alembic import op


revision = "20260718_bilibili_intelligence"
down_revision = "20260716_zsxq_topic_comments"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.bilibili_competitors (
            id varchar(36) PRIMARY KEY,
            name varchar(255) NOT NULL,
            uid varchar(32) NOT NULL UNIQUE,
            content_type varchar(32) NOT NULL,
            source_url text,
            active boolean NOT NULL DEFAULT true,
            last_synced_at timestamp,
            latest_published_at timestamp,
            last_error text,
            video_count integer,
            created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # ``uid ... UNIQUE`` already creates the constraint-owned unique index.
    # Avoid the historical standalone duplicate on fresh installations.
    op.execute("CREATE INDEX IF NOT EXISTS ix_bilibili_competitors_content_type ON public.bilibili_competitors (content_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bilibili_competitors_active ON public.bilibili_competitors (active)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bilibili_competitors_type_active ON public.bilibili_competitors (content_type, active)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.bilibili_competitor_videos (
            id varchar(36) PRIMARY KEY,
            competitor_id varchar(36) NOT NULL REFERENCES public.bilibili_competitors(id) ON DELETE CASCADE,
            content_type varchar(32) NOT NULL,
            bvid varchar(32) NOT NULL UNIQUE,
            aid varchar(32),
            title varchar(500) NOT NULL,
            description text,
            cover_url text,
            duration_seconds integer,
            published_at timestamp,
            play_count integer,
            comment_count integer,
            danmaku_count integer,
            is_charging boolean NOT NULL DEFAULT false,
            raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            match_status varchar(32) NOT NULL DEFAULT 'pending',
            match_method varchar(32),
            match_confidence double precision,
            match_relation varchar(32),
            matched_local_type varchar(40),
            matched_local_id varchar(64),
            matched_local_title varchar(500),
            local_exists boolean,
            orchestration_status varchar(32),
            orchestration_task_id varchar(36),
            match_reason text,
            match_error text,
            match_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            matched_at timestamp,
            decision_status varchar(32) NOT NULL DEFAULT 'pending',
            decision_note text,
            decided_at timestamp,
            first_seen_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # ``bvid ... UNIQUE`` above already creates the constraint-owned unique
    # index.  Do not recreate the historical standalone duplicate on fresh
    # installations; live databases use the separately gated cleanup tool.
    op.execute("CREATE INDEX IF NOT EXISTS ix_bilibili_competitor_videos_competitor_id ON public.bilibili_competitor_videos (competitor_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bilibili_competitor_videos_content_type ON public.bilibili_competitor_videos (content_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bilibili_competitor_videos_published_at ON public.bilibili_competitor_videos (published_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bilibili_competitor_videos_match_status ON public.bilibili_competitor_videos (match_status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bilibili_competitor_videos_matched_local_id ON public.bilibili_competitor_videos (matched_local_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bilibili_competitor_videos_orchestration_status ON public.bilibili_competitor_videos (orchestration_status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bilibili_competitor_videos_orchestration_task_id ON public.bilibili_competitor_videos (orchestration_task_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bilibili_competitor_videos_decision_status ON public.bilibili_competitor_videos (decision_status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bilibili_competitor_videos_competitor_published ON public.bilibili_competitor_videos (competitor_id, published_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bilibili_competitor_videos_type_match_published ON public.bilibili_competitor_videos (content_type, match_status, published_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bilibili_competitor_videos_decision_published ON public.bilibili_competitor_videos (decision_status, published_at DESC)")

    op.execute(
        """
        INSERT INTO public.bilibili_competitors
            (id, name, uid, content_type, source_url, active, created_at, updated_at)
        VALUES
            ('bili-report-xiaobai', '小白投资笔记', '1221628467', 'report', 'https://space.bilibili.com/1221628467/upload/video', true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            ('bili-report-us-stock', '美股研报局', '3546604889967195', 'report', 'https://space.bilibili.com/3546604889967195/upload/video', true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            ('bili-audio-snowball', '滚雪球之路', '1938835498', 'audio', 'https://space.bilibili.com/1938835498/video', true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            ('bili-audio-starlight', '星辰财经-', '3494372945431057', 'audio', 'https://space.bilibili.com/3494372945431057/video', true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (uid) DO NOTHING
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS public.bilibili_competitor_videos")
    op.execute("DROP TABLE IF EXISTS public.bilibili_competitors")
