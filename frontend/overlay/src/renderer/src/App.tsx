import React from "react";
import { ErrorBoundary } from "@sentry/react";
import { Overlay } from "./Overlay";
import "./styles/globals.css";

function FallbackUI({ error }: { error: Error }): React.ReactElement {
  return (
    <div
      style={{
        background: "var(--surface-base, #1C1C1E)",
        color: "var(--text-primary, #E8E6E1)",
        fontFamily: "var(--font-body)",
        padding: "var(--space-lg, 24px)",
        borderRadius: "var(--radius-lg, 12px)",
        fontSize: "13px",
        lineHeight: "1.5",
        width: "100%",
        maxWidth: 480,
        margin: "var(--space-sm, 8px)",
      }}
    >
      <p style={{ marginBottom: 4, fontWeight: 600 }}>Something went wrong.</p>
      <p style={{ color: "var(--text-muted, #6A6860)", fontSize: "11px" }}>{error.message}</p>
    </div>
  );
}

export default function App(): React.ReactElement {
  return (
    <ErrorBoundary fallback={({ error }) => <FallbackUI error={error} />}>
      <Overlay />
    </ErrorBoundary>
  );
}
