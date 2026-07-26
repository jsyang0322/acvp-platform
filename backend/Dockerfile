# syntax=docker/dockerfile:1
#
# Single-container image: FastAPI backend + the NIST GenVal engine (Orleans silo
# + runner), all on localhost. This matches how the NIST engine is designed to
# run — OrleansSiloHost hardcodes AdvertisedIPAddress = Loopback, so the silo is
# only reachable on localhost. The backend shells out to the GenVal runner, which
# reaches the silo over localhost inside this container.
#
# The NIST binaries come from backend/nist-bin/ (a portable, framework-dependent
# `dotnet publish` produced by scripts/nist/build-genval.sh — run that first).

# --- base: ASP.NET 8 runtime + the prebuilt NIST engine ------------------------
FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS engine
WORKDIR /engine
COPY nist-bin/ /engine/
# Move the Orleans silo port off 8000 so it doesn't collide with the API (uvicorn).
RUN sed -i 's/"OrleansSiloPort":[[:space:]]*8000/"OrleansSiloPort": 11111/' \
      /engine/orleans-server/sharedappsettings.json \
      /engine/genval-runner/sharedappsettings.json

# --- FastAPI backend + Orleans silo (started by the entrypoint) ----------------
FROM engine AS backend
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip3 install --no-cache-dir --break-system-packages \
    fastapi "uvicorn[standard]" pydantic pydantic-settings pyjwt arq \
    sqlalchemy "psycopg[binary]" alembic
COPY app ./app
# Alembic migrations — the entrypoint runs `alembic upgrade head` when DATABASE_URL
# is set, before the app (and its import-time seed) starts.
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY docker/backend-entrypoint.sh /usr/local/bin/backend-entrypoint.sh
RUN chmod +x /usr/local/bin/backend-entrypoint.sh
ENV USE_NIST_GENVAL=true \
    GENVAL_RUNNER_DLL=/engine/genval-runner/NIST.CVP.ACVTS.Generation.GenValApp.dll \
    GENVAL_ARTIFACT_ROOT=/tmp/acvp-sessions
EXPOSE 8000
CMD ["/usr/local/bin/backend-entrypoint.sh"]
