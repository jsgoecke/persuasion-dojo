/**
 * Retro import pane — upload audio or transcript file for post-hoc analysis.
 *
 * POST /retro/upload (multipart) → { job_id }
 * GET  /retro/jobs/{job_id}     → { status, progress, total, utterances, scores, participants, debrief }
 */
import React, { useState, useRef, useEffect } from "react";

const API = "http://localhost:8000";
const MONO = "'Geist Mono', 'SF Mono', monospace";

interface Utterance {
  speaker_id: string;
  text: string;
  start: number;
}

interface ParticipantProfile {
  speaker_id: string;
  name?: string;
  archetype: string;
  confidence: number;
  participant_id?: string;
}

interface Scores {
  persuasion_score: number;
  timing_score: number;
  ego_safety_score: number;
  convergence_score: number;
}

interface Job {
  status: "pending" | "processing" | "done" | "error";
  progress: number;
  total: number;
  utterances?: Utterance[];
  scores?: Scores;
  participants?: ParticipantProfile[];
  debrief?: string;
  session_id?: string;
  error?: string;
}

interface RetroImportPaneProps {
  onBack: () => void;
  onViewSession?: (sessionId: string) => void;
  /** Lifted job ID — survives unmount so back button doesn't kill the analysis. */
  activeJobId?: string | null;
  onJobIdChange?: (jobId: string | null) => void;
}

const ARCHETYPE_COLORS: Record<string, string> = {
  Architect: "#0EA5E9",
  Firestarter: "#F59E0B",
  Inquisitor: "#A855F7",
  "Bridge Builder": "#10B981",
  Unknown: "#6B7280",
};

