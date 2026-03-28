/**
 * E2E: Sparring partner mode
 *
 * Practice mode lets the user spar against an AI opponent without a live
 * meeting. The flow is:
 *   idle → click "Practice (Sparring)" → SetupView (archetype + scenario)
 *       → click "Start sparring →"
 *       → POST /sparring/sessions → WebSocket /ws/sparring/{id}
 *       → ActiveView (chat log + input)
 *       → user sends a turn → server streams opponent response
 *       → EndedView after sparring_ended message
 *   ← Back returns to idle from SetupView.
 *
 * The mock server handles:
 *   POST /sparring/sessions → {"session_id": "sparring-1"}
 *   WS   /ws/sparring/sparring-1 → controlled by sparringSocket promise
 *
 * Onboarding is bypassed by setting localStorage before each test.
 */

import { test, expect, _electron as electron } from "@playwright/test";
import { join } from "path";
import http from "http";
import type { IncomingMessage } from "http";

// eslint-disable-next-line @typescript-eslint/no-require-imports
const WebSocketServer = require("ws").WebSocketServer as typeof import("ws").WebSocketServer;

const OVERLAY_DIR = join(__dirname, "..", "..");
const MAIN_JS     = join(OVERLAY_DIR, "out", "main", "index.js");

// ── Mock server ────────────────────────────────────────────────────────────

interface SparringMockServer {
  close(): Promise<void>;
  /** Resolves with the WS once the app connects for a sparring session. */
  sparringSocket: Promise<import("ws").WebSocket>;
}

function startSparringMockServer(sessionId = "sparring-1"): SparringMockServer {
  const httpServer = http.createServer((req: IncomingMessage, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      if (req.method === "POST" && req.url === "/sparring/sessions") {
        res.writeHead(201, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ session_id: sessionId }));
      } else {
        // absorb coaching /sessions or any other stray request
        res.writeHead(404);
        res.end();
      }
    });
  });

  const wss = new WebSocketServer({ server: httpServer });

  let resolveSocket!: (ws: import("ws").WebSocket) => void;
  const sparringSocket = new Promise<import("ws").WebSocket>(resolve => {
    resolveSocket = resolve;
  });

  wss.on("connection", (ws: import("ws").WebSocket, req: IncomingMessage) => {
    if (req.url?.startsWith("/ws/sparring/")) {
      resolveSocket(ws);
    }
  });

  httpServer.listen(8000);

  return {
    close: () =>
      new Promise<void>((resolve, reject) => {
        wss.close(() => {
          httpServer.close(err => (err ? reject(err) : resolve()));
        });
      }),
    sparringSocket,
  };
}

// ── Fixtures ───────────────────────────────────────────────────────────────

function opponentChunk(text: string, turnNumber = 1): string {
  return JSON.stringify({
    type:         "sparring_turn",
    role:         "opponent",
    text,
    turn_number:  turnNumber,
    is_final:     false,
    coaching_tip: "",
  });
}

function opponentFinal(text: string, turnNumber = 1): string {
  return JSON.stringify({
    type:         "sparring_turn",
    role:         "opponent",
    text,
    turn_number:  turnNumber,
    is_final:     true,
    coaching_tip: "Lead with a concrete number next time.",
  });
}

function coachingTurn(tip: string, turnNumber = 1): string {
  return JSON.stringify({
    type:         "sparring_turn",
    role:         "coaching",
    text:         tip,
    turn_number:  turnNumber,
    is_final:     true,
    coaching_tip: tip,
  });
}

function sparringEnded(turns = 2): string {
  return JSON.stringify({ type: "sparring_ended", turns });
}

// ── Helpers ────────────────────────────────────────────────────────────────

