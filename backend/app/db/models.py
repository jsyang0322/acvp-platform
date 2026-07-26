"""ORM models — the persistent shape of app/store.py.

These mirror the in-memory dataclasses (TestSession, VectorSet) and dict records
(requests, validations, metadata) one-for-one, so swapping the in-memory Store for
a DB-backed one is mechanical (see store.py's module docstring). This phase only
defines the schema; the running app still uses the in-memory store.

JSON payloads (prompt, response, internalProjection, ...) are stored as JSONB on
PostgreSQL and generic JSON elsewhere. The threading.Lock that guards
VectorSet.settle() in memory has no column here — the DB-backed store will use a
row-level lock / transaction for the same concurrent-cancel guard.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# JSONB on PostgreSQL, generic JSON on any other backend so the models stay
# importable (and unit-testable) without a Postgres connection.
_JSON = JSON().with_variant(JSONB(), "postgresql")


class TestSessionRow(Base):
    """Mirrors store.TestSession. `id` reproduces the in-memory session_id sequence."""

    __tablename__ = "test_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    encrypt_at_rest: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    publishable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_on: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_on: Mapped[str | None] = mapped_column(String, nullable=True)
    # One-way hash of the session accessToken (core.auth.hash_access_token), not the
    # raw credential — a DB compromise must not yield a live session token. The token
    # is verified from its JWT signature, never by looking this up. [HUMAN REVIEW]
    access_token: Mapped[str | None] = mapped_column(String, nullable=True)
    # JWT subject that created the session; scopes the listing (spec 12.16).
    owner: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    vector_sets: Mapped[list[VectorSetRow]] = relationship(
        back_populates="test_session",
        cascade="all, delete-orphan",
        order_by="VectorSetRow.id",
    )


class VectorSetRow(Base):
    """Mirrors store.VectorSet. The lifecycle `status` string and the JSON payloads
    match the dataclass fields exactly."""

    __tablename__ = "vector_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_session_id: Mapped[int] = mapped_column(
        ForeignKey("test_sessions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    mode_folder: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="generating", nullable=False)
    prompt: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    response: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    validation: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    resubmit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missing_tc_ids: Mapped[list | None] = mapped_column(_JSON, default=list, nullable=True)
    capabilities: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    registration: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    # The answer key from generation; NIST validate needs it (not just the prompt).
    internal_projection: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    # expectedResults, disclosed only for isSample sessions (spec 12.17.5.1).
    expected: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Submission deadline (spec 14). Stored as a real timestamp — the in-memory
    # store keeps this as an aware datetime, unlike the created_on/expires_on strings.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    show_expected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    test_session: Mapped[TestSessionRow] = relationship(back_populates="vector_sets")


class RequestRow(Base):
    """Mirrors a store._requests entry: {status, location, owner}. Backs the
    request-retry polling resource (spec 12.7)."""

    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String, default="processing", nullable=False)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    owner: Mapped[str | None] = mapped_column(String, index=True, nullable=True)


class ValidationRow(Base):
    """Mirrors a store._validations entry: the certificate resource produced by
    certification (spec 12.16.4.1)."""

    __tablename__ = "validations"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    created_on: Mapped[str] = mapped_column(String, nullable=False)
    certify: Mapped[dict] = mapped_column(_JSON, nullable=False)


class MetadataRow(Base):
    """One row per metadata resource object (spec 12.8-12.13).

    `resource` is one of store.METADATA_RESOURCES; `local_id` reproduces the
    in-memory store's per-resource id sequence, where vendors/1 and persons/1
    coexist — so the identity is the composite (resource, local_id)."""

    __tablename__ = "metadata_resources"

    resource: Mapped[str] = mapped_column(String, primary_key=True)
    local_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(_JSON, nullable=False)
