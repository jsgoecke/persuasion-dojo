/**
 * LayerBadge — clickable pill indicating the active coaching layer.
 *
 * Displays one of: audience | self | group
 * Active badge: aria-label="Active layer: {layer}"
 * Inactive badge: aria-label="Switch to {layer} layer"
 * Optional "→" separator for use in a layer switcher row.
 */

import React from "react";
import type { Layer } from "../types";

interface LayerBadgeProps {
  layer: Layer;
  active: boolean;
  showSeparator?: boolean;
  onClick?: () => void;
}

const LAYER_COLORS: Record<Layer, string> = {
  audience: "#0EA5E9",
  self:     "#F59E0B",
  group:    "#10B981",
};

const LAYER_LABELS: Record<Layer, string> = {
  audience: "Audience",
  self:     "Self",
  group:    "Group",
};

export function LayerBadge({ layer, active, showSeparator = false, onClick }: LayerBadgeProps) {
  const color = LAYER_COLORS[layer];
  const label = LAYER_LABELS[layer];
  const ariaLabel = active ? `Active layer: ${layer}` : `Switch to ${layer} layer`;

  return (
    <>
      {showSeparator && <span aria-hidden="true">→</span>}
      <button
        role="button"
        aria-label={ariaLabel}
        onClick={onClick}
        style={{
          backgroundColor: active ? color : "transparent",
          border: `1px solid ${color}`,
          borderRadius: 4,
          color: active ? "#fff" : color,
          cursor: onClick ? "pointer" : "default",
          fontSize: 11,
          fontWeight: active ? 600 : 400,
          padding: "2px 8px",
        }}
      >
        {label}
      </button>
    </>
  );
}
