# PostgreSQL setup (containerized — Docker or Podman)

The persistent store runs **PostgreSQL in a container**, with data in a named
volume — never a local SQLite file and never scattered in the repo.

**Store selection is by `DATABASE_URL` (dual-store):** set it and the app uses the
PostgreSQL-backed `DbStore` (`app/db/db_store.py`); leave it unset and the app uses
the in-memory `Store` (`app/store.py`) — the default, so dev and the test suite run
with no database. The `DbStore` is a *write-through mirror* of the in-memory store:
it exposes the same `TestSession` / `VectorSet` surface, but every attribute read
queries the row and every write commits it, so all call sites behave identically on
both backends. `settle()`/`cancel()` take a `SELECT … FOR UPDATE` row lock, so a
generate/validate thread can't undo a cancel.

Works the same under **Docker** (`docker compose`) and **Podman**
(`podman compose` / `podman-compose`). Where they differ, both are shown.

## What was added

- `db` service in `docker-compose.yml` (`postgres:16-alpine`) + named volume `pgdata`.
- SQLAlchemy 2.0 models mirroring the store shapes: `app/db/models.py`.
- Write-through DB-backed store selected by `DATABASE_URL`: `app/db/db_store.py`.
- Lazy engine / session factory + a `db_ping()` probe: `app/db/session.py`.
- Alembic migrations: `backend/alembic/`, config in `backend/alembic.ini`.
- `DATABASE_URL` / `DB_ECHO` settings: `app/core/config.py`.
- Readiness endpoint `GET /health/db` (200 up, 503 down/unconfigured).
- The backend container's entrypoint waits for the DB and runs `alembic upgrade
  head` before serving (`backend/docker/backend-entrypoint.sh`).

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

## Running the tests against Postgres

The suite is the faithfulness bar: it must pass on **both** backends. Default runs
use the in-memory store; set `DATABASE_URL` (with the `db` container up) to run the
identical suite against PostgreSQL. `conftest.py` drops and recreates the schema
before the app imports, so each run starts clean.

```bash
# in-memory (fast, no infra) — the default
pytest -q

# against Postgres (needs the db container; DATABASE_URL points at the published port)
DB_HOST_PORT=55432 PROXY_SECRET=x JWT_SECRET=x docker compose up -d --wait db
DATABASE_URL='postgresql+psycopg://acvp:acvp-dev-only@localhost:55432/acvp' pytest -q
```

## Later work

- The write-through proxy issues one small query per attribute access — correct but
  chatty. Batch hot reads (e.g. `disposition()`) if it ever matters at scale.
- `test_sessions.access_token` is stored in the clear to mirror the in-memory store;
  hash or encrypt it before this is anything but a prototype ([HUMAN REVIEW]).
- Point the arq worker (deployment task queue) at the same `DATABASE_URL`.
