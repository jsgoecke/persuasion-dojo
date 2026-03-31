# Speaker Identification Research: Mapping Diarized Speakers to Real Names

**Date:** 2026-03-30
**Purpose:** Landscape analysis of how meeting intelligence products solve the speaker_0 → "Sarah Chen" problem.

---

## Executive Summary

There are **7 distinct approaches** to mapping anonymous diarized speakers to real names, ranging from trivial (platform metadata) to cutting-edge (multimodal LLMs). The right choice depends on your audio capture architecture. Persuasion Dojo's ScreenCaptureKit approach (mixed audio, no meeting bot) is the hardest case — you get a single mixed audio stream with no platform metadata. This constrains you to approaches 3-7 below.

---

## Approach 1: Platform Metadata (Meeting Bot)

**Used by:** Recall.ai, Nylas Notetaker, MeetStream.ai, Otter.ai (when bot-joined)

### How it works
A meeting bot joins the call as a participant, gaining access to the meeting platform's participant list, including names and email addresses. Many platforms (Zoom RTMS, Teams) provide **separate audio streams per participant** — each stream is tagged with the participant's identity. Recall.ai calls this "Perfect Diarization": transcribe each isolated stream independently, then label with the known participant name. No acoustic diarization needed at all.

### Technical details
- Zoom's Real-Time Media Streams (RTMS) is a WebSocket-based API providing real-time audio, video, transcripts, and participant metadata
- Recall.ai processes each participant's audio stream independently: mono 16-bit signed little-endian PCM at 16kHz
- When multiple people share a mic (e.g., conference room), standard diarization is used within that stream only

### Pros
- **100% accurate** speaker attribution when separate streams are available
- Works even with overlapping speech (streams are independent)
- Names come from the platform — no voice enrollment needed
- Real-time capable

### Cons
- **Requires a meeting bot** visible to all participants (social friction, compliance concerns)
- Platform-dependent — each platform (Zoom, Teams, Meet, Webex) has different APIs
- Some orgs block third-party bots via admin policies
- Engineering cost: must maintain bot infrastructure across platforms

### Latency
- Real-time (~100ms per stream chunk)

### Privacy implications
- Bot is visible to all participants — transparent but intrusive
- Audio data typically processed on vendor's cloud infrastructure
- Participant metadata (names, emails) flows through the bot platform

### Enrollment required
- None. Names come from the meeting platform.

### Relevance to Persuasion Dojo
- **Not directly applicable** — Persuasion Dojo uses ScreenCaptureKit (no bot). However, this is the gold standard for accuracy. Could be offered as an alternative capture mode for users who don't mind a bot.

---

## Approach 2: Calendar Pre-Seeding + First-Speaker Heuristics

**Used by:** Granola (partially), Meetily, most bot-free notetakers

### How it works
Pull the attendee list from a calendar invite (Google Calendar, Outlook) before the meeting starts. You now know *who* should be on the call (names + emails), but not *which diarized speaker* maps to which attendee. The simplest heuristic: Speaker 0 = first person to talk. More sophisticated: use speaking order, mic vs. system audio split, or ask the user to confirm after the first few utterances.

### Technical details
- Granola integrates with Google Calendar and labels speakers as "Me" (microphone input) and "Them" (system audio) on desktop
- The "Me" vs. "Them" split gives you a guaranteed 2-way attribution if there are exactly 2 participants
- For multi-party calls, Granola falls back to "Speaker A", "Speaker B" — no named identification
- Post-meeting, the AI summary engine (GPT-4o) can sometimes infer names from transcript content

### Pros
- No enrollment needed — calendar data provides the roster
- Works with any audio capture method (including ScreenCaptureKit)
- "Me" vs. "Them" split is trivially accurate for 2-person calls
- Zero latency for the roster itself; real-time for the Me/Them split

### Cons
- Calendar attendees != actual participants (people join late, skip, or call in from unexpected numbers)
- No way to map Speaker A/B/C to specific attendees in multi-party calls without additional signals
- First-speaker heuristic is fragile — the host doesn't always speak first
- "Me" vs. "Them" only works for 2-person calls

