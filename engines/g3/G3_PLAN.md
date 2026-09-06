# G3 Lite closure plan

G3 is now a narrow, draft-only delivery boundary. It does not render media, write copy, schedule posts, publish posts, or host a web application.

## Architecture

1. G2 emits a checksummed `company-core.g3-handoff.v1` manifest.
2. G3 verifies the manifest, local path confinement, MIME type, and every media SHA-256.
3. G3 uploads immutable media objects to Cloudflare R2.
4. G3 proves the exact public URL is anonymously readable and returns the expected media bytes and content type.
5. G3 creates an Instagram carousel draft and/or X thread draft through Buffer GraphQL.
6. G3 requires Buffer to return `status: draft` before writing its SQLite idempotency ledger.
7. The founder reviews and publishes manually in Buffer.

## Closure gates

| Gate | Evidence |
| --- | --- |
| Local footprint | Python virtual environment plus SQLite; no Docker services |
| Buffer connection | `company-core-g3 doctor --require instagram --require x` |
| Media hosting | R2 credentials validate and each permanent HTTPS URL passes an anonymous range request |
| Safety | Manifest rejects publication fields; API boundary hard-codes and checks `saveToDraft: true` |
| Instagram | One multi-image Buffer draft appears in the Instagram channel |
| X | One Buffer X thread draft appears with ordered parts |
| Idempotency | Re-running the same handoff returns `duplicate_skipped` |
| Human control | Scheduling and publishing exist only in Buffer's UI |
