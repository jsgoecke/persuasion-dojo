/**
 * Team sync pane — export participants to AES-256-GCM bundle / import from bundle.
 *
 * POST /team/export { passphrase } → { bundle: "<base64 JSON>" }
 * POST /team/import { bundle, passphrase } → 204
 */
import React, { useState, useRef } from "react";

const API = "http://localhost:8000";

interface TeamSyncPaneProps {
  onBack: () => void;
}

export function TeamSyncPane({ onBack }: TeamSyncPaneProps): React.ReactElement {
  // Export state
  const [exportPass, setExportPass] = useState("");
  const [exportBundle, setExportBundle] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  // Import state
  const [importPass, setImportPass] = useState("");
  const [importBundle, setImportBundle] = useState("");
  const [importing, setImporting] = useState(false);
  const [importDone, setImportDone] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function handleExport(): Promise<void> {
    if (!exportPass.trim()) return;
    setExporting(true);
    setExportError(null);
    setExportBundle(null);
    try {
      const res = await fetch(`${API}/team/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ passphrase: exportPass }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail ?? `HTTP ${res.status}`);
      }
      const { bundle } = await res.json();
      setExportBundle(bundle);
    } catch (e) {
      setExportError(String(e));
    } finally {
      setExporting(false);
    }
  }

  function downloadBundle(): void {
    if (!exportBundle) return;
    const blob = new Blob([exportBundle], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `pdojo-team-${new Date().toISOString().slice(0, 10)}.pdojo`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function copyBundle(): void {
    if (!exportBundle) return;
    navigator.clipboard.writeText(exportBundle).catch(() => {});
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>): void {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => setImportBundle((ev.target?.result as string) ?? "");
    reader.readAsText(file);
  }

  async function handleImport(): Promise<void> {
    if (!importBundle.trim() || !importPass.trim()) return;
    setImporting(true);
    setImportError(null);
    setImportDone(false);
    try {
      const res = await fetch(`${API}/team/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bundle: importBundle.trim(), passphrase: importPass }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail ?? `HTTP ${res.status}`);
      }
      setImportDone(true);
      setImportBundle("");
      setImportPass("");
    } catch (e) {
      setImportError(String(e));
    } finally {
      setImporting(false);
    }
  }

  const container: React.CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 14,
    padding: "14px 16px",
    fontFamily: "var(--font-body)",
    color: "var(--text-primary)",
  };

  const backBtn: React.CSSProperties = {
    background: "transparent",
    border: "none",
    color: "var(--text-secondary)",
    fontSize: 12,
    cursor: "pointer",
    padding: 0,
    textAlign: "left" as const,
  };

  const sectionHead: React.CSSProperties = {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--text-secondary)",
    letterSpacing: "0.04em",
    textTransform: "uppercase" as const,
    marginBottom: 2,
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    background: "var(--bg-card)",
    border: "1px solid var(--border-medium)",
    borderRadius: 10,
    padding: "7px 10px",
    fontSize: 13,
    color: "var(--text-primary)",
    outline: "none",
    boxSizing: "border-box" as const,
    fontFamily: "var(--font-body)",
  };

  const primaryBtn = (disabled: boolean): React.CSSProperties => ({
    background: "var(--gold)",
    border: "none",
    borderRadius: 12,
    color: "var(--bg-primary)",
    fontSize: 13,
    padding: "8px 12px",
    cursor: "pointer",
    opacity: disabled ? 0.5 : 1,
    width: "100%",
  });

  const ghostBtn: React.CSSProperties = {
    background: "var(--bg-card)",
    border: "1px solid var(--border-medium)",
    borderRadius: 10,
    color: "var(--text-secondary)",
    fontSize: 12,
    padding: "6px 10px",
    cursor: "pointer",
  };

  return (
    <div style={container}>

      {/* Export */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={sectionHead}>Export</div>
        <div style={{ fontSize: 12, color: "var(--text-tertiary)", lineHeight: 1.5 }}>
          Encrypt your participant profiles so a teammate can import them.
        </div>
        <input
          type="password"
          style={inputStyle}
          placeholder="Passphrase (share separately)"
          value={exportPass}
          onChange={(e) => setExportPass(e.target.value)}
          aria-label="Export passphrase"
        />
        {exportError && <div style={{ fontSize: 12, color: "var(--red)" }}>{exportError}</div>}
        <button
          style={primaryBtn(exporting || !exportPass.trim())}
          onClick={handleExport}
          disabled={exporting || !exportPass.trim()}
          aria-label="Export participants"
        >
          {exporting ? "Encrypting…" : "Export"}
        </button>

        {exportBundle && (
          <div style={{ display: "flex", gap: 6 }}>
            <button style={ghostBtn} onClick={downloadBundle}>Download .pdojo</button>
            <button style={ghostBtn} onClick={copyBundle}>Copy to clipboard</button>
          </div>
        )}
      </div>

      <div style={{ borderTop: "1px solid var(--border-subtle)" }} />

      {/* Import */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={sectionHead}>Import</div>
        <div style={{ fontSize: 12, color: "var(--text-tertiary)", lineHeight: 1.5 }}>
          Load encrypted participant profiles from a teammate.
        </div>

        <input
          type="file"
          ref={fileRef}
          accept=".pdojo,.json"
          style={{ display: "none" }}
          onChange={handleFileChange}
        />
        <button style={ghostBtn} onClick={() => fileRef.current?.click()}>
          {importBundle ? "File loaded ✓" : "Choose .pdojo file…"}
        </button>

        <div style={{ fontSize: 11, color: "var(--text-tertiary)", textAlign: "center" as const }}>or paste bundle text</div>

        <textarea
          rows={3}
          value={importBundle}
          onChange={(e) => setImportBundle(e.target.value)}
          placeholder="Paste encrypted bundle here…"
          style={{
            ...inputStyle,
            resize: "none" as const,
            fontSize: 11,
            fontFamily: "var(--font-mono)",
          }}
          aria-label="Import bundle"
        />

        <input
          type="password"
          style={inputStyle}
          placeholder="Passphrase"
          value={importPass}
          onChange={(e) => setImportPass(e.target.value)}
          aria-label="Import passphrase"
        />

        {importError && <div style={{ fontSize: 12, color: "var(--red)" }}>{importError}</div>}
        {importDone && <div style={{ fontSize: 12, color: "var(--green)" }}>Import complete. Participants added.</div>}

        <button
          style={primaryBtn(importing || !importBundle.trim() || !importPass.trim())}
          onClick={handleImport}
          disabled={importing || !importBundle.trim() || !importPass.trim()}
          aria-label="Import participants"
        >
          {importing ? "Importing…" : "Import"}
        </button>
      </div>
    </div>
  );
}
