---
title: Prerequisites
description: System requirements before cloning the Persuasion Dojo repo — macOS version, Python, Node, Xcode, and Docker.
tags: [guide, getting-started]
type: guide
related:
  - "[[First-Time Setup]]"
  - "[[Running the Swift Binary]]"
updated: 2026-04-19
---

# Prerequisites

## System requirements

- **macOS 12.3+** — required for ScreenCaptureKit. Older macOS cannot capture system audio without the Zoom SDK.
- **Python 3.12+** — declared in `pyproject.toml` (`requires-python = ">=3.12"`).
- **Node.js 20+** — for the [[Frontend Overview|Electron overlay]].
- **Xcode Command Line Tools** — for the [[AudioCapture Binary|Swift binary]]. Install with `xcode-select --install`.
- **Docker** (optional) — for containerized backend deployment, see [[Docker Deployment]].

## Verify your setup

```bash
sw_vers -productVersion    # must be 12.3 or higher
python3 --version          # must be 3.12+
xcode-select --print-path  # must return a path
node --version             # must be 20+
npm --version
docker --version           # optional
```

## Next

→ [[Environment Variables]]
→ [[First-Time Setup]]
