# Excel Insider Backend

REST API for Excel Insider, a headless setup where a Next.js frontend talks to this
API instead of WordPress. Serves posts, categories, tags, comments and media, and
keeps URLs stable after the WordPress migration.

## Stack

- Python 3.14, FastAPI, Pydantic v2
- PostgreSQL 16 with SQLAlchemy 2.0 async + Alembic
- Redis 7 for cache, rate limiting and token blacklist
- JWT auth with refresh token rotation, Argon2 password hashing
- Cloudflare R2 for media storage

## Getting Started

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `SECRET_KEY` (generate with `openssl rand -hex 32`) and adjust the
database URL if needed.

Start PostgreSQL and Redis:

```bash
docker compose up -d
```

Run the API:

```bash
uvicorn app.main:app --reload
```

Interactive docs live at http://127.0.0.1:8000/docs, health check at /health.

## Migrations

```bash
alembic revision --autogenerate -m "create users table"
alembic upgrade head
python -m app.core.seed
```

The seed creates the first Super Admin from `FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD` and is skipped if one already exists. In production the container entrypoint runs all three automatically on every deploy.

## Tests

```bash
pytest
```

## Deployment

Built as a single container from the root Dockerfile, deployed on Dokploy.
PostgreSQL and Redis run as separate Dokploy services; connection strings come
from environment variables set in the Dokploy panel.
