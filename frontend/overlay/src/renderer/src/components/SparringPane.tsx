/**
 * SparringPane
 *
 * Text-based AI sparring partner — practice mode with no audio required.
 *
 * Two sub-views:
 *   setup  → pick your archetype, opponent archetype, and scenario
 *   active → chat-like turn view with opponent responses + coaching tips
 *   ended  → summary with turn count + "Practice again" / "Back to coaching"
 */

import React, { useRef, useState, useEffect } from "react";
import type { SparringArchetype } from "../hooks/useSparringSocket";
import { useSparringSocket } from "../hooks/useSparringSocket";

// ── Constants ──────────────────────────────────────────────────────────────

const ARCHETYPES: SparringArchetype[] = ["Architect", "Firestarter", "Inquisitor", "Bridge Builder"];

const ARCHETYPE_DESC: Record<SparringArchetype, string> = {
  Architect:       "Logic + Analyze. Data-first, needs structure.",
  Firestarter:     "Narrative + Advocate. Inspires through vision.",
  Inquisitor:      "Logic + Advocate. Questions everything.",
  "Bridge Builder": "Narrative + Analyze. Builds consensus.",
};

const SCENARIO_PRESETS = [
  "Pitch a new product roadmap to a skeptical VP of Engineering",
  "Negotiate a budget increase with a cost-conscious CFO",
  "Convince a resistant colleague to adopt a new process",
];

// ── Style primitives ───────────────────────────────────────────────────────

const sectionHeaderStyle: React.CSSProperties = {
  padding: "10px 14px",
  borderBottom: "1px solid var(--border-medium)",
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
};

const labelStyle: React.CSSProperties = {
  fontFamily: "var(--font-body)",
  fontSize: 11,
  fontWeight: 500,
  color: "var(--text-tertiary)",
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  display: "block",
  marginBottom: 4,
};

const selectStyle: React.CSSProperties = {
  width: "100%",
  height: 42,
  background: "var(--bg-card)",
  border: "1px solid var(--border-medium)",
  borderRadius: 10,
  color: "var(--text-primary)",
  fontFamily: "var(--font-body)",
  fontSize: 13,
  padding: "0 10px",
  marginBottom: 8,
};

const textareaStyle: React.CSSProperties = {
  width: "100%",
  height: 72,
  background: "var(--bg-card)",
  border: "1px solid var(--border-medium)",
  borderRadius: 10,
  color: "var(--text-primary)",
  fontFamily: "var(--font-body)",
  fontSize: 13,
  padding: "8px 10px",
  resize: "none",
  marginBottom: 8,
  lineHeight: 1.4,
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  height: 42,
  background: "var(--bg-card)",
  border: "1px solid var(--border-medium)",
  borderRadius: 10,
  color: "var(--text-primary)",
  fontFamily: "var(--font-body)",
  fontSize: 13,
  padding: "0 10px",
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
  marginTop: 4,
};

const ghostBtnStyle: React.CSSProperties = {
  width: "100%",
  height: 42,
  background: "var(--bg-elevated)",
  border: "1px solid var(--border-medium)",
  borderRadius: 10,
  cursor: "pointer",
  fontFamily: "var(--font-body)",
  fontSize: 13,
  fontWeight: 500,
  color: "var(--text-primary)",
  marginTop: 4,
};

// ── Setup view ─────────────────────────────────────────────────────────────

interface SetupViewProps {
  onStart: (
    userArchetype: SparringArchetype,
    opponentArchetype: SparringArchetype,
    scenario: string,
  ) => void;
  onBack: () => void;
}

