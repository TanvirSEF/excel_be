# First-party analytics

Session analytics for the public site, shown in the admin panel at `/dashboard/traffic`.

## Audit of what existed before

| Piece | Behaviour | Verdict |
|---|---|---|
| `GET /posts/{slug}` registered a view | Counted every ISR regeneration, with the Next server's IP and a UA that is not on the bot list | Wrong. Removed |
| `POST /posts/{slug}/view` (article beacon) | No origin check, no rate limit, no dedupe, stored raw IPs | Hardened (below) |
| "Active Now (30m)" card | Number of article views in 30 minutes, not people | Renamed "Article Views (30m)" |
| `post_views.ip_address` | Raw IP of every reader | No longer written. Legacy rows leave with the existing 90 day prune |
| Bot filter | User agent regex only | Replaced for sessions by layered classification |

`post_views` and `Post.view_count` still power per-article stats and trending. Sessions are a separate, richer system.

## Architecture

```
Browser (lib/analytics-tracker.ts)
  -> POST /api/v1/track   batch of up to 20 events, direct to the API (no Vercel function)
  -> origin check, rate limit, schema validation, event-id dedupe (Redis NX)
  -> classify signals, derive device/source/geo, enqueue (Redis list)
     and, for clean traffic, update live sets (Redis sorted sets)
  -> flush_analytics job (every 20s) -> Postgres
        analytics_sessions, analytics_pageviews, analytics_events
  -> /api/v1/analytics/realtime         reads Redis only
  -> /api/v1/analytics/reports/*        reads Postgres, Redis-cached
```

Redis and Postgres were already in the stack; nothing new was introduced.

### Event types

