/**
 * Sentry renderer-process utilities.
 *
 * Import this module once from main.tsx before rendering anything.
 * Subsequent imports are no-ops (Sentry SDK is a singleton).
 */
import * as Sentry from "@sentry/electron/renderer";
import { init as reactInit } from "@sentry/react";

export function initSentry(): void {
  Sentry.init(
    {
      // DSN injected at build time via Vite define (absent in local dev).
      dsn: import.meta.env.VITE_SENTRY_DSN,
      environment: import.meta.env.MODE,
      // Release is set on the main-process side; the renderer inherits it
      // automatically via the @sentry/electron IPC bridge.

      // Only send events in production.
      enabled: import.meta.env.PROD,

      // Capture 10% of sessions for performance monitoring.
      tracesSampleRate: 0.1,
    },
    // Pass React-specific init so the ErrorBoundary and hooks work.
    reactInit,
  );
}

// Re-export for convenience so callers only need to import from this module.
export { Sentry };
