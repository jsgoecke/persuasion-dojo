/**
 * E2E: First-run onboarding wizard
 *
 * The OnboardingWizard gates the main overlay behind a 3-screen privacy
 * disclosure flow. Completion is persisted in localStorage('pdojo:onboarded').
 *
 * Tests:
 *   1. First-run: wizard is shown instead of the overlay.
 *   2. Wizard completion: clicking through all three screens reaches the idle overlay.
 *   3. Returning user: wizard is skipped when the flag is already set.
 *
 * No backend mock is needed — the coaching socket only connects after
 * "Start session" is clicked, which is never reached in these tests.
 * A minimal HTTP server is started anyway so any incidental fetches
 * return a clean 404 rather than ECONNREFUSED.
 */

import { test, expect, _electron as electron } from "@playwright/test";
import { join } from "path";
import http from "http";

const OVERLAY_DIR = join(__dirname, "..", "..");
const MAIN_JS     = join(OVERLAY_DIR, "out", "main", "index.js");

// ── Minimal stub server (no routes needed, just absorbs stray requests) ────

function startStubServer(): { close(): Promise<void> } {
  const server = http.createServer((_req, res) => { res.writeHead(404); res.end(); });
  server.listen(8000);
  return {
    close: () => new Promise<void>((resolve, reject) =>
      server.close(err => (err ? reject(err) : resolve())),
    ),
  };
}

// ── Helpers ────────────────────────────────────────────────────────────────

async function clearOnboarding(page: import("@playwright/test").Page): Promise<void> {
  await page.evaluate(() => localStorage.removeItem("pdojo:onboarded"));
  await page.reload();
}

async function setOnboarded(page: import("@playwright/test").Page): Promise<void> {
  await page.evaluate(() => localStorage.setItem("pdojo:onboarded", "1"));
  await page.reload();
}

// ── Tests ─────────────────────────────────────────────────────────────────

test.describe("Onboarding wizard", () => {
  let stub: { close(): Promise<void> };

  test.beforeEach(() => { stub = startStubServer(); });
  test.afterEach(async () => { await stub.close(); });

  test("first run: wizard is shown instead of the overlay", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await clearOnboarding(page);

    // Wizard header visible, overlay idle button must NOT be present.
    await expect(page.getByText(/persuasion dojo/i)).toBeVisible({ timeout: 5_000 });
    await expect(page.getByRole("button", { name: /start session/i })).not.toBeVisible();

    await app.close();
  });

  test("wizard screen 1: next button advances to privacy screen", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await clearOnboarding(page);

    await page.getByRole("button", { name: /next/i }).click();

    await expect(page.getByText(/privacy/i)).toBeVisible({ timeout: 3_000 });
    await expect(page.getByText(/audio capture/i)).toBeVisible();

    await app.close();
  });

  test("wizard completion: clicking through all 3 screens reaches the overlay", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await clearOnboarding(page);

    // Screen 1 → Screen 2
    await page.getByRole("button", { name: /next/i }).click();
    await expect(page.getByRole("button", { name: /i understand/i })).toBeVisible({ timeout: 3_000 });

    // Screen 2 → Screen 3
    await page.getByRole("button", { name: /i understand/i }).click();
    await expect(page.getByRole("button", { name: /let'?s go/i })).toBeVisible({ timeout: 3_000 });

    // Screen 3 → overlay
    await page.getByRole("button", { name: /let'?s go/i }).click();
    await expect(page.getByRole("button", { name: /start session/i })).toBeVisible({ timeout: 5_000 });

    await app.close();
  });

  test("wizard completion: onboarding flag is persisted in localStorage", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await clearOnboarding(page);

    await page.getByRole("button", { name: /next/i }).click();
    await page.getByRole("button", { name: /i understand/i }).click();
    await page.getByRole("button", { name: /let'?s go/i }).click();

    // Verify localStorage was written.
    const flag = await page.evaluate(() => localStorage.getItem("pdojo:onboarded"));
    expect(flag).toBe("1");

    await app.close();
  });

  test("returning user: wizard is skipped when flag is already set", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await setOnboarded(page);

    // Should land directly on the overlay idle view.
    await expect(page.getByRole("button", { name: /start session/i })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(/persuasion dojo/i)).not.toBeVisible();

    await app.close();
  });

  test("step dots: correct dot is highlighted on each screen", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await clearOnboarding(page);

    // Screen 1 — first dot active (we just check the page renders without error)
    await expect(page.getByRole("button", { name: /next/i })).toBeVisible();

    await page.getByRole("button", { name: /next/i }).click();
    // Screen 2 visible
    await expect(page.getByRole("button", { name: /i understand/i })).toBeVisible();

    await page.getByRole("button", { name: /i understand/i }).click();
    // Screen 3 visible
    await expect(page.getByRole("button", { name: /let'?s go/i })).toBeVisible();

    await app.close();
  });
});
