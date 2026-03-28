/**
 * TranscriptPane
 *
 * Shows the stored utterances for a session plus the post-session Opus debrief.
 * Loaded via GET /sessions/{id}/transcript and GET /sessions/{id}.
 */

import React, { useEffect, useState } from "react";

const API = "http://localhost:8000";
const MONO = "var(--font-mono)";
const BODY = "var(--font-body)";

interface UtteranceRow {
  sequence: number;
  speaker_id: string;
  text: string;
  start_s: number;
  end_s: number;
  is_user: boolean;
}

interface SessionDetail {
  session_id: string;
  title: string | null;
  context: string;
  persuasion_score: number | null;
  started_at: string;
  debrief_text: string | null;
}

interface TranscriptPaneProps {
  sessionId: string;
}

function fmt(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

const SPEAKER_COLORS = [
  "var(--blue)",
  "var(--gold)",
  "var(--green)",
  "var(--red)",
  "var(--text-secondary)",
];

function speakerColor(speakerId: string): string {
  const idx = parseInt(speakerId.replace(/\D/g, "") || "0", 10);
  return SPEAKER_COLORS[idx % SPEAKER_COLORS.length];
}

export function TranscriptPane({ sessionId }: TranscriptPaneProps): React.ReactElement {
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [utterances, setUtterances] = useState<UtteranceRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);

    Promise.all([
      fetch(`${API}/sessions/${sessionId}`).then((r) => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)),
      fetch(`${API}/sessions/${sessionId}/transcript`).then((r) => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)),
    ])
      .then(([det, utts]: [SessionDetail, UtteranceRow[]]) => {
        setDetail(det);
        setUtterances(utts);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [sessionId]);

  if (loading) {
    return (
      <div style={{ fontSize: 13, color: "var(--text-tertiary)", padding: "16px 0" }}>
        Loading transcript…
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ fontSize: 13, color: "var(--red)", padding: "16px 0" }}>
        Failed to load: {error}
      </div>
    );
  }

  const date = detail ? new Date(detail.started_at).toLocaleDateString("en-US", {
    month: "long", day: "numeric", year: "numeric",
  }) : "";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Session header */}
      {detail && (
        <div>
          <div style={{ fontSize: 16, fontWeight: 500, color: "var(--text-primary)", marginBottom: 2 }}>
            {detail.title || "Untitled session"}
          </div>
          <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
            {date} · {detail.context}
            {detail.persuasion_score != null && (
              <span style={{ fontFamily: MONO, marginLeft: 8 }}>
                {detail.persuasion_score}/100
              </span>
            )}
          </div>
        </div>
      )}

      {/* Opus debrief */}
      {detail?.debrief_text && (
        <div
          style={{
            background: "var(--bg-card)",
            borderRadius: 10,
            padding: "14px 16px",
            borderLeft: "3px solid var(--gold)",
          }}
        >
          <div style={{
            fontSize: 11,
            fontWeight: 500,
            color: "var(--gold)",
            textTransform: "uppercase",
            letterSpacing: 0.8,
            marginBottom: 8,
          }}>
            Coaching debrief
          </div>
          <p style={{
            margin: 0,
            fontFamily: BODY,
            fontSize: 13,
            color: "var(--text-secondary)",
            lineHeight: 1.6,
          }}>
            {detail.debrief_text}
          </p>
        </div>
      )}

      {/* Debrief pending */}
      {detail && !detail.debrief_text && (
        <div style={{
          background: "var(--bg-card)",
          borderRadius: 10,
          padding: "12px 16px",
          fontSize: 12,
          color: "var(--text-tertiary)",
          fontStyle: "italic",
        }}>
          Coaching debrief being prepared…
        </div>
      )}

      {/* Transcript */}
      <div>
        <div style={{
          fontSize: 11,
          fontWeight: 500,
          color: "var(--text-tertiary)",
          textTransform: "uppercase",
          letterSpacing: 0.8,
          marginBottom: 10,
        }}>
          Transcript · {utterances.length} turns
        </div>

        {utterances.length === 0 && (
          <div style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
            No transcript stored for this session.
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {utterances.map((u) => (
            <div
              key={u.sequence}
              style={{
                display: "flex",
                gap: 10,
                alignItems: "flex-start",
                padding: "8px 12px",
                background: u.is_user ? "rgba(255,255,255,0.03)" : "transparent",
                borderRadius: 6,
              }}
            >
              {u.start_s > 0 && (
                <span style={{
                  fontFamily: MONO,
                  fontSize: 10,
                  color: "var(--text-tertiary)",
                  minWidth: 36,
                  paddingTop: 2,
                  flexShrink: 0,
                }}>
                  {fmt(u.start_s)}
                </span>
              )}
              <div style={{ flex: 1, minWidth: 0 }}>
                <span style={{
                  fontFamily: MONO,
                  fontSize: 10,
                  color: speakerColor(u.speaker_id),
                  marginRight: 6,
                }}>
                  {u.is_user ? "YOU" : u.speaker_id}
                </span>
                <span style={{
                  fontFamily: BODY,
                  fontSize: 13,
                  color: "var(--text-primary)",
                  lineHeight: 1.5,
                }}>
                  {u.text}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
