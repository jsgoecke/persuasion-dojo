/**
 * PreSeedPane
 *
 * Lets the user classify meeting participants before a session starts,
 * using free-form text (LinkedIn bio, email, meeting notes, etc.).
 *
 * Calls POST /participants/pre-seed → returns archetype, confidence, reasoning.
 * Participants are accumulated in local state for the session.
 */

import React, { useState, useRef, useCallback } from "react";

const API_BASE = "http://localhost:8000";

// ── Types ──────────────────────────────────────────────────────────────────

interface PreSeedResult {
  name: string;
  archetype: string;
  confidence: number;
  reasoning: string;
}

// ── Shared styles ──────────────────────────────────────────────────────────

const labelStyle: React.CSSProperties = {
  fontFamily: "var(--font-body)",
  fontSize: 13,
  color: "var(--text-secondary)",
  display: "block",
  marginBottom: 4,
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  background: "var(--bg-card)",
  border: "1px solid var(--border-medium)",
  borderRadius: 10,
  color: "var(--text-primary)",
  fontFamily: "var(--font-body)",
  fontSize: 14,
  padding: "8px 10px",
  boxSizing: "border-box",
  outline: "none",
};

const textareaStyle: React.CSSProperties = {
  ...inputStyle,
  resize: "none",
  lineHeight: 1.5,
  minHeight: 72,
};

const primaryBtnStyle: React.CSSProperties = {
  width: "100%",
  height: 54,
  background: "var(--gold)",
  border: "none",
  borderRadius: 12,
  cursor: "pointer",
  fontFamily: "var(--font-body)",
  fontSize: 16,
  fontWeight: 500,
  color: "var(--bg-primary)",
  letterSpacing: "0.01em",
};

const ghostBtnStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  cursor: "pointer",
  fontFamily: "var(--font-body)",
  fontSize: 13,
  color: "var(--text-tertiary)",
  padding: 0,
  textDecoration: "underline",
};

const ARCHETYPE_COLORS: Record<string, string> = {
  Architect:        "var(--gold)",
  Firestarter:      "var(--red)",
  Inquisitor:       "var(--green)",
  "Bridge Builder": "var(--blue)",
};

const CONFIDENCE_LABEL = (c: number) =>
  c >= 0.8 ? "High" : c >= 0.6 ? "Medium" : "Low";

// ── Result row ─────────────────────────────────────────────────────────────

function ResultRow({ result }: { result: PreSeedResult }): React.ReactElement {
  const [expanded, setExpanded] = useState(false);
  const color = ARCHETYPE_COLORS[result.archetype] ?? "var(--text-secondary)";

  return (
    <div
      style={{
        padding: "10px 0",
        borderBottom: "1px solid var(--border-subtle)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span
          style={{
            fontFamily: "var(--font-body)",
            fontSize: 14,
            fontWeight: 600,
            color: "var(--text-primary)",
          }}
        >
          {result.name}
        </span>
        <span
          style={{
            fontFamily: "var(--font-body)",
            fontSize: 12,
            color,
            fontWeight: 600,
          }}
        >
          {result.archetype}
        </span>
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          marginTop: 3,
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "var(--text-tertiary)",
          }}
        >
          {CONFIDENCE_LABEL(result.confidence)} confidence
        </span>
        <button
          style={ghostBtnStyle}
          onClick={() => setExpanded(e => !e)}
        >
          {expanded ? "hide" : "why?"}
        </button>
      </div>
      {expanded && (
        <p
          style={{
            margin: "6px 0 0",
            fontFamily: "var(--font-body)",
            fontSize: 13,
            lineHeight: 1.5,
            color: "var(--text-secondary)",
          }}
        >
          {result.reasoning}
        </p>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export interface PreSeedPaneProps {
  onBack: () => void;
}

export function PreSeedPane({ onBack }: PreSeedPaneProps): React.ReactElement {
  const [name, setName]   = useState("");
  const [text, setText]   = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError]   = useState<string | null>(null);
  const [results, setResults] = useState<PreSeedResult[]>([]);
  const nameRef = useRef<HTMLInputElement>(null);

  const classify = useCallback(async () => {
    const trimName = name.trim();
    const trimText = text.trim();
    if (!trimName || !trimText) return;

    setLoading(true);
    setError(null);

    try {
      const resp = await fetch(`${API_BASE}/participants/pre-seed`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimName, text: trimText }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json() as { archetype: string; confidence: number; reasoning: string };
      setResults(prev => [
        { name: trimName, archetype: data.archetype, confidence: data.confidence, reasoning: data.reasoning },
        ...prev,
      ]);
      setName("");
      setText("");
      nameRef.current?.focus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Classification failed");
    } finally {
      setLoading(false);
    }
  }, [name, text]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      void classify();
    }
  }, [classify]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Form */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div>
          <label style={labelStyle}>Name</label>
          <input
            ref={nameRef}
            style={inputStyle}
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="Sarah Chen"
            onKeyDown={handleKeyDown}
          />
        </div>
        <div>
          <label style={labelStyle}>Bio, email style, notes…</label>
          <textarea
            style={textareaStyle}
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder={"LinkedIn bio, meeting intro,\nemail excerpts, anything…"}
            onKeyDown={handleKeyDown}
          />
        </div>
        {error && (
          <p
            style={{
              margin: 0,
              fontFamily: "var(--font-body)",
              fontSize: 13,
              color: "var(--red)",
            }}
          >
            {error}
          </p>
        )}
        <button
          style={{ ...primaryBtnStyle, opacity: loading || !name.trim() || !text.trim() ? 0.5 : 1 }}
          onClick={() => void classify()}
          disabled={loading || !name.trim() || !text.trim()}
        >
          {loading ? "Classifying…" : "Classify  ⌘↵"}
        </button>
      </div>

      {/* Results */}
      {results.length > 0 && (
        <div>
          {results.map((r, i) => (
            <ResultRow key={`${r.name}-${i}`} result={r} />
          ))}
        </div>
      )}

      {results.length === 0 && (
        <p
          style={{
            margin: 0,
            fontFamily: "var(--font-body)",
            fontSize: 13,
            lineHeight: 1.6,
            color: "var(--text-tertiary)",
          }}
        >
          Add each meeting participant's profile to prime the coaching engine before the call starts.
        </p>
      )}
    </div>
  );
}