export function RetroImportPane({ onBack, onViewSession, activeJobId, onJobIdChange }: RetroImportPaneProps): React.ReactElement {
  const [file, setFile] = useState<File | null>(null);
  // Use lifted jobId from parent if provided, otherwise local state
  const [localJobId, setLocalJobId] = useState<string | null>(null);
  const jobId = activeJobId ?? localJobId;
  const setJobId = (id: string | null) => {
    setLocalJobId(id);
    onJobIdChange?.(id);
  };
  const [job, setJob] = useState<Job | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // On mount, if there's an active job from a previous visit, start polling it
  useEffect(() => {
    if (!jobId) return;
    // Immediately fetch current state
    fetch(`${API}/retro/jobs/${jobId}`)
      .then((res) => res.ok ? res.json() : null)
      .then((data) => { if (data) setJob(data); })
      .catch(() => {});

    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API}/retro/jobs/${jobId}`);
        if (!res.ok) return;
        const data: Job = await res.json();
        setJob(data);
        if (data.status === "error") {
          if (pollRef.current) clearInterval(pollRef.current);
        }
        if (data.status === "done" && data.debrief) {
          if (pollRef.current) clearInterval(pollRef.current);
        }
      } catch {
        // polling — ignore transient errors
      }
    }, 1000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [jobId]);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>): void {
    const f = e.target.files?.[0] ?? null;
    setFile(f);
    setJob(null);
    setJobId(null);
    setError(null);
  }

  async function upload(): Promise<void> {
    if (!file) return;
    setUploading(true);
    setError(null);
    setJob(null);
    setJobId(null);

    const form = new FormData();
    form.append("file", file);

    try {
      const res = await fetch(`${API}/retro/upload`, { method: "POST", body: form });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail ?? `HTTP ${res.status}`);
      }
      const { job_id } = await res.json();
      setJobId(job_id);
    } catch (e) {
      setError(String(e));
    } finally {
      setUploading(false);
    }
  }

  function handleReset(): void {
    setFile(null);
    setJob(null);
    setJobId(null);
    setError(null);
    if (fileRef.current) fileRef.current.value = "";
  }

  const container: React.CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 12,
    padding: "14px 16px",
    fontFamily: "var(--font-body)",
    color: "var(--text-primary)",
  };

  const primaryBtn = (disabled: boolean): React.CSSProperties => ({
    background: "var(--gold)",
    border: "none",
    borderRadius: 12,
    color: "var(--bg-primary)",
    fontSize: 16,
    fontWeight: 500,
    height: 54,
    padding: "0",
    cursor: disabled ? "default" : "pointer",
    width: "100%",
    opacity: disabled ? 0.5 : 1,
    fontFamily: "var(--font-body)",
  });

  const ghostBtn: React.CSSProperties = {
    background: "var(--bg-card)",
    border: "1px solid var(--border-medium)",
    borderRadius: 10,
    color: "var(--text-secondary)",
    fontSize: 12,
    padding: "6px 10px",
    cursor: "pointer",
    width: "100%",
    height: 42,
    textAlign: "left" as const,
    fontFamily: "var(--font-body)",
  };

  const pct = job && job.total > 0 ? Math.round((job.progress / job.total) * 100) : 0;
  const isDone = job?.status === "done";

  // ── Completed state: show results ──────────────────────────────────────
  if (isDone) {
    return (
      <div style={container}>
        {/* Success header */}
        <div style={{
          background: "rgba(16, 185, 129, 0.1)",
          border: "1px solid #10B981",
          borderRadius: 10,
          padding: "12px 16px",
          fontSize: 13,
          color: "#10B981",
          lineHeight: 1.5,
          textAlign: "center",
        }}>
          Analysis complete — {job.utterances?.length ?? 0} utterances processed
        </div>

        {/* Persuasion Score */}
        {job.scores && (
          <div style={{ background: "var(--bg-card)", borderRadius: 12, padding: 20, textAlign: "center" }}>
            <div style={{ fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 8 }}>
              Persuasion Score
            </div>
            <div style={{ fontSize: 48, fontWeight: 600, color: "var(--text-primary)", fontFamily: MONO, lineHeight: 1 }}>
              {job.scores.persuasion_score}
            </div>
            <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 8 }}>out of 100</div>

            {/* Breakdown */}
            <div style={{ display: "flex", justifyContent: "center", gap: 16, marginTop: 16 }}>
              {[
                { label: "Timing", value: job.scores.timing_score, max: 30 },
                { label: "Ego Safety", value: job.scores.ego_safety_score, max: 30 },
                { label: "Convergence", value: job.scores.convergence_score, max: 40 },
              ].map((s, i) => (
                <div key={i} style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 20, fontWeight: 500, color: "var(--text-primary)", fontFamily: MONO }}>{s.value}</div>
                  <div style={{ fontSize: 10, color: "var(--text-tertiary)" }}>{s.label} /{s.max}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Participant profiles */}
        {job.participants && job.participants.length > 0 && (
          <>
            <div style={{ fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: 0.8, marginTop: 4 }}>
              Participant profiles
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {job.participants.map((p, i) => (
                <div key={i} style={{
                  background: "var(--bg-card)",
                  borderRadius: 10,
                  padding: "10px 14px",
                  borderLeft: `3px solid ${p.participant_id ? "#0EA5E9" : "var(--gold)"}`,
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontSize: 13, color: "var(--text-secondary)", fontWeight: 600 }}>{p.name || p.speaker_id}</span>
                      <span style={{ fontSize: 11, color: p.participant_id ? "var(--text-tertiary)" : "var(--gold)" }}>
                        {p.participant_id ? "profile updated" : "new profile"}
                      </span>
                    </div>
                    <span style={{
                      fontSize: 11,
                      fontWeight: 600,
                      color: ARCHETYPE_COLORS[p.archetype] || "var(--text-primary)",
                      textTransform: "uppercase",
                      letterSpacing: 0.5,
                    }}>
                      {p.archetype} ({Math.round(p.confidence * 100)}%)
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

        {/* Coaching debrief */}
        <div style={{ fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: 0.8, marginTop: 4 }}>
          Coaching debrief
        </div>
        {job.debrief ? (
          <div style={{
            background: "var(--bg-card)",
            borderRadius: 10,
            padding: "14px 16px",
            fontSize: 14,
            color: "var(--text-secondary)",
            lineHeight: 1.6,
          }}>
            {job.debrief}
          </div>
        ) : (
          <div style={{
            background: "var(--bg-card)",
            borderRadius: 10,
            padding: "14px 16px",
            fontSize: 13,
            color: "var(--text-tertiary)",
            textAlign: "center",
          }}>
            Generating coaching debrief…
          </div>
        )}

        {/* Transcript preview */}
        {job.utterances && job.utterances.length > 0 && (
          <>
            <div style={{ fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: 0.8, marginTop: 4 }}>
              Transcript ({job.utterances.length} utterances)
            </div>
            <div style={{
              maxHeight: 160,
              overflowY: "auto",
              background: "var(--bg-card)",
              borderRadius: 8,
              padding: "10px 12px",
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}>
              {job.utterances.slice(0, 20).map((u, i) => (
                <div key={i} style={{ display: "flex", gap: 8 }}>
                  <span style={{
                    fontSize: 10,
                    color: "var(--text-tertiary)",
                    fontFamily: MONO,
                    minWidth: 52,
                    paddingTop: 2,
                  }}>
                    {formatTime(u.start)}
                  </span>
                  <div>
                    <span style={{ fontSize: 10, color: "var(--gold)", marginRight: 6 }}>
                      {u.speaker_id}
                    </span>
                    <span style={{ fontSize: 12, color: "var(--text-primary)", lineHeight: 1.45 }}>
                      {u.text}
                    </span>
                  </div>
                </div>
              ))}
              {job.utterances.length > 20 && (
                <div style={{ fontSize: 11, color: "var(--text-tertiary)", textAlign: "center", paddingTop: 4 }}>
                  + {job.utterances.length - 20} more…
                </div>
              )}
            </div>
          </>
        )}

        {/* View session / Analyze another */}
        {job.session_id && onViewSession && (
          <button
            style={{ ...primaryBtn(false), marginTop: 8 }}
            onClick={() => onViewSession(job.session_id!)}
          >
            View full session
          </button>
        )}
        <button
          style={{ ...ghostBtn, textAlign: "center", marginTop: job.session_id && onViewSession ? 4 : 8 }}
          onClick={handleReset}
        >
          Analyze another file
        </button>
      </div>
    );
  }

  // ── Upload state ───────────────────────────────────────────────────────
  return (
    <div style={container}>
      <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.5 }}>
        Upload a meeting recording (WAV, MP3, M4A) or a text transcript (.txt, .json, .md) to analyze retroactively.
        Audio files require a Deepgram API key in Settings. Text transcripts are parsed locally.
      </div>

      <input
        ref={fileRef}
        type="file"
        accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.txt,.json,.jsonl,.md"
        style={{ display: "none" }}
        onChange={handleFileChange}
      />
      <button style={ghostBtn} onClick={() => fileRef.current?.click()}>
        {file ? `📎 ${file.name}` : "Choose file (audio or transcript)…"}
      </button>

      {error && <div style={{ fontSize: 12, color: "var(--red)" }}>{error}</div>}

      <button
        style={primaryBtn(uploading || !file || (job !== null && job.status !== "error"))}
        onClick={upload}
        disabled={uploading || !file || (job !== null && job.status !== "error")}
        aria-label="Analyze"
      >
        {uploading ? "Uploading…" : "Analyze"}
      </button>

      {/* Progress */}
      {job && (job.status === "pending" || job.status === "processing") && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
            {job.status === "pending" ? "Queued…" : `Processing — ${pct}%`}
          </div>
          <div style={{ height: 3, background: "var(--bg-card)", borderRadius: 2 }}>
            <div style={{
              height: "100%",
              background: "var(--gold)",
              borderRadius: 2,
              width: `${pct}%`,
              transition: "width 0.5s ease",
            }} />
          </div>
        </div>
      )}

      {/* Error state */}
      {job?.status === "error" && (
        <div style={{ fontSize: 12, color: "var(--red)" }}>
          Analysis failed: {job.error ?? "unknown error"}
        </div>
      )}
    </div>
  );
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
