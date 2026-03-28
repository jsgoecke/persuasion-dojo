/**
 * E2E: Retro import pane
 *
 * Flow:
 *   idle → click "Retro Analysis" → RetroImportPane
 *        → pick audio file → click "Analyze Recording"
 *        → POST /retro/upload → { job_id }
 *        → polls GET /retro/jobs/{job_id} until done
 *        → renders utterance transcript
 *   ← Back returns to idle.
 *
 * Mock server handles:
 *   POST /retro/upload  → { job_id: "job-123" }
 *   GET  /retro/jobs/job-123 → job status (pending → done)
 */

import { test, expect, _electron as electron } from "@playwright/test";
import { join } from "path";
import http from "http";
import type { IncomingMessage } from "http";

const OVERLAY_DIR = join(__dirname, "..", "..");
const MAIN_JS     = join(OVERLAY_DIR, "out", "main", "index.js");

// ── Mock server ────────────────────────────────────────────────────────────

interface RetroMockServer {
  close(): Promise<void>;
  /** Advance job to "done" with utterances. */
  completeJob(): void;
  /** Set job to "error" state. */
  failJob(error: string): void;
}

function startRetroMockServer(): RetroMockServer {
  const JOB_ID = "job-e2e-123";
  let jobStatus: "pending" | "processing" | "done" | "error" = "pending";
  let jobError: string | undefined;

  const DONE_UTTERANCES = [
    { speaker_id: "speaker_0", text: "We need more data before deciding.", start: 0 },
    { speaker_id: "speaker_1", text: "I agree — let's run a quick pilot.",   start: 8.5 },
  ];

  const server = http.createServer((req: IncomingMessage, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      const url = req.url ?? "";

      if (req.method === "POST" && url === "/retro/upload") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ job_id: JOB_ID }));
      } else if (req.method === "GET" && url === `/retro/jobs/${JOB_ID}`) {
        const body: Record<string, unknown> = {
          status: jobStatus,
          progress: jobStatus === "done" ? DONE_UTTERANCES.length : 0,
          total:    DONE_UTTERANCES.length,
        };
        if (jobStatus === "done") body.utterances = DONE_UTTERANCES;
        if (jobStatus === "error") body.error = jobError ?? "unknown error";
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(body));
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
    completeJob: () => { jobStatus = "done"; },
    failJob:     (error) => { jobStatus = "error"; jobError = error; },
  };
}

// ── Helpers ────────────────────────────────────────────────────────────────

async function bypassOnboarding(page: import("@playwright/test").Page): Promise<void> {
  await page.evaluate(() => localStorage.setItem("pdojo:onboarded", "1"));
  await page.reload();
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe("Retro import pane", () => {
  let mock: RetroMockServer;

  test.beforeEach(() => { mock = startRetroMockServer(); });
  test.afterEach(async () => { await mock.close(); });

  test("clicking Retro Analysis opens the pane", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /retro analysis/i }).click();

    await expect(page.getByText(/retro analysis/i)).toBeVisible({ timeout: 3_000 });
    await expect(page.getByRole("button", { name: /choose audio file/i })).toBeVisible();

    await app.close();
  });

  test("back button returns to idle view", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /retro analysis/i }).click();
    await expect(page.getByText(/retro analysis/i)).toBeVisible({ timeout: 3_000 });

    await page.getByRole("button", { name: /← back/i }).click();

    await expect(page.getByRole("button", { name: /start session/i })).toBeVisible({ timeout: 3_000 });

    await app.close();
  });

  test("Analyze Recording button is disabled until a file is chosen", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /retro analysis/i }).click();
    await expect(page.getByRole("button", { name: /analyze recording/i })).toBeVisible({ timeout: 3_000 });

    await expect(page.getByRole("button", { name: /analyze recording/i })).toBeDisabled();

    await app.close();
  });

  test("after upload: shows utterance transcript when job completes", async () => {
    // Job is "done" immediately on first poll.
    mock.completeJob();

    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /retro analysis/i }).click();
    await expect(page.getByText(/retro analysis/i)).toBeVisible({ timeout: 3_000 });

    // Inject a File into the hidden file input so the Analyze button enables.
    await page.evaluate(() => {
      const input = document.querySelector("input[type='file']") as HTMLInputElement;
      const blob = new Blob(["RIFF...."], { type: "audio/wav" });
      const file = new File([blob], "meeting.wav", { type: "audio/wav" });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    await expect(page.getByRole("button", { name: /analyze recording/i })).toBeEnabled({ timeout: 2_000 });
    await page.getByRole("button", { name: /analyze recording/i }).click();

    // Poll resolves quickly — transcript should appear.
    await expect(page.getByText(/we need more data/i)).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(/speaker_0/i)).toBeVisible();

    await app.close();
  });

  test("job error state shows error message", async () => {
    mock.failJob("Deepgram key missing");

    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /retro analysis/i }).click();
    await expect(page.getByText(/retro analysis/i)).toBeVisible({ timeout: 3_000 });

    await page.evaluate(() => {
      const input = document.querySelector("input[type='file']") as HTMLInputElement;
      const blob = new Blob(["RIFF...."], { type: "audio/wav" });
      const file = new File([blob], "meeting.wav", { type: "audio/wav" });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    await expect(page.getByRole("button", { name: /analyze recording/i })).toBeEnabled({ timeout: 2_000 });
    await page.getByRole("button", { name: /analyze recording/i }).click();

    await expect(page.getByText(/analysis failed/i)).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(/deepgram key missing/i)).toBeVisible();

    await app.close();
  });
});
