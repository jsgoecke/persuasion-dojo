#!/usr/bin/env bash
# =============================================================================
# build-mdm-profile.sh
#
# Sign the Persuasion Dojo MDM configuration profile.
#
# A signed .mobileconfig is required before uploading to a corporate MDM
# (Jamf Pro, Mosyle, Kandji, etc.).  Unsigned profiles can still be
# deployed manually by double-clicking on the user's Mac, but most MDM
# platforms reject unsigned payloads.
#
# Prerequisites
# -------------
#   1. An Apple Developer account with a valid signing certificate installed in
#      the system Keychain.  The certificate must be one of:
#        • "Developer ID Application: <Team Name> (<TeamID>)"  ← preferred
#        • "Apple Distribution: <Team Name> (<TeamID>)"
#        • "iPhone Distribution: <Team Name> (<TeamID>)"       ← legacy
#
#   2. macOS codesign / security tools (part of Xcode Command Line Tools).
#
# Usage
# -----
#   # Auto-detect the first valid signing certificate:
#   ./scripts/build-mdm-profile.sh
#
#   # Specify a certificate explicitly (use the full Common Name from Keychain):
#   SIGN_CERT="Developer ID Application: Acme Corp (ABCD123456)" \
#     ./scripts/build-mdm-profile.sh
#
#   # Override input / output paths:
#   INPUT=resources/mdm/PersuasionDojo.mobileconfig \
#   OUTPUT=dist/PersuasionDojo-signed.mobileconfig \
#     ./scripts/build-mdm-profile.sh
#
# Output
# ------
#   dist/PersuasionDojo-signed.mobileconfig  (default OUTPUT path)
#
# Verification
# ------------
#   security cms -D -i dist/PersuasionDojo-signed.mobileconfig
#   # Should print the decoded plist with no error.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INPUT="${INPUT:-"${REPO_ROOT}/resources/mdm/PersuasionDojo.mobileconfig"}"
OUTPUT="${OUTPUT:-"${REPO_ROOT}/dist/PersuasionDojo-signed.mobileconfig"}"

# ---------------------------------------------------------------------------
# Resolve signing certificate
# ---------------------------------------------------------------------------

if [[ -z "${SIGN_CERT:-}" ]]; then
  # Auto-detect the first Developer ID Application certificate in the keychain.
  SIGN_CERT=$(
    security find-certificate -a -Z -p /Library/Keychains/System.keychain \
      2>/dev/null \
    | grep -A 1 "Developer ID Application" \
    | grep "Developer ID Application" \
    | head -1 \
    | sed 's/^[[:space:]]*//' \
    || true
  )
fi

if [[ -z "${SIGN_CERT:-}" ]]; then
  # Fall back to the login keychain.
  SIGN_CERT=$(
    security find-certificate -a -Z -p ~/Library/Keychains/login.keychain-db \
      2>/dev/null \
    | grep -A 1 "Developer ID Application" \
    | grep "Developer ID Application" \
    | head -1 \
    | sed 's/^[[:space:]]*//' \
    || true
  )
fi

if [[ -z "${SIGN_CERT:-}" ]]; then
  echo "ERROR: No 'Developer ID Application' certificate found in keychain." >&2
  echo "" >&2
  echo "  Install a valid Apple Developer signing certificate, or set SIGN_CERT:" >&2
  echo '  SIGN_CERT="Developer ID Application: Acme Corp (ABCD1234)" \\' >&2
  echo '    ./scripts/build-mdm-profile.sh' >&2
  exit 1
fi

echo "Signing with: ${SIGN_CERT}"

# ---------------------------------------------------------------------------
# Validate input
# ---------------------------------------------------------------------------

if [[ ! -f "${INPUT}" ]]; then
  echo "ERROR: Input profile not found: ${INPUT}" >&2
  exit 1
fi

# Basic plist validation
if ! plutil -lint "${INPUT}" >/dev/null 2>&1; then
  echo "ERROR: Input profile is not a valid plist: ${INPUT}" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Create output directory
# ---------------------------------------------------------------------------

mkdir -p "$(dirname "${OUTPUT}")"

# ---------------------------------------------------------------------------
# Sign the profile using CMS (Cryptographic Message Syntax)
# ---------------------------------------------------------------------------
# security cms -S signs the plist as a PKCS#7/CMS envelope.
# The MDM reads the inner plist; the signature is verified by the MDM server.

security cms \
  -S \
  -N "${SIGN_CERT}" \
  -i "${INPUT}" \
  -o "${OUTPUT}"

echo ""
echo "✓ Signed profile written to: ${OUTPUT}"
echo ""

# ---------------------------------------------------------------------------
# Verify the signature
# ---------------------------------------------------------------------------

echo "Verifying signature..."
security cms -D -i "${OUTPUT}" > /dev/null
echo "✓ Signature verified."
echo ""

# ---------------------------------------------------------------------------
# Print deployment instructions
# ---------------------------------------------------------------------------

echo "Deployment:"
echo "  1. Upload '${OUTPUT}' to your MDM (Jamf, Mosyle, Kandji, etc.)."
echo "  2. Scope the profile to the desired smart group."
echo "  3. Force a check-in or wait for the next MDM sync (~15 min)."
echo ""
echo "Manual install (for testing on a single Mac):"
echo "  open '${OUTPUT}'"
echo "  System Settings → Privacy & Security → Profiles → install"
echo ""
echo "Verify on a managed device:"
echo "  profiles list | grep 'Persuasion Dojo'"
