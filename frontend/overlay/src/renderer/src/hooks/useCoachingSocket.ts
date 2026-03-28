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

    setConnectionState("connecting");
    updatePhase("active");
    setPrompts([]);
    setTranscripts([]);
    setSessionResult(null);
    setErrorMessage(null);

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
          received_at: Date.now(),
        };
        setPrompts(prev => [prompt, ...prev].slice(0, MAX_HISTORY));
      } else if (msg.type === "swift_restart_needed") {
        // Python silence watchdog fired — Swift binary stopped writing to the
        // FIFO. Ask the main process to restart the capture binary.
        window.api.restartCapture();
      } else if (msg.type === "session_ended") {
        setSessionResult(msg as unknown as SessionEndData);
        updatePhase("ended");
        setConnectionState("idle");
        stopPing();
        wsRef.current = null;
      }
    };

    ws.onclose = () => {
      stopPing();
      if (phaseRef.current !== "ended") {
        // Reset phase so startSession() guard lets the user retry.
        updatePhase("idle");
        setConnectionState("error");
        // errorMessage may already be set via {"type":"error"} — don't overwrite it.
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
      stopPing();
      wsRef.current?.close();
      wsRef.current = null;
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
    updatePhase("idle");
  }, [updatePhase]);

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
    startSession,
    endSession,
    dismissPrompt,
    clearError,
    resetSession,
  };
}
