/**
 * E2E: Session happy-path
 *
 * Exercises the full session lifecycle against a local mock backend:
 *   1. App launches → idle state (Start Session button visible)
 *   2. Click "Start Session" → POST /sessions creates session
 *   3. WebSocket connects → mock server sends coaching_prompt
 *   4. PromptCard renders with the prompt text
 *   5. Dismiss via button → prompt disappears
 *   6. Click "End Session" → ws message "session_end" sent
 *   7. Mock server replies "session_ended" → SessionEndCard renders
 *
 * The mock server listens on localhost:8000 so the app's hardcoded
 * API_BASE / WS_BASE URLs resolve naturally. Port must be free when the
 * test runs (CI kills any stray processes before the suite).
 *
 * Electron is launched against the pre-built out/main/index.js so no
 * dev-server is required. Run `npm run build` once before `npm run test:e2e`.
 */

import { test, expect, _electron as electron } from "@playwright/test";
import { join } from "path";
import http from "http";
import type { IncomingMessage } from "http";

// ── ws is a transitive dep (pulled in by the backend toolchain) ───────────
// We import dynamically to avoid TypeScript "no declaration file" errors
// while still getting full type safety where possible.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const WebSocketServer = require("ws").WebSocketServer as typeof import("ws").WebSocketServer;

// ── Paths ─────────────────────────────────────────────────────────────────

const OVERLAY_DIR = join(__dirname, "..", "..");
const MAIN_JS     = join(OVERLAY_DIR, "out", "main", "index.js");

// ── Mock server helpers ───────────────────────────────────────────────────

interface MockServer {
  close(): Promise<void>;
  /** WebSocket connection accepted after the app opens a session. */
  sessionSocket: Promise<import("ws").WebSocket>;
}

function startMockServer(sessionId = "test-session-1"): MockServer {
  const httpServer = http.createServer((req: IncomingMessage, res) => {
    const body: Buffer[] = [];
    req.on("data", (chunk: Buffer) => body.push(chunk));
    req.on("end", () => {
      if (req.method === "POST" && req.url === "/sessions") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ session_id: sessionId }));
      } else {
        res.writeHead(404);
        res.end();
      }
    });
  });

  const wss = new WebSocketServer({ server: httpServer });

  // Resolve once with the first WebSocket that connects.
  let resolveSocket!: (ws: import("ws").WebSocket) => void;
  const sessionSocket = new Promise<import("ws").WebSocket>((resolve) => {
    resolveSocket = resolve;
  });

  wss.on("connection", (ws: import("ws").WebSocket, req: IncomingMessage) => {
    if (req.url?.startsWith(`/ws/session/`)) {
      resolveSocket(ws);
    }
  });

  httpServer.listen(8000);

  return {
    close() {
      return new Promise<void>((resolve, reject) => {
        wss.close(() => {
          httpServer.close((err) => (err ? reject(err) : resolve()));
        });
      });
    },
    sessionSocket,
  };
}

// ── Fixtures ──────────────────────────────────────────────────────────────

const PROMPT_FIXTURE = {
  type: "coaching_prompt",
  layer: "audience",
  text: "Sarah is an Inquisitor — anchor your next point in a number.",
  is_fallback: false,
  triggered_by: "elm_detected",
  speaker_id: "speaker_0",
};

const SESSION_ENDED_FIXTURE = {
  type: "session_ended",
  session_id: "test-session-1",
  persuasion_score: 72,
  growth_score: 8,
  duration_seconds: 42,
  prompts_shown: 1,
};

// ── Tests ─────────────────────────────────────────────────────────────────

// Mark onboarding complete so sessions tests always see the overlay, not the wizard.
async function bypassOnboarding(page: import("@playwright/test").Page): Promise<void> {
  await page.evaluate(() => localStorage.setItem("pdojo:onboarded", "1"));
  await page.reload();
}

test.describe("Session lifecycle", () => {
  let mock: MockServer;

  test.beforeEach(async () => {
    mock = startMockServer();
  });

  test.afterEach(async () => {
    await mock.close();
  });

  test("idle state: start button is visible on launch", async () => {
    const app = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await expect(page.getByRole("button", { name: /start session/i })).toBeVisible();

    await app.close();
  });

  test("happy-path: start → prompt → dismiss → end → score card", async () => {
    const app = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    // 1. Start the session.
    await page.getByRole("button", { name: /start session/i }).click();

    // 2. Wait for the WebSocket to connect and send a coaching prompt.
    const ws = await mock.sessionSocket;
    ws.send(JSON.stringify(PROMPT_FIXTURE));

    // 3. PromptCard should show the prompt text.
    await expect(page.getByText(PROMPT_FIXTURE.text)).toBeVisible({ timeout: 5_000 });

    // 4. Dismiss the prompt.
    await page.getByRole("button", { name: /dismiss/i }).click();
    await expect(page.getByText(PROMPT_FIXTURE.text)).not.toBeVisible();

    // 5. End the session.
    await page.getByRole("button", { name: /end session/i }).click();

    // 6. Confirm the app sent session_end over the WebSocket.
    const sessionEndMsg = await new Promise<Record<string, unknown>>((resolve) => {
      ws.once("message", (data: import("ws").RawData) => {
        resolve(JSON.parse(data.toString()) as Record<string, unknown>);
      });
    });
    expect(sessionEndMsg.type).toBe("session_end");

    // 7. Backend replies with session_ended → SessionEndCard renders.
    ws.send(JSON.stringify(SESSION_ENDED_FIXTURE));
    await expect(page.getByText(/persuasion score/i)).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText("72")).toBeVisible();

    await app.close();
  });

  test("swift_restart_needed: app handles watchdog signal without crashing", async () => {
    const app = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /start session/i }).click();

    const ws = await mock.sessionSocket;

    // Simulate the Python silence watchdog firing.
    // The renderer must forward this to the main process via IPC without
    // crashing the app — we verify the window stays open and responsive.
    ws.send(JSON.stringify({ type: "swift_restart_needed" }));

    // Give the IPC round-trip time to complete, then verify the UI
    // is still functional (not crashed or frozen).
    await page.waitForTimeout(500);
    await expect(page.getByRole("button", { name: /end session/i })).toBeVisible();

    await app.close();
  });
});