### Latency
- Roster available pre-meeting. Speaker mapping: depends on heuristic (first-speaker is immediate; user confirmation adds seconds to minutes).

### Privacy implications
- Minimal — calendar data already accessible to the user
- No voice data leaves device in Granola's architecture

### Enrollment required
- None. Calendar integration only.

### Relevance to Persuasion Dojo
- **Highly applicable.** Persuasion Dojo already has `calendar_service.py` and `pre_seeding.py`. The calendar roster gives you the *who* — you just need to solve the mapping. The mic/system audio split gives you "user" vs. "everyone else" for free. This is your baseline.

---

## Approach 3: Voice Fingerprint Enrollment (Voiceprint)

**Used by:** Otter.ai, Jamie AI, Amazon Connect Voice ID, Picovoice Eagle

### How it works
During an explicit enrollment phase, the user records a short voice sample. The system extracts a voice embedding (a mathematical fingerprint of the voice's unique characteristics — pitch, timbre, cadence, formant structure). This embedding is stored as a "voiceprint." During meetings, the system compares incoming audio segments against all enrolled voiceprints to identify who is speaking.

### Technical details

**Otter.ai:**
- Users teach Otter their voice by reading a short script
- System learns from tagged paragraphs — tagging a speaker in one transcript improves future identification
- Voice embeddings are stored server-side
- 89-95% accuracy, improving over time as more samples accumulate
- Workspace-wide: shared speakers across an organization

**Jamie AI:**
- First meeting: speakers labeled "Speaker 1", "Speaker 2" with audio clips for manual labeling
- After labeling, Jamie remembers the voice for all future meetings
- Audio is deleted after summary generation — voiceprint persists
- EU-hosted servers, audio processed then deleted

**Picovoice Eagle:**
- Fully on-device — no voice data leaves the user's machine
- Enrollment from "a few seconds" of natural speech — no specific phrases required
- Produces an Eagle Profile object (voiceprint) stored locally
- Real-time streaming recognition: compares incoming audio frames against enrolled profiles
- Cross-platform (macOS, iOS, Android, web), language-agnostic, text-independent
- GDPR & HIPAA compliant by design
- 96%+ accuracy with 3 seconds of speech

**Amazon Connect Voice ID (retiring May 2026):**
- 30 seconds of net speech for enrollment
- 10 seconds for verification
- Authentication score 0-100 (default threshold: 90)
- Text-independent — no passphrases needed
- Designed for call center authentication, not meeting transcription

### Pros
- High accuracy (89-96%+) once enrolled
- Works with mixed audio streams — no separate streams needed
- On-device options available (Picovoice Eagle) — excellent privacy
- Improves over time with more samples
- Language and accent agnostic
- Can identify speakers from the first utterance (no warm-up needed)

### Cons
- **Cold start problem:** requires enrollment before first use — every participant must have been seen before
- External participants (clients, prospects) won't be enrolled unless you capture their voice in a prior meeting
- Voice characteristics can change (illness, aging, emotional state)
- Voiceprint storage is biometric data — significant regulatory implications (GDPR Article 9, BIPA in Illinois)
- Accuracy degrades with low-quality audio, background noise, or very short utterances

### Latency
- Picovoice Eagle: real-time (~10ms per frame)
- Otter.ai: near-real-time (within the transcription pipeline)
- Jamie: post-meeting (processes after recording stops, ~1-2 minutes)

### Privacy implications
- **Critical concern.** Voice embeddings are biometric data — as unique as fingerprints. Under GDPR, biometric data for identification is "special category" data requiring explicit consent and strict safeguards.
- On-device processing (Picovoice) mitigates cloud storage concerns but the voiceprint itself is still biometric
- Otter.ai stores voiceprints server-side — disclosed in privacy policy
- Jamie deletes audio but retains voiceprint — a meaningful privacy distinction

### Enrollment required
- **Yes.** This is the fundamental limitation. Ranges from 3 seconds (Picovoice) to 30 seconds (Amazon) of speech.

### Relevance to Persuasion Dojo
- **Very applicable for the user's own voice** — you can enroll the Persuasion Dojo user during onboarding (read a sentence or two). This gives you reliable "that's the user speaking" detection, which is critical for coaching prompts ("you've been talking for 4 minutes — ask a question").
- **Less applicable for other participants** — in high-stakes exec meetings, you can't ask the CEO you're pitching to enroll their voice beforehand. However, over time, repeat participants (team members, recurring stakeholders) can be passively enrolled.
- Picovoice Eagle is the strongest candidate: on-device, fast enrollment, real-time, privacy-compliant.

---

## Approach 4: LLM-Based Contextual Inference

**Used by:** AssemblyAI (Speech Understanding API), research prototypes, Granola's post-meeting AI

### How it works
After (or during) transcription, pass the diarized transcript to an LLM along with contextual signals to infer who is speaking. The LLM looks for self-identification ("Hi, I'm Sarah from marketing"), role indicators ("As CEO, I think..."), how speakers address each other ("Great point, Mike"), topic expertise patterns, and conversational dynamics (who asks questions vs. who answers).

### Technical details

**AssemblyAI Speech Understanding API:**
- Two-step process: first diarize to speaker clusters, then use LLM to map clusters to names
- `known_values` parameter: pass expected names/roles, model assigns them to clusters
- `speaker_type` can be "name" (specific people) or "role" (Agent, Customer)
- Extra names in `known_values` are silently ignored — handles uncertainty gracefully
- `speakers` parameter allows additional metadata per speaker for better inference
- Output replaces generic labels ("A", "B") with identified names in utterances and words

**LLM Zero-Shot Inference (research findings):**
- Recent research (2025) shows text-only LLMs (Qwen2.5-7B, ChatGPT-4.5) perform **poorly** at zero-shot speaker identification correction
- Primary failure mode: **hallucination** — LLMs alter utterance content while trying to fix speaker labels
- Fine-tuned LLMs (PaLM 2-S) perform better but require training data
- Best results: combine LLM predictions with acoustic information (beam search decoding)

**Practical signals an LLM can use:**
- Self-identification: "This is David from legal"
- Direct address: "Sarah, what do you think?"
- Role indicators: "From a finance perspective..." (maps to CFO)
- Topic continuity: same speaker tends to own a topic thread
- Meeting structure: first speaker is often the organizer
- Question patterns: facilitators ask more questions

### Pros
- Works without any enrollment — fully cold-start capable
- Can leverage calendar roster as `known_values` to constrain the problem
- Handles participants never seen before
- Can identify speakers even mid-meeting
- Graceful degradation — partial identification is still useful

### Cons
- **Unreliable in practice** — significant hallucination risk, especially zero-shot
- Accuracy drops when speakers don't self-identify or address each other by name
- Many business meetings are contextually ambiguous ("I agree" — could be anyone)
- Latency cost of LLM inference (100ms-2s depending on model)
- Requires transcript context to accumulate before inference is useful (first few minutes are blind)

### Latency
- Real-time capable with streaming LLMs, but accuracy improves with more context
- AssemblyAI: post-recording API call
- Practical: best used as a 30-60 second delayed refinement that improves over the meeting duration

### Privacy implications
- Transcript text sent to LLM API (cloud) — same privacy posture as existing coaching engine
- No biometric data involved
- Speaker names inferred from conversation content — participants may not know they're being identified

### Enrollment required
- **No.** This is the key advantage. Works completely cold.

### Relevance to Persuasion Dojo
- **Highly applicable as a secondary signal.** You already send transcripts to Claude for coaching prompts. Adding a "who is this speaker?" inference step is low marginal cost. Combine with calendar roster (`known_values` equivalent) for constrained inference. Key risk: hallucination must be managed — confidence thresholds are essential.

---

## Approach 5: Microphone vs. System Audio Split

**Used by:** Granola, Jamie, Bluedot, most bot-free local-capture tools

### How it works
On macOS/Windows, capture two separate audio streams: (1) the microphone input (the user's own voice) and (2) the system audio output (everyone else on the call). This gives you a guaranteed binary split: "me" vs. "not me." For the user's own voice, attribution is trivially correct. For everyone else, you still need diarization within the system audio stream.

### Technical details
- macOS: ScreenCaptureKit can capture application audio (system audio) separately from the microphone
- The user's microphone input is available via AVAudioEngine or Core Audio
- Two parallel streams → two parallel transcriptions → merge with timestamp alignment
- Diarization is only needed on the system audio stream (the "not me" speakers)
- Overlap between mic and system audio (echo) requires echo cancellation or the mic input is ignored when system audio is active

### Pros
- **Trivially identifies the user** — the most important speaker for coaching
- No enrollment needed for the user
- Reduces the diarization problem from N speakers to N-1
- Works with any meeting platform
- Real-time capable
- No privacy concerns beyond existing audio capture

### Cons
- Only solves "me" vs. "everyone else" — doesn't identify other participants
- Still need diarization + identification for the remaining speakers
- Echo cancellation adds complexity
- If the user uses a conference room speaker, the mic captures everyone (split breaks down)
- Some meeting apps route both directions through system audio (platform-dependent)

### Latency
- Real-time (< 50ms for stream splitting)

### Privacy implications
- Identical to current ScreenCaptureKit capture — no additional data collection

### Enrollment required
- **No.**

### Relevance to Persuasion Dojo
- **Immediately applicable and should be implemented.** This is the highest-value, lowest-cost improvement. Persuasion Dojo already uses ScreenCaptureKit for system audio — adding a parallel microphone stream gives you "user is speaking" vs. "someone else is speaking" with zero false positives. This directly enables the most important coaching prompts: "You've been talking for 4 minutes" and "You're mid-utterance — suppressing prompt."

---

## Approach 6: End-to-End Neural Models (SpeakerLM)

**Used by:** Research only (as of March 2026)

### How it works
A single multimodal LLM processes raw audio and jointly performs speech recognition, speaker diarization, and speaker identification in one pass. The model learns to predict "who spoke when and what" without a traditional pipeline of separate ASR → diarization → identification steps.

### Technical details (SpeakerLM, August 2025)
- Architecture: SenseVoice-large audio encoder → Transformer projector → Qwen2.5-7B-Instruct language model
- Audio encoder extracts acoustic features; projector aligns audio embeddings with text embedding space
- Flexible speaker registration: can optionally accept enrolled voiceprints or work zero-shot
- Outperforms cascaded baselines on public benchmarks
- Eliminates error propagation between pipeline stages
- Handles overlapping speech natively

### Pros
- State-of-the-art accuracy on benchmarks
- Single model eliminates pipeline complexity
- Handles overlapping speech better than cascaded approaches
- Can work with or without prior speaker enrollment

### Cons
- **Not production-ready** — research paper published August 2025
- Requires significant compute (7B parameter model)
- Not available as an API or SDK
- Real-time streaming not demonstrated
- Training requires large-scale annotated data

### Latency
- Unknown for real-time use — likely too slow for streaming with 7B parameters
- Better suited for near-real-time or post-meeting processing

### Privacy implications
- Could theoretically run on-device with sufficient hardware
- Currently research-only — no production privacy considerations yet

### Enrollment required
- **Optional** — supports both enrolled and zero-shot modes

### Relevance to Persuasion Dojo
- **Future consideration only.** Monitor this space — within 12-18 months, production-ready end-to-end models may emerge. Not actionable for current MVP.

---

## Approach 7: On-Device Speaker Diarization (Picovoice Falcon, FluidAudio)

**Used by:** Meetily (open-source), Picovoice Falcon, FluidAudio (Core ML)

### How it works
Run speaker diarization locally on the user's device using optimized models. This separates voices in a mixed audio stream and assigns speaker labels (speaker_0, speaker_1) — but does not identify who each speaker is. The identification step still requires one of the other approaches above.

### Technical details

**Picovoice Falcon:**
- On-device speaker diarization — no cloud required
- Runs on macOS, iOS, Android, web
- Can be combined with Eagle (speaker recognition) for full identification
- Outputs speaker segments with timestamps

**FluidAudio:**
- Core ML models for macOS/iOS
- Open-source, optimized for Apple silicon
- Provides speaker diarization, ASR, and voice activity detection
- Designed to integrate with ScreenCaptureKit

### Pros
- Fully offline — maximum privacy
- No API costs
- Low latency (on-device processing)
- Works with any audio source

### Cons
- Diarization only — doesn't tell you *who* each speaker is
- Still need a separate identification approach
- Accuracy may be lower than cloud-based alternatives
- Requires device compute resources

### Latency
- Real-time capable on modern hardware

### Privacy implications
- Excellent — all processing stays on device

### Enrollment required
- **No** for diarization. Yes for identification (if combined with Eagle).

### Relevance to Persuasion Dojo
- **Applicable as a Deepgram supplement or replacement.** If Deepgram diarization accuracy on ScreenCaptureKit-captured mixed audio is insufficient (the hard gate in CLAUDE.md), on-device alternatives like Falcon or FluidAudio could be fallbacks. FluidAudio's Core ML integration is particularly interesting for a macOS-first product.

---

## Recommended Architecture for Persuasion Dojo

Given the ScreenCaptureKit capture method (mixed audio, no meeting bot), here is the recommended layered approach, ordered by implementation priority:

### Layer 1: Mic/System Audio Split (Implement First)
- Capture microphone input separately from ScreenCaptureKit system audio
- This gives you "user is speaking" vs. "other participants are speaking" with 100% accuracy
- Enables the most critical coaching prompts immediately
- **Cost:** Low — modify `audio.py` to capture two streams
- **Accuracy:** 100% for user identification

### Layer 2: Calendar Roster Pre-Seeding (Implement Second)
- Pull attendee list from `calendar_service.py` before meeting starts
- Know the N possible speakers before audio starts
- Constrain the identification problem from "who in the world?" to "which of these 4-8 people?"
- **Cost:** Low — already have `calendar_service.py` and `pre_seeding.py`
- **Accuracy:** Roster accuracy depends on calendar data quality

### Layer 3: Voiceprint Enrollment for User (Implement Third)
- During onboarding, record the user reading 2-3 sentences
- Use Picovoice Eagle (on-device, privacy-safe) to create a voiceprint
- Provides a secondary signal for "user is speaking" beyond mic/system split
- Also enables identification when user is in a conference room (mic split fails)
- **Cost:** Medium — integrate Picovoice Eagle SDK
- **Accuracy:** 96%+ with 3 seconds of enrollment speech

### Layer 4: LLM Contextual Inference (Implement Fourth)
- During the meeting, pass accumulated transcript + calendar roster to Claude
- Ask: "Given these attendees [names from calendar], assign speaker labels to names based on context"
- Use self-identification, direct address, role indicators as signals
- Run every 60 seconds with growing context window
- Apply confidence thresholds — only assign a name when confidence is high
- **Cost:** Low marginal — already calling Claude for coaching prompts
- **Accuracy:** Variable, improves over meeting duration. Best for 3+ person calls.

### Layer 5: Passive Voiceprint Learning (Implement Later)
- After a meeting where speakers are identified (via LLM or user confirmation), save voiceprints for identified speakers
- Next meeting with same participants, recognize them from voice
- Jamie AI's approach: first meeting requires labeling, subsequent meetings are automatic
- **Cost:** Medium — requires voiceprint storage and matching infrastructure
- **Accuracy:** 89-95%, improving with more samples per speaker

### Not Recommended for V1
- **Meeting bot approach:** Conflicts with Persuasion Dojo's privacy-first, no-bot architecture
- **End-to-end neural models (SpeakerLM):** Not production-ready
- **Amazon Connect Voice ID:** Being retired May 2026

---

## Key Insight: The Problem is Easier Than It Looks

For Persuasion Dojo's specific use case, you don't need to solve the general speaker identification problem. You need to answer three specific questions:

1. **"Is the user speaking right now?"** — Solved by mic/system audio split (Layer 1). This enables 80% of coaching prompts.

2. **"Who is this other speaker?"** — Partially solved by calendar roster (Layer 2) + LLM inference (Layer 4). Enables audience-layer prompts ("Sarah is an Inquisitor — she needs data").

3. **"Is this the same person who spoke 2 minutes ago?"** — Solved by Deepgram diarization (already implemented). Speaker consistency within a session is easier than cross-session identification.

The hardest problem — "map speaker_0 to a real name in a multi-party call with strangers" — is also the least important for V1. The user almost always knows who's on the call. A simple UI confirmation ("Is Speaker 2 Sarah Chen?") combined with LLM inference handles the long tail.

---

## Sources

- [Granola Transcription Docs](https://docs.granola.ai/help-center/taking-notes/transcription)
- [Granola Review - tl;dv](https://tldv.io/blog/granola-review/)
- [Google Meet Transcription Help](https://support.google.com/meet/answer/12849897?hl=en)
- [Deepgram Diarization Docs](https://developers.deepgram.com/docs/diarization)
- [Deepgram GitHub Discussion #475](https://github.com/orgs/deepgram/discussions/475)
- [Deepgram Next-Gen Diarization](https://deepgram.com/learn/nextgen-speaker-diarization-and-language-detection-models)
- [Otter.ai Speaker Identification Overview](https://help.otter.ai/hc/en-us/articles/21665587209367-Speaker-Identification-Overview)
- [Otter.ai Best Practices for Speaker ID](https://help.otter.ai/hc/en-us/articles/37817241040535-Best-Practices-to-Maximize-Speaker-Identification)
- [Otter.ai Speaker Tagging](https://help.otter.ai/hc/en-us/articles/360048465453-Tagging-speaker-names-in-a-conversation)
- [Recall.ai Perfect Diarization Docs](https://docs.recall.ai/docs/perfect-diarization)
- [Recall.ai Speaker Labels Blog](https://www.recall.ai/blog/speaker-labels-and-speaker-diarization-explained-how-to-obtain-and-use-them-for-accurate-transcription)
- [Recall.ai Separate Audio Per Participant](https://docs.recall.ai/docs/how-to-get-separate-audio-per-participant-realtime)
- [AssemblyAI Speaker Identification Docs](https://www.assemblyai.com/docs/speech-understanding/speaker-identification)
- [AssemblyAI Speaker ID Blog](https://www.assemblyai.com/blog/assemblyai-speaker-identification-diarization)
- [Picovoice Eagle SDK](https://picovoice.ai/platform/eagle/)
- [Picovoice Eagle Docs](https://picovoice.ai/docs/eagle/)
- [Picovoice State of Speaker Recognition 2026](https://picovoice.ai/blog/state-of-speaker-recognition/)
- [Amazon Connect Voice ID](https://docs.aws.amazon.com/connect/latest/adminguide/voice-id.html)
- [Jamie AI Speaker Identification Docs](https://docs.meetjamie.ai/pages/getting_started/identify_speaker)
- [Jamie AI Help Center - Speaker ID](https://intercom.help/meetjamie/en/articles/7913795-how-to-identify-speakers-in-jamie)
- [SpeakerLM Paper (arXiv:2508.06372)](https://arxiv.org/abs/2508.06372)
- [LLM-based Speaker Diarization Correction (ScienceDirect)](https://www.sciencedirect.com/science/article/abs/pii/S0167639325000391)
- [Speaker Diarization Privacy Risks - Basil AI](https://basilai.app/articles/2026-03-15-speaker-diarization-privacy-risks-who-gets-identified-in-cloud-transcription.html)
- [FluidAudio (Core ML)](https://github.com/FluidInference/FluidAudio)
- [Picovoice Falcon (On-Device Diarization)](https://github.com/Picovoice/falcon)
- [Zoom Real-Time Media Streams](https://www.zoom.com/en/realtime-media-streams/)
- [Nylas Notetaker API](https://www.nylas.com/products/notetaker-api/speaker-diarization-api/)
- [Shadow.do - Bot-Free Notetakers](https://www.shadow.do/blog/best-ai-meeting-note-takers-that-dont-join-as-a-bot-2026)
- [Meetily (Open Source)](https://github.com/Zackriya-Solutions/meeting-minutes)
