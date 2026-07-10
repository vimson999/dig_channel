-- ZSXQ projection phase 2: keep hot list pages off zsxq_topics.raw_payload.
-- Safe to rerun in DBeaver or psql.
--
-- Purpose:
--   1. Ensure local topic browse indexes exist.
--   2. Backfill historical image rows into zsxq_attachments.
--   3. Store image source context inside attachment raw_payload so the API can
--      reconstruct answer images without reading zsxq_topics.raw_payload.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

CREATE INDEX IF NOT EXISTS ix_zsxq_topics_group_tab_create_synced_desc
    ON public.zsxq_topics (group_id, tab_id, create_time DESC, last_synced_at DESC);

CREATE INDEX IF NOT EXISTS ix_zsxq_topics_group_create_synced_desc
    ON public.zsxq_topics (group_id, create_time DESC, last_synced_at DESC);

CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_topic_tab_created
    ON public.zsxq_attachments (group_id, topic_id, tab_id, created_at);

CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_tab_topic_type
    ON public.zsxq_attachments (group_id, tab_id, topic_id, attachment_type);

-- Refresh topic projection counters. New writes do this in application code;
-- this fixes old rows and rows written before the projection discipline existed.
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
        WHEN jsonb_typeof(raw_payload #> '{talk,article}') = 'object'
          OR jsonb_typeof(raw_payload -> 'article') = 'object'
            THEN 'article'
        ELSE 'talk'
    END,
    files_count =
        CASE WHEN jsonb_typeof(raw_payload -> 'files') = 'array' THEN jsonb_array_length(raw_payload -> 'files') ELSE 0 END
      + CASE WHEN jsonb_typeof(raw_payload #> '{talk,files}') = 'array' THEN jsonb_array_length(raw_payload #> '{talk,files}') ELSE 0 END
      + CASE WHEN jsonb_typeof(raw_payload #> '{article,files}') = 'array' THEN jsonb_array_length(raw_payload #> '{article,files}') ELSE 0 END
      + CASE WHEN jsonb_typeof(raw_payload #> '{talk,article,files}') = 'array' THEN jsonb_array_length(raw_payload #> '{talk,article,files}') ELSE 0 END,
    images_count =
        CASE WHEN jsonb_typeof(raw_payload -> 'images') = 'array' THEN jsonb_array_length(raw_payload -> 'images') ELSE 0 END
      + CASE WHEN jsonb_typeof(raw_payload #> '{talk,images}') = 'array' THEN jsonb_array_length(raw_payload #> '{talk,images}') ELSE 0 END
      + CASE WHEN jsonb_typeof(raw_payload #> '{article,images}') = 'array' THEN jsonb_array_length(raw_payload #> '{article,images}') ELSE 0 END
      + CASE WHEN jsonb_typeof(raw_payload #> '{talk,article,images}') = 'array' THEN jsonb_array_length(raw_payload #> '{talk,article,images}') ELSE 0 END
      + CASE WHEN jsonb_typeof(raw_payload #> '{question,images}') = 'array' THEN jsonb_array_length(raw_payload #> '{question,images}') ELSE 0 END
      + CASE WHEN jsonb_typeof(raw_payload #> '{answer,images}') = 'array' THEN jsonb_array_length(raw_payload #> '{answer,images}') ELSE 0 END,
    content = COALESCE(
        NULLIF(
            CONCAT_WS(E'\n\n',
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
WHERE raw_payload IS NOT NULL;

WITH topic_payloads AS (
    SELECT
        id,
        group_id,
        tab_id,
        topic_id,
        COALESCE(first_synced_at, last_synced_at, now()) AS created_at,
        COALESCE(last_synced_at, now()) AS updated_at,
        raw_payload::jsonb AS payload
    FROM public.zsxq_topics
    WHERE raw_payload IS NOT NULL
),
image_sources AS (
    SELECT *, 'topic' AS image_context, payload -> 'images' AS images
    FROM topic_payloads
    WHERE jsonb_typeof(payload -> 'images') = 'array'
    UNION ALL
    SELECT *, 'talk' AS image_context, payload #> '{talk,images}' AS images
    FROM topic_payloads
    WHERE jsonb_typeof(payload #> '{talk,images}') = 'array'
    UNION ALL
    SELECT *, 'article' AS image_context, payload #> '{article,images}' AS images
    FROM topic_payloads
    WHERE jsonb_typeof(payload #> '{article,images}') = 'array'
    UNION ALL
    SELECT *, 'talk_article' AS image_context, payload #> '{talk,article,images}' AS images
    FROM topic_payloads
    WHERE jsonb_typeof(payload #> '{talk,article,images}') = 'array'
    UNION ALL
    SELECT *, 'question' AS image_context, payload #> '{question,images}' AS images
    FROM topic_payloads
    WHERE jsonb_typeof(payload #> '{question,images}') = 'array'
    UNION ALL
    SELECT *, 'answer' AS image_context, payload #> '{answer,images}' AS images
    FROM topic_payloads
    WHERE jsonb_typeof(payload #> '{answer,images}') = 'array'
),
image_items AS (
    SELECT
        source.group_id,
        source.tab_id,
        source.topic_id,
        source.created_at,
        source.updated_at,
        source.image_context,
        item.ordinality::integer - 1 AS image_index,
        item.image_payload
    FROM image_sources AS source
    CROSS JOIN LATERAL jsonb_array_elements(source.images)
        WITH ORDINALITY AS item(image_payload, ordinality)
),
normalized AS (
    SELECT
        group_id,
        tab_id,
        topic_id,
        image_context,
        image_index,
        created_at,
        updated_at,
        LEFT(COALESCE(
            NULLIF(image_payload ->> 'file_id', ''),
            NULLIF(image_payload ->> 'image_id', ''),
            NULLIF(image_payload ->> 'id', ''),
            NULLIF(image_payload ->> 'hash_id', ''),
            NULLIF(image_payload ->> 'url', ''),
            image_context || '-' || image_index::text
        ), 255) AS attachment_key,
        LEFT(COALESCE(
            NULLIF(image_payload ->> 'name', ''),
            NULLIF(image_payload ->> 'title', ''),
            NULLIF(image_payload ->> 'url', ''),
            NULLIF(image_payload #>> '{original,url}', ''),
            NULLIF(image_payload #>> '{large,url}', ''),
            NULLIF(image_payload #>> '{thumbnail,url}', ''),
            '图片'
        ), 500) AS name,
        LEFT(COALESCE(
            NULLIF(image_payload ->> 'file_id', ''),
            NULLIF(image_payload ->> 'image_id', ''),
            NULLIF(image_payload ->> 'id', ''),
            NULLIF(image_payload ->> 'hash_id', '')
        ), 128) AS file_id,
        CASE
            WHEN COALESCE(
                NULLIF(image_payload ->> 'download_count', ''),
                NULLIF(image_payload ->> 'downloads_count', ''),
                NULLIF(image_payload ->> 'downloadCount', ''),
                NULLIF(image_payload ->> 'downloadsCount', '')
            ) ~ '^[0-9]+$'
                THEN COALESCE(
                    NULLIF(image_payload ->> 'download_count', ''),
                    NULLIF(image_payload ->> 'downloads_count', ''),
                    NULLIF(image_payload ->> 'downloadCount', ''),
                    NULLIF(image_payload ->> 'downloadsCount', '')
                )::integer
            ELSE NULL
        END AS download_count,
        CASE
            WHEN jsonb_typeof(image_payload) = 'object'
                THEN jsonb_set(image_payload, '{_zsxq_image_context}', to_jsonb(image_context), true)
            ELSE jsonb_build_object('url', image_payload::text, '_zsxq_image_context', image_context)
        END AS payload_with_context
    FROM image_items
),
with_ids AS (
    SELECT
        substr(stable_hash, 1, 8)
            || '-' || substr(stable_hash, 9, 4)
            || '-' || substr(stable_hash, 13, 4)
            || '-' || substr(stable_hash, 17, 4)
            || '-' || substr(stable_hash, 21, 12) AS id,
        group_id,
        tab_id,
        topic_id,
        attachment_key,
        'image' AS attachment_type,
        name,
        file_id,
        download_count,
        payload_with_context AS raw_payload,
        created_at,
        updated_at
    FROM (
        SELECT
            md5(CONCAT_WS(':', group_id, tab_id, topic_id, attachment_key)) AS stable_hash,
            *
        FROM normalized
    ) AS hashed
)
INSERT INTO public.zsxq_attachments (
    id,
    group_id,
    tab_id,
    topic_id,
    attachment_key,
    attachment_type,
    name,
    file_id,
    download_count,
    raw_payload,
    created_at,
    updated_at
)
SELECT
    id,
    group_id,
    tab_id,
    topic_id,
    attachment_key,
    attachment_type,
    name,
    file_id,
    download_count,
    raw_payload,
    created_at,
    updated_at
FROM with_ids
WHERE attachment_key IS NOT NULL
ON CONFLICT ON CONSTRAINT uq_zsxq_attachments_group_tab_topic_key
DO UPDATE SET
    attachment_type = EXCLUDED.attachment_type,
    name = EXCLUDED.name,
    file_id = EXCLUDED.file_id,
    download_count = EXCLUDED.download_count,
    raw_payload = EXCLUDED.raw_payload,
    updated_at = now();

ANALYZE public.zsxq_topics;
ANALYZE public.zsxq_attachments;

-- Verification: historical image projection coverage.
SELECT
    count(*) FILTER (WHERE images_count > 0) AS topics_with_images,
    COALESCE(sum(images_count), 0) AS projected_topic_image_count
FROM public.zsxq_topics;

SELECT
    count(*) FILTER (WHERE attachment_type = 'image') AS image_attachment_rows,
    count(*) FILTER (
        WHERE attachment_type = 'image'
          AND raw_payload::jsonb ? '_zsxq_image_context'
    ) AS image_attachment_rows_with_context,
    count(*) FILTER (WHERE attachment_type = 'file') AS file_attachment_rows
FROM public.zsxq_attachments;

SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
      'ix_zsxq_topics_group_tab_create_synced_desc',
      'ix_zsxq_topics_group_create_synced_desc',
      'ix_zsxq_attachments_group_topic_tab_created',
      'ix_zsxq_attachments_group_tab_topic_type'
  )
ORDER BY tablename, indexname;
