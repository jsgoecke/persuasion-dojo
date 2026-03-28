# Design System — Persuasion Dojo

> **Source of truth:** `persuasion-dojo-reference-screens.html`
> Open the HTML file in a browser. Inspect every element with DevTools.
> The Electron app must match it pixel-for-pixel.

## Product Context
- **What this is:** A real-time conversation coaching app that listens to meetings and surfaces private text prompts telling the user how to be more persuasive in the moment.
- **Who it's for:** Senior executives and salespeople (VP+) with high-stakes conversations — board meetings, client pitches, procurement reviews. Revenue and influence tool, not self-improvement.
- **Platform:** macOS Electron desktop app — 480×720 companion panel, always-on-top beside Zoom/Teams/Meet.

---

## Typography

Three font families, no substitutions:

| Role | Font | Weight | Size | Color |
|------|------|--------|------|-------|
| App title | Playfair Display | 600 | 32px | `#D4A853` (gold) |
| Assessment title | Playfair Display | 600 | 24px | `#D4A853` |
| Reveal type name | Playfair Display | 600 | 30px | `#1A1A1E` (on gold card) |
| Body text | DM Sans | 400 | 14px | `#E8E6E1` |
| Labels/descriptions | DM Sans | 400 | 13px | `#9A9890` |
| Section labels (uppercase) | DM Sans | 500 | 11px | `#6A6860` |
| Coaching prompt text | DM Sans | 500 | 17px | `#D4A853` |
| Coaching prompt context | DM Sans | 400 | 13px | `#9A9890` |
| Buttons primary | DM Sans | 500 | 16px | `#1A1A1E` |
| Buttons ghost | DM Sans | 400 | 13px | `#E8E6E1` |
| Transcript | JetBrains Mono | 400 | 13px | `#9A9890` |
| Timer | JetBrains Mono | 400 | 13px | `#9A9890` |
| API key inputs | JetBrains Mono | 400 | 13px | `#E8E6E1` |

**Never use:** Inter, Roboto, Arial, Helvetica, Geist, Instrument Serif.

---

## Color System

### Backgrounds (dark — V1 only theme)
| Token | Value | Usage |
|-------|-------|-------|
| `--bg-primary` | `#1A1A1E` | App background, deepest layer |
| `--bg-card` | `#222226` | Card surfaces, input backgrounds |
| `--bg-elevated` | `#2A2A2F` | Hover states, active panels, prompt cards |
| `--bg-hover` | `#32323A` | Hover on elevated surfaces |

### Text
| Token | Value | Usage |
|-------|-------|-------|
| `--text-primary` | `#E8E6E1` | Main body text, headings |
| `--text-secondary` | `#9A9890` | Labels, descriptions, metadata |
| `--text-tertiary` | `#6A6860` | Timestamps, hints, disabled |

### Accents — each has a single semantic meaning
| Token | Value | Usage |
|-------|-------|-------|
| `--gold` | `#D4A853` | Coaching intelligence, primary CTAs, superpower badge |
| `--red` | `#C75B4A` | LIVE indicator dot only, danger buttons, blind spot card |
| `--green` | `#5A9E6F` | Post coach accent, success states |
| `--blue` | `#7B8EC2` | Participant elements ONLY (initials circle, chips, speaker names) |

### Forbidden colors
- `#000000` or any pure black
- `#FFFFFF` or any pure white
- Any blue as an accent (like `#007AFF`, `#2196F3`, `#3B82F6`). Steel blue `#7B8EC2` is ONLY for participant elements.

---

## Spacing & Radii

### Border radii
| Element | Radius |
|---------|--------|
| Primary buttons, coaching prompt card | 12px |
| Ghost buttons, inputs, assessment options, archetype cards | 10px |
| Recent session rows, small elements | 8px |
| Participant chips | 20px (pill) |
| Superpower reveal card, app window | 16px |

### Button heights
| Type | Height |
|------|--------|
| Primary CTA (gold filled) | 54px |
| Second primary (gold outlined) | 50px |
| Ghost buttons | 42px |
| Danger button | 42px |

### Spacing
- Content padding (horizontal): 28px
- Gap between primary and outlined button: 10px
- Gap between outlined button and ghost row: 12px
- Gap between ghost buttons in a row: 8px
- Card internal padding: 18px 20px (prep cards), 22px 24px (coaching prompt), 14px 16px (moment cards)
- Coaching prompt left border: 4px solid gold
- Prep card left border: 3px solid accent

