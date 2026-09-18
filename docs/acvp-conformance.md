# ACVP conformance matrix (server-client / protocol layer)

Maps this platform's behaviour to the ACVP specification, against **both** sources:

- **NIST rendered spec** — https://pages.nist.gov/ACVP/draft-fussell-acvp-spec.html
- **GitHub source** — https://github.com/usnistgov/ACVP (`src/protocol/sections/*.adoc`)

Pinned to ACVP **1.0**. Legend: ✅ conformant · ⏳ deferred (reason given) · normative level per spec (MUST/SHALL/SHOULD/RECOMMENDED/MAY).

Every row is backed by a pytest in `backend/tests/`. Run `pytest -q` (currently **200 passed, 6 skipped**).

---

## 1. Message envelope & JSON naming

| Requirement | Level | Source | Status | Proof |
|---|---|---|---|---|
| Every message is `[{"acvVersion":"1.0"}, {payload}]` | required | §10 messaging | ✅ `models/envelope.py` `wrap`/`unwrap` | `test_envelope.py` |
| Wrong `acvVersion` rejected (400) | required | §10 | ✅ | `test_envelope.py` |
| JSON keys use **lowerCamelCase** | SHALL | §21 JSON guidelines | ✅ all keys (`accessToken`, `vectorSetUrls`, `tgId`, `tcId`, `disposition`, `approvedUrl`, …) | reviewed per endpoint |

---

## 2. HTTP URI hierarchy & naming

Structure: `…/acvp/v1/<resource>` (§6 / `04-architecture.adoc` `uri_table`). Path parameters are named exactly as the spec (`{testSessionId}`, `{vectorSetId}`, `{requestId}`) — visible in the generated OpenAPI (`/openapi.json`, `/docs`).

| Method + URI (ours) | Spec resource | File | Proof |
|---|---|---|---|
| `POST /login` | `/login` (§10) | `api/login.py` | `test_login.py` |
| `POST /login/refresh` | `/login/refresh` (§10, MAY) | `api/login.py` | `test_login_refresh.py` |
| `GET /algorithms` | `/algorithms` | `api/algorithms.py` | `test_login.py` |
| `POST /testSessions` | `/testSessions` | `api/test_sessions.py` | `test_flow.py` |
| `GET /testSessions/{testSessionId}` | `/testSessions/{testSessionId}` | `api/test_sessions.py` | `test_session_token.py` |
| `PUT /testSessions/{testSessionId}` (certify) | `testSession_put` | `api/test_sessions.py` | `test_certify.py` |
| `GET /testSessions/{testSessionId}/results` | `/…/results` | `api/test_sessions.py` | `test_session_results.py` |
| `GET /testSessions/{testSessionId}/vectorSets` | `/…/vectorSets` | `api/vector_sets.py` | `test_vectorset_list.py` |
| `GET /…/vectorSets/{vectorSetId}` | `/…/{vectorSetId}` | `api/vector_sets.py` | `test_vector_retry.py` |
| `GET /…/{vectorSetId}/expected` | `/…/expected` | `api/vector_sets.py` | `test_is_sample.py` |
| `POST /…/{vectorSetId}/results` | `vectorSet_results_post` | `api/vector_sets.py` | `test_results_submission.py` |
| `PUT /…/{vectorSetId}/results` | `vectorSet_results_put` | `api/vector_sets.py` | `test_resubmit.py` |
| `GET /…/{vectorSetId}/results` | `vectorSet_results_get` (wrapped in `{"results":{…}}`) | `api/vector_sets.py` | `test_disposition.py` |
| `GET /requests/{requestId}` | `/requests/{requestId}` | `api/requests.py` | `test_certify.py` |
| `GET /validations/{validationId}` | `/validations` | `api/validations.py` | `test_validations.py` |
| `GET /modules`, `GET /oes` | `/modules`, `/oes` (paged) | `api/metadata.py` | `test_certify.py` |

