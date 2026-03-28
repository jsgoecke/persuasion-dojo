/**
 * SkillBadgesPane
 *
 * Shows all skill badges the user has earned — awarded when a coaching prompt
 * type has not fired for 3 consecutive sessions (skill internalized).
 *
 * Empty state: encouragement copy + "No badges yet" message.
 * Loaded state: one card per badge with name, tagline, and award date.
 */

import React, { useEffect, useState } from "react";

const API = "http://localhost:8000";

interface SkillBadge {
  id: string;
  trigger_type: string;
  badge_name: string;
  tagline: string;
  awarded_at: string;
  consecutive_sessions: number;
}

const BODY = "var(--font-body)";
const MONO = "var(--font-mono)";

function BadgeCard({ badge }: { badge: SkillBadge }): React.ReactElement {
  const awarded = new Date(badge.awarded_at);
  const dateLabel = awarded.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

  return (
    <div
      style={{
        background: "var(--bg-card)",
        borderRadius: 10,
        padding: "14px 16px",
        marginBottom: 8,
        display: "flex",
        alignItems: "flex-start",
        gap: 12,
      }}
    >
      {/* Badge icon */}
      <div
        style={{
          flexShrink: 0,
          width: 36,
          height: 36,
          borderRadius: 8,
          background: "var(--gold-bg)",
          border: "1px solid var(--gold-border)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <path
            d="M9 1L11.5 6.5L17 7.5L13 11.5L14 17L9 14.5L4 17L5 11.5L1 7.5L6.5 6.5L9 1Z"
            fill="currentColor"
            opacity="0.3"
            stroke="currentColor"
            strokeWidth="1"
            style={{ color: "var(--gold)" }}
          />
        </svg>
      </div>

      {/* Badge text */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontFamily: BODY,
            fontSize: 14,
            fontWeight: 500,
            color: "var(--text-primary)",
            marginBottom: 2,
          }}
        >
          {badge.badge_name}
        </div>
        <div
          style={{
            fontFamily: BODY,
            fontSize: 12,
            color: "var(--text-secondary)",
            marginBottom: 6,
            fontStyle: "italic",
          }}
        >
          {badge.tagline}
        </div>
        <div
          style={{
            fontFamily: MONO,
            fontSize: 11,
            color: "var(--text-tertiary)",
          }}
        >
          {dateLabel} · {badge.consecutive_sessions} sessions
        </div>
      </div>
    </div>
  );
}

export function SkillBadgesPane(): React.ReactElement {
  const [badges, setBadges] = useState<SkillBadge[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/skill-badges`)
      .then((r) => {
        if (!r.ok) throw new Error("Failed to load badges");
        return r.json() as Promise<SkillBadge[]>;
      })
      .then(setBadges)
      .catch(() => setError("Could not load badges"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <div
        style={{
          fontSize: 11,
          fontWeight: 500,
          color: "var(--text-tertiary)",
          textTransform: "uppercase",
          letterSpacing: 0.8,
          marginBottom: 12,
          marginTop: 20,
        }}
      >
        Skill Badges
      </div>

      {loading && (
        <div style={{ fontSize: 13, color: "var(--text-tertiary)", padding: "10px 0" }}>
          Loading…
        </div>
      )}

      {error && (
        <div style={{ fontSize: 13, color: "var(--red)", padding: "10px 0" }}>
          {error}
        </div>
      )}

      {!loading && !error && badges.length === 0 && (
        <div
          style={{
            background: "var(--bg-card)",
            borderRadius: 10,
            padding: "14px 16px",
          }}
        >
          <div
            style={{
              fontSize: 13,
              color: "var(--text-secondary)",
              lineHeight: 1.5,
            }}
          >
            No badges yet — keep coaching.
          </div>
          <div
            style={{
              fontSize: 12,
              color: "var(--text-tertiary)",
              marginTop: 4,
              lineHeight: 1.4,
            }}
          >
            A badge is awarded when a coaching prompt type stops firing for
            3 consecutive sessions — the system's way of saying the skill is yours now.
          </div>
        </div>
      )}

      {!loading && !error && badges.map((b) => <BadgeCard key={b.id} badge={b} />)}
    </div>
  );
}