function SetupView({ onStart, onBack }: SetupViewProps): React.ReactElement {
  const [userArc, setUserArc]       = useState<SparringArchetype>("Architect");
  const [oppArc, setOppArc]         = useState<SparringArchetype>("Inquisitor");
  const [scenario, setScenario]     = useState(SCENARIO_PRESETS[0]);
  const [customScenario, setCustom] = useState("");

  const finalScenario = scenario === "custom" ? customScenario.trim() : scenario;

  return (
    <>
      <div style={sectionHeaderStyle}>
        <span
          style={{
            fontFamily: "var(--font-body)",
            fontSize: 14,
            fontWeight: 500,
            color: "var(--text-primary)",
          }}
        >
          Practice mode
        </span>
      </div>
      <div style={{ padding: "12px 14px" }}>
        <label style={labelStyle}>Your archetype</label>
        <select
          style={selectStyle}
          value={userArc}
          onChange={e => setUserArc(e.target.value as SparringArchetype)}
        >
          {ARCHETYPES.map(a => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>

        <label style={labelStyle}>Opponent plays</label>
        <select
          style={selectStyle}
          value={oppArc}
          onChange={e => setOppArc(e.target.value as SparringArchetype)}
        >
          {ARCHETYPES.map(a => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>

        <label style={labelStyle}>Scenario</label>
        <select
          style={selectStyle}
          value={scenario}
          onChange={e => setScenario(e.target.value)}
        >
          {SCENARIO_PRESETS.map(s => (
            <option key={s} value={s}>{s.length > 42 ? s.slice(0, 42) + "\u2026" : s}</option>
          ))}
          <option value="custom">Custom\u2026</option>
        </select>

        {scenario === "custom" && (
          <textarea
            style={textareaStyle}
            placeholder="Describe the meeting scenario\u2026"
            value={customScenario}
            onChange={e => setCustom(e.target.value)}
          />
        )}

        <p
          style={{
            fontFamily: "var(--font-body)",
            fontSize: 12,
            color: "var(--text-tertiary)",
            margin: "0 0 8px",
            lineHeight: 1.4,
          }}
        >
          {ARCHETYPE_DESC[oppArc]}
        </p>

        <button
          style={primaryBtnStyle}
          disabled={!finalScenario}
          onClick={() => onStart(userArc, oppArc, finalScenario)}
        >
          Start sparring
        </button>
      </div>
    </>
  );
}

// ── Active / chat view ─────────────────────────────────────────────────────

interface ActiveViewProps {
  turns: ReturnType<typeof useSparringSocket>["turns"];
  streamingChunk: string;
  onSend: (text: string) => void;
  onEnd: () => void;
  maxTurns: number;
  usedTurns: number;
}

function ActiveView({
  turns, streamingChunk, onSend, onEnd, maxTurns, usedTurns,
}: ActiveViewProps): React.ReactElement {
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll on new turns
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns.length, streamingChunk]);

  function handleSend(): void {
    const text = input.trim();
    if (!text) return;
    setInput("");
    onSend(text);
  }

  const roleBadge: Record<string, { label: string; color: string }> = {
    user:     { label: "You",      color: "var(--gold)" },
    opponent: { label: "Opponent", color: "var(--blue)" },
    coaching: { label: "Coach",    color: "var(--green)" },
  };

  const turnsRemaining = maxTurns - usedTurns;

  return (
    <>
      <div style={sectionHeaderStyle}>
        <span
          style={{
            fontFamily: "var(--font-body)",
            fontSize: 14,
            fontWeight: 500,
            color: "var(--text-primary)",
          }}
        >
          Sparring
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 13,
            color: "var(--text-tertiary)",
          }}
        >
          {turnsRemaining} turn{turnsRemaining !== 1 ? "s" : ""} left
        </span>
      </div>

      {/* Turn log */}
      <div
        style={{
          maxHeight: 360,
          overflowY: "auto",
          padding: "8px 14px",
          display: "flex",
          flexDirection: "column",
          gap: 6,
        }}
      >
        {turns.map(t => {
          const badge = roleBadge[t.role] ?? { label: t.role, color: "var(--text-tertiary)" };
          return (
            <div key={t.id}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 5,
                  marginBottom: 2,
                }}
              >
                <span
                  style={{
                    fontFamily: "var(--font-body)",
                    fontSize: 11,
                    fontWeight: 700,
                    color: badge.color,
                    textTransform: "uppercase",
                    letterSpacing: "0.08em",
                  }}
                >
                  {badge.label}
                </span>
              </div>
              <p
                style={{
                  margin: 0,
                  fontFamily: "var(--font-body)",
                  fontSize: 13,
                  lineHeight: 1.5,
                  color:
                    t.role === "coaching"
                      ? "var(--green)"
                      : "var(--text-secondary)",
                  fontStyle: t.role === "coaching" ? "italic" : "normal",
                }}
              >
                {t.text}
              </p>
            </div>
          );
        })}

        {/* Streaming opponent chunk */}
        {streamingChunk && (
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 2 }}>
              <span
                style={{
                  fontFamily: "var(--font-body)",
                  fontSize: 11,
                  fontWeight: 700,
                  color: "var(--blue)",
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                }}
              >
                Opponent
              </span>
            </div>
            <p
              style={{
                margin: 0,
                fontFamily: "var(--font-body)",
                fontSize: 13,
                lineHeight: 1.5,
                color: "var(--text-secondary)",
              }}
            >
              {streamingChunk}
              <span
                style={{
                  display: "inline-block",
                  width: 6,
                  height: 10,
                  background: "var(--blue)",
                  marginLeft: 2,
                  animation: "learningPulse 0.8s ease-in-out infinite",
                  borderRadius: 1,
                  verticalAlign: "text-bottom",
                }}
              />
            </p>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input row */}
      <div
        style={{
          padding: "8px 14px 12px",
          borderTop: "1px solid var(--border-medium)",
          display: "flex",
          gap: 6,
        }}
      >
        <input
          autoFocus
          style={{ ...inputStyle, flex: 1 }}
          placeholder="Your response\u2026"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          disabled={turnsRemaining === 0}
        />
        <button
          style={{
            width: 42,
            height: 42,
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-medium)",
            borderRadius: 10,
            cursor: "pointer",
            color: "var(--text-secondary)",
            fontSize: 14,
            flexShrink: 0,
          }}
          onClick={handleSend}
          disabled={turnsRemaining === 0}
          aria-label="Send"
        >
          →
        </button>
        <button
          style={{
            width: 42,
            height: 42,
            background: "none",
            border: "1px solid var(--border-medium)",
            borderRadius: 10,
            cursor: "pointer",
            color: "var(--text-tertiary)",
            fontSize: 12,
            flexShrink: 0,
          }}
          onClick={onEnd}
          aria-label="End sparring"
          title="End sparring"
        >
          ✕
        </button>
      </div>
    </>
  );
}

