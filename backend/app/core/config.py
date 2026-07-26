from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Known placeholder secrets shipped in examples/templates. Any of these in a
# security-sensitive setting means "not configured", never a usable value.
_PLACEHOLDER_SECRETS = {
    "change-me-dev-only",
    "CHANGE-ME-GENERATE-A-REAL-SECRET",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    acv_version: str = "1.0"
    # "dev" (default) or "production". Production refuses placeholder secrets.
    app_env: str = "dev"
    jwt_secret: str = "change-me-dev-only"
    jwt_alg: str = "HS256"
    jwt_issuer: str = "acvp-server"
    jwt_expire_seconds: int = 1800
    session_expire_seconds: int = 30 * 24 * 3600  # test sessions live ~30 days
    # Deadline for submitting a vector set's responses (spec section 14). The spec
    # leaves the length to the server; NIST's own deployment uses ~30 days.
    vector_set_expire_seconds: int = 30 * 24 * 3600
    demo_password: str = "acvp-demo"
    # Seed a starting catalogue of vendors/modules/OEs (see core/seed.py). Without
    # one, a fresh server has nothing certifiable to bind a certificate to.
    seed_demo_metadata: bool = True

    # Browser origins allowed to call the API (the web client dev servers).
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Set FIXTURES_DIR_OVERRIDE to point at the golden vectors explicitly
    # (e.g. when running in a container). Otherwise resolved relative to the repo.
    fixtures_dir_override: str | None = None
    
    # --- TLS / mTLS (ACVP spec §7, §6 ACV Proxy pattern) -----------------------
    # Set TLS_ENABLED=true to activate TLS termination (via Nginx or uvicorn).
    # Set MTLS_ENABLED=true to additionally require a client certificate on all
    # API endpoints (ACVP spec §7.1 "TLS mutual certificate authentication").
    # In the Docker / Nginx deployment these paths refer to files mounted inside
    # the nginx container; in bare-metal / dev mode they point at backend/certs/.
    tls_enabled: bool = False
    tls_certfile: str | None = None          # e.g. /etc/nginx/certs/server.crt
    tls_keyfile: str | None = None           # e.g. /etc/nginx/certs/server.key
    # CA cert used to verify client certificates (mTLS):
    mtls_enabled: bool = False
    mtls_ca_certfile: str | None = None      # e.g. /etc/nginx/certs/ca.crt
    # Paths that are exempt from the mTLS client-cert requirement even when
    # MTLS_ENABLED=true (health check must remain reachable by the orchestrator).
    mtls_exempt_paths: list[str] = ["/health"]
    # Shared secret between Nginx and the backend.  Nginx injects this value in
    # the X-Proxy-Secret header; the middleware rejects any request where the
    # header is missing or does not match.  This prevents an attacker from
    # connecting directly to uvicorn (:8000) and forging X-Client-Verify headers.
    # Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
    proxy_secret: str | None = None

    # --- TOTP second factor (NIST wiki "Credentials Specification") -------------
    # When true, /login and /login/refresh require a fresh RFC 6238 TOTP
    # (HMAC-SHA-256, 8 digits, 30s step) instead of the static demo password.
    # Dev default is false — spec §4.2 lets internal testing omit this.
    totp_enabled: bool = False
    # Per-client seed registry: mTLS certificate Subject DN -> Base64 seed.
    # Clients not using mTLS fall back to the "default" entry. From the env,
    # supply JSON: TOTP_SEEDS='{"default": "<base64>"}'
    # Generate a seed: python -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
    totp_seeds: dict[str, str] = {}

    # --- Crypto boundary (NIST ACVP-Server GenVal) ------------------------------
    # When false (default), the boundary stands in with the vendored NIST fixtures
    # so the whole pipeline runs without the .NET engine. Flip to true once the
    # NIST GenValApp runner is built and Orleans is running (see scripts/nist/).
    use_nist_genval: bool = False
    # Absolute path to the published NIST.CVP.ACVTS.Generation.GenValApp.dll.
    genval_runner_dll: str | None = None
    # Root under which per-vector-set work dirs (registration/prompt/... json) live.
    genval_artifact_root: str | None = None
    genval_timeout_seconds: int = 120

    # --- Database (PostgreSQL) --------------------------------------------------
    # SQLAlchemy URL for the persistent store (psycopg v3 sync driver):
    #   postgresql+psycopg://user:password@host:5432/dbname
    # None (default) means "not configured" — the in-memory store still backs the
    # app in this phase, so the server and the test suite run without Postgres.
    # In Docker/Podman the host is the `db` service; from the host it is localhost
    # (the container's published port). Contains a password — keep it in .env,
    # never in the repo (see .env.example). [HUMAN REVIEW] connection secret.
    database_url: str | None = None
    # Echo SQL to the logs. Debugging only — may log data; never enable in prod.
    db_echo: bool = False

    @model_validator(mode="after")
    def _fail_closed_secrets(self) -> "Settings":
        """Refuse to start with unusable secrets (fail-closed, never fail-open).

        [HUMAN REVIEW] mTLS mode: without a real PROXY_SECRET, anything on the
        container network could forge X-Client-Verify and bypass client-cert
        checks, so the secret is mandatory. Production: a placeholder JWT
        signing key would make every token forgeable.
        """
        if self.tls_enabled:
            if not self.tls_certfile or not self.tls_keyfile:
                raise ValueError(
                    "TLS_ENABLED=true requires both TLS_CERTFILE and TLS_KEYFILE "
                    "to be set to valid file paths."
                )

        if self.mtls_enabled:
            if (
                not self.proxy_secret
                or self.proxy_secret in _PLACEHOLDER_SECRETS
                or len(self.proxy_secret) < 16
            ):
                raise ValueError(
                    "MTLS_ENABLED=true requires PROXY_SECRET to be set to a real "
                    "secret (>=16 chars, not a placeholder). Generate one with: "
                    "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
                )
        if self.app_env.lower() in ("production", "prod"):
            if self.jwt_secret in _PLACEHOLDER_SECRETS or len(self.jwt_secret) < 32:
                raise ValueError(
                    "APP_ENV=production requires JWT_SECRET to be set to a real "
                    "secret (>=32 chars, not the shipped placeholder)."
                )
        if self.totp_enabled:
            import base64 as _b64

            def _bad(seed: str) -> bool:
                try:
                    return len(_b64.b64decode(seed, validate=True)) < 16
                except Exception:
                    return True

            if not self.totp_seeds or any(_bad(s) for s in self.totp_seeds.values()):
                raise ValueError(
                    "TOTP_ENABLED=true requires TOTP_SEEDS with valid Base64 seeds "
                    "(>=16 bytes decoded). Generate one with: python -c "
                    "\"import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())\""
                )
        return self

    @property
    def fixtures_dir(self) -> Path:
        if self.fixtures_dir_override:
            return Path(self.fixtures_dir_override)
        # repo-root/tests/fixtures/nist  (this file: backend/app/core/config.py)
        return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "nist"

    @property
    def genval_artifact_dir(self) -> Path:
        if self.genval_artifact_root:
            return Path(self.genval_artifact_root)
        # backend/data/acvp-sessions  (this file: backend/app/core/config.py)
        return Path(__file__).resolve().parents[2] / "data" / "acvp-sessions"


@lru_cache
def get_settings() -> Settings:
    return Settings()
