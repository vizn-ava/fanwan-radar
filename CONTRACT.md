# Local V1 API Contract

The backend serves `/` and `/static`. APIs are same-origin, localhost only, with no-store responses and checked write origins. Version: `1.0.0`.

## State And Model Configuration

- `GET /api/health`: status, version, loaded-code build fingerprint, instance identity and safe configuration flag; never credentials. The launcher refuses to silently reuse an older build.
- `GET /api/profile`: profile, recent messages, revision, generation, update timestamp and confirmation notices.
- `PATCH` or `PUT /api/profile`: partial `{patch, expected_revision?, generation?}`. Unknown fields and wrong types are rejected; updates never replace the whole profile implicitly.
- `POST /api/profile/interpret`: `{text, draft_patch?, expected_revision, generation}`. Interpret a paragraph with the actual configured model and return `{patch, changes, evidence, questions, summary, revision, generation, mode:"llm_profile_preview"}`. This is a read-only proposal, not a saved profile or chat message. User confirmation fills a local draft; the regular profile-save endpoint persists approved conditions. Missing Key, malformed model output or concurrent profile edits never silently save a partial proposal. Description length is capped at 20000 characters, is not stored in browser storage, and cannot initiate collection or application.
- `GET` / `PUT /api/model-settings`: safe settings / save `{base_url, model, api_key?}`. Blank Key preserves the existing Key. Responses contain `has_key`, never plaintext or ciphertext.
- `POST /api/model-settings/discover`: save supplied configuration and fetch actual available model IDs. `POST /api/model-settings/test`: genuine model connection test.
- `DELETE /api/model-settings/key`: explicitly remove stored Key.
- `POST /api/browser/open`: run the fixed dedicated-browser launcher, no arbitrary executable input. Protocol connection does not assert platform login.

State versions prevent stale writes; generation changes on clear. API reads and analysis do not invent missing profile facts. Conditions support salary period/basis, multiple locations, insurance, work/rest cycles, hours/shifts, employment, industry, exclusions and independent must/prefer/ignore priorities. See `app/models.py` for the authoritative schema.

## Real Search And Jobs

- `GET /api/sources`: real provider statuses and capabilities. BOSS and NCSS adapters are implemented; Liepin/Zhaopin are unavailable, public-summary mode is disabled.
- `POST /api/search/plan`: `{sources, keyword?, expected_revision?, generation?}` -> bounded query plan, supported front filters and pending postfilters. No collection occurs.
- `POST /api/search`: same body -> HTTP 202 with task. No demo source or fallback. The saved profile must be valid and confirmed.
- `GET /api/tasks/{id}`: task progress, independent source states, partial real jobs and matching analysis. Profile content fingerprint distinguishes actual condition edits from chat-only revision changes.
- `POST /api/tasks/{id}/cancel`: cooperative cancellation; preserve obtained real data and stop the affected worker without killing the user's browser.
- `GET /api/jobs`: analyzed saved results, summary and search metadata. Old fictional records are excluded.
- `POST /api/jobs/import`: `{jobs:[...]}`, real supported source URL and substantive supplied text required; marked user-provided/unverified.
- `GET /api/jobs/{id}`: analyzed real job. `POST /api/jobs/{id}/application`: text draft, questions and only explicitly supplied public contacts; no sending.

Jobs retain source URL(s), collection timestamp, full_jd/list_only/search_snippet/user_provided completeness, original facts and evidence. Title-only detail pages remain list_only. Deduplication preserves source links. Unknown conditions remain unknown; scores are heuristic sorting, never hiring probabilities. Incomplete or failed new searches do not relabel old results as newly fetched.

## Actual Streaming Analysis

`POST /api/chat/stream`: `{message, request_id, job_ids?:[max5], expected_revision?, generation?}`. With no explicit selection, use a bounded set of existing real jobs, not a new search. Missing Key returns 428; invalid selection returns 404; stale versions return 409.

Response is actual `text/event-stream`:

- `start`: request ID, context job IDs, profile revision.
- `delta`: `{text}` from actual upstream content deltas. Internal reasoning is neither displayed nor persisted.
- `done`: atomically committed profile/history snapshot, reply, mode and contextual job IDs.
- `error`: safe detail/code/http_status. Partial answer is not persisted.

Analysis is read-only for profile and cannot search, apply, or message. Only a complete valid upstream answer commits both chat messages atomically. Request IDs are idempotent; disconnected/stopped/invalid/incomplete/conflicted streams do not commit. Retries never silently fabricate a reply. History is capped at 40, replay metadata bounded, clear invalidates old work. Supported native Qwen3 uses fast non-thinking mode; arbitrary providers receive only compatible common parameters.

Legacy `POST /api/chat` structured-profile dialogue and `POST /api/resume` are compatibility APIs, not normal V1 workflow. Resume upload has no active UI; history resume text is excluded from model context by default.

`DELETE /api/data` requires `X-Confirm-Delete: yes`, clears business state and increments generation, while Key removal remains independent. Windows Key storage uses DPAPI for the current user; neither data nor browser state belongs in a source distribution.
