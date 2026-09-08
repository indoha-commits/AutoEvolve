# G2V1 Video Architecture

## Authority split

- G1 owns claims, narrative, headline, body copy and CTA.
- The storyboard compiler selects visual grammar and delivery intent.
- Stock providers supply contextual backgrounds, never factual evidence by themselves.
- The deterministic layer owns platform selection, the subtitle-safe area and outro fitting.
- The selected voice provider owns waveform generation and optional timing boundaries.
- Measured audio duration owns scene boundaries.
- Faster-Whisper is reserved for uploaded speech with unknown timing.
- The premade outro asset owns all visible branding; editorial scenes have no logo overlay.
- FFmpeg owns deterministic push/pan movement, scene cuts, composition and encoding.
- G2 cannot publish.

## Review and production modes

Review mode may use unapproved, checksum-valid stock assets, but it does not alter the
visible frame with a badge or logo. The output filename and manifest identify review
renders. Production mode denies any unapproved or modified stock asset. Both modes
remain non-publishing outputs.

## Edit grammar

- Source videos retain their native movement after deterministic crop and scaling.
- Still images receive a shallow push or horizontal pan capped near 6% zoom.
- Scene changes alternate clean hard cuts and 140 ms visual fades.
- The configured CTA/outro remains locked off so its designed branding is not distorted.

## Extension contract

New visual primitives must preserve the voice-over-first hierarchy. Stock video,
approved product UI captures, music ducking and sound effects remain extensions.
Diagrams, editorial cards, random media substitution and generated fallback screens
stay out of the default renderer.

## Security

- Asset paths are resolved beneath the configured root.
- Input checksums are verified before rendering.
- No shell text is built from campaign copy.
- Subprocess calls use argument arrays.
- Generated manifests retain campaign hash and source provenance.
- Voice providers receive narration text only.
- Delivery metadata uses bounded pacing parameters and cannot select an accent.
- Missing visual media fails closed for scenes 1–4. Scene 5 alone may use the fixed
  deterministic control-layer illustration when owned product media is absent.
- Reference audio, voice cloning, and speaker impersonation are outside the G2 contract.
