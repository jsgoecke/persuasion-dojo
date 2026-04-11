/**
 * useCoachingSocket
 *
 * Manages the full session lifecycle:
 *   1. POST /sessions       — create a MeetingSession row in the backend DB
 *   2. WebSocket connect    — ws://localhost:8000/ws/session/{id}
 *   3. Receive coaching_prompt messages → prepend to prompt history
 *   4. Send session_end     — backend computes scores, sends session_ended
 *   5. Receive session_ended → expose SessionEndData to the UI
 *
 * Ping/pong keepalive is sent every PING_INTERVAL_MS while connected.
 */

import { useState, useRef, useCallback, useEffect } from "react";
import type {
  CoachingPrompt,
  ConnectionState,
  DetectedProfile,
  SessionEndData,
  SessionPhase,
} from "../types";

const API_BASE = "http://localhost:8000";
const WS_BASE  = "ws://localhost:8000";

/** Number of prompts to retain in history (current + 4 prior). */
const MAX_HISTORY = 5;

/** Interval between WebSocket pings. Must be less than the server's idle timeout. */
const PING_INTERVAL_MS = 30_000;

export interface TranscriptEntry {
  speaker_id: string;
  text: string;
  is_final: boolean;
  timestamp: number;
}

export interface CoachingSocketState {
  sessionId: string | null;
  connectionState: ConnectionState;
  sessionPhase: SessionPhase;
  /** All received prompts, newest first. Max MAX_HISTORY entries. */
  prompts: CoachingPrompt[];
  /** Shorthand for prompts[0] (the prompt currently shown to the user). */
  currentPrompt: CoachingPrompt | null;
  sessionResult: SessionEndData | null;
  /** Human-readable error detail, set when connectionState === "error". */
  errorMessage: string | null;
  /** Normalised audio input level 0.0–1.0, updated ~4×/sec. */
  audioLevel: number;
  /** Live transcript entries, newest last. */
  transcripts: TranscriptEntry[];
  /** Resolved speaker names: counterpart_0 → "Sarah Chen". */
  speakerNames: Record<string, string>;
  /** Profiles detected during the session. */
  detectedProfiles: DetectedProfile[];
  /** Active transcription backend: "cloud" (Deepgram), "local" (Moonshine), or null if unknown. */
  transcriptionBackend: "cloud" | "local" | null;
}

export interface SessionParticipant {
  name: string;
  archetype: string; // "Architect" | "Firestarter" | "Inquisitor" | "Bridge Builder"
}

export interface CoachingSocketActions {
  startSession: (opts?: {
    userArchetype?: string;
    participants?: SessionParticipant[];
    meetingTitle?: string;
  }) => Promise<void>;
  endSession: () => void;
  /** Remove the current prompt (dismiss hotkey: ⌘ Shift D). */
  dismissPrompt: () => void;
  /** Clear error state and return to idle menu. */
  clearError: () => void;
  /** Reset session state and return to idle menu (used from session-end card). */
  resetSession: () => void;
  /** Confirm or edit a detected profile name. */
  confirmProfile: (speakerId: string, name: string) => void;
}

