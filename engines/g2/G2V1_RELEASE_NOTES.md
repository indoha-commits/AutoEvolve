# G2V1 release notes

## G2.3 / V0.7.3 — lossless render path

HD source imagery remains lossless through layer preparation, scene rendering
and concatenation. Burned subtitles and platform compatibility are applied in
one final CRF 16 delivery encode. This removes the former JPEG plus three-H.264-
generation path that produced a 1080x1920 file with 360p-like effective detail.

## G2.3 / V0.7.2 — informational delivery and resumable Edge rendering

The delivery layer now varies pace, pitch, silence and target loudness by scene
purpose. The variation is selective: evidence and the control resolution carry
slightly more energy, while the failure point is slower and quieter. This avoids
both flat lecture delivery and the cognitive noise of constant enthusiasm.

Edge synthesis now retries transient provider failures with exponential backoff.
Each completed WAV/VTT pair is protected by a cache key containing the text,
voice, purpose and delivery profile, allowing interrupted voice bakeoffs to
resume safely without mixing stale narration or different voices.

## G2.3 / V0.7.1 — product-media illustration path

- Uses owned product media for scene 5 when available.
- Otherwise renders a fixed cargo-control illustration with no fake interface.
- Keeps scenes 1–4 strict: missing media still stops the render.
- Records the illustration decision in asset provenance.
- Passes 35 deterministic tests and a real FFmpeg smoke render.

## G2.3 / V0.7 — synchronized voice-first closure

- Uses provider timing instead of retranscribing generated speech.
- Applies subtle delivery intent by scene purpose.
- Makes exact generated audio duration control every visual boundary.
- Disables random, generated, repeated and colored-screen visual fallback.
- Replaces fractional image movement with stable static framing.
- Requires explicit approved product media for the control-system scene.
- Regenerates timestamps during final H.264/AAC assembly.
- Records voice, delivery, subtitle and synchronization provenance.
- Passes 32 deterministic tests.

## G2.2 / V0.6 — preset local voice closure

- Added distinct YouTube, Shorts, and TikTok editorial profiles.
- Added true 1920×1080 YouTube rendering alongside 1080×1920 vertical output.
- Removed the persistent logo from editorial scenes.
- Replaced generated end-card layers with the approved premade social outro.
- Added Faster-Whisper INT8 subtitle alignment and SRT sidecars.
- Removed reference-audio cloning and all speaker-sample requirements.
- Added a zero-model system/eSpeak NG fallback and retained Kokoro as the natural preset voice.
- Retained Kokoro as the lightweight fallback.
- Expanded the deterministic suite to 27 tests.

## V0.4 — voice-over-first simplification

- Removed editorial cards, large headline overlays, diagrams, slide numbers, and decorative routes.
- Made contextual imagery full-frame with restrained documentary movement.
- Reduced branding to one small white logo.
- Moved subtitles into the vertical-platform safe zone and removed caption boxes.
- Added a deterministic test that rejects large production overlays.

## Proven in this package

- 32 deterministic tests
- six-scene storyboard compilation
- 1920×1080 and 1080×1920, 30 fps H.264/AAC FFmpeg output
- stable full-frame approved media without synthetic fallback
- provider-timed subtitles burned into the final video
- exact audio-driven scene boundaries and regenerated timestamps
- campaign hash, asset checksums and source provenance
- review-only use of unapproved assets
- no publishing capability

## Optional production dependency

Edge TTS is the default production adapter because it produces timestamped speech
without installing a GPU stack. Install `.[network-voice]` and select
`--voice-mode edge`. Kokoro remains an isolated experimental adapter; system/eSpeak is
an emergency offline fallback. Faster Whisper is only needed for uploaded or external
recordings that arrive without provider timestamps.

## Deliberately deferred

- stock video clips
- music and sound-effect mixing
- regional map primitives
- optional word-level forced alignment for external recordings
- UI screenshot motion
- Postiz publishing
