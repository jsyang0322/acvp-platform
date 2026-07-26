"""DB-backed store — a write-through mirror of the in-memory Store.

Selected by app.store when DATABASE_URL is set. It returns the same TestSession /
VectorSet *surface* the in-memory store does, but backed by PostgreSQL: reading an
attribute queries the row, writing one UPDATEs and commits it. So every call site
that mutates a returned object in place — routers AND tests (e.g. a test forcing
`vs.expires_at` to the past, or `vs.status`) — behaves exactly as in memory. The DB
is the shared state now, just as the module singleton was, so the swap is mechanical.

Concurrency: settle()/cancel() take a row lock (SELECT ... FOR UPDATE) and re-check
the terminal state, reproducing VectorSet.settle's lock discipline — a generate or
validate thread that finishes after a cancel cannot undo it (see test_cancel).

[HUMAN REVIEW] persistence of session access tokens and validation payloads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.db.models import (
    MetadataRow,
    RequestRow,
    TestSessionRow,
    ValidationRow,
    VectorSetRow,
)
from app.db.session import get_sessionmaker


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(status: str, expires_at: datetime | None) -> bool:
    """Mirror of VectorSet.expired(): clock-derived, with an explicit override."""
    if status == "expired":
        return True
    if expires_at is None:
        return False
    return _now() >= expires_at


class _VectorSetProxy:
    """Write-through handle to one vector_sets row. Reads query the row; writes
    UPDATE-and-commit, so a change is visible to the next request immediately."""

    __slots__ = ("_id",)
    _COLS = frozenset(
        {
            "mode_folder",
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
        }
    )

    def __init__(self, vs_id: int) -> None:
        object.__setattr__(self, "_id", vs_id)

    # Identity mirrors the dataclass field name (call sites read vs.vs_id).
    @property
    def vs_id(self) -> int:
        return self._id

    def __getattr__(self, name: str) -> Any:
        if name in _VectorSetProxy._COLS:
            with get_sessionmaker()() as s:
                row = s.get(VectorSetRow, self._id)
                return getattr(row, name) if row is not None else None
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _VectorSetProxy._COLS:
            with get_sessionmaker()() as s:
                row = s.get(VectorSetRow, self._id)
                if row is not None:
                    setattr(row, name, value)
                    s.commit()
            return
        object.__setattr__(self, name, value)

    # --- lifecycle helpers (mirror store.VectorSet) --------------------------

    def settle(self, **updates: Any) -> bool:
        """Apply a background task's result unless the set is already terminal.

        Takes a row lock and re-checks cancelled/expired, so a generate/validate
        thread cannot land a result after a cancel or the deadline.
        """
        with get_sessionmaker()() as s:
            row = s.get(VectorSetRow, self._id, with_for_update=True)
            if row is None:
                return False
            if row.status == "cancelled" or _is_expired(row.status, row.expires_at):
                return False
            for name, value in updates.items():
                setattr(row, name, value)
            s.commit()
            return True

    def cancel(self) -> None:
        """Terminal: the client withdrew this vector set. Row-locked so it wins any
        in-flight settle (see test_cancel)."""
        with get_sessionmaker()() as s:
            row = s.get(VectorSetRow, self._id, with_for_update=True)
            if row is not None:
                row.status = "cancelled"
                s.commit()

    def expired(self) -> bool:
        with get_sessionmaker()() as s:
            row = s.get(VectorSetRow, self._id)
            return False if row is None else _is_expired(row.status, row.expires_at)

    def disposition(self) -> str:
        # Imported lazily: app.store finishes importing this module before its own
        # DISPOSITIONS is referenced at request time (avoids an import cycle).
        from app.store import DISPOSITIONS

        with get_sessionmaker()() as s:
            row = s.get(VectorSetRow, self._id)
            if row is None:
                return "unreceived"
            if row.status == "expired":
                return "expired"
            if row.status == "error":
                return "error"
            if row.response is None and _is_expired(row.status, row.expires_at):
                return "expired"
            if row.missing_tc_ids:
                return "missing"
            if row.validation is not None:
                disposition = row.validation.get("disposition", "error")
                return disposition if disposition in DISPOSITIONS else "error"
            if row.status == "response_submitted":
                return "incomplete"
            return "unreceived"


class _TestSessionProxy:
    """Write-through handle to one test_sessions row (see _VectorSetProxy)."""

    __slots__ = ("_id",)
    _COLS = frozenset(
        {
            "passed",
            "is_sample",
            "encrypt_at_rest",
            "publishable",
            "created_on",
            "expires_on",
            "access_token",
            "owner",
            "cancelled",
        }
    )

    def __init__(self, sid: int) -> None:
        object.__setattr__(self, "_id", sid)

    @property
    def session_id(self) -> int:
        return self._id

    def __getattr__(self, name: str) -> Any:
        if name in _TestSessionProxy._COLS:
            with get_sessionmaker()() as s:
                row = s.get(TestSessionRow, self._id)
                return getattr(row, name) if row is not None else None
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _TestSessionProxy._COLS:
            with get_sessionmaker()() as s:
                row = s.get(TestSessionRow, self._id)
                if row is not None:
                    setattr(row, name, value)
                    s.commit()
            return
        object.__setattr__(self, name, value)

    @property
    def vector_sets(self) -> list[_VectorSetProxy]:
        with get_sessionmaker()() as s:
            ids = (
                s.execute(
                    select(VectorSetRow.id)
                    .where(VectorSetRow.test_session_id == self._id)
                    .order_by(VectorSetRow.id)
                )
                .scalars()
                .all()
            )
        return [_VectorSetProxy(i) for i in ids]

    @property
    def active_vector_sets(self) -> list[_VectorSetProxy]:
        return [v for v in self.vector_sets if v.status != "cancelled"]

    @property
    def has_cancelled_vector_sets(self) -> bool:
        return any(v.status == "cancelled" for v in self.vector_sets)


class DbStore:
    """PostgreSQL-backed implementation of the in-memory Store's interface."""

    # --- test sessions -------------------------------------------------------

    def create_session(self) -> _TestSessionProxy:
        with get_sessionmaker()() as s:
            row = TestSessionRow()
            s.add(row)
            s.commit()
            return _TestSessionProxy(row.id)

    def get_session(
        self, sid: int, *, include_cancelled: bool = False
    ) -> _TestSessionProxy | None:
        with get_sessionmaker()() as s:
            row = s.get(TestSessionRow, sid)
            if row is None or (row.cancelled and not include_cancelled):
                return None
            return _TestSessionProxy(sid)

    def list_sessions(self, owner: str | None = None) -> list[_TestSessionProxy]:
        with get_sessionmaker()() as s:
            q = select(TestSessionRow.id).where(TestSessionRow.cancelled.is_(False))
            if owner is not None:
                q = q.where(TestSessionRow.owner == owner)
            ids = s.execute(q.order_by(TestSessionRow.id.desc())).scalars().all()
        return [_TestSessionProxy(i) for i in ids]

    def add_vector_set(self, session: Any, mode_folder: str) -> _VectorSetProxy:
        with get_sessionmaker()() as s:
            row = VectorSetRow(test_session_id=session.session_id, mode_folder=mode_folder)
            s.add(row)
            s.commit()
            return _VectorSetProxy(row.id)

    def get_vector_set(
        self, session: Any, vs_id: int, *, include_cancelled: bool = False
    ) -> _VectorSetProxy | None:
        with get_sessionmaker()() as s:
            row = s.get(VectorSetRow, vs_id)
            if row is None or row.test_session_id != session.session_id:
                return None
            if row.status == "cancelled" and not include_cancelled:
                return None
            return _VectorSetProxy(vs_id)

    # --- requests ------------------------------------------------------------

    def new_request(self, owner: str | None = None) -> int:
        with get_sessionmaker()() as s:
            row = RequestRow(status="processing", location=None, owner=owner)
            s.add(row)
            s.commit()
            return row.id

    def get_request(self, rid: int) -> dict | None:
        with get_sessionmaker()() as s:
            row = s.get(RequestRow, rid)
            if row is None:
                return None
            return {"status": row.status, "location": row.location, "owner": row.owner}

    def list_requests(self, owner: str | None = None) -> list[tuple[int, dict]]:
        with get_sessionmaker()() as s:
            q = select(RequestRow)
            if owner is not None:
                q = q.where(RequestRow.owner == owner)
            rows = s.execute(q.order_by(RequestRow.id.desc())).scalars().all()
            return [
                (r.id, {"status": r.status, "location": r.location, "owner": r.owner})
                for r in rows
            ]

    def complete_request(self, rid: int, location: str) -> None:
        with get_sessionmaker()() as s:
            row = s.get(RequestRow, rid)
            if row is not None:
                row.status = "approved"
                row.location = location
                s.commit()

    # --- metadata resources --------------------------------------------------

    def add_metadata(self, resource: str, obj: dict) -> int:
        with get_sessionmaker()() as s:
            # Per-resource id sequence (vendors/1 and persons/1 coexist), matching
            # the in-memory store's itertools.count per resource.
            next_id = s.execute(
                select(func.coalesce(func.max(MetadataRow.local_id), 0) + 1).where(
                    MetadataRow.resource == resource
                )
            ).scalar_one()
            s.add(MetadataRow(resource=resource, local_id=next_id, data=obj))
            s.commit()
            return next_id

    def get_metadata(self, resource: str, rid: int) -> dict | None:
        with get_sessionmaker()() as s:
            row = s.get(MetadataRow, (resource, rid))
            return dict(row.data) if row is not None else None

    def list_metadata(self, resource: str) -> list[tuple[int, dict]]:
        with get_sessionmaker()() as s:
            rows = (
                s.execute(
                    select(MetadataRow)
                    .where(MetadataRow.resource == resource)
                    .order_by(MetadataRow.local_id)
                )
                .scalars()
                .all()
            )
            return [(r.local_id, dict(r.data)) for r in rows]

    def replace_metadata(self, resource: str, rid: int, obj: dict) -> bool:
        with get_sessionmaker()() as s:
            row = s.get(MetadataRow, (resource, rid))
            if row is None:
                return False
            row.data = obj
            s.commit()
            return True

    def delete_metadata(self, resource: str, rid: int) -> bool:
        with get_sessionmaker()() as s:
            row = s.get(MetadataRow, (resource, rid))
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True

    # --- validations ---------------------------------------------------------

    def add_validation(self, session_id: int, created_on: str, certify: dict) -> int:
        with get_sessionmaker()() as s:
            row = ValidationRow(
                session_id=session_id, created_on=created_on, certify=certify
            )
            s.add(row)
            s.commit()
            return row.id

    def get_validation(self, vid: int) -> dict | None:
        with get_sessionmaker()() as s:
            row = s.get(ValidationRow, vid)
            if row is None:
                return None
            return {
                "session_id": row.session_id,
                "created_on": row.created_on,
                "certify": dict(row.certify),
            }
