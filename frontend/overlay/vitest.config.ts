import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
  },
  define: {
    "import.meta.env.PROD": false,
    "import.meta.env.MODE": JSON.stringify("test"),
    "import.meta.env.VITE_SENTRY_DSN": JSON.stringify(""),
  },
});
