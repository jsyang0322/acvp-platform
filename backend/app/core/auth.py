import hashlib
import time

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import get_settings

_bearer = HTTPBearer(auto_error=True)


def hash_access_token(token: str) -> str:
    """One-way digest of a session access token, for storage at rest.

    The token is a high-entropy signed JWT, so a fast digest (SHA-256 — how GitHub
    stores personal access tokens) is appropriate; the salted slow hashing a
    low-entropy password needs would buy nothing here. Session access is verified
    statelessly from the JWT signature (see require_session_access), never by looking
    the stored value up — so this digest is never compared. It exists only so a
    database compromise cannot yield a live session bearer token. [HUMAN REVIEW]
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(subject: str) -> str:
    s = get_settings()
    now = int(time.time())
    payload = {
        "iss": s.jwt_issuer,
        "sub": subject,
        "iat": now,
        "nbf": now,
        "exp": now + s.jwt_expire_seconds,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_alg)


def decode_token(token: str, *, verify_exp: bool = True) -> dict:
    s = get_settings()
    try:
        # `algorithms` is an allow-list: an `alg: none` token can never be accepted.
        # The four spec-required claims must be present (spec 12.3), and iss is
        # verified. verify_exp is relaxed only for renewal/refresh of expired tokens
        # — the claim must still be present, just not enforced as unexpired.
        return jwt.decode(
            token,
            s.jwt_secret,
            algorithms=[s.jwt_alg],
            issuer=s.jwt_issuer,
            options={"require": ["iss", "nbf", "exp", "iat"], "verify_exp": verify_exp},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid token: {exc.__class__.__name__}",
        )


def current_subject(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> str:
    return decode_token(creds.credentials).get("sub", "")


def require_session_access(
    testSessionId: int, creds: HTTPAuthorizationCredentials = Depends(_bearer)
) -> int:
    """Authorize a session-scoped request.

    Spec: the accessToken issued per test session MUST be supplied to access that
    Test Session. Only that session's own token (sub == "session:{id}") is
    accepted here — the shared login token and other sessions' tokens are
    rejected with 403. decode_token already rejects alg:none / bad-sig / expired.

    The path parameter is named `testSessionId` to match the spec's URI hierarchy.
    """
    sub = decode_token(creds.credentials).get("sub")
    if sub != f"session:{testSessionId}":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="access token is not authorized for this test session",
        )
    return testSessionId
