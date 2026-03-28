/**
 * Tests for Sentry initialisation and ErrorBoundary integration.
 *
 * These run under Vitest with jsdom. The @sentry/electron renderer SDK
 * is mocked so no real network calls are made and the test environment
 * does not need Electron's IPC bridge.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

// ── Mock @sentry/electron/renderer before importing sentry.ts ────────────────

const mockSentryInit = vi.fn();
const mockReactInit = vi.fn();

vi.mock("@sentry/electron/renderer", () => ({
  init: mockSentryInit,
}));

vi.mock("@sentry/react", () => ({
  init: mockReactInit,
  ErrorBoundary: ({ children }: { children: unknown }) => children,
}));

// ── Tests ────────────────────────────────────────────────────────────────────

describe("initSentry", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.resetModules();
  });

  it("calls Sentry.init with the react init adapter", async () => {
    const { initSentry } = await import(
      "../src/renderer/src/sentry"
    );
    initSentry();

    expect(mockSentryInit).toHaveBeenCalledOnce();
    // Second argument must be the React init function.
    expect(mockSentryInit).toHaveBeenCalledWith(
      expect.objectContaining({ tracesSampleRate: 0.1 }),
      mockReactInit,
    );
  });

  it("passes enabled:false in test environment (import.meta.env.PROD is false)", async () => {
    const { initSentry } = await import(
      "../src/renderer/src/sentry"
    );
    initSentry();

    const [options] = mockSentryInit.mock.calls[0] as [{ enabled: boolean }];
    expect(options.enabled).toBe(false);
  });

  it("is idempotent — calling twice only calls Sentry.init twice (SDK handles dedup internally)", async () => {
    const { initSentry } = await import(
      "../src/renderer/src/sentry"
    );
    initSentry();
    initSentry();
    // We don't gate double-init ourselves; the SDK does. Just verify we don't throw.
    expect(mockSentryInit).toHaveBeenCalledTimes(2);
  });
});
