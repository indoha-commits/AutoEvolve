# G2.4 Media Intelligence Contract

G2.4 is a retrieval and comparison system, not a template-video generator.
Its unit of work is a timed scene requirement derived from the grounded G1
campaign and the platform-specific storyboard.

## Retrieval order

1. Founder-approved local catalog.
2. Pexels video and image search.
3. Pixabay video and image search.
4. Coverr video search, with its quota-bearing signed URL resolved only after selection.
5. Wikimedia Commons video and image search with file-level license metadata.
6. Lordicon free vector search for the control-system scene only when an API
   token is configured.

All calls execute concurrently under one global deadline. A failed or slow
provider is recorded and does not erase successful candidates from other
sources.

## Medium routing

| Scene | Preferred | Permitted fallback |
|---|---|---|
| Cover through failure point | Short video | One relevant still image |
| Control system | Configured owned showcase image or video | None |
| CTA | Approved premade outro | None |

G2 selects one primary medium per scene. It does not layer unrelated assets.
The renderer uses the configured showcase for the control-system scene and does
not execute third-party Lottie JSON.

## Deterministic comparison

Before scoring, a candidate must match at least one scene-specific literal
visual anchor. For example, `office` and `desk` cannot make a coffee clip valid
for a document-copy scene. Only candidates that pass this gate receive scores
for metadata overlap, preferred medium, native orientation, delivery resolution,
clip duration coverage, explicit avoid-term conflicts and premium/free status.
The model is not asked to invent a score. One candidate ID cannot silently carry
multiple scenes.

## Security and approval

- Download URLs must remain HTTPS and inside provider-specific host allowlists.
- Coverr search results retain a provider API URL; the short-lived storage URL is requested only for the selected asset.
- Downloads have media-specific byte limits.
- Images are decoded, videos are inspected with FFprobe, JSON is parsed and SVG
  signatures are checked.
- Every acquired candidate begins unapproved.
- Production rendering retains checksum and Founder-approval gates.
- Publishing remains disabled.
