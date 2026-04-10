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
  /** Server-assigned prompt ID for feedback tracking. */
  prompt_id: string;
  /** Primary coaching bullet ID used to generate this prompt. */
  bullet_id: string;
  /** Client-side timestamp (Date.now()) assigned when the message arrives. */
  received_at: number;
  /** User feedback: "helpful" | "harmful" | undefined */
  user_feedback?: "helpful" | "harmful";
}

/**
 * Payload of the ``session_ended`` WebSocket message.
 * The backend computes these scores at session end.
 */
/** Per-participant profile included in session_ended data. */
export interface SessionParticipantProfile {
  speaker_id: string;
  name: string;
  archetype: string;
  confidence: number;
}

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
  /** Per-participant profiles detected during the session. */
  participants?: SessionParticipantProfile[];
  /** User's auto-detected archetype for the session. */
  user_archetype?: string | null;
}

/** WebSocket connection lifecycle state. */
export type ConnectionState =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "error";

/** A speaker profile detected during a live or retro session. */
export interface DetectedProfile {
  speaker_id: string;
  suggested_name: string;
  archetype: string;
  confidence: number;
  is_existing: boolean;
  confirmed: boolean;
  participant_id?: string;
}

/** High-level session lifecycle state. */
export type SessionPhase =
  | "idle"      // No session created yet
  | "active"    // WebSocket connected, receiving prompts
  | "ending"    // session_end sent, waiting for session_ended
  | "ended";    // session_ended received — show score
