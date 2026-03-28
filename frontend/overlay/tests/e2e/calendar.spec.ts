/**
 * E2E: Calendar pane
 *
 * Flow (connected):
 *   idle → click "Calendar" → CalendarPane → meetings list
 *        → "Pre-seed attendees →" → switches to PreSeedPane
 *
 * Flow (not connected):
 *   GET /calendar/status → { configured: true, connected: false }
 *   → GET /calendar/auth-url → opens OAuth URL in system browser
 *   → Google redirects to /calendar/callback → backend exchanges code
 *   → Frontend polls /calendar/status until connected
 *
 * Mock server handles:
 *   GET /calendar/status → { configured, connected }
 *   GET /calendar/meetings?hours_ahead=48 → Meeting[] | 400 | 503
 *   GET /calendar/auth-url → { url: "https://…" }
 *   GET /calendar/callback?code=… → HTML success page
 */

import { test, expect, _electron as electron } from "@playwright/test";
import { join } from "path";
import http from "http";
import type { IncomingMessage } from "http";

const OVERLAY_DIR = join(__dirname, "..", "..");
const MAIN_JS     = join(OVERLAY_DIR, "out", "main", "index.js");

// ── Mock server ────────────────────────────────────────────────────────────

interface Meeting {
  id: string;
  title: string;
  start: string;
  attendees: string[];
}

type CalendarMode = "connected" | "not_connected" | "not_configured";

interface CalendarMockServer {
  close(): Promise<void>;
  setMode(m: CalendarMode): void;
  setMeetings(meetings: Meeting[]): void;
}

function startCalendarMockServer(): CalendarMockServer {
  let mode: CalendarMode = "not_connected";
  let meetings: Meeting[] = [
    {
      id: "evt1",
      title: "Q4 Planning",
      start: new Date(Date.now() + 3_600_000).toISOString(),
      attendees: ["alice@co.com", "bob@co.com"],
    },
  ];

  const server = http.createServer((req: IncomingMessage, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      const url = req.url ?? "";

      if (req.method === "GET" && url.startsWith("/calendar/status")) {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({
          configured: mode !== "not_configured",
          connected: mode === "connected",
        }));
      } else if (req.method === "GET" && url.startsWith("/calendar/meetings")) {
        if (mode === "not_connected") {
          res.writeHead(400); res.end();
        } else if (mode === "not_configured") {
          res.writeHead(503); res.end();
        } else {
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify(meetings));
        }
      } else if (req.method === "GET" && url.startsWith("/calendar/auth-url")) {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ url: "https://accounts.google.com/oauth?mock=1" }));
      } else if (req.method === "GET" && url.startsWith("/calendar/callback")) {
        res.writeHead(200, { "Content-Type": "text/html" });
        res.end("<html><body><h2>Connected!</h2></body></html>");
      } else if (req.method === "POST" && url.startsWith("/calendar/disconnect")) {
        mode = "not_connected";
        res.writeHead(204); res.end();
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
    setMode: m => { mode = m; },
    setMeetings: m => { meetings = m; },
  };
}

// ── Helpers ────────────────────────────────────────────────────────────────

async function bypassOnboarding(page: import("@playwright/test").Page): Promise<void> {
  await page.evaluate(() => localStorage.setItem("pdojo:onboarded", "1"));
  await page.reload();
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe("Calendar pane", () => {
  let mock: CalendarMockServer;

  test.beforeEach(() => { mock = startCalendarMockServer(); });
  test.afterEach(async () => { await mock.close(); });

  test("clicking Calendar opens the calendar pane", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /calendar/i }).click();

    await expect(page.getByText(/google calendar/i)).toBeVisible({ timeout: 3_000 });

    await app.close();
  });

  test("back button returns to idle view", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /calendar/i }).click();
    await expect(page.getByText(/google calendar/i)).toBeVisible({ timeout: 3_000 });

    await page.getByRole("button", { name: /← back/i }).click();

    await expect(page.getByRole("button", { name: /start session/i })).toBeVisible({ timeout: 3_000 });

    await app.close();
  });

  test("not connected: shows Connect Google Calendar button", async () => {
    mock.setMode("not_connected");

    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /calendar/i }).click();

    await expect(page.getByRole("button", { name: /connect google calendar/i })).toBeVisible({ timeout: 5_000 });

    await app.close();
  });

  test("connected: shows meetings list", async () => {
    mock.setMode("connected");
    mock.setMeetings([
      { id: "m1", title: "Board Review", start: new Date(Date.now() + 3_600_000).toISOString(), attendees: ["ceo@co.com"] },
    ]);

    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /calendar/i }).click();

    await expect(page.getByText("Board Review")).toBeVisible({ timeout: 5_000 });

    await app.close();
  });

  test("connected: Pre-seed attendees button switches to pre-seed pane", async () => {
    mock.setMode("connected");
    mock.setMeetings([
      { id: "m1", title: "Strategy Sync", start: new Date(Date.now() + 3_600_000).toISOString(), attendees: ["sarah@co.com", "tom@co.com"] },
    ]);

    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /calendar/i }).click();
    await expect(page.getByText("Strategy Sync")).toBeVisible({ timeout: 5_000 });

    await page.getByRole("button", { name: /pre-seed attendees/i }).click();

    // Should switch to the Pre-seed pane.
    await expect(page.getByPlaceholder(/sarah chen/i)).toBeVisible({ timeout: 3_000 });

    await app.close();
  });

  test("not configured: shows configuration error message", async () => {
    mock.setMode("not_configured");

    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /calendar/i }).click();

    await expect(page.getByText(/google oauth credentials/i)).toBeVisible({ timeout: 5_000 });

    await app.close();
  });
});
