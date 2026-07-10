# DB Projection Upgrade Discipline

This project keeps full JSON payloads for audit/debug, but hot application paths
must use structured projection columns or child tables.

## Upgrade Order

1. Define the application read scenario first.
2. Add or reuse structured columns/tables for the fields shown, filtered, sorted,
   joined, counted, or polled by the UI/workers.
3. Project new writes at ingest time.
4. Backfill historical rows with a rerunnable SQL script.
5. Move query endpoints to the projection fields.
6. Add tests that prevent hot paths from falling back to large JSON scans.
7. Verify with slow_query.log after restart.

## Rules

- Do not filter, sort, or search hot list pages by casting `params`, `result`, or
  `raw_payload` to text.
- `publication_tasks.params/result` and `zsxq_topics.raw_payload` are audit
  payloads. They are acceptable for detail views, repair tools, and explicit
  raw export/debug actions.
- ZSXQ local topic list pages should read `zsxq_topics` lightweight columns and
  `zsxq_attachments` projected rows. They should not hydrate
  `zsxq_topics.raw_payload` from DB unless `include_raw_payload=true`.
- If a new feature adds data to JSON and later needs to list/filter/sort it, add
  the projection at the same time as the ingest code.
- Every production SQL hotfix should include verification queries and be safe to
  rerun.

## Current Projection Points

- `PublicationTask.sync_projection_fields()` projects feature/user/source/title
  and report-audio workflow list fields from `params/result`.
- `ZsxqTopic.sync_projection_fields()` projects topic type, question/answer
  text, article summary, file count, and image count from `raw_payload`.
- `ZsxqSyncService._upsert_attachments()` projects files/images into
  `zsxq_attachments`; image attachments include `_zsxq_image_context` for API
  reconstruction.

## Migration Scripts

- `docs/sql/wide_projection_phase1_dbeaver_20260629.sql`
- `docs/sql/publication_task_relationship_hot_indexes_dbeaver_20260709.sql`
- `docs/sql/zsxq_projection_phase2_images_dbeaver_20260709.sql`
