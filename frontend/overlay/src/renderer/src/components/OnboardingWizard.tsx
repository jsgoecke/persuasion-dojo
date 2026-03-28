/**
 * OnboardingWizard
 *
 * First-run privacy disclosure. Shown once on launch; completion stored in
 * localStorage('pdojo:onboarded'). Gates the rest of the app.
 *
 * Three screens:
 *   1. What the app does (live coaching overview)
 *   2. Privacy disclosure (Claude API + Deepgram process audio/text)
 *   3. Ready to start
 */

import React, { useState } from "react";

const STORAGE_KEY = "pdojo:onboarded";

/** Returns true if the user has already completed onboarding. */
export function hasOnboarded(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/** Mark onboarding complete. */
function markOnboarded(): void {
  try {
    localStorage.setItem(STORAGE_KEY, "1");
  } catch {
    // storage blocked — proceed anyway
  }
}

// ── Shared style primitives ────────────────────────────────────────────────

const containerStyle: React.CSSProperties = {
  width: 260,
  background: "var(--overlay-surface)",
  borderRadius: "var(--radius-md)",
  border: "1px solid var(--overlay-border)",
  overflow: "hidden",
  animation: "slideUp var(--duration-medium) ease-out",
};

const headerStyle: React.CSSProperties = {
  padding: "10px 14px",
  borderBottom: "1px solid var(--overlay-border)",
  display: "flex",
  alignItems: "center",
  gap: 6,
};

const stepDotStyle = (active: boolean): React.CSSProperties => ({
  width: 5,
  height: 5,
  borderRadius: "var(--radius-full)",
  background: active ? "var(--overlay-text-secondary)" : "var(--overlay-border)",
  transition: "background var(--duration-short) ease",
});

const bodyStyle: React.CSSProperties = {
  padding: "12px 14px",
};

const pStyle: React.CSSProperties = {
  fontFamily: "var(--font-body)",
  fontSize: 12,
  lineHeight: 1.6,
  color: "var(--overlay-text-secondary)",
  margin: "0 0 10px",
};

const smallStyle: React.CSSProperties = {
  fontFamily: "var(--font-body)",
  fontSize: 10,
  lineHeight: 1.5,
  color: "var(--overlay-text-muted)",
  display: "block",
  marginBottom: 10,
};

const primaryBtnStyle: React.CSSProperties = {
  width: "100%",
  height: 32,
  background: "var(--overlay-surface-elevated)",
  border: "1px solid rgba(255,255,255,0.12)",
  borderRadius: "var(--radius-md)",
  cursor: "pointer",
  fontFamily: "var(--font-body)",
  fontSize: 12,
  fontWeight: 500,
  color: "var(--overlay-text-primary)",
  letterSpacing: "0.01em",
  marginTop: 4,
};

// ── Screens ────────────────────────────────────────────────────────────────

function Screen1({ onNext }: { onNext: () => void }): React.ReactElement {
  return (
    <>
      <div style={headerStyle}>
        <span
          style={{
            fontFamily: "var(--font-body)",
            fontSize: 12,
            fontWeight: 600,
            color: "var(--overlay-text-primary)",
          }}
        >
          Persuasion Dojo
        </span>
      </div>
      <div style={bodyStyle}>
        <p style={pStyle}>
          A live coaching overlay for high-stakes conversations.
        </p>
        <p style={pStyle}>
          While you're in a meeting, Dojo listens, identifies who you're talking
          to, and surfaces private text prompts — only visible to you — telling
          you how to be more persuasive in the moment.
        </p>
        <p style={{ ...pStyle, margin: 0 }}>
          No interruptions. No audio output. Just quiet guidance.
        </p>
        <button style={{ ...primaryBtnStyle, marginTop: 14 }} onClick={onNext}>
          Next →
        </button>
      </div>
    </>
  );
}

function Screen2({ onNext }: { onNext: () => void }): React.ReactElement {
  return (
    <>
      <div style={headerStyle}>
        <span
          style={{
            fontFamily: "var(--font-body)",
            fontSize: 12,
            fontWeight: 600,
            color: "var(--overlay-text-primary)",
          }}
        >
          Privacy
        </span>
      </div>
      <div style={bodyStyle}>
        <p style={pStyle}>Before you start, a few things to know:</p>

        <p style={{ ...pStyle, marginBottom: 6 }}>
          <strong style={{ color: "var(--overlay-text-primary)" }}>Audio capture</strong>
        </p>
        <span style={smallStyle}>
          Meeting audio is captured via ScreenCaptureKit and processed locally.
          Audio never leaves your Mac — only the text transcript is sent for coaching.
        </span>

        <p style={{ ...pStyle, marginBottom: 6 }}>
          <strong style={{ color: "var(--overlay-text-primary)" }}>Transcription</strong>
        </p>
        <span style={smallStyle}>
          Speech is transcribed by Deepgram's streaming API. Transcript text
          is sent to Deepgram's servers for this purpose.
        </span>

        <p style={{ ...pStyle, marginBottom: 6 }}>
          <strong style={{ color: "var(--overlay-text-primary)" }}>Coaching</strong>
        </p>
        <span style={smallStyle}>
          Transcript segments are sent to Anthropic's Claude API to generate
          coaching prompts. Participant profiles are stored locally in SQLite
          and never shared.
        </span>

        <button style={primaryBtnStyle} onClick={onNext}>
          I understand →
        </button>
      </div>
    </>
  );
}

function Screen3({ onDone }: { onDone: () => void }): React.ReactElement {
  return (
    <>
      <div style={headerStyle}>
        <span
          style={{
            fontFamily: "var(--font-body)",
            fontSize: 12,
            fontWeight: 600,
            color: "var(--overlay-text-primary)",
          }}
        >
          You're ready
        </span>
      </div>
      <div style={bodyStyle}>
        <p style={pStyle}>
          Hover over the overlay and press{" "}
          <code
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              background: "var(--overlay-surface-elevated)",
              padding: "1px 4px",
              borderRadius: "var(--radius-sm)",
              color: "var(--overlay-text-primary)",
            }}
          >
            ⌘⇧D
          </code>{" "}
          to dismiss a prompt,{" "}
          <code
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              background: "var(--overlay-surface-elevated)",
              padding: "1px 4px",
              borderRadius: "var(--radius-sm)",
              color: "var(--overlay-text-primary)",
            }}
          >
            ⌘⇧L
          </code>{" "}
          to cycle layers, and{" "}
          <code
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              background: "var(--overlay-surface-elevated)",
              padding: "1px 4px",
              borderRadius: "var(--radius-sm)",
              color: "var(--overlay-text-primary)",
            }}
          >
            ⌘⇧M
          </code>{" "}
          to hide.
        </p>
        <p style={{ ...pStyle, margin: 0 }}>
          Join a meeting, then tap{" "}
          <em style={{ color: "var(--overlay-text-primary)" }}>Start session</em>.
        </p>
        <button
          style={primaryBtnStyle}
          onClick={() => {
            markOnboarded();
            onDone();
          }}
        >
          Let's go
        </button>
      </div>
    </>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export interface OnboardingWizardProps {
  onComplete: () => void;
}

export function OnboardingWizard({ onComplete }: OnboardingWizardProps): React.ReactElement {
  const [step, setStep] = useState(0);

  return (
    <div style={{ padding: "var(--space-sm)" }}>
      <div style={containerStyle}>
        {step === 0 && <Screen1 onNext={() => setStep(1)} />}
        {step === 1 && <Screen2 onNext={() => setStep(2)} />}
        {step === 2 && <Screen3 onDone={onComplete} />}

        {/* Step dots */}
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            gap: 5,
            padding: "8px 14px 12px",
          }}
        >
          {[0, 1, 2].map(i => (
            <div key={i} style={stepDotStyle(i === step)} />
          ))}
        </div>
      </div>
    </div>
  );
}
