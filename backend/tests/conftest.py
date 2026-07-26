import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # put backend/ on sys.path

# Postgres-backed run: with DATABASE_URL set the app selects the DbStore, so give it
# a clean schema BEFORE importing app (seed_demo_metadata runs at import time). Drop
# + recreate mirrors the in-memory store's fresh-per-process state; tables then
# accumulate across the run, exactly as the in-memory singleton does. Default runs
# (no DATABASE_URL) use the in-memory store and touch no database.
if os.getenv("DATABASE_URL"):
    from sqlalchemy import create_engine  # noqa: E402

    from app.db import models  # noqa: E402,F401  (register tables on Base.metadata)
    from app.db.base import Base  # noqa: E402

    _engine = create_engine(os.environ["DATABASE_URL"])
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    _engine.dispose()

from app.main import app  # noqa: E402
from app.core.config import get_settings  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def acv_version() -> str:
    return get_settings().acv_version


@pytest.fixture
def auth_header(client) -> dict:
    s = get_settings()
    body = [{"acvVersion": s.acv_version}, {"password": s.demo_password}]
    token = client.post("/acvp/v1/login", json=body).json()[1]["accessToken"]
    return {"Authorization": f"Bearer {token}"}
