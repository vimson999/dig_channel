# Design QA: 频道排行榜时间切换与高密度宽屏布局

## Visual truth

- Source visual truth paths: `/var/folders/dd/51s7f6gs6vz30qy285xd_b_80000gn/T/codex-clipboard-58006e8f-23a7-4f5e-88fe-396296e1652d.png` and the follow-up critique capture `/Users/v9/.codex/attachments/8ba72fd7-9a92-4c0d-9f6d-ec11b12d9a7b/image-1.png`.
- Browser-rendered implementation screenshot artifact: Codex in-app browser capture at `http://127.0.0.1:5001/rankings.html?type=monthly_revenue`.
- Source pixels: initial `1455 x 948`; follow-up `1769 x 959`, both including browser chrome. The latest comparison normalized the follow-up content region to a `1769 x 917` CSS viewport.
- Implementation CSS viewport and capture: `1769 x 917`, device scale factor `1`; comparison used the page content region rather than browser chrome.
- State: live monthly and daily revenue rankings, page size 20. Time-switching checks covered current period, August, July, and the exact day `2026-08-07`.

## Comparison evidence

- Full-view comparison: passed after a second design iteration. The initial source showed a 1280px content cap, large breadcrumb/hero blocks, repeated headings, two-line channel rows, and only four visible records. The first compact implementation showed all 20 rows but still left a large empty band between identity and value. The final implementation uses that band for semantic relative-to-leader comparison bars.
- Focused ranking-table comparison: passed. Channel name and YouTube ID share one line on desktop; rank, the active metric, relative percentage, semantic progress bar, and action retain distinct aligned columns.
- Focused mobile comparison: passed after iteration. At `390 x 844`, the secondary YouTube ID is hidden while rank, channel name, value, and action remain visible without horizontal dragging.

## Findings and iteration history

1. Initial P1: the source layout spent most of the first viewport on container margins, breadcrumb, hero copy, and repeated section headings, leaving only four ranking rows visible.
2. Fix: expanded the page frame from 1280px to 1680px, removed the breadcrumb and redundant descriptions, reduced the page header, and condensed the ranking controls and metadata.
3. Initial P1: 20 two-line rows could not fit in the desktop viewport and required page scrolling.
4. Fix: changed rows to a 24px minimum-height single-line grid and moved pagination into the metadata bar. Post-fix browser evidence shows all 20 rows, including rank 20, within `1455 x 906`.
5. Initial P2: the first responsive pass kept the desktop minimum table width, pushing values offscreen on mobile.
6. Fix: added a four-column mobile grid and hid only the secondary channel ID. Post-fix evidence shows channel values and actions inside the `390px` viewport.
7. Follow-up P1: widening the table without adding information left a conspicuous empty middle band; the metric remained isolated at the far right and the layout looked stretched rather than designed.
8. Fix: capped the channel identity column, added a flexible `相对榜首` column using semantic progress elements, displayed exact percentages, and changed the generic `数据` heading to the active metric name.
9. Post-fix: the `1769 x 917` browser capture shows continuous information across the table width. Monthly revenue uses blue comparison bars, subscriber growth uses purple, and page 2 preserves the page-1 leader baseline (`1.3%` for its first row rather than resetting to `100%`).
10. Follow-up P1: the period label was read-only, so historical monthly and daily rankings were inaccessible even though the API already supported them.
11. Fix: added a compact native date/month picker, previous/next period buttons, and a “本期” shortcut in the existing toolbar. Daily and monthly selections are preserved separately and encoded in the URL for refresh/share continuity.
12. Post-fix: August and July monthly rankings each rendered 20 of 49 live channels; `2026-08-07` rendered 20 of 48 daily records. Current-period next/current actions disable automatically, and the controls fit without reintroducing the removed vertical bulk.
13. Follow-up: added `年收入` and `年新增订阅` alongside the existing daily/monthly tabs. The same compact period control becomes a four-digit year selector with previous year, next year, and current-year behavior.
14. Post-fix: the live 2026 annual revenue and subscriber rankings aggregate `2026-01-01` through `2026-09-15`; navigating back renders the full `2025-01-01` through `2025-12-31` period. All six tabs remain on one compact desktop control row.
15. Follow-up: each ranking row now includes the channel's saved category and CPM reference value. Both are compact single-line badges beside the channel ID (`教育`, `参考 $4.00/千次` in the live first row), so the table gains configuration context without increasing row height or displacing the metric/progress columns.
16. Correction: category and CPM were initially placed inline after a variable-length channel ID, which made their horizontal positions drift. They are now dedicated grid columns with matching headers, fixed widths, consistent right alignment for CPM, and subtle vertical separators. Browser evidence confirms 20 category cells and 20 CPM cells align independently of ID length.

## Fidelity surfaces

