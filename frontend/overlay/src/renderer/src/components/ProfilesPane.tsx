/**
 * ProfilesPane
 *
 * Browsable list of all saved participant profiles with observation log.
 * Supports viewing details, editing name/notes, and adding new profiles
 * via the pre-seed classifier.
 */

import React, { useState, useEffect, useCallback, useRef } from "react";

const API_BASE = "http://localhost:8000";

// ── Types ──────────────────────────────────────────────────────────────────

interface ParticipantSummary {
  id: string;
  name: string | null;
  notes: string | null;
  archetype: string | null;
  confidence: number | null;
  reasoning: string | null;
  sessions_observed: number;
  focus_score: number | null;
  stance_score: number | null;
  created_at: string;
  updated_at: string;
}

interface Observation {
  session_id: string;
  archetype: string;
  focus_score: number;
  stance_score: number;
  confidence: number;
  utterance_count: number;
  context: string;
}

interface ParticipantDetail extends ParticipantSummary {
  observations: Observation[];
}

interface ContextVariation {
  context: string;
  archetype: string | null;
  sessions: number;
  focus_score: number;
  stance_score: number;
}

interface NotableUtterance {
  text: string;
  signals: Record<string, number>;
  strength: number;
  context: string;
}

interface Fingerprint {
  participant_id: string;
  name: string | null;
  archetype: string | null;
  confidence: number | null;
  sessions_observed: number;
  context_variations: ContextVariation[];
  patterns: string[];
  notable_utterances: NotableUtterance[];
  elm_tendencies: Record<string, number>;
  avg_convergence: number;
  avg_uptake_ratio: number;
}

// ── Constants ──────────────────────────────────────────────────────────────

const ARCHETYPE_COLORS: Record<string, string> = {
  Architect:        "var(--gold)",
  Firestarter:      "var(--red)",
  Inquisitor:       "var(--green)",
  "Bridge Builder": "var(--blue)",
};

const CONFIDENCE_LABEL = (c: number) =>
  c >= 0.8 ? "High" : c >= 0.6 ? "Medium" : "Low";

// ── Styles ─────────────────────────────────────────────────────────────────

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
  minHeight: 60,
};

// ── Sub-components ─────────────────────────────────────────────────────────

