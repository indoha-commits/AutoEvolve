# Media Security Policy

- Only `ready_for_media` packages with no warnings, unsupported claims, or forbidden terms are accepted.
- Inputs are capped; JSON and images are decoded and validated before use.
- Asset paths are resolved beneath one approved directory; traversal is rejected.
- Every stock or owned candidate requires `approved: true` before a production render.
- Automatic generated-image fallback is disabled.
- Product screenshots must use `source_type: owned`; approved illustrations use
  `source_type: illustration` and are never represented as screenshots.
- The built-in scene-5 fallback is a fixed operational illustration, not generated UI.
- Stock downloads use HTTPS provider allowlists, content-type checks, redirect revalidation, byte limits and image decoding.
- OmniRoute image output must be base64 image data; provider-returned arbitrary URLs are rejected.
- Style-reference hashes are verified and near-duplicate reference reuse is blocked.
- Provider keys remain environment variables and never enter prompts or manifests.
- Output includes source and file SHA-256 values for review and idempotency.
- Proof placeholders carry `proof_only: true` and `publish_allowed: false`.
- No publish, arbitrary shell, arbitrary URL, social upload, or delete capability exists in G2.1.
- External asset metadata must record provider, source URL, and license.