- Fonts and typography: passed. Existing system fonts and weight hierarchy remain; row labels use compact 12–14px sizes with truncation for long names and IDs.
- Spacing and layout rhythm: passed. Desktop side margins are reduced to 16px, controls form two compact bands, row rhythm is consistent, and 20 records fit without page scrolling at the target viewport.
- Colors and visual tokens: passed. Existing slate, blue, green, purple, orange, borders, selected states, and subtle white surface remain consistent with Shioa; comparison bars follow the active ranking metric color.
- Image quality and assets: passed. The page contains no raster content; existing Font Awesome icons are retained without placeholders or custom drawings.
- Copy and content: passed. Duplicate explanatory copy was removed; total, period, page status, names, IDs, active metric labels, and relative-to-leader values continue to use live API data or transparent client-side derivation from that data.

## Functional checks

- Desktop page rendered 20 live rows; the last visible row was rank 20, `Skill Leap AI`.
- Page 2 interaction rendered 20 rows and updated the status to `第 2 / 3 页`; returning to page 1 restored the initial ranking.
- Relative comparison remained anchored to the global first-place value when paging; page 2's first result rendered `1.3%`, not `100%`.
- Switching to daily subscriber ranking updated the value header to `日增订阅` and the progress token to purple; returning to monthly revenue restored `月收入` and blue.
- Desktop browser console contained zero errors.
- Historical-time browser checks passed for `month=2026-08`, previous-month navigation to `2026-07`, exact `date=2026-08-07`, and return to the current period. URLs and API-rendered period labels remained synchronized.
- Annual browser checks passed for both revenue and subscriber rankings, including `year=2026`, previous-year navigation to `year=2025`, 20-row pagination, and a clean-console run in a fresh browser tab.
- Channel metadata browser check passed with live category and configured reference CPM on all rendered rows; the fresh-tab console remained clean and the page continued to render 20 results.
- Mobile capture retained rank, channel, metric, and detail action without pushing the metric offscreen; the desktop-only comparison visualization was correctly hidden.
- Frontend ranking regression suite passed 10/10; combined ranking and trend suite passed 15/15; `git diff --check` passed.

final result: passed

---

# Design QA: 知识星球内容分析工作台

## Visual truth

- Selected design direction: `/Users/v9/.codex/generated_images/019faf40-cced-7853-9ecf-71b1f5f7d146/call_nEda2IkB8bnsvZuaKfZ80UNn.png`
- Browser implementation: `/private/tmp/zsxq-lab-content-workbench-1440x1024.png`
- Side-by-side comparison: `/private/tmp/zsxq-lab-content-workbench-comparison.png`
- Desktop viewport: `1440x1024`; live state uses `前沿信息收录 / 调研纪要` with 20 loaded topics and 168 attachments.
- Mobile viewport: `390x844`; live local group list loaded.

## Comparison

The selected direction and browser implementation were reviewed together in one side-by-side visual comparison input.

- Information architecture: passed. The generic three-step hero and abstract 3D graph are gone. The first screen now exposes active group, active Tab, topic coverage, search and filters, a selected-topic inspector, and actual next actions.
- Header: passed. The implementation keeps the selected direction's compact title, current-context line, coverage metrics, and primary sync action while preserving Shioa's existing navigation.
- Main workspace: passed. Desktop uses a `285px / minmax(0, 1fr) / 350px` three-column workbench. Existing topic cards and attachment controls remain operational instead of being replaced with mock table interactions.
- Topic inspector: passed. It explains正文、附件、外链、总结、语音 coverage, identifies the current gap, shows the processing path, and offers only real actions: locate topic, open source when available, and sync current Tab.

## Iteration history

1. Initial P1: the original hero repeated obvious operating steps and consumed the first screen without showing live state.
2. Initial P1: the Three.js graph visualized object types but did not explain what was missing, why it mattered, or what to do next.
3. Fix: replaced both areas with live coverage metrics and a contextual topic inspector driven by existing topic, attachment, external-document, summary, and TTS fields.
4. Post-fix: switched between `生财有术` and `前沿信息收录`, loaded real topics, selected multiple topic inspectors, and confirmed the UI updates without console errors.

## Fidelity surfaces

- Typography and color: passed. Existing Shioa system fonts, slate/blue palette, borders, radii, and Font Awesome icon system are retained.
- Spacing and density: passed. The first screen is materially denser but keeps readable labels and clear separation between navigation, filters, content, and analysis.
- Copy and content: passed. Metrics and inspector states use live local data; no placeholder counts or fabricated processing results are shown.
- Responsive behavior: passed. At `390x844`, `body.scrollWidth`, document width, and viewport width all equal `390`; the header, metrics, and workbench stack without horizontal overflow.

## Functional checks

- Local star list loaded four real groups.
- Selecting `生财有术` loaded 16 topics and 17 official Tabs; selecting another topic changed the inspector title and explanation.
- Selecting `前沿信息收录` loaded 20 topics and 168 attachments; the first inspector showed 9 attachments and the attachment-processing recommendation.
- Header sync delegates to the existing current-Tab synchronization action.
- Inspector “定位主题” and conditional “查看原文” use existing topic data and page behavior.
- JavaScript syntax checks, duplicate-ID check, console error check, responsive overflow check, and `git diff --check` passed.

final result: passed

---

