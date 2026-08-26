# Maintenance Scripts

This directory holds one-off repair or migration scripts that are not part of normal app startup.

Keep shell entrypoints under `scripts/dev/` or `scripts/maintenance/`; the project root should stay limited to config, docs, and dependency files.

## Canonical cleaned-asset backfill

After the `content_pipeline_assets` cleaned-output migration is applied, preview
historical task mappings before changing them:

```bash
PYTHONPATH=src/backend venv/bin/python scripts/maintenance/backfill_content_pipeline_cleaned_assets.py --dry-run --limit 200
```

Apply the verified mappings with the same filter:

```bash
PYTHONPATH=src/backend venv/bin/python scripts/maintenance/backfill_content_pipeline_cleaned_assets.py --limit 200
```

For a narrowly scoped repair, repeat `--task-id <UUID>` and optionally add
`--media-type pdf` or `--media-type audio`. The script only registers output
files under `CONTENT_PIPELINE_OUTPUT_ROOT`; ambiguous, missing, or invalid
records are reported as unresolved and are never mapped to a raw download.
