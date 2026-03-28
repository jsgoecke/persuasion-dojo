/**
 * Shared TypeScript types for the coaching overlay.
 *
 * These mirror the WebSocket message protocol defined in backend/main.py.
 */

/** The three coaching layers (plus a cached fallback state). */
export type Layer = "audience" | "self" | "group";

/**
 * A coaching prompt received from the backend WebSocket.
 * Mirrors the server-side ``coaching_prompt`` message type.
 */
export interface CoachingPrompt {
  layer: Layer;
  text: string;
  is_fallback: boolean;
  /** e.g. "elm:ego_threat" or "cadence:60s" */
  triggered_by: string;
  speaker_id: string;
  /** Client-side timestamp (Date.now()) assigned when the message arrives. */
  received_at: number;
}

/**
 * Payload of the ``session_ended`` WebSocket message.
 * The backend computes these scores at session end.
 */
export interface SessionEndData {
  session_id: string;
  /** 0–100 composite Persuasion Score */
  persuasion_score: number;
  /** Signed delta vs. rolling average of prior sessions (null if <2 prior). */
  growth_delta: number | null;
  breakdown: {
    /** 0–100 Timing sub-score (30% weight) */
    timing: number;
    /** 0–100 Ego Safety sub-score (30% weight) */
    ego_safety: number;
    /** 0–100 Convergence sub-score (40% weight) */
    convergence: number;
  };
}

/** WebSocket connection lifecycle state. */
export type ConnectionState =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "error";

/** High-level session lifecycle state. */
export type SessionPhase =
  | "idle"      // No session created yet
  | "active"    // WebSocket connected, receiving prompts
  | "ending"    // session_end sent, waiting for session_ended
  | "ended";    // session_ended received — show score
