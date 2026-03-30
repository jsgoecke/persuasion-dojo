import { app, BrowserWindow, clipboard, dialog, globalShortcut, ipcMain, Menu, screen, shell } from "electron";
import { join } from "path";
import { readFileSync, writeFileSync } from "fs";
import { spawn, ChildProcess } from "child_process";
import { electronApp, optimizer, is } from "@electron-toolkit/utils";
import * as Sentry from "@sentry/electron/main";
import { autoUpdater } from "electron-updater";

// ── Persistent window position ────────────────────────────────────────────────
const PREFS_PATH = join(app.getPath("userData"), "window-prefs.json");

interface WindowPrefs {
  x?: number;
  y?: number;
}

function loadPrefs(): WindowPrefs {
  try {
    return JSON.parse(readFileSync(PREFS_PATH, "utf-8")) as WindowPrefs;
  } catch {
    return {};
  }
}

function savePrefs(prefs: WindowPrefs): void {
  try {
    writeFileSync(PREFS_PATH, JSON.stringify(prefs), "utf-8");
  } catch {
    // Non-critical — position just won't persist
  }
}

// ── Sentry (main process) ─────────────────────────────────────────────────────
// Initialise before any other code so unhandled exceptions are captured.
// DSN is read from the SENTRY_DSN env var injected at build time via vite
// define, so it is absent in local dev unless explicitly set.
Sentry.init({
  dsn: process.env.SENTRY_DSN,
  release: app.getVersion(),
  environment: is.dev ? "development" : "production",
  // Disable in dev so we don't pollute the Sentry project with noise.
  enabled: !is.dev,
});

// ── Swift audio capture binary ────────────────────────────────────────────────
//
// The AudioCapture binary:
//   - Creates /tmp/persuasion_audio.pipe (FIFO)
//   - Streams Int16 LE mono 16 kHz PCM to it via ScreenCaptureKit
//   - Exits 2 on Screen Recording permission denied
//   - Exits 0 on clean SIGTERM
//
// Electron owns the lifecycle: spawn on app ready, restart on watchdog signal,
// kill on quit.

const CAPTURE_BINARY = is.dev
  ? join(__dirname, "../../../../swift/AudioCapture/.build/debug/AudioCapture")
  : join(process.resourcesPath, "bin/AudioCapture");

let captureProcess: ChildProcess | null = null;

function notifyRenderer(channel: string, ...args: unknown[]): void {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, ...args);
  }
}

function killOrphanedCaptures(): void {
  // Kill any AudioCapture processes left over from a previous session.
  // This prevents two writers on the same FIFO which corrupts the audio stream.
  // Uses pgrep to find PIDs, then kills each individually. This avoids the race
  // where a blanket `pkill -f AudioCapture` could kill a newly spawned process.
  try {
    const pids = require("child_process")
      .execSync("pgrep -f AudioCapture", { encoding: "utf-8" })
      .trim()
      .split("\n")
      .filter((p: string) => p.length > 0);
    for (const pid of pids) {
      // Skip our own captureProcess if it somehow survived — we'll manage it directly
      if (captureProcess && String(captureProcess.pid) === pid) continue;
      try {
        process.kill(Number(pid), "SIGKILL");
        process.stderr.write(`[AudioCapture] killed orphan PID ${pid}\n`);
      } catch {
        // Already exited
      }
    }
  } catch {
    // No orphans found — expected on clean start
  }
  // Pipe cleanup is owned by AudioPipeReader (Python) — do not delete here.
  // Deleting the pipe from Electron races with AudioPipeReader creating it.
}

