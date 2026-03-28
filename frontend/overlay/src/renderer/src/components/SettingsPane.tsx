import React, { useState, useEffect } from "react";

const API = "http://localhost:8000";

interface SettingsPaneProps {
  onBack: () => void;
}

export function SettingsPane({ onBack }: SettingsPaneProps): React.ReactElement {
  const [displayName, setDisplayName] = useState("");
  const [originalName, setOriginalName] = useState("");
  const [anthropicKey, setAnthropicKey] = useState("");
  const [deepgramKey, setDeepgramKey] = useState("");
  const [googleClientId, setGoogleClientId] = useState("");
  const [googleClientSecret, setGoogleClientSecret] = useState("");
  const [anthropicSet, setAnthropicSet] = useState(false);
  const [deepgramSet, setDeepgramSet] = useState(false);
  const [googleIdSet, setGoogleIdSet] = useState(false);
  const [googleSecretSet, setGoogleSecretSet] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/settings`)
      .then((r) => r.json())
      .then((d) => {
        setAnthropicSet(d.anthropic_api_key_set ?? false);
        setDeepgramSet(d.deepgram_api_key_set ?? false);
        setGoogleIdSet(d.google_client_id_set ?? false);
        setGoogleSecretSet(d.google_client_secret_set ?? false);
      })
      .catch(() => {});
    fetch(`${API}/users/me`)
      .then((r) => r.json())
      .then((d) => {
        const name = d.display_name && d.display_name !== "Local User" ? d.display_name : "";
        setDisplayName(name);
        setOriginalName(name);
      })
      .catch(() => {});
  }, []);

  async function handleSave(): Promise<void> {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      // Save API keys if provided
      const keyBody: Record<string, string> = {};
      if (anthropicKey.trim()) keyBody.anthropic_api_key = anthropicKey.trim();
      if (deepgramKey.trim()) keyBody.deepgram_api_key = deepgramKey.trim();
      if (googleClientId.trim()) keyBody.google_client_id = googleClientId.trim();
      if (googleClientSecret.trim()) keyBody.google_client_secret = googleClientSecret.trim();
      if (Object.keys(keyBody).length > 0) {
        const res = await fetch(`${API}/settings`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(keyBody),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        if (anthropicKey.trim()) { setAnthropicSet(true); setAnthropicKey(""); }
        if (deepgramKey.trim()) { setDeepgramSet(true); setDeepgramKey(""); }
        if (googleClientId.trim()) { setGoogleIdSet(true); setGoogleClientId(""); }
        if (googleClientSecret.trim()) { setGoogleSecretSet(true); setGoogleClientSecret(""); }
      }
      // Save display name if changed
      const nameChanged = displayName.trim() && displayName.trim() !== originalName;
      if (nameChanged) {
        const res = await fetch(`${API}/users/me`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_name: displayName.trim() }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        setOriginalName(displayName.trim());
      }
      setSaved(true);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  const container: React.CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 16,
    padding: "14px 16px",
    fontFamily: "var(--font-body)",
    color: "var(--text-primary)",
  };

  const label: React.CSSProperties = {
    fontSize: 11,
    color: "var(--text-tertiary)",
    marginBottom: 4,
    letterSpacing: "0.04em",
    textTransform: "uppercase" as const,
    fontWeight: 500,
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    background: "var(--bg-card)",
    border: "1px solid var(--border-medium)",
    borderRadius: 10,
    padding: "10px 12px",
    fontSize: 13,
    color: "var(--text-primary)",
    outline: "none",
    boxSizing: "border-box" as const,
    fontFamily: "var(--font-mono)",
    transition: "border-color 200ms ease",
  };

  const badgeStyle: React.CSSProperties = {
    display: "inline-block",
    fontSize: 10,
    padding: "2px 8px",
    borderRadius: 6,
    background: "rgba(90, 158, 111, 0.15)",
    color: "var(--green)",
    marginLeft: 8,
    verticalAlign: "middle",
    fontWeight: 500,
  };

  const nameChanged = displayName.trim() && displayName.trim() !== originalName;
  const canSave = anthropicKey.trim() || deepgramKey.trim() || googleClientId.trim() || googleClientSecret.trim() || nameChanged;

  return (
    <div style={container}>

      <div>
        <div style={label}>Your Name</div>
        <input
          type="text"
          style={{ ...inputStyle, fontFamily: "var(--font-body)" }}
          placeholder="How should the coach address you?"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          onFocus={(e) => { e.currentTarget.style.borderColor = "var(--gold-border)"; }}
          onBlur={(e) => { e.currentTarget.style.borderColor = "var(--border-medium)"; }}
          aria-label="Display name"
        />
      </div>

      <div>
        <div style={label}>
          Anthropic API Key
          {anthropicSet && <span style={badgeStyle}>set</span>}
        </div>
        <input
          type="password"
          style={inputStyle}
          placeholder={anthropicSet ? "••••••••  (stored)" : "sk-ant-..."}
          value={anthropicKey}
          onChange={(e) => setAnthropicKey(e.target.value)}
          onFocus={(e) => { e.currentTarget.style.borderColor = "var(--gold-border)"; }}
          onBlur={(e) => { e.currentTarget.style.borderColor = "var(--border-medium)"; }}
          aria-label="Anthropic API key"
        />
      </div>

      <div>
        <div style={label}>
          Deepgram API Key
          {deepgramSet && <span style={badgeStyle}>set</span>}
        </div>
        <input
          type="password"
          style={inputStyle}
          placeholder={deepgramSet ? "••••••••  (stored)" : "dg_..."}
          value={deepgramKey}
          onChange={(e) => setDeepgramKey(e.target.value)}
          onFocus={(e) => { e.currentTarget.style.borderColor = "var(--gold-border)"; }}
          onBlur={(e) => { e.currentTarget.style.borderColor = "var(--border-medium)"; }}
          aria-label="Deepgram API key"
        />
      </div>

      {/* Google Calendar section */}
      <div style={{ borderTop: "1px solid var(--border-subtle)", paddingTop: 16 }}>
        <div style={{ ...label, fontSize: 12, textTransform: "none" as const, letterSpacing: 0, fontWeight: 500, marginBottom: 10, color: "var(--text-secondary)" }}>
          Google Calendar
        </div>
        <div style={{ fontSize: 11, color: "var(--text-tertiary)", lineHeight: 1.5, marginBottom: 12 }}>
          Create a Desktop OAuth client in Google Cloud Console with Calendar API enabled.
        </div>
        <div style={{ marginBottom: 12 }}>
          <div style={label}>
            Google Client ID
            {googleIdSet && <span style={badgeStyle}>set</span>}
          </div>
          <input
            type="password"
            style={inputStyle}
            placeholder={googleIdSet ? "••••••••  (stored)" : "123456...apps.googleusercontent.com"}
            value={googleClientId}
            onChange={(e) => setGoogleClientId(e.target.value)}
            onFocus={(e) => { e.currentTarget.style.borderColor = "var(--gold-border)"; }}
            onBlur={(e) => { e.currentTarget.style.borderColor = "var(--border-medium)"; }}
            aria-label="Google Client ID"
          />
        </div>
        <div>
          <div style={label}>
            Google Client Secret
            {googleSecretSet && <span style={badgeStyle}>set</span>}
          </div>
          <input
            type="password"
            style={inputStyle}
            placeholder={googleSecretSet ? "••••••••  (stored)" : "GOCSPX-..."}
            value={googleClientSecret}
            onChange={(e) => setGoogleClientSecret(e.target.value)}
            onFocus={(e) => { e.currentTarget.style.borderColor = "var(--gold-border)"; }}
            onBlur={(e) => { e.currentTarget.style.borderColor = "var(--border-medium)"; }}
            aria-label="Google Client Secret"
          />
        </div>
      </div>

      {error && (
        <div style={{ fontSize: 12, color: "var(--red)", lineHeight: 1.4 }}>{error}</div>
      )}
      {saved && (
        <div style={{ fontSize: 12, color: "var(--green)", lineHeight: 1.4 }}>Settings saved.</div>
      )}

      <button
        onClick={handleSave}
        disabled={saving || !canSave}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: "100%",
          height: 48,
          background: "var(--gold)",
          color: "var(--bg-primary)",
          fontFamily: "var(--font-body)",
          fontSize: 15,
          fontWeight: 500,
          border: "none",
          borderRadius: 12,
          cursor: canSave ? "pointer" : "default",
          opacity: (!canSave || saving) ? 0.35 : 1,
          transition: "background 200ms ease, opacity 200ms ease",
        }}
        onMouseEnter={(e) => { if (canSave) e.currentTarget.style.background = "var(--gold-hover)"; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = "var(--gold)"; }}
        aria-label="Save settings"
      >
        {saving ? "Saving…" : "Save"}
      </button>
    </div>
  );
}
