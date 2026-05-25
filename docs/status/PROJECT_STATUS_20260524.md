# Project Status - 2026-05-24

## ZSXQ Lab

- Added `src/frontend/zsxq-lab.html`, `src/frontend/js/zsxq-lab.js`, `src/backend/apis/v1/zsxq.py`, and `src/backend/services/zsxq_service.py` as a low-frequency personal verification page for the ZSXQ mobile API.
- Cookie is accepted from the browser only and stored in `sessionStorage`; backend normalizes Cookie header strings and browser-exported Cookie JSON without persisting or logging the token.
- Implemented group loading, topic loading, topic detail JSON copy, single attachment download, and front-end queued multi-download.
- Multi-download intentionally avoids zip creation and concurrency. It downloads individual files one by one, with a random 9-12 second delay after the first file.
- Added visual tabs matching the user's ZSXQ page: latest, report categories, database, files, images, links, and digg.
- Fixed category tab behavior so local category tabs render from the all-topic base list. If the current base list came from digg, switching to a category first reloads `scope=all`.
- Added a "copy topics API JSON" button to help inspect upstream response structure when a server-side tab/category cannot be inferred from the public payload.
- Backend now treats upstream `succeeded:false` as an API error instead of wrapping it as a successful empty list, so the UI no longer reports interface zero when ZSXQ actually returned an error.
- The basic topic payload does not expose an explicit official tab/category id. Local tab matching therefore uses text, file names, and hashtag-like category lines as inference.
- Fixed the mismatch where the UI could request 200 topics while the backend silently capped the request at 50. The backend now paginates serially up to 200 topics using `end_time`, with a small delay between pages.
- Added parsing for short standalone category lines like `#外资研报`, because ZSXQ topic text may use this format instead of XML hashtag entities.
- Reworked the ZSXQ lab page toward a native-like reading workflow:
  - Cookie now persists in browser `localStorage` instead of only the current session.
  - Each group + tab has its own cached topic list, pagination cursor, loading state, and end-of-list state.
  - Topic requests default to the UI page size and use `end_time` for incremental pagination instead of backend preloading large batches.
  - The topic list supports scroll-to-bottom loading and an explicit "continue loading" button.
  - The copied debug JSON now includes sanitized request parameters plus the upstream response, making Charles packet comparisons easier.
- Reduced automatic local-filter backfill pressure after the user saw intermittent `/zsxq/topics` 400 responses:
  - Category tabs auto-fetch at most 2 pages before waiting for user scroll/click.
  - Topic requests are spaced by at least 1.8 seconds on the frontend.
  - If a later page fails after content already loaded, existing content remains visible and the error appears in the footer.
  - Backend now logs sanitized `/zsxq/topics` 400 context (`group_id`, `count`, `scope`, `has_end_time`, extra param keys, error message) without logging Cookie.
- Fixed duplicated tabs when a hardcoded category label is also discovered as a hashtag tab from ZSXQ content, e.g. `外资研报`. The hardcoded category tab is kept because it matches the broader local category rules; the same-name dynamic hashtag tab is hidden.
- Normalized ZSXQ hashtag XML snippets in topic text so `<e type="hashtag"...>` displays as normal decoded hashtag text instead of raw XML.
- Treated ZSXQ upstream `code=1059` as rate limiting/temporary rejection:
  - Removed automatic multi-page backfill; each tab request now fetches only one page.
  - Added a random 4.5-7 second minimum gap between topic requests.
  - Added a 30 second frontend cooldown after `1059`, with a visible footer message instead of repeated retries.

## Verification

- `node --check src/frontend/js/zsxq-lab.js`
- `venv/bin/python -m py_compile src/backend/apis/v1/zsxq.py src/backend/services/zsxq_service.py`

## Notes

- Follow project discipline in `GEMINI.md`: runtime logs belong under `/logs`, and status reports belong under `docs/status/`.
- The user pasted a real `zsxq_access_token` during testing. Treat it as sensitive and do not echo it in logs or status reports.
