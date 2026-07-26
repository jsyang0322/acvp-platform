from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status
from pydantic import ValidationError

from app.core import refs
from app.core.auth import (
    create_access_token,
    current_subject,
    hash_access_token,
    require_session_access,
)
from app.core.config import get_settings
from app.core.jobs import run_background, submit
from app.crypto_boundary import client
from app.crypto_boundary.registration import to_nist_registration
from app.models.certify import CertifyPayload
from app.models.envelope import wrap, unwrap
from app.models.paging import DEFAULT_LIMIT, paged
from app.models.registration import InvalidRegistration, UnsupportedAlgorithm, parse_registration
from app.store import VectorSet, store

router = APIRouter()

# Map a registered algorithm/mode/revision to its NIST fixture folder (stub wiring).
_MODE_FOLDER = {
    ("ML-KEM", "keyGen", "FIPS203"): "ML-KEM-keyGen-FIPS203",
    ("ML-KEM", "encapDecap", "FIPS203"): "ML-KEM-encapDecap-FIPS203",
    ("ML-DSA", "keyGen", "FIPS204"): "ML-DSA-keyGen-FIPS204",
    ("ML-DSA", "sigGen", "FIPS204"): "ML-DSA-sigGen-FIPS204",
    ("ML-DSA", "sigVer", "FIPS204"): "ML-DSA-sigVer-FIPS204",
}


def _start_generation(session, vs: VectorSet) -> None:

    async def _gen() -> None:
        try:
            result = client.generate(
                vs.registration, vs.mode_folder,
                session_id=session.session_id, vs_id=vs.vs_id,
            )
            # settle(), not a bare assignment: the client may have cancelled this
            # vector set while we were generating. See VectorSet.settle.
            vs.settle(
                prompt=result.prompt,
                internal_projection=result.internal_projection,  # NIST validate needs this
                expected=result.expected,
                status="ready",
            )
        except Exception as exc:  # engine/config/artifact failure -> disposition "error"
            vs.settle(error=str(exc), status="error")

    run_background(_gen)