// ── Ended view ─────────────────────────────────────────────────────────────

function EndedView({
  totalTurns, onReset, onBack,
}: { totalTurns: number; onReset: () => void; onBack: () => void }): React.ReactElement {
  return (
    <>
      <div style={sectionHeaderStyle}>
        <span
          style={{
            fontFamily: "var(--font-body)",
            fontSize: 14,
            fontWeight: 500,
            color: "var(--text-primary)",
          }}
        >
          Session complete
        </span>
      </div>
      <div style={{ padding: "12px 14px" }}>
        <p
          style={{
            fontFamily: "var(--font-body)",
            fontSize: 13,
            color: "var(--text-secondary)",
            marginBottom: 12,
          }}
        >
          You completed{" "}
          <span
            style={{
              fontFamily: "var(--font-mono)",
              color: "var(--text-primary)",
            }}
          >
            {totalTurns}
          </span>{" "}
          turn{totalTurns !== 1 ? "s" : ""}.
        </p>
        <button style={primaryBtnStyle} onClick={onReset}>
          Practice again
        </button>
        <button
          style={{ ...ghostBtnStyle, marginTop: 6, color: "var(--text-tertiary)" }}
          onClick={onBack}
        >
          Back to coaching
        </button>
      </div>
    </>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export interface SparringPaneProps {
  onBack: () => void;
}

export function SparringPane({ onBack }: SparringPaneProps): React.ReactElement {
  const {
    phase, turns, streamingChunk, totalTurns, error,
    start, sendTurn, end, reset,
  } = useSparringSocket();

  const [maxTurns, setMaxTurns] = useState(10);
  // Track used turns as the number of "user" is_final turns
  const usedTurns = turns.filter(t => t.role === "user").length;

  if (error) {
    return (
      <div style={{ padding: "12px 14px" }}>
        <p
          style={{
            fontFamily: "var(--font-body)",
            fontSize: 13,
            color: "var(--red)",
            marginBottom: 8,
          }}
        >
          {error}
        </p>
        <button style={ghostBtnStyle} onClick={reset}>Try again</button>
        <button
          style={{ ...ghostBtnStyle, marginTop: 6, color: "var(--text-tertiary)" }}
          onClick={onBack}
        >
          Back
        </button>
      </div>
    );
  }

  return (
    <>
      {(phase === "idle" || phase === "setup") && (
        <SetupView
          onBack={onBack}
          onStart={(ua, oa, sc) => {
            setMaxTurns(10);
            void start(ua, oa, sc, 10);
          }}
        />
      )}
      {phase === "active" && (
        <ActiveView
          turns={turns}
          streamingChunk={streamingChunk}
          onSend={sendTurn}
          onEnd={end}
          maxTurns={maxTurns}
          usedTurns={usedTurns}
        />
      )}
      {phase === "ended" && (
        <EndedView totalTurns={totalTurns} onReset={reset} onBack={onBack} />
      )}
    </>
  );
}
