/**
 * E2E: Pre-seed participants mode
 *
 * Before a meeting the user can classify participants by pasting free-form
 * text (LinkedIn bio, email, notes). The flow is:
 *
 *   idle → click "Pre-seed participants" → PreSeedPane (form)
 *       → fill name + text → click "Classify" (or ⌘↵)
 *       → POST /participants/pre-seed → result row with archetype
 *       → click "why?" → reasoning expands
 *   ← Back returns to idle.
 *
 * The mock server handles:
 *   POST /participants/pre-seed → {archetype, confidence, reasoning}
 *   Everything else → 404
 *
 * Onboarding is bypassed via localStorage before each test.
 */

import { test, expect, _electron as electron } from "@playwright/test";
import { join } from "path";
import http from "http";
import type { IncomingMessage } from "http";

const OVERLAY_DIR = join(__dirname, "..", "..");
const MAIN_JS     = join(OVERLAY_DIR, "out", "main", "index.js");

// ── Mock server ────────────────────────────────────────────────────────────

interface PreSeedResponse {
  archetype:   string;
  confidence:  number;
  reasoning:   string;
}

interface PreSeedMockServer {
  close(): Promise<void>;
  /** Call this to set the next response the mock will return. */
  setResponse(r: PreSeedResponse): void;
  /** The last request body received by the mock. */
  lastRequest(): Promise<{ name: string; text: string }>;
}

