/**
 * E2E: Team sync pane (export + import)
 *
 * Export flow:
 *   idle → click "Team Sync" → TeamSyncPane
 *        → fill passphrase → click Export
 *        → POST /team/export → encrypted bundle
 *        → Download .pdojo / Copy to clipboard buttons appear
 *
 * Import flow:
 *   → paste bundle text + passphrase → click Import
 *   → POST /team/import → "Import complete" confirmation
 *
 * Mock server handles:
 *   POST /team/export → { bundle: "…" }
 *   POST /team/import → 200
 */

import { test, expect, _electron as electron } from "@playwright/test";
import { join } from "path";
import http from "http";
import type { IncomingMessage } from "http";

const OVERLAY_DIR = join(__dirname, "..", "..");
const MAIN_JS     = join(OVERLAY_DIR, "out", "main", "index.js");

// ── Mock server ────────────────────────────────────────────────────────────

interface TeamSyncMockServer {
  close(): Promise<void>;
  lastExport(): Promise<{ passphrase: string }>;
  lastImport(): Promise<{ bundle: string; passphrase: string }>;
}

const MOCK_BUNDLE = "eyJlbmMiOiJtb2NrZWRidW5kbGUifQ==";

function startTeamSyncMockServer(): TeamSyncMockServer {
  let resolveExport!: (v: { passphrase: string }) => void;
  let exportPromise = new Promise<{ passphrase: string }>(r => { resolveExport = r; });

  let resolveImport!: (v: { bundle: string; passphrase: string }) => void;
  let importPromise = new Promise<{ bundle: string; passphrase: string }>(r => { resolveImport = r; });

  const server = http.createServer((req: IncomingMessage, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      const url = req.url ?? "";

      if (req.method === "POST" && url === "/team/export") {
        const body = JSON.parse(Buffer.concat(chunks).toString()) as { passphrase: string };
        resolveExport(body);
        exportPromise = new Promise(r => { resolveExport = r; });
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ bundle: MOCK_BUNDLE }));
      } else if (req.method === "POST" && url === "/team/import") {
        const body = JSON.parse(Buffer.concat(chunks).toString()) as { bundle: string; passphrase: string };
        resolveImport(body);
        importPromise = new Promise(r => { resolveImport = r; });
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true }));
      } else {
        res.writeHead(404); res.end();
      }
    });
  });

  server.listen(8000);

  return {
    close: () => new Promise<void>((resolve, reject) =>
      server.close(err => (err ? reject(err) : resolve())),
    ),
    lastExport: () => exportPromise,
    lastImport: () => importPromise,
  };
}

// ── Helpers ────────────────────────────────────────────────────────────────

async function bypassOnboarding(page: import("@playwright/test").Page): Promise<void> {
  await page.evaluate(() => localStorage.setItem("pdojo:onboarded", "1"));
  await page.reload();
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe("Team sync pane", () => {
  let mock: TeamSyncMockServer;

  test.beforeEach(() => { mock = startTeamSyncMockServer(); });
  test.afterEach(async () => { await mock.close(); });

  test("clicking Team Sync opens the pane", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /team sync/i }).click();

    await expect(page.getByText(/team intelligence sync/i)).toBeVisible({ timeout: 3_000 });

    await app.close();
  });

  test("back button returns to idle view", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /team sync/i }).click();
    await expect(page.getByText(/team intelligence sync/i)).toBeVisible({ timeout: 3_000 });

    await page.getByRole("button", { name: /← back/i }).click();

    await expect(page.getByRole("button", { name: /start session/i })).toBeVisible({ timeout: 3_000 });

    await app.close();
  });

  test("export: sends passphrase to POST /team/export", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /team sync/i }).click();
    await expect(page.getByText(/team intelligence sync/i)).toBeVisible({ timeout: 3_000 });

    const exportPromise = mock.lastExport();

    await page.getByPlaceholder(/passphrase \(share separately\)/i).fill("s3cr3t-pass");
    await page.getByRole("button", { name: /^export$/i }).click();

    const body = await exportPromise;
    expect(body.passphrase).toBe("s3cr3t-pass");

    await app.close();
  });

  test("export: download and copy buttons appear after successful export", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /team sync/i }).click();
    await expect(page.getByText(/team intelligence sync/i)).toBeVisible({ timeout: 3_000 });

    await page.getByPlaceholder(/passphrase \(share separately\)/i).fill("mypass");
    await page.getByRole("button", { name: /^export$/i }).click();

    await expect(page.getByRole("button", { name: /download .pdojo/i })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByRole("button", { name: /copy to clipboard/i })).toBeVisible();

    await app.close();
  });

  test("import: sends bundle and passphrase to POST /team/import", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /team sync/i }).click();
    await expect(page.getByText(/team intelligence sync/i)).toBeVisible({ timeout: 3_000 });

    const importPromise = mock.lastImport();

    await page.getByPlaceholder(/paste encrypted bundle/i).fill(MOCK_BUNDLE);
    await page.getByPlaceholder(/^passphrase$/i).fill("shared-secret");
    await page.getByRole("button", { name: /^import$/i }).click();

    const body = await importPromise;
    expect(body.bundle).toBe(MOCK_BUNDLE);
    expect(body.passphrase).toBe("shared-secret");

    await app.close();
  });

  test("import: shows success message after import completes", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /team sync/i }).click();
    await expect(page.getByText(/team intelligence sync/i)).toBeVisible({ timeout: 3_000 });

    await page.getByPlaceholder(/paste encrypted bundle/i).fill(MOCK_BUNDLE);
    await page.getByPlaceholder(/^passphrase$/i).fill("shared-secret");
    await page.getByRole("button", { name: /^import$/i }).click();

    await expect(page.getByText(/import complete/i)).toBeVisible({ timeout: 5_000 });

    await app.close();
  });

  test("export button is disabled until passphrase is filled", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /team sync/i }).click();
    await expect(page.getByText(/team intelligence sync/i)).toBeVisible({ timeout: 3_000 });

    const exportBtn = page.getByRole("button", { name: /^export$/i });
    await expect(exportBtn).toBeDisabled();

    await page.getByPlaceholder(/passphrase \(share separately\)/i).fill("abc");
    await expect(exportBtn).toBeEnabled();

    await app.close();
  });
});