function spawnCapture(): void {
  if (captureProcess) return; // already running

  killOrphanedCaptures();
  process.stderr.write(`[AudioCapture] attempting to spawn: ${CAPTURE_BINARY}\n`);

  // Check if binary exists before trying to spawn.
  try {
    require("fs").accessSync(CAPTURE_BINARY, require("fs").constants.X_OK);
  } catch {
    process.stderr.write(
      `[AudioCapture] binary not found at ${CAPTURE_BINARY}\n`
    );
    notifyRenderer("audio:status", {
      type: "binary_missing",
      message:
        "Audio capture binary not found. Build it with: cd swift/AudioCapture && swift build",
    });
    return;
  }

  captureProcess = spawn(CAPTURE_BINARY, [], {
    stdio: ["ignore", "ignore", "pipe"],
  });

  captureProcess.on("error", (err) => {
    captureProcess = null;
    process.stderr.write(`[AudioCapture] spawn error: ${err.message}\n`);
    notifyRenderer("audio:status", {
      type: "binary_missing",
      message: `Audio capture failed to start: ${err.message}`,
    });
  });

  captureProcess.stderr?.on("data", (chunk: Buffer) => {
    const text = chunk.toString();
    process.stderr.write(`[AudioCapture] ${text}`);
    // Notify renderer when capture is confirmed running
    if (text.includes("streaming started") || text.includes("MicCapture: started")) {
      notifyRenderer("audio:status", { type: "running", message: "Audio capture active" });
    }
    // Mic permission issue
    if (text.includes("no input device available")) {
      notifyRenderer("audio:status", {
        type: "mic_unavailable",
        message: "Microphone not available. Grant Microphone permission in System Settings → Privacy & Security → Microphone.",
      });
    }
  });

  captureProcess.on("exit", (code, signal) => {
    captureProcess = null;

    if (code === 2) {
      // Permission denied — notify the overlay UI AND show native dialog.
      notifyRenderer("audio:status", {
        type: "permission_denied",
        message:
          "Screen Recording permission required. Open System Settings → Privacy & Security → Screen Recording, enable Persuasion Dojo, then restart.",
      });

      dialog.showMessageBox({
        type: "warning",
        title: "Screen Recording Permission Required",
        message:
          "Persuasion Dojo needs Screen Recording access to capture meeting audio.",
        detail:
          "Open System Settings → Privacy & Security → Screen Recording, then enable Persuasion Dojo and restart the session.",
        buttons: ["Open System Settings", "Dismiss"],
        defaultId: 0,
      }).then(({ response }) => {
        if (response === 0) {
          shell.openExternal(
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
          );
        }
      });
      return;
    }

    if (signal !== "SIGTERM" && code !== 0) {
      process.stderr.write(`[AudioCapture] exited with code=${code} signal=${signal}\n`);
      notifyRenderer("audio:status", {
        type: "crash",
        message: `Audio capture exited unexpectedly (code ${code}).`,
      });
    }
  });
}

function stopCapture(): void {
  if (captureProcess) {
    captureProcess.kill("SIGTERM");
    captureProcess = null;
  }
}

// ── Overlay window ────────────────────────────────────────────────────────────

let mainWindow: BrowserWindow | null = null;

