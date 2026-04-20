---
title: Design Overview
description: Dark, minimal, gold-accented floating overlay that lives beside meeting apps.
tags: [design, guide, overview]
type: concept
related:
  - "[[Typography]]"
  - "[[Colors]]"
  - "[[Spacing and Radii]]"
  - "[[First-Run Wizard]]"
updated: 2026-04-19
---

# Design Overview

Persuasion Dojo is a **floating, always-on-top overlay** that sits beside
Zoom / Meet / Teams. Its visual language is dark, minimal, and
gold-accented — designed to feel like a private coach whispering in the
user's ear rather than a desktop app demanding attention.

`DESIGN.md` at the repo root is the **source of truth**. Everything in
this vault summarizes it; if the two ever disagree, `DESIGN.md` wins.

## Principles

- **Quiet by default.** The overlay is small, dim, and non-modal. It only
  raises its voice when coaching fires.
- **One thing at a time.** A single coaching prompt dominates the frame;
  secondary content recedes.
- **Gold means intelligence.** `#D4A853` is reserved for coaching,
  scores, and primary CTAs — the things the user should trust.
- **Never pure black, never pure white.** Pure values feel cheap and
  fatigue the eye during long meetings.

## Aesthetic map

```mermaid
flowchart LR
  A[Dark canvas<br/>#1A1A1E] --> B[Card #222226]
  B --> C[Elevated #2A2A2F]
  C --> D[Hover #32323A]
  A --> E[Gold accent<br/>#D4A853]
  E --> F[Coaching prompts]
  E --> G[Primary CTAs]
  E --> H[Score badges]
```

## QA-enforced rules

- Background must be `#1A1A1E` — **never** `#000000`.
- Typography: [[Typography|Playfair Display + DM Sans + JetBrains Mono]]
  only. No Inter / Roboto / Arial / Helvetica / Geist / Instrument Serif.
- Gold `#D4A853` is the single accent for coaching intelligence and
  primary CTAs — don't use it for decoration.
- See [[Colors]] for the full palette, [[Spacing and Radii]] for layout
  constants, and [[First-Run Wizard]] for the onboarding emotional arc.
