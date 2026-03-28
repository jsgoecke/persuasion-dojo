/**
 * E2E: Settings pane
 *
 * The user opens Settings from the idle view, can see which keys are already
 * saved, update them, and navigate back.
 *
 * Mock server handles:
 *   GET  /settings → { anthropic_api_key_set, deepgram_api_key_set }
 *   POST /settings → 200
 */

import { test, expect, _electron as electron } from "@playwright/test";
import { join } from "path";
import http from "http";
import type { IncomingMessage } from "http";

const OVERLAY_DIR = join(__dirname, "..", "..");
const MAIN_JS     = join(OVERLAY_DIR, "out", "main", "index.js");

// ── Mock server ────────────────────────────────────────────────────────────

interface SettingsState {
  anthropic_api_key_set: boolean;
  deepgram_api_key_set: boolean;
}

interface SettingsMockServer {
  close(): Promise<void>;
  setState(s: SettingsState): void;
  lastPost(): Promise<Record<string, string>>;
}

function startSettingsMockServer(initial: SettingsState = { anthropic_api_key_set: false, deepgram_api_key_set: false }): SettingsMockServer {
  let state = { ...initial };
  let resolvePost!: (v: Record<string, string>) => void;
  let postPromise = new Promise<Record<string, string>>(r => { resolvePost = r; });

  const server = http.createServer((req: IncomingMessage, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      if (req.method === "GET" && req.url === "/settings") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(state));
      } else if (req.method === "POST" && req.url === "/settings") {
        const body = JSON.parse(Buffer.concat(chunks).toString()) as Record<string, string>;
        resolvePost(body);
        postPromise = new Promise(r => { resolvePost = r; });
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true }));
      } else {
        res.writeHead(404);
        res.end();
      }
    });
  });

  server.listen(8000);

  return {
    close: () => new Promise<void>((resolve, reject) =>
      server.close(err => (err ? reject(err) : resolve())),
    ),
    setState: s => { state = { ...s }; },
    lastPost: () => postPromise,
  };
}

// ── Helpers ────────────────────────────────────────────────────────────────

async function bypassOnboarding(page: import("@playwright/test").Page): Promise<void> {
  await page.evaluate(() => localStorage.setItem("pdojo:onboarded", "1"));
  await page.reload();
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe("Settings pane", () => {
  let mock: SettingsMockServer;

  test.beforeEach(() => { mock = startSettingsMockServer(); });
  test.afterEach(async () => { await mock.close(); });

  test("clicking Settings opens the settings pane", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /settings/i }).click();

    await expect(page.getByText(/settings/i)).toBeVisible({ timeout: 3_000 });
    await expect(page.getByRole("button", { name: /save/i })).toBeVisible();

    await app.close();
  });

  test("back button returns to idle view", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /settings/i }).click();
    await expect(page.getByRole("button", { name: /save/i })).toBeVisible({ timeout: 3_000 });

    await page.getByRole("button", { name: /← back/i }).click();

    await expect(page.getByRole("button", { name: /start session/i })).toBeVisible({ timeout: 3_000 });
    await expect(page.getByRole("button", { name: /save/i })).not.toBeVisible();

    await app.close();
  });

  test("save sends the API keys to POST /settings", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /settings/i }).click();
    await expect(page.getByRole("button", { name: /save/i })).toBeVisible({ timeout: 3_000 });

    const postPromise = mock.lastPost();

    // Fill in both password inputs (order: anthropic then deepgram).
    const inputs = page.locator("input[type='password']");
    await inputs.nth(0).fill("sk-ant-test-key");
    await inputs.nth(1).fill("dg-test-key");
    await page.getByRole("button", { name: /save/i }).click();

    const body = await postPromise;
    expect(body.anthropic_api_key).toBe("sk-ant-test-key");
    expect(body.deepgram_api_key).toBe("dg-test-key");

    await app.close();
  });

  test("keys already set shows a 'set' badge on load", async () => {
    mock.setState({ anthropic_api_key_set: true, deepgram_api_key_set: true });

    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /settings/i }).click();

    // At least one "set" indicator should be visible.
    await expect(page.getByText(/set/i).first()).toBeVisible({ timeout: 3_000 });

    await app.close();
  });
});
