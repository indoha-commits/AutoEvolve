# Changelog

## v0.8.1 — relevance and voice comparison closure

- Added required scene-specific visual anchors before technical ranking bonuses.
- Added regression coverage rejecting coffee/desk footage for document evidence.
- Rewrote the opening as one conversational sentence and neutralized its delivery.
- Bumped the voice cache key so previous opening takes cannot be reused.
- Added audio-only voice bakeoff for curated, explicit, or all English Edge voices.
- Added bounded scene timeouts, incremental reports, clean interruption and
  cache-backed resume for long voice bakeoffs.

## v0.8.0 — timed media intelligence

- Added deadline-bound federated discovery across local, stock, open-license and
  optional vector sources.
- Added timed requirements, deterministic ranking and direct moving-video input.
- Added safe acquisition and reusable approved-media catalog indexing.

## v0.7.3 — lossless render path

- Replaced quality-91 JPEG scene layers with lossless PNG layers.
- Scene H.264 files are now lossless intermediates tuned for still imagery.
- Concatenation now uses stream copy and cannot introduce another lossy pass.
- Subtitle rendering performs the single delivery encode at CRF 16, slow preset,
  High profile, with fast-start metadata for upload playback.
- Corrected vertical SRT scaling so captions remain in the bottom safe zone
  instead of becoming oversized and moving toward the top of the frame.
- Added regression tests that reject lossy scene layers and concat re-encoding.

## v0.7.2 — informational voice delivery

- Added a research-informed scene energy arc for informational short video.
- Preserved controlled scene-to-scene loudness contrast instead of flattening
  every narration segment to the same target.
- Added transient Edge TTS retries with exponential backoff.
- Added keyed per-scene audio and timing caches so interrupted renders resume
  without regenerating completed voice work.
- Added cache and target-loudness provenance to the render manifest.

## 0.7.1 — Control illustration fallback

- Prioritized owned product media for the control-system scene.
- Added a deterministic control-layer illustration when product media is absent.
- Kept strict missing-media failure for scenes 1–4.
- Recorded illustration use explicitly in asset provenance.
- Allowed approved supplied illustrations without treating them as product UI.
- Expanded the deterministic suite to 35 tests.

## 0.7.0 — Synchronized voice-first closure

- Added Edge neural voice adapter with native word-boundary VTT.
- Added six restrained, purpose-specific delivery profiles.
- Made generated scene audio the authority for visual duration.
- Added provider-timed SRT assembly without Whisper.
- Removed automatic generated-image fallback.
- Made missing non-outro media a hard render error.
- Removed fractional image movement and switched to stable static holds.
- Regenerated timestamps and re-encoded scene concatenation.
- Added voice, delivery, media, and synchronization provenance.
- Expanded the deterministic suite to 32 tests.

## 0.3.0 — G2V1 Mixed-Media Video

- Added six-scene storyboard compilation.
- Added contextual stock backgrounds with deterministic kinetic typography.
- Added modular document, role-route, status, control-layer and CTA graphics.
- Added Kokoro narration adapter and silent proof mode.
- Added ASS caption generation and FFmpeg composition.
- Added review-only rendering for unapproved candidates.
- Added bundled Pexels proof assets with provenance.

## 0.2.1 — G2.1 Asset Intelligence

- Added six immutable Example Company style references and integrity registry.
- Added stock-first Pexels and Pixabay discovery.
- Added OmniRoute `/images/generations` background-plate fallback.
- Added prompt policy preventing generated typography, logos, UI, watermarks and neon treatment.
- Added deterministic candidate ranking, image quality checks, SHA-256 and perceptual hashes.
- Added reference, campaign and accepted-memory duplicate protection.
- Added HTTPS provider allowlists and safe download validation.
- Added asset plans, resolution reports, provenance manifests and live-skippable provider proofs.
- Required founder approval for every production asset and owned provenance for product UI.
- Fixed repeated-logo alpha reuse and added logo-presence assertions for all slides.
- Isolated FFmpeg proof input from numbered carousel filenames.
# 0.6.0

- Removed reference-audio voice cloning.
- Added `system` and `kokoro` preset-only voice modes.
- Bound narration generation to approved script text with no speaker identity input.
