---
title: Scripts and MDM
description: Purpose of every file in scripts/ plus the MDM mobileconfig used for corporate deployment.
tags: [tooling, ops]
type: guide
related:
  - "[[Key Constraints and Decisions]]"
  - "[[Scoring Engine]]"
updated: 2026-04-19
---

# Scripts and MDM

Small tools live in `scripts/`. The MDM profile lives in `resources/mdm/`. Neither is shipped to end users — both are internal build/validation infrastructure.

## scripts/

- **`build-mdm-profile.sh`** — signs `resources/mdm/PersuasionDojo.mobileconfig` via `security cms -S` with a Developer ID Application certificate (auto-detected, overridable with `SIGN_CERT=`). Validates the plist, writes to `dist/`, and verifies the signature post-sign. Output is the file MDM administrators upload to Jamf / Mosyle / Kandji.
- **`convergence_spike.py`** — P0 gate script. Runs the convergence signal detectors against annotated transcripts and fails if agreement is below 75%. Cleared on 2026-03-25 with 3/3 signals correct on a real Granola transcript.
- **`voiceprint_spike.py`** — validation gate for WeSpeaker ECAPA-TDNN. Pass criterion: intra-speaker cosine similarity >0.6 and inter-speaker <0.4 on held-out audio. Cleared before v0.11.1.0 shipped.
- **`convert_granola.py`** — converts a Granola transcript (named speakers, no timestamps) into the spike format (utterances with synthetic timestamps) so `convergence_spike.py` can consume it.
- **`real_world_gate.py`** — pre-seeding accuracy gate. Classifies known-profile participants from their descriptions; pass criterion is ≥70% correct. Cleared 2026-03-25 with 5/5 Sailplane team members.
- **`sample_annotation.json`** / **`sample_transcript.json`** — small fixtures showing the expected input format for the spike scripts.
- **`spike_results.json`** — recorded output from the last convergence spike run against the sample fixture.
- **`spike_transcripts/`** — the 5 real meeting transcripts plus human annotations that actually cleared the convergence gate. Kept in-tree so the gate can be re-run after signal-detector changes.

## MDM resources

- **`resources/mdm/PersuasionDojo.mobileconfig`** — unsigned Privacy Preferences Policy Control profile (`com.apple.TCC.configuration-profile-policy`). Pre-grants ScreenCapture (main app + SCK helper) and Microphone permissions for `com.persuasiondojo.overlay`. Must be signed via `build-mdm-profile.sh` before upload. Deploy target is corporate IT managing fleets where Screen Recording permission would otherwise be blocked (see [[Key Constraints and Decisions]]).