# Design QA: 频道数据中心趋势分析整合

## Visual truth

- Source visual truth path: `/Users/v9/.codex/attachments/58df59a2-9011-4305-ac02-f291237df02d/image-1.png`.
- Browser-rendered implementation screenshot artifact: Codex in-app browser task capture at `http://127.0.0.1:5001/rankings.html?view=trends`.
- Viewport: desktop `1280 x 720`; responsive check `390 x 844`.
- Dimensions and density: source `1440 x 586`; implementation desktop capture `1280 x 720`, CSS density `1`. The comparison was normalized around the trend-card region rather than the surrounding application navigation.
- State: live local data, 50 active channels, daily revenue over the latest seven days. Additional states checked: monthly aggregation, one-channel filtering, pie view, ranking view, and mobile layout.

## Comparison evidence

- Full-view comparison: passed. The source and implementation captures were reviewed together in the task context. Both use a white elevated data card, compact title block, right-aligned controls, a scrollable legend, and a large chart canvas.
- Focused control comparison: passed. Line, bar, and pie controls; channel filtering; daily/monthly aggregation; and the range selector preserve the source workflow. The implementation adds a metric selector for the backend's existing revenue, subscriber, and view data instead of hard-coding revenue.
- Focused chart comparison: passed. The seven-day line view retains the source's dense multi-channel comparison. The pie view intentionally shows the ten largest channels and combines the remainder as “其他频道” so labels remain readable.

## Iteration history

1. Initial P1: Tailwind display utilities overrode the native `hidden` attribute, so the filter dialog and chart empty state appeared on first load.
2. Fix: synchronized the semantic `hidden` property with Tailwind's `hidden` class in the controller and initial markup.
3. Post-fix: the initial chart renders unobstructed; opening, applying, and closing the filter dialog works normally.
4. Initial P2: the pie view rendered all 50 channels, creating overlapping labels and unusable visual density.
5. Fix: sorted current values, retained the ten largest slices, and aggregated the long tail into “其他频道”.
6. Post-fix: the pie legend contains ten named channels plus one aggregate slice; no remaining actionable P0/P1/P2 findings.

## Fidelity surfaces

- Fonts and typography: passed. Existing system fonts, compact control labels, bold section titles, and readable metadata hierarchy match the Shioa interface and source density.
- Spacing and layout rhythm: passed. Desktop controls stay aligned with the title card; mobile controls wrap into logical rows without clipping. At `390px`, body width, document width, and viewport width all equal `390px`.
- Colors and visual tokens: passed. Existing indigo, slate, white, soft border, shadow, and semantic chart colors are reused.
- Image quality and assets: passed. The region contains data visualization and standard UI icons only; ECharts and the existing Font Awesome library render them sharply without placeholder or generated assets.
- Copy and content: passed. Labels describe the actual metric, aggregation, range, selected-channel count, channel count, and date range returned by the live API.

## Functional checks

- Daily revenue trend loaded 50 live channels for `2026-09-09` through `2026-09-15`.
- Monthly aggregation loaded the six-month range `2026-04-01` through `2026-09-15`.
- Selecting one channel updated both the filter summary and chart metadata to one channel.
- Switching between trend analysis and the complete ranking updated the URL and lazy-loaded the selected workspace.
- Mobile layout has no horizontal overflow at `390 x 844`.
- Browser console has no application errors; only the project's existing Tailwind CDN warning remains.

final result: passed

---

# Design QA: 生财情报台亦仁认可标签与正文可读性

## Visual truth

- Source screenshot: `/var/folders/dd/51s7f6gs6vz30qy285xd_b_80000gn/T/codex-clipboard-e8ff414b-3b08-48ee-b713-b7c2521e7bad.png`
- Browser-rendered implementation: `/private/tmp/zsxq-insights-yiren-readability-implementation-1564x1139.png`
- Source pixels: `1564x1140`.
- Implementation pixels and CSS viewport: `1564x1139`, device scale factor `1`; the one-pixel height difference is non-material.
- State: search query `我们聊了很多AI工具`, topic `55522445112882414`, detail dialog open, live local data.

## Comparison evidence

- Full-view comparison: passed. Source and implementation were opened together at effectively identical viewport dimensions. The implementation preserves the existing Shioa page and dialog hierarchy while replacing raw platform markup with readable article structure.
- Focused dialog comparison: passed. Encoded `text_bold` tokens now render as section headings with a restrained blue rule; encoded `mention` tokens render inline as a blue mention. The metadata row adds the bounded `未见亦仁认可` state without competing with the title.
- Intentional deviation: the implementation uses a narrower `940px` reading column instead of the source's nearly full-width dialog. This improves line length and scanning and is the requested readability change.

## Findings and iteration history