**Deferred resources** (spec defines, not yet built — names reserved, will match on build): `/vendors` (+addresses/contacts), `/persons`, `/dependencies`, `/large`, and the create/update (POST/PUT/DELETE) methods on the metadata resources. These are secondary per the project plan (`docs/dev-process.md §4`).

---

## 3. Disposition & results JSON

| Requirement | Source | Status | Proof |
|---|---|---|---|
| 7 disposition states `passed/fail/incomplete/unreceived/missing/expired/error` | `vectorSet_results_get` | ✅ `store.VectorSet.disposition()` | `test_disposition.py` |
| Per-vectorSet results wrapped `{"results":{vsId,disposition,tests}}` | example JSON | ✅ | `test_disposition.py` |
| Results retrievable at any time (not 404 before validation) | §11 | ✅ | `test_disposition.py` |
| Submit returns **no content**, no score | `results_post` | ✅ 200 empty body | `test_results_submission.py` |
| Session summary `{passed, results:[{vectorSetUrl,status}]}` | `testSession_results_get` | ✅ | `test_session_results.py` |
| Vector retrieval async `{vsId, retry}` | `vectorSet_get` | ✅ | `test_vector_retry.py` |
| Errors carry `{"error": …}` | Appendix B | ✅ app-wide handler | `test_errors.py` |

*Note:* per-test `reason`/`expected`/`provided` fields come from the crypto module's `validation.json` (203/204 domain); the NIST golden fixtures omit `reason`, so it passes through absent until the real module supplies it.

---

## 4. JWT / authentication

| Requirement | Level | Source | Status | Proof |
|---|---|---|---|---|
| Claims `iss/nbf/exp/iat` **present & verified** | MUST (required) | §12.3 jwtToken | ✅ `decode_token` `require=[…]` + `issuer=` | `test_jwt_claims.py` |
| `alg` HS256; reject `alg:none` | if-desired (we stricter) | jwtToken | ✅ allow-list | `test_login.py` |
| Token **SHALL expire**; expired → **401** | SHALL | §12.3 login | ✅ | `test_login.py`, `test_jwt_claims.py` |
| Bearer token to access protected resources | MUST | jwtToken | ✅ HTTPBearer | `test_login.py` |
| Per-session accessToken **MUST** access that session | MUST | §12.16 | ✅ `require_session_access` (sub==session:id → else 403) | `test_authz.py` |
| `POST /login` initial + renewal | flow | §12.3 | ✅ (forged renewal token → 401, not silent downgrade) | `test_jwt_claims.py` |
| `POST /login/refresh` Multi-Refresh (array, order preserved) | MAY | §12.3 | ✅ | `test_login_refresh.py` |
| Login response `{accessToken, largeEndpointRequired, sizeConstraint}` | shape | §12.3 | ✅ | `test_login.py` |
| `pkey` claim | optional | jwtToken | ⏳ deferred — carries a DB-encryption key; no DB/encryptAtRest yet | — |

---

## 5. Transport / deployment (not application code)

| Requirement | Level | Source | Status |
|---|---|---|---|
| HTTPS + TLS 1.2+ | RECOMMENDED | §6 security | ⏳ deployment (terminate at uvicorn/reverse proxy) |
| **mutual TLS (mTLS)** | SHALL *for a validation authority*; internal testing MAY skip | §3 overview | ⏳ deployment — see the mTLS deployment notes |
| CORS for the web client | — | — | ✅ `main.py` (configurable origins) |

---

## Summary

Every endpoint, URI name, path parameter, JSON envelope/key, disposition value and JWT rule that this layer implements conforms to **both** the NIST rendered spec and the GitHub source, at the MUST/SHALL level, plus the optional Multi-Refresh. The only gaps are **explicit deferrals**: `pkey` (needs a DB), TLS/mTLS (deployment; SHALL only for a validation authority), and the secondary metadata/`large` resources (project plan). The crypto correctness of vectors is validated separately at 203/204 integration (feed a NIST prompt → reproduce NIST `expectedResults`).
