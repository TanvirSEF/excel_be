# Excel Insider — FastAPI Backend Engineering Specification
**Internal Technical PRD — v1.0**
**Prepared by:** Tanvir Hasan, Zephlo
**Purpose:** A complete, implementation-ready backend specification. Feed this document to an AI coding assistant (Claude Code, Cursor, etc.) section by section to scaffold and build the entire backend.

---

## Table of Contents

1. [Overview & Objectives](#1-overview--objectives)
2. [Tech Stack & Rationale](#2-tech-stack--rationale)
3. [Project Folder Structure](#3-project-folder-structure)
4. [Database Design (Full Schema)](#4-database-design-full-schema)
5. [Authentication & RBAC](#5-authentication--rbac)
6. [API Endpoint Specification](#6-api-endpoint-specification)
7. [Caching Strategy (Redis)](#7-caching-strategy-redis)
8. [Search Implementation](#8-search-implementation)
9. [Media & File Storage](#9-media--file-storage)
10. [Background Jobs](#10-background-jobs)
11. [WordPress Migration (ETL)](#11-wordpress-migration-etl)
12. [Configuration & Environment Variables](#12-configuration--environment-variables)
13. [Error Handling Standard](#13-error-handling-standard)
14. [Security Checklist](#14-security-checklist)
15. [Testing Strategy](#15-testing-strategy)
16. [Deployment (Dokploy)](#16-deployment-dokploy)
17. [Build Order / Milestones](#17-build-order--milestones)

---

## 1. Overview & Objectives

Excel Insider is moving from a monolithic WordPress site to a **headless architecture**:
`Next.js (frontend, separate project) → REST API (this backend) → PostgreSQL + Redis`

This backend must:
- Serve all content (posts, categories, tags, comments, media) via REST API
- Handle authentication and 4-tier role-based access control
- Power a custom block-based editor's data layer (Excel formula blocks, calculators, tables)
- Support full-text search across articles
- Track analytics (views, trending posts)
- Safely import ~1,900+ existing WordPress posts with zero broken URLs
- Run reliably on a single server managed by **Dokploy** (not serverless) — so the design favors a monolithic-but-modular FastAPI app over microservices

---

## 2. Tech Stack & Rationale

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | Latest stable, better typing, faster than 3.10/3.11 |
| Framework | **FastAPI** | Async-native, auto-generated OpenAPI/Swagger docs, best-in-class for AI-assisted development (huge training data coverage) |
| **ORM** | **SQLAlchemy 2.0 (async) + Alembic** | See detailed comparison below |
| Database | **PostgreSQL 16** | JSONB for block-editor content, native full-text search, mature, works great on a single VPS |
| Migrations | **Alembic** | Standard companion to SQLAlchemy; version-controlled schema changes |
| Cache/Session | **Redis 7** | Query caching, rate limiting, session/refresh-token blacklist |
| Auth | **OAuth2 Password Flow + JWT** via `PyJWT` | Access + refresh token rotation; PyJWT is the actively maintained choice |
| Password Hashing | **Argon2** (via `pwdlib[argon2]`) | Stronger than bcrypt, recommended by OWASP; `pwdlib` replaces the unmaintained `passlib` (broken on Python 3.13+) |
| Email | **Resend** HTTP API (via `httpx`) | Transactional mail (password reset, verification, unsubscribe links) — no SMTP dependency, one module to swap providers |
| Validation | **Pydantic v2** | Ships with FastAPI, fast (Rust core), clean schema definitions |
| Search | **PostgreSQL Full-Text Search** (`tsvector` + GIN index) to start; upgrade path to **Meilisearch** later | One less service to run on a single VPS; Postgres FTS is genuinely fast enough for ~2,000 articles |
| Object Storage | **Cloudflare R2** (S3-compatible) via `boto3` | No egress fees, cheaper than S3 |
| Background Jobs | **APScheduler** (in-process) to start; upgrade path to **Celery + Redis** if load grows | Avoids running a separate worker + broker on a single VPS for a site this size |
| Server | **Uvicorn** in a Docker container, reverse-proxied by **Dokploy's Traefik** | TLS termination, routing and git-push deploys handled by Dokploy |
| Containerization | **Docker** (root `Dockerfile`) deployed via **Dokploy** | Reproducible builds; Postgres + Redis run as separate Dokploy services. Compose stays for local dev only |

### Why SQLAlchemy 2.0 (async) over alternatives

| ORM | Verdict |
|---|---|
| **SQLAlchemy 2.0 async** ✅ | Chosen. Most mature, most AI-assistant training coverage, best for complex relationships (self-referencing categories, many-to-many tags), rock-solid Alembic migrations. The new `Mapped[]` typed syntax (2.0-style) is clean and close to Pydantic. |
| SQLModel | Elegant (same author as FastAPI), less boilerplate — but weaker for complex queries/relationships and smaller ecosystem. Good for tiny projects, not ideal here. |
| Tortoise ORM | Async-native, Django-like — but smaller community, fewer AI-assistant examples, migration tooling (Aerich) less mature than Alembic. |
| Prisma (Python client) | Great DX but still less mature in Python than in JS/TS; adds a non-Python codegen step. |

**Decision: SQLAlchemy 2.0 async + Alembic.** This is what the rest of this document assumes.

---

## 3. Project Folder Structure

```
excel-insider-backend/
├── app/
│   ├── main.py                     # FastAPI app entrypoint
│   ├── core/
│   │   ├── config.py                # Settings (Pydantic BaseSettings)
│   │   ├── security.py              # JWT creation/verification, password hashing
│   │   ├── database.py              # Async engine + session factory
│   │   ├── redis_client.py          # Redis connection pool
│   │   └── exceptions.py            # Custom exception classes
│   │
│   ├── models/                      # SQLAlchemy ORM models
│   │   ├── user.py
│   │   ├── post.py
│   │   ├── category.py
│   │   ├── tag.py
│   │   ├── comment.py
│   │   ├── media.py
│   │   ├── downloadable_asset.py
│   │   ├── refresh_token.py
│   │   ├── audit_log.py
│   │   ├── newsletter.py
│   │   ├── redirect.py
│   │   └── post_view.py
│   │
│   ├── schemas/                     # Pydantic request/response models
│   │   ├── user.py
│   │   ├── post.py
│   │   ├── category.py
│   │   ├── tag.py
│   │   ├── comment.py
│   │   ├── auth.py
│   │   ├── media.py
│   │   └── common.py                # Shared: pagination, error responses
│   │
│   ├── api/
│   │   └── v1/
│   │       ├── router.py            # Aggregates all v1 routers
│   │       ├── auth.py
│   │       ├── users.py
│   │       ├── posts.py
│   │       ├── categories.py
│   │       ├── tags.py
│   │       ├── comments.py
│   │       ├── media.py
│   │       ├── search.py
│   │       ├── analytics.py
│   │       └── newsletter.py
│   │
│   ├── services/                    # Business logic (kept out of routers)
│   │   ├── auth_service.py
│   │   ├── post_service.py
│   │   ├── category_service.py
│   │   ├── media_service.py
│   │   ├── search_service.py
│   │   ├── seo_service.py           # sitemap.xml, OG image trigger, JSON-LD
│   │   ├── email_service.py          # transactional SMTP (reset links, unsubscribe)
│   │   └── analytics_service.py
│   │
│   ├── deps/                        # FastAPI dependencies
│   │   ├── auth_deps.py             # get_current_user, require_role()
│   │   └── pagination.py
│   │
│   ├── jobs/                        # APScheduler background jobs
│   │   ├── scheduler.py
│   │   ├── trending_calculator.py
│   │   ├── sitemap_regenerator.py
│   │   ├── view_count_flusher.py
│   │   └── scheduled_publisher.py
│   │
│   └── utils/
│       ├── slugify.py
│       ├── reading_time.py
│       └── image_processing.py       # WebP conversion, resizing
│
├── alembic/
│   ├── versions/
│   └── env.py
│
├── scripts/
│   ├── migrate_wordpress.py          # WP ETL entrypoint
│   ├── wp_extract.py
│   ├── wp_transform.py
│   └── wp_load.py
│
├── tests/
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_posts.py
│   ├── test_categories.py
│   └── test_rbac.py
│
├── .env.example
├── .gitignore
├── .dockerignore
├── Dockerfile
├── docker-compose.yml           # local dev only: Postgres + Redis
├── alembic.ini
├── requirements.txt
└── README.md
```

---

## 4. Database Design (Full Schema)

**Engine:** PostgreSQL 16 · **Primary keys:** UUID (via `uuid_generate_v4()` or Python `uuid4`) · **Timestamps:** `TIMESTAMPTZ`, always UTC

### 4.1 `users`

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| name | VARCHAR(120) | NOT NULL |
| email | VARCHAR(255) | UNIQUE, NOT NULL |
| password_hash | VARCHAR(255) | NOT NULL |
| role | ENUM | `super_admin`, `senior_editor`, `technical_writer`, `seo_specialist` — NOT NULL |
| avatar_url | TEXT | NULLABLE |
| bio | TEXT | NULLABLE |
| is_active | BOOLEAN | DEFAULT true |
| is_verified | BOOLEAN | DEFAULT false |
| last_login_at | TIMESTAMPTZ | NULLABLE |
| created_at | TIMESTAMPTZ | DEFAULT now() |
| updated_at | TIMESTAMPTZ | DEFAULT now(), auto-update |

**Indexes:** unique on `email`; index on `role`

### 4.2 `refresh_tokens`

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| user_id | UUID | FK → users.id, CASCADE DELETE |
| token_hash | VARCHAR(255) | NOT NULL — store a hash, never the raw token |
| device_info | VARCHAR(255) | NULLABLE (user agent snippet) |
| expires_at | TIMESTAMPTZ | NOT NULL |
| revoked | BOOLEAN | DEFAULT false |
| created_at | TIMESTAMPTZ | DEFAULT now() |

**Indexes:** index on `user_id`, index on `expires_at` (for cleanup job)

### 4.2b `password_reset_tokens`

Single-use reset tokens — opaque random value, only the SHA-256 hash is stored (same pattern as `refresh_tokens`).

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| user_id | UUID | FK → users.id, CASCADE DELETE |
| token_hash | VARCHAR(255) | UNIQUE, NOT NULL |
| expires_at | TIMESTAMPTZ | NOT NULL — 30 minute lifetime |
| used | BOOLEAN | DEFAULT false — flipped on use; a new request invalidates previous unused tokens |
| created_at | TIMESTAMPTZ | DEFAULT now() |

Email-verification tokens are stateless short-lived JWTs (`type=verify_email`, 24h) — low-risk flag, no table needed.

### 4.3 `categories`

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| name | VARCHAR(100) | NOT NULL |
| slug | VARCHAR(120) | UNIQUE, NOT NULL |
| parent_id | UUID | FK → categories.id, NULLABLE (self-referencing, for sub-topics) |
| order_index | INTEGER | DEFAULT 0 |
| description | TEXT | NULLABLE |
| icon_url | TEXT | NULLABLE |
| color_hex | VARCHAR(7) | NULLABLE, e.g. `#22C55E` |
| is_featured | BOOLEAN | DEFAULT false |
| seo_title | VARCHAR(255) | NULLABLE |
| seo_description | VARCHAR(500) | NULLABLE |
| created_at | TIMESTAMPTZ | DEFAULT now() |
| updated_at | TIMESTAMPTZ | DEFAULT now() |

**Indexes:** unique on `slug`, index on `parent_id`

### 4.4 `posts`

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| title | VARCHAR(255) | NOT NULL |
| slug | VARCHAR(255) | UNIQUE, NOT NULL |
| excerpt | VARCHAR(500) | NULLABLE |
| content_json | JSONB | NOT NULL — block-editor structured content (formula blocks, tables, calculators) |
| content_html | TEXT | NULLABLE — server-rendered cache of `content_json`, regenerated on save |
| content_tsv | TSVECTOR | GENERATED — for full-text search (see §8) |
| featured_image_url | TEXT | NULLABLE |
| author_id | UUID | FK → users.id |
| category_id | UUID | FK → categories.id, NULLABLE (drafts may be uncategorized) |
| status | ENUM | `draft`, `pending_review`, `published`, `rejected`, `scheduled` |
| view_count | INTEGER | DEFAULT 0 |
| is_trending | BOOLEAN | DEFAULT false |
| reading_time_minutes | SMALLINT | NULLABLE, auto-calculated on save |
| meta_title | VARCHAR(255) | NULLABLE |
| meta_description | VARCHAR(500) | NULLABLE |
| canonical_url | TEXT | NULLABLE |
| og_image_url | TEXT | NULLABLE |
| schema_type | VARCHAR(50) | DEFAULT `TechArticle` — for JSON-LD (`TechArticle`, `HowTo`, `FAQPage`) |
| published_at | TIMESTAMPTZ | NULLABLE |
| scheduled_at | TIMESTAMPTZ | NULLABLE |
| created_at | TIMESTAMPTZ | DEFAULT now() |
| updated_at | TIMESTAMPTZ | DEFAULT now() |
| deleted_at | TIMESTAMPTZ | NULLABLE — soft delete marker; all queries filter `deleted_at IS NULL` |

**Indexes:** unique on `slug`, index on `status`, index on `category_id`, index on `author_id`, index on `published_at DESC`, **GIN index on `content_tsv`**, index on `is_trending`

### 4.5 `tags` & `post_tags`

**tags**

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| name | VARCHAR(60) | NOT NULL |
| slug | VARCHAR(80) | UNIQUE, NOT NULL |

**post_tags** (many-to-many join table)

| Column | Type | Constraints |
|---|---|---|
| post_id | UUID | FK → posts.id, CASCADE DELETE, part of composite PK |
| tag_id | UUID | FK → tags.id, CASCADE DELETE, part of composite PK |

### 4.6 `comments`

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| post_id | UUID | FK → posts.id, CASCADE DELETE |
| parent_id | UUID | FK → comments.id, NULLABLE (nested replies) |
| user_name | VARCHAR(100) | NOT NULL |
| user_email | VARCHAR(255) | NOT NULL (not shown publicly) |
| comment_text | TEXT | NOT NULL |
| status | ENUM | `pending`, `approved`, `spam`, `rejected` — DEFAULT `pending` |
| ip_address | VARCHAR(45) | NULLABLE (IPv4/IPv6) |
| created_at | TIMESTAMPTZ | DEFAULT now() |

**Indexes:** index on `post_id`, index on `status`

### 4.7 `downloadable_assets`

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| post_id | UUID | FK → posts.id, CASCADE DELETE |
| file_name | VARCHAR(255) | NOT NULL |
| file_url | TEXT | NOT NULL |
| file_type | VARCHAR(20) | e.g. `xlsx`, `csv` |
| file_size_kb | INTEGER | NULLABLE |
| download_count | INTEGER | DEFAULT 0 |
| created_at | TIMESTAMPTZ | DEFAULT now() |

### 4.8 `media`

General media library (separate from post-attached downloadable assets — this covers images used across the site).

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| uploader_id | UUID | FK → users.id |
| file_url | TEXT | NOT NULL |
| file_type | VARCHAR(20) | `image`, `document`, etc. |
| alt_text | VARCHAR(255) | NULLABLE, auto-generated on upload |
| width | INTEGER | NULLABLE |
| height | INTEGER | NULLABLE |
| size_kb | INTEGER | NULLABLE |
| folder | VARCHAR(100) | DEFAULT `uncategorized` |
| created_at | TIMESTAMPTZ | DEFAULT now() |

### 4.9 `post_views` (or aggregated daily table)

For a site this size, log raw events and roll them up nightly rather than aggregating on every request.

| Column | Type | Constraints |
|---|---|---|
| id | BIGSERIAL | PK |
| post_id | UUID | FK → posts.id |
| viewed_at | TIMESTAMPTZ | DEFAULT now() |
| ip_hash | VARCHAR(64) | SHA-256 hash of IP (never store raw IP for views — privacy) |
| referrer | TEXT | NULLABLE |
| user_agent | TEXT | NULLABLE |

**Indexes:** index on `(post_id, viewed_at)` — this table grows fast; consider partitioning by month once traffic is high, or pruning rows older than 90 days after rollup.

### 4.10 `audit_logs`

Tracks admin-level actions (required for Super Admin oversight — publish/delete/role changes).

| Column | Type | Constraints |
|---|---|---|
| id | BIGSERIAL | PK |
| user_id | UUID | FK → users.id |
| action | VARCHAR(50) | e.g. `post.publish`, `user.role_change`, `post.delete` |
| entity_type | VARCHAR(50) | e.g. `post`, `user`, `category` |
| entity_id | UUID | NULLABLE |
| metadata | JSONB | NULLABLE — before/after values, etc. |
| created_at | TIMESTAMPTZ | DEFAULT now() |

### 4.11 `newsletter_subscribers`

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| email | VARCHAR(255) | UNIQUE, NOT NULL |
| status | ENUM | `subscribed`, `unsubscribed`, `bounced` |
| source | VARCHAR(50) | e.g. `article_footer`, `popup` |
| subscribed_at | TIMESTAMPTZ | DEFAULT now() |

> Sync this table to Mailchimp/ConvertKit via their API on a scheduled job, or on webhook — don't make the newsletter provider a hard dependency of the signup endpoint's response time.

### 4.12 `redirects`

Critical for the zero-broken-link WordPress migration.

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| old_path | VARCHAR(500) | UNIQUE, NOT NULL — full old WordPress URL path |
| new_path | VARCHAR(500) | NOT NULL |
| redirect_type | SMALLINT | DEFAULT 301 |
| created_at | TIMESTAMPTZ | DEFAULT now() |

**Indexes:** unique on `old_path` — this table is queried by Next.js middleware or an edge function on every 404, so keep the lookup O(1).

### 4.13 Entity Relationship Summary

```
users ─┬──< posts (author)
       ├──< refresh_tokens
       ├──< media (uploader)
       └──< audit_logs

categories ─┬──< categories (self, parent_id)
            └──< posts

posts ─┬──< post_tags >── tags
       ├──< comments (self-referencing via parent_id)
       ├──< downloadable_assets
       └──< post_views

comments ──< comments (self, parent_id — nested replies)
```

---

## 5. Authentication & RBAC

### 5.1 Flow

1. `POST /api/v1/auth/login` — email + password → returns `access_token` (short-lived, 15 min) + `refresh_token` (long-lived, 7–30 days, stored hashed in `refresh_tokens` table)
2. Access token sent as `Authorization: Bearer <token>` on every protected request
3. `POST /api/v1/auth/refresh` — exchanges a valid refresh token for a new access token; **rotate** the refresh token on every use (issue new, revoke old) to limit replay-attack window
4. `POST /api/v1/auth/logout` — revokes the refresh token (sets `revoked = true`)
5. Access tokens are **never** stored server-side (stateless JWT) — only refresh tokens are tracked, so logout/revocation is enforceable

### 5.2 JWT Payload

```json
{
  "sub": "<user_id>",
  "role": "senior_editor",
  "jti": "<uuid4>",
  "exp": 1234567890,
  "iat": 1234567000,
  "type": "access"
}
```

The `jti` (JWT ID) claim backs the `revoked:{token_id}` revocation cache in §7.

### 5.3 Role Permission Matrix

| Action | Super Admin | Senior Editor | Technical Writer | SEO Specialist |
|---|---|---|---|---|
| Manage users / roles | ✅ | ❌ | ❌ | ❌ |
| Site settings / DB backup trigger | ✅ | ❌ | ❌ | ❌ |
| Create draft post | ✅ | ✅ | ✅ | ❌ |
| Edit **own** draft | ✅ | ✅ | ✅ | ❌ |
| Edit **any** post | ✅ | ✅ | ❌ | ❌ |
| Publish / reject / schedule post | ✅ | ✅ | ❌ | ❌ |
| Manage categories / navigation | ✅ | ✅ | ❌ | ❌ |
| Moderate comments | ✅ | ✅ | ❌ | ❌ |
| Edit SEO fields (any post) | ✅ | ✅ | ❌ | ✅ |
| View own post analytics | ✅ | ✅ | ✅ | ❌ |
| View site-wide analytics | ✅ | ✅ | ❌ | ✅ |

### 5.4 Implementation Pattern

```python
# app/deps/auth_deps.py
from fastapi import Depends, HTTPException, status
from app.models.user import UserRole

def require_role(*allowed_roles: UserRole):
    def dependency(current_user = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return current_user
    return dependency

# Usage in a router:
@router.post("/posts/{id}/publish")
async def publish_post(
    id: UUID,
    user = Depends(require_role(UserRole.SUPER_ADMIN, UserRole.SENIOR_EDITOR)),
):
    ...
```

For **object-level** rules (e.g. "Technical Writer can edit only their own drafts"), add a second check inside the service layer after the role dependency passes — comparing `post.author_id == current_user.id`.

### 5.5 Password Handling

- Hash with **Argon2** (`pwdlib.PasswordHash.recommended()` — argon2id)
- Minimum password policy: 10+ characters, enforced client-side and server-side
- Rate-limit `/auth/login` to 5 attempts / 15 min per IP (via Redis, see §14)

---

## 6. API Endpoint Specification

Base path: `/api/v1`. All list endpoints support `?page=1&page_size=20` pagination (`page_size` capped at 50, default 20) and return the shape defined in `schemas/common.py`:

```json
{
  "items": [ ... ],
  "total": 1904,
  "page": 1,
  "page_size": 20,
  "total_pages": 96
}
```

### 6.1 Auth

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/auth/register` | Super Admin only | Create a new team account (no public self-signup) |
| POST | `/auth/login` | Public | Email + password → tokens |
| POST | `/auth/refresh` | Refresh token | Rotate tokens |
| POST | `/auth/logout` | Access token | Revoke refresh token |
| GET | `/auth/me` | Access token | Current user profile |
| POST | `/auth/forgot-password` | Public (rate-limited) | Email a reset link; always returns 200 to prevent user enumeration |
| POST | `/auth/reset-password` | Public (rate-limited) | Set new password via emailed token; revokes all of the user's refresh tokens |
| POST | `/auth/change-password` | Access token | Verify current password, set new one, revoke other sessions |
| POST | `/auth/verify-email` | Public | Mark `is_verified = true` using the token from the verification email |

### 6.2 Users

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/users` | Super Admin | List all team accounts |
| GET | `/users/{id}` | Super Admin or self | User detail |
| PATCH | `/users/{id}` | Super Admin or self (limited fields) | Update profile / role |
| DELETE | `/users/{id}` | Super Admin | Deactivate (soft-delete via `is_active=false`) |

### 6.3 Categories

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/categories` | Public | Full nested tree (cached, see §7) |
| GET | `/categories/{slug}` | Public | Single category + its posts (paginated) |
| POST | `/categories` | Editor+ | Create |
| PATCH | `/categories/{id}` | Editor+ | Update |
| PATCH | `/categories/reorder` | Editor+ | Bulk drag-drop reorder (accepts array of `{id, order_index, parent_id}`) |
| DELETE | `/categories/{id}` | Super Admin | Delete (blocked if posts exist under it) |

Route order matters: declare `/categories/reorder` before `/categories/{id}`, or FastAPI will match the literal `reorder` as an `{id}`.

### 6.4 Posts

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/posts` | Public | List published posts — filters: `?category=`, `?tag=`, `?trending=true`, `?author=` |
| GET | `/posts/{slug}` | Public | Full post detail (increments view — see §7 for async counting) |
| GET | `/posts/admin` | Writer+ | All posts regardless of status, scoped to role (writers see own only) |
| POST | `/posts` | Writer+ | Create draft |
| PATCH | `/posts/{id}` | Writer (own) / Editor+ (any) | Update |
| POST | `/posts/{id}/submit-review` | Writer (own) | Draft → pending_review |
| POST | `/posts/{id}/publish` | Editor+ | pending_review → published |
| POST | `/posts/{id}/reject` | Editor+ | pending_review → rejected (with reason) |
| POST | `/posts/{id}/schedule` | Editor+ | Set `scheduled_at`, status → scheduled |
| DELETE | `/posts/{id}` | Editor+ | Soft delete |
| PATCH | `/posts/{id}/seo` | Editor+ or SEO Specialist | Update meta_title/description/canonical/og_image only |

### 6.5 Tags

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/tags` | Public | List all |
| POST | `/tags` | Writer+ | Create (or auto-create on post save if not exists) |

### 6.6 Comments

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/posts/{post_id}/comments` | Public | Approved comments, nested |
| POST | `/posts/{post_id}/comments` | Public | Submit (status=pending, rate-limited) |
| PATCH | `/comments/{id}/moderate` | Editor+ | Approve / reject / mark spam |
| DELETE | `/comments/{id}` | Editor+ | Delete |

### 6.7 Media

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/media/upload` | Writer+ | Multipart upload → resizes, converts to WebP, pushes to R2, returns URL |
| GET | `/media` | Writer+ | Browse library, filter by folder |
| DELETE | `/media/{id}` | Editor+ | Delete (blocks if referenced by a published post) |

### 6.8 Downloadable Assets

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/posts/{id}/assets` | Writer+ | Attach `.xlsx`/`.csv` — stored as-is under the `/downloads/` prefix (§9) |
| GET | `/posts/{id}/assets` | Public | List a post's downloadable files |
| GET | `/assets/{id}/download` | Public | Issue a short-lived signed R2 URL, increments `download_count` |
| DELETE | `/assets/{id}` | Editor+ | Remove asset |

### 6.9 Search

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/search?q=` | Public | Full-text search across title/excerpt/content, typo-tolerant (see §8) |

### 6.10 Analytics

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/analytics/posts/{id}` | Own post (Writer) or Editor+ | Views over time, referrers |
| GET | `/analytics/overview` | Editor+ / SEO Specialist | Site-wide trending, top posts, traffic summary |

### 6.11 Newsletter

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/newsletter/subscribe` | Public (rate-limited) | Add subscriber, sync to ESP async |
| POST | `/newsletter/unsubscribe` | Public (token-based, from email link) | |

### 6.12 SEO / System

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/sitemap.xml` | Public | Auto-generated, cached, invalidated on publish |
| GET | `/redirects/{old_path:path}` | Public/internal | Lookup for Next.js middleware 301 handling — the `:path` converter lets WordPress paths containing slashes match |

---

## 7. Caching Strategy (Redis)

| What | Key Pattern | TTL | Invalidation |
|---|---|---|---|
| Category tree | `cat:tree` | 1 hour | On any category create/update/delete/reorder |
| Post detail | `post:{slug}` | 10 min | On post update/publish |
| Homepage post list | `posts:home:{page}` | 5 min | On any publish event |
| Trending posts | `posts:trending` | 30 min | Recomputed by scheduled job (§10) |
| Rate limiting | `ratelimit:{ip}:{endpoint}` | Sliding window | N/A (auto-expires) |
| Refresh-token revocation check | `revoked:{token_id}` | Matches token TTL | Set on logout |
| View counting buffer | `views:pending:{post_id}` | Flushed every 60s | Background job increments DB then clears |

**View counting pattern (important for accuracy under load):** don't `UPDATE posts SET view_count = view_count + 1` on every single request — instead `INCR` a Redis counter per post, and a background job (§10) flushes accumulated counts to Postgres every 60 seconds. This avoids row-lock contention on popular posts. Skip counting for obvious bots/crawlers (user-agent check) so analytics stay honest.

---

## 8. Search Implementation

**Phase 1 (launch): PostgreSQL full-text search**

```sql
ALTER TABLE posts ADD COLUMN content_tsv tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('english', coalesce(title,'')), 'A') ||
    setweight(to_tsvector('english', coalesce(excerpt,'')), 'B') ||
    setweight(to_tsvector('english', coalesce(content_html,'')), 'C')
  ) STORED;

CREATE INDEX idx_posts_content_tsv ON posts USING GIN (content_tsv);
```

Query with `websearch_to_tsquery('english', :query)` and rank with `ts_rank`. Add `pg_trgm` extension + trigram index on `title` for typo-tolerant fuzzy matching:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_posts_title_trgm ON posts USING GIN (title gin_trgm_ops);
```

**Phase 2 (if needed later):** stand up **Meilisearch** as a separate container once article count or query volume outgrows Postgres FTS comfort — sync via a post-publish webhook. Not needed at current scale (~1,900 posts).

---

## 9. Media & File Storage

- Upload target: **Cloudflare R2** (S3-compatible API, zero egress fees)
- Pipeline on upload: receive multipart file → validate type/size → resize (max width 1920px) → convert to **WebP** (via `Pillow`) → generate a simple auto alt-text stub from filename/context (real alt-text still editable by the writer) → upload to R2 → save `media` row → return public URL
- Downloadable `.xlsx` / `.csv` templates are stored as-is (no conversion), same bucket, different prefix (`/downloads/`)

```python
# app/utils/image_processing.py — sketch
from PIL import Image
import io

def to_webp(file_bytes: bytes, max_width: int = 1920, quality: int = 82) -> bytes:
    img = Image.open(io.BytesIO(file_bytes))
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)))
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=quality)
    return out.getvalue()
```

---

## 10. Background Jobs

Using **APScheduler** in-process (simplest reliable option for a single-VPS deployment — avoids running Celery + a broker for this scale).

| Job | Schedule | Purpose |
|---|---|---|
| `flush_view_counts` | Every 60s | Moves Redis view counters into `posts.view_count` + inserts `post_views` rows |
| `publish_scheduled_posts` | Every minute | Flips `scheduled` posts with `scheduled_at <= now()` to `published`, sets `published_at`, triggers sitemap regen + cache invalidation |
| `calculate_trending` | Every 30 min | Recomputes "Last 7 Days" velocity ranking → updates `is_trending` flags → refreshes `posts:trending` cache |
| `regenerate_sitemap` | On publish event + every 6h as fallback | Rebuilds `sitemap.xml`, invalidates CDN cache |
| `cleanup_expired_tokens` | Daily | Deletes expired/revoked rows from `refresh_tokens` |
| `sync_newsletter_subscribers` | Every 15 min | Pushes new `newsletter_subscribers` rows to Mailchimp/ConvertKit API |
| `prune_old_post_views` | Weekly | Deletes raw `post_views` rows older than 90 days (aggregate stats already rolled up) |

```python
# app/jobs/scheduler.py — sketch
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

def start_scheduler():
    scheduler.add_job(flush_view_counts, "interval", seconds=60)
    scheduler.add_job(calculate_trending, "interval", minutes=30)
    scheduler.add_job(cleanup_expired_tokens, "cron", hour=3)
    scheduler.start()
```

---

## 11. WordPress Migration (ETL)

Three-stage script under `scripts/`, run once (with a dry-run mode) before cutover.

### 11.1 Extract (`wp_extract.py`)
- Pull via WordPress REST API (`/wp-json/wp/v2/posts`, `/categories`, `/tags`, `/media`, `/comments`) with pagination, or via `WP-CLI export` if the REST API is rate-limited/incomplete
- Save raw JSON dumps locally first (so re-runs don't hit WP again)

### 11.2 Transform (`wp_transform.py`)
- Map WP post → new `posts` schema: convert WP's block/HTML content into `content_json` (best-effort block mapping; flag anything that needs manual cleanup in the new editor)
- Preserve original `slug` exactly — **this is what makes the redirect table trivial** (old path == new path in most cases, so most redirects are 1:1 and only category/permalink-structure differences need explicit rows)
- Map WP categories/tags → new `categories`/`tags`, preserving hierarchy
- Map WP comments → new `comments`, preserving `parent_id` nesting
- Build the `redirects` table entries for any slug/path that *does* change

### 11.3 Load (`wp_load.py`)
- Bulk insert in batches (500 rows/transaction) inside a single DB transaction per batch — rollback-safe
- Re-run WordPress's `content_json` field for any specialty page (e.g. `/excel-pro-tips/matrix/transpose/`) through a validation pass before publish so formula blocks render correctly in the new editor

### 11.4 Verification Checklist
- [ ] Row counts match (WP post count == migrated post count)
- [ ] Spot-check 20 random posts render identically
- [ ] Every old URL returns either 200 (same path) or 301 (via `redirects` table) — no 404s
- [ ] Category tree structure matches
- [ ] Comment threads preserved

---

## 12. Configuration & Environment Variables

```bash
# .env.example

# App
ENVIRONMENT=production
SECRET_KEY=                          # for JWT signing, generate with `openssl rand -hex 32`
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=30
ALGORITHM=HS256

# Bootstrap admin (used once on first boot; skipped if a super admin exists)
FIRST_ADMIN_EMAIL=
FIRST_ADMIN_PASSWORD=

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/excel_insider
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20

# Redis
REDIS_URL=redis://localhost:6379/0

# Object Storage (Cloudflare R2)
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=excel-insider-media
R2_PUBLIC_URL=https://media.excelinsider.com

# CORS
ALLOWED_ORIGINS=https://excelinsider.com,https://www.excelinsider.com

# Newsletter
MAILCHIMP_API_KEY=
MAILCHIMP_LIST_ID=

# Rate limiting
RATE_LIMIT_LOGIN=5/15minutes
RATE_LIMIT_COMMENT=3/10minutes
RATE_LIMIT_NEWSLETTER=5/1hour

# Transactional email (Resend)
RESEND_API_KEY=
EMAIL_FROM="Excel Insider <no-reply@excelinsider.com>"

# Links
FRONTEND_URL=https://excelinsider.com
```

Load via `pydantic-settings` (`BaseSettings` subclass in `core/config.py`) — never read `os.environ` directly elsewhere in the app.

---

## 13. Error Handling Standard

All errors return a consistent shape via a global exception handler:

```json
{
  "error": {
    "code": "POST_NOT_FOUND",
    "message": "No post found with the given slug.",
    "status": 404
  }
}
```

```python
# app/core/exceptions.py — sketch
class AppException(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code

# Register a single handler in main.py that catches AppException
# and formats the response above — keeps every router's error handling consistent.
```

Use specific subclasses: `NotFoundException`, `PermissionDeniedException`, `ValidationException`, `ConflictException` (e.g. duplicate slug).

Also register a handler for FastAPI's `RequestValidationError` (422) that maps Pydantic errors into the same envelope — otherwise body-validation errors leak FastAPI's default `detail` array shape and break the contract.

---

## 14. Security Checklist

- [ ] **Rate limiting** on `/auth/login`, `/comments`, `/newsletter/subscribe` via Redis (sliding window or token bucket)
- [ ] **CORS** locked to the known Next.js frontend origin(s) only — no wildcard `*` in production
- [ ] **SQL injection** — non-issue by default since SQLAlchemy parameterizes all queries; never use raw string-interpolated SQL
- [ ] **XSS** — sanitize any HTML rendered from `content_json` → `content_html` on the server (use `nh3`, the maintained Rust successor to the archived `bleach`) before storing/serving
- [ ] **Input validation** — every request body validated via Pydantic schemas; reject unknown fields (`model_config = ConfigDict(extra="forbid")`)
- [ ] **File upload validation** — check MIME type + magic bytes (not just extension) before processing uploads; cap file size (e.g. 10MB images, 25MB downloadable assets)
- [ ] **Secrets** — never commit `.env`; inject via Dokploy's environment variables
- [ ] **HTTPS only** — terminated by Dokploy's Traefik; add an HSTS header in app middleware
- [ ] **Refresh token rotation** — every refresh issues a new token and revokes the old one (limits replay window)
- [ ] **Audit logging** — every publish/delete/role-change action recorded in `audit_logs`
- [ ] **Dependency scanning** — run `pip-audit` in CI before each deploy

---

## 15. Testing Strategy

- **Framework:** `pytest` + `pytest-asyncio` + `httpx.AsyncClient` for API tests
- **Test DB:** separate Postgres database (or Testcontainers), reset between test modules via transaction rollback
- **Coverage priorities (in order):**
  1. Auth flows (login, refresh, logout, expired/invalid tokens)
  2. RBAC — every protected endpoint tested against all 4 roles (expect 200 vs 403 correctly)
  3. Post lifecycle (draft → pending_review → published, and the writer-can't-publish-directly rule)
  4. Slug uniqueness / conflict handling
  5. Redirect table lookups (migration correctness)
- **Fixtures:** `conftest.py` provides a seeded test user per role, a sample category tree, and a sample post — reused across test files

```python
# tests/conftest.py — sketch
@pytest.fixture
async def writer_client(test_app, seed_users):
    token = create_access_token(seed_users["writer"])
    transport = httpx.ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.headers["Authorization"] = f"Bearer {token}"
        yield c
```

---

## 16. Deployment (Dokploy)

```
Internet → Dokploy (Traefik: TLS, routing, redeploys) → app container (Uvicorn)
                                                        ↘ PostgreSQL (Dokploy database service)
                                                        ↘ Redis (Dokploy database service)
```

### 16.1 App service

- Service type: **Docker**, built from the repo-root `Dockerfile` — the entrypoint runs `alembic upgrade head`, then the idempotent super-admin seed (`python -m app.core.seed`), then starts `uvicorn app.main:app --host 0.0.0.0 --port 8000`, so every deploy applies pending migrations before serving traffic
- No Nginx config of our own: Dokploy's built-in Traefik handles reverse proxy, Let's Encrypt TLS, and automatic redeploys on git push
- Health check path `/health` (must check DB **and** Redis — see §16.4); Dokploy restarts unhealthy containers
- Set Dokploy's max request body size to 25M for media uploads
- Enable the HSTS header as app-level middleware, since TLS is terminated upstream at Traefik
- All secrets (`DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `SMTP_*`, R2 keys) are set as environment variables in the Dokploy panel — never baked into the image

### 16.2 Databases

- PostgreSQL 16 and Redis 7 run as **separate Dokploy database services**, not inside the app container
- `DATABASE_URL` / `REDIS_URL` point at the internal Dokploy service hostnames
- Dokploy's scheduled database backups target S3-compatible storage — point them at a dedicated **Cloudflare R2 bucket**, separate from the media bucket

### 16.3 Backups

- Dokploy automated Postgres backups → R2, retain 14 daily + 6 monthly
- Media already live in R2; enable R2 object versioning as the media-side safety net

### 16.4 Monitoring

- `/health` checks DB **and** Redis connectivity, wired to an uptime monitor (UptimeRobot or similar)
- Structured JSON logging (via `structlog` or similar) so the "no error log" problem can't recur — logs to stdout, captured and viewable in the Dokploy dashboard

---

## 17. Build Order / Milestones

Matches the client-facing timeline's Week 1–2, but broken into daily-buildable chunks for AI-assisted development:

1. **Scaffold** — project structure, `config.py`, `database.py`, Docker Compose (Postgres + Redis running locally)
2. **Models + Alembic** — all tables from §4, first migration, verify schema in a DB client
3. **Auth module** — register (Super Admin auto-seeds from `FIRST_ADMIN_*` env on first boot), login, refresh, logout, forgot/reset/change password (email via SMTP), email verification, `get_current_user` dependency, `require_role()` dependency
4. **Categories module** — full CRUD + reorder endpoint + tree-building query + Redis caching
5. **Posts module (core)** — CRUD, status transitions, slug generation/uniqueness, RBAC per §5.3
6. **Tags + Comments modules**
7. **Media module** — upload pipeline, R2 integration, WebP conversion
8. **Search** — Postgres FTS setup + `/search` endpoint
9. **Analytics** — view counting via Redis buffer + flush job, trending job
10. **SEO/system endpoints** — sitemap.xml generation, redirects lookup
11. **Newsletter module**
12. **WordPress ETL scripts** — extract → transform → load → verification checklist
13. **Hardening pass** — rate limiting, audit logging, security checklist review, test suite to green
14. **Deploy** — push to Git, Dokploy builds the root `Dockerfile`, Postgres + Redis as Dokploy database services, backups configured, `/health` wired to uptime monitor

---

*End of specification. Feed each numbered section to your AI coding assistant one at a time for best results — trying to generate the entire backend from one giant prompt tends to produce shallower code than working section-by-section against this spec.*
