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
  "voices": {
    "host": {
      "label": "清澈",
      "voice_id": "zh_female_qingchezizi_uranus_bigtts",
      "speed": 1.0,
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
- `voices`: role-to-voice mapping.
- `segments`: ordered logical spoken units.

Required voice fields:

- `label`
- `voice_id`

Optional voice fields:

- `speed`: default `1.0`.
- `loudness`: default `1.0`.
- `emotion`: default empty.
- `emotion_scale`: default `4`.

Required segment fields:

- `id`: unique zero-padded sequence, such as `001`.
- `slug`: filesystem-safe descriptive label.
- `role`: key present in `voices`.
- `text`: final spoken text only.

Optional segment fields:

- `kind`: opening, source, commentary, transition, or closing.
- `pause_after_ms`: default `500`.

Do not place API keys, source file paths containing secrets, production instructions, citations, or unspoken labels in segment text.

