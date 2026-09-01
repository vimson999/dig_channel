---
name: doubao-dialogue-tts
description: Use when turning a named analyst's research report plus host interpretation into a multi-voice spoken program, especially when the source must remain verbatim, the analyst should remain the primary voice, or the approved text will be sent to a paid TTS service.
---

# Doubao Dialogue TTS

Create a reviewable dialogue manifest first, then synthesize only after the user authorizes paid API use.

## Channel profile routing

Resolve the editorial profile before drafting any spoken text:

1. An explicit `editorial.profile_id` in the episode manifest or user request wins.
2. Otherwise, search upward from the current project, source, or manifest directory for `.doubao-dialogue-tts.json` and use its `default_profile`.
3. An explicit `generic` disables a project default. When no profile is found, remain generic; never carry a profile into another project by memory or inference.

When a profile is active, read its complete JSON from `references/channel-profiles/<profile_id>.json` before drafting. Treat its fixed opening, audience relationship, spoken aliases, commentary contract, forbidden host phrases, closing policy, and voice defaults as one contract. Record the resolved ID in `editorial.profile_id`; a clear episode-specific user instruction may override a default.

## Editorial hierarchy

When the program is built around a named report author, the author is the reason the audience is listening. For a Hong Hao report, for example, Hong Hao's argument—not the host's personality—is the primary content.

Use these planning defaults when applicable: `source_protection: strict_verbatim`, `author_priority: source_first`, and `host_mode: explanatory_secondary`. “Secondary” describes authorship and attribution, not a required shorter speaking time.

- The host's success criterion is audience comprehension: after the commentary, listeners should better understand what the report author means, how the argument works, what evidence it relies on, and where its conclusion stops.
- Shape each useful insertion around the active profile's positive commentary contract. In generic mode, identify what the preceding passage contributes, explain at least one mechanism or term, connect it to the surrounding argument, and finish with a relevant condition, boundary, or observable variable when one is needed. Do not force every insertion into identical phrasing.
- Keep requested source passages complete, contiguous, and clearly attributed.
- Decide host length by interpretive need, not by a fixed character or time quota. A host explanation may be brief or extensive when it materially improves the audience's understanding.
- Do not force equal time or artificial alternation. Segment by comprehension and argument flow, while keeping the report author's viewpoint and attribution primary.
- The host explains terms, supplies necessary context, connects passages, and flags relevant boundaries; it may develop those explanations at length, but it must not replace the author's thesis with a new host-led essay.
- Keep external verification, corrections, sources, and editorial uncertainty in non-spoken review notes by default. Only move a discrepancy into speech when it materially changes audience understanding, and frame it without turning the host into the author's reviewer. Never attribute a host addition to the report author.

## Workflow

1. Resolve the channel profile, then ingest all supplied material. Use pasted text directly; for DOCX input, extract it with `scripts/extract_docx.py`.
2. Read the complete source and commentary before segmenting either one.
3. Map every requested source range and commentary insertion point in source order. If an anchor is missing or malformed, use the smallest defensible contiguous range, disclose that assumption in the review copy, and ask only when different boundaries would materially change the program.
4. Decide and label the source mode:
   - use `verbatim` when wording is unchanged apart from an explicitly approved pronunciation-safe normalization;
   - use `locked_verbatim` when the user says “不要动原文”“原文逐字保留” or equivalent. In this mode, do not correct typos, OCR, punctuation, numbers, names, English, or factual claims in source passages. Put any correction or qualification in host commentary or a non-spoken note;
   - use `adapted` and label it “研报整理版” or “原文转述” only when the user permits substantive editing;
   - never call adapted material a verbatim original passage.