function startPreSeedMockServer(): PreSeedMockServer {
  let nextResponse: PreSeedResponse = {
    archetype:  "Inquisitor",
    confidence: 0.82,
    reasoning:  "Frequent use of 'why' and 'show me the data' language.",
  };

  let resolveLastReq!: (v: { name: string; text: string }) => void;
  let lastReqPromise = new Promise<{ name: string; text: string }>(r => {
    resolveLastReq = r;
  });

  const server = http.createServer((req: IncomingMessage, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      if (req.method === "POST" && req.url === "/participants/pre-seed") {
        const body = JSON.parse(Buffer.concat(chunks).toString()) as { name: string; text: string };
        resolveLastReq(body);
        // Reset for the next call.
        lastReqPromise = new Promise(r => { resolveLastReq = r; });
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(nextResponse));
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
    setResponse: r => { nextResponse = r; },
    lastRequest: () => lastReqPromise,
  };
}

// ── Helpers ────────────────────────────────────────────────────────────────

async function bypassOnboarding(page: import("@playwright/test").Page): Promise<void> {
  await page.evaluate(() => localStorage.setItem("pdojo:onboarded", "1"));
  await page.reload();
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe("Pre-seed participants mode", () => {
  let mock: PreSeedMockServer;

  test.beforeEach(() => { mock = startPreSeedMockServer(); });
  test.afterEach(async () => { await mock.close(); });

  test("clicking Pre-seed participants opens the form", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /pre-seed participants/i }).click();

    await expect(page.getByText(/pre-seed participants/i)).toBeVisible({ timeout: 3_000 });
    await expect(page.getByPlaceholder(/sarah chen/i)).toBeVisible();
    await expect(page.getByPlaceholder(/linkedin bio/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /classify/i })).toBeVisible();

    await app.close();
  });

  test("back button returns to idle view", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /pre-seed participants/i }).click();
    await expect(page.getByRole("button", { name: /classify/i })).toBeVisible({ timeout: 3_000 });

    await page.getByRole("button", { name: /← back/i }).click();

    await expect(page.getByRole("button", { name: /start session/i })).toBeVisible({ timeout: 3_000 });
    await expect(page.getByRole("button", { name: /classify/i })).not.toBeVisible();

    await app.close();
  });

  test("classify: result row appears with archetype and confidence", async () => {
    mock.setResponse({ archetype: "Inquisitor", confidence: 0.82, reasoning: "Uses data-first language." });

    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /pre-seed participants/i }).click();

    await page.getByPlaceholder(/sarah chen/i).fill("Sarah Chen");
    await page.getByPlaceholder(/linkedin bio/i).fill("VP Engineering. Demands evidence before any decision.");
    await page.getByRole("button", { name: /classify/i }).click();

    // Result row: participant name visible.
    await expect(page.getByText("Sarah Chen")).toBeVisible({ timeout: 5_000 });
    // Archetype label visible.
    await expect(page.getByText("Inquisitor")).toBeVisible();
    // Confidence label: 0.82 → "High".
    await expect(page.getByText(/high confidence/i)).toBeVisible();

    await app.close();
  });

  test("classify: request body contains the submitted name and text", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /pre-seed participants/i }).click();

    const reqPromise = mock.lastRequest();

    await page.getByPlaceholder(/sarah chen/i).fill("Alex Kim");
    await page.getByPlaceholder(/linkedin bio/i).fill("Consensus-builder. Always asks for everyone's input.");
    await page.getByRole("button", { name: /classify/i }).click();

    const body = await reqPromise;
    expect(body.name).toBe("Alex Kim");
    expect(body.text).toContain("consensus-builder");

    await app.close();
  });

  test("why? button expands and collapses the reasoning", async () => {
    mock.setResponse({
      archetype:  "Bridge Builder",
      confidence: 0.75,
      reasoning:  "Emphasises group alignment and shared outcomes.",
    });

    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /pre-seed participants/i }).click();

    await page.getByPlaceholder(/sarah chen/i).fill("Jordan Lee");
    await page.getByPlaceholder(/linkedin bio/i).fill("Always wants everyone on board first.");
    await page.getByRole("button", { name: /classify/i }).click();

    await expect(page.getByText("Jordan Lee")).toBeVisible({ timeout: 5_000 });

    // Reasoning should be hidden initially.
    await expect(page.getByText(/group alignment/i)).not.toBeVisible();

    // Expand.
    await page.getByRole("button", { name: /why\?/i }).click();
    await expect(page.getByText(/group alignment/i)).toBeVisible({ timeout: 2_000 });

    // Collapse.
    await page.getByRole("button", { name: /hide/i }).click();
    await expect(page.getByText(/group alignment/i)).not.toBeVisible();

    await app.close();
  });

  test("multiple participants can be classified in sequence", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /pre-seed participants/i }).click();

    // First participant.
    mock.setResponse({ archetype: "Architect", confidence: 0.9, reasoning: "Systematic thinker." });
    await page.getByPlaceholder(/sarah chen/i).fill("Dana Park");
    await page.getByPlaceholder(/linkedin bio/i).fill("Systems architect. Data-driven.");
    await page.getByRole("button", { name: /classify/i }).click();
    await expect(page.getByText("Dana Park")).toBeVisible({ timeout: 5_000 });

    // Second participant — form should reset after the first submission.
    mock.setResponse({ archetype: "Firestarter", confidence: 0.78, reasoning: "Vision-driven energy." });
    await page.getByPlaceholder(/sarah chen/i).fill("Morgan Reyes");
    await page.getByPlaceholder(/linkedin bio/i).fill("Loves big bold ideas.");
    await page.getByRole("button", { name: /classify/i }).click();
    await expect(page.getByText("Morgan Reyes")).toBeVisible({ timeout: 5_000 });

    // Both results should be in the list.
    await expect(page.getByText("Dana Park")).toBeVisible();
    await expect(page.getByText("Morgan Reyes")).toBeVisible();
    await expect(page.getByText("Architect")).toBeVisible();
    await expect(page.getByText("Firestarter")).toBeVisible();

    await app.close();
  });

  test("classify button is disabled until both fields are filled", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /pre-seed participants/i }).click();

    const btn = page.getByRole("button", { name: /classify/i });

    // Both empty → disabled.
    await expect(btn).toBeDisabled();

    // Name only → still disabled.
    await page.getByPlaceholder(/sarah chen/i).fill("Someone");
    await expect(btn).toBeDisabled();

    // Both filled → enabled.
    await page.getByPlaceholder(/linkedin bio/i).fill("Some bio.");
    await expect(btn).toBeEnabled();

    await app.close();
  });
});