1. Initial P1: raw `<e type="text_bold">` and URL-encoded `title` attributes occupied the article body, making the content effectively unreadable.
2. Initial P1: the list and dialog had no explicit, evidence-bounded indication of whether 亦仁 recognized the topic.
3. Fix: added safe parsing for known bold and mention tokens, paragraph/heading typography, and a three-state recognition label: `亦仁认可`, `亦仁有回应`, `未见亦仁认可`.
4. First browser pass found a P2 residual: list excerpts still exposed raw rich-text tokens because a previous Redis payload remained cached.
5. Fix: added backend plain-text excerpt normalization and advanced the overview cache namespace to `v4`.
6. Post-fix evidence: the screenshot topic renders 48 readable paragraphs, multiple section headings, an inline `@Jacky` mention, and no visible `<e type=...>` text in either list excerpt or dialog. No remaining P0/P1/P2 findings.

## Fidelity surfaces

- Fonts and typography: passed. Existing system fonts remain; dialog body is `16px / 1.9`, headings are `18px` with clear hierarchy, and title weight/scale remain consistent with Shioa.
- Spacing and layout rhythm: passed. The reading column, paragraph spacing, metadata wrapping, dialog padding, and mobile overrides form a consistent vertical rhythm.
- Colors and visual tokens: passed. Existing slate/blue/green tokens are reused; recognition states use green for confirmed, blue for response-only, and neutral slate for no evidence found.
- Image quality and assets: passed. No new raster assets were required; existing Font Awesome icons are reused.
- Copy and content: passed. Recognition copy explicitly distinguishes confirmed recognition, response-only evidence, and absence of captured evidence; it does not infer rejection.

## Functional checks

- Search narrowed the live page to the exact screenshot topic.
- List excerpt contains decoded Chinese and `@Jacky`; no raw rich-text tag remains.
- Topic dialog opens, closes, and reopens successfully.
- Detail parser produced 48 paragraphs and recognized the expected section headings and mention.
- Screenshot topic truthfully reports `未见亦仁认可`; the API returned `yiren_recognition: not_found`.
- Browser console contained no errors.
- Automated regression: `18 passed`.
- JavaScript syntax, backend health, and `git diff --check` passed.

final result: passed

---

# Design QA: 生财情报台

## Visual truth

- Source design: `/Users/v9/.codex/generated_images/019f449e-85ad-7043-8239-e8e16a880fea/exec-9413569e-953e-4e23-8be9-2d17b9aeb4dd.png`
- Existing-product reference: `/Users/v9/.codex/visualizations/2026/07/09/019f449e-85ad-7043-8239-e8e16a880fea/zsxq-lab-current-reference.png`
- Final implementation screenshot: `/Users/v9/.codex/visualizations/2026/07/09/019f449e-85ad-7043-8239-e8e16a880fea/zsxq-insights-final-clean.png`
- Viewport and state: `1440x1024`, default `AI + 销售私域`, `近90天`, live local data.

## Comparison

The source and implementation screenshots were reviewed together in the same visual comparison input.

- Full view: passed. Header, compact filter band, view tabs, three-column research workspace, and information density follow the selected direction.
- Header and toolbar: passed. Final header height is about `65px`, toolbar height about `54.5px`, and workspace begins at about `174.5px`.
- Main workspace: passed. Final columns use `290px / minmax(0, 1fr) / 320px`; no desktop or mobile horizontal overflow was found.
- Metrics and trend: passed. Metric hierarchy, restrained chart height, signal colors, and supporting labels remain legible without crowding.
- Feed and interpretation panel: passed. Rows support scanning and detail drill-down; the right panel keeps conclusions and evidence visible.

## Iteration history

1. Initial P1: vertical density was too loose. The header was about `106px` and the workspace began around `235px`, while the source was substantially tighter. Reset heading margins and reduced header, toolbar, and tab padding.
2. Initial P2: the trend chart was too tall, the follow action wrapped, and side columns were slightly narrow. Reduced the chart to `180px`, prevented button wrapping, and adjusted the desktop grid.
3. Post-fix: header is `65px`, toolbar is about `54.5px`, workspace begins around `174.5px`, and `body.scrollWidth` equals the viewport width at desktop and mobile breakpoints.

## Fidelity surfaces

- Typography: passed. Uses the existing product's Chinese system-font stack with compact dashboard hierarchy and consistent weights.
- Spacing and layout: passed. Dense research-tool proportions match the selected direction and existing Shioa navigation.
- Color: passed. Neutral white/slate surfaces use green for positive momentum, amber for opportunity signals, and blue for neutral structure.
- Assets and icons: passed. The target contains no imagery; existing navigation assets, Font Awesome icons, and Chart.js are used consistently.
- Copy and content: passed. All visible metrics, trends, authors, topics, and interpretations come from the local 生财有术 dataset rather than mock content.

## Functional checks

