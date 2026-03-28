/**
 * HistoryTray
 *
 * Collapsible tray showing up to 4 prior prompts (current prompt is
 * shown in PromptCard above). Slides open below the PromptCard.
 *
 * DESIGN.md: --overlay-surface-elevated (#2C2C2E) background,
 * --overlay-border dividers, compact row for each prior prompt.
 */

import React from "react";
import type { CoachingPrompt, Layer } from "../types";

const LAYER_COLOR: Record<Layer, string> = {
  audience: "var(--badge-audience)",
  self: "var(--badge-self)",
  group: "var(--badge-group)",
};

function formatAge(receivedAt: number): string {
  const secs = Math.round((Date.now() - receivedAt) / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  return `${mins}m ago`;
}

interface HistoryTrayProps {
  /** Prior prompts, newest first (excludes the current prompt shown in PromptCard). */
  prompts: CoachingPrompt[];
  open: boolean;
}

export function HistoryTray({ prompts, open }: HistoryTrayProps): React.ReactElement | null {
  if (!open || prompts.length === 0) return null;

  // Show at most 4 prior entries.
  const visible = prompts.slice(0, 4);

  return (
    <div
      role="list"
      aria-label="Prompt history"
      style={{
        width: "100%",
        background: "var(--overlay-surface-elevated)",
        borderRadius: "0 0 var(--radius-md) var(--radius-md)",
        borderTop: "1px solid var(--overlay-border)",
        overflow: "hidden",
        animation: "slideUp var(--duration-short) ease-out",
      }}
    >
      {visible.map((prompt, i) => (
        <div
          key={prompt.received_at}
          role="listitem"
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: "var(--space-sm)",
            padding: "8px 14px",
            borderTop: i > 0 ? "1px solid var(--overlay-border)" : undefined,
            opacity: 1 - i * 0.15, // Fade older entries
          }}
        >
          {/* Layer identity dot */}
          <span
            aria-hidden="true"
            style={{
              width: 5,
              height: 5,
              borderRadius: "var(--radius-full)",
              background: LAYER_COLOR[prompt.layer] ?? "var(--overlay-text-muted)",
              flexShrink: 0,
              marginTop: 5,
            }}
          />

          {/* Truncated prompt text */}
          <span
            style={{
              flex: 1,
              fontFamily: "var(--font-body)",
              fontSize: 12,
              lineHeight: 1.4,
              color: "var(--overlay-text-secondary)",
              overflow: "hidden",
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
            }}
          >
            {prompt.text}
          </span>

          {/* Age */}
          <span
            style={{
              flexShrink: 0,
              fontFamily: "var(--font-body)",
              fontSize: 10,
              color: "var(--overlay-text-muted)",
              paddingTop: 2,
            }}
          >
            {formatAge(prompt.received_at)}
          </span>
        </div>
      ))}
    </div>
  );
}