5. Complete an editorial pass before building the manifest:
   - keep the source author's claims distinct from host additions;
   - for time-sensitive or high-stakes claims added by the host, verify authoritative sources, correct or qualify unsupported precision, and keep citations outside spoken text;
   - keep source discrepancies and editorial corrections in non-spoken review notes unless they materially change what the listener must understand; do not turn routine fact checking into host performance;
   - apply the active profile's spoken-author aliases only to host speech; source passages and non-spoken metadata retain the source's exact wording and real names;
   - decide insertion count by distinct comprehension value, not article length or a fixed target; merge adjacent repeated explanations;
   - remove production labels that should not be spoken;
   - correct broken range descriptions in editorial metadata only; correct name/term errors only in host/commentary or non-spoken notes, never inside a locked_verbatim source passage;
   - add short transitions where the speaker change would otherwise feel abrupt;
   - convert tables, chart directions, numerals, dates, currencies, percentages, symbols, and abbreviations into natural spoken language when needed;
   - keep DOCX body text separate from embedded images, charts, tables, and OCR fragments; do not silently merge visual text into a locked source passage;
   - keep the source's viewpoint and attribution primary while allowing host interpretation to be as detailed as comprehension requires; do not use duration or character count as a proxy for viewpoint priority; do not add a host preamble explaining the two-voice setup or narrating the editing process;
   - remove process narration such as “本期采用两条声线”“原文保持不动”“按要求完整保留”“初步分析稿中……” from spoken text. Keep that information in metadata or review notes;
   - scan host speech against the active profile's forbidden phrases before review;
   - remove operator packages, tags, and visual-only instructions unless the listener needs a concise narrated version;
   - do not silently strengthen claims or attribute added analysis to the source author.
6. Create a human-reviewable `final-script.md`, then build a UTF-8 JSON manifest from the same approved segment texts using `references/manifest-schema.md`. Treat the manifest as the synthesis source of truth. If either artifact changes, update the other and repeat the dry run. In locked_verbatim mode, compare every source block against the extracted DOCX/TXT paragraphs and fail the review if any character differs.
7. Keep each editorial unit logical and replaceable: opening, source passage, commentary, source passage, commentary, closing.
8. Apply the active profile's voice defaults, then preserve any explicit episode override. With no active profile, the standard pairing is:
   - host/commentary: `zh_female_qingchezizi_uranus_bigtts` (清澈)
   - source/original: `zh_male_liufei_uranus_bigtts` (刘飞)
9. Set manifest `model` to `seed-tts-2.0-standard` unless a verified API model is explicitly requested. Do not use the Resource ID as the model.
10. Run a no-cost validation first:

```bash
python -B scripts/dialogue_tts.py manifest.json --output-dir output --dry-run
```

11. Before a paid run, show the user the active profile and its source, segment count, character count, request model, voice IDs and effective speeds, output location, and a data-transfer notice: the approved spoken text will be sent to the Doubao TTS service. Obtain affirmative consent to that external transfer unless the user already explicitly authorized it after seeing the notice.
12. Run synthesis only after explicit paid authorization. A direct instruction such as “开始合成” or “运行” counts when it follows the destination and payload disclosure:

```bash
python -B scripts/dialogue_tts.py manifest.json --output-dir output --env-file /path/to/.env
```

13. Validate `generation-report.json`, every logical segment MP3/WAV, and both complete-program files. Do not report success if any requested segment failed.
14. After the final duration is known, recommend a content monetization node when the user needs one. Prefer the end of a complete, already valuable free section whose final sentence naturally opens the program's most differentiated remaining logic. Report the timestamp, preceding sentence, next sentence, and remaining paid duration; do not use a fixed percentage and do not split the audio unless requested.
15. Deliver the complete MP3 first, plus WAV, review script, monetization note when applicable, and report links. In clients that support local media playback, embed the MP3 with an absolute filesystem path.

## Charging terminology

- **API billing boundary:** the first real external TTS request after review, disclosure, and affirmative authorization. Drafting, review, `--dry-run`, cached reuse, and local merging do not send a new synthesis request.
- **Content monetization node:** the timestamp where free listening ends. Choose it only from the final audio timeline using editorial value and a natural transition; it is unrelated to when the TTS provider may bill.

