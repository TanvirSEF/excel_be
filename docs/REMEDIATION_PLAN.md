# Remediation Plan

Findings are verified against the code as of 2026-08-20. Each phase is implemented,
tested, and left green before the next one starts.

Status: Phases 0–8 complete.

Verified state that differs from the review:

- `.env` is gitignored and untracked; `.dockerignore` already excludes `venv/`,
  `.git`, `.env`, caches, `docs/`, `tests/`, `scripts/`. The `.env` on disk is local
  dev config only. Secrets should still be rotated if the source archive was ever
  shared outside the machine.
- `PostView` rows are already pruned after 90 days (`app/jobs/maintenance.py`), so
  storing one row per real view is sustainable at this scale.
- Pre-existing suite failure: `test_audit.py`, `test_auth.py` and `test_rbac.py`
  log in as `editor@test.com` (plus `writer@test.com`, `seo@test.com`), but nothing
  creates those users. They were manually seeded in one dev database. Any fresh
  database failed the suite.
- The redirects route is NOT a mismatch: the PRD lists API paths without the
  `/api/v1` prefix, and `GET /api/v1/redirects/{old_path}` matches that convention.
  `/sitemap.xml` sits at the root because crawlers require it there.

## Phase 0 — Green test baseline — DONE

Problem: undeclared dependency on hand-seeded test users.

Work:
- conftest gains an idempotent session fixture that ensures `editor@test.com`,
  `writer@test.com` and `seo@test.com` exist before tests run.

Result: full suite passes against a fresh database.

## Phase 1 — Data integrity core (P0 items 1–3) — DONE

### 1a. View/analytics pipeline

Problem: one Redis counter per post plus a single last-write-wins meta hash; the
flusher inserts one `PostView` row per flush regardless of the counter value.
`view_count`, daily series, unique visitors, referrers and trending all disagree.

Design: buffer individual events, not aggregates.

- `view_service.register_view` pushes one JSON event
  `{post_id, ip_hash, referrer, user_agent, viewed_at}` onto a Redis list
  `views:queue` (LPUSH, 7-day TTL refreshed per push).
- `view_count_flusher` pops batches (RPOP with count, 500 per batch, 100k cap per
  run), inserts the rows with the event timestamp as `viewed_at`, and increments
  `posts.view_count` by the per-post batch count in the same transaction.
- `views:pending:*` and `views:meta:*` keys are removed entirely.
- Popping before inserting keeps the current at-most-once semantics; a crash
  mid-flush can lose at most one batch, same class of risk as the old GETDEL.
- Bot filtering unchanged. Analytics and trending queries work unchanged.

Done when: N views of a post produce N `PostView` rows with distinct referrers
preserved, `view_count == N`, and analytics/trending read correctly after flush.

### 1b. Atomic refresh-token rotation

Problem: read-check-set on `revoked` lets two concurrent rotations both succeed.

Work: claim the token with a single conditional UPDATE
(`SET revoked = true WHERE token_hash = :h AND revoked = false RETURNING user_id`);
only the request that claims it proceeds.

Done when: two concurrent rotations of one token yield exactly one 200 and one 401.

### 1c. Transaction boundaries for post/tag writes

Problem: `tag_service.sync_post_tags` commits internally, so `post_service.create`
and `update` are two transactions; tags can land while the post change rolls back.
The WordPress importer calls the same function.

Work:
- `sync_post_tags` flushes but never commits; callers own the commit.
- `post_service.create`/`update` become single transactions including tags.
- Importer adds its own explicit commit (it intentionally keeps granular commits
  for restartability).

Done when: a post update that fails after tag sync leaves tags unchanged, and the
existing suite stays green.

## Phase 2 — Security hardening (P0 items 4–6) — DONE

Also fixed while here: deactivating a user via `PATCH /users/{id}` now revokes
their refresh tokens, matching the dedicated deactivate endpoint.

- Media upload: stream-read the multipart body with a hard byte cap instead of
  `await file.read()`; reject over-limit uploads before they are buffered.
