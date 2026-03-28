import { defineConfig } from "@playwright/test";
import { join } from "path";

/**
 * Playwright E2E configuration for the Electron overlay.
 *
 * Uses @playwright/test directly — Electron is launched programmatically
 * inside the spec via electron-playwright-helpers, so the config sets
 * project-level settings only (no `use.baseURL`, no browser channel).
 *
 * Run: npx playwright test  (or `npm run test:e2e`)
 */
export default defineConfig({
  testDir: join(__dirname, "tests", "e2e"),
  timeout: 30_000,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  // Electron tests share port 8000 for mock servers — run files serially.
  workers: 1,
  use: {
    // Screenshots on failure help diagnose Electron UI issues.
    screenshot: "only-on-failure",
  },
});