function ProfileCard({
  profile,
  onSelect,
  onDelete,
}: {
  profile: ParticipantSummary;
  onSelect: () => void;
  onDelete: () => void;
}): React.ReactElement {
  const color = ARCHETYPE_COLORS[profile.archetype ?? ""] ?? "var(--text-tertiary)";
  const displayName = profile.name || "Unknown";

  return (
    <div
      className="profile-card-row"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 0,
        position: "relative",
      }}
    >
      <button
        onClick={onSelect}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          flex: 1,
          padding: "12px 14px",
          background: "var(--bg-card)",
          border: "1px solid var(--border-subtle)",
          borderRadius: 10,
          cursor: "pointer",
          textAlign: "left",
          transition: "background 120ms ease",
          minWidth: 0,
        }}
        onMouseEnter={e => {
          e.currentTarget.style.background = "var(--bg-elevated)";
          const del = e.currentTarget.parentElement?.querySelector("[data-profile-delete]") as HTMLElement | null;
          if (del) del.style.opacity = "1";
        }}
        onMouseLeave={e => {
          e.currentTarget.style.background = "var(--bg-card)";
          const del = e.currentTarget.parentElement?.querySelector("[data-profile-delete]") as HTMLElement | null;
          if (del) del.style.opacity = "0";
        }}
      >
        {/* Initials circle */}
        <div
          style={{
            width: 36,
            height: 36,
            borderRadius: "50%",
            background: "var(--blue)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: "var(--font-body)",
            fontSize: 14,
            fontWeight: 600,
            color: "var(--bg-primary)",
            flexShrink: 0,
          }}
        >
          {displayName
            .split(" ")
            .map(w => w[0])
            .join("")
            .slice(0, 2)
            .toUpperCase()}
        </div>

        {/* Info */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontFamily: "var(--font-body)",
              fontSize: 14,
              fontWeight: 600,
              color: "var(--text-primary)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {displayName}
          </div>
          <div
            style={{
              fontFamily: "var(--font-body)",
              fontSize: 12,
              color: "var(--text-tertiary)",
              marginTop: 2,
            }}
          >
            {profile.sessions_observed} session{profile.sessions_observed !== 1 ? "s" : ""} observed
          </div>
        </div>

        {/* Archetype badge */}
        {profile.archetype && (
          <div
            style={{
              fontFamily: "var(--font-body)",
              fontSize: 12,
              fontWeight: 600,
              color,
              flexShrink: 0,
            }}
          >
            {profile.archetype}
          </div>
        )}
      </button>

      {/* Delete button — appears on hover */}
      <button
        data-profile-delete
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        style={{
          position: "absolute",
          right: -4,
          top: "50%",
          transform: "translateY(-50%)",
          opacity: 0,
          background: "var(--bg-card)",
          border: "1px solid var(--border-medium)",
          borderRadius: "50%",
          width: 24,
          height: 24,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          cursor: "pointer",
          color: "var(--text-tertiary)",
          fontSize: 14,
          lineHeight: 1,
          transition: "opacity 150ms ease, color 150ms ease, border-color 150ms ease",
          padding: 0,
        }}
        onMouseEnter={e => { e.currentTarget.style.opacity = "1"; e.currentTarget.style.color = "var(--red)"; e.currentTarget.style.borderColor = "var(--red)"; }}
        onMouseLeave={e => { e.currentTarget.style.color = "var(--text-tertiary)"; e.currentTarget.style.borderColor = "var(--border-medium)"; }}
        title="Delete profile"
      >
        ×
      </button>
    </div>
  );
}