async function bypassOnboarding(page: import("@playwright/test").Page): Promise<void> {
  await page.evaluate(() => localStorage.setItem("pdojo:onboarded", "1"));
  await page.reload();
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe("Sparring mode", () => {
  let mock: SparringMockServer;

  test.beforeEach(() => { mock = startSparringMockServer(); });
  test.afterEach(async () => { await mock.close(); });

  test("clicking Practice (Sparring) opens the setup view", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /practice.*sparring/i }).click();

    await expect(page.getByText(/practice mode/i)).toBeVisible({ timeout: 3_000 });
    await expect(page.getByText(/your archetype/i)).toBeVisible();
    await expect(page.getByText(/opponent plays/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /start sparring/i })).toBeVisible();

    await app.close();
  });

  test("back button from setup returns to idle view", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /practice.*sparring/i }).click();
    await expect(page.getByText(/practice mode/i)).toBeVisible({ timeout: 3_000 });

    await page.getByRole("button", { name: /← back/i }).click();

    await expect(page.getByRole("button", { name: /start session/i })).toBeVisible({ timeout: 3_000 });
    await expect(page.getByText(/practice mode/i)).not.toBeVisible();

    await app.close();
  });

  test("scenario dropdown contains presets and custom option", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /practice.*sparring/i }).click();

    const scenarioSelect = page.locator("select").nth(2); // third select = scenario
    const options = await scenarioSelect.locator("option").allTextContents();
    expect(options.some(o => /roadmap/i.test(o))).toBe(true);
    expect(options.some(o => /budget/i.test(o))).toBe(true);
    expect(options.some(o => /custom/i.test(o))).toBe(true);

    await app.close();
  });

  test("happy-path: start → opponent streams → coaching tip → ended view", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    // Enter sparring setup.
    await page.getByRole("button", { name: /practice.*sparring/i }).click();
    await expect(page.getByRole("button", { name: /start sparring/i })).toBeVisible({ timeout: 3_000 });

    // Click Start — triggers POST /sparring/sessions + WebSocket connect.
    await page.getByRole("button", { name: /start sparring/i }).click();

    // Wait for the WebSocket to connect on the server side.
    const ws = await mock.sparringSocket;

    // Server streams an opponent turn in two chunks then finalises.
    ws.send(opponentChunk("I'm not convinced — "));
    ws.send(opponentFinal("I'm not convinced — show me the data.", 1));
    ws.send(coachingTurn("Lead with a concrete number next time.", 1));

    // Opponent final text must appear in the chat log.
    await expect(
      page.getByText("I'm not convinced — show me the data."),
    ).toBeVisible({ timeout: 5_000 });

    // Coaching tip must appear.
    await expect(page.getByText(/lead with a concrete number/i)).toBeVisible({ timeout: 3_000 });

    // End the session — server confirms sparring_ended.
    ws.send(sparringEnded(2));
    await expect(page.getByText(/sparring complete/i)).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(/2 turn/i)).toBeVisible();

    await app.close();
  });

  test("user turn is sent over WebSocket when Enter is pressed", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /practice.*sparring/i }).click();
    await page.getByRole("button", { name: /start sparring/i }).click();

    const ws = await mock.sparringSocket;

    // Send an initial opponent turn so the input is accessible.
    ws.send(opponentFinal("Your opening move?", 1));
    await expect(page.getByText("Your opening move?")).toBeVisible({ timeout: 5_000 });

    // Capture the next WebSocket message before typing.
    const receivedMsg = new Promise<Record<string, unknown>>(resolve => {
      ws.once("message", (data: import("ws").RawData) => {
        resolve(JSON.parse(data.toString()) as Record<string, unknown>);
      });
    });

    // Type a reply and press Enter.
    const input = page.locator("input[placeholder]").last();
    await input.fill("Here are the numbers you asked for.");
    await input.press("Enter");

    const msg = await receivedMsg;
    expect(msg.type).toBe("user_turn");
    expect(msg.text).toBe("Here are the numbers you asked for.");

    await app.close();
  });

  test("practice again button resets to setup view", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /practice.*sparring/i }).click();
    await page.getByRole("button", { name: /start sparring/i }).click();

    const ws = await mock.sparringSocket;
    ws.send(sparringEnded(1));

    await expect(page.getByText(/sparring complete/i)).toBeVisible({ timeout: 5_000 });

    // "Practice again" should reset back to the setup view.
    await page.getByRole("button", { name: /practice again/i }).click();
    await expect(page.getByText(/practice mode/i)).toBeVisible({ timeout: 3_000 });
    await expect(page.getByRole("button", { name: /start sparring/i })).toBeVisible();

    await app.close();
  });

  test("back to coaching from ended view returns to idle", async () => {
    const app  = await electron.launch({ args: [MAIN_JS] });
    const page = await app.firstWindow();
    await bypassOnboarding(page);

    await page.getByRole("button", { name: /practice.*sparring/i }).click();
    await page.getByRole("button", { name: /start sparring/i }).click();

    const ws = await mock.sparringSocket;
    ws.send(sparringEnded(1));

    await expect(page.getByText(/sparring complete/i)).toBeVisible({ timeout: 5_000 });

    await page.getByRole("button", { name: /back to coaching/i }).click();
    await expect(page.getByRole("button", { name: /start session/i })).toBeVisible({ timeout: 3_000 });

    await app.close();
  });
});