| Event | Meaning |
|---|---|
| `pageview` | One page view. The first pageview of a session creates the session (`session_start`) |
| `ping` | `user_engagement`: cumulative engaged milliseconds for the page, sent every 30s while active and on `pagehide`/tab hide |
| `event` | `outbound_click` (destination host only), `file_download` (path only), `calculator_use` (page path, sent once per page on the first input inside a calculator page's `main`) |

Every event carries a client `event_id`. A repeated id inside one hour is dropped, so retries and double submits cannot double count. Pageviews are additionally unique on `page_id` in the database, and engagement is stored with `GREATEST`, so replays are harmless.

## Metric definitions

| Metric | Formula |
|---|---|
| Session | Events from one browser; a new session starts after 30 minutes without an event |
| Users | `COUNT(DISTINCT visitor_id)` (pseudonymous id kept in the browser, not a person) |
| New users | Users with no earlier session (same traffic filter) before the period start |
| Returning rate | `(users - new users) / users` |
| Pageviews | Pageviews whose timestamp is inside the period |
| Engagement time | Visible and active time (activity in the last 30s). Hidden or idle time excluded |
| Engaged session | Engagement time >= 10s or >= 2 pageviews |
| Engagement rate | `engaged sessions / sessions` |
| Bounce rate | `1 - engagement rate` |
| Session duration | `last_seen_at - started_at` for measurable sessions only |
| Measurable session | At least two events, and inactive for 30+ minutes (finished) |
| Average / median duration | Mean and `percentile_cont(0.5)` over measurable sessions |
| Excluded from duration | Single-event sessions and still-active sessions. Reported as a count, never as zero |
| Active users (realtime) | Distinct visitors with an event in the last 5 minutes; 30 minute figure shown as well |

Engagement time and session duration are measured separately. Where the browser closes without a final event the last heartbeat (at most 30s old) is the end of the session.

## Traffic quality

Each session gets a class, a reason and a confidence:

| Class | Rule | Confidence |
|---|---|---|
| `internal` | Matches `ANALYTICS_INTERNAL_IPS`, or the browser was flagged by visiting the dashboard | high |
| `known_bot` | User agent matches the known bot/automation list | high |
| `suspicious` | Behaviour score >= 50. Signals add up: `webdriver` +70, request burst (> 30 pageviews/min per visitor) +50, datacenter IP +40, missing languages +30, zero-size screen +30 | medium |
| `likely_human` | Score < 30 and (direct interaction, or >= 10s engaged, or >= 2 pages) | high with interaction, otherwise medium |
| `unknown` | Everything else, for example a quick read with no interaction | low |

The reports default to the `clean` filter (`likely_human` + `unknown`). `human` shows only `likely_human`; `all` shows everything.

Limitations, also shown in the UI:

- Only visitors that execute JavaScript are observed. Most crawlers never appear.
- Known bots are matched by user agent, which can be spoofed. Crawler identity is not verified by reverse DNS.
- `suspicious` is an estimate, not proof. `unknown` is not a claim of humanity.
- Datacenter detection only knows the published AWS (EC2) and Google Cloud IPv4 ranges, refreshed weekly. A datacenter IP adds 40 points on its own, which keeps a session out of likely_human but does not make it suspicious. Residential proxies, VPNs, other clouds and IPv6 are not detected.
- A client can mark itself `internal`, which only removes itself from reports.

## Privacy

- No cookies. A random visitor id and session id live in `localStorage`.
- IPs are used for the request only: salted SHA-256 hash is stored on the session, never the address.
- Country/city come from CDN headers or a cached GeoIP lookup; city is stored, address is not.
- Honours Do Not Track, Global Privacy Control, and `localStorage.ei_optout = "1"`.
- Outbound click stores the destination host only; download stores the path without query string.
- Dashboard pages are never tracked. Visiting the dashboard flags that browser as internal.
- Session ids shown in the admin UI are 8 character one-way hashes.
- Retention: sessions and pageviews `ANALYTICS_RETENTION_DAYS` (400), events `ANALYTICS_EVENT_RETENTION_DAYS` (90). A weekly job deletes older rows.

## API

All report endpoints require a super admin, senior editor or SEO specialist token. The ingest endpoint is public but origin checked and rate limited.

| Method and path | Purpose |
|---|---|
| `POST /api/v1/track` | Body `{ "events": [TrackEvent, ...] }`, 1 to 20 events. 204 on success |
| `GET /api/v1/analytics/realtime` | Active users (5 and 30 min), pageviews, top pages, sources, countries, devices, browsers, OS, new vs returning, recent pageviews |
| `GET /api/v1/analytics/reports/overview` | Summary, optional previous period, time series, duration distribution, methodology |
| `GET /api/v1/analytics/reports/breakdown?dimension=` | `source`, `referrer`, `device`, `browser`, `os`, `country`, `landing` |
| `GET /api/v1/analytics/reports/pages` | Paginated pages with views, users, engaged time, scroll, exits |
| `GET /api/v1/analytics/reports/quality` | Class counts, reasons, limitations |
| `GET /api/v1/analytics/reports/sessions` | Paginated privacy-safe session summaries |
| `GET /api/v1/analytics/reports/events` | Outbound click and download counts |

Common query parameters for reports: `from`, `to` (inclusive dates in the chosen timezone), `tz` (IANA name, default `UTC`), `traffic` (`clean` | `human` | `all`), `compare` (overview only). Ranges are capped at 400 days. Timestamps are stored in UTC; day and hour buckets use `tz`.

Reports are cached in Redis for 60 seconds when the period is still open and for one hour once it has closed.

## Database

New tables (migrations `d7e8f9a0b1c2`, `e8f9a0b1c2d3`):

- `analytics_sessions` indexed on `started_at`, `visitor_id`, `(traffic_class, started_at)`
- `analytics_pageviews` indexed on `session_id`, `occurred_at`, `(path, occurred_at)`
- `analytics_events` indexed on `session_id`, `(name, occurred_at)`

Existing tables are untouched.

No rollup table yet, on purpose. Reports hit indexed ranges and are cached (60s open periods, 1h closed), which is ample until sessions reach the hundreds of thousands per month. Add a rollup fed by the flush job when an uncached overview takes more than about one second; `report_service` is the only reader, so the change is local.

## Environment variables

Backend:

| Variable | Default | Notes |
|---|---|---|
| `ALLOWED_ORIGINS` | `http://localhost:3000` | Must include the public site origin or `/track` and `/view` return 403 |
| `FRONTEND_URL` | `http://localhost:3000` | Used to treat own-site referrers as direct |
| `RATE_LIMIT_TRACK` | `120/1minute` | Per IP |
| `RATE_LIMIT_VIEW` | `60/1minute` | Per IP |
| `ANALYTICS_INTERNAL_IPS` | empty | Comma separated IPs or CIDRs, for example office or VPN |
| `ANALYTICS_RETENTION_DAYS` | `400` | Sessions and pageviews |
| `ANALYTICS_EVENT_RETENTION_DAYS` | `90` | Custom events |

Frontend: only the existing `NEXT_PUBLIC_API_URL`.

## Verification

`tests/test_tracking.py` and `tests/test_view_beacon.py` cover: duplicate delivery, one session across pageviews, engagement versus duration, unmeasurable sessions, live-user expiry, bot/suspicious/internal classification, custom events, validation, authorization, comparison periods, timezone handling and the hardened view beacon. Expected differences from GA4: no cross-device identity, user agent bot matching instead of the IAB list, and engagement measured by this tracker.

## Production checklist

- [ ] Backend deployed first, then the frontend
- [ ] `ALLOWED_ORIGINS` and `FRONTEND_URL` set to the production site
- [ ] `ANALYTICS_INTERNAL_IPS` set if the team browses from fixed addresses
- [ ] `/api/v1/track` returns 204 from the live site (DevTools network tab)
- [ ] `/dashboard/traffic` Realtime shows your own visit, then Overview after a few minutes
- [ ] Cookie policy mentions first-party analytics identifiers in `localStorage`
- [ ] Decide whether to expose an opt-out control (`localStorage.ei_optout`)
