---
title: Running the Swift Binary
description: Build the ScreenCaptureKit-based AudioCapture binary in dev or release mode, grant screen-recording permission, and wire it to the backend.
tags: [guide, getting-started, lang/swift, layer/audio]
type: guide
related:
  - "[[AudioCapture Binary]]"
  - "[[TCP Transport]]"
  - "[[Audio Lifecycle and Supervision]]"
  - "[[Environment Variables]]"
updated: 2026-04-19
---

# Running the Swift Binary

## Debug build

```bash
cd swift/AudioCapture
swift build -c debug
./.build/debug/AudioCapture
```

The binary reads `AUDIO_BACKEND_PORT` from the environment (default `9090`) and dials `127.0.0.1:<port>`. See [[TCP Transport]] for the wire format.

## Release build + code signing

```bash
./build.sh
# or manually
swift build -c release
codesign --force --options runtime \
  --entitlements AudioCapture.entitlements \
  --sign "Developer ID Application: Your Name (TEAMID)" \
  .build/release/AudioCapture
```

Auto-detects the first `Developer ID Application` cert in your Keychain, or pass `CODESIGN_IDENTITY=<cert>` to override. Verifies signature + entitlements post-signing.

## Permissions

The binary requires **Screen Recording** permission:

- **System Settings → Privacy & Security → Screen Recording** → enable Persuasion Dojo.app (or the bare binary when running standalone during dev).

Permission is silently revoked when the bundle signature changes (e.g. after an update). The [[Audio Lifecycle and Supervision|silence watchdog]] detects this (>5s of no audio) and signals Electron to restart the binary so the user is re-prompted.

## Entitlements

`swift/AudioCapture/AudioCapture.entitlements`:

```xml
<key>com.apple.security.screen-capture</key>
<true/>
```

Required for the Hardened Runtime (`--options runtime`) to allow ScreenCaptureKit.

## Troubleshooting

- **Exit code 2** — Screen Recording denied. Open System Settings → Privacy & Security → Screen Recording.
- **Connection refused** — backend not running, or wrong `AUDIO_BACKEND_PORT`. Check `uvicorn backend.main:app` is up.
- **Silent binary** — check stderr (`AudioCapture: streaming started...` should appear within ~5s). If not, ScreenCaptureKit startup timed out.

## Next

→ [[AudioCapture Binary]]
→ [[TCP Transport]]
→ [[Audio Lifecycle and Supervision]]