- Theme switch updates summary, metrics, trend, distribution, feed, and interpretation.
- Topic rows open a modal and fetch full cached local content on demand.
- Range, search, sorting, view tabs, follow state, refresh, and chart-week filtering are wired.
- Overview responses now include the complete matching summary set (up to the local-library safety cap), so “查看更多” and week filtering are not limited to the first 30 records.
- Rapid overview requests cancel stale requests; theme follow state persists across reloads.
- Weekly trend data includes ISO week keys, keeping chart filtering correct across year boundaries.
- Desktop check: `1440x1024`, six initial feed rows, five metric values, no horizontal overflow.
- Mobile check: `390x844`, `bodyWidth === viewportWidth`, content remains readable.
- Console check after final reload: no new warnings or errors.
- API check: health endpoint and overview endpoint returned HTTP 200 with `3,222` local topics.
- Real-data checks: AI + 销售私域 returned all `247` matching 90-day summaries; Ozon search returned `22` matching 730-day records; topic detail returned cached full text; invalid pagination returned structured HTTP 400.
- Automated tests: `10` unittest cases passed, covering classifiers, parameter bounds, cross-year trend keys, compact topic payloads, API parameter forwarding, API validation, and 404 detail behavior.
- Static checks: Python compilation, JavaScript syntax, and `git diff --check` passed.

final result: passed

---

# Design QA: 任务概览模块化进度

## Visual truth

- Source visual truth: `/var/folders/dd/51s7f6gs6vz30qy285xd_b_80000gn/T/codex-clipboard-f3ccaf1b-0cfc-413c-b710-a2e698c4228d.png`
- Browser-rendered implementation: `/tmp/report-audio-workflow-stages-v4.png`
- Responsive implementation: `/tmp/report-audio-workflow-stages-v4-1024.png`
- Side-by-side comparison: `/tmp/report-audio-workflow-stages-v4-comparison.png`
- Viewport: source and desktop implementation both `1589 × 382` CSS px at device scale factor `1`; responsive check `1024 × 768`.
- Pixel dimensions: source `1589 × 382`, implementation `1589 × 382`; no density normalization was required.
- State: source shows `review_cover_titles · 60%`; the live task had completed before the final capture and therefore shows all six stages completed.

## Comparison evidence

- Full view: passed. The summary card keeps the existing status badge, title, metadata, action buttons, surface, radius, and density while replacing only the opaque percentage bar.
- Focused progress region: passed. The old bar and the six-stage implementation were placed side by side. The new cards expose PDF preparation, Gemini parsing, script cleanup, speech generation, video generation, and final completion as separate states.
- Responsive region: passed. At `1589px` the stages use one six-column row; at `1024px` they use a readable two-row, three-column layout without clipping the summary card.

## Findings and iteration history

1. Initial P1: a numeric percentage did not explain which workflow module had finished or where the task was currently blocked.
2. Fix: replaced the bar with six semantic stage cards and mapped backend step names plus progress fallback values to completed, active, pending, failed, and canceled states.
3. Post-fix: green completed stages and the blue active stage are immediately distinguishable; future stages stay neutral. No remaining P0/P1/P2 findings in the changed region.

## Fidelity surfaces

- Fonts and typography: passed. Existing system font, small-label scale, bold hierarchy, and compact metadata are preserved.
- Spacing and layout rhythm: passed. The stage cards use the existing `gap-2`, compact padding, rounded borders, and responsive grid rhythm.
- Colors and visual tokens: passed. Emerald is used only for completed stages, blue for the active stage, slate for pending, red for failed, and amber for canceled.
- Image quality and assets: passed. The component has no raster imagery; existing Font Awesome icons are reused.
- Copy and content: passed. Every card has a concise module name and an explicit state label rather than relying on color alone.

## Functional checks

- The live completed task rendered six green `已完成` modules.
- `review_cover_titles` maps to `文案整理`, so the first two modules are complete, the third is active, and later modules remain pending.
- The legacy horizontal percentage bar is no longer rendered.
- The current backend step remains visible in the summary metadata for diagnosis.
- Browser DOM exposed the ordered list with `aria-label="任务流程进度"`.
- Automated regression: `60 passed`, `4 subtests passed`.
- JavaScript syntax and `git diff --check` passed.

final result: passed

---

# Design QA: PDF 证据卡片回归主内容流

## Visual truth

- Source screenshot: `/var/folders/dd/51s7f6gs6vz30qy285xd_b_80000gn/T/codex-clipboard-462fe5f8-63bf-40b6-b03e-05807d9111b9.png`
- Implementation screenshot: `/tmp/report-pdf-evidence-layout-v3-viewport.png`
- Focused implementation screenshot: `/tmp/report-pdf-evidence-layout-v3-focused.png`
- Combined comparison: `/tmp/report-pdf-evidence-layout-v3-comparison.png`
- Source and implementation pixels: `1604x903`; CSS viewport `1604x903`, device scale `1`.
- State: source shows the evidence card incorrectly outside the two-column detail grid; implementation shows the same evidence pending state inside the right-hand task flow. Dynamic report content differs because the local task advanced independently.

## Comparison evidence

- Full view: passed after fix. The evidence card is now a child of `#detail-root`, has exactly the same width as adjacent task cards, and no longer spans beneath the sticky quick navigation.
- Focused region: passed. Quick navigation scrolls to the evidence card, then the normal order continues with child tasks and the publishing center.
- Responsive check: passed at `1024x768`; panel width equals the right content width (`780px`) and `body.scrollWidth` equals viewport width.

## Findings and iteration history

