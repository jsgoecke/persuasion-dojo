# Speaker Identification Research: World-Class Diarization for Bot-Free Meeting Apps

**Date:** 2026-04-09 (v2, complete rewrite)
**Version:** 0.10.2.0
**Status:** Research complete, ready for implementation planning

---

## Executive Summary

Persuasion Dojo's speaker identification is poor not because of architectural flaws, but because of fixable implementation gaps. The dual-stream capture (mic + system audio) is correct and matches industry best practice. The problems are: Deepgram mono diarization is unreliable for multi-speaker system audio, the SpeakerResolver runs too slowly with too little context, and there's no audio-based voiceprint matching.

Granola, the benchmark, does NOT solve multi-speaker identification. They label speakers as "Me" vs "Them" only. We are already ahead of Granola architecturally. The path to world-class: fix the resolver (Phase 1), add voiceprint embeddings (Phase 2), optionally run local streaming diarization (Phase 3).

---

## Part 1: Granola App Analysis

### Architecture (analyzed from /Applications/Granola.app v7.99.1)

- **Platform:** Electron 41.0.2 (Chromium 146)
- **Bundle ID:** com.granola.app
- **Native binary:** `granola.node` (1.5MB universal, N-API) implements `CombinedAudioCapture`
- **Auth:** Supabase + WorkOS
- **Backend API:** api.granola.ai (subdomains: cinnamon, stream, berry, pecan)
- **Local storage:** SQLite via WASM + JSON cache (`cache-v6.json`)

### Dual-Stream Audio Capture

Granola captures two audio streams simultaneously via native binary:

1. **Microphone stream** — CoreAudio `AudioUnit` (direct input device, `kAudioOutputUnitProperty_CurrentDevice`)
2. **System audio stream** — ScreenCaptureKit (`SCStream`, `SCShareableContent`) with CoreAudio fallback

Key evidence from binary strings:
- `audio-capture-use-screencapturekit` and `audio-capture-use-coreaudio` — two capture paths
- `Recording microphone and system audio`
- Separate `microphoneBuffer` and `systemAudioBuffer`
- `CAPTURE_METHODS = ["browser", "mac-core-audio", "win-v2-native"]`

### Speaker Identification — The Truth

**Granola explicitly disables diarization.** Both Deepgram and AssemblyAI connections set `diarize: false`.

Speaker identification is trivially solved by the dual-stream architecture:
- `source === "microphone"` → "Me" (green bubbles)
- `source === "system"` → "Them" (grey bubbles)

Frontend rendering code confirms:
```javascript
const p = h => h.source === "microphone"
  ? (i && h.contributor?.userName ? h.contributor.userName : "Me")
  : h.source === "system" ? "Them" : h.source;
```

For collaborative meetings (multiple Granola users), a `contributor` object with `userName` enables per-user attribution among Granola users only.

**Granola cannot distinguish between multiple remote participants.** CEO Chris Pedregal publicly acknowledged: "Real time diarization is really tough, but we're working on it."

### Transcription Providers (Failover Architecture)

| Provider | Endpoint | Model | Notes |
|----------|----------|-------|-------|
| **Deepgram** (primary) | `wss://api.deepgram.com/v1/listen` | nova-3 (feature-flagged), nova-2 fallback | `diarize:false`, `smart_format:true`, `interim_results:true` |
| **AssemblyAI** (secondary) | `wss://streaming.assemblyai.com/v3/ws` | Universal v2 | API version 2025-05-12, `format_turns:true` |
| **Speechmatics** (tertiary) | `wss://eu2.rt.speechmatics.com/v2` | Standard + Enhanced variants | |

Token management via backend endpoints: `get-deepgram-token`, `get-transcription-auth-token`.

### Other Notable Granola Features

| Module | Purpose |
|--------|---------|
| `ambient_context.node` | Window title monitoring via accessibility APIs → sent to `process-ambient-context` API |
| `chatpaste.node` | Pastes consent messages into Zoom/Teams/Meet chat via accessibility APIs |
| `eventkit.node` | macOS Calendar (EventKit) integration |
| `macos_mic_apps_with_devices.node` | Detects which apps use the microphone |
| `GranolaTalk` | Dictation feature using Deepgram nova-3 + Groq (Llama 3.3 70B) for formatting |

### No Patents Found

Searches across Google Patents, USPTO, and general web for patents by Granola Inc or Chris Pedregal related to transcription or speaker identification returned zero results.

---

