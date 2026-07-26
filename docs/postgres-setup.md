# PostgreSQL setup (containerized — Docker or Podman)

The persistent store runs **PostgreSQL in a container**, with data in a named
volume — never a local SQLite file and never scattered in the repo. This is the
first phase of the store migration: the schema, the engine/session layer, and the
migrations are in place, but the running app **still uses the in-memory store**
(`app/store.py`). Nothing here changes app behavior yet; it stands the database up
so the DB-backed store can plug in next.

Works the same under **Docker** (`docker compose`) and **Podman**
(`podman compose` / `podman-compose`). Where they differ, both are shown.

## What was added

- `db` service in `docker-compose.yml` (`postgres:16-alpine`) + named volume `pgdata`.
- SQLAlchemy 2.0 models mirroring the store shapes: `app/db/models.py`.
- Lazy engine / session factory + a `db_ping()` probe: `app/db/session.py`.
- Alembic migrations: `backend/alembic/`, config in `backend/alembic.ini`.
- `DATABASE_URL` / `DB_ECHO` settings: `app/core/config.py`.
- Readiness endpoint `GET /health/db` (200 up, 503 down/unconfigured).

## 1. Start Postgres

The `db` container publishes `127.0.0.1:5432` for host tooling (Alembic, `psql`, a
GUI). The backend reaches it over the internal compose network as host `db`.

Bring up **only** the database (the rest of the stack is unaffected):

```bash
# Docker
docker compose up -d --wait db

# Podman
podman compose up -d db          # or: podman-compose up -d db
```

> Compose interpolates the whole file, so the backend service's required secrets
> (`JWT_SECRET`, `PROXY_SECRET`) must be non-empty even when starting only `db`.
> Put them in a repo-root `.env` (copy from `.env.example`), or prefix the command:
> `PROXY_SECRET=x JWT_SECRET=x docker compose up -d --wait db`.

Credentials come from `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`
(dev-only defaults `acvp` / `acvp-dev-only` / `acvp`). Override them in the
repo-root `.env` for anything shared or deployed.

**Podman image note:** if your Podman has no default registry, qualify the image as
`docker.io/library/postgres:16-alpine` (in the compose file or your registries
config).

## 2. Apply the schema (Alembic migrations)

Run from `backend/` with `DATABASE_URL` pointing at the published port (host =
`localhost`):

```bash
cd backend
source .venv/bin/activate            # or: uv venv && source .venv/bin/activate
pip install -e .                     # brings in sqlalchemy, psycopg, alembic

export DATABASE_URL='postgresql+psycopg://acvp:acvp-dev-only@localhost:5432/acvp'
alembic upgrade head                 # create all tables
```

Useful checks:

```bash
alembic current                      # applied revision
alembic check                        # models vs. DB — clean = no drift
psql "$DATABASE_URL" -c '\dt'        # list tables
```

## 3. Verify connectivity

```bash
# API probe (start the backend first: uvicorn app.main:app --reload)
curl -i http://localhost:8000/health/db      # 200 {"database": true} when reachable

# or the pytest connectivity test (skipped when DATABASE_URL is unset)
DATABASE_URL="$DATABASE_URL" pytest tests/test_db_models.py -q
```

## Data lifecycle

- `docker compose down` — stops containers, **keeps** `pgdata` (data survives).
- `docker compose down -v` — also removes `pgdata` (**deletes all data**).
- Inspect: `docker volume ls | grep pgdata`.

## Configuration reference

| Setting | Env var | Default | Purpose |
| --- | --- | --- | --- |
| `database_url` | `DATABASE_URL` | `None` | SQLAlchemy URL (psycopg v3). `None` = in-memory store. |
| `db_echo` | `DB_ECHO` | `false` | Log SQL (debug only — may log data). |

The URL contains a password: keep it in `.env` (gitignored), never in the repo.
`DATABASE_URL` unset means the app runs on the in-memory store — the default this
phase, and what the test suite uses.

## Next phase

Swap the in-memory `Store` for a DB-backed implementation with the same method
surface (see the `store.py` module docstring), migrate the tests onto a
transactional session, and add `depends_on: db (service_healthy)` + a migration
step to the backend container. The models and migrations here are that target
schema.
