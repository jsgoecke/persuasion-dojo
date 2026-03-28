/**
 * useSparringSocket
 *
 * Manages one AI sparring session:
 *   1. POST /sparring/sessions  → create in-memory session on backend
 *   2. WebSocket /ws/sparring/{id}
 *   3. Send user_turn → receive streamed opponent + coaching turns
 *   4. Send end / receive sparring_ended
 */

import { useState, useRef, useCallback, useEffect } from "react";

const API_BASE = "http://localhost:8000";
const WS_BASE  = "ws://localhost:8000";

const PING_INTERVAL_MS = 30_000;

// ── Types ──────────────────────────────────────────────────────────────────

export type SparringRole = "user" | "opponent" | "coaching";
export type SparringArchetype = "Architect" | "Firestarter" | "Inquisitor" | "Bridge Builder";
export type SparringPhase = "idle" | "setup" | "active" | "ended";

export interface SparringTurn {
  role: SparringRole;
  /** For opponent turns that are still streaming, each chunk arrives separately;
   *  is_final=true marks the complete assembled text. */
  text: string;
  turn_number: number;
  is_final: boolean;
  coaching_tip: string;
  /** Client-assigned key for React lists. */
  id: string;
}

export interface SparringState {
  phase: SparringPhase;
  sessionId: string | null;
  /** All completed turns (is_final=true). Streaming chunks are in streamingChunk. */
  turns: SparringTurn[];
  /** Current streaming opponent text (not yet final). */
  streamingChunk: string;
  totalTurns: number;
  error: string | null;
}

export interface SparringActions {
  start: (
    userArchetype: SparringArchetype,
    opponentArchetype: SparringArchetype,
    scenario: string,
    maxTurns?: number,
  ) => Promise<void>;
  sendTurn: (text: string) => void;
  end: () => void;
  reset: () => void;
}

// ── Hook ──────────────────────────────────────────────────────────────────

export function useSparringSocket(): SparringState & SparringActions {
  const [phase, setPhase]         = useState<SparringPhase>("idle");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns]         = useState<SparringTurn[]>([]);
  const [streamingChunk, setStreamingChunk] = useState("");
  const [totalTurns, setTotalTurns]         = useState(0);
  const [error, setError]         = useState<string | null>(null);

  const wsRef       = useRef<WebSocket | null>(null);
  const pingRef     = useRef<ReturnType<typeof setInterval> | null>(null);
  const phaseRef    = useRef<SparringPhase>("idle");

  const updatePhase = useCallback((p: SparringPhase) => {
    phaseRef.current = p;
    setPhase(p);
  }, []);

  const stopPing = useCallback(() => {
    if (pingRef.current !== null) {
      clearInterval(pingRef.current);
      pingRef.current = null;
    }
  }, []);

  const start = useCallback(async (
    userArchetype: SparringArchetype,
    opponentArchetype: SparringArchetype,
    scenario: string,
    maxTurns = 10,
  ) => {
    if (phaseRef.current !== "idle") return;

    updatePhase("setup");
    setTurns([]);
    setStreamingChunk("");
    setTotalTurns(0);
    setError(null);

    let id: string;
    try {
      const resp = await fetch(`${API_BASE}/sparring/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_archetype: userArchetype,
          opponent_archetype: opponentArchetype,
          scenario,
          max_turns: maxTurns,
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json() as { session_id: string };
      id = data.session_id;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create sparring session");
      updatePhase("idle");
      return;
    }

    setSessionId(id);

    const ws = new WebSocket(`${WS_BASE}/ws/sparring/${id}`);
    wsRef.current = ws;

    ws.onopen = () => {
      updatePhase("active");
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

      if (msg.type === "sparring_turn") {
        const turn = msg as unknown as SparringTurn;

        if (turn.role === "opponent" && !turn.is_final) {
          // Accumulate streaming chunk
          setStreamingChunk(prev => prev + turn.text);
          return;
        }

        if (turn.role === "opponent" && turn.is_final) {
          // Finalise — clear streaming buffer, add complete turn
          setStreamingChunk("");
        }

        const completeTurn: SparringTurn = {
          role: turn.role,
          text: turn.text,
          turn_number: turn.turn_number,
          is_final: true,
          coaching_tip: turn.coaching_tip ?? "",
          id: `${turn.role}-${turn.turn_number}-${Date.now()}`,
        };
        setTurns(prev => [...prev, completeTurn]);

      } else if (msg.type === "sparring_ended") {
        setTotalTurns((msg.turns as number) ?? 0);
        updatePhase("ended");
        stopPing();
        wsRef.current = null;
      } else if (msg.type === "error") {
        setError((msg.message as string) ?? "Session error");
        updatePhase("idle");
        stopPing();
      }
    };

    ws.onclose = () => {
      stopPing();
      if (phaseRef.current !== "ended") {
        setError("Connection closed unexpectedly");
        updatePhase("idle");
      }
    };

    ws.onerror = () => setError("WebSocket error");
  }, [updatePhase, stopPing]);

  const sendTurn = useCallback((text: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "user_turn", text }));
    }
  }, []);

  const end = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "end" }));
    }
  }, []);

  const reset = useCallback(() => {
    stopPing();
    wsRef.current?.close();
    wsRef.current = null;
    setSessionId(null);
    setTurns([]);
    setStreamingChunk("");
    setTotalTurns(0);
    setError(null);
    updatePhase("idle");
  }, [stopPing, updatePhase]);

  useEffect(() => {
    return () => {
      stopPing();
      wsRef.current?.close();
    };
  }, [stopPing]);

  return {
    phase, sessionId, turns, streamingChunk, totalTurns, error,
    start, sendTurn, end, reset,
  };
}
