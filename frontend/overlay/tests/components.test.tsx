/**
 * Component tests for the Persuasion Dojo overlay UI.
 *
 * Runs under Vitest + jsdom. The Electron IPC bridge (window.api) and
 * the useCoachingSocket hook are mocked so tests never touch real
 * WebSockets or Electron preload code.
 *
 * vi.mock() calls are hoisted by Vitest before static imports, so the
 * modules imported below receive the mocked versions automatically.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";

// ── Mock useCoachingSocket (hoisted before imports) ────────────────────────

vi.mock("../src/renderer/src/hooks/useCoachingSocket", () => ({
  useCoachingSocket: () => ({
    ...mockSocketState,
    startSession: mockStartSession,
    endSession: mockEndSession,
    dismissPrompt: mockDismissPrompt,
    clearError: mockClearError,
    resetSession: mockResetSession,
  }),
}));

// Mock state object — mutate properties in tests to change socket behaviour.
const mockSocketState = {
  sessionId: null as string | null,
  connectionState: "idle" as string,
  sessionPhase: "idle" as string,
  prompts: [] as unknown[],
  currentPrompt: null as unknown | null,
  sessionResult: null as unknown | null,
  errorMessage: null as string | null,
};

const mockStartSession = vi.fn();
const mockEndSession = vi.fn();
const mockDismissPrompt = vi.fn();
const mockClearError = vi.fn();
const mockResetSession = vi.fn();

// ── Static component imports (receive mocked hook) ─────────────────────────

import { LayerBadge } from "../src/renderer/src/components/LayerBadge";
import { PromptCard } from "../src/renderer/src/components/PromptCard";
import { HistoryTray } from "../src/renderer/src/components/HistoryTray";
import { ConnectionStatus } from "../src/renderer/src/components/ConnectionStatus";
import { SessionEndCard } from "../src/renderer/src/components/SessionEndCard";
import { Overlay } from "../src/renderer/src/Overlay";

// ── Shared fixtures ────────────────────────────────────────────────────────

const AUDIENCE_PROMPT = {
  layer: "audience" as const,
  text: "Sarah is an Inquisitor — anchor your next point in a number.",
  is_fallback: false,
  triggered_by: "elm:ego_threat",
  speaker_id: "spk_1",
  received_at: 1_700_000_000_000,
};

const CACHED_PROMPT = {
  layer: "self" as const,
  text: "You have been in advocate mode for 4 minutes — ask a question.",
  is_fallback: true,
  triggered_by: "cadence:60s",
  speaker_id: "",
  received_at: 1_700_000_001_000,
};

const SESSION_RESULT = {
  session_id: "sess_abc",
  persuasion_score: 72,
  growth_delta: 5,
  breakdown: {
    timing: 80,
    ego_safety: 65,
    convergence: 70,
  },
};

// ── Mock window.api IPC bridge ─────────────────────────────────────────────

const mockOnHotkey = vi.fn(() => vi.fn());
const mockMinimize = vi.fn();

beforeEach(() => {
  (window as unknown as Record<string, unknown>).api = {
    getVersion: () => "1.0.0",
    onHotkey: mockOnHotkey,
    minimize: mockMinimize,
  };
  // Reset socket state to idle before each test.
  Object.assign(mockSocketState, {
    sessionId: null,
    connectionState: "idle",
    sessionPhase: "idle",
    prompts: [],
    currentPrompt: null,
    sessionResult: null,
    errorMessage: null,
  });
});

afterEach(() => {
  vi.clearAllMocks();
  delete (window as unknown as Record<string, unknown>).api;
});

// ── LayerBadge ─────────────────────────────────────────────────────────────

describe("LayerBadge", () => {
  it("renders the active badge with correct aria-label", () => {
    render(<LayerBadge layer="audience" active={true} />);
    expect(
      screen.getByRole("button", { name: /active layer: audience/i }),
    ).toBeInTheDocument();
  });

  it("renders inactive badge with switch aria-label", () => {
    render(<LayerBadge layer="self" active={false} />);
    expect(
      screen.getByRole("button", { name: /switch to self layer/i }),
    ).toBeInTheDocument();
  });

  it("shows → separator when showSeparator is true", () => {
    render(<LayerBadge layer="group" active={false} showSeparator={true} />);
    expect(screen.getByText("→")).toBeInTheDocument();
  });

  it("does not show separator by default", () => {
    render(<LayerBadge layer="group" active={false} />);
    expect(screen.queryByText("→")).not.toBeInTheDocument();
  });

  it("calls onClick when clicked", () => {
    const onClick = vi.fn();
    render(<LayerBadge layer="audience" active={false} onClick={onClick} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledOnce();
  });
});

// ── PromptCard ─────────────────────────────────────────────────────────────

describe("PromptCard", () => {
  const defaultProps = {
    prompt: AUDIENCE_PROMPT,
    activeLayer: "audience" as const,
    onCycleLayer: vi.fn(),
    onDismiss: vi.fn(),
    historyOpen: false,
    onToggleHistory: vi.fn(),
  };

  it("renders prompt text", () => {
    render(<PromptCard {...defaultProps} />);
    expect(screen.getByText(AUDIENCE_PROMPT.text)).toBeInTheDocument();
  });

  it("does not show cached badge for non-fallback prompts", () => {
    render(<PromptCard {...defaultProps} />);
    expect(screen.queryByLabelText("Cached fallback prompt")).not.toBeInTheDocument();
  });

  it("shows cached badge for fallback prompts", () => {
    render(<PromptCard {...defaultProps} prompt={CACHED_PROMPT} />);
    expect(screen.getByLabelText("Cached fallback prompt")).toBeInTheDocument();
  });

  it("renders the prompt region with accessible label", () => {
    render(<PromptCard {...defaultProps} />);
    expect(
      screen.getByRole("region", { name: /coaching prompt/i }),
    ).toBeInTheDocument();
  });
});

// ── HistoryTray ────────────────────────────────────────────────────────────

describe("HistoryTray", () => {
  it("renders nothing when open is false", () => {
    const { container } = render(
      <HistoryTray prompts={[AUDIENCE_PROMPT]} open={false} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when prompts is empty", () => {
    const { container } = render(<HistoryTray prompts={[]} open={true} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the list with accessible label when open", () => {
    render(<HistoryTray prompts={[AUDIENCE_PROMPT]} open={true} />);
    expect(
      screen.getByRole("list", { name: /prompt history/i }),
    ).toBeInTheDocument();
  });

  it("shows at most 4 prior prompts", () => {
    const fivePrompts = Array.from({ length: 5 }, (_, i) => ({
      ...AUDIENCE_PROMPT,
      received_at: 1_700_000_000_000 + i * 1000,
      text: `Prompt ${i + 1}`,
    }));
    render(<HistoryTray prompts={fivePrompts} open={true} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
  });

  it("renders prompt text inside tray items", () => {
    render(<HistoryTray prompts={[AUDIENCE_PROMPT]} open={true} />);
    expect(screen.getByText(AUDIENCE_PROMPT.text)).toBeInTheDocument();
  });
});

// ── ConnectionStatus ───────────────────────────────────────────────────────

describe("ConnectionStatus", () => {
  it("renders nothing when connected and active", () => {
    const { container } = render(
      <ConnectionStatus connectionState="connected" sessionPhase="active" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when idle", () => {
    const { container } = render(
      <ConnectionStatus connectionState="idle" sessionPhase="idle" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders connecting state", () => {
    render(<ConnectionStatus connectionState="connecting" sessionPhase="active" />);
    expect(screen.getByRole("status")).toHaveTextContent("Connecting…");
  });

  it("renders reconnecting alert", () => {
    render(
      <ConnectionStatus connectionState="reconnecting" sessionPhase="active" />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Reconnecting…");
  });

  it("renders error alert with retry button", () => {
    const onRetry = vi.fn();
    render(
      <ConnectionStatus
        connectionState="error"
        sessionPhase="active"
        onRetry={onRetry}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Connection lost");
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("renders ending state when sessionPhase is ending", () => {
    render(
      <ConnectionStatus connectionState="connected" sessionPhase="ending" />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Ending session…");
  });
});

// ── SessionEndCard ─────────────────────────────────────────────────────────

describe("SessionEndCard", () => {
  it("renders all three sub-score labels", () => {
    render(<SessionEndCard result={SESSION_RESULT} onDismiss={vi.fn()} />);
    expect(screen.getByText("Timing")).toBeInTheDocument();
    expect(screen.getByText("Ego Safety")).toBeInTheDocument();
    expect(screen.getByText("Convergence")).toBeInTheDocument();
  });

  it("renders the session results region", () => {
    render(<SessionEndCard result={SESSION_RESULT} onDismiss={vi.fn()} />);
    expect(
      screen.getByRole("region", { name: /session results/i }),
    ).toBeInTheDocument();
  });
});

// ── Overlay ────────────────────────────────────────────────────────────────

// Mock fetch for recent sessions
const mockFetch = vi.fn();

describe("Overlay", () => {
  beforeEach(() => {
    // Default: return empty sessions list for home screen fetch
    mockFetch.mockResolvedValue({ ok: true, json: () => Promise.resolve([]) });
    vi.stubGlobal("fetch", mockFetch);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows 'Go live' button on home screen", () => {
    render(<Overlay />);
    expect(screen.getByText("Go live")).toBeInTheDocument();
  });

  it("shows 'Prepare' button on home screen", () => {
    render(<Overlay />);
    expect(screen.getByText("Prepare")).toBeInTheDocument();
  });

  it("shows 'Self assessment', 'Profiles', and 'Retro' ghost buttons", () => {
    render(<Overlay />);
    expect(screen.getByText("Self assessment")).toBeInTheDocument();
    expect(screen.getByText("Profiles")).toBeInTheDocument();
    expect(screen.getByText("Retro")).toBeInTheDocument();
  });

  it("shows Persuasion Dojo title on home screen", () => {
    render(<Overlay />);
    expect(screen.getByText(/Persuasion/)).toBeInTheDocument();
    expect(screen.getByText(/Dojo/)).toBeInTheDocument();
  });

  it("shows Settings link on home screen", () => {
    render(<Overlay />);
    expect(screen.getByText("Settings")).toBeInTheDocument();
  });

  it("shows 'No sessions yet' when backend returns empty", async () => {
    render(<Overlay />);
    expect(await screen.findByText("No sessions yet")).toBeInTheDocument();
  });

  it("shows recent sessions from backend", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve([
        { session_id: "s1", title: "Q3 Board prep", context: "board", persuasion_score: 72, started_at: new Date().toISOString() },
        { session_id: "s2", title: "Spar vs Inquisitor", context: "practice", persuasion_score: null, started_at: new Date().toISOString() },
      ]),
    });
    render(<Overlay />);
    expect(await screen.findByText("Q3 Board prep")).toBeInTheDocument();
    expect(screen.getByText("Spar vs Inquisitor")).toBeInTheDocument();
  });

  it("navigates to setup when 'Go live' is clicked", () => {
    render(<Overlay />);
    fireEvent.click(screen.getByText("Go live"));
    expect(screen.getByText("Meeting name")).toBeInTheDocument();
    expect(screen.getByText("Begin coaching")).toBeInTheDocument();
  });

  it("navigates to preparation hub when 'Prepare' is clicked", () => {
    render(<Overlay />);
    fireEvent.click(screen.getByText("Prepare"));
    expect(screen.getByText("Spar with an archetype")).toBeInTheDocument();
    expect(screen.getByText("Rehearse with a contact")).toBeInTheDocument();
  });

  it("shows Post coach card in preparation hub", () => {
    render(<Overlay />);
    fireEvent.click(screen.getByText("Prepare"));
    expect(screen.getByText("Post coach")).toBeInTheDocument();
    expect(screen.getByText(/Paste a draft LinkedIn post/)).toBeInTheDocument();
  });

  it("navigates to post coach screen from preparation hub", () => {
    render(<Overlay />);
    fireEvent.click(screen.getByText("Prepare"));
    fireEvent.click(screen.getByText("Post coach").closest("div[style]")!);
    expect(screen.getByText("Your draft")).toBeInTheDocument();
    expect(screen.getByText("Get coaching")).toBeInTheDocument();
  });

  it("registers hotkey handler on mount via window.api.onHotkey", () => {
    render(<Overlay />);
    expect(mockOnHotkey).toHaveBeenCalledOnce();
  });

  it("calls the cleanup function from onHotkey when unmounted", () => {
    const cleanup = vi.fn();
    mockOnHotkey.mockReturnValueOnce(cleanup);
    const { unmount } = render(<Overlay />);
    unmount();
    expect(cleanup).toHaveBeenCalledOnce();
  });
});
