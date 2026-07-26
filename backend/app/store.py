"""In-memory state store for the prototype.

Replace with PostgreSQL + SQLAlchemy for deployment (see CLAUDE.md). The shapes
here intentionally mirror a persistent model so the swap is mechanical.
"""
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import count
from typing import Any

# ACVP disposition vocabulary (spec messaging). The crypto engine (NIST GenVal)
# emits passed/failed at the vector-set and per-test-case level; we synthesize the
# rest from lifecycle state. Any disposition the engine returns outside this set is
# treated as `error` rather than passed through blindly.
DISPOSITIONS = frozenset(
    {"passed", "failed", "incomplete", "unreceived", "missing", "expired", "error"}
)

# Metadata resources (spec 12.8-12.13). They share one shape — a collection of
# JSON objects addressed by /acvp/v1/{resource}/{id} — so one generic store and
# one generic router serve all five.
METADATA_RESOURCES = ("vendors", "persons", "modules", "oes", "dependencies")


@dataclass
class VectorSet:
    vs_id: int
    mode_folder: str          # e.g. "ML-KEM-keyGen-FIPS203"
    # generating: vectors not ready yet (GET returns retry) -> ready: prompt available ->
    # prompt_retrieved -> response_submitted -> disposition -> certified; expired is terminal.
    status: str = "generating"
    prompt: dict | None = None
    response: dict | None = None
    validation: dict | None = None
    resubmit_count: int = 0
    missing_tc_ids: list[int] = field(default_factory=list)
    capabilities: dict | None = None   # validated registration capabilities
    registration: dict | None = None   # NIST-shaped registration for generate()
    internal_projection: dict | None = None  # answer key from generation; NIST validate needs it
    expected: dict | None = None       # expectedResults, disclosed only for isSample
    error: str | None = None           # set when generation/validation raised
    expires_at: datetime | None = None  # submission deadline (spec 14)
    show_expected: bool = False        # client asked to see expected/provided (spec 12.17.5.1)
    # Serializes a background task's completion against a concurrent cancel.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def settle(self, **updates: Any) -> bool:
        """Apply a background task's result — unless this vector set is already done for.

        Generation and validation run on background threads (app.core.jobs) while the
        client keeps talking to us. It can cancel a vector set, or the deadline can
        pass, while that thread is still in flight. An unconditional `status = ...`
        write on completion then lands *after* the cancel and resurrects the vector
        set back into the session's listing — which spec 12.17.3 forbids.

        Returns False when the write was refused because the set is terminal.
        """
        with self._lock:
            if self.status == "cancelled" or self.expired():
                return False
            for name, value in updates.items():
                setattr(self, name, value)
            return True

    def cancel(self) -> None:
        """Terminal: the client withdrew this vector set (spec 12.17.3)."""
        with self._lock:
            self.status = "cancelled"

    def expired(self) -> bool:
        """True once the submission deadline has passed (spec 14).

        Derived from the clock rather than swept by a background job, so it is
        correct the moment it is asked. `status == "expired"` stays supported as
        an explicit terminal override.
        """
        if self.status == "expired":
            return True
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    def disposition(self) -> str:
        """Map lifecycle state to an ACVP disposition value (see DISPOSITIONS).

        We synthesize unreceived/incomplete/missing/expired/error from state;
        passed/failed come through from the crypto module's validation, normalized
        against the known vocabulary.
        """
        if self.status == "expired":
            return "expired"
        if self.status == "error":
            return "error"  # generation or validation failed (see self.error)
        if self.response is None and self.expired():
            # Spec: expired == the responses never arrived before the deadline. A
            # grade earned in time is not retroactively voided by the deadline.
            return "expired"
        if self.missing_tc_ids:
            return "missing"  # submission lacked some of the prompt's test cases
        if self.validation is not None:
            disposition = self.validation.get("disposition", "error")
            return disposition if disposition in DISPOSITIONS else "error"
        if self.status == "response_submitted":
            return "incomplete"  # responses received, validation in progress
        return "unreceived"      # no responses received yet


@dataclass
class TestSession:
    session_id: int
    vector_sets: list[VectorSet] = field(default_factory=list)
    passed: bool | None = None
    is_sample: bool = False
    encrypt_at_rest: bool = False
    publishable: bool = False
    created_on: str | None = None
    expires_on: str | None = None
    access_token: str | None = None
    owner: str | None = None    # JWT subject that created it; scopes the listing
    cancelled: bool = False     # spec 12.16.5

    @property
    def active_vector_sets(self) -> list[VectorSet]:
        """The vector sets still under test — cancelled ones are gone (spec 12.17.3)."""
        return [v for v in self.vector_sets if v.status != "cancelled"]

    @property
    def has_cancelled_vector_sets(self) -> bool:
        return any(v.status == "cancelled" for v in self.vector_sets)


