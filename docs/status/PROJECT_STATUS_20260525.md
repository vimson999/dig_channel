# Project Status - 2026-05-25

## ZSXQ Lab API Reverse Engineering

- Reviewed Proxyman CSV/HAR exports for `api.zsxq.com`.
- Confirmed official group tabs are returned by `GET /v2/groups/{group_id}/menus`.
- Confirmed hashtag-backed tabs use `GET /v2/hashtags/{hashtag_id}/topics?count=20`.
- Confirmed `外资研报` maps to hashtag id `51184248544214` in the captured `menus` payload.

## Implementation

- Added backend `POST /api/v1/zsxq/menus` proxy endpoint.
- Updated topic proxy routing so `extra_params.hashtag_id` calls `/v2/hashtags/{hashtag_id}/topics` instead of local group topic filtering.
- Updated `zsxq-lab.js` to load official menus after a group is selected.
- Updated tab rendering to prefer official menus and use real hashtag ids for hashtag tabs.
- Expanded the scope selector to include `with_files`, `with_images`, and `digests`, matching captured preset menu behavior.
- Fixed image display in ZSXQ topic cards by rendering `topic.images`, `talk.images`, and `article.images` with lazy-loaded thumbnails that link to the larger image URL.
- Reviewed upload capture:
  - Files are registered with `POST /v2/uploads` using `{ type, size, name, hash }`.
  - Topic publishing uses `POST /v2/groups/{group_id}/topics` with `text`, `image_ids`, `file_ids`, and `mentioned_user_ids`.

## Verification

- `node --check src/frontend/js/zsxq-lab.js`
- `venv/bin/python -m py_compile src/backend/apis/v1/zsxq.py src/backend/services/zsxq_service.py`

## Notes

- HAR responses include sensitive cookies/tokens in surrounding request context. Do not commit HAR/CSV captures.
