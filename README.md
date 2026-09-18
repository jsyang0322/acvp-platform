# ACVP Platform — a post-quantum cryptographic validation server (FIPS 203 / 204)

**Language / 語言：English ｜ [繁體中文](README.zh-TW.md)**

A cryptographic module seeking federal certification has to be tested by a validation authority, not
just documented. **ACVP** — NIST's Automated Cryptographic Validation Protocol — is how that testing
happens over the wire: the server issues test vectors, the implementation under test computes
answers, and the server grades them against an answer key the implementation never sees.

This is that server — plus the web client that drives it — for the two new **post-quantum**
standards: **FIPS 203 (ML-KEM)** and **FIPS 204 (ML-DSA)**. It is written from the protocol layer up
against [`draft-fussell-acvp-spec`](https://pages.nist.gov/ACVP/): the `acvVersion` message envelope,
JWT issuance and per-session authorization scoping, the vector-set state machine, two independent
asynchronous polling points, a seven-state disposition model, and an mTLS terminator, all written
from the specification rather than adapted from an existing ACVP server.

The cryptographic computation itself sits behind a process boundary. The server drives NIST's own
ACVP-Server GenVal engine over a file-based interface, JSON in and JSON out, rather than
reimplementing ML-KEM and ML-DSA: a vector set is only as trustworthy as the implementation that
generated it, and for a freshly standardised primitive that means the reference implementation. The
boundary also shapes the architecture. Underneath the protocol layer sits either a set of pinned NIST
golden vectors or the live engine, and nothing above it needs to know which.

> Built as the server-client half of an industry-academia FIPS 203 / 204 validation project.
> Design records under `docs/` are written in Traditional Chinese; this README is the English entry point.

---

## 🔬 Evidence

Each of the following can be verified independently:

| Evidence | Result | Report |
|---|---|---|
| **NIST golden vectors as the oracle** | All five ML-KEM / ML-DSA modes vendored **read-only** from `usnistgov/ACVP-Server`, pinned to commit `15c0f3de`. Feeding a NIST `prompt` must reproduce the NIST `expectedResults`; a fixture is never edited to make a test pass | [`tests/fixtures/nist/SOURCE.md`](tests/fixtures/nist/SOURCE.md) |
| **Test suite** | **200 passed, 6 skipped** across 33 files. Written test-first against the fixtures: read the golden vector, write the failing test, then implement | [`docs/acvp-conformance.md`](docs/acvp-conformance.md) |
| **Clause-by-clause conformance matrix** | Every requirement mapped to its spec clause, its normative level (SHALL / MUST / SHOULD), and the specific test that pins it — including the ones deliberately deferred, with reasons | [`docs/acvp-conformance.md`](docs/acvp-conformance.md) |
| **Live mTLS handshake in CI** | GitHub Actions runs the verification battery against a **real nginx container** — certificate rejection, CRL revocation reload, and the direct-to-backend bypass attempt, not just the middleware in isolation | [`.github/workflows/mtls-verify.yml`](.github/workflows/mtls-verify.yml) |
| **Browser end-to-end** | The full six-step wizard driven in **system Chrome** via Playwright, screenshotting each step; non-zero exit and an error capture on any failing step | [`frontend/e2e/`](frontend/e2e/README.md) |
| **Acceptance against the real engine** | An opt-in suite drives all five modes through NIST's actual GenVal engine on **freshly generated random vectors** rather than the static fixture: the answers produced by that generation must grade `passed`, and a format-preserving corruption must grade `failed` | [`test_nist_real_engine.py`](backend/tests/test_nist_real_engine.py) |
| **The grading engine is pinned** | The engine assigns the verdicts, so it is version-pinned like the vectors are; the build script **refuses to build off-pin** without an explicit override, so verdicts can't drift with the engine | [`scripts/nist/build-genval.sh`](scripts/nist/build-genval.sh) |

---

## 🏛️ Architecture & Tech Stack

* **Server:** FastAPI + Python 3.11, Pydantic v2. Every ACVP message is a typed model (registration / prompt / response / validation), so schema validation is the type system rather than hand-written checks. PyJWT (HS256), arq for the long-running generate / validate work.
* **Stores:** an in-memory store by default, so the whole flow runs with no database · PostgreSQL 16 via SQLAlchemy 2 + psycopg3 + Alembic, selected purely by `DATABASE_URL` and written through, so the swap is invisible to every endpoint.
* **Web client:** React 18 + Vite 5 + TypeScript, TanStack Query driving the two polling loops, presented as a six-step wizard that mirrors the protocol. Types are generated from FastAPI's OpenAPI schema, so client and server share one definition rather than two that drift.
* **Transport:** nginx as the spec's **ACV Proxy**, handling TLS 1.2 / 1.3 termination, client-certificate verification and CRL revocation. The application server never publishes a port to the host.
* **Crypto boundary:** NIST's ACVP-Server GenVal engine (C# / .NET 8 + Orleans) driven over a file-based CLI. Two providers implement one interface; `USE_NIST_GENVAL` chooses between them.
* **Verification:** pytest against the golden vectors, vitest for the client, Playwright for the browser flow, GitHub Actions for the live TLS handshake.

Every protocol endpoint lives under `/acvp/v1/`. `/health` is the single mTLS-exempt path, so an
orchestrator can probe liveness without a client certificate:

```
/acvp/v1/   login · login/refresh · algorithms[/{id}]
            testSessions[/{id}]                             create · get · certify · cancel
            testSessions/{id}/results                       session-level summary
            testSessions/{id}/vectorSets[/{vsId}]           retrieve (async: {vsId, retry:N})
            testSessions/{id}/vectorSets/{vsId}/results     POST submit · PUT resubmit · GET grade
            testSessions/{id}/vectorSets/{vsId}/expected    sample sessions only
            requests[/{id}]                                 async request-retry polling
            validations/{id}                                issued validation records
            vendors · persons · modules · oes · dependencies
/health     liveness (mTLS-exempt) · /health/db readiness
```

---

## 🧠 Engineering Notes

The more demanding problems encountered during implementation, and how each was resolved:

**Vector retrieval and result retrieval are two independent polling points.** Result retrieval is the
more intuitive of the two: answers are submitted, then the grade is polled for. The one more easily
overlooked is that vector retrieval also polls. While generation is still in progress the server
returns `{"vsId": N, "retry": 30}`, and the client must reissue the request after N seconds. The two
differ in both state and failure mode, and an implementation providing only one will stall whenever
generation is slow.

**Submitting answers returns an HTTP status code only.** `POST .../results` carries no grading
information; the disposition must be obtained by a separate `GET` on the same URL. The specification
is arranged this way because results are retrieved by the client rather than pushed by the server: a
validation authority grades on its own schedule, and the client may be offline for extended periods.

**A cancellation must take precedence over a background job still in progress.** Generation and
validation execute on background threads while the client continues to interact with the server, so a
vector set may be cancelled, or reach its deadline, before the thread completes. Writing status
directly on completion places that write after the cancellation, returning a cancelled vector set to
the session listing, which the specification prohibits. Completion therefore writes through
`settle()`, which refuses any write to a vector set already in a terminal state; expiry is likewise
not swept by a background job but computed from the clock at read time.

**The effectiveness of mTLS depends on whether another path to the server exists.** nginx verifies the
client certificate and forwards the outcome to the backend in a header. Should the backend be
directly reachable, that header can be forged externally. The backend therefore publishes no host
port, and nginx injects a shared secret that the middleware validates before the certificate header
is trusted at all.

**Browsers treat a client certificate as a credential.** Under the fetch specification a TLS client
certificate is a credential, so a request without `credentials: "include"` has its response discarded
by the browser; enabling that mode, however, prohibits a wildcard CORS origin. The CORS middleware
must additionally sit outside the mTLS middleware for preflights and rejected responses to carry CORS
headers. The three constraints are interdependent and present identically.

**Incorrect configuration should prevent startup.** A JWT signing key left at its placeholder value
renders every token forgeable; an unset proxy secret allows the mTLS check to be bypassed. Neither is
handled as a warning: startup validation blocks both outright, so that the failure mode is a service
that does not start rather than one operating normally on unsafe settings.

**The actual contract differed from the documentation.** The integration was initially assumed to be a
stdin/stdout command-line tool; inspection of the engine's source confirmed a file-based CLI, and
that NIST's validate step consumes the `internalProjection` (the complete answer key) rather than the
prompt. This difference affects the data model: the answer key must be persisted at generation time,
as it can no longer be obtained at the grading stage.

---

## 🔒 Protocol Coverage

| Capability | What is implemented | Specification |
|---|---|---|
| Message envelope | Every message is `[{"acvVersion":"1.0"}, {payload}]`; a wrong version, a missing envelope, or extra top-level elements are all rejected; lowerCamelCase throughout | §10 · §21 |
| Authentication | JWT HS256 on an allow-list (`alg:none` rejected), `iss`/`nbf`/`exp`/`iat` all *verified* rather than merely present, enforced expiry, renewal, and order-preserving Multi-Refresh | §12.3 |
| Session authorization | A per-session `accessToken` scoped to **that session alone**, stored as a one-way hash and disclosed exactly once, at issue | §12.16 |
| Test sessions | Create from a capability registration, list (paged, owner-scoped), get, certify, cancel; `isSample` sessions may retrieve the answer key, production sessions never can | §12.16 · §12.17.5.1 |
| Vector sets | Asynchronous retrieval with `retry`, a submission deadline with an `expired` terminal state, and cancellation that survives an in-flight generate or validate | §12.17 · §14 |
| Results & disposition | All seven states: `passed` / `failed` / `incomplete` / `unreceived` / `missing` / `expired` / `error`, synthesized from lifecycle state where the engine doesn't supply one, plus whole-set resubmission after a failure | `vectorSet_results_*` |
| Request-retry | Slow work runs in the background; `GET /requests/{id}` polls `initial` → `processing` → `approved` / `rejected` and yields an `approvedUrl` | §12.7 |
| Metadata | vendors · persons · modules · oes · dependencies, created and updated through that same approval flow, with every submitted URL reference validated before it is stored | §12.8–12.13 |
| Transport security | mTLS with CRL revocation, TLS 1.2 / 1.3 restricted to FIPS-approved AES-GCM suites, and a shared proxy secret defeating direct-to-backend bypass | §6 · §7.1 · SP 800-52r2 |
| Second factor | TOTP per NIST's credentials specification: RFC 6238, HMAC-SHA-256, 8 digits, 30-second step, seed keyed by the client's certificate DN | RFC 6238 · RFC 4226 |

---

## 🚀 Running It

**Prerequisites:** Python 3.11+ and Node 20+; Docker for the full stack; .NET 8 only for the real
grading engine.

```bash
# Vendor the golden vectors (a pinned sparse checkout, written read-only)
scripts/fetch-nist-fixtures.sh

# The server. Runs on the fixture provider: no .NET, no database, no certificates
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                               # 200 passed, 6 skipped
uvicorn app.main:app --reload           # API on :8000, OpenAPI at /docs

# The web client, in another terminal. Login password: acvp-demo
cd frontend && npm install && npm run dev        # http://localhost:5173
```

The full stack (mTLS, PostgreSQL and the real grading engine) is one command behind a dev PKI:

```bash
scripts/gen-certs.sh                    # CA, server, client, CRL, browser p12
cp .env.example .env                    # PROXY_SECRET and JWT_SECRET; compose refuses to start without them
docker compose up --build               # web client :5173 · API https://localhost:8443/acvp/v1
```

---

## 📚 Scope & Boundaries

**Cryptography is integrated, not reimplemented.** ML-KEM / ML-DSA key generation, encapsulation,
signing and verification, together with the comparison that determines pass or fail, are provided by
NIST's reference implementation and invoked across a process boundary rather than linked in. Given
how recently these primitives were standardised, a bespoke implementation would reduce the
trustworthiness of the verdicts this platform issues, and was therefore not adopted. The boundary is
drawn accordingly: the engine's language and runtime are not visible to the protocol layer, which is
also what allows that layer to run against golden vectors or against the live engine.

**Known limitations.** ML-KEM encapsulation and key-check cannot inject the randomness `m` through
the in-box .NET API, so an implementation under test built on that API has limited known-answer
coverage in that direction; vector generation in this platform is unaffected, as it derives from
NIST's reference implementation. The default fixture provider returns stored validation content,
allowing the full protocol flow to run without .NET; actual grading requires the engine.