---

## Screens & Navigation

No sidebar, no tabs, no bottom nav. Each state is a full-screen view with crossfade transition.

```
Home
  ├─> Go Live ─> Pre-session Setup ─> Live Session ─> Post-session Review ─> Home
  ├─> Prepare ─> Preparation Hub
  │     ├─> Spar Setup ─> Sparring Session (WebSocket) ─> Home
  │     ├─> Rehearse Setup ─> Rehearsal Session (WebSocket) ─> Home
  │     └─> Post Coach ─> POST /coach/text ─> Coaching tips
  ├─> Self Assessment ─> Assessment Questions ─> Reveal ─> Home
  ├─> Profiles ─> PreSeedPane (POST /participants/pre-seed)
  ├─> Retro ─> RetroImportPane (POST /retro/upload)
  └─> Settings ─> SettingsPane (GET/POST /settings)
```

Every sub-screen has a "← Back" link (13px, `#9A9890`, hover `#E8E6E1`) top-left, title centered, spacer right.

---

## Electron Window

```javascript
{
  width: 480, height: 720,
  minWidth: 420, minHeight: 600, maxWidth: 600,
  backgroundColor: '#1A1A1E',
  titleBarStyle: 'hiddenInset',
  trafficLightPosition: { x: 18, y: 18 },
  frame: false, transparent: false,
  alwaysOnTop: true, resizable: true,
}
```

Titlebar: 48px tall, `-webkit-app-region: drag`. All buttons inside need `no-drag`.

---

## Animations

| Name | Duration | Usage |
|------|----------|-------|
| `livePulse` | 2s ease-in-out infinite | Red LIVE dot |
| `sparPulse` | 2.5s ease-in-out infinite | Gold SPAR dot |
| `promptIn` | 400ms ease-out | Coaching prompt entrance |
| `revealIn` | 600ms ease-out | Superpower reveal card |
| `fadeIn` | 200ms ease-out | Screen transitions |
| `shimmer` | 3s ease-in-out infinite | Unassessed badge border |

---

## Backend Integration

| Screen | Component | Endpoint |
|--------|-----------|----------|
| Live Session | `useCoachingSocket` | POST `/sessions`, WebSocket `/ws/session/{id}` |
| Sparring Session | `SparringPane` → `useSparringSocket` | POST `/sparring/sessions`, WebSocket `/ws/sparring/{id}` |
| Rehearsal Session | `SparringPane` → `useSparringSocket` | Same as sparring |
| Settings | `SettingsPane` | GET/POST `/settings` |
| Profiles | `PreSeedPane` | POST `/participants/pre-seed` |
| Retro | `RetroImportPane` | POST `/retro/upload`, GET `/retro/jobs/{id}` |
| Post Coach | `Overlay` (post-coach screen) | POST `/coach/text` |
| Self Assessment | Inline (4 questions) | localStorage only (no backend) |

### Backend endpoints NOT yet wired to frontend
- GET `/sessions/{id}` — could power session review with real data
- GET `/users/me` — could display user profile
- Calendar endpoints (`/calendar/*`) — Google Calendar integration exists but no UI
- Team sync (`/team/export`, `/team/import`) — export/import exists but no UI

---

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-26 | Switched from Instrument Serif + Geist to Playfair Display + DM Sans + JetBrains Mono | Reference screens spec — better personality and readability |
| 2026-03-26 | Removed `vibrancy: 'hud'` and `transparent: true` from Electron config | Solid dark background matches mockups; vibrancy made content washed out |
| 2026-03-26 | Changed bg from `#1C1C1E` to `#1A1A1E` | Reference screens spec |
| 2026-03-26 | Added Preparation Hub with Spar/Rehearse/Post Coach cards | New navigation structure from brief |
| 2026-03-26 | Replaced inline settings with SettingsPane component | Real backend wiring (GET/POST /settings) |
| 2026-03-26 | Replaced inline spar/rehearse with SparringPane | Real WebSocket backend integration |
| 2026-03-26 | Replaced inline profiles with PreSeedPane | Real backend wiring (POST /participants/pre-seed) |
| 2026-03-26 | Wired recent sessions to GET /sessions | Home screen shows real session history from backend |
| 2026-03-26 | Wired "View full transcript" to retro screen | Users can upload recordings for transcript analysis |
| 2026-03-26 | Built Post Coach screen + POST /coach/text endpoint | Text-based persuasion coaching on drafts (LinkedIn, email, etc.) |
