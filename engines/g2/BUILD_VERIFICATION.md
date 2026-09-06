# G2.4.1 Build Verification

Verified on 2026-08-25:

- 54/54 offline unit tests pass.
- The complete `run_proofs.sh` flow passes.
- The grounded G1 package still validates against the same source SHA-256.
- Six storyboard scenes compile into six contiguous timed visual requirements.
- Cover through failure point prefer short video and permit one still fallback.
- The control-system scene searches resolution-independent vector media; the CTA
  remains the supplied premade outro.
- Search is federated across the approved local catalog, Pexels, Pixabay,
  Wikimedia Commons and optional Lordicon under one global deadline.
- Provider errors fail soft and are retained in the decision report.
- A required visual-anchor gate rejects candidates missing the scene's literal
  subject before any technical-quality bonuses are applied.
- Ranking is deterministic and checks literal metadata, medium, orientation,
  delivery resolution, duration coverage, avoid terms and premium status.
- The same candidate cannot be selected for multiple scenes.
- Downloads enforce HTTPS host allowlists, redirect checks and byte limits.
- Images are decoded, videos are FFprobed, Lottie JSON is parsed and SVG
  signatures are checked before a manifest record is created.
- Every acquired candidate begins `approved=false`.
- Only Founder-approved files can enter the reusable local media catalog.
- Selected video enters FFmpeg directly with a single aspect-ratio crop; it is
  not flattened into a still image.
- A real FFmpeg smoke test produced a 360x640, 30 fps, 0.8-second scene from a
  moving source clip with exact audio-bound duration.
- Still imagery remains lossless through scene assembly.
- The cover uses a conversational single-sentence hook and a neutral delivery
  profile; the scene cache version ensures stale robotic takes are not reused.
- Audio-only voice bakeoff supports a curated shortlist, explicit voices or all
  installed English Edge voices without duplicate video rendering.
- Every Edge scene call has a configurable hard timeout. Bakeoff reports are
  checkpointed after each voice, failures remain isolated, and interrupted runs
  reuse completed scene caches when restarted.
- Publishing remains disabled.

Live provider calls require their corresponding credentials and network access.
They do not weaken or block offline proof verification.
