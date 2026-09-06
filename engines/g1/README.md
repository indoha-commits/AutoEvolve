# G1 Campaign Engine

G1 turns a researched brief into a validated campaign package. It enforces approved product claims,
story order, CTA policy, image-search constraints, and a strict handoff schema for G2.

Install all engines from the repository root with `make setup`. Run this engine directly with:

```bash
engines/g1/.venv/bin/company-core-g1 doctor
engines/g1/.venv/bin/company-core-g1 create --help
```

Customize `knowledge/brand_profile.json`, `knowledge/product_claims.json`, the narrative registry,
and CTA registry before production use. Runtime directories are excluded from version control.
