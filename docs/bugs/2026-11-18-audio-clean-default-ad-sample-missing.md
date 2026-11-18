# Audio batch failed because its runtime ad sample was missing

- Affected task: d8bc26e0-87a8-4b12-9bc0-657d567a8f68.
- Evidence: the batch manifest records 34 failed files, all with the same missing default-ad-sample.wav error. No valid outputs were produced.
- The task exception handler discarded the adapter result, leaving the UI with zero successes and zero failures and a generic missing-output message.
- Restored the tracked nine-second runtime ad sample. It is an application dependency, not disposable generated media.
- Preserve adapter and canonical-registration results before any failure. Include underlying per-file errors and success/failure/skip counts in the no-output exception.
- Repaired the affected failed task's statistics from its on-disk manifest.
- Regression: tests/unit/test_audio_cleaner_output_limit.py verifies the default sample exists and is nonempty.
