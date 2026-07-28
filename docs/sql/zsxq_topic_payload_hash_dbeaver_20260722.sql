-- ZSXQ topic payload fingerprint migration.
-- Run in DBeaver as postgres/admin with Auto-commit enabled.
-- The whole change is transactional; any failed assertion rolls everything back.

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '5min';

DO $guard$
DECLARE
    current_revision text;
    table_owner text;
    is_superuser boolean;
BEGIN
    SELECT pg_get_userbyid(relowner)
    INTO table_owner
    FROM pg_class
    WHERE oid = 'public.zsxq_topics'::regclass;

    SELECT rolsuper
    INTO is_superuser
    FROM pg_roles
    WHERE rolname = current_user;

    IF current_user <> table_owner AND NOT coalesce(is_superuser, false) THEN
        RAISE EXCEPTION
            'Run as postgres/admin or zsxq_topics owner; current_user=%, owner=%',
            current_user,
            table_owner;
    END IF;

    SELECT version_num INTO current_revision FROM public.alembic_version;
    IF current_revision NOT IN (
        '20260722_report_audio_keyframe',
        '20260722_zsxq_topic_payload_hash'
    ) THEN
        RAISE EXCEPTION
            'Unexpected Alembic revision %, expected 20260722_report_audio_keyframe',
            current_revision;
    END IF;
END
$guard$;

ALTER TABLE public.zsxq_topics
    ADD COLUMN IF NOT EXISTS raw_payload_hash varchar(32);

UPDATE public.zsxq_topics
SET raw_payload_hash = md5(raw_payload::jsonb::text)
WHERE raw_payload IS NOT NULL
  AND raw_payload_hash IS DISTINCT FROM md5(raw_payload::jsonb::text);

UPDATE public.alembic_version
SET version_num = '20260722_zsxq_topic_payload_hash'
WHERE version_num = '20260722_report_audio_keyframe';

DO $verify$
DECLARE
    mismatch_count bigint;
    current_revision text;
BEGIN
    SELECT count(*) INTO mismatch_count
    FROM public.zsxq_topics
    WHERE raw_payload IS NOT NULL
      AND raw_payload_hash IS DISTINCT FROM md5(raw_payload::jsonb::text);

    SELECT version_num INTO current_revision FROM public.alembic_version;
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION 'raw_payload_hash mismatch rows=%', mismatch_count;
    END IF;
    IF current_revision <> '20260722_zsxq_topic_payload_hash' THEN
        RAISE EXCEPTION 'Alembic revision was not advanced: %', current_revision;
    END IF;
END
$verify$;

COMMIT;

SELECT
    count(*) AS total_topics,
    count(raw_payload_hash) AS hashed_topics,
    count(*) FILTER (
        WHERE raw_payload IS NOT NULL
          AND raw_payload_hash IS DISTINCT FROM md5(raw_payload::jsonb::text)
    ) AS mismatched_topics
FROM public.zsxq_topics;

SELECT version_num FROM public.alembic_version;