1. Initial P1: `#pdf-evidence-panel` was placed after the entire two-column grid, making it span the full page and disconnecting it from the task-detail hierarchy.
2. Initial P2: a static out-of-grid mount could not survive full detail rerenders without either disappearing or remaining in the wrong order.
3. Fix: removed the static page-level section; the evidence controller now mounts the card after 文案内容, or after 素材预览 while content is unavailable, and remounts after detail DOM replacement.
4. Post-fix: no remaining P0/P1/P2 findings.

## Fidelity surfaces

- Typography: passed; existing detail-page font family, scale, weights, and helper-text hierarchy are unchanged.
- Spacing and layout: passed; evidence uses the same right-column width, `space-y-4` rhythm, radius, border, padding, and elevation as neighboring cards.
- Colors and tokens: passed; existing slate surfaces and blue pending state are retained.
- Images and icons: passed; no new raster assets were required and the existing Font Awesome icon family is preserved.
- Copy and content: passed; the pending state remains explicit and truthful.

## Functional checks

- Evidence quick-navigation target resolves uniquely and scrolls to the mounted card.
- Full detail rerenders remount the card in the correct position.
- Pending evidence data remains visible after remount.
- Desktop `1604x903` and narrower `1024x768` checks have no horizontal overflow.
- Browser console has no application errors.
- JavaScript syntax, `10` relevant tests, `5` subtests, and `git diff --check` passed.

final result: passed

---

# Design QA: 研报视频生成配置分层

## Visual truth

- Source screenshot: `/var/folders/dd/51s7f6gs6vz30qy285xd_b_80000gn/T/codex-clipboard-4cafe5c7-d061-485b-9b33-1e397f7ee0ff.png`
- Keyframe implementation screenshot: `/tmp/report-audio-output-mode-keyframes-final.png`
- Full-video implementation screenshot: `/tmp/report-audio-output-mode-full-video-final.png`
- Combined keyframe comparison: `/tmp/report-audio-output-mode-keyframes-comparison.png`
- Combined full-video comparison: `/tmp/report-audio-output-mode-full-video-comparison.png`
- Source pixels: `470x809`.
- Implementation viewport and pixels: `1280x720`, desktop CSS viewport at device scale `1`.
- State: the same MVP video-stage form, checked once in “关键帧 + 封皮” state and once in “完整视频” state.

## Comparison evidence

- Full view: passed. The existing Shioa sidebar card, typography, radii, neutral surfaces, and compact density are preserved.
- Focused region: passed. The original video-stage card and both revised mode states were combined side by side before review. The new hierarchy makes generation mode the first decision and removes inactive settings from view.
- Interaction state: passed. Selecting “关键帧 + 封皮” shows only six image outputs and TTS step 3; selecting “完整视频” shows horizontal versions, vertical versions, and TTS step 4.

## Findings and iteration history

1. Initial P2: the keyframe card remained visually highlighted after selecting full video. Added active-card token switching so border and background follow the checked radio.
2. Initial P2: three vertical-video cards were cramped in the narrow sidebar. Changed the responsive grid to two columns so names and descriptions remain readable.
3. Post-fix: no remaining P0/P1/P2 findings.

## Fidelity surfaces

- Fonts and typography: passed. Existing Chinese system-font stack, size scale, weights, and compact helper text are retained.
- Spacing and layout rhythm: passed. The card keeps the existing padding and radius while adding numbered sections and consistent `gap-2`/`gap-3` grouping.
- Colors and visual tokens: passed. Indigo marks the selected mode; amber identifies image outputs; sky and emerald distinguish horizontal and vertical video choices without changing the product palette.
- Image quality and assets: passed. This form contains no raster assets; existing browser-native inputs and product icon resources are retained.
- Copy and content: passed. Every selectable output names its orientation, purpose, scheme, and dimensions where applicable.

## Functional checks

- Inactive mode controls are disabled and excluded from submitted parameters.
- Keyframe mode requires at least one selected image output.
- Complete-video mode requires at least one horizontal, vertical, or independent visual-video version.
- Up to three vertical keyframes may be selected; full vertical videos retain the existing maximum of two.
- Backend accepts explicit keyframe output keys, generates only selected artifacts, and preserves legacy requests without the new field.
- Browser console showed no application errors; the existing Tailwind CDN development warning remains unchanged.
- Automated regression: `186 passed`, `33 subtests passed`.
- JavaScript syntax, Python compilation, and `git diff --check` passed.

final result: passed

---

# Design QA: 详情页统一发布中心

## Visual truth

- Source references:
  - `/var/folders/dd/51s7f6gs6vz30qy285xd_b_80000gn/T/codex-clipboard-82a12466-cbde-4e3f-80d4-a9db1fdaac2d.png`
  - `/var/folders/dd/51s7f6gs6vz30qy285xd_b_80000gn/T/codex-clipboard-c75c2c28-4dc8-4dd3-b590-4ff5bcc75c9b.png`
  - `/var/folders/dd/51s7f6gs6vz30qy285xd_b_80000gn/T/codex-clipboard-b76bac4f-c115-4e7d-9307-fdb6695e534f.png`
