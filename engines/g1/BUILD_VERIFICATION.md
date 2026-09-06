# Build Verification

Verified in a clean temporary installation:

- Python compilation: PASS
- Installer and database initialization: PASS
- Twenty-five offline tests: PASS
- Full fake-provider Researcher → Concept ranking → Writer → Validator → artifact pipeline: PASS
- Forbidden customer and obsolete-offer blocking: PASS
- Live generic-copy regression fixture: BLOCKED as expected
- One bounded editorial rewrite: PASS
- Failed rewrite preserves a founder-review package: PASS
- Five-slide deterministic story normalization: PASS
- Collapsed concept set repair and diversity check: PASS
- Positive memory vs rejected-draft lesson separation: PASS
- Concept eligibility before novelty: PASS
- Domain-diverse research selection: PASS
- Actual routed-model persistence: PASS
- Exact reference copy excluded from generation context: PASS
- PDF routing bypasses browser crawling: PASS
- Research-model outage degrades without external claims: PASS
- Full generation-provider outage returns an audited review draft: PASS
- Live G1.5 real-time/caption/causal regression fixture closes to media-ready: PASS
- Extensionless PDF detection bypasses browser crawling: PASS
- Research-model retries are bounded to one attempt before fail-soft continuation: PASS
- Clean installer and in-place upgrader use a target-local pip cache: PASS
- Seven-slide model output is reduced to the canonical six-slide story: PASS
- Provider fallback provenance is distinct from schema-invalid output: PASS
- M0 bridge fails closed when unconfigured: PASS

Target-machine prerequisites already demonstrated by the Founder:

- SearXNG JSON search: PASS
- Crawl4AI extraction against Example Company and WTO: PASS
- OmniRoute JSON output: PASS
- M0 draft allowed and unauthorized publish denied: PASS

The observed intermittent OmniRoute timeout is handled in production with two timeout windows, exponential backoff, attempt logging, and a configurable fallback model.
