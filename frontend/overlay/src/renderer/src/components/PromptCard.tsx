/**
 * PromptCard — displays a live coaching prompt in the overlay.
 *
 * Shows prompt text, layer badges, a dismiss button, and a ↻ cached
 * badge when the prompt is a fallback (Haiku timed out).
 */

import React from "react";
import type { CoachingPrompt, Layer } from "../types";
import { LayerBadge } from "./LayerBadge";

interface PromptCardProps {
  prompt: CoachingPrompt;
  activeLayer: Layer;
  onCycleLayer: () => void;
  onDismiss: () => void;
  historyOpen: boolean;
  onToggleHistory: () => void;
}

const LAYERS: Layer[] = ["audience", "self", "group"];

export function PromptCard({
  prompt,
  activeLayer,
  onCycleLayer,
  onDismiss,
  historyOpen,
  onToggleHistory,
}: PromptCardProps) {
  return (
    <div role="region" aria-label="Coaching prompt">
      {/* Layer switcher */}
      <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 8 }}>
        {LAYERS.map((layer, i) => (
          <LayerBadge
            key={layer}
            layer={layer}
            active={layer === activeLayer}
            showSeparator={i > 0}
            onClick={onCycleLayer}
          />
        ))}
      </div>

      {/* Prompt text */}
      <p style={{ margin: 0, fontSize: 13, lineHeight: 1.4 }}>{prompt.text}</p>

      {/* Cached fallback badge */}
      {prompt.is_fallback && (
        <span aria-label="Cached fallback prompt" style={{ fontSize: 10, opacity: 0.6 }}>
          ↻ cached
        </span>
      )}

      {/* Actions */}
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button onClick={onDismiss} aria-label="Dismiss prompt">
          ✕
        </button>
        <button
          onClick={onToggleHistory}
          aria-label={historyOpen ? "Close history" : "Open history"}
        >
          {historyOpen ? "▲" : "▼"}
        </button>
      </div>
    </div>
  );
}