function createWindow(): BrowserWindow {
  const prefs = loadPrefs();
  const display = screen.getPrimaryDisplay();
  const defaultX = display.workArea.x + display.workArea.width - 480 - 20;
  const defaultY = display.workArea.y + display.workArea.height - 720 - 40;

  const win = new BrowserWindow({
    width: 480,
    height: 720,
    minWidth: 420,
    minHeight: 600,
    maxWidth: 600,
    x: prefs.x ?? defaultX,
    y: prefs.y ?? defaultY,
    show: false,
    alwaysOnTop: true,
    level: "floating",
    visibleOnAllWorkspaces: true,
    titleBarStyle: "hiddenInset",
    trafficLightPosition: { x: 18, y: 18 },
    frame: false,
    transparent: false,
    resizable: true,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    hasShadow: true,
    backgroundColor: "#1A1A1E",
    webPreferences: {
      preload: join(__dirname, "../preload/index.js"),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.on("ready-to-show", () => {
    win.show();
  });

  // ── Blur/focus opacity (Raycast/Superhuman pattern) ───────────────────
  // Recede when user is focused on their meeting app; snap back on hover.
  win.on("blur", () => {
    win.setOpacity(0.75);
  });
  win.on("focus", () => {
    win.setOpacity(1.0);
  });

  // ── Persist window position across sessions ───────────────────────────
  let moveTimer: ReturnType<typeof setTimeout> | null = null;
  win.on("moved", () => {
    // Debounce writes — user may be dragging continuously.
    if (moveTimer) clearTimeout(moveTimer);
    moveTimer = setTimeout(() => {
      const [x, y] = win.getPosition();
      savePrefs({ x, y });
    }, 500);
  });

  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  if (is.dev && process.env["ELECTRON_RENDERER_URL"]) {
    win.loadURL(process.env["ELECTRON_RENDERER_URL"]);
  } else {
    win.loadFile(join(__dirname, "../renderer/index.html"));
  }

  return win;
}

// ── Global hotkeys ─────────────────────────────────────────────────────────────
// All registered after the window is ready so we can send IPC to the renderer.
// They work even when Zoom/Teams has focus (globalShortcut, not local accelerators).

function registerHotkeys(win: BrowserWindow): void {
  // ⌘⇧D — dismiss current prompt
  globalShortcut.register("CommandOrControl+Shift+D", () => {
    win.webContents.send("overlay:dismiss-prompt");
  });

  // ⌘⇧L — cycle active layer (Audience → Self → Group → Audience)
  globalShortcut.register("CommandOrControl+Shift+L", () => {
    win.webContents.send("overlay:cycle-layer");
  });

  // ⌘⇧H — toggle history tray
  globalShortcut.register("CommandOrControl+Shift+H", () => {
    win.webContents.send("overlay:toggle-history");
  });

  // ⌘⇧M — minimize overlay (hide window, icon stays in menubar/dock)
  globalShortcut.register("CommandOrControl+Shift+M", () => {
    win.hide();
  });
}

// ── App lifecycle ─────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  electronApp.setAppUserModelId("com.persuasiondojo.overlay");

  // Set up application menu so standard Edit shortcuts (⌘C, ⌘V, ⌘X, ⌘A)
  // work in input fields. Required because the window is frameless.
  const template: Electron.MenuItemConstructorOptions[] = [
    {
      label: app.name,
      submenu: [
        { role: "about" },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "selectAll" },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));

  app.on("browser-window-created", (_, window) => {
    optimizer.watchWindowShortcuts(window);
  });

  // Right-click context menu for cut/copy/paste in all input fields.
  ipcMain.on("context-menu:show", (event, params: { hasSelection: boolean; isEditable: boolean }) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (!win) return;
    const items: Electron.MenuItemConstructorOptions[] = [];
    if (params.isEditable) {
      items.push(
        { label: "Cut", role: "cut", enabled: params.hasSelection },
        { label: "Copy", role: "copy", enabled: params.hasSelection },
        { label: "Paste", role: "paste" },
        { type: "separator" },
        { label: "Select All", role: "selectAll" },
      );
    } else if (params.hasSelection) {
      items.push({ label: "Copy", role: "copy" });
    }
    if (items.length > 0) {
      Menu.buildFromTemplate(items).popup({ window: win });
    }
  });

  mainWindow = createWindow();
  registerHotkeys(mainWindow);

  // Start the Swift audio capture binary.
  spawnCapture();

  // IPC: renderer can request hide (minimize to menubar).
  ipcMain.on("overlay:minimize", () => {
    mainWindow?.hide();
  });

  // IPC: app version query from renderer.
  ipcMain.on("app:version", (event) => {
    event.returnValue = app.getVersion();
  });

  // IPC: renderer requests opening a URL in the system browser.
  // Used by CalendarPane for Google OAuth and any other external links.
  ipcMain.on("shell:open-external", (_event, url: string) => {
    if (typeof url === "string" && (url.startsWith("https://") || url.startsWith("http://"))) {
      shell.openExternal(url);
    }
  });

  // IPC: open macOS System Settings to Screen Recording privacy pane.
  // On macOS 13+ the deep link changed to the new System Settings app.
  ipcMain.on("shell:open-screen-recording", () => {
    spawn("open", [
      "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    ]);
    // Also try the macOS 13+ Ventura deep link in case the old one doesn't land correctly.
    spawn("open", [
      "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenRecording",
    ]);
  });

  // IPC: query whether the AudioCapture binary is alive.
  ipcMain.on("audio:is-running", (event) => {
    event.returnValue = captureProcess !== null && captureProcess.exitCode === null;
  });

  // IPC: Python watchdog detected silence >5s — restart the capture binary.
  // The renderer forwards this after receiving a "swift_restart_needed" WebSocket
  // message from the Python backend.
  ipcMain.on("swift:restart", () => {
    stopCapture();
    spawnCapture();
  });

  // IPC: New session starting — ensure the capture binary is running.
  // The renderer sends this before opening the WebSocket so audio is flowing.
  ipcMain.on("swift:start", () => {
    process.stderr.write("[AudioCapture] session starting — ensuring capture is running\n");
    spawnCapture();
  });

  // IPC: Session ended — stop the capture binary to prevent orphaned processes.
  // The renderer forwards this after receiving a "stop_capture" WebSocket message.
  ipcMain.on("swift:stop", () => {
    process.stderr.write("[AudioCapture] session ended — stopping capture\n");
    stopCapture();
  });

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createWindow();
      registerHotkeys(mainWindow);
    } else {
      mainWindow?.show();
    }
  });

  // Check for updates in production only.
  if (!is.dev) {
    autoUpdater.checkForUpdatesAndNotify();
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

// Unregister all hotkeys and stop the capture binary before quitting.
app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  stopCapture();
});