def _iso(dt: datetime) -> str:
    """ISO-8601 UTC timestamp, e.g. 2018-05-31T12:03:43Z (spec format)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_error(exc: ValidationError) -> str:
    """Render a Pydantic failure as an ACVP error string (spec 22)."""
    err = exc.errors()[0]
    where = ".".join(str(p) for p in err["loc"])
    return f"{where}: {err['msg']}" if where else err["msg"]


def _session_passed(session) -> bool:
    """True only when every vectorSet has a passed disposition.

    A session with any cancelled vector set can never pass. Otherwise DELETE would
    be a certification bypass: fail a vector set, cancel it, and certify on the
    strength of the ones that happened to pass. [HUMAN REVIEW]
    """
    if session.has_cancelled_vector_sets:
        return False
    return bool(session.vector_sets) and all(
        vs.disposition() == "passed" for vs in session.vector_sets
    )


def _session_publishable(session) -> bool:
    """A session can be certified once it has passed; sample runs never can."""
    return _session_passed(session) and not session.is_sample


def _session_base(session) -> dict:
    """Fields common to the POST and GET test-session objects."""
    return {
        "url": f"/acvp/v1/testSessions/{session.session_id}",
        "acvpVersion": get_settings().acv_version,
        "createdOn": session.created_on,
        "expiresOn": session.expires_on,
        "encryptAtRest": session.encrypt_at_rest,
        "publishable": _session_publishable(session),
        "passed": _session_passed(session),
        "isSample": session.is_sample,
    }


@router.get("/testSessions")
def list_test_sessions(
    offset: int = 0, limit: int = DEFAULT_LIMIT, subject: str = Depends(current_subject)
) -> list:
    """Paged listing of the current user's test sessions (spec 12.16.1, OPTIONAL).

    Each element is a test session object (spec 12.16.3) — note that the session's
    accessToken is deliberately not among those fields: a credential is disclosed
    once, at creation, and never re-served from a listing.
    """
    data = [
        {
            **_session_base(s),
            "vectorSetsUrl": f"/acvp/v1/testSessions/{s.session_id}/vectorSets",
        }
        for s in store.list_sessions(owner=subject)
    ]
    return wrap(paged("testSessions", data, offset=offset, limit=limit))


@router.post("/testSessions")
def create_test_session(body: list = Body(...), subject: str = Depends(current_subject)) -> list:
    payload = unwrap(body)
    session = store.create_session()
    session.owner = subject
    session.is_sample = bool(payload.get("isSample", False))
    session.encrypt_at_rest = bool(payload.get("encryptAtRest", False))
    now = datetime.now(timezone.utc)
    session.created_on = _iso(now)
    session.expires_on = _iso(now + timedelta(seconds=get_settings().session_expire_seconds))
    # Per-session JWT credential (HS256), disclosed to the client once (below) and
    # verified thereafter from its signature, never by lookup. So persist only a
    # one-way hash — a DB compromise must not yield a live session token. [HUMAN REVIEW]
    session_token = create_access_token(f"session:{session.session_id}")
    session.access_token = hash_access_token(session_token)

    for algo in payload.get("algorithms", []):
        try:
            capability = parse_registration(algo)
        except (UnsupportedAlgorithm, InvalidRegistration) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
        folder = _MODE_FOLDER[(capability.algorithm, capability.mode, capability.revision)]
        vs = store.add_vector_set(session, folder)
        vs.expires_at = now + timedelta(seconds=get_settings().vector_set_expire_seconds)
        # What generate() receives. NIST GenVal's registration input is the
        # capabilities plus the resource vsId and the session's isSample flag
        # (its shape matches our NIST registration.json fixtures verbatim).
        vs.capabilities = capability.model_dump(exclude_none=True, exclude={"vsId", "isSample"})
        vs.registration = to_nist_registration(
            vs.capabilities, vs_id=vs.vs_id, is_sample=session.is_sample
        )
        _start_generation(session, vs)

    return wrap(
        {
            **_session_base(session),
            "vectorSetUrls": [
                f"/acvp/v1/testSessions/{session.session_id}/vectorSets/{v.vs_id}"
                for v in session.vector_sets
            ],
            "accessToken": session_token,
        }
    )


@router.get("/testSessions/{testSessionId}")
def get_test_session(testSessionId: int, _: int = Depends(require_session_access)) -> list:
    session_id = testSessionId
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "test session not found")
    return wrap(
        {
            **_session_base(session),
            "vectorSetsUrl": f"/acvp/v1/testSessions/{session_id}/vectorSets",
        }
    )


@router.put("/testSessions/{testSessionId}")
def certify_test_session(
    testSessionId: int, body: list = Body(...), _: int = Depends(require_session_access)
) -> list:
    session_id = testSessionId
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "test session not found")
    try:
        certify = CertifyPayload(**unwrap(body))
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _first_error(exc))
    # The authorization gate comes first: a session that cannot be certified gets
    # the same 403 whether or not its module exists, so this is not a probe for
    # which metadata resources are present.
    if not (_session_passed(session) and _session_publishable(session)):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "test session must be publishable and passed before certification",
        )
    # [HUMAN REVIEW] A certificate states that *this module*, in *this environment*,
    # passed. If the references are never checked, it states nothing at all.
    for field, target in (("moduleUrl", "modules"), ("oeUrl", "oes")):
        url = getattr(certify, field)
        if url is not None and not refs.exists(url, resource=target):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{field} does not resolve to an existing {target} resource: {url}",
            )

    recorded = certify.model_dump(exclude_none=True)

    async def _run(rid: int) -> None:
        vid = store.add_validation(session_id, _iso(datetime.now(timezone.utc)), recorded)
        store.complete_request(rid, f"/acvp/v1/validations/{vid}")

    # The request belongs to the *user* who owns the session, not to the session
    # token that authorized this call — "session:{id}" is a credential scope, not
    # an identity, and GET /requests lists "requests for the current user" (12.7.1).
    rid = submit(_run, owner=session.owner)
    return wrap({"url": f"/acvp/v1/requests/{rid}", "status": "processing"})


@router.delete("/testSessions/{testSessionId}", status_code=status.HTTP_200_OK)
def cancel_test_session(
    testSessionId: int, _: int = Depends(require_session_access)
) -> Response:
    """Cancel a test session (spec 12.16.5).

    Marks it cancelled; store.get_session then reads it as absent, so every other
    operation on it 404s, as the spec allows.
    """
    session = store.get_session(testSessionId)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "test session not found")
    session.cancelled = True
    return Response(status_code=status.HTTP_200_OK)


@router.get("/testSessions/{testSessionId}/results")
def get_session_results(testSessionId: int, _: int = Depends(require_session_access)) -> list:
    session_id = testSessionId
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "test session not found")

    results = [
        {
            "vectorSetUrl": f"/acvp/v1/testSessions/{session_id}/vectorSets/{vs.vs_id}",
            "status": vs.disposition(),
        }
        for vs in session.active_vector_sets
    ]
    # `passed` comes from _session_passed, not from the rows above: a cancelled
    # vector set drops out of the listing but still denies the session a pass.
    return wrap({"passed": _session_passed(session), "results": results})