- MP3 implementation screenshot: `/Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel/.codex-mp3-publish-tabs.png`
- PDF-to-audio final implementation screenshot: `/Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel/.codex-report-publish-tabs-final.png`
- Viewport: desktop, `1280px` browser width.
- State: publish center with the Douyin tab selected; Bilibili and WeChat Channels were also tested.

## Comparison evidence

- Full component view: passed. The three formerly stacked platform cards are represented by one white publish-center card with a compact three-column tab strip and one visible form.
- Focused region: passed. The reference Douyin form and the implementation Douyin tab panel were opened in the same comparison input. Form typography, field grid, borders, density, account-management action, and semantic warning treatment remain consistent with the source.
- MP3 and PDF-to-audio pages both use the same tab component and the same platform panels, so the platform-specific forms did not drift between workflows.

## Findings and iteration history

1. Initial P2: the browser-native focus outline produced an amber ring around the selected platform and did not match the existing indigo interaction color. Added an explicit indigo `focus-visible` ring and removed the native outline.
2. Post-fix: active-tab hierarchy is clear, keyboard focus remains visible, and neither the document nor the publish center has horizontal overflow.
3. No remaining P0/P1/P2 findings.

## Fidelity surfaces

- Fonts and typography: passed. Existing system-font stack, label sizes, weights, line heights, and form hierarchy are unchanged.
- Spacing and layout rhythm: passed. The tab header adds a compact navigation layer while eliminating the two extra full-height platform cards. Existing `p-4`, form gaps, radii, and section rhythm are retained.
- Colors and visual tokens: passed. White/slate surfaces and indigo selection reuse the existing detail-page palette; platform brand icon colors remain visible inside each panel.
- Image quality and assets: passed. These forms contain no raster imagery; existing Font Awesome brand and account-management icons are reused rather than replaced.
- Copy and content: passed. All platform labels, safety notices, account/video choices, titles, tags, and upload histories remain supplied by the original components.

## Functional checks

- Bilibili, Douyin, and WeChat Channels tabs each become the sole visible `tabpanel` when selected.
- Mouse selection and Left/Right/Home/End keyboard navigation are wired with `tablist`, `tab`, `tabpanel`, `aria-selected`, and roving `tabindex` semantics.
- A temporary Douyin title edit survived a switch to WeChat Channels and back; the original value was restored after the test.
- The selected tab survives the detail page's internal rerender because the page controller retains the active platform key.
- Both pages showed one tablist, no horizontal overflow, and no browser console errors.
- JavaScript syntax checks, two unit tests, and `git diff --check` passed.

final result: passed

---

# Design QA: MP3 选择列表增高

## Visual truth

- Source screenshot: `/var/folders/dd/51s7f6gs6vz30qy285xd_b_80000gn/T/codex-clipboard-40b13402-3eb2-4c80-8222-9c23df164278.png`
- Implementation screenshot: `/private/tmp/mp3-audio-list-height-after.png`
- Combined comparison: `/private/tmp/mp3-audio-list-height-comparison.png`
- Source pixels: `496x830`.
- Implementation focused-region pixels: `470x1364`.
- Desktop viewport: `1280x720` CSS pixels at device scale `1`.
- Responsive check viewport: `1024x900` CSS pixels at device scale `1`.
- State: “全部 MP3”第 1 页，10 条音频已加载，未选择音频。

## Comparison evidence

- Full/focused comparison: passed. The source and revised list region were placed side by side. The card styling, typography, spacing, controls, colors, copy, and pagination remain unchanged; only the available vertical list space increases.
- Desktop behavior: passed. At the `xl` breakpoint the list has a `1152px` maximum height. The loaded list measured `1112px` for both `clientHeight` and `scrollHeight`, so all 10 rows fit without an internal scrollbar.
- Responsive behavior: passed. At `1024x900`, the list resolves to `630px` (`70vh`) and retains vertical scrolling so the control does not grow beyond a practical small-screen viewport.

## Findings and iteration history

1. Initial P2: the `544px` list viewport displayed about five of ten rows and required a nested vertical scroll.
2. Fix: changed the responsive list cap to `70vh` below `xl` and `72rem` at `xl` and above.
3. Post-fix: all ten desktop rows fit in the list without nested scrolling; no remaining P0/P1/P2 findings.

## Fidelity surfaces

- Fonts and typography: passed. Existing system fonts, sizes, weights, truncation, and line heights are unchanged.
- Spacing and layout rhythm: passed. Existing card gaps, padding, border radii, and pagination spacing are unchanged.
- Colors and visual tokens: passed. Existing slate, indigo, emerald, border, and surface tokens are unchanged.
- Image quality and assets: passed. This region contains no raster assets; the existing icon library remains unchanged.
- Copy and content: passed. Search, tabs, metadata, audio controls, and pagination copy are unchanged.

## Functional checks

