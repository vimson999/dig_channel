# Report Audio Gem V2 rollout record

Updated: 2026-08-12 18:16 CST

## Outcome

The report-audio workflow now has an explicit `report_audio_gem_v2` handoff between the configured Gemini Gem and downstream production. New API submissions default to `shadow`, so the Gem receives only a compact runtime trigger and returns one structured package, while the current DeepSeek normalization and title-review stages remain authoritative until measured evidence supports a cutover.

No content production, TTS, video rendering, or publishing is part of this rollout verification.

## Runtime modes

| Mode | Gemini request | Contract handling | DeepSeek behavior | Intended use |
|---|---|---|---|---|
| `off` | Legacy prompt plus generic review/follow-ups | None | Normalizer and title generation/review run | Immediate rollback and legacy comparison |
| `shadow` | Minimal trigger; no generic review/follow-up | Validate; repair at most once; record compact diagnostics | Both existing stages still run | Current default and evidence collection |
| `direct` | Minimal trigger; no generic review/follow-up | Validate; repair at most once | Skip both stages only when valid; otherwise fall back to both | Explicit canary only |

Configuration: request field `gem_v2_mode`, or environment default `REPORT_AUDIO_GEM_V2_MODE`. The code default is `shadow`. Unsupported values fail before task creation.

## Contract and safety boundary

The pure validator checks the schema version, exact source filename, one-report shape, script minimum length, paragraph/evidence alignment, continuous indexes, claim references, three candidates per title format, selected-candidate consistency, cover/landscape consistency, material boundary, evidence page range when the actual PDF can be opened, report/batch readiness, and QC counters.

The generic Gemini browser worker receives a contract callback only for this workflow. When the first answer is invalid it submits one error-specific prompt in the same Gem session and treats that answer as a complete replacement. A second invalid answer is returned to the parent; it is never repaired repeatedly and is never concatenated with the first JSON.

The parent independently validates the final child response. This second check is the authority for `direct`; child-reported validity cannot itself bypass DeepSeek.

## Persisted comparison evidence

The parent result stores `gem_v2` diagnostics without duplicating the full response:

- mode and selected route (`shadow_deepseek`, `direct`, or `deepseek_fallback`);
- validity and stable error codes;
- non-whitespace script length and paragraph count;
- selected candidate IDs and `output_ready`;
- repair diagnostics remain available on the Gemini child result.

The full Gem answer remains on the child task. Shadow mode keeps the DeepSeek-compatible `structured_result` on the parent, enabling paired comparison without changing production output.

## Direct-cutover criteria

Do not change the default to `direct` from a single successful PDF. Collect a diverse shadow cohort and require all of the following:

1. High valid-on-first-pass rate, with repair frequency and failure codes stable across institutions, Chinese/English reports, tables, scans, and long documents.
2. No unsupported claims or wrong page references in manual evidence audits.
3. Script completeness, factuality, TTS cleanliness, and paragraph/evidence alignment at least match the DeepSeek path.
4. Content, landscape, and vertical selected titles are consistently usable without a downstream rewrite and remain faithful to the PDF thesis.
5. Latency/cost reduction is material after accounting for the optional repair turn.
6. Invalid results demonstrably fall back without triggering TTS or losing task traceability.

Start direct testing through one explicit `gem_v2_mode=direct`, `script_material_only=true` canary. Roll back immediately with `gem_v2_mode=off` or `REPORT_AUDIO_GEM_V2_MODE=off` if evidence or title consistency regresses.

## Verification and restart status

- `364 passed, 41 subtests passed` across Gem V2, report-audio service/task/API, Gemini task, Gemini profile recovery, storyboard, and MP3/Gemini regression suites.
- Shadow/direct unit coverage proves minimal trigger submission, absence of generic follow-ups, DeepSeek preservation in shadow, valid-only bypass in direct, invalid fallback, one repair maximum, and V2 extension-field preservation.
- Modified backend modules compile successfully and the scoped diff has no whitespace errors.
- The restart safety command was attempted at 2026-08-12 18:13 CST. It failed closed because the sandbox could not reach the project database. The required read-only escalation was then rejected by the current Codex usage limit. Because the safety gate could not prove the absence of non-interruptible tasks, the system was not restarted and no live smoke task was submitted. Do not claim these processes have loaded the new code.
- The pre-existing backend still answers `GET http://127.0.0.1:5001/health` with `status=healthy`, `version=1.0.0`; this only proves the old process remains healthy, not that it imported this working-tree revision.
- Next operational step: rerun `venv/bin/python scripts/dev/check-restart-safety.py` with database access. Only when it prints `OK: no non-interruptible processing tasks found.` run `./scripts/dev/restart-dev-stack.sh`, then verify `/health`, the report-audio and report-PDF-Gemini workers, and one `script_material_only=true`, `gem_v2_mode=shadow` job. Do not use `FORCE_RESTART_ACTIVE_TASKS=1` for this rollout.