## Part 2: Current Persuasion Dojo Architecture

### What Works Well

The dual-stream capture is correctly implemented:

| Component | Status | Details |
|-----------|--------|---------|
| Swift AudioCapture binary | Working | ScreenCaptureKit (system) + AVAudioEngine (mic) |
| Dual named pipes | Working | `/tmp/persuasion_audio.pipe` (system) + `/tmp/persuasion_mic.pipe` (mic) |
| Separate Deepgram connections | Working | System: `diarize=true`, Mic: `diarize=false` |
| Echo suppression | Working | Text similarity filter (0.6 threshold) between mic and system utterances |
| Speaker routing | Working | Mic → "user", System → "counterpart_0", "counterpart_1", etc. |

### What's Broken (10 Specific Issues)

#### CRITICAL #1: SpeakerResolver Runs Every 60 Seconds
- **Location:** `backend/speaker_resolver.py:83`
- **Impact:** Users see "counterpart_0" for a full minute minimum. Introductions ("Hi, I'm Sarah") happen in the first 30 seconds and may not be caught until the second cycle.
- **Fix:** Drop to 15s (match coaching cadence floor).

#### CRITICAL #2: Only Last 100 Utterances in Context
- **Location:** `backend/speaker_resolver.py:165`
- **Impact:** In a 30+ minute meeting, the identifying moments (introductions, first-time addressing) have scrolled out of the window.
- **Fix:** Always include first 20 utterances + last 80.

#### MAJOR #3: Exact String Match on Known Names
- **Location:** `backend/speaker_resolver.py:212`
- **Impact:** Claude returns "Sarah Chen" but calendar says "Sarah Lynn Chen" → rejected. `identity.py` has fuzzy matching (0.85 threshold) that the resolver never uses.
- **Fix:** Use `SequenceMatcher` from `identity.py` instead of `in` operator.

#### MAJOR #4: Confidence Can Never Decrease
- **Location:** `backend/speaker_resolver.py:226`
- **Impact:** Wrong mapping at 0.72 confidence can never be corrected. Wrong mapping at 0.82 is permanently locked.
- **Fix:** Allow confidence to decrease with a decay factor. Use `max(new_confidence, existing * 0.9)` logic.

#### MAJOR #5: No Cross-Session Speaker Memory
- **Location:** `backend/speaker_resolver.py` (entire file)
- **Impact:** Each session starts from scratch. If you talked to Sarah Chen last week, the resolver doesn't know.
- **Fix:** Query Participant DB at session start. Pre-seed known_names from past sessions.

#### MAJOR #6: Hybrid Failover Loses Diarization
- **Location:** `backend/hybrid_transcription.py:191-227`
- **Impact:** Ring buffer stores raw audio bytes only. When replayed through Moonshine after Deepgram failure, all speaker IDs become speaker_0.
- **Fix:** Accept diarization loss during failover (document as known limitation) or store diarization metadata alongside audio in ring buffer.

#### MEDIUM #7: Late Database Persistence
- **Location:** `backend/main.py:1933-1951`
- **Impact:** Resolved names written to DB only at session end. If session crashes, all name mappings are lost.
- **Fix:** Persist name mappings on each resolution cycle.

#### MEDIUM #8: No Speaker Assignment Conflict Detection
- **Impact:** Deepgram sometimes swaps speaker IDs mid-conversation. No consistency checking catches this.
- **Fix:** Track embedding centroids per speaker; flag when a speaker's embedding suddenly changes.

#### MEDIUM #9: Empty Roster Handling
- **Location:** `backend/main.py:1420`
- **Impact:** Sessions without Google Calendar have no known_names list. Claude must rely solely on self-identification.
- **Fix:** Cross-session participant DB provides a "likely attendees" list even without calendar.

#### MINOR #10: Using Deepgram nova-2, Not nova-3
- **Impact:** nova-3 has improved diarization. Granola defaults to nova-3.
- **Fix:** Upgrade model parameter from `nova-2` to `nova-3`.

---

## Part 3: How Competitors Solve Speaker ID

