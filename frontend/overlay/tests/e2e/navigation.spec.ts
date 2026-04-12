/**
 * E2E: Screen navigation regression tests
 *
 * Verifies that every screen accessible from the home menu navigates
 * correctly and returns to the right place. Catches the bug where
 * profilePickerReturn state leaked between "Go Live → Add participant"
 * and "Home → Profiles" flows.
 *
 * Tests:
 *   1. Home → Profiles → Back returns to Home (not setup)
 *   2. Home → Go Live → Add participant → profiles shows "Add to session"
 *   3. After using Go Live picker, Home → Profiles shows browse mode (not picker)
 *   4. Every home grid item navigates to the correct screen and back
 */

import { test, expect, _electron as electron } from "@playwright/test";
import { join } from "path";
import http from "http";

const OVERLAY_DIR = join(__dirname, "..", "..");
const MAIN_JS     = join(OVERLAY_DIR, "out", "main", "index.js");

// ── Mock server with /participants, /users/me, /sessions, /health ──────────

function startMockServer(): { close(): Promise<void> } {
  const server = http.createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");

    if (req.method === "OPTIONS") {
      res.writeHead(204);
      res.end();
      return;
    }

    if (req.url === "/health") {
      res.writeHead(200);
      res.end(JSON.stringify({ status: "ok" }));
    } else if (req.url?.startsWith("/participants")) {
      res.writeHead(200);
      res.end(JSON.stringify([
        { id: "p1", name: "Sarah Chen", archetype: "Architect", sessions_observed: 3 },
        { id: "p2", name: "Mike Ross", archetype: "Bridge Builder", sessions_observed: 1 },
      ]));
    } else if (req.url?.startsWith("/users/me")) {
      res.writeHead(200);
      res.end(JSON.stringify({ display_name: "Test User", archetype: "Architect" }));
    } else if (req.url?.startsWith("/sessions")) {
      if (req.method === "GET") {
        res.writeHead(200);
        res.end(JSON.stringify([]));
      } else {
        res.writeHead(200);
        res.end(JSON.stringify({ session_id: "test-session" }));
      }
    } else {
      res.writeHead(404);
      res.end();
    }
  });
  server.listen(8000);
  return {
    close: () => new Promise<void>((resolve, reject) =>
      server.close(err => (err ? reject(err) : resolve())),
    ),
  };
}

// ── Helpers ────────────────────────────────────────────────────────────────

async function bypassOnboarding(page: import("@playwright/test").Page): Promise<void> {
  await page.evaluate(() => localStorage.setItem("pdojo:onboarded", "1"));
  await page.reload();
}

async function waitForHome(page: import("@playwright/test").Page): Promise<void> {
  // Home screen has the "Go live" primary CTA
  await expect(page.getByText("Go live")).toBeVisible({ timeout: 5_000 });
}

// ── Tests ─────────────────────────────────────────────────────────────────

test.describe("Screen navigation", () => {
  let stub: { close(): Promise<void> };

  test.beforeEach(() => { stub = startMockServer(); });
  test.afterEach(async () => { await stub.close(); });

  test("Home → Profiles shows browse mode (not session picker)", async () => {
    const app = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);
    await waitForHome(page);

    // Click Profiles from home grid
    await page.getByText("Profiles", { exact: true }).click();

    // Should show "Profiles" in the top bar, NOT "Add to session"
    // Wait for profile list to load
    await expect(page.getByText("Sarah Chen")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText("Add to session")).not.toBeVisible();

    await app.close();
  });

  test("Home → Profiles → Back returns to Home", async () => {
    const app = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);
    await waitForHome(page);

    await page.getByText("Profiles", { exact: true }).click();
    await expect(page.getByText("Sarah Chen")).toBeVisible({ timeout: 5_000 });

    // Click back
    await page.getByText("← Back").click();

    // Should be back at Home
    await waitForHome(page);

    await app.close();
  });

  test("Go Live → Add participant shows picker mode", async () => {
    const app = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);
    await waitForHome(page);

    // Enter Go Live setup
    await page.getByText("Go live").click();
    await expect(page.getByText("Session setup")).toBeVisible({ timeout: 3_000 });

    // Click Add participant
    await page.getByText(/add participant/i).click();

    // Should show "Add to session" title (picker mode)
    await expect(page.getByText("Add to session")).toBeVisible({ timeout: 3_000 });

    await app.close();
  });

  test("After Go Live picker, Home → Profiles shows browse mode (regression)", async () => {
    const app = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);
    await waitForHome(page);

    // Go Live → setup screen
    await page.getByText("Go live").click();
    await expect(page.getByText("Session setup")).toBeVisible({ timeout: 3_000 });

    // Add participant → enters picker mode
    await page.getByText(/add participant/i).click();
    await expect(page.getByText("Add to session")).toBeVisible({ timeout: 3_000 });

    // Go back to setup
    await page.getByText("← Back").click();
    await expect(page.getByText("Session setup")).toBeVisible({ timeout: 3_000 });

    // Go back to home
    await page.getByText("← Back").click();
    await waitForHome(page);

    // NOW navigate to Profiles from home — must be browse mode, not picker
    await page.getByText("Profiles", { exact: true }).click();
    await expect(page.getByText("Sarah Chen")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText("Add to session")).not.toBeVisible();

    await app.close();
  });

  test("Home grid items navigate and return correctly", async () => {
    const app = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);
    await waitForHome(page);

    // Self assessment → back
    await page.getByText("Self assessment").click();
    await expect(page.getByText("← Back")).toBeVisible({ timeout: 3_000 });
    await page.getByText("← Back").click();
    await waitForHome(page);

    // Upload & Analyze → back
    await page.getByText("Upload & Analyze").click();
    await expect(page.getByText("← Back")).toBeVisible({ timeout: 3_000 });
    await page.getByText("← Back").click();
    await waitForHome(page);

    await app.close();
  });
});
