"""Increment 7: per-session accessToken + complete session object.

[HUMAN REVIEW] POST /testSessions issues a JWT credential scoped to the session.
It MUST be HS256 (never alg:none) and carry exp/nbf/iss.
"""
import pytest

from helpers import registration, session_headers

from app.core.auth import decode_token, hash_access_token
from app.core.config import get_settings
from app.store import store

_FIXTURE = get_settings().fixtures_dir / "ML-KEM-keyGen-FIPS203" / "prompt.json"

pytestmark = pytest.mark.skipif(
    not _FIXTURE.exists(),
    reason="NIST fixtures not vendored; run scripts/fetch-nist-fixtures.sh",
)


def _register(client, v, auth_header):
    reg = [{"acvVersion": v}, {"algorithms": [
        registration("ML-KEM-keyGen-FIPS203")
    ]}]
    return client.post("/acvp/v1/testSessions", json=reg, headers=auth_header).json()[1]


def test_registration_issues_session_token(client, acv_version, auth_header):
    body = _register(client, acv_version, auth_header)
    token = body["accessToken"]
    assert token  # non-empty

    sid = int(body["url"].rsplit("/", 1)[1])
    claims = decode_token(token)  # rejects alg:none / bad signature
    assert claims["sub"] == f"session:{sid}"
    for claim in ("iss", "iat", "nbf", "exp"):
        assert claim in claims


def test_registration_object_is_spec_shaped(client, acv_version, auth_header):
    body = _register(client, acv_version, auth_header)
    # POST response object per spec (vectorSetUrls array + accessToken).
    assert set(body.keys()) == {
        "url", "acvpVersion", "createdOn", "expiresOn", "encryptAtRest",
        "vectorSetUrls", "publishable", "passed", "isSample", "accessToken",
    }
    assert body["acvpVersion"] == acv_version
    assert body["publishable"] is False
    assert body["passed"] is False  # nothing validated yet
    assert body["encryptAtRest"] is False
    assert isinstance(body["createdOn"], str) and body["expiresOn"] > body["createdOn"]


def test_stored_access_token_is_hashed_not_raw(client, acv_version, auth_header):
    """[HUMAN REVIEW] The raw session token is disclosed once; the server persists
    only a one-way hash of it, so a store/DB compromise cannot yield a live token."""
    body = _register(client, acv_version, auth_header)
    raw = body["accessToken"]
    sid = int(body["url"].rsplit("/", 1)[1])

    stored = store.get_session(sid).access_token
    assert stored != raw                     # never the raw bearer token
    assert stored == hash_access_token(raw)  # exactly the hash of what we issued

    # The raw token still authorizes the session — access is verified from the JWT
    # signature, not by looking the stored hash up.
    got = client.get(f"/acvp/v1/testSessions/{sid}", headers=session_headers(body))
    assert got.status_code == 200


def test_get_session_object_has_no_token(client, acv_version, auth_header):
    body = _register(client, acv_version, auth_header)
    sid = int(body["url"].rsplit("/", 1)[1])
    got = client.get(f"/acvp/v1/testSessions/{sid}", headers=session_headers(body)).json()[1]
    # GET object uses vectorSetsUrl pointer and does NOT re-issue the token.
    assert "accessToken" not in got
    assert got["vectorSetsUrl"] == f"/acvp/v1/testSessions/{sid}/vectorSets"
    assert got["acvpVersion"] == acv_version


def test_encrypt_at_rest_is_echoed(client, acv_version, auth_header):
    reg = [{"acvVersion": acv_version},
           {"algorithms": [registration("ML-KEM-keyGen-FIPS203")],
            "encryptAtRest": True}]
    body = client.post("/acvp/v1/testSessions", json=reg, headers=auth_header).json()[1]
    assert body["encryptAtRest"] is True