### Otter.ai
- Uses a **meeting bot** that joins the call (access to participant roster)
- Builds **voiceprints** from user-recorded scripts
- Post-recording ML matches voiceprints to speaker segments
- Users can manually tag paragraphs to train the system
- Source: [Otter.ai Speaker Identification Overview](https://help.otter.ai/hc/en-us/articles/21665587209367)

### Fireflies.ai
- Uses a **meeting bot** for audio capture
- 4-stage AI process: audio preprocessing → feature extraction → speaker clustering → refinement
- Deep neural networks with multi-layer speaker embedding models
- Claims 95%+ accuracy up to 50 speakers (degrades above 6 with overlapping speech)

### Recall.ai (Infrastructure Provider)
- Three diarization methods:
  1. **Speaker Timeline** — uses meeting platform active speaker events
  2. **Perfect Diarization** — separate audio streams per participant (bot-only, 100% accurate)
  3. **Machine Diarization** — AI-based voice recognition
- Their Desktop Recording SDK (Granola-like) does NOT support perfect diarization
- Confirms: bot-free apps cannot get per-participant audio streams

### Caret
- Claims "only product offering real-time speaker diarization" in bot-free category
- Uses "audio sampling and LLM" for speaker identification
- Native C++ engine with AGC, VAD, active noise cancellation
- Source: [Show HN: Caret](https://news.ycombinator.com/item?id=44522847)

### Microsoft Teams
- Built-in voiceprint enrollment ("intelligent speakers")
- Users record a voice sample → stored securely
- Real-time matching during meetings
- Only works within Teams ecosystem

---

## Part 4: Academic Research & Open Source Tools

### ECAPA-TDNN (Speaker Embedding Model)
- **Architecture:** Emphasized Channel Attention, Propagation and Aggregation in TDNN
- **Output:** Fixed-dimensional speaker embedding vector from variable-length audio
- **Latency:** ~70ms inference per segment on CPU
- **Accuracy:** EER 0.87% on VoxCeleb1 (C=1024 variant)
- **Enrollment:** Produces embeddings comparable via cosine similarity
- **Available via:** SpeechBrain (`speechbrain/spkrec-ecapa-voxceleb`), WeSpeaker
- **Relevance:** This is how you build voiceprint enrollment. Extract embeddings, store them, compare live audio against stored profiles.

### WeSpeaker (Production Speaker Embeddings)
- **Architecture:** ECAPA-TDNN, ResNet, and others with first-class ONNX export
- **Runtime:** `wespeakerruntime` — lightweight C++/Python for deployment
- **Accuracy:** Competitive with SpeechBrain on VoxCeleb benchmarks
- **License:** Apache 2.0
- **GitHub:** [wenet-e2e/wespeaker](https://github.com/wenet-e2e/wespeaker)
- **Relevance:** HIGH. ONNX runtime is ideal for low-overhead embedding extraction on Mac. Better deployment story than PyTorch-based alternatives.

### diart (Streaming Speaker Diarization)
- **Architecture:** pyannote segmentation model + embedding model + incremental online clustering
- **Latency:** 500ms per update step, 1-2s end-to-end
- **Accuracy:** Competitive with offline systems on AMI, DIHARD benchmarks
- **Enrollment:** No (unsupervised clustering). Can post-match to known voiceprints.
- **License:** MIT
- **GitHub:** [juanmc2005/diart](https://github.com/juanmc2005/diart)
- **Relevance:** HIGH. Processes exactly your use case: single mono audio stream. 500ms update cadence fits <2s target.

### NVIDIA Streaming Sortformer
- **Architecture:** Fast-Conformer encoder + 18-layer Transformer with Arrival-Order Speaker Cache (AOSC)
- **Latency:** Frame-level, minimal latency
- **Accuracy:** Lower DER than other streaming systems
- **Enrollment:** No (zero-shot via AOSC)
- **License:** Open source via NeMo
- **HuggingFace:** [nvidia/diar_streaming_sortformer_4spk-v2.1](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1)
- **Relevance:** MEDIUM-HIGH. Best accuracy but requires NVIDIA GPU. Not suitable for on-device Mac.

### pyannote Speaker Diarization 3.1
- **Architecture:** Segmentation (powerset model) → embedding extraction → agglomerative clustering
- **Latency:** Offline (real-time factor ~2.5% on V100 GPU)
- **Accuracy:** State-of-the-art offline DER
- **License:** MIT (gated HuggingFace access)
- **Relevance:** MEDIUM. Not streaming-native. Its models power diart's streaming pipeline. Good for retroactive transcript processing.

### FluidAudio (CoreML on Apple Silicon)
- **Architecture:** pyannote Community-1 pipeline compiled to CoreML
- **Runtime:** Apple Neural Engine (ANE), not CPU or GPU
- **Latency:** Near real-time on ANE, 10-second window for streaming
- **Accuracy:** 23.2% DER on AMI English 16 meetings (single-channel)
- **License:** Apache 2.0
- **GitHub:** [FluidInference/FluidAudio](https://github.com/FluidInference/FluidAudio)
- **Relevance:** HIGH. Native Swift SDK, runs on same Mac as ScreenCaptureKit binary. ANE offloading means zero CPU cost. Could integrate directly into Swift AudioCapture binary.

### Picovoice Falcon
- **Architecture:** Proprietary on-device diarization engine optimized for CPU
- **Latency:** Not streaming (processes complete files). 25x real-time on single CPU core.
- **Accuracy:** Claims 5x better than Google STT diarization. 0.1 GiB memory vs pyannote's 1.5 GiB.
- **License:** Proprietary (free tier available)
- **Relevance:** MEDIUM. Great efficiency. Not streaming-native. Proprietary license is a consideration.

### Resemblyzer
- **Architecture:** GE2E speaker encoder from Google's paper. 256-dim embeddings.
- **Latency:** ~1000x real-time on GPU, CPU-friendly
- **Accuracy:** Older architecture, less accurate than ECAPA-TDNN
- **License:** Apache 2.0
- **GitHub:** [resemble-ai/Resemblyzer](https://github.com/resemble-ai/Resemblyzer)
- **Relevance:** LOW. Outdated. WeSpeaker is strictly better.

### Key Research Papers
- **Meeting Transcription Using Virtual Microphone Arrays** (Microsoft, 2019) — 3 async microphones yielded 11.1% WER improvement and 14.8% speaker-attributed WER improvement
- **Advances in Online Audio-Visual Meeting Transcription** (2019) — "Separate, Recognize, Diarize" (SRD) framework for overlapping speech (>10% of meeting time)
- **Deepgram Multichannel vs Diarization** — Deepgram explicitly recommends sending two-channel audio with `multichannel=true` for "effectively perfect diarization" on Me/Them split

---

## Part 5: Recommended Architecture

### Phase 1: Fix the SpeakerResolver (Low-Effort, High-Impact)

Changes to `backend/speaker_resolver.py`:

1. **Drop interval from 60s to 15s** — match coaching cadence floor
2. **Always include first 20 utterances** in context window — `first_20 + last_80` instead of `last_100`
3. **Add fuzzy name matching** — use `SequenceMatcher` (already in `identity.py`) with 0.85 threshold
4. **Query Participant DB at session start** — pre-seed `known_names` from past sessions with this user
5. **Allow confidence decay** — `if confidence < existing * 0.9: continue` instead of `if confidence < existing: continue`
6. **Persist mappings each cycle** — write to DB on each resolution, not just session end
7. **Upgrade to Deepgram nova-3** — better diarization model (Granola's default)

**Estimated impact:** Name resolution latency drops from 60s to 15s. Cross-session recognition works. Wrong mappings become correctable.

### Phase 2: Speaker Embeddings via WeSpeaker (The Differentiator)

Add audio-based voiceprints alongside text-based LLM resolver:

1. **Extract ECAPA-TDNN embeddings** from each diarized speech segment using WeSpeaker ONNX runtime
2. **Cluster embeddings per session** to build per-speaker centroids
3. **Compare centroids against stored Participant voiceprints** (cosine similarity, threshold ~0.7)
4. **Calendar-seeded matching** — narrow candidate set to expected attendees
5. **Store/update voiceprint centroids** in Participant records after each session
6. **Progressive refinement** — update stored embeddings with exponential moving average each meeting

**Performance budget:** WeSpeaker ONNX processes a 3-second segment in ~70ms on CPU. Processing 6 segments every 10 seconds = 420ms. Well within budget.

**New dependency:** `wespeakerruntime` (Apache 2.0, ONNX-based, no GPU required)

### Phase 3: Local Streaming Diarization (Optional, If Deepgram Unreliable)

If Deepgram nova-3 mono diarization is still unreliable:

1. **Run diart locally** as parallel diarization on system audio stream
2. **Use pyannote segmentation + WeSpeaker embeddings** (already loaded from Phase 2)
3. **Merge diart speaker labels** with Deepgram transcription timestamps
4. **Adds ~1-2s latency** but much better speaker boundary detection

### Phase 4: FluidAudio on Apple Neural Engine (Future)

For zero-CPU-cost diarization:

1. **Integrate FluidAudio Swift SDK** into AudioCapture binary
2. **Run diarization on ANE** alongside audio capture
3. **Send diarization labels through named pipe** alongside audio data
4. **Offloads all ML** from Python process entirely

### Phase Dependencies

```
Phase 1 (SpeakerResolver fixes)
  |
  ├── Phase 2 (WeSpeaker voiceprints)
  │     |
  │     └── Phase 3 (diart local diarization) — only if Deepgram still unreliable
  │
  └── Phase 4 (FluidAudio ANE) — independent, future
```

---

## Part 6: Validation Gates

Before implementing, validate with real ScreenCaptureKit audio (per CLAUDE.md constraints):

| Gate | Target | Method |
|------|--------|--------|
| Deepgram nova-3 mono DER | ≥85% accuracy | 5 real SCK-captured meetings |
| WeSpeaker ONNX inference latency | <100ms per 3s segment | M-series Mac CPU benchmark |
| Cosine similarity threshold | Maximize precision, maintain recall | ROC curve on 10+ SCK sessions |
| Cross-session voiceprint stability | Same person ≥0.85 cosine similarity across sessions | 3+ sessions with same participants |

---

## Part 7: Expected Outcomes

| Capability | Current | After Phase 1 | After Phase 2 |
|-----------|---------|---------------|---------------|
| Me/Them split | 100% accurate | 100% accurate | 100% accurate |
| Multi-remote speaker separation | Deepgram mono (unreliable) | Deepgram nova-3 (better) | Deepgram + voiceprint confirmation |
| Name resolution latency | 60s minimum | 15s minimum | 5-10s (audio-based) |
| Cross-session recognition | None | DB-backed names | DB-backed voiceprints |
| Calendar-seeded matching | Text only | Fuzzy text | Text + audio |
| Wrong name correction | Impossible (locked) | Correctable with decay | Audio evidence overrides text |

---

## Sources

### Granola Analysis
- Granola app binary analysis: `/Applications/Granola.app` v7.99.1
- [Granola Help Center: Transcription](https://docs.granola.ai/help-center/taking-notes/transcription)
- [Granola Security](https://www.granola.ai/security)
- [getprobo/reverse-engineering-granola-api](https://github.com/getprobo/reverse-engineering-granola-api)

### Competitor Analysis
- [Otter.ai Speaker Identification](https://help.otter.ai/hc/en-us/articles/21665587209367)
- [Recall.ai Desktop Recording SDK](https://www.recall.ai/product/desktop-recording-sdk)
- [Recall.ai Perfect Diarization](https://docs.recall.ai/docs/perfect-diarization)
- [Recall.ai: How to build a desktop recording app](https://www.recall.ai/blog/how-to-build-a-desktop-recording-app)
- [Show HN: Caret](https://news.ycombinator.com/item?id=44522847)

### Academic / Technical
- [Deepgram: Multichannel vs Diarization](https://developers.deepgram.com/docs/multichannel-vs-diarization)
- [AssemblyAI: Multichannel Speaker Diarization](https://www.assemblyai.com/blog/multichannel-speaker-diarization)
- [Microsoft: Meeting Transcription Using Virtual Microphone Arrays](https://ar5iv.labs.arxiv.org/html/1905.02545)
- [Advances in Online Audio-Visual Meeting Transcription](https://ar5iv.labs.arxiv.org/html/1912.04979)
- [ECAPA-TDNN](https://arxiv.org/abs/2005.07143)
- [Systematic Evaluation of Online Diarization Latency](https://arxiv.org/abs/2407.04293)
- [Optimizing DIART Inference](https://arxiv.org/abs/2408.02341)

### Open Source Tools
- [diart](https://github.com/juanmc2005/diart) — MIT, streaming diarization
- [WeSpeaker](https://github.com/wenet-e2e/wespeaker) — Apache 2.0, speaker embeddings
- [FluidAudio](https://github.com/FluidInference/FluidAudio) — Apache 2.0, CoreML diarization
- [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) — MIT
- [NVIDIA Streaming Sortformer](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1) — NeMo
- [Picovoice Falcon](https://picovoice.ai/platform/falcon/) — Proprietary
- [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit) — Whisper + streaming diarization

### Community Discussions
- [HN: Open-source Granola alternative](https://news.ycombinator.com/item?id=44271745)
- [HN: Granola API Reverse Engineering](https://news.ycombinator.com/item?id=45920768)
- [Medium: Why I ditched Granola for OBS](https://medium.com/@ilia_zadiabin/why-i-ditched-granola-for-a-15-minute-obs-setup-ee8763c55e7e)
