#!/bin/sh
# Start the NIST Orleans silo (localhost) in the background, wait for its gateway,
# then serve the API. Everything runs on localhost inside this one container, which
# is what the NIST engine expects (the silo advertises 127.0.0.1). The GenVal runner
# is spawned on demand by the backend and connects to the silo over localhost.
set -e

echo "entrypoint: starting Orleans silo..."
( cd /engine/orleans-server && dotnet NIST.CVP.ACVTS.Orleans.ServerHost.dll --console ) &

echo "entrypoint: waiting for Orleans gateway on 127.0.0.1:30000..."
python3 - <<'PY'
import socket, sys, time
for _ in range(90):
    try:
        socket.create_connection(("127.0.0.1", 30000), 2).close()
        print("entrypoint: Orleans gateway ready", flush=True)
        sys.exit(0)
    except OSError:
        time.sleep(2)
print("entrypoint: Orleans gateway not ready after wait — starting API anyway", flush=True)
PY

# When a database is configured the app uses the DbStore, and seed_demo_metadata()
# writes at import — so the schema must exist first. Wait for Postgres, then migrate.
# (No compose `depends_on` is used, so the CI mTLS job's `up backend proxy` never
# pulls in Postgres; this wait handles ordering on a full `docker compose up`.)
if [ -n "$DATABASE_URL" ]; then
  echo "entrypoint: waiting for the database..."
  python3 - <<'PY'
import os, sys, time
import psycopg
url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
for _ in range(60):
    try:
        psycopg.connect(url, connect_timeout=2).close()
        print("entrypoint: database ready", flush=True)
        sys.exit(0)
    except Exception:
        time.sleep(2)
print("entrypoint: database not ready after wait", flush=True)
sys.exit(1)
PY
  echo "entrypoint: applying migrations (alembic upgrade head)..."
  alembic upgrade head
fi

echo "entrypoint: starting API (uvicorn)..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
