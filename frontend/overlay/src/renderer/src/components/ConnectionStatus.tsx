/**
 * ConnectionStatus
 *
 * Shows overlay state feedback:
 *   - connecting: gold pulse ("Connecting…")
 *   - reconnecting: amber banner ("Reconnecting…")
 *   - error: red banner with error detail + retry
 *   - ending: muted label ("Ending session…")
 *
 * Nothing is rendered in the connected/active or idle states.
 */

import React from "react";
import type { ConnectionState, SessionPhase } from "../types";

const BODY = "var(--font-body)";

interface ConnectionStatusProps {
  connectionState: ConnectionState;
  sessionPhase: SessionPhase;
  errorMessage?: string | null;
  onRetry?: () => void;
}

export function ConnectionStatus({
  connectionState,
  sessionPhase,
  errorMessage,
  onRetry,
}: ConnectionStatusProps): React.ReactElement | null {
  if (connectionState === "connecting") {
    return (
      <div
        role="status"
        aria-live="polite"
        style={{
          width: "100%",
          padding: "14px 18px",
          fontFamily: BODY,
          fontSize: 13,
          color: "var(--gold)",
          background: "var(--gold-bg)",
          borderRadius: 10,
          borderLeft: "3px solid var(--gold)",
        }}
      >
        Connecting…
      </div>
    );
  }

  if (connectionState === "reconnecting") {
    return (
      <div
        role="alert"
        style={{
          width: "100%",
          padding: "14px 18px",
          background: "rgba(212, 168, 83, 0.08)",
          borderRadius: 10,
          borderLeft: "3px solid var(--gold)",
          fontFamily: BODY,
          fontSize: 13,
          color: "var(--gold)",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <span aria-hidden="true">⟳</span>
        Reconnecting…
      </div>
    );
  }

  if (connectionState === "error") {
    return (
      <div
        role="alert"
        style={{
          width: "100%",
          padding: "18px 20px",
          background: "var(--bg-elevated)",
          borderRadius: 12,
          borderLeft: "4px solid var(--red)",
          fontFamily: BODY,
          fontSize: 13,
          color: "var(--text-primary)",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        <div style={{ fontWeight: 500, color: "var(--red)", fontSize: 14 }}>
          Connection lost
        </div>
        {errorMessage && (
          <div style={{ color: "var(--text-secondary)", lineHeight: 1.5, fontSize: 13 }}>
            {errorMessage}
          </div>
        )}
        {onRetry && (
          <button
            onClick={onRetry}
            style={{
              alignSelf: "flex-start",
              background: "transparent",
              border: "1px solid var(--border-medium)",
              borderRadius: 8,
              cursor: "pointer",
              color: "var(--text-primary)",
              fontFamily: BODY,
              fontSize: 13,
              fontWeight: 500,
              padding: "8px 16px",
              transition: "background 200ms ease, border-color 200ms ease",
            }}
            onMouseEnter={e => { e.currentTarget.style.background = "var(--bg-hover)"; e.currentTarget.style.borderColor = "var(--border-hover)"; }}
            onMouseLeave={e => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.borderColor = "var(--border-medium)"; }}
          >
            Retry
          </button>
        )}
      </div>
    );
  }

  if (sessionPhase === "ending") {
    return (
      <div
        role="status"
        aria-live="polite"
        style={{
          width: "100%",
          padding: "14px 18px",
          fontFamily: BODY,
          fontSize: 13,
          color: "var(--text-secondary)",
          background: "var(--bg-elevated)",
          borderRadius: 10,
          borderLeft: "3px solid var(--text-tertiary)",
        }}
      >
        Ending session…
      </div>
    );
  }

  return null;
}