function ProfileDetail({
  participantId,
  onBack,
  onDeleted,
}: {
  participantId: string;
  onBack: () => void;
  onDeleted: () => void;
}): React.ReactElement {
  const [detail, setDetail] = useState<ParticipantDetail | null>(null);
  const [fingerprint, setFingerprint] = useState<Fingerprint | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editNotes, setEditNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const fetchDetail = useCallback(async () => {
    try {
      const [detailResp, fpResp] = await Promise.all([
        fetch(`${API_BASE}/participants/${participantId}`),
        fetch(`${API_BASE}/participants/${participantId}/fingerprint`),
      ]);
      if (detailResp.ok) {
        const data = await detailResp.json();
        setDetail(data);
        setEditName(data.name ?? "");
        setEditNotes(data.notes ?? "");
      }
      if (fpResp.ok) {
        setFingerprint(await fpResp.json());
      }
    } finally {
      setLoading(false);
    }
  }, [participantId]);

  useEffect(() => { void fetchDetail(); }, [fetchDetail]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const resp = await fetch(`${API_BASE}/participants/${participantId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: editName.trim() || null, notes: editNotes.trim() || null }),
      });
      if (resp.ok) {
        const updated = await resp.json();
        setDetail(d => d ? { ...d, ...updated } : d);
        setEditing(false);
      }
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    const resp = await fetch(`${API_BASE}/participants/${participantId}`, { method: "DELETE" });
    if (resp.ok || resp.status === 204) {
      onDeleted();
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 40, color: "var(--text-tertiary)", fontFamily: "var(--font-body)", fontSize: 14 }}>
        Loading…
      </div>
    );
  }

  if (!detail) {
    return (
      <div style={{ textAlign: "center", padding: 40, color: "var(--text-tertiary)", fontFamily: "var(--font-body)", fontSize: 14 }}>
        Profile not found.
      </div>
    );
  }

  const color = ARCHETYPE_COLORS[detail.archetype ?? ""] ?? "var(--text-tertiary)";
  const displayName = detail.name || "Unknown";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Back link */}
      <button
        onClick={onBack}
        style={{
          background: "none",
          border: "none",
          cursor: "pointer",
          fontFamily: "var(--font-body)",
          fontSize: 13,
          color: "var(--text-secondary)",
          padding: 0,
          textAlign: "left",
        }}
      >
        ← All profiles
      </button>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <div
          style={{
            width: 48,
            height: 48,
            borderRadius: "50%",
            background: "var(--blue)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: "var(--font-body)",
            fontSize: 18,
            fontWeight: 600,
            color: "var(--bg-primary)",
            flexShrink: 0,
          }}
        >
          {(editing ? editName || displayName : displayName).split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase()}
        </div>
        <div style={{ flex: 1 }}>
          {editing ? (
            <input
              style={{ ...inputStyle, fontSize: 18, fontWeight: 600, padding: "4px 8px" }}
              value={editName}
              onChange={e => setEditName(e.target.value)}
              autoFocus
            />
          ) : (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ fontFamily: "var(--font-body)", fontSize: 18, fontWeight: 600, color: "var(--text-primary)" }}>
                {displayName}
              </div>
              <button
                onClick={() => setEditing(true)}
                style={{
                  background: "none", border: "none", cursor: "pointer",
                  fontFamily: "var(--font-body)", fontSize: 12, color: "var(--gold)", padding: 0,
                }}
              >
                Edit
              </button>
            </div>
          )}
          {detail.archetype && (
            <div style={{ fontFamily: "var(--font-body)", fontSize: 14, color, fontWeight: 600, marginTop: 2 }}>
              {detail.archetype}
              {detail.confidence != null && (
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-tertiary)", fontWeight: 400, marginLeft: 8 }}>
                  {CONFIDENCE_LABEL(detail.confidence)} confidence
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Reasoning */}
      {detail.reasoning && (
        <div
          style={{
            background: "var(--bg-card)",
            borderRadius: 10,
            padding: "12px 14px",
            borderLeft: "3px solid var(--gold)",
          }}
        >
          <div style={{ fontFamily: "var(--font-body)", fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>
            Classification reasoning
          </div>
          <div style={{ fontFamily: "var(--font-body)", fontSize: 13, lineHeight: 1.5, color: "var(--text-secondary)" }}>
            {detail.reasoning}
          </div>
        </div>
      )}

      {/* Axis scores */}
      {detail.focus_score != null && detail.stance_score != null && (
        <div style={{ display: "flex", gap: 10 }}>
          <div style={{ flex: 1, background: "var(--bg-card)", borderRadius: 10, padding: "10px 12px" }}>
            <div style={{ fontFamily: "var(--font-body)", fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Focus axis
            </div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 16, color: "var(--text-primary)", marginTop: 4 }}>
              {detail.focus_score > 0 ? "Logic" : "Narrative"} {Math.abs(Math.round(detail.focus_score))}
            </div>
          </div>
          <div style={{ flex: 1, background: "var(--bg-card)", borderRadius: 10, padding: "10px 12px" }}>
            <div style={{ fontFamily: "var(--font-body)", fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Stance axis
            </div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 16, color: "var(--text-primary)", marginTop: 4 }}>
              {detail.stance_score > 0 ? "Advocate" : "Analyze"} {Math.abs(Math.round(detail.stance_score))}
            </div>
          </div>
        </div>
      )}

      {/* Behavioral patterns */}
      {fingerprint && fingerprint.patterns.length > 0 && (
        <div>
          <div style={{ fontFamily: "var(--font-body)", fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>
            Behavioral patterns
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {fingerprint.patterns.map((p, i) => (
              <div key={i} style={{
                background: "var(--bg-card)",
                borderRadius: 8,
                padding: "8px 12px",
                fontFamily: "var(--font-body)",
                fontSize: 13,
                lineHeight: 1.5,
                color: "var(--text-secondary)",
                borderLeft: "3px solid var(--gold)",
              }}>
                {p}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ELM tendencies */}
      {fingerprint && Object.keys(fingerprint.elm_tendencies).length > 0 && (
        <div>
          <div style={{ fontFamily: "var(--font-body)", fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>
            ELM tendencies
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {Object.entries(fingerprint.elm_tendencies).map(([state, count]) => (
              <div key={state} style={{
                background: state === "ego_threat" ? "rgba(239,68,68,0.15)" : state === "shortcut" ? "rgba(245,158,11,0.15)" : "rgba(59,130,246,0.15)",
                borderRadius: 8,
                padding: "6px 10px",
                fontFamily: "var(--font-body)",
                fontSize: 12,
                color: state === "ego_threat" ? "var(--red)" : state === "shortcut" ? "var(--gold)" : "var(--blue)",
                fontWeight: 600,
              }}>
                {state.replace(/_/g, " ")} ({count}x)
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Context breakdown */}
      {fingerprint && fingerprint.context_variations.length > 1 && (
        <div>
          <div style={{ fontFamily: "var(--font-body)", fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>
            Style by context
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {fingerprint.context_variations.map((cv, i) => {
              const cvColor = ARCHETYPE_COLORS[cv.archetype ?? ""] ?? "var(--text-tertiary)";
              return (
                <div key={i} style={{
                  background: "var(--bg-card)",
                  borderRadius: 8,
                  padding: "8px 12px",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}>
                  <span style={{ fontFamily: "var(--font-body)", fontSize: 13, color: "var(--text-secondary)" }}>
                    {cv.context}
                  </span>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontFamily: "var(--font-body)", fontSize: 12, color: cvColor, fontWeight: 600 }}>
                      {cv.archetype ?? "Undetermined"}
                    </span>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-tertiary)" }}>
                      {cv.sessions} session{cv.sessions !== 1 ? "s" : ""}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Notable observations */}
      {fingerprint && fingerprint.notable_utterances.length > 0 && (
        <div>
          <div style={{ fontFamily: "var(--font-body)", fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>
            Notable observations
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {fingerprint.notable_utterances.map((u, i) => (
              <div key={i} style={{
                background: "var(--bg-card)",
                borderRadius: 8,
                padding: "8px 12px",
              }}>
                <div style={{ fontFamily: "var(--font-body)", fontSize: 13, lineHeight: 1.5, color: "var(--text-secondary)", fontStyle: "italic" }}>
                  &ldquo;{u.text}&rdquo;
                </div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-tertiary)", marginTop: 4 }}>
                  {u.context} &middot; strength {u.strength}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Notes */}
      <div>
        <div style={{ fontFamily: "var(--font-body)", fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>
          Notes
        </div>
        {editing ? (
          <textarea style={textareaStyle} value={editNotes} onChange={e => setEditNotes(e.target.value)} rows={4} placeholder="Add notes about this person..." />
        ) : (
          <div
            onClick={() => setEditing(true)}
            style={{
              fontFamily: "var(--font-body)",
              fontSize: 13,
              lineHeight: 1.5,
              color: detail.notes ? "var(--text-secondary)" : "var(--text-tertiary)",
              fontStyle: detail.notes ? "normal" : "italic",
              cursor: "pointer",
              padding: "8px 10px",
              background: "var(--bg-card)",
              borderRadius: 8,
              border: "1px solid transparent",
              transition: "border-color 150ms ease",
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = "var(--border-medium)"; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = "transparent"; }}
            title="Click to edit"
          >
            {detail.notes || "No notes yet — click to add."}
          </div>
        )}
      </div>

      {/* Save / Cancel bar when editing */}
      {editing && (
        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={() => void handleSave()}
            disabled={saving}
            style={{
              flex: 1, height: 36, background: "var(--gold)", border: "none",
              borderRadius: 10, cursor: "pointer", fontFamily: "var(--font-body)",
              fontSize: 13, fontWeight: 500, color: "var(--bg-primary)",
              opacity: saving ? 0.5 : 1,
            }}
          >
            {saving ? "Saving…" : "Save changes"}
          </button>
          <button
            onClick={() => { setEditing(false); setEditName(detail.name ?? ""); setEditNotes(detail.notes ?? ""); }}
            style={{
              flex: 1, height: 36, background: "none",
              border: "1px solid var(--border-medium)", borderRadius: 10,
              cursor: "pointer", fontFamily: "var(--font-body)",
              fontSize: 13, color: "var(--text-secondary)",
            }}
          >
            Cancel
          </button>
        </div>
      )}

      {/* Observation log */}
      {detail.observations.length > 0 && (
        <div>
          <div style={{ fontFamily: "var(--font-body)", fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>
            Observation log ({detail.observations.length} session{detail.observations.length !== 1 ? "s" : ""})
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {detail.observations.map((obs, i) => {
              const obsColor = ARCHETYPE_COLORS[obs.archetype] ?? "var(--text-tertiary)";
              return (
                <div
                  key={i}
                  style={{
                    background: "var(--bg-card)",
                    borderRadius: 8,
                    padding: "8px 12px",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <div>
                    <span style={{ fontFamily: "var(--font-body)", fontSize: 13, color: obsColor, fontWeight: 600 }}>
                      {obs.archetype}
                    </span>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-tertiary)", marginLeft: 8 }}>
                      {obs.utterance_count} utterances
                    </span>
                  </div>
                  <div style={{ fontFamily: "var(--font-body)", fontSize: 11, color: "var(--text-tertiary)" }}>
                    {obs.context}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Delete with confirmation */}
      {confirmDelete ? (
        <div style={{
          display: "flex", flexDirection: "column", gap: 8, marginTop: 8,
          background: "rgba(239,68,68,0.06)", borderRadius: 10, padding: "12px 14px",
        }}>
          <div style={{ fontFamily: "var(--font-body)", fontSize: 13, color: "var(--red)", fontWeight: 500 }}>
            Delete {displayName}? This cannot be undone.
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              onClick={() => void handleDelete()}
              style={{
                flex: 1, height: 36, background: "var(--red)", border: "none",
                borderRadius: 10, cursor: "pointer", fontFamily: "var(--font-body)",
                fontSize: 13, fontWeight: 500, color: "#fff",
              }}
            >
              Delete permanently
            </button>
            <button
              onClick={() => setConfirmDelete(false)}
              style={{
                flex: 1, height: 36, background: "none",
                border: "1px solid var(--border-medium)", borderRadius: 10,
                cursor: "pointer", fontFamily: "var(--font-body)",
                fontSize: 13, color: "var(--text-secondary)",
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setConfirmDelete(true)}
          style={{
            background: "none",
            border: "1px solid var(--red)",
            borderRadius: 10,
            height: 36,
            cursor: "pointer",
            fontFamily: "var(--font-body)",
            fontSize: 13,
            color: "var(--red)",
            marginTop: 8,
            transition: "background 150ms ease",
          }}
          onMouseEnter={e => { e.currentTarget.style.background = "rgba(239,68,68,0.06)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "none"; }}
        >
          Delete profile
        </button>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export interface ProfilesPaneProps {
  onBack: () => void;
}

export function ProfilesPane({ onBack }: ProfilesPaneProps): React.ReactElement {
  const [profiles, setProfiles] = useState<ParticipantSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  // Add-new form state
  const [addName, setAddName] = useState("");
  const [addText, setAddText] = useState("");
  const [addUrl, setAddUrl] = useState("");
  const [addLoading, setAddLoading] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const urlRef = useRef<HTMLInputElement>(null);

  const isLinkedInUrl = (s: string) => /^https?:\/\/(www\.)?linkedin\.com\/in\/[\w-]+\/?$/i.test(s.trim());

  const fetchProfiles = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/participants`);
      if (!resp.ok) return;
      const data = await resp.json();
      setProfiles(data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void fetchProfiles(); }, [fetchProfiles]);

  const handleListDelete = async (id: string) => {
    const resp = await fetch(`${API_BASE}/participants/${id}`, { method: "DELETE" });
    if (resp.ok || resp.status === 204) {
      setPendingDeleteId(null);
      void fetchProfiles();
    }
  };

  const handleAdd = async () => {
    const trimName = addName.trim();
    const trimText = addText.trim();
    const trimUrl = addUrl.trim();
    const hasUrl = isLinkedInUrl(trimUrl);

    // Must have either (name + text) or a LinkedIn URL
    if (!hasUrl && (!trimName || !trimText)) return;

    setAddLoading(true);
    setAddError(null);
    try {
      const payload: Record<string, string> = {};
      if (trimName) payload.name = trimName;
      if (trimText) payload.text = trimText;
      if (hasUrl) payload.url = trimUrl;

      const resp = await fetch(`${API_BASE}/participants/pre-seed`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      setAddName("");
      setAddText("");
      setAddUrl("");
      setShowAdd(false);
      void fetchProfiles();
    } catch (e) {
      setAddError(e instanceof Error ? e.message : "Failed");
    } finally {
      setAddLoading(false);
    }
  };

  // Detail view
  if (selectedId) {
    return (
      <ProfileDetail
        participantId={selectedId}
        onBack={() => { setSelectedId(null); void fetchProfiles(); }}
        onDeleted={() => { setSelectedId(null); void fetchProfiles(); }}
      />
    );
  }

  // List view
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Section header */}
      <div>
        <div style={{
          fontFamily: "var(--font-display)",
          fontSize: 24,
          fontWeight: 600,
          color: "var(--gold)",
          lineHeight: 1.15,
          marginBottom: 4,
        }}>
          Your Network
        </div>
        <div style={{
          fontFamily: "var(--font-body)",
          fontSize: 13,
          color: "var(--text-secondary)",
        }}>
          {profiles.length} {profiles.length === 1 ? "person" : "people"} profiled
        </div>
      </div>

      {/* Add new profile button / form */}
      {showAdd ? (
        <div
          style={{
            background: "var(--bg-card)",
            borderRadius: 12,
            padding: "14px 16px",
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          <div style={{ fontFamily: "var(--font-body)", fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
            Add participant
          </div>

          {/* LinkedIn URL input */}
          <div style={{ position: "relative" }}>
            <input
              ref={urlRef}
              style={{
                ...inputStyle,
                paddingLeft: 32,
                fontFamily: "var(--font-mono)",
                fontSize: 13,
              }}
              value={addUrl}
              onChange={e => setAddUrl(e.target.value)}
              placeholder="LinkedIn URL (optional)"
              onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void handleAdd(); }}
            />
            <span style={{
              position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)",
              fontSize: 14, color: isLinkedInUrl(addUrl) ? "var(--blue)" : "var(--text-tertiary)",
              transition: "color 150ms ease",
            }}>
              in
            </span>
          </div>

          {isLinkedInUrl(addUrl) && (
            <div style={{
              fontFamily: "var(--font-body)", fontSize: 12, color: "var(--blue)",
              display: "flex", alignItems: "center", gap: 6,
            }}>
              <span style={{ width: 6, height: 6, borderRadius: 3, background: "var(--blue)", display: "inline-block" }} />
              Profile will be fetched automatically — name and notes are optional
            </div>
          )}

          <div style={{
            height: 1, background: "var(--border-subtle)", margin: "2px 0",
          }} />

          <input
            ref={nameRef}
            style={inputStyle}
            value={addName}
            onChange={e => setAddName(e.target.value)}
            placeholder={isLinkedInUrl(addUrl) ? "Name (auto-detected from LinkedIn)" : "Name"}
            onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void handleAdd(); }}
          />
          <textarea
            style={textareaStyle}
            value={addText}
            onChange={e => setAddText(e.target.value)}
            placeholder={isLinkedInUrl(addUrl) ? "Extra context (optional)" : "LinkedIn bio, email style, meeting notes…"}
            onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void handleAdd(); }}
          />
          {addError && (
            <div style={{ fontFamily: "var(--font-body)", fontSize: 13, color: "var(--red)" }}>{addError}</div>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <button
              onClick={() => void handleAdd()}
              disabled={addLoading || (!isLinkedInUrl(addUrl) && (!addName.trim() || !addText.trim()))}
              style={{
                flex: 1,
                height: 54,
                background: "var(--gold)",
                border: "none",
                borderRadius: 12,
                cursor: "pointer",
                fontFamily: "var(--font-body)",
                fontSize: 16,
                fontWeight: 500,
                color: "var(--bg-primary)",
                opacity: addLoading || (!isLinkedInUrl(addUrl) && (!addName.trim() || !addText.trim())) ? 0.5 : 1,
              }}
            >
              {addLoading ? (isLinkedInUrl(addUrl) ? "Fetching & classifying…" : "Classifying…") : "Classify"}
            </button>
            <button
              onClick={() => { setShowAdd(false); setAddName(""); setAddText(""); setAddUrl(""); setAddError(null); }}
              style={{
                height: 42,
                background: "none",
                border: "1px solid var(--border-medium)",
                borderRadius: 10,
                cursor: "pointer",
                fontFamily: "var(--font-body)",
                fontSize: 13,
                color: "var(--text-secondary)",
                padding: "0 16px",
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => { setShowAdd(true); setTimeout(() => nameRef.current?.focus(), 50); }}
          style={{
            width: "100%",
            height: 50,
            background: "transparent",
            border: "1.5px solid var(--gold)",
            borderRadius: 12,
            cursor: "pointer",
            fontFamily: "var(--font-body)",
            fontSize: 14,
            fontWeight: 500,
            color: "var(--gold)",
            transition: "background 200ms ease",
          }}
          onMouseEnter={e => { e.currentTarget.style.background = "var(--gold-bg)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "transparent"; }}
        >
          + Add participant
        </button>
      )}

      {/* Profile list */}
      {loading ? (
        <div style={{ textAlign: "center", padding: 32, color: "var(--text-tertiary)", fontFamily: "var(--font-body)", fontSize: 14 }}>
          Loading profiles…
        </div>
      ) : profiles.length === 0 ? (
        <div
          style={{
            textAlign: "center",
            padding: "40px 24px",
          }}
        >
          <div style={{
            fontFamily: "var(--font-display)",
            fontSize: 20,
            fontWeight: 600,
            color: "var(--gold)",
            marginBottom: 8,
          }}>
            No profiles yet
          </div>
          <div style={{
            fontFamily: "var(--font-body)",
            fontSize: 13,
            lineHeight: 1.6,
            color: "var(--text-tertiary)",
          }}>
            Add a participant to start building your network intelligence
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {profiles.map(p => (
            <React.Fragment key={p.id}>
              <ProfileCard
                profile={p}
                onSelect={() => setSelectedId(p.id)}
                onDelete={() => setPendingDeleteId(p.id)}
              />
              {pendingDeleteId === p.id && (
                <div style={{
                  display: "flex", alignItems: "center", gap: 8,
                  background: "rgba(239,68,68,0.06)", borderRadius: 8, padding: "8px 12px",
                }}>
                  <span style={{ fontFamily: "var(--font-body)", fontSize: 12, color: "var(--red)", flex: 1 }}>
                    Delete {p.name || "this profile"}?
                  </span>
                  <button
                    onClick={() => void handleListDelete(p.id)}
                    style={{
                      background: "var(--red)", border: "none", borderRadius: 8,
                      padding: "4px 12px", cursor: "pointer",
                      fontFamily: "var(--font-body)", fontSize: 12, fontWeight: 500, color: "#fff",
                    }}
                  >
                    Delete
                  </button>
                  <button
                    onClick={() => setPendingDeleteId(null)}
                    style={{
                      background: "none", border: "1px solid var(--border-medium)", borderRadius: 8,
                      padding: "4px 12px", cursor: "pointer",
                      fontFamily: "var(--font-body)", fontSize: 12, color: "var(--text-secondary)",
                    }}
                  >
                    Cancel
                  </button>
                </div>
              )}
            </React.Fragment>
          ))}
        </div>
      )}
    </div>
  );
}
