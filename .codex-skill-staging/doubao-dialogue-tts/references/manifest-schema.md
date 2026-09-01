# Dialogue manifest schema

Use UTF-8 JSON.

```json
{
  "schema_version": "doubao-dialogue-v1",
  "title": "节目标题",
  "model": "seed-tts-2.0-standard",
  "audio": {
    "sample_rate": 24000,
    "bit_rate": 128000,
    "format": "mp3",
    "chunk_chars": 450
  },
  "editorial": {
    "profile_id": "xiaojunjun-honghao",
    "source_mode": "locked_verbatim",
    "source_author": "洪灏",
    "spoken_author_reference": "洪总"
  },
  "voices": {
    "host": {
      "label": "清澈",
      "voice_id": "zh_female_qingchezizi_uranus_bigtts",
      "speed": 1.08,
      "loudness": 1.0
    },
    "source": {
      "label": "刘飞",
      "voice_id": "zh_male_liufei_uranus_bigtts",
      "speed": 1.0,
      "loudness": 1.0
    }
  },
  "segments": [
    {
      "id": "001",
      "slug": "host-opening",
      "role": "host",
      "kind": "opening",
      "text": "主持人开场。",
      "pause_after_ms": 600
    },
    {
      "id": "002",
      "slug": "source-01",
      "role": "source",
      "kind": "source",
      "text": "第一段原文。",
      "pause_after_ms": 650
    }
  ]
}
```

Required top-level fields:

- `schema_version`: must be `doubao-dialogue-v1`.
- `title`: non-empty program title.
- `segments`: ordered logical spoken units.

`voices` is required in generic mode. With an active channel profile, the profile may supply the complete role-to-voice mapping and the manifest may omit it or override only selected fields.

Optional top-level fields:

- `model`: Doubao API request model; default `seed-tts-2.0-standard`.
- `audio`: sample rate, bit rate, API chunk size, and output format settings.
- `editorial`: review-only metadata. `editorial.profile_id` explicitly selects a channel profile; `generic` disables a project default for the episode.

## Channel profile resolution

Resolution order is:

1. explicit `editorial.profile_id` in the manifest;
2. `.doubao-dialogue-tts.json` found by walking upward from the manifest path, then the current working directory;
3. generic mode.

A project selector contains only stable routing data:

```json
{
  "schema_version": "doubao-dialogue-project-v1",
  "default_profile": "xiaojunjun-honghao"
}
```

Profiles live in `references/channel-profiles/<profile_id>.json`. The resolved profile ID is written to the effective manifest. Manifest voice fields override profile defaults field by field; omitted fields inherit from the profile.

Do not confuse the request model with the API Resource ID:

- manifest `model`: `seed-tts-2.0-standard`;
- `DOUBAO_TTS_RESOURCE_ID` / `X-Api-Resource-Id`: `seed-tts-2.0` by default.

The dry run rejects `"model": "seed-tts-2.0"` because that value is a Resource ID, not a valid request model for this workflow.

Required voice fields:

- `label`
- `voice_id`

Optional voice fields:

- `speed`: default `1.0`.
- `loudness`: default `1.0`.
- `emotion`: default empty.
- `emotion_scale`: default `4`.

For `xiaojunjun-honghao`, the verified defaults are host/清澈 `1.08` and source/刘飞 `1.00`. An explicit episode speed is preserved and reported as `manifest_override` in the dry-run summary.

Required segment fields:

- `id`: unique zero-padded sequence, such as `001`.
- `slug`: filesystem-safe descriptive label.
- `role`: key present in `voices`.
- `text`: final spoken text only.

Optional segment fields:

- `kind`: opening, source, commentary, transition, or closing.
- `pause_after_ms`: default `500`.

Do not place API keys, source file paths containing secrets, production instructions, citations, or unspoken labels in segment text.

When a profile is active, dry-run validation also enforces its fixed opening, host-only author aliases, and forbidden backstage phrases. Locked source segments are exempt from host alias replacement and retain their exact text.

The dry-run summary includes `profile`, `voice_settings`, `api_billing_boundary`, and `external_data_transfer`. `api_billing_boundary: first_external_tts_request` describes provider-request timing; it is not the program's content monetization timestamp.
