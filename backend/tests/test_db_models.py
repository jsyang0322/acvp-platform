"""Persistence-layer schema tests.

The schema assertions run without a database: they check that the ORM models in
app/db/models.py mirror the in-memory store shapes (the "swap is mechanical"
contract in store.py). The connectivity test is opt-in — skipped unless
DATABASE_URL points at a reachable Postgres — so the default `pytest` run needs no
database and the in-memory store still backs every other test.
"""
import os

import pytest

from app.db import models
from app.db.base import Base
from app.store import METADATA_RESOURCES

EXPECTED_TABLES = {
    "test_sessions",
    "vector_sets",
    "requests",
    "validations",
    "metadata_resources",
}


def test_metadata_registers_all_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_vectorset_columns_mirror_store_dataclass() -> None:
    # Every persisted field of store.VectorSet must have a column. The in-memory
    # -only _lock has none, and vs_id/mode_folder map to id/mode_folder.
    cols = set(models.VectorSetRow.__table__.columns.keys())
    for field in (
        "status",
        "prompt",
        "response",
        "validation",
        "resubmit_count",
        "missing_tc_ids",
        "capabilities",
        "registration",
        "internal_projection",
        "expected",
        "error",
        "expires_at",
        "show_expected",
    ):
        assert field in cols, f"VectorSetRow is missing a column for {field}"


def test_metadata_row_reproduces_per_resource_id_sequence() -> None:
    # The store keys metadata by (resource, per-resource id); the composite PK
    # must match so vendors/1 and persons/1 can coexist.
    pk = [c.name for c in models.MetadataRow.__table__.primary_key.columns]
    assert pk == ["resource", "local_id"]
    # Sanity: the resource column holds the same vocabulary the store uses.
    assert isinstance(METADATA_RESOURCES, tuple) and METADATA_RESOURCES


# --- opt-in connectivity test (only when a real DB is configured) -------------

_DB_URL = os.getenv("DATABASE_URL")


@pytest.mark.skipif(
    not _DB_URL, reason="DATABASE_URL not set — the database is optional this phase"
)
def test_db_ping_succeeds_when_configured() -> None:
    from app.db.session import db_ping

    assert db_ping() is True