- Importer media download: `client.stream(...)` with a byte ceiling; abort when
  the running total exceeds `MAX_DOWNLOAD_BYTES` (Content-Length can lie).
- Rate-limit `POST /auth/forgot-password` and `POST /auth/reset-password` (new
  `RATE_LIMIT_FORGOT_PASSWORD` / `RATE_LIMIT_RESET_PASSWORD` settings, IP-keyed
  via the existing limiter).
- Block `is_active: false` self-update by a super admin in `update_user` (same
  `SELF_DEACTIVATION` error the dedicated endpoint returns).

Done when: each item has a failing-then-passing test.

## Phase 3 — API contract (P1 items 9–10) — DONE

- `extra="forbid"` on request schemas via a shared base model.
- Global `RequestValidationError` handler emitting the project error envelope
  (`{"error": {code, message, status}}`) instead of FastAPI's default shape.

Done when: unknown-field and bad-type requests return the standard envelope with
422, and the existing suite is updated where it asserted the old shape.

## Phase 4 — Caching layer (P1 items 8, 11) — DONE

- `app/services/cache_service.py`: JSON get/set/delete, pattern delete, all
  fail-open when Redis is down.
- `cat:tree` (10 min), `post:{slug}` (5 min), `posts:home:{page}:{page_size}`
  and `posts:trending:{page}:{page_size}` (2 min).
- Invalidation: category create/update/delete/reorder → tree + sitemap; post
  update/publish/soft-delete/SEO update → detail; publish/soft-delete and the
  scheduled publisher → detail + both list families; trending recalc →
  `posts:trending:*`. Post slug change invalidates the sitemap too.
- `revoked:{token_id}` from the PRD is deliberately NOT implemented: rotation is
  now a single atomic claim (nothing cached to read), access tokens expire in
  15 minutes, and caching auth decisions would delay revocation propagation.

## Phase 5 — Downloadable assets (P1 item 7) — DONE

- `app/schemas/asset.py`, `app/services/asset_service.py`, routes on
  `/posts/{id}/assets` plus `app/api/v1/assets.py` for
  `GET /assets/{id}/download` and `DELETE /assets/{id}`.
- Allowed: xlsx, xls, csv, pdf, zip; 25MB cap via the streaming reader; magic
  byte check for binary formats; stored under `downloads/{post_id}/…` in R2.
- Download issues a 5-minute presigned R2 URL and increments `download_count`;
  public list/download only for published posts; delete is editor-only.

## Phase 6 — Audit and importer robustness (P1 item 12, P2 item 13) — DONE

- Scheduled publications write `post.publish` audit rows (system actor,
  `source: "scheduler"` in meta) in the same transaction as the status update.
- Importer creates all comments first, flushes, then links parents in a second
  pass, so replies preceding their parent in the WXR keep their thread.

## Phase 7 — Config and deploy hygiene (P2 items 15–16, leftovers) — DONE

- README says Python 3.14, matching Dockerfile and venv.
- `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` wired into the engine, plus `pool_pre_ping`.
- HSTS sent only when `environment == "production"`.
- Redirects mount verified as correct (see note above); no change.

## Phase 8 — Test expansion and docs — DONE

- Concurrency: 5-way refresh rotation burst (exactly one 200, one surviving
  token row) and 50 parallel view registrations flushed by two concurrent
  flush runs (every view stored exactly once, view_count exact).
- Analytics end-to-end through the API: post totals, 7-day series, unique
  visitors and overview top-posts match flushed events.
- Cache invalidation matrix: title update in the home list, slug rename
  (old detail 404s, new resolves), soft delete (detail 404 + gone from the
  list), category rename in the tree.
- Upload caps: the 25MB asset cap rejects with FILE_TOO_LARGE (the media 10MB
  cap was already covered).
- The matrix caught a real gap: post update only invalidated the detail key,
  so the home/trending lists served a stale title for up to 2 minutes.
  Updates touching list-visible fields (title, slug, excerpt,
  featured_image_url, content_json) now invalidate both list families too.
- README's test section notes the Docker services requirement.
