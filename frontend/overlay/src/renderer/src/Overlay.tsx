/**
 * Overlay — Root component for the Persuasion Dojo companion panel.
 *
 * Navigation map (from brief):
 *   Home
 *     ├─> Go Live ─> Pre-session Setup ─> Live Session ─> Post-session Review ─> Home
 *     ├─> Prepare ─> Preparation Hub
 *     │     ├─> Spar Setup ─> Sparring Session ─> Post-session Review ─> Home
 *     │     ├─> Rehearse Setup ─> Rehearsal Session ─> Post-session Review ─> Home
 *     │     └─> Post Coach ─> POST /coach/text ─> Coaching tips
 *     ├─> Self Assessment ─> Assessment Questions ─> Reveal ─> Home
 *     ├─> Profiles (stub)
 *     └─> Settings
 *
 * Every sub-screen: ← Back (13px, #9A9890, hover #E8E6E1) top-left,
 * title centered, spacer right.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import type { Layer } from "./types";
import { useCoachingSocket } from "./hooks/useCoachingSocket";
import { CalendarPane } from "./components/CalendarPane";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { HistoryTray } from "./components/HistoryTray";
import { RetroImportPane } from "./components/RetroImportPane";
import { SettingsPane } from "./components/SettingsPane";
import { SparringPane } from "./components/SparringPane";
import { PreSeedPane } from "./components/PreSeedPane";
import { ProfilesPane } from "./components/ProfilesPane";
import { SkillBadgesPane } from "./components/SkillBadgesPane";
import { TeamSyncPane } from "./components/TeamSyncPane";
import { TranscriptPane } from "./components/TranscriptPane";

// ── Design tokens (from reference HTML) ──────────────────────────────────────
const DISPLAY = "var(--font-display)";
const BODY    = "var(--font-body)";
const MONO    = "var(--font-mono)";

// ── Types ────────────────────────────────────────────────────────────────────
type Screen =
  | "home"
  | "setup"       // pre-session setup (Go Live flow)
  | "live"
  | "review"
  | "prepare"     // preparation hub
  | "spar-setup"
  | "spar-live"
  | "rehearse-setup"
  | "rehearse-live"
  | "assessment"
  | "reveal"
  | "settings"
  | "profiles"
  | "retro"
  | "post-coach"
  | "transcript"
  | "calendar"
  | "team-sync"
  | "all-sessions";

interface RecentSession {
  session_id: string;
  title: string | null;
  context: string;
  persuasion_score: number | null;
  started_at: string;
}

type Archetype = "architect" | "firestarter" | "inquisitor" | "bridgebuilder";
type Difficulty = "warmup" | "challenge" | "adversarial";

const LAYER_CYCLE: Layer[] = ["audience", "self", "group"];

// ── Timer hook ───────────────────────────────────────────────────────────────
function fmt(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function useTimer(running: boolean): number {
  const [elapsed, setElapsed] = useState(0);
  const t0 = useRef(0);
  useEffect(() => {
    if (!running) { setElapsed(0); return; }
    t0.current = Date.now();
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - t0.current) / 1000)), 1000);
    return () => clearInterval(id);
  }, [running]);
  return elapsed;
}

// ── Assessment data ──────────────────────────────────────────────────────────
const ASSESSMENT_QUESTIONS = [
  {
    scenario: "Your team disagrees on which market to enter first. In the meeting, you are most likely to:",
    options: [
      { text: "Present a data-driven comparison of both markets and argue for the stronger one.", archetype: "architect" as Archetype },
      { text: "Tell the story of a customer in one market who desperately needs what we build.", archetype: "firestarter" as Archetype },
      { text: "Ask probing questions about what assumptions each side is making.", archetype: "inquisitor" as Archetype },
      { text: "Summarize what both sides agree on and propose a way to test both hypotheses.", archetype: "bridgebuilder" as Archetype },
    ],
  },
  {
    scenario: "A key stakeholder pushes back on your proposal during a board meeting. Your instinct is to:",
    options: [
      { text: "Pull up the supporting data and walk through the logic step by step.", archetype: "architect" as Archetype },
      { text: "Share a vivid example of what happens if we don't act now.", archetype: "firestarter" as Archetype },
      { text: "Ask them to explain the specific concern so you can address it directly.", archetype: "inquisitor" as Archetype },
      { text: "Acknowledge their concern and find the overlap between your positions.", archetype: "bridgebuilder" as Archetype },
    ],
  },
  {
    scenario: "You need to convince a skeptical executive to fund your project. You lead with:",
    options: [
      { text: "An ROI model with three scenarios and clear assumptions.", archetype: "architect" as Archetype },
      { text: "A compelling narrative about the future this investment enables.", archetype: "firestarter" as Archetype },
      { text: "Questions about what evidence would change their mind.", archetype: "inquisitor" as Archetype },
      { text: "Testimonials from other executives who initially had the same doubts.", archetype: "bridgebuilder" as Archetype },
    ],
  },
  {
    scenario: "A meeting is going in circles with no resolution. You step in by:",
    options: [
      { text: "Proposing a framework to evaluate the options systematically.", archetype: "architect" as Archetype },
      { text: "Painting a picture of the cost of indecision to create urgency.", archetype: "firestarter" as Archetype },
      { text: "Asking what's really blocking the decision that nobody is saying.", archetype: "inquisitor" as Archetype },
      { text: "Naming the two positions clearly and suggesting a compromise path.", archetype: "bridgebuilder" as Archetype },
    ],
  },
];

const ARCHETYPE_INFO: Record<Archetype, { name: string; axes: string; description: string; blindspot: string }> = {
  architect: {
    name: "Architect",
    axes: "Logic + Advocate",
    description: "You build irrefutable cases. Your natural instinct is to lead with evidence and advocate for the strongest position.",
    blindspot: "You can come across as emotionally distant. The Dojo will teach you to wrap your data in a compelling story.",
  },
  firestarter: {
    name: "Firestarter",
    axes: "Narrative + Advocate",
    description: "You inspire action through story. Your energy and conviction move people before the data does.",
    blindspot: "You can overwhelm analytical thinkers. The Dojo will teach you to anchor your narrative in hard evidence.",
  },
  inquisitor: {
    name: "Inquisitor",
    axes: "Logic + Analyze",
    description: "You question everything. Your forensic approach uncovers assumptions others miss.",
    blindspot: "You can seem adversarial when you're just being thorough. The Dojo will teach you to frame questions as collaboration.",
  },
  bridgebuilder: {
    name: "Bridge Builder",
    axes: "Narrative + Analyze",
    description: "You read the room and build consensus. You find the common ground others can't see.",
    blindspot: "You can prioritize harmony over progress. The Dojo will teach you when to push instead of bridge.",
  },
};

// ═════════════════════════════════════════════════════════════════════════════
// Main component
// ═════════════════════════════════════════════════════════════════════════════

export function Overlay(): React.ReactElement {
  const {
    sessionId: liveSessionId, connectionState, sessionPhase, currentPrompt, prompts, sessionResult,
    errorMessage, audioLevel, transcripts, startSession, endSession, dismissPrompt, clearError, resetSession,
  } = useCoachingSocket();

  const [screen, setScreen]                 = useState<Screen>("home");
  const [activeLayer, setActiveLayer]       = useState<Layer>("audience");
  const [historyOpen, setHistoryOpen]       = useState(false);
  const [meetingName, setMeetingName]       = useState("");
  const [userArchetype, setUserArchetype]   = useState<Archetype | null>(null);

  // Assessment state
  const [assessmentQ, setAssessmentQ]       = useState(0);
  const [assessmentAnswers, setAssessmentAnswers] = useState<Archetype[]>([]);
  const [selectedOption, setSelectedOption] = useState<number | null>(null);
  const [assessmentName, setAssessmentName] = useState("");
  const [assessmentStep, setAssessmentStep] = useState<"name" | "questions">("name");

  // Spar state
  const [sparArchetype, setSparArchetype]   = useState<Archetype | null>(null);
  const [sparTopic, setSparTopic]           = useState("");
  const [sparDifficulty, setSparDifficulty] = useState<Difficulty>("challenge");

  // Rehearse state
  const [rehearseContact, setRehearseContact] = useState<number | null>(null);
  const [rehearseTopic, setRehearseTopic]   = useState("");

  // Recent sessions (from backend)
  const [recentSessions, setRecentSessions] = useState<RecentSession[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [allSessions, setAllSessions] = useState<RecentSession[]>([]);
  const [allSessionsQuery, setAllSessionsQuery] = useState("");

  // Post coach state
  const [postCoachText, setPostCoachText]     = useState("");
  const [postCoachContext, setPostCoachContext] = useState("");
  const [postCoachTips, setPostCoachTips]     = useState<string[]>([]);
  const [postCoachOverall, setPostCoachOverall] = useState("");
  const [postCoachLoading, setPostCoachLoading] = useState(false);

  // Session debrief (polled after session ends)
  const [reviewDebrief, setReviewDebrief] = useState<string | null>(null);

  // Retro import — lift jobId so it survives navigation away and back
  const [activeRetroJobId, setActiveRetroJobId] = useState<string | null>(null);

  // Audio status (from Electron main process via Swift binary OR backend no_audio)
  const [audioStatus, setAudioStatus] = useState<{ type: string; message: string } | null>(null);
  const [audioToastDismissed, setAudioToastDismissed] = useState(false);

  // Listen for audio pipeline status from the main process
  useEffect(() => {
    const api = (window as unknown as { api?: { onAudioStatus?: (h: (s: { type: string; message: string }) => void) => () => void } }).api;
    if (!api?.onAudioStatus) return;
    return api.onAudioStatus((status) => {
      if (status.type === "running") {
        // AudioCapture is confirmed working — clear any error toast
        setAudioStatus(null);
        setAudioToastDismissed(false);
        return;
      }
      setAudioStatus(status);
      setAudioToastDismissed(false); // re-show on new events
    });
  }, []);

  // Surface backend no_audio as the toast
  useEffect(() => {
    if (errorMessage && connectionState === "connected") {
      setAudioStatus({ type: "no_audio", message: errorMessage });
      setAudioToastDismissed(false);
    }
  }, [errorMessage, connectionState]);

  // Re-show toast when entering live screen if there's an existing audio issue
  useEffect(() => {
    if (screen === "live" && audioStatus) {
      setAudioToastDismissed(false);
    }
  }, [screen]);

  // If on the live screen for 8s with no prompts and no audio status yet, check if AudioCapture is alive
  useEffect(() => {
    if (screen !== "live") return;
    const timer = setTimeout(() => {
      if (!currentPrompt && !audioStatus) {
        const api = (window as unknown as { api?: { isAudioRunning?: () => boolean } }).api;
        const running = api?.isAudioRunning?.() ?? false;
        if (!running) {
          setAudioStatus({
            type: "binary_missing",
            message: "Audio capture is not running. Restart the app to try again.",
          });
        }
        // If running but no prompts, audio is flowing — just no speech detected yet. Don't show toast.
      }
    }, 8000);
    return () => clearTimeout(timer);
  }, [screen, currentPrompt]);

  const openScreenRecordingSettings = useCallback(() => {
    const api = (window as unknown as { api?: { openScreenRecording?: () => void } }).api;
    api?.openScreenRecording?.();
  }, []);

  // Fetch recent sessions when navigating to home, or when search query changes
  useEffect(() => {
    if (screen !== "home") return;
    const params = new URLSearchParams({ limit: "5" });
    if (searchQuery.trim()) params.set("q", searchQuery.trim());
    const id = setTimeout(() => {
      fetch(`http://localhost:8000/sessions?${params}`)
        .then(r => r.ok ? r.json() : [])
        .then((data: RecentSession[]) => setRecentSessions(data))
        .catch(() => {});
    }, searchQuery ? 300 : 0);
    return () => clearTimeout(id);
  }, [screen, searchQuery]);

  // Fetch all sessions for the all-sessions screen
  useEffect(() => {
    if (screen !== "all-sessions") return;
    const params = new URLSearchParams({ limit: "200" });
    if (allSessionsQuery.trim()) params.set("q", allSessionsQuery.trim());
    const id = setTimeout(() => {
      fetch(`http://localhost:8000/sessions?${params}`)
        .then(r => r.ok ? r.json() : [])
        .then((data: RecentSession[]) => setAllSessions(data))
        .catch(() => {});
    }, allSessionsQuery ? 300 : 0);
    return () => clearTimeout(id);
  }, [screen, allSessionsQuery]);

  // Poll for debrief on review screen (Opus generates it in background after session ends)
  useEffect(() => {
    if (screen !== "review" || !liveSessionId) return;
    setReviewDebrief(null);
    let stopped = false;
    const poll = async () => {
      for (let i = 0; i < 30 && !stopped; i++) {
        try {
          const res = await fetch(`http://localhost:8000/sessions/${liveSessionId}`);
          if (res.ok) {
            const data = await res.json();
            if (data.debrief_text) {
              setReviewDebrief(data.debrief_text);
              return;
            }
          }
        } catch { /* ignore */ }
        await new Promise(r => setTimeout(r, 3000));
      }
    };
    void poll();
    return () => { stopped = true; };
  }, [screen, liveSessionId]);

  // Load archetype from localStorage
  useEffect(() => {
    const s = localStorage.getItem("pdojo:archetype");
    if (s && s in ARCHETYPE_INFO) setUserArchetype(s as Archetype);
  }, [screen]);

  // Load user display name from backend
  useEffect(() => {
    fetch("http://localhost:8000/users/me")
      .then(r => r.json())
      .then(d => {
        if (d.display_name && d.display_name !== "Local User") {
          setAssessmentName(d.display_name);
        }
      })
      .catch(() => {});
  }, []);

  const elapsed = useTimer(screen === "live" || screen === "spar-live" || screen === "rehearse-live");

  // Sync active layer with incoming prompts
  useEffect(() => { if (currentPrompt) setActiveLayer(currentPrompt.layer); }, [currentPrompt?.received_at]);
  useEffect(() => { if (connectionState === "connected" && screen === "setup") setScreen("live"); }, [connectionState, screen]);
  useEffect(() => {
    if (sessionPhase === "ended") {
      // If we have results go to review, otherwise just go home.
      if (sessionResult) { setScreen("review"); }
      else { resetSession(); setScreen("home"); }
    }
  }, [sessionPhase, sessionResult]);

  const cycleLayer    = useCallback(() => setActiveLayer(p => LAYER_CYCLE[(LAYER_CYCLE.indexOf(p) + 1) % 3]), []);
  const toggleHistory = useCallback(() => setHistoryOpen(o => !o), []);

  // Hotkeys
  useEffect(() => {
    const api = (window as unknown as { api?: { onHotkey?: (h: (a: string) => void) => () => void } }).api;
    if (!api?.onHotkey) return;
    return api.onHotkey((action: string) => {
      if (action === "overlay:dismiss-prompt") dismissPrompt();
      else if (action === "overlay:cycle-layer") cycleLayer();
      else if (action === "overlay:toggle-history") toggleHistory();
    });
  }, [dismissPrompt, cycleLayer, toggleHistory]);

  const archetypeLabel = (key: Archetype | null): string => {
    const map: Record<Archetype, string> = {
      architect: "Architect", firestarter: "Firestarter",
      inquisitor: "Inquisitor", bridgebuilder: "Bridge Builder",
    };
    return key ? map[key] : "Unknown";
  };
  const typeAbbrevToFull: Record<string, string> = {
    ARC: "Architect", FIR: "Firestarter", INQ: "Inquisitor", BRI: "Bridge Builder",
  };

  const handleBeginCoaching = useCallback(async () => {
    const participants = [
      { initials: "SC", name: "Sarah Chen", type: "INQ" },
      { initials: "MR", name: "Mike R", type: "ARC" },
      { initials: "KB", name: "Kevin B", type: "BRI" },
    ];
    await startSession({
      userArchetype: archetypeLabel(userArchetype),
      meetingTitle: meetingName || undefined,
      participants: participants.map(p => ({
        name: p.name,
        archetype: typeAbbrevToFull[p.type] || p.type,
      })),
    });
  }, [startSession, userArchetype, meetingName]);
  const handleBackToHome = useCallback(() => {
    resetSession(); setScreen("home"); setMeetingName(""); setHistoryOpen(false);
  }, [resetSession]);

  // ── Top bar (shared by all sub-screens) ────────────────────────────────────
  const topBar = (title: string, onBack: () => void) => (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      marginBottom: 24, height: 20,
    }}>
      <a
        onClick={onBack}
        className="no-drag"
        style={{
          fontSize: 13, color: "var(--text-secondary)", cursor: "pointer",
          textDecoration: "none", transition: "color 200ms ease",
        }}
        onMouseEnter={e => { e.currentTarget.style.color = "var(--text-primary)"; }}
        onMouseLeave={e => { e.currentTarget.style.color = "var(--text-secondary)"; }}
      >
        ← Back
      </a>
      <span style={{ fontSize: 14, fontWeight: 500, color: "var(--text-primary)" }}>{title}</span>
      <span style={{ width: 40 }} />
    </div>
  );

  // ── Shell wrapper ──────────────────────────────────────────────────────────
  const shell = (content: React.ReactNode, noDrag?: boolean) => (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden", position: "relative" }}>
      {!noDrag && <div className="titlebar" />}
      <div style={{ flex: 1, overflow: "auto", padding: "0 28px 32px" }}>
        {content}
      </div>

      {/* Floating audio permission toast */}
      {audioStatus && !audioToastDismissed && (
        <div style={{
          position: "absolute", bottom: 20, left: 16, right: 16,
          background: "var(--bg-card)", border: "1px solid var(--border-medium)",
          borderRadius: 14, padding: "16px 18px",
          boxShadow: "0 8px 32px rgba(0, 0, 0, 0.45), 0 2px 8px rgba(0, 0, 0, 0.3)",
          animation: "promptIn 400ms ease-out",
          zIndex: 100,
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--red)", flexShrink: 0 }} />
              <span style={{ fontSize: 14, fontWeight: 500, color: "var(--text-primary)" }}>
                {audioStatus.type === "permission_denied" ? "Screen Recording access needed" :
                 audioStatus.type === "binary_missing" ? "Audio capture not found" :
                 "No audio detected"}
              </span>
            </div>
            <button
              onClick={() => setAudioToastDismissed(true)}
              style={{
                background: "none", border: "none", cursor: "pointer",
                color: "var(--text-tertiary)", fontSize: 16, lineHeight: 1, padding: "0 0 0 8px",
              }}
            >×</button>
          </div>
          <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.5, marginBottom: 14 }}>
            {audioStatus.type === "permission_denied"
              ? <>Enable <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>Screen Recording</span> for Electron in System Settings → Privacy &amp; Security, then restart the app.</>
              : audioStatus.type === "mic_unavailable"
              ? <>Enable <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>Microphone</span> for Electron in System Settings → Privacy &amp; Security → Microphone, then restart the app.</>
              : audioStatus.type === "binary_missing"
              ? <>Audio capture binary not found or not running. Rebuild with <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>swift build</span> and restart the app.</>
              : <>No audio reaching the server. Check that Screen Recording and Microphone permissions are granted for Electron.</>
            }
          </div>
          {(audioStatus.type === "permission_denied" || audioStatus.type === "no_audio" || audioStatus.type === "mic_unavailable") && (
            <button
              onClick={openScreenRecordingSettings}
              style={{
                display: "flex", alignItems: "center", justifyContent: "center",
                width: "100%", height: 42, background: "var(--gold)", color: "var(--bg-primary)",
                fontFamily: BODY, fontSize: 14, fontWeight: 500, border: "none",
                borderRadius: 10, cursor: "pointer",
                transition: "background 200ms ease",
              }}
              onMouseEnter={e => { e.currentTarget.style.background = "var(--gold-hover)"; }}
              onMouseLeave={e => { e.currentTarget.style.background = "var(--gold)"; }}
            >
              Open System Settings
            </button>
          )}
        </div>
      )}
    </div>
  );

  // ── Connecting / ending overlay (only when actively transitioning) ────────
  if (connectionState === "connecting" || sessionPhase === "ending") {
    return shell(
      <div style={{ paddingTop: 40 }}>
        <ConnectionStatus connectionState={connectionState} sessionPhase={sessionPhase} errorMessage={errorMessage} />
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: HOME
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "home") {
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {/* Title */}
        <div style={{
          fontFamily: DISPLAY, fontSize: 32, fontWeight: 600,
          color: "var(--gold)", lineHeight: 1.08, letterSpacing: -0.3,
          marginBottom: 32,
        }}>
          Persuasion<br />Dojo
        </div>

        {/* Superpower badge */}
        {userArchetype ? (
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 10,
            padding: "9px 16px", background: "var(--gold-bg)",
            border: "1px solid var(--gold-border)", borderRadius: 10,
            marginBottom: 28,
          }}>
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <path d="M9 1L11.5 6.5L17 7.5L13 11.5L14 17L9 14.5L4 17L5 11.5L1 7.5L6.5 6.5L9 1Z"
                fill="currentColor" opacity="0.25" stroke="currentColor" strokeWidth="1" />
            </svg>
            <div>
              <div style={{ fontSize: 14, fontWeight: 500, color: "var(--gold)", lineHeight: 1 }}>
                {ARCHETYPE_INFO[userArchetype].name}
              </div>
              <div style={{ fontSize: 11, color: "rgba(212, 168, 83, 0.55)", lineHeight: 1, marginTop: 2 }}>
                {ARCHETYPE_INFO[userArchetype].axes}
              </div>
            </div>
          </div>
        ) : (
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 10,
            padding: "9px 16px", background: "var(--gold-bg)",
            border: "1px solid var(--gold-border)", borderRadius: 10,
            marginBottom: 28, animation: "shimmer 3s ease-in-out infinite",
          }}>
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <path d="M9 1L11.5 6.5L17 7.5L13 11.5L14 17L9 14.5L4 17L5 11.5L1 7.5L6.5 6.5L9 1Z"
                fill="currentColor" opacity="0.25" stroke="currentColor" strokeWidth="1" />
            </svg>
            <div>
              <div style={{ fontSize: 14, fontWeight: 500, color: "var(--gold)", lineHeight: 1 }}>
                Take assessment
              </div>
              <div style={{ fontSize: 11, color: "rgba(212, 168, 83, 0.55)", lineHeight: 1, marginTop: 2 }}>
                Discover your superpower
              </div>
            </div>
          </div>
        )}

        {/* Go live — primary CTA */}
        <button
          onClick={() => { clearError(); setScreen("setup"); }}
          style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            width: "100%", height: 54, background: "var(--gold)", color: "var(--bg-primary)",
            fontFamily: BODY, fontSize: 16, fontWeight: 500, border: "none",
            borderRadius: 12, cursor: "pointer", letterSpacing: 0.1,
            transition: "background 200ms ease, transform 100ms ease",
            marginBottom: 10,
          }}
          onMouseEnter={e => { e.currentTarget.style.background = "var(--gold-hover)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "var(--gold)"; }}
          onMouseDown={e => { e.currentTarget.style.transform = "scale(0.98)"; }}
          onMouseUp={e => { e.currentTarget.style.transform = "scale(1)"; }}
        >
          Go live
        </button>

        {/* Prepare — outlined */}
        <button
          onClick={() => setScreen("prepare")}
          style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            width: "100%", height: 50, background: "transparent", color: "var(--gold)",
            fontFamily: BODY, fontSize: 16, fontWeight: 500,
            border: "1.5px solid var(--gold)", borderRadius: 12,
            cursor: "pointer", letterSpacing: 0.1,
            transition: "background 200ms ease, transform 100ms ease",
            marginBottom: 12,
          }}
          onMouseEnter={e => { e.currentTarget.style.background = "var(--gold-bg)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "transparent"; }}
          onMouseDown={e => { e.currentTarget.style.transform = "scale(0.98)"; }}
          onMouseUp={e => { e.currentTarget.style.transform = "scale(1)"; }}
        >
          Enter the Dojo
        </button>

        {/* Navigation grid with subtitles */}
        {([
          { label: "Self assessment", sub: "Discover your style", target: "assessment" as Screen, color: "var(--gold)" },
          { label: "Profiles", sub: "People you've met", target: "profiles" as Screen, color: "var(--blue)" },
          { label: "Upload & Analyze", sub: "Review a past meeting", target: "retro" as Screen, color: "var(--green)" },
          { label: "Calendar", sub: "Upcoming meetings", target: "calendar" as Screen, color: "#0EA5E9" },
          { label: "Import / Export", sub: "Share team profiles", target: "team-sync" as Screen, color: "var(--text-tertiary)" },
        ] as const).map(({ label, sub, target, color }) => (
          <div
            key={label}
            onClick={() => setScreen(target)}
            style={{
              width: "100%", display: "flex", flexDirection: "column", alignItems: "flex-start",
              padding: "14px 18px", background: "var(--bg-elevated)", color: "var(--text-primary)",
              fontFamily: BODY, fontSize: 14, fontWeight: 500,
              borderRadius: 12, borderLeft: `3px solid ${color}`,
              cursor: "pointer", transition: "background 200ms ease",
              marginBottom: 8, textAlign: "left",
            }}
            onMouseEnter={e => { e.currentTarget.style.background = "var(--bg-hover)"; }}
            onMouseLeave={e => { e.currentTarget.style.background = "var(--bg-elevated)"; }}
          >
            <span>{label}</span>
            <span style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 3, fontWeight: 400 }}>{sub}</span>
          </div>
        ))}

        {/* Divider */}
        <hr style={{ border: "none", borderTop: "1px solid var(--border-subtle)", margin: "0 0 16px" }} />

        {/* Recent sessions */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: 0.8 }}>
            Recent
          </div>
          <button
            onClick={() => { setAllSessionsQuery(""); setScreen("all-sessions"); }}
            style={{
              background: "var(--bg-card)", border: "1px solid var(--border-subtle)",
              borderRadius: 6, color: "var(--text-tertiary)", fontSize: 12,
              padding: "4px 8px", cursor: "pointer",
              fontFamily: "var(--font-body)",
            }}
          >
            Search…
          </button>
        </div>

        {recentSessions.length === 0 ? (
          <div style={{ fontSize: 13, color: "var(--text-tertiary)", padding: "10px 14px", textAlign: "center" }}>
            No sessions yet
          </div>
        ) : recentSessions.map((s) => {
          const d = new Date(s.started_at);
          const now = Date.now();
          const diff = now - d.getTime();
          const hours = Math.floor(diff / 3_600_000);
          const timeAgo = hours < 1 ? "Just now" : hours < 24 ? `${hours}h ago` : hours < 48 ? "Yesterday" : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
          return (
            <div
              key={s.session_id}
              className="recent-session-row"
              style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                padding: "10px 14px", background: "var(--bg-card)", borderRadius: 10,
                cursor: "pointer", transition: "background 200ms ease", marginBottom: 6,
                position: "relative",
              }}
              onClick={() => { setSelectedSessionId(s.session_id); setScreen("transcript"); }}
              onMouseEnter={e => {
                e.currentTarget.style.background = "var(--bg-elevated)";
                const btn = e.currentTarget.querySelector("[data-delete]") as HTMLElement | null;
                if (btn) btn.style.opacity = "1";
              }}
              onMouseLeave={e => {
                e.currentTarget.style.background = "var(--bg-card)";
                const btn = e.currentTarget.querySelector("[data-delete]") as HTMLElement | null;
                if (btn) btn.style.opacity = "0";
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, color: "var(--text-primary)", fontWeight: 400, lineHeight: 1.2 }}>{s.title || s.context}</div>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 2 }}>
                  {s.context === "retro" ? "Upload & Analyze" : s.context}{s.persuasion_score != null ? ` · Score: ${s.persuasion_score}` : ""}
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)", whiteSpace: "nowrap" }}>{timeAgo}</div>
                <button
                  data-delete
                  onClick={(e) => {
                    e.stopPropagation();
                    fetch(`http://localhost:8000/sessions/${s.session_id}`, { method: "DELETE" })
                      .then(() => setRecentSessions(prev => prev.filter(r => r.session_id !== s.session_id)))
                      .catch(() => {});
                  }}
                  style={{
                    opacity: 0, background: "none", border: "none", cursor: "pointer",
                    color: "var(--red)", fontSize: 15, lineHeight: 1, padding: "2px 4px",
                    transition: "opacity 150ms ease, color 150ms ease",
                  }}
                  onMouseEnter={e => { e.currentTarget.style.color = "#E06B5A"; }}
                  onMouseLeave={e => { e.currentTarget.style.color = "var(--red)"; }}
                  title="Delete session"
                >
                  ×
                </button>
              </div>
            </div>
          );
        })}

        {/* Settings link */}
        <a
          onClick={() => setScreen("settings")}
          style={{
            display: "block", textAlign: "center", fontSize: 12, color: "var(--text-tertiary)",
            cursor: "pointer", padding: "16px 0 0", transition: "color 200ms ease", textDecoration: "none",
          }}
          onMouseEnter={e => { e.currentTarget.style.color = "var(--text-secondary)"; }}
          onMouseLeave={e => { e.currentTarget.style.color = "var(--text-tertiary)"; }}
        >
          Settings
        </a>
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: PREPARATION HUB
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "prepare") {
    const cards = [
      { key: "spar", title: "Spar with an archetype", desc: "Practice against an AI opponent playing any of the four communication styles.", color: "var(--gold)", next: "spar-setup" as Screen },
      { key: "rehearse", title: "Rehearse with a contact", desc: "Debate against one of your saved participant profiles. The AI will model their style.", color: "var(--blue)", next: "rehearse-setup" as Screen },
      { key: "post-coach", title: "Text Coach", desc: "Paste a draft LinkedIn post, email, or message and get persuasion coaching on the text.", color: "var(--green)", next: "post-coach" as Screen },
    ];
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {topBar("The Dojo", () => setScreen("home"))}
        {cards.map(c => (
          <div
            key={c.key}
            onClick={() => setScreen(c.next)}
            style={{
              background: "var(--bg-elevated)", borderRadius: 12, padding: "18px 20px",
              marginBottom: 10, cursor: "pointer", transition: "background 200ms ease",
              borderLeft: `3px solid ${c.color}`,
            }}
            onMouseEnter={e => { e.currentTarget.style.background = "var(--bg-hover)"; }}
            onMouseLeave={e => { e.currentTarget.style.background = "var(--bg-elevated)"; }}
          >
            <div style={{ fontSize: 15, fontWeight: 500, color: "var(--text-primary)", marginBottom: 6 }}>{c.title}</div>
            <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.45, marginBottom: 10 }}>{c.desc}</div>
            <div style={{ fontSize: 12, color: "var(--text-tertiary)", textAlign: "right" }}>Start →</div>
          </div>
        ))}
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: SPAR SETUP
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "spar-setup") {
    const archetypes: { key: Archetype; name: string; axes: string; tint: string }[] = [
      { key: "architect", name: "Architect", axes: "Logic + Advocate", tint: "var(--tint-architect)" },
      { key: "firestarter", name: "Firestarter", axes: "Narrative + Advocate", tint: "var(--tint-firestarter)" },
      { key: "inquisitor", name: "Inquisitor", axes: "Logic + Analyze", tint: "var(--tint-inquisitor)" },
      { key: "bridgebuilder", name: "Bridge Builder", axes: "Narrative + Analyze", tint: "var(--tint-bridgebuilder)" },
    ];
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {topBar("Spar setup", () => setScreen("prepare"))}

        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 6 }}>Choose your opponent</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 8 }}>
          {archetypes.map(a => (
            <div
              key={a.key}
              onClick={() => setSparArchetype(a.key)}
              style={{
                border: `1.5px solid ${sparArchetype === a.key ? "var(--gold)" : "var(--border-subtle)"}`,
                borderRadius: 12, padding: "18px 12px", cursor: "pointer",
                transition: "border-color 200ms ease, background 200ms ease",
                textAlign: "center",
                background: sparArchetype === a.key ? "rgba(212, 168, 83, 0.05)" : a.tint,
              }}
            >
              <div style={{ fontSize: 15, fontWeight: 500, color: "var(--text-primary)", marginBottom: 4 }}>{a.name}</div>
              <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{a.axes}</div>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", padding: "0 4px", marginBottom: 20 }}>
          <span style={{ fontSize: 10, color: "var(--text-tertiary)", letterSpacing: 0.5, textTransform: "uppercase" }}>Logic ←</span>
          <span style={{ fontSize: 10, color: "var(--text-tertiary)", letterSpacing: 0.5, textTransform: "uppercase" }}>→ Narrative</span>
        </div>

        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 6 }}>Topic</div>
        <input
          type="text" value={sparTopic} onChange={e => setSparTopic(e.target.value)}
          placeholder="Should we expand into APAC?"
          style={{
            width: "100%", background: "var(--bg-card)", border: "1px solid var(--border-medium)",
            borderRadius: 10, padding: "11px 14px", fontFamily: BODY, fontSize: 14,
            color: "var(--text-primary)", outline: "none", transition: "border-color 200ms ease",
            marginBottom: 20,
          }}
          onFocus={e => { e.currentTarget.style.borderColor = "var(--gold-border)"; }}
          onBlur={e => { e.currentTarget.style.borderColor = "var(--border-medium)"; }}
        />

        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 6 }}>Difficulty</div>
        <div style={{ display: "flex", background: "var(--bg-card)", borderRadius: 10, padding: 3, gap: 2, marginBottom: 28 }}>
          {(["warmup", "challenge", "adversarial"] as Difficulty[]).map(d => (
            <button
              key={d}
              onClick={() => setSparDifficulty(d)}
              style={{
                flex: 1, padding: "10px 6px", borderRadius: 8, border: "none",
                background: sparDifficulty === d ? "var(--bg-elevated)" : "transparent",
                color: sparDifficulty === d ? "var(--text-primary)" : "var(--text-tertiary)",
                fontFamily: BODY, fontSize: 12, fontWeight: sparDifficulty === d ? 500 : 400,
                cursor: "pointer", transition: "background 200ms ease, color 200ms ease",
                textAlign: "center",
              }}
            >
              {d === "warmup" ? "Warm-up" : d === "challenge" ? "Challenge" : "Adversarial"}
            </button>
          ))}
        </div>

        <button
          onClick={() => setScreen("spar-live")}
          style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            width: "100%", height: 54, background: "var(--gold)", color: "var(--bg-primary)",
            fontFamily: BODY, fontSize: 16, fontWeight: 500, border: "none",
            borderRadius: 12, cursor: "pointer", transition: "background 200ms ease",
          }}
          onMouseEnter={e => { e.currentTarget.style.background = "var(--gold-hover)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "var(--gold)"; }}
        >
          Begin sparring
        </button>
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: REHEARSE SETUP
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "rehearse-setup") {
    const contacts = [
      { initials: "SC", name: "Sarah Chen", type: "Inquisitor", role: "VP Engineering" },
      { initials: "MR", name: "Mike Rodriguez", type: "Architect", role: "CFO" },
      { initials: "KB", name: "Kevin Brown", type: "Bridge Builder", role: "EVP Operations" },
    ];
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {topBar("Rehearse setup", () => setScreen("prepare"))}

        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 6 }}>Choose a contact</div>
        {contacts.map((c, i) => (
          <div
            key={i}
            onClick={() => setRehearseContact(i)}
            style={{
              display: "flex", alignItems: "center", gap: 14, padding: "14px 16px",
              background: rehearseContact === i ? "rgba(212, 168, 83, 0.04)" : "var(--bg-card)",
              border: `1.5px solid ${rehearseContact === i ? "var(--gold)" : "var(--border-subtle)"}`,
              borderRadius: 10, cursor: "pointer",
              transition: "border-color 200ms ease, background 200ms ease",
              marginBottom: 8,
            }}
          >
            <div style={{
              width: 40, height: 40, borderRadius: "50%", background: "var(--blue-bg)",
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 14, fontWeight: 500, color: "var(--blue)", flexShrink: 0,
            }}>
              {c.initials}
            </div>
            <div>
              <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text-primary)" }}>{c.name}</div>
              <div style={{ fontSize: 12, color: "var(--blue)", marginTop: 1 }}>{c.type}</div>
              <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 1 }}>{c.role}</div>
            </div>
          </div>
        ))}

        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 6, marginTop: 20 }}>Topic</div>
        <input
          type="text" value={rehearseTopic} onChange={e => setRehearseTopic(e.target.value)}
          placeholder="Budget approval for Q4 expansion"
          style={{
            width: "100%", background: "var(--bg-card)", border: "1px solid var(--border-medium)",
            borderRadius: 10, padding: "11px 14px", fontFamily: BODY, fontSize: 14,
            color: "var(--text-primary)", outline: "none", transition: "border-color 200ms ease",
            marginBottom: 28,
          }}
          onFocus={e => { e.currentTarget.style.borderColor = "var(--gold-border)"; }}
          onBlur={e => { e.currentTarget.style.borderColor = "var(--border-medium)"; }}
        />

        <button
          onClick={() => setScreen("rehearse-live")}
          style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            width: "100%", height: 54, background: "var(--gold)", color: "var(--bg-primary)",
            fontFamily: BODY, fontSize: 16, fontWeight: 500, border: "none",
            borderRadius: 12, cursor: "pointer", transition: "background 200ms ease",
          }}
          onMouseEnter={e => { e.currentTarget.style.background = "var(--gold-hover)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "var(--gold)"; }}
        >
          Begin rehearsal
        </button>
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: SELF ASSESSMENT (name + questions)
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "assessment") {
    const exitAssessment = () => {
      setAssessmentQ(0); setAssessmentAnswers([]); setSelectedOption(null);
      setAssessmentStep("name"); setScreen("home");
    };

    // Step 1: Name capture
    if (assessmentStep === "name") {
      const handleNameContinue = () => {
        const trimmed = assessmentName.trim();
        if (!trimmed) return;
        // Save name to backend
        fetch("http://localhost:8000/users/me", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_name: trimmed }),
        }).catch(() => {});
        setAssessmentStep("questions");
      };
      return shell(
        <div style={{ animation: "fadeIn 200ms ease-out" }}>
          {topBar("Self assessment", exitAssessment)}
          <div style={{
            fontFamily: DISPLAY, fontSize: 24, fontWeight: 600,
            color: "var(--gold)", lineHeight: 1.15, marginBottom: 8,
          }}>
            Before we start
          </div>
          <div style={{ fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.5, marginBottom: 24 }}>
            What should the coach call you?
          </div>
          <input
            type="text"
            placeholder="Your name"
            value={assessmentName}
            onChange={(e) => setAssessmentName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleNameContinue(); }}
            autoFocus
            style={{
              width: "100%", background: "var(--bg-card)",
              border: "1px solid var(--border-medium)", borderRadius: 10,
              padding: "12px 14px", fontSize: 15, color: "var(--text-primary)",
              outline: "none", boxSizing: "border-box" as const,
              fontFamily: BODY, transition: "border-color 200ms ease",
            }}
            onFocus={(e) => { e.currentTarget.style.borderColor = "var(--gold-border)"; }}
            onBlur={(e) => { e.currentTarget.style.borderColor = "var(--border-medium)"; }}
          />
          <a
            onClick={handleNameContinue}
            style={{
              display: "block", textAlign: "right", marginTop: 20,
              fontSize: 14, fontWeight: 500, color: "var(--gold)", cursor: "pointer",
              textDecoration: "none", opacity: assessmentName.trim() ? 1 : 0.3,
              pointerEvents: assessmentName.trim() ? "auto" : "none",
            }}
          >
            Continue →
          </a>
        </div>,
      );
    }

    // Step 2: Questions
    const q = ASSESSMENT_QUESTIONS[assessmentQ];
    const progress = ((assessmentQ + 1) / ASSESSMENT_QUESTIONS.length) * 100;

    const handleBack = () => {
      setSelectedOption(null);
      if (assessmentQ > 0) {
        setAssessmentAnswers(assessmentAnswers.slice(0, -1));
        setAssessmentQ(assessmentQ - 1);
      } else {
        setAssessmentStep("name");
      }
    };

    const handleNext = () => {
      if (selectedOption === null) return;
      const newAnswers = [...assessmentAnswers, q.options[selectedOption].archetype];
      setAssessmentAnswers(newAnswers);
      setSelectedOption(null);

      if (assessmentQ + 1 < ASSESSMENT_QUESTIONS.length) {
        setAssessmentQ(assessmentQ + 1);
      } else {
        // Tally answers — most frequent archetype wins
        const counts: Record<string, number> = {};
        newAnswers.forEach(a => { counts[a] = (counts[a] || 0) + 1; });
        const winner = Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0] as Archetype;
        setUserArchetype(winner);
        localStorage.setItem("pdojo:archetype", winner);
        setScreen("reveal");
      }
    };

    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {topBar("Self assessment", handleBack)}
        <div style={{
          fontFamily: DISPLAY, fontSize: 24, fontWeight: 600,
          color: "var(--gold)", lineHeight: 1.15, marginBottom: 20,
        }}>
          Discover your<br />superpower
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
          <span style={{ fontSize: 12, color: "var(--text-tertiary)", whiteSpace: "nowrap" }}>
            Question {assessmentQ + 1} of {ASSESSMENT_QUESTIONS.length}
          </span>
          <div style={{ flex: 1, height: 3, background: "var(--bg-card)", borderRadius: 2, overflow: "hidden" }}>
            <div style={{ height: "100%", background: "var(--gold)", borderRadius: 2, transition: "width 400ms ease", width: `${progress}%` }} />
          </div>
        </div>

        <div style={{ fontSize: 15, color: "var(--text-primary)", lineHeight: 1.55, marginBottom: 20 }}>
          {q.scenario}
        </div>

        {q.options.map((opt, i) => (
          <div
            key={i}
            onClick={() => setSelectedOption(i)}
            style={{
              background: selectedOption === i ? "var(--gold-bg-strong)" : "var(--bg-card)",
              border: `1.5px solid ${selectedOption === i ? "var(--gold)" : "var(--border-subtle)"}`,
              borderRadius: 10, padding: "14px 18px", marginBottom: 8, cursor: "pointer",
              transition: "border-color 200ms ease, background 200ms ease",
              fontSize: 14, color: "var(--text-primary)", lineHeight: 1.45,
            }}
          >
            {opt.text}
          </div>
        ))}

        <a
          onClick={handleNext}
          style={{
            display: "block", textAlign: "right", marginTop: 20,
            fontSize: 14, fontWeight: 500, color: "var(--gold)", cursor: "pointer",
            textDecoration: "none", opacity: selectedOption !== null ? 1 : 0.3,
            pointerEvents: selectedOption !== null ? "auto" : "none",
          }}
        >
          Next →
        </a>
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: REVEAL
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "reveal" && userArchetype) {
    const info = ARCHETYPE_INFO[userArchetype];
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out", paddingTop: 20 }}>
        <div style={{
          fontFamily: DISPLAY, fontSize: 24, fontWeight: 600,
          color: "var(--gold)", lineHeight: 1.15, marginBottom: 24,
        }}>
          Your superpower
        </div>

        <div style={{
          background: "var(--gold)", color: "var(--bg-primary)", borderRadius: 16,
          padding: "36px 28px", textAlign: "center",
          animation: "revealIn 600ms ease-out forwards", marginBottom: 24,
        }}>
          <div style={{ fontSize: 24, marginBottom: 8, opacity: 0.7 }}>★</div>
          <div style={{ fontFamily: DISPLAY, fontSize: 30, fontWeight: 600, marginBottom: 6 }}>{info.name}</div>
          <div style={{ fontSize: 14, opacity: 0.65 }}>{info.axes}</div>
        </div>

        <div style={{ fontSize: 15, color: "var(--text-primary)", lineHeight: 1.55, marginBottom: 24 }}>
          {info.description}
        </div>

        <div style={{
          background: "var(--bg-elevated)", borderLeft: "4px solid var(--red)",
          borderRadius: 12, padding: "18px 22px", marginBottom: 32,
        }}>
          <div style={{ fontSize: 12, fontWeight: 500, color: "var(--red)", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 8 }}>
            Your blind spot
          </div>
          <div style={{ fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.5 }}>
            {info.blindspot}
          </div>
        </div>

        <button
          onClick={() => { setAssessmentQ(0); setAssessmentAnswers([]); setSelectedOption(null); setAssessmentStep("name"); setScreen("home"); }}
          style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            width: "100%", height: 54, background: "var(--gold)", color: "var(--bg-primary)",
            fontFamily: BODY, fontSize: 16, fontWeight: 500, border: "none",
            borderRadius: 12, cursor: "pointer", transition: "background 200ms ease",
          }}
          onMouseEnter={e => { e.currentTarget.style.background = "var(--gold-hover)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "var(--gold)"; }}
        >
          Enter the Dojo
        </button>
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: SETTINGS
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "settings") {
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {topBar("Settings", () => setScreen("home"))}
        <SettingsPane onBack={() => setScreen("home")} />
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: PRE-SESSION SETUP (Go Live flow)
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "setup") {
    const setupParticipants = [
      { initials: "SC", name: "Sarah Chen", type: "INQ" },
      { initials: "MR", name: "Mike R", type: "ARC" },
      { initials: "KB", name: "Kevin B", type: "BRI" },
    ];

    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out", display: "flex", flexDirection: "column", flex: 1 }}>
        {topBar("Session setup", () => { clearError(); setScreen("home"); })}

        {/* Inline error (shown when backend is unreachable) */}
        {connectionState === "error" && (
          <div style={{ marginBottom: 20 }}>
            <ConnectionStatus connectionState={connectionState} sessionPhase={sessionPhase} errorMessage={errorMessage} onRetry={() => void startSession()} />
          </div>
        )}

        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 6 }}>Meeting name</div>
        <input
          type="text" value={meetingName} onChange={e => setMeetingName(e.target.value)}
          placeholder="Q3 Board Review" autoFocus
          style={{
            width: "100%", background: "var(--bg-card)", border: "1px solid var(--border-medium)",
            borderRadius: 10, padding: "11px 14px", fontFamily: BODY, fontSize: 14,
            color: "var(--text-primary)", outline: "none", transition: "border-color 200ms ease",
            marginBottom: 20,
          }}
          onFocus={e => { e.currentTarget.style.borderColor = "var(--gold-border)"; }}
          onBlur={e => { e.currentTarget.style.borderColor = "var(--border-medium)"; }}
        />

        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 6 }}>Participants (optional)</div>
        <button
          onClick={() => setScreen("profiles")}
          style={{
            display: "flex", alignItems: "center", gap: 8, width: "100%", padding: "12px 16px",
            background: "transparent", border: "1px dashed var(--border-medium)", borderRadius: 10,
            color: "var(--text-tertiary)", fontFamily: BODY, fontSize: 13, cursor: "pointer",
            transition: "border-color 200ms ease, color 200ms ease", marginBottom: 16,
          }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = "var(--border-hover)"; e.currentTarget.style.color = "var(--text-secondary)"; }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = "var(--border-medium)"; e.currentTarget.style.color = "var(--text-tertiary)"; }}
        >
          + Add participant
        </button>

        {/* Participant chips */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 20 }}>
          {setupParticipants.map(p => (
            <span key={p.initials} style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "6px 14px", background: "var(--blue-bg)", borderRadius: 20,
              fontSize: 12, color: "var(--blue)",
            }}>
              {p.name} <span style={{ fontSize: 10, fontWeight: 500, textTransform: "uppercase", letterSpacing: 0.4, opacity: 0.65 }}>{p.type}</span>
            </span>
          ))}
        </div>

        <div style={{ flex: 1 }} />

        <button
          onClick={handleBeginCoaching}
          style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            width: "100%", height: 54, background: "var(--gold)", color: "var(--bg-primary)",
            fontFamily: BODY, fontSize: 16, fontWeight: 500, border: "none",
            borderRadius: 12, cursor: "pointer", marginTop: 40,
            transition: "background 200ms ease",
          }}
          onMouseEnter={e => { e.currentTarget.style.background = "var(--gold-hover)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "var(--gold)"; }}
        >
          Begin coaching
        </button>
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: LIVE SESSION
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "live") {
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {/* Live bar */}
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "14px 0", borderBottom: "1px solid var(--border-subtle)", marginBottom: 20,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ width: 8, height: 8, background: "var(--red)", borderRadius: "50%", animation: "livePulse 2s ease-in-out infinite" }} />
            <span style={{ fontSize: 12, fontWeight: 500, color: "var(--red)", letterSpacing: 0.5, textTransform: "uppercase" }}>Live</span>
            {/* Audio level meter — 5 bars */}
            <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 14, marginLeft: 4 }}>
              {[0.02, 0.04, 0.08, 0.15, 0.25].map((threshold, i) => {
                const active = audioLevel >= threshold;
                return (
                  <div
                    key={i}
                    style={{
                      width: 3,
                      height: 4 + i * 2.5,
                      borderRadius: 1,
                      background: active ? "var(--green)" : "var(--border-medium)",
                      transition: "background 100ms ease",
                    }}
                  />
                );
              })}
            </div>
            {meetingName && <span style={{ fontSize: 13, color: "var(--text-secondary)", marginLeft: 6 }}>{meetingName}</span>}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <span style={{ fontSize: 13, color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums", fontFamily: MONO }}>{fmt(elapsed)}</span>
            <a onClick={endSession} className="no-drag" style={{ fontSize: 13, color: "var(--text-tertiary)", cursor: "pointer", textDecoration: "none", transition: "color 200ms ease" }}
              onMouseEnter={e => { e.currentTarget.style.color = "var(--text-primary)"; }}
              onMouseLeave={e => { e.currentTarget.style.color = "var(--text-tertiary)"; }}
            >End →</a>
          </div>
        </div>

        {/* Error banner (e.g. Deepgram key invalid, no audio) */}
        {errorMessage && !currentPrompt && (
          <div style={{
            background: "rgba(239, 68, 68, 0.1)", border: "1px solid var(--red)",
            borderRadius: 10, padding: "12px 16px", marginBottom: 14,
            fontSize: 12, color: "var(--red)", lineHeight: 1.5,
          }}>
            {errorMessage}
          </div>
        )}

        {/* Coaching prompt */}
        {currentPrompt ? (
          <div style={{
            background: "var(--bg-elevated)", borderLeft: "4px solid var(--gold)",
            borderRadius: 12, padding: "22px 24px", marginBottom: 20,
            animation: "promptIn 400ms ease-out forwards",
          }}>
            <div style={{ fontSize: 17, fontWeight: 500, color: "var(--gold)", lineHeight: 1.5, marginBottom: 14 }}>
              {currentPrompt.text}
            </div>
            <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.45 }}>
              {currentPrompt.triggered_by && `Triggered by ${currentPrompt.triggered_by}`}
            </div>
          </div>
        ) : (
          <div style={{
            background: "var(--bg-elevated)", borderLeft: "4px solid var(--gold)",
            borderRadius: 12, padding: "22px 24px", marginBottom: 20,
          }}>
            <div style={{ fontSize: 17, fontWeight: 500, color: "var(--gold)", lineHeight: 1.5, opacity: 0.5, animation: "shimmer 3s ease-in-out infinite" }}>
              Listening…
            </div>
          </div>
        )}

        {/* Prompt history tray */}
        {prompts.length > 1 && (
          <div style={{ marginBottom: 12 }}>
            <a
              onClick={toggleHistory}
              style={{
                fontSize: 12, color: "var(--text-tertiary)", cursor: "pointer",
                textDecoration: "none", transition: "color 200ms ease",
                display: "block", marginBottom: 6,
              }}
              onMouseEnter={e => { e.currentTarget.style.color = "var(--text-secondary)"; }}
              onMouseLeave={e => { e.currentTarget.style.color = "var(--text-tertiary)"; }}
            >
              {historyOpen ? "Hide" : "Show"} history ({prompts.length - 1})
            </a>
            <HistoryTray
              prompts={prompts.slice(1)}
              open={historyOpen}
            />
          </div>
        )}

        <hr style={{ border: "none", borderTop: "1px solid var(--border-subtle)", margin: "0 0 16px" }} />

        {/* Transcript */}
        <div style={{ fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 12 }}>
          Transcript
        </div>
        <div style={{ maxHeight: 200, overflowY: "auto", scrollBehavior: "smooth" }}>
          {transcripts.length === 0 ? (
            <div style={{ fontSize: 13, fontFamily: MONO, color: "var(--text-secondary)", lineHeight: 1.55, fontStyle: "italic" }}>
              Waiting for speech…
            </div>
          ) : (
            transcripts.filter(t => t.is_final).slice(-10).map((t, i) => (
              <div key={i} style={{ fontSize: 13, fontFamily: MONO, color: "var(--text-secondary)", lineHeight: 1.55, marginBottom: 4 }}>
                <span style={{ color: "var(--text-tertiary)", fontSize: 11, marginRight: 6 }}>
                  {t.speaker_id.replace("speaker_", "S")}
                </span>
                {t.text}
              </div>
            ))
          )}
        </div>

        {/* Participant bar */}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", paddingTop: 16, borderTop: "1px solid var(--border-subtle)", marginTop: 16 }}>
          {[
            { name: "Sarah", type: "INQ" },
            { name: "Mike", type: "ARC" },
            { name: "Kevin", type: "BRI" },
          ].map(p => (
            <span key={p.name} style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "6px 14px", background: "var(--blue-bg)", borderRadius: 20, fontSize: 12, color: "var(--blue)" }}>
              {p.name} <span style={{ fontSize: 10, fontWeight: 500, textTransform: "uppercase", letterSpacing: 0.4, opacity: 0.65 }}>{p.type}</span>
            </span>
          ))}
        </div>
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: SPARRING SESSION (uses real SparringPane with WebSocket backend)
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "spar-live") {
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        <SparringPane onBack={() => setScreen("home")} />
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: REHEARSAL SESSION (reuses SparringPane — contact rehearsal is
  // the same backend flow, just different initial prompt context)
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "rehearse-live") {
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        <SparringPane onBack={() => setScreen("home")} />
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: POST-SESSION REVIEW
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "review") {
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {topBar("Session review", handleBackToHome)}

        <div style={{ fontSize: 20, fontWeight: 500, color: "var(--text-primary)", marginBottom: 4 }}>
          {meetingName || "Session"}
        </div>
        <div style={{ fontSize: 13, color: "var(--text-tertiary)", marginBottom: 24 }}>
          {new Date().toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })} · {fmt(elapsed)}
        </div>

        {/* Metric grid */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 24 }}>
          {[
            { label: "Persuasion Score", value: sessionResult?.persuasion_score != null ? sessionResult.persuasion_score : "—", color: "var(--gold)" },
            { label: "Prompts delivered", value: prompts.length || "—", color: "var(--blue)" },
            { label: "Growth", value: sessionResult?.growth_delta != null ? `${sessionResult.growth_delta > 0 ? "+" : ""}${sessionResult.growth_delta}` : "—", color: "var(--green)" },
            { label: "Style shifts", value: "—", color: "#0EA5E9" },
          ].map((m, i) => (
            <div key={i} style={{ background: "var(--bg-elevated)", borderRadius: 12, padding: "16px 18px", borderLeft: `3px solid ${m.color}` }}>
              <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 6 }}>{m.label}</div>
              <div style={{ fontSize: 28, fontWeight: 500, color: "var(--text-primary)" }}>{m.value}</div>
            </div>
          ))}
        </div>

        {/* Score breakdown */}
        {sessionResult?.breakdown && (
          <>
            <div style={{ fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 12 }}>
              Score breakdown
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 24 }}>
              {[
                { label: "Timing (30%)", value: sessionResult.breakdown.timing, color: "#F59E0B" },
                { label: "Ego Safety (30%)", value: sessionResult.breakdown.ego_safety, color: "#10B981" },
                { label: "Convergence (40%)", value: sessionResult.breakdown.convergence, color: "#0EA5E9" },
              ].map((s, i) => (
                <div key={i} style={{ background: "var(--bg-elevated)", borderRadius: 12, padding: "14px 18px", display: "flex", justifyContent: "space-between", alignItems: "center", borderLeft: `3px solid ${s.color}` }}>
                  <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>{s.label}</div>
                  <div style={{ fontSize: 18, fontWeight: 500, color: "var(--text-primary)", fontFamily: MONO }}>{s.value}</div>
                </div>
              ))}
            </div>
          </>
        )}

        {/* Key moments */}
        <div style={{ fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 12 }}>
          Key moments
        </div>

        {prompts.length > 0 ? (
          prompts.slice(0, 3).map((p, i) => {
            const layerColor = p.layer === "audience" ? "#0EA5E9" : p.layer === "self" ? "#F59E0B" : "#10B981";
            return (
              <div key={i} style={{ background: "var(--bg-elevated)", borderRadius: 12, padding: "14px 18px", marginBottom: 8, borderLeft: `3px solid ${layerColor}` }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: layerColor, fontFamily: MONO, marginBottom: 4, textTransform: "uppercase", letterSpacing: 0.5 }}>{p.layer}</div>
                <div style={{ fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.45 }}>
                  {p.text}
                </div>
              </div>
            );
          })
        ) : (
          <div style={{ background: "var(--bg-elevated)", borderRadius: 12, padding: "14px 18px", marginBottom: 8, borderLeft: "3px solid var(--text-tertiary)" }}>
            <div style={{ fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.45 }}>
              No coaching prompts were delivered this session.
            </div>
          </div>
        )}

        {/* Coaching debrief */}
        <div style={{ fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: 0.8, marginTop: 16, marginBottom: 12 }}>
          Coaching debrief
        </div>
        {reviewDebrief ? (
          <div style={{
            background: "var(--bg-elevated)", borderRadius: 12, padding: "16px 18px",
            borderLeft: "3px solid var(--gold)", marginBottom: 8,
          }}>
            <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
              {reviewDebrief}
            </div>
          </div>
        ) : (
          <div style={{
            background: "var(--bg-elevated)", borderRadius: 12, padding: "16px 18px",
            borderLeft: "3px solid var(--text-tertiary)", marginBottom: 8,
          }}>
            <div style={{ fontSize: 13, color: "var(--text-tertiary)", fontStyle: "italic" }}>
              Generating coaching debrief...
            </div>
          </div>
        )}

        <a
          onClick={() => { setSelectedSessionId(liveSessionId); setScreen("transcript"); }}
          style={{ display: "block", textAlign: "center", fontSize: 13, color: "var(--text-tertiary)", cursor: "pointer", padding: "16px 0", textDecoration: "none" }}
          onMouseEnter={e => { e.currentTarget.style.color = "var(--text-secondary)"; }}
          onMouseLeave={e => { e.currentTarget.style.color = "var(--text-tertiary)"; }}
        >
          View full transcript →
        </a>
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: RETRO (upload transcript for coaching)
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "retro") {
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {topBar("Upload & Analyze", () => setScreen("home"))}
        <RetroImportPane
          onBack={() => setScreen("home")}
          onViewSession={(sid) => { setSelectedSessionId(sid); setScreen("transcript"); }}
          activeJobId={activeRetroJobId}
          onJobIdChange={setActiveRetroJobId}
        />
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: TRANSCRIPT (session transcript + debrief)
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "transcript" && selectedSessionId) {
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {topBar("Session", () => setScreen("home"))}
        <TranscriptPane sessionId={selectedSessionId} />
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: POST COACH (text-based persuasion coaching)
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "post-coach") {
    const handleCoachSubmit = async () => {
      if (!postCoachText.trim() || postCoachLoading) return;
      setPostCoachLoading(true);
      setPostCoachTips([]);
      setPostCoachOverall("");
      try {
        const res = await fetch("http://localhost:8000/coach/text", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: postCoachText, context: postCoachContext }),
        });
        if (!res.ok) throw new Error("Failed");
        const data = await res.json();
        setPostCoachTips(data.tips || []);
        setPostCoachOverall(data.overall || "");
      } catch {
        setPostCoachOverall("Could not reach coaching service. Check that the backend is running.");
      } finally {
        setPostCoachLoading(false);
      }
    };

    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {topBar("Text Coach", () => { setPostCoachTips([]); setPostCoachOverall(""); setScreen("prepare"); })}

        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 6 }}>Your draft</div>
        <textarea
          value={postCoachText}
          onChange={e => setPostCoachText(e.target.value)}
          placeholder="Paste your LinkedIn post, email, or message here…"
          rows={6}
          style={{
            width: "100%", background: "var(--bg-card)", border: "1px solid var(--border-medium)",
            borderRadius: 10, padding: "11px 14px", fontFamily: BODY, fontSize: 14,
            color: "var(--text-primary)", outline: "none", transition: "border-color 200ms ease",
            marginBottom: 12, resize: "vertical", lineHeight: 1.5,
          }}
          onFocus={e => { e.currentTarget.style.borderColor = "var(--gold-border)"; }}
          onBlur={e => { e.currentTarget.style.borderColor = "var(--border-medium)"; }}
        />

        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 6 }}>Context (optional)</div>
        <input
          type="text"
          value={postCoachContext}
          onChange={e => setPostCoachContext(e.target.value)}
          placeholder="e.g. Pitching to a skeptical CFO"
          style={{
            width: "100%", background: "var(--bg-card)", border: "1px solid var(--border-medium)",
            borderRadius: 10, padding: "11px 14px", fontFamily: BODY, fontSize: 14,
            color: "var(--text-primary)", outline: "none", transition: "border-color 200ms ease",
            marginBottom: 24,
          }}
          onFocus={e => { e.currentTarget.style.borderColor = "var(--gold-border)"; }}
          onBlur={e => { e.currentTarget.style.borderColor = "var(--border-medium)"; }}
        />

        <button
          onClick={handleCoachSubmit}
          disabled={!postCoachText.trim() || postCoachLoading}
          style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            width: "100%", height: 54, background: !postCoachText.trim() || postCoachLoading ? "var(--bg-elevated)" : "var(--gold)",
            color: !postCoachText.trim() || postCoachLoading ? "var(--text-tertiary)" : "var(--bg-primary)",
            fontFamily: BODY, fontSize: 16, fontWeight: 500, border: "none",
            borderRadius: 12, cursor: !postCoachText.trim() || postCoachLoading ? "not-allowed" : "pointer",
            transition: "background 200ms ease", marginBottom: 24,
          }}
          onMouseEnter={e => { if (postCoachText.trim() && !postCoachLoading) e.currentTarget.style.background = "var(--gold-hover)"; }}
          onMouseLeave={e => { if (postCoachText.trim() && !postCoachLoading) e.currentTarget.style.background = "var(--gold)"; }}
        >
          {postCoachLoading ? "Analyzing…" : "Get coaching"}
        </button>

        {/* Results */}
        {postCoachTips.length > 0 && (
          <div style={{ animation: "promptIn 400ms ease-out" }}>
            <div style={{ fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 12 }}>
              Coaching tips
            </div>
            {postCoachTips.map((tip, i) => (
              <div key={i} style={{
                background: "var(--bg-elevated)", borderLeft: "4px solid var(--gold)",
                borderRadius: 12, padding: "14px 18px", marginBottom: 8,
              }}>
                <div style={{ fontSize: 14, fontWeight: 500, color: "var(--gold)", lineHeight: 1.5 }}>{tip}</div>
              </div>
            ))}

            {postCoachOverall && (
              <div style={{
                background: "var(--bg-card)", borderRadius: 10, padding: "14px 18px", marginTop: 12,
              }}>
                <div style={{ fontSize: 12, fontWeight: 500, color: "var(--green)", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 6 }}>
                  Assessment
                </div>
                <div style={{ fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.5 }}>{postCoachOverall}</div>
              </div>
            )}
          </div>
        )}

        {postCoachOverall && postCoachTips.length === 0 && (
          <div style={{ background: "var(--bg-card)", borderRadius: 10, padding: "14px 18px" }}>
            <div style={{ fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.5 }}>{postCoachOverall}</div>
          </div>
        )}
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: CALENDAR
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "calendar") {
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {topBar("Calendar", () => setScreen("home"))}
        <CalendarPane onBack={() => setScreen("home")} />
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: TEAM SYNC
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "team-sync") {
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {topBar("Import / Export", () => setScreen("home"))}
        <TeamSyncPane onBack={() => setScreen("home")} />
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: ALL SESSIONS (search + browse by date)
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "all-sessions") {
    // Group sessions by date label
    const groups: { label: string; sessions: RecentSession[] }[] = [];
    const labelMap = new Map<string, RecentSession[]>();
    for (const s of allSessions) {
      const d = new Date(s.started_at);
      const now = new Date();
      const isToday = d.toDateString() === now.toDateString();
      const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
      const isYesterday = d.toDateString() === yesterday.toDateString();
      const label = isToday ? "Today" : isYesterday ? "Yesterday" : d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: d.getFullYear() !== now.getFullYear() ? "numeric" : undefined });
      if (!labelMap.has(label)) { labelMap.set(label, []); groups.push({ label, sessions: labelMap.get(label)! }); }
      labelMap.get(label)!.push(s);
    }
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {topBar("All Sessions", () => setScreen("home"))}
        <input
          type="text"
          value={allSessionsQuery}
          onChange={e => setAllSessionsQuery(e.target.value)}
          placeholder="Search sessions…"
          autoFocus
          style={{
            width: "100%", background: "var(--bg-card)", border: "1px solid var(--border-medium)",
            borderRadius: 10, padding: "10px 14px", fontFamily: BODY, fontSize: 14,
            color: "var(--text-primary)", outline: "none", transition: "border-color 200ms ease",
            marginBottom: 20, boxSizing: "border-box",
          }}
          onFocus={e => { e.currentTarget.style.borderColor = "var(--gold-border)"; }}
          onBlur={e => { e.currentTarget.style.borderColor = "var(--border-medium)"; }}
        />
        {allSessions.length === 0 ? (
          <div style={{ fontSize: 13, color: "var(--text-tertiary)", textAlign: "center", padding: "40px 0" }}>
            {allSessionsQuery ? "No matching sessions" : "No sessions yet"}
          </div>
        ) : groups.map(g => (
          <div key={g.label} style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 11, fontWeight: 500, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 8 }}>
              {g.label}
            </div>
            {g.sessions.map(s => (
              <div
                key={s.session_id}
                onClick={() => { setSelectedSessionId(s.session_id); setScreen("transcript"); }}
                style={{
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  padding: "10px 14px", background: "var(--bg-card)", borderRadius: 10,
                  cursor: "pointer", transition: "background 200ms ease", marginBottom: 6,
                }}
                onMouseEnter={e => { e.currentTarget.style.background = "var(--bg-elevated)"; }}
                onMouseLeave={e => { e.currentTarget.style.background = "var(--bg-card)"; }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, color: "var(--text-primary)", fontWeight: 400, lineHeight: 1.2 }}>{s.title || s.context}</div>
                  <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 2 }}>
                    {s.context === "retro" ? "Upload & Analyze" : s.context}{s.persuasion_score != null ? ` · Score: ${s.persuasion_score}` : ""}
                  </div>
                </div>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)", whiteSpace: "nowrap", flexShrink: 0 }}>
                  {new Date(s.started_at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>,
    );
  }

  // ═════════════════════════════════════════════════════════════════════════
  // SCREEN: PROFILES
  // ═════════════════════════════════════════════════════════════════════════
  if (screen === "profiles") {
    return shell(
      <div style={{ animation: "fadeIn 200ms ease-out" }}>
        {topBar("Profiles", () => setScreen("home"))}
        <ProfilesPane onBack={() => setScreen("home")} />
      </div>,
    );
  }

  // Fallback
  return shell(
    <div>
      <ConnectionStatus connectionState={connectionState} sessionPhase={sessionPhase} errorMessage={errorMessage} onRetry={() => void startSession()} />
    </div>,
  );
}