- Desktop list: 10 children, `clientHeight=1112`, `scrollHeight=1112`, `max-height=1152px`.
- Responsive list: `clientHeight=630`, `scrollHeight=1112`, `max-height=630px`, `overflow-y=auto`.
- Browser console: no application errors; only the existing Tailwind CDN development warning was observed.
- Source diff is limited to the list height and overflow utility classes.

final result: passed

---

# Design QA: PDF 投递列表完整展开

## Visual truth

- Source screenshot: `/var/folders/dd/51s7f6gs6vz30qy285xd_b_80000gn/T/codex-clipboard-c3880788-e8ec-4ccd-8b0f-78d55b4c5e30.png`
- Implementation screenshot: `/private/tmp/report-audio-pdf-expanded-viewport.png`
- Combined comparison: `/private/tmp/report-audio-pdf-height-comparison.png`
- Source viewport: `583x1013`.
- Implementation viewport: `583x1013`.
- State: “选择已同步 PDF”已选中，“全部 PDF”第 1 页，当前页 10 条 PDF 已加载。

## Comparison evidence

- Focused comparison: passed. 参考图与实现图在同一张对比图中检查；PDF 卡片、搜索、标签、元数据、预览入口和分页样式保持原有设计。
- Height behavior: passed. 列表从固定 `544px` 调整为内容自然高度；10 条数据下 `clientHeight=1112`、`scrollHeight=1112`，列表内部不再出现第二条纵向滚动条。
- Page behavior: passed. 分页仍紧接第 10 条 PDF 之后显示，页面使用浏览器自身的单一纵向滚动，不再嵌套滚动。

## Findings and iteration history

1. Initial P2: `max-h-[34rem] overflow-auto` 将 10 条 PDF 压在 `544px` 容器内，实际内容高 `1112px`，造成红框区域必须单独上下滚动。
2. Fix: 移除列表的固定最大高度和内部 overflow，让分页限定的 10 条内容自然撑开区域。
3. Post-fix: 10 条均完整展开，分页保留，无剩余 P0/P1/P2 问题。

## Fidelity surfaces

- Fonts and typography: passed. 字体、字号、字重、截断和行高未修改。
- Spacing and layout rhythm: passed. 卡片间距、内边距、边框、圆角、搜索区与分页间距未修改。
- Colors and visual tokens: passed. 原有 slate、indigo、状态标签和表面颜色未修改。
- Image quality and assets: passed. 此区域未新增或替换图片资产，继续使用现有图标库。
- Copy and content: passed. 标签、下载量、状态、文件信息和分页文案未修改。

## Functional checks

- PDF 列表：10 个子项，`clientHeight=1112`，`scrollHeight=1112`，`overflow-y=visible`。
- 分页：第 10 条之后正常显示，区域高度大于 0。
- 浏览器控制台：无应用错误；仅有项目原先存在的 Tailwind CDN 开发环境警告。
- 单元测试：`tests/unit/test_report_audio_workflow_frontend_contract.py` 共 11 项通过。

final result: passed

---

# Design QA: 网页内容投递历史

- source path: `/var/folders/dd/51s7f6gs6vz30qy285xd_b_80000gn/T/codex-clipboard-04b9fde8-51cb-4b76-bfd2-1cafff641388.png`
- implementation screenshot path/artifact: Codex in-app browser deliverable at `http://127.0.0.1:8891/content-pipeline-submit-qa.html`
- viewport: desktop `1456 x 991`; responsive check `390 x 844`; side-by-side comparison canvas `2400 x 820`
- dimensions/density: reference `1456 x 991`; implementation uses the same `1456 x 991` viewport at 1x CSS density
- same-state comparison evidence: `http://127.0.0.1:8891/__compare.html` renders the reference (browser chrome cropped) and implementation side by side at the same scale, both showing the successful-submission state
- focused region: right rail below “本次结果”, including “投递历史”, status chips, progress, PDF/MP3 child rows, refresh, and progressive loading

## Findings

- The existing header, page width, two-column grid, card radius, border color, spacing, and success-result state remain aligned with the supplied screenshot.
- The new history card stays inside the existing right rail and does not shift or resize the left submission workflow.
- Pending, processing, completed, partial-failure, and failed states are visually distinct without introducing a new color language.
- Per-media success/failure counts remain readable in the narrow desktop rail and in the stacked mobile layout.
- Mobile layout stays single-column with no horizontal clipping at `390 x 844`.

## Iteration history

1. Added the right-rail history card with compact grouped submissions and status markers.
2. Added paging, manual refresh, and automatic refresh for active submissions.
3. Preserved already loaded pages during refresh and hid “加载更多” once the full list had been fetched.
4. Added per-media success/failure counts and a visible partial-failure state after backend review.

## Primary interactions

- Manual refresh: passed.
- Load more: passed; list expanded from 4 to 6 records.
- Refresh after loading all: passed; 6 records remained and “加载更多” stayed hidden.
- Automatic refresh eligibility for pending/processing records: covered by active-state contract and browser state.

## Console errors

- No new errors were emitted by the feature during the final desktop/mobile verification. The browser log only retained three earlier shared-navigation `MutationObserver` errors from before the final verification run.

final result: passed