class Store:
    def __init__(self) -> None:
        self._sessions: dict[int, TestSession] = {}
        self._session_ids = count(1)
        self._vs_ids = count(1)
        self._requests: dict[int, dict[str, Any]] = {}
        self._request_ids = count(1)
        self._validations: dict[int, dict[str, Any]] = {}
        self._validation_ids = count(1)
        self._metadata: dict[str, dict[int, dict]] = {r: {} for r in METADATA_RESOURCES}
        self._metadata_ids: dict[str, Any] = {r: count(1) for r in METADATA_RESOURCES}

    def create_session(self) -> TestSession:
        sid = next(self._session_ids)
        s = TestSession(session_id=sid)
        self._sessions[sid] = s
        return s

    def get_session(self, sid: int, *, include_cancelled: bool = False) -> TestSession | None:
        """Look up a session. Cancelled sessions read as absent (spec 12.16.5: further
        operations "may return 404"), so every endpoint gets that for free."""
        s = self._sessions.get(sid)
        if s is None or (s.cancelled and not include_cancelled):
            return None
        return s

    def list_sessions(self, owner: str | None = None) -> list[TestSession]:
        """Live sessions owned by `owner`, newest first (the first page is the
        one a client just created, which is the one they want)."""
        return [
            s for s in reversed(self._sessions.values())
            if not s.cancelled and (owner is None or s.owner == owner)
        ]

    def add_vector_set(self, session: TestSession, mode_folder: str) -> VectorSet:
        vs = VectorSet(vs_id=next(self._vs_ids), mode_folder=mode_folder)
        session.vector_sets.append(vs)
        return vs

    def get_vector_set(
        self, session: TestSession, vs_id: int, *, include_cancelled: bool = False
    ) -> VectorSet | None:
        """As with sessions, a cancelled vector set reads as absent (spec 12.17.3)."""
        vs = next((v for v in session.vector_sets if v.vs_id == vs_id), None)
        if vs is None or (vs.status == "cancelled" and not include_cancelled):
            return None
        return vs

    def new_request(self, owner: str | None = None) -> int:
        rid = next(self._request_ids)
        self._requests[rid] = {"status": "processing", "location": None, "owner": owner}
        return rid

    def get_request(self, rid: int) -> dict | None:
        return self._requests.get(rid)

    def list_requests(self, owner: str | None = None) -> list[tuple[int, dict]]:
        """The current user's requests, newest first (spec 12.7.1)."""
        return [
            (rid, req) for rid, req in reversed(self._requests.items())
            if owner is None or req.get("owner") == owner
        ]

    def complete_request(self, rid: int, location: str) -> None:
        # Update in place: overwriting the dict would drop the owner.
        self._requests[rid].update(status="approved", location=location)

    # --- metadata resources (spec 12.8-12.13) -----------------------------------

    def add_metadata(self, resource: str, obj: dict) -> int:
        rid = next(self._metadata_ids[resource])
        self._metadata[resource][rid] = obj
        return rid

    def get_metadata(self, resource: str, rid: int) -> dict | None:
        return self._metadata[resource].get(rid)

    def list_metadata(self, resource: str) -> list[tuple[int, dict]]:
        return list(self._metadata[resource].items())

    def replace_metadata(self, resource: str, rid: int, obj: dict) -> bool:
        if rid not in self._metadata[resource]:
            return False
        self._metadata[resource][rid] = obj
        return True

    def delete_metadata(self, resource: str, rid: int) -> bool:
        return self._metadata[resource].pop(rid, None) is not None

    def add_validation(self, session_id: int, created_on: str, certify: dict) -> int:
        """Record a validation (certificate) resource produced by certification.

        `certify` is the validated PUT body — the module/OE the certificate is
        bound to, plus any algorithm prerequisites (spec 12.16.4.1).
        """
        vid = next(self._validation_ids)
        self._validations[vid] = {
            "session_id": session_id,
            "created_on": created_on,
            "certify": certify,
        }
        return vid

    def get_validation(self, vid: int) -> dict | None:
        return self._validations.get(vid)


def _make_store():
    """Pick the persistence backend at startup.

    DATABASE_URL set → PostgreSQL (DbStore, a write-through mirror of this Store);
    otherwise the in-memory Store — the default, so dev and the test suite run with
    no database. The DbStore import is lazy on purpose: the in-memory path must
    never import SQLAlchemy (the minimal CI image ships without it).
    """
    from app.core.config import get_settings

    if get_settings().database_url:
        from app.db.db_store import DbStore

        return DbStore()
    return Store()


store = _make_store()
