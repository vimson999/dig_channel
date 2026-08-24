# Knowledge Planet Sidebar Deduplication and Manifest Integrity Design

## Context

The 2026-08-18 MP3 sidebar run downloaded files that had already been accepted by an earlier browser-import run, downloaded one MP3 twice within the same run, and then produced a manifest whose declared filenames did not match the actual downloaded paths. The malformed manifest failed validation and never entered the local import workflow, but the history endpoint still consumed its inbox JSON as if it were trusted history.

The scheduled automation stays paused while this repair is developed and manually accepted.

## Confirmed failure modes

1. Historical identity compared raw timestamp strings. `2026-08-17T13:39+08:00` and `2026-08-17T13:39:00+08:00` describe the same instant but produced different identities.
2. Historical identity included `source_url`, although the fixed tab sometimes emitted differently encoded or malformed URL variants for the same file.
3. The history service trusted every `sidebar-submit-*.json` in the inbox. A deterministic inline manifest with no durable `ZsxqBrowserSubmission` receipt therefore polluted future deduplication after validation failed.
4. The sidebar could click a stale detail modal. The intended candidate filename, visible detail filename, and actual browser download filename were not bound together before and after the click.
5. A run had no explicit set of normalized actual filenames, so a Chrome `(1)` duplicate could enter the same manifest.
6. Final counters could diverge from the frozen manifest because reporting was not required to derive from the exact submitted object.

## Desired behavior

### Canonical historical identity

For browser-import history, a file's primary identity inside the fixed kind/group/tab and three-day window is:

```
normalized_filename
```

`normalized_filename` uses Unicode NFKC normalization, strips a terminal Chrome duplicate suffix such as ` (1)` before the extension, removes invisible spacing, collapses whitespace, and case-folds Latin text. `canonical_published_at` still parses ISO-8601 values and serializes the instant in UTC at second precision, but is audit metadata rather than identity. The fixed kind/group/tab and recent-history window already supply scope; both `published_at` and `source_url` have been observed with inconsistent values for the same download and therefore cannot split identity.

When no usable filename exists, the service may fall back to `file_id`, then SHA-256. Rows without any stable identity remain distinct and are marked incomplete rather than silently merged.

The history response exposes `normalized_filename`, `published_at_canonical`, and `dedupe_key` so the sidebar does not need to reproduce backend parsing ambiguously.

### Trusted history sources

Legacy history files remain readable for backward compatibility. New deterministic inline manifests, recognized by their terminal 12-hex digest filename, count as trusted history only when a matching `ZsxqBrowserSubmission.manifest_path` exists in the database.

Any durable receipt status counts as accepted history because backend validation completed before the receipt was reserved. A deterministic manifest without a receipt is ignored and counted in `ignored_untrusted_manifests`. When receipt verification is requested and the database cannot be queried, the endpoint fails closed with incomplete history.

### Manifest integrity

Before reserving a run ID, the backend rejects a manifest when:

- `scan_summary.new_download_success` is present and differs from `len(files)`;
- two entries resolve to the same normalized actual basename;
- file preview detects duplicate content within the manifest;
- a declared filename does not correspond to the actual path basename;
- an expected local file is absent or otherwise fails the existing preview checks.

A definite validation failure must not reserve the run ID. A retry may correct the manifest and reuse the run ID. Once a receipt exists, the existing idempotency contract remains unchanged.

### Sidebar modal and download binding

Both MP3 and PDF skills must:

1. maintain a set of candidate dedupe keys and normalized actual basenames for the current run;
2. after opening a detail modal, compare the expected filename with the modal filename;
3. on first mismatch or missing data, keep the same modal open, wait at least two seconds, and reread once;
4. after a second mismatch, record `detail_file_mismatch_after_2s` and skip without clicking;
5. after browser download completion, compare the landed basename (after Chrome suffix normalization) with the expected filename;
6. allow only one in-flight download and wait for the current candidate's exact completion event before opening another candidate;
7. record `downloaded_file_mismatch` and stop further downloads when the binding is unsafe;
8. record `duplicate_actual_file_in_same_run` and stop when the normalized actual basename already exists in the run or trusted history;
9. freeze one manifest object, validate its counters and files, submit that exact object, and derive the final report only from it and the submit result.

## Recovery of the failed run

The malformed 2026-08-18 manifest must remain untrusted. Recovery imports only the six confirmed new unique MP3 files using a new run ID and excludes:

- twelve files already accepted by the earlier completed run;
- the same-run Chrome `(1)` duplicate;
- any missing or filename/path-mismatched entry.

Recovery is performed only after the repaired code passes its focused and regression tests.

## Acceptance gates

1. Backend unit tests prove canonical identity, untrusted-manifest filtering, and manifest integrity.
2. Both project skill copies and installed skill copies contain the same production rules: 48-hour window, 40 unique topic groups, maximum 40 downloads.
3. Manual MP3 and PDF sidebar runs complete with no historical duplicate, no same-run duplicate, matching counters, and a queued or replayed task ID.
4. After the user accepts those runs, both skills are temporarily changed to 24 hours, 10 unique topic groups, and maximum 5 downloads for a bounded operational test.
5. If that test succeeds, restore 48/40/40, verify copies, and only then resume the four-hour automation.

## Non-goals

- Do not redesign the existing Knowledge Planet API ingestion path.
- Do not delete downloaded duplicate files automatically.
- Do not resume or modify the scheduled automation before the stated gates.
- Do not change MP3/PDF threshold values or the three-day history window.
