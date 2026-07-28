/**
 * Browser end-to-end smoke test for the ACVP Validation Console (milestone M4).
 *
 * Drives the full six-step wizard in a real browser — system Google Chrome via
 * Playwright's `channel: "chrome"`, so no separate browser download is needed —
 * against a running stack, screenshotting each step. Exits non-zero if any step
 * fails, so it doubles as a smoke gate.
 *
 * Prerequisites (start these first, in separate shells):
 *   backend :  cd ../backend && .venv/bin/uvicorn app.main:app --port 8000   # fixture stub; no DB/mTLS
 *   frontend:  npm run dev                                                   # Vite on :5173
 *   Google Chrome installed.
 *
 * Run:   npm run test:e2e
 * Env:   FRONTEND_URL (default http://localhost:5173), E2E_OUT (screenshot dir)
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.FRONTEND_URL || "http://localhost:5173";
const OUT = process.env.E2E_OUT || join(HERE, "screenshots");
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("console", (m) => {
  if (m.type() === "error" && !m.text().includes("favicon")) console.log("  [browser]", m.text());
});

// Scope button lookups to the content area — the workflow rail items are also
// role="button" (keyboard-accessible), so an unscoped getByRole would collide.
const main = page.locator("main.content");
const mbtn = (name, exact = false) => main.getByRole("button", { name, exact });
const railNav = (label) => page.locator("nav.rail li").filter({ hasText: label }).first();
const shot = (name) => page.screenshot({ path: join(OUT, `${name}.png`) });

const failures = [];
const step = async (label, fn) => {
  try {
    await fn();
    console.log("  ✓", label);
  } catch (e) {
    failures.push(label);
    console.log("  ✗", label, "—", e.message.split("\n")[0]);
    await shot(`ERR-${label.replace(/\W+/g, "-")}`);
  }
};

const createProductionSession = async () => {
  await mbtn("Production", true).click();
  await mbtn(/Create test session|Create another session/).click();
  await main.getByRole("heading", { name: "Retrieve vectors" }).waitFor({ timeout: 15000 });
  await main.getByText("retrieved", { exact: true }).first().waitFor({ timeout: 30000 });
};

console.log(`E2E against ${BASE}`);

await step("authenticate", async () => {
  await page.goto(BASE, { waitUntil: "networkidle" });
  await main.getByRole("heading", { name: "Authenticate" }).waitFor({ timeout: 15000 });
  await shot("01-login");
  await mbtn("Sign in").click();
  await main.getByRole("heading", { name: "Create a test session" }).waitFor({ timeout: 15000 });
  await shot("02-configure");
});

await step("create session + history", async () => {
  await createProductionSession();
  await shot("03-vectors");
  await railNav("Create test session").click();
  await createProductionSession(); // a second session so the history list is exercised
  await railNav("Create test session").click();
  await main.getByRole("heading", { name: "Session history" }).waitFor({ timeout: 8000 });
  await shot("04-history");
});

await step("submit answers", async () => {
  await railNav("Submit answers").click();
  await main.getByRole("heading", { name: "Submit answers" }).waitFor({ timeout: 15000 });
  const btns = mbtn("Submit responses");
  const n = await btns.count();
  for (let i = 0; i < n; i++) await btns.nth(i).click();
  await main.getByText(/Responses submitted/).first().waitFor({ timeout: 15000 });
  await shot("05-submit");
});

await step("results", async () => {
  await mbtn(/Continue to results/).click();
  await main.getByRole("heading", { name: "Results & disposition" }).waitFor({ timeout: 15000 });
  await mbtn("Download Markdown report").waitFor({ timeout: 30000 }); // summary loaded
  await shot("06-results");
});

await step("certify", async () => {
  await mbtn(/Continue to certification/).click();
  await main.getByRole("heading", { name: "Certify the session" }).waitFor({ timeout: 15000 });
  await mbtn("Submit for validation").click();
  await main.locator(".badge.ok", { hasText: "approved" }).first().waitFor({ timeout: 30000 });
  await shot("07-certify");
});

await browser.close();
console.log(failures.length ? `\nFAILED: ${failures.join(", ")}` : "\nAll steps passed.");
process.exit(failures.length ? 1 : 0);
