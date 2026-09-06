# G2.3 Architecture

## Contract

`G1 ready_for_media JSON → media gate → explicit media resolution → founder asset review → audio-first timing → stable renderer → review artifact`

The renderer never rewrites copy or claims. A rejected G1 package returns to G1.
Scene 5 prioritizes explicitly approved owned product media. When none is supplied,
the renderer uses its fixed control-layer illustration without pretending it is a
product interface. The final end card uses the premade approved asset. Missing media
for scenes 1–4 remains unresolved and can never trigger a generated or colored-screen
fallback.

## Media tracks

| Track | G2.0 output | Later production component |
|---|---|---|
| Carousel | Six 1080×1350 PNGs and checksummed manifest | Postiz handoff in G3 |
| Short video | 1080×1920 scene manifest, 35–55 seconds | FFmpeg assembly, TTS timestamps, captions |

The same G1 narrative and claim IDs feed both tracks; visual treatment changes, facts do not.

## Roles

| Component | Reads | Writes | External mutation |
|---|---|---|---|
| Media gate | G1 package | validation result | none |
| Asset resolver | approved asset directory/provider | asset manifest | downloads only when configured |
| Renderer | package + manifest | output directory | none |
| Publisher | review-approved artifact | provider post ID | intentionally absent until G3 |
