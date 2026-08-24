---
name: doubao-dialogue-tts
description: Build a two-voice or multi-voice spoken dialogue from source text and commentary, especially alternating original passages with host analysis. Use when Codex needs to extract DOCX/TXT material, align commentary to source order, lightly edit spoken transitions, route each logical segment to a Doubao Seed-TTS voice, save replaceable per-segment audio, resume failed synthesis, and merge a complete MP3/WAV program.
---

# Doubao Dialogue TTS

Create a reviewable dialogue manifest first, then synthesize only after the user authorizes paid API use.

## Workflow

1. Extract source documents with `scripts/extract_docx.py`.
2. Read both extracted paragraph lists completely.
3. Preserve the source article's order. Make only light edits to commentary:
   - remove production labels that should not be spoken;
   - correct broken range descriptions and obvious name/term errors;
   - add short transitions where the speaker change would otherwise feel abrupt;
   - do not silently strengthen claims or attribute added analysis to the source author.
4. Build a UTF-8 JSON manifest using the schema in `references/manifest-schema.md`.
5. Keep each editorial unit logical and replaceable: opening, source passage, commentary, source passage, commentary, closing.
6. Set the requested voice per role. For the standard pairing use:
   - host/commentary: `zh_female_qingchezizi_uranus_bigtts` (清澈)
   - source/original: `zh_male_liufei_uranus_bigtts` (刘飞)
7. Run a no-cost validation first:

```bash
python -B scripts/dialogue_tts.py manifest.json --output-dir output --dry-run
```

8. Show the user the segment count, character count, voice assignment, and output location before a paid run unless the user already explicitly authorized generation.
9. Run synthesis:

```bash
python -B scripts/dialogue_tts.py manifest.json --output-dir output --env-file /path/to/.env
```

10. Validate `generation-report.json`, every logical segment MP3/WAV, and both complete-program files. Do not report success if any requested segment failed.

## Execution rules

- Never store an API key in the Skill, manifest, logs, or output report.
- Read credentials from `DOUBAO_TTS_API_KEY`; optionally accept `VOLCENGINE_TTS_API_KEY`, `VOLC_API_KEY`, or `X_API_KEY`.
- Use `DOUBAO_TTS_RESOURCE_ID` when supplied; otherwise use `seed-tts-2.0`.
- Treat logical segments separately from API chunks. Split long text internally, but merge the chunks back into one logical segment file.
- Reuse a completed segment only when its text, voice, model, speed, loudness, and audio settings hash all match.
- Preserve successful segments on retry. Regenerate only missing or stale outputs.
- Use punctuation-aware splitting. Never cut a number, Latin token, or sentence when a nearby sentence boundary is available.
- Keep source and commentary pauses configurable in the manifest.
- Prefer a 450-650 ms speaker-change pause and a 250-400 ms same-speaker pause unless the user specifies timing.
- Save the effective manifest beside outputs for auditability.
- Use `ffmpeg`/`ffprobe` for decoding and validation. Stop with a clear error when either is unavailable.
- Use a Python interpreter that provides `websocket-client`. Do not install packages without user approval.

## Output contract

Produce:

```text
output/
├── effective-manifest.json
├── generation-report.json
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
- source/commentary order matches the intended dialogue;
- every segment has non-empty text and a known voice;
- no production labels such as “插入点”“原文播放范围” remain in spoken text;
- all audio files are non-empty and decodable;
- generated segment count equals requested segment count;
- the complete WAV duration is consistent with segment durations plus pauses;
- the complete MP3 is decodable and has a positive duration;
- `generation-report.json` contains no failed items.
