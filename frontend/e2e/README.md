# Browser end-to-end (M4)

`flow.mjs` drives the full ACVP wizard in a **real browser** and screenshots each
step — the milestone-M4 "browser end-to-end" check, as a repeatable smoke test.

It uses **system Google Chrome** through Playwright's `channel: "chrome"`, so there
is no browser binary to download (only the `playwright` dev dependency).

## Run

Start the stack first (two shells), then run the driver:

```bash
# 1) backend — fixture stub (no DB, no mTLS)
cd backend && .venv/bin/uvicorn app.main:app --port 8000

# 2) frontend dev server
cd frontend && npm run dev

# 3) drive the browser
cd frontend && npm run test:e2e
```

Exit code is non-zero if any step fails (an `ERR-*.png` is captured for the failing
step). Screenshots land in `frontend/e2e/screenshots/` (gitignored).

Config via env: `FRONTEND_URL` (default `http://localhost:5173`), `E2E_OUT`
(screenshot directory).

## What it covers

Authenticate → create test session (×2, exercising the session-history list) →
retrieve vectors (async polling) → submit answers → results & disposition → certify
→ approved. Against the fixture stub the submitted answers grade `passed`; with the
real NIST engine (`USE_NIST_GENVAL=true`) the demo auto-fill would not, so run this
against the stub.
