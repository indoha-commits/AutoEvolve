# G2 Media Engine

G2 consumes a validated G1 campaign and handles media search, acquisition, approved owned assets,
voice generation, subtitles, carousel rendering, and mixed-video rendering.

Install from the repository root with `make setup`. FFmpeg is required for video workflows.

```bash
engines/g2/.venv/bin/company-core-g2 --help
```

Configure stock-media and optional generation providers in the root `.env`. Replace files under
`assets/references/` with licensed brand assets before publishing.