export function useCoachingSocket(): CoachingSocketState & CoachingSocketActions {
  const [sessionId, setSessionId]           = useState<string | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>("idle");
  const [sessionPhase, setSessionPhase]     = useState<SessionPhase>("idle");
  const [prompts, setPrompts]               = useState<CoachingPrompt[]>([]);
  const [sessionResult, setSessionResult]   = useState<SessionEndData | null>(null);
  const [errorMessage, setErrorMessage]     = useState<string | null>(null);
  const [audioLevel, setAudioLevel]         = useState<number>(0);
  const [transcripts, setTranscripts]       = useState<TranscriptEntry[]>([]);
  const [speakerNames, setSpeakerNames]     = useState<Record<string, string>>({});
  const [detectedProfiles, setDetectedProfiles] = useState<DetectedProfile[]>([]);
  const [transcriptionBackend, setTranscriptionBackend] = useState<"cloud" | "local" | null>(null);

  const wsRef       = useRef<WebSocket | null>(null);
  const pingRef     = useRef<ReturnType<typeof setInterval> | null>(null);
  // Track phase in a ref so the WebSocket onclose handler sees current value.
  const phaseRef    = useRef<SessionPhase>("idle");

  const updatePhase = useCallback((p: SessionPhase) => {
    phaseRef.current = p;
    setSessionPhase(p);
  }, []);

  const stopPing = useCallback(() => {
    if (pingRef.current !== null) {
      clearInterval(pingRef.current);
      pingRef.current = null;
    }
  }, []);

  const startSession = useCallback(async (opts?: {
    userArchetype?: string;
    participants?: SessionParticipant[];
    meetingTitle?: string;
  }) => {
    if (phaseRef.current !== "idle") return;

    // Ensure the Swift audio capture binary is running before we connect.
    // It may have been stopped by the previous session's stop_capture signal.
    window.api.startCapture();

    setConnectionState("connecting");
    updatePhase("active");
    setPrompts([]);
    setTranscripts([]);
    setSessionResult(null);
    setErrorMessage(null);
    setTranscriptionBackend(null);

    let id: string;
    try {
      const resp = await fetch(`${API_BASE}/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          context: "meeting",
          title: opts?.meetingTitle || null,
          user_archetype: opts?.userArchetype || null,
          participants: opts?.participants?.map(p => ({
            name: p.name,
            archetype: p.archetype,
          })) || [],
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      id = data.session_id as string;
    } catch {
      setErrorMessage("Could not reach the coaching server. Start the backend with: uvicorn backend.main:app --reload");
      setConnectionState("error");
      updatePhase("idle");
      return;
    }

    setSessionId(id);

    const ws = new WebSocket(`${WS_BASE}/ws/session/${id}`);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnectionState("connected");
      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping" }));
        }
      }, PING_INTERVAL_MS);
    };

    ws.onmessage = (evt: MessageEvent) => {
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(evt.data as string) as Record<string, unknown>;
      } catch {
        return;
      }

      if (msg.type === "error") {
        setErrorMessage((msg.message as string) ?? "Session error");
        setConnectionState("error");
        stopPing();
        return;
      }

      if (msg.type === "no_audio") {
        // Audio pipeline issue — session is still connected, just no audio arriving.
        // Surface as an error message but keep the connection alive.
        setErrorMessage((msg.message as string) ?? "No audio detected. Check Screen Recording permission.");
        return;
      }

      if (msg.type === "audio_level") {
        setAudioLevel(msg.level as number);
        return;
      }

      if (msg.type === "transcriber_status") {
        const event = msg.event as string;
        if (event === "using_cloud") {
          setTranscriptionBackend("cloud");
        } else if (event === "using_local" || event === "fallback_activated") {
          setTranscriptionBackend("local");
        }
        return;
      }

      if (msg.type === "utterance") {
        const entry: TranscriptEntry = {
          speaker_id: (msg.speaker_id as string) ?? "speaker_0",
          text: (msg.text as string) ?? "",
          is_final: Boolean(msg.is_final),
          timestamp: Date.now(),
        };
        setTranscripts(prev => {
          // For interim results, replace the last entry if same speaker and not final
          if (!entry.is_final && prev.length > 0) {
            const last = prev[prev.length - 1];
            if (!last.is_final && last.speaker_id === entry.speaker_id) {
              return [...prev.slice(0, -1), entry];
            }
          }
          // Keep last 50 entries
          return [...prev, entry].slice(-50);
        });
        return;
      }

      if (msg.type === "coaching_prompt") {
        const prompt: CoachingPrompt = {
          layer: msg.layer as CoachingPrompt["layer"],
          text: msg.text as string,
          is_fallback: Boolean(msg.is_fallback),
          triggered_by: (msg.triggered_by as string) ?? "",
          speaker_id: (msg.speaker_id as string) ?? "",
          prompt_id: (msg.prompt_id as string) ?? "",
          bullet_id: (msg.bullet_id as string) ?? "",
          received_at: Date.now(),
        };
        setPrompts(prev => [prompt, ...prev].slice(0, MAX_HISTORY));
      } else if (msg.type === "speaker_identified") {
        const sid = msg.speaker_id as string;
        const name = msg.name as string;
        const conf = typeof msg.confidence === "number" ? msg.confidence : undefined;
        if (sid && name) {
          setSpeakerNames(prev => ({ ...prev, [sid]: name }));
          // Update confidence on detected profile so the UI can show/hide the "?" badge
          if (conf !== undefined) {
            setDetectedProfiles(prev =>
              prev.map(p => p.speaker_id === sid ? { ...p, confidence: conf } : p)
            );
          }
        }
      } else if (msg.type === "profile_detected") {
        const profile: DetectedProfile = {
          speaker_id: (msg.speaker_id as string) ?? "",
          suggested_name: (msg.suggested_name as string) ?? "",
          archetype: (msg.archetype as string) ?? "",
          confidence: (msg.confidence as number) ?? 0,
          is_existing: Boolean(msg.is_existing),
          confirmed: false,
          participant_id: msg.participant_id as string | undefined,
        };
        setDetectedProfiles(prev => {
          // Don't add duplicates
          if (prev.some(p => p.speaker_id === profile.speaker_id)) {
            return prev.map(p => p.speaker_id === profile.speaker_id ? { ...p, ...profile, confirmed: p.confirmed } : p);
          }
          return [...prev, profile];
        });
      } else if (msg.type === "swift_restart_needed") {
        // Python silence watchdog fired — Swift binary stopped writing to the
        // FIFO. Ask the main process to restart the capture binary.
        window.api.restartCapture();
      } else if (msg.type === "session_ended") {
        // Stop AudioCapture immediately on session_ended — this is more reliable
        // than a separate stop_capture message which races with ws.close().
        window.api.stopCapture();
        setSessionResult(msg as unknown as SessionEndData);
        updatePhase("ended");
        setConnectionState("idle");
        stopPing();
        wsRef.current = null;
      }
    };

    ws.onclose = () => {
      stopPing();
      if (phaseRef.current === "ending") {
        // session_end was sent but session_ended never arrived (backend crashed
        // during scoring). Show review with a fallback result rather than dumping
        // the user back to the home screen with no feedback.
        window.api.stopCapture();
        setSessionResult(prev => prev ?? {
          session_id: id,
          persuasion_score: null,
          growth_delta: null,
          breakdown: { timing: 0, ego_safety: 0, convergence: 0 },
        } as unknown as SessionEndData);
        updatePhase("ended");
        setConnectionState("idle");
      } else if (phaseRef.current !== "ended") {
        // Unexpected close (crash, network drop) during active session.
        window.api.stopCapture();
        updatePhase("idle");
        setConnectionState("error");
      }
    };

    ws.onerror = () => {
      updatePhase("idle");
      setConnectionState("error");
      setErrorMessage(prev => prev ?? "WebSocket connection failed.");
    };
  }, [updatePhase, stopPing]);

  const endSession = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "session_end" }));
      updatePhase("ending");
    } else {
      // WebSocket not open — force end locally so the UI isn't stuck.
      // Provide a fallback result so the review screen renders instead of going home.
      stopPing();
      wsRef.current?.close();
      wsRef.current = null;
      setSessionResult(prev => prev ?? {
        session_id: "",
        persuasion_score: null,
        growth_delta: null,
        breakdown: { timing: 0, ego_safety: 0, convergence: 0 },
      } as unknown as SessionEndData);
      updatePhase("ended");
      setConnectionState("idle");
    }
  }, [updatePhase, stopPing]);

  const dismissPrompt = useCallback(() => {
    setPrompts(prev => prev.slice(1));
  }, []);

  const clearError = useCallback(() => {
    setConnectionState("idle");
    setErrorMessage(null);
    updatePhase("idle");
  }, [updatePhase]);

  const resetSession = useCallback(() => {
    setConnectionState("idle");
    setSessionResult(null);
    setPrompts([]);
    setTranscripts([]);
    setErrorMessage(null);
    setAudioLevel(0);
    setTranscriptionBackend(null);
    setSpeakerNames({});
    setDetectedProfiles([]);
    updatePhase("idle");
  }, [updatePhase]);

  const confirmProfile = useCallback((speakerId: string, name: string) => {
    // Send confirmation to backend
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "confirm_profile", speaker_id: speakerId, name }));
    }
    // Optimistically update local state
    setSpeakerNames(prev => ({ ...prev, [speakerId]: name }));
    setDetectedProfiles(prev =>
      prev.map(p => p.speaker_id === speakerId ? { ...p, suggested_name: name, confirmed: true } : p)
    );
  }, []);

  const sendFeedback = useCallback((promptId: string, helpful: boolean) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "prompt_feedback", prompt_id: promptId, helpful }));
    }
    // Optimistically update the prompt's feedback state
    setPrompts(prev =>
      prev.map(p =>
        p.prompt_id === promptId
          ? { ...p, user_feedback: helpful ? "helpful" : "harmful" }
          : p
      )
    );
  }, []);

  // Clean up on unmount.
  useEffect(() => {
    return () => {
      stopPing();
      wsRef.current?.close();
    };
  }, [stopPing]);

  return {
    sessionId,
    connectionState,
    sessionPhase,
    prompts,
    currentPrompt: prompts[0] ?? null,
    sessionResult,
    errorMessage,
    audioLevel,
    transcripts,
    speakerNames,
    detectedProfiles,
    transcriptionBackend,
    startSession,
    endSession,
    dismissPrompt,
    clearError,
    resetSession,
    confirmProfile,
    sendFeedback,
  };
}
