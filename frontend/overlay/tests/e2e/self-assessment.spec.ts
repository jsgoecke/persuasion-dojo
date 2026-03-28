/**
 * E2E: Self-assessment wizard
 *
 * Flow:
 *   idle → click "Self-assessment" → wizard (Likert items step-by-step)
 *        → micro-argument text → POST /self-assessment/submit
 *        → archetype result card
 *   ← Back returns to idle.
 *
 * Mock server handles:
 *   GET  /self-assessment/items → array of AssessmentItem
 *   POST /self-assessment/submit → AssessmentResult with archetype
 */

import { test, expect, _electron as electron } from "@playwright/test";
import { join } from "path";
import http from "http";
import type { IncomingMessage } from "http";

const OVERLAY_DIR = join(__dirname, "..", "..");
const MAIN_JS     = join(OVERLAY_DIR, "out", "main", "index.js");

// ── Mock server ────────────────────────────────────────────────────────────

const MOCK_ITEMS = [
  { id: "q1", statement: "I prefer data over stories.", scale: "agree_disagree" },
  { id: "q2", statement: "I energize rooms with ideas.", scale: "agree_disagree" },
];

const MOCK_RESULT = {
  archetype: "Architect",
  scores: { logic_narrative: 0.7, advocate_analyze: 0.6 },
  description: "You are systematic and data-driven.",
  strengths: ["Analytical", "Structured"],
  growth_areas: ["Storytelling"],
};

interface AssessmentMockServer {
  close(): Promise<void>;
  lastSubmit(): Promise<unknown>;
}

function startAssessmentMockServer(): AssessmentMockServer {
  let resolveSubmit!: (v: unknown) => void;
  let submitPromise = new Promise<unknown>(r => { resolveSubmit = r; });

  const server = http.createServer((req: IncomingMessage, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      if (req.method === "GET" && req.url === "/self-assessment/items") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(MOCK_ITEMS));
      } else if (req.method === "POST" && req.url === "/self-assessment/submit") {
        const body = JSON.parse(Buffer.concat(chunks).toString());
        resolveSubmit(body);
        submitPromise = new Promise(r => { resolveSubmit = r; });
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(MOCK_RESULT));
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
    lastSubmit: () => submitPromise,
  };
}

// ── Helpers ────────────────────────────────────────────────────────────────

async function bypassOnboarding(page: import("@playwright/test").Page): Promise<void> {
  await page.evaluate(() => localStorage.setItem("pdojo:onboarded", "1"));
  await page.reload();
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe("Self-assessment wizard", () => {
  let mock: AssessmentMockServer;

  test.beforeEach(() => { mock = startAssessmentMockServer(); });
  test.afterEach(async () => { await mock.close(); });

  test("clicking Self-assessment opens the wizard", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /self-assessment/i }).click();

    // First Likert statement should appear.
    await expect(page.getByText(/prefer data over stories/i)).toBeVisible({ timeout: 5_000 });

    await app.close();
  });

  test("back button from wizard returns to idle", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /self-assessment/i }).click();
    await expect(page.getByText(/prefer data over stories/i)).toBeVisible({ timeout: 5_000 });

    await page.getByRole("button", { name: /← back/i }).click();

    await expect(page.getByRole("button", { name: /start session/i })).toBeVisible({ timeout: 3_000 });

    await app.close();
  });

  test("answering all Likert items advances to the micro-argument step", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /self-assessment/i }).click();

    // Q1: click any response option.
    await expect(page.getByText(/prefer data over stories/i)).toBeVisible({ timeout: 5_000 });
    await page.getByRole("button", { name: /agree/i }).first().click();

    // Q2: next statement auto-advances.
    await expect(page.getByText(/energize rooms/i)).toBeVisible({ timeout: 3_000 });
    await page.getByRole("button", { name: /agree/i }).first().click();

    // Micro-argument step appears.
    await expect(page.getByPlaceholder(/write a brief argument/i)).toBeVisible({ timeout: 3_000 });

    await app.close();
  });

  test("submitting yields an archetype result card", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /self-assessment/i }).click();

    // Answer Q1 and Q2.
    await expect(page.getByText(/prefer data over stories/i)).toBeVisible({ timeout: 5_000 });
    await page.getByRole("button", { name: /agree/i }).first().click();
    await expect(page.getByText(/energize rooms/i)).toBeVisible({ timeout: 3_000 });
    await page.getByRole("button", { name: /agree/i }).first().click();

    // Fill micro-argument and submit.
    const textarea = page.getByPlaceholder(/write a brief argument/i);
    await expect(textarea).toBeVisible({ timeout: 3_000 });
    await textarea.fill("Data is the most reliable basis for any decision.");
    await page.getByRole("button", { name: /submit/i }).click();

    // Result card shows the archetype.
    await expect(page.getByText(/architect/i)).toBeVisible({ timeout: 5_000 });

    await app.close();
  });

  test("submit sends responses to POST /self-assessment/submit", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /self-assessment/i }).click();

    const submitPromise = mock.lastSubmit();

    await expect(page.getByText(/prefer data over stories/i)).toBeVisible({ timeout: 5_000 });
    await page.getByRole("button", { name: /agree/i }).first().click();
    await expect(page.getByText(/energize rooms/i)).toBeVisible({ timeout: 3_000 });
    await page.getByRole("button", { name: /disagree/i }).first().click();

    const textarea = page.getByPlaceholder(/write a brief argument/i);
    await expect(textarea).toBeVisible({ timeout: 3_000 });
    await textarea.fill("Structured thinking beats intuition.");
    await page.getByRole("button", { name: /submit/i }).click();

    const body = await submitPromise as { responses: unknown[]; micro_argument: string };
    expect(Array.isArray(body.responses)).toBe(true);
    expect(body.micro_argument).toBe("Structured thinking beats intuition.");

    await app.close();
  });
});