## Execution rules

- Never store an API key in the Skill, manifest, logs, or output report.
- Read credentials from `DOUBAO_TTS_API_KEY`; optionally accept `VOLCENGINE_TTS_API_KEY`, `VOLC_API_KEY`, or `X_API_KEY`.
- Keep the request model and Resource ID separate:
  - manifest/API request model: `seed-tts-2.0-standard`;
  - `X-Api-Resource-Id` / `DOUBAO_TTS_RESOURCE_ID`: `seed-tts-2.0` by default.
- If the API returns business error `45000001`, first verify that `seed-tts-2.0` was not mistakenly placed in manifest `model`.
- Treat logical segments separately from API chunks. Split long text internally, but merge the chunks back into one logical segment file.
- Reuse a completed segment only when its text, voice, model, speed, loudness, and audio settings hash all match.
- Preserve successful segments on retry. Regenerate only missing or stale outputs.
- After a localized script edit, rerun against the same output directory so matching segments are reused and only changed segments incur synthesis work; always rebuild and revalidate the complete program.
- Use punctuation-aware splitting. Never cut a number, Latin token, or sentence when a nearby sentence boundary is available.
- Keep source and commentary pauses configurable in the manifest.
- Prefer a 450-650 ms speaker-change pause and a 250-400 ms same-speaker pause unless the user specifies timing.
- Save the effective manifest beside outputs for auditability.
- Use `ffmpeg`/`ffprobe` for decoding and validation. Stop with a clear error when either is unavailable.
- Use a Python interpreter that provides `websocket-client`. Do not install packages without user approval.
- Prefer an existing project virtual environment or configured runtime that already provides `websocket-client`; check it before attempting any installation.
- If the sandbox blocks the TTS WebSocket connection, request the required network authorization. Do not use an indirect or alternate route to bypass the block.

## Output contract

Produce:

```text
project/
├── final-script.md
├── manifest.json
└── output/
    ├── effective-manifest.json
    ├── generation-report.json
    ├── monetization-note.md
    ├── segments/
    │   ├── 001_host_opening_qingche.mp3
    │   ├── 001_host_opening_qingche.wav
    │   └── ...
    ├── complete-program.wav
    └── complete-program.mp3
```

Keep `.chunks/` as resumable internal state. Do not present it as a deliverable unless debugging.

## Quality gate

Confirm all of the following:

- manifest IDs are unique and sequence numbers are contiguous;
- the resolved profile ID and selection source are visible in the dry-run summary and effective manifest;
- when a profile is active, its fixed opening, host-only author aliases, forbidden host phrases, and effective voice defaults pass validation;
- source/commentary order matches the intended dialogue;
- `final-script.md` and manifest contain the same spoken text for every segment;
- source passages are accurately labeled as verbatim or adapted;
- in locked_verbatim mode, every source passage is an exact paragraph-level match to the extracted source;
- the named report author's viewpoint and attribution remain primary; host commentary may be short or long when it is clearly explanatory rather than a replacement thesis;
- host commentary increases comprehension of the author's argument by adding explanation, context, connections, or carefully marked boundaries;
- every segment has non-empty text and a known voice;
- no production labels or process narration such as “插入点”“原文播放范围”“两条声线”“原文保持不动”“按要求保留” remain in spoken text;
- no table rows, chart directions, unsupported precision, or review-only notes remain as unnatural spoken text;
- request model is not confused with the Resource ID;
- all audio files are non-empty and decodable;
- generated segment count equals requested segment count;
- the complete WAV duration is consistent with segment durations plus pauses;
- the complete MP3 is decodable and has a positive duration;
- `generation-report.json` contains no failed items.
- any content monetization recommendation uses a verified final-audio timestamp and states the remaining paid duration.
