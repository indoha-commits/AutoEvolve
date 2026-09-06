# G2.1 Asset Intelligence

## Resolution order

1. Search Pexels for realistic documentary photography.
2. Search Pixabay when Pexels is unavailable or weak.
3. Leave the scene unresolved when neither provider returns suitable media.
4. Use the owned image or video supplied through `--showcase` for `product_ui`.
5. Use the approved premade outro for the final scene.

The six files in `assets/references/` define visual language only. Registry hashes protect their identity and `reuse_as_output=false` blocks them from becoming production photography.

## Hard media boundary

Automatic image generation is disabled. A failed stock search cannot become a
generated plate, repeated prior image, colored screen, or synthetic gradient. The
founder must explicitly approve another stock candidate or provide owned media.

G2 never generates product screenshots. The control-system scene always uses the
configured owned showcase. Editorial scenes do not receive headlines, cards,
decorative routes, or a persistent logo; imagery supports the narration and subtitles.

## Candidate state

Provider output is a candidate, not a publishable asset. Each record retains provider,
candidate ID, source URL, license, SHA-256, perceptual hash, and dimensions. New
candidates are initially `approved=false` and cannot enter a production render until
founder approval.

## Environment

```bash
export PEXELS_API_KEY=...
export PIXABAY_API_KEY=...
```

No image-model credentials are required by the automatic production path.
