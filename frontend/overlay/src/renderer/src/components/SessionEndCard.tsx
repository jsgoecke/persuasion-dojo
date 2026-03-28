/**
 * SessionEndCard — post-session score summary shown in the overlay.
 *
 * Displays the Persuasion Score, Growth delta, and the three sub-scores:
 * Timing (30%), Ego Safety (30%), Convergence (40%).
 *
 * Disclosure required per CLAUDE.md: score is a heuristic index, not
 * empirically derived. Shown as a footnote.
 */

import React from "react";
import type { SessionEndData } from "../types";

interface SessionEndCardProps {
  result: SessionEndData;
  onDismiss: () => void;
}

export function SessionEndCard({ result, onDismiss }: SessionEndCardProps) {
  const { persuasion_score, growth_delta, breakdown } = result;

  return (
    <section role="region" aria-label="Session results">
      {/* Headline score */}
      <div style={{ textAlign: "center", marginBottom: 12 }}>
        <span style={{ fontSize: 32, fontWeight: 700 }}>{persuasion_score}</span>
        <span style={{ fontSize: 13, marginLeft: 4, opacity: 0.6 }}>/ 100</span>
        {growth_delta !== null && (
          <div style={{ fontSize: 12, marginTop: 2 }}>
            {growth_delta >= 0 ? "+" : ""}
            {growth_delta} vs. your baseline
          </div>
        )}
      </div>

      {/* Sub-scores */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <SubScore label="Timing"      value={breakdown.timing}      weight="30%" />
        <SubScore label="Ego Safety"  value={breakdown.ego_safety}  weight="30%" />
        <SubScore label="Convergence" value={breakdown.convergence} weight="40%" />
      </div>

      {/* Disclosure */}
      <p style={{ fontSize: 10, opacity: 0.5, marginTop: 12 }}>
        Persuasion Score is a heuristic index. Weights are calibrated by feedback, not empirically derived.
      </p>

      <button onClick={onDismiss} aria-label="Dismiss session results" style={{ marginTop: 8 }}>
        Done
      </button>
    </section>
  );
}

function SubScore({ label, value, weight }: { label: string; value: number; weight: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <span style={{ fontSize: 12 }}>
        {label} <span style={{ opacity: 0.5 }}>({weight})</span>
      </span>
      <span style={{ fontSize: 12, fontWeight: 600 }}>{value}</span>
    </div>
  );
}
