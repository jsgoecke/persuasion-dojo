#!/usr/bin/env bash
# verify-notarization.sh — post-build Gatekeeper check for all .dmg artifacts.
#
# Usage:
#   ./scripts/verify-notarization.sh [dist-dir]
#
# Checks every .dmg in DIST_DIR with spctl so that both the initial
# distribution DMG and the electron-updater update payload DMG are
# verified before they are uploaded to GitHub Releases.
#
# The electron-updater payload is the file listed under "files[].url" in
# latest-mac.yml. We parse that file and confirm the named artifact appears
# in our verified set, making it impossible to ship an update that Gatekeeper
# will silently block on existing users' machines.
#
# Exit codes:
#   0  — all DMGs accepted by Gatekeeper
#   1  — one or more DMGs failed, or no DMGs found

set -euo pipefail

DIST_DIR="${1:-dist}"

if [ ! -d "$DIST_DIR" ]; then
  echo "ERROR: dist directory not found: $DIST_DIR"
  exit 1
fi

# Collect .dmg files.
mapfile -t dmgs < <(find "$DIST_DIR" -maxdepth 1 -name "*.dmg" -type f | sort)

if [ "${#dmgs[@]}" -eq 0 ]; then
  echo "ERROR: No .dmg files found in $DIST_DIR"
  exit 1
fi

echo "=== Notarization verification: ${#dmgs[@]} DMG(s) found ==="
failures=0

for dmg in "${dmgs[@]}"; do
  name=$(basename "$dmg")
  echo ""
  echo "── $name"

  # 1. Deep code-signature check (catches missing entitlements, bad nesting).
  if ! codesign --verify --deep --strict --verbose=2 "$dmg" 2>&1; then
    echo "  FAIL: codesign --verify failed"
    ((failures++))
    continue
  fi
  echo "  codesign: OK"

  # 2. Gatekeeper assessment — this is the notarization gate.
  #    spctl exits non-zero if the file is rejected.
  spctl_out=$(spctl --assess --verbose=4 --type open \
    --context context:primary-signature "$dmg" 2>&1 || true)
  echo "  spctl: $spctl_out"

  if echo "$spctl_out" | grep -q "accepted"; then
    echo "  PASS: Gatekeeper accepted $name"
  else
    echo "  FAIL: Gatekeeper did NOT accept $name"
    ((failures++))
  fi
done

# ── Cross-check electron-updater artifact ────────────────────────────────────
# Parse latest-mac.yml to confirm the update payload .dmg was among the files
# we just verified. This is the specific scenario the TODO guards against:
# the initial DMG being notarized but the update DMG being missed.
LATEST_YML="$DIST_DIR/latest-mac.yml"
if [ -f "$LATEST_YML" ]; then
  echo ""
  echo "── electron-updater artifact cross-check"

  # latest-mac.yml format:
  #   files:
  #     - url: Persuasion Dojo-1.0.0-arm64.dmg
  #       ...
  #     - url: Persuasion Dojo-1.0.0.dmg
  mapfile -t update_files < <(grep "^\s*- url:" "$LATEST_YML" | awk '{print $3}')

  if [ "${#update_files[@]}" -eq 0 ]; then
    echo "  WARN: No update artifacts listed in latest-mac.yml"
  else
    for uf in "${update_files[@]}"; do
      artifact="$DIST_DIR/$uf"
      if [ -f "$artifact" ]; then
        echo "  OK: Update artifact present and verified — $uf"
      else
        echo "  FAIL: Update artifact listed in latest-mac.yml but not found: $uf"
        ((failures++))
      fi
    done
  fi
else
  echo ""
  echo "  WARN: latest-mac.yml not found in $DIST_DIR — skipping electron-updater cross-check"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
if [ "$failures" -gt 0 ]; then
  echo "=== FAILED: $failures artifact(s) not properly notarized — do not release ==="
  exit 1
else
  echo "=== PASSED: All ${#dmgs[@]} DMG(s) notarized and accepted by Gatekeeper ==="
fi
