---
title: Running the Frontend Overlay
description: Install npm dependencies, run the Electron overlay in dev, and produce a signed .dmg via electron-builder.
tags: [guide, getting-started, stack/electron, stack/react]
type: guide
related:
  - "[[First-Time Setup]]"
  - "[[Electron Main Process]]"
  - "[[React Renderer]]"
  - "[[Build and Package]]"
updated: 2026-04-19
---

# Running the Frontend Overlay

## Development

```bash
cd frontend/overlay
npm install
npm run dev
```

- Spawns a Vite dev server + an Electron window with hot reload.
- The renderer expects the backend at `http://localhost:8000` — start it first (see [[Running the Backend]]).
- If you have a Swift binary built, Electron will spawn it when the user clicks **Go Live**. See [[Audio Lifecycle and Supervision]].

## Production build

```bash
npm run build     # electron-vite build → out/
npm run package   # electron-builder → dist/*.dmg
```

`npm run package` runs through `electron-builder`:

1. Bundles React + Vite output (`out/`).
2. Copies the [[AudioCapture Binary]] from `swift/AudioCapture/.build/release/AudioCapture` into the app bundle at `bin/AudioCapture` (via `extraResources` in `electron-builder.json`).
3. Code-signs (macOS) and then calls `notarize.cjs` via the `afterSign` hook. See [[Release Pipeline]].
4. Produces `dist/Persuasion Dojo-<version>.dmg` for both arm64 and x64.

## Scripts (package.json)

| script | purpose |
|---|---|
| `dev` | `electron-vite dev` — dev server + Electron with HMR |
| `build` | `electron-vite build` — main + preload + renderer → `out/` |
| `preview` | `electron-vite preview` |
| `package` | `npm run build && electron-builder` — produce .dmg |
| `test` | `vitest run` — [[Frontend Tests|Vitest]] unit tests |
| `test:watch` | `vitest` in watch mode |
| `test:e2e` | `playwright test` — [[Frontend Tests|Playwright-for-Electron]] E2E |

## Entitlements

The built app requests:

- `NSMicrophoneUsageDescription` — microphone
- `NSScreenCaptureDescription` — screen recording (required for ScreenCaptureKit)

Users grant these in **System Settings → Privacy & Security** on first run.

## Next

→ [[Electron Main Process]]
→ [[React Renderer]]
→ [[Build and Package]]
