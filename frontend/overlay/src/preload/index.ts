import { contextBridge, ipcRenderer } from "electron";
import { electronAPI } from "@electron-toolkit/preload";

/**
 * IPC channels sent from the main process to the renderer.
 * The renderer registers a single handler via `window.api.onHotkey`.
 */
const HOTKEY_CHANNELS = [
  "overlay:dismiss-prompt",
  "overlay:cycle-layer",
  "overlay:toggle-history",
] as const;

type HotkeyChannel = typeof HOTKEY_CHANNELS[number];

// Expose a minimal surface to the renderer via contextBridge.
contextBridge.exposeInMainWorld("electron", electronAPI);

contextBridge.exposeInMainWorld("api", {
  /** Synchronous version query (used by Sentry release tag). */
  getVersion: (): string => ipcRenderer.sendSync("app:version"),

  /**
   * Register a handler for global hotkey actions delivered by the main process.
   *
   * Returns a cleanup function that removes all listeners — call it in a
   * useEffect cleanup to avoid leaking listeners on re-mount.
   *
   * @example
   * useEffect(() => {
   *   return window.api.onHotkey((action) => {
   *     if (action === "overlay:dismiss-prompt") dismissPrompt();
   *   });
   * }, []);
   */
  onHotkey: (handler: (action: HotkeyChannel) => void): (() => void) => {
    const listeners: Array<{ channel: HotkeyChannel; fn: () => void }> = [];

    for (const channel of HOTKEY_CHANNELS) {
      const fn = () => handler(channel);
      ipcRenderer.on(channel, fn);
      listeners.push({ channel, fn });
    }

    return () => {
      for (const { channel, fn } of listeners) {
        ipcRenderer.removeListener(channel, fn);
      }
    };
  },

  /**
   * Hide the overlay window (minimise to menubar).
   * Triggered by the renderer's "End session" or ⌘⇧M handler.
   */
  minimize: (): void => {
    ipcRenderer.send("overlay:minimize");
  },

  /**
   * Tell the main process to restart the Swift audio capture binary.
   * Called by the renderer when the Python backend sends a
   * "swift_restart_needed" WebSocket message (silence watchdog fired).
   */
  restartCapture: (): void => {
    ipcRenderer.send("swift:restart");
  },

  /**
   * Tell the main process to start (or restart) the Swift audio capture binary.
   * Called when a new session begins so audio is flowing before Deepgram connects.
   */
  startCapture: (): void => {
    ipcRenderer.send("swift:start");
  },

  /**
   * Tell the main process to stop the Swift audio capture binary.
   * Called when a session ends to prevent orphaned AudioCapture processes.
   */
  stopCapture: (): void => {
    ipcRenderer.send("swift:stop");
  },

  /**
   * Open macOS System Settings → Privacy & Security → Screen Recording.
   */
  openScreenRecording: (): void => {
    ipcRenderer.send("shell:open-screen-recording");
  },

  /**
   * Synchronously check if the AudioCapture binary is currently running.
   */
  isAudioRunning: (): boolean => ipcRenderer.sendSync("audio:is-running"),

  /**
   * Listen for audio pipeline status events from the main process.
   * Fired when the Swift binary exits with an error (permission denied,
   * binary missing, crash) so the overlay can show an actionable message.
   *
   * @returns Cleanup function to remove the listener.
   */
  onAudioStatus: (
    handler: (status: { type: string; message: string }) => void,
  ): (() => void) => {
    const fn = (_event: Electron.IpcRendererEvent, status: { type: string; message: string }) =>
      handler(status);
    ipcRenderer.on("audio:status", fn);
    return () => {
      ipcRenderer.removeListener("audio:status", fn);
    };
  },
});

/**
 * Expose shell utilities to the renderer under `window.electronAPI`.
 * Used by CalendarPane for Google OAuth flows and any other external links.
 */
contextBridge.exposeInMainWorld("electronAPI", {
  openExternal: (url: string): void => {
    ipcRenderer.send("shell:open-external", url);
  },
});

// ── Right-click context menu for cut/copy/paste ──────────────────────────────
// Electron frameless windows don't show native context menus by default.
// Listen for contextmenu events and request a native menu from the main process.
window.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  const target = e.target as HTMLElement;
  const isEditable =
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target.isContentEditable;
  const selection = window.getSelection();
  const hasSelection = !!selection && selection.toString().length > 0;
  ipcRenderer.send("context-menu:show", { hasSelection, isEditable });
});
