/**
 * Calendar pane — Google OAuth connection + upcoming meetings list.
 *
 * Flow (loopback redirect — standard for desktop apps):
 *   1. GET /calendar/status → check if configured + connected
 *   2. GET /calendar/auth-url → open Google consent in system browser
 *   3. Google redirects to http://127.0.0.1:8000/calendar/callback
 *   4. Backend exchanges code automatically → user sees "Connected!" in browser
 *   5. Frontend polls /calendar/status until connected, then loads meetings
 */
import React, { useState, useEffect, useCallback, useRef } from "react";

const API = "http://localhost:8000";

interface Meeting {
  id: string;
  title: string;
  start: string;
  attendees: string[];
}

interface CalendarPaneProps {
  onBack: () => void;
  onPreseedAttendees?: (attendees: string[]) => void;
}

export function CalendarPane({ onBack, onPreseedAttendees }: CalendarPaneProps): React.ReactElement {
  const [configured, setConfigured] = useState(false);
  const [connected, setConnected] = useState(false);
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [loadingMeetings, setLoadingMeetings] = useState(false);
  const [waitingForAuth, setWaitingForAuth] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const checkStatus = useCallback(async (): Promise<{ configured: boolean; connected: boolean }> => {
    try {
      const res = await fetch(`${API}/calendar/status`);
      const data = await res.json();
      setConfigured(data.configured);
      setConnected(data.connected);
      return data;
    } catch {
      return { configured: false, connected: false };
    }
  }, []);

  const fetchMeetings = useCallback(async (): Promise<void> => {
    setLoadingMeetings(true);
    setError(null);
    try {
      const res = await fetch(`${API}/calendar/meetings?hours_ahead=48`);
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail ?? `HTTP ${res.status}`);
      }
      const data: Meeting[] = await res.json();
      setMeetings(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingMeetings(false);
    }
  }, []);

  // Check status on mount, load meetings if connected
  useEffect(() => {
    checkStatus().then((s) => {
      if (s.connected) fetchMeetings();
    });
  }, [checkStatus, fetchMeetings]);

  // Poll for connection while waiting for OAuth callback
  useEffect(() => {
    if (!waitingForAuth) return;
    pollRef.current = setInterval(async () => {
      const s = await checkStatus();
      if (s.connected) {
        setWaitingForAuth(false);
        fetchMeetings();
      }
    }, 2000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [waitingForAuth, checkStatus, fetchMeetings]);

  async function startOAuth(): Promise<void> {
    setError(null);
    try {
      const res = await fetch(`${API}/calendar/auth-url`);
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail ?? `HTTP ${res.status}`);
      }
      const { url } = await res.json();
      // Open in system browser
      if (window.electronAPI?.openExternal) {
        window.electronAPI.openExternal(url);
      } else {
        window.open(url, "_blank");
      }
      setWaitingForAuth(true);
    } catch (e) {
      setError(String(e));
    }
  }

  async function disconnect(): Promise<void> {
    try {
      await fetch(`${API}/calendar/disconnect`, { method: "POST" });
      setConnected(false);
      setMeetings([]);
    } catch (e) {
      setError(String(e));
    }
  }

  const container: React.CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 12,
    padding: "14px 16px",
    fontFamily: "var(--font-body)",
    color: "var(--text-primary)",
  };

  const primaryBtn: React.CSSProperties = {
    background: "var(--gold)",
    border: "none",
    borderRadius: 12,
    color: "var(--bg-primary)",
    fontSize: 13,
    fontWeight: 500,
    padding: "10px 16px",
    cursor: "pointer",
    transition: "background 200ms ease",
  };

  const ghostBtn: React.CSSProperties = {
    background: "transparent",
    border: "1px solid var(--border-medium)",
    borderRadius: 10,
    color: "var(--text-secondary)",
    fontSize: 12,
    padding: "6px 12px",
    cursor: "pointer",
    transition: "background 200ms ease, border-color 200ms ease",
  };

  // ── Not configured: prompt to add credentials in Settings ──
  if (!configured) {
    return (
      <div style={container}>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.6 }}>
          To connect your Google Calendar, add your Google OAuth credentials in <strong>Settings</strong>.
        </div>
        <div style={{ fontSize: 12, color: "var(--text-tertiary)", lineHeight: 1.6 }}>
          You'll need a Google Cloud project with the Calendar API enabled and an OAuth 2.0 Client ID (Desktop type).
        </div>
      </div>
    );
  }

  // ── Configured but not connected: show Connect button ──
  if (!connected) {
    return (
      <div style={container}>
        {error && <div style={{ fontSize: 12, color: "var(--red)", lineHeight: 1.4 }}>{error}</div>}
        <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.6 }}>
          Connect your Google Calendar to see upcoming meetings and pre-seed attendee profiles.
        </div>
        {waitingForAuth ? (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10, padding: "16px 0" }}>
            <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
              Waiting for authorization...
            </div>
            <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
              Complete the sign-in in your browser, then return here.
            </div>
            <button
              style={ghostBtn}
              onClick={() => setWaitingForAuth(false)}
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            style={primaryBtn}
            onClick={startOAuth}
            onMouseEnter={(e) => { e.currentTarget.style.background = "var(--gold-hover)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = "var(--gold)"; }}
          >
            Connect Google Calendar
          </button>
        )}
      </div>
    );
  }

  // ── Connected: show meetings ──
  return (
    <div style={container}>
      {error && <div style={{ fontSize: 12, color: "var(--red)", lineHeight: 1.4 }}>{error}</div>}

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--green)" }} />
          <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>Google Calendar connected</span>
        </div>
        <button
          style={{ ...ghostBtn, fontSize: 11, padding: "3px 8px", color: "var(--text-tertiary)" }}
          onClick={disconnect}
        >
          Disconnect
        </button>
      </div>

      {loadingMeetings && (
        <div style={{ fontSize: 12, color: "var(--text-secondary)", padding: "10px 0" }}>Loading meetings...</div>
      )}

      {!loadingMeetings && meetings.length === 0 && (
        <div style={{ fontSize: 13, color: "var(--text-tertiary)", padding: "20px 0", textAlign: "center" }}>
          No upcoming meetings in the next 48 hours.
        </div>
      )}

      {meetings.map((m) => (
        <div
          key={m.id}
          style={{
            background: "var(--bg-card)",
            borderRadius: 10,
            padding: "12px 14px",
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text-primary)" }}>{m.title}</div>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            {new Date(m.start).toLocaleString(undefined, {
              weekday: "short", month: "short", day: "numeric",
              hour: "numeric", minute: "2-digit",
            })}
          </div>
          {m.attendees.length > 0 && (
            <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>
              {m.attendees.slice(0, 4).join(", ")}
              {m.attendees.length > 4 && ` +${m.attendees.length - 4} more`}
            </div>
          )}
          {onPreseedAttendees && m.attendees.length > 0 && (
            <button
              onClick={() => onPreseedAttendees(m.attendees)}
              style={{
                background: "transparent",
                border: "1px solid var(--border-medium)",
                borderRadius: 10,
                color: "var(--gold)",
                fontSize: 11,
                padding: "4px 8px",
                cursor: "pointer",
                alignSelf: "flex-start",
                marginTop: 4,
                transition: "border-color 200ms ease",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--gold-border)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--border-medium)"; }}
            >
              Pre-seed attendees
            </button>
          )}
        </div>
      ))}

      <button
        style={{ ...ghostBtn, alignSelf: "center", marginTop: 4 }}
        onClick={fetchMeetings}
        disabled={loadingMeetings}
        onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(255,255,255,0.03)"; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
      >
        Refresh
      </button>
    </div>
  );
}

// Minimal Electron preload type shim
declare global {
  interface Window {
    electronAPI?: {
      openExternal?: (url: string) => void;
    };
  }
}
