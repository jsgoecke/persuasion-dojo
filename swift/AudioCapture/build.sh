#!/bin/bash
# Build and codesign the AudioCapture binary for distribution.
#
# Usage:
#   ./build.sh                         # uses first "Developer ID Application" identity found
#   CODESIGN_IDENTITY="..." ./build.sh  # explicit identity string or SHA-1
#
# The signed binary is written to .build/release/AudioCapture.
# electron-builder picks it up from there via the extraResources entry.
set -euo pipefail

cd "$(dirname "$0")"

# ── Build release binary ───────────────────────────────────────────────────

echo "Building AudioCapture (release)…"
swift build -c release 2>&1

BINARY=".build/release/AudioCapture"

if [[ ! -f "$BINARY" ]]; then
  echo "Error: binary not found at $BINARY" >&2
  exit 1
fi

# ── Codesign ──────────────────────────────────────────────────────────────

# Prefer explicit identity, fall back to first Developer ID Application cert.
if [[ -z "${CODESIGN_IDENTITY:-}" ]]; then
  CODESIGN_IDENTITY=$(
    security find-identity -v -p codesigning 2>/dev/null \
      | grep "Developer ID Application" \
      | head -1 \
      | sed 's/.*"\(.*\)"/\1/'
  )
fi

if [[ -z "$CODESIGN_IDENTITY" ]]; then
  echo "Warning: no Developer ID Application certificate found — skipping codesign." >&2
  echo "The binary will work on your own machine but cannot be distributed." >&2
  echo "Set CODESIGN_IDENTITY to a valid Developer ID to sign for distribution." >&2
  exit 0
fi

echo "Signing with: $CODESIGN_IDENTITY"

codesign \
  --force \
  --options runtime \
  --entitlements AudioCapture.entitlements \
  --sign "$CODESIGN_IDENTITY" \
  "$BINARY"

# Verify the signature and entitlements.
codesign --verify --verbose "$BINARY"
codesign -d --entitlements - "$BINARY" 2>/dev/null | grep -q "screen-capture" \
  && echo "✓ com.apple.security.screen-capture entitlement confirmed" \
  || { echo "Error: screen-capture entitlement missing after signing" >&2; exit 1; }

echo "Done: $BINARY"
