import { defineConfig, externalizeDepsPlugin } from "electron-vite";
import react from "@vitejs/plugin-react";
import { sentryVitePlugin } from "@sentry/vite-plugin";

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      sourcemap: true,
      lib: {
        entry: "src/main/index.ts",
      },
    },
  },

  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      sourcemap: true,
      rollupOptions: {
        input: "src/preload/index.ts",
      },
    },
  },

  renderer: {
    plugins: [
      react(),
      // Upload source maps to Sentry during production builds.
      // Skipped when SENTRY_AUTH_TOKEN is absent (local dev, CI without secrets).
      ...(process.env.SENTRY_AUTH_TOKEN
        ? [
            sentryVitePlugin({
              org: process.env.SENTRY_ORG,
              project: process.env.SENTRY_PROJECT,
              authToken: process.env.SENTRY_AUTH_TOKEN,
              sourcemaps: {
                assets: "./out/renderer/**",
                ignore: ["node_modules"],
              },
            }),
          ]
        : []),
    ],
    build: {
      sourcemap: true,
    },
  },
});
