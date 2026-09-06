# G1 → G2 Media Contract

Accepted only when `status=ready_for_media`, `schema_valid=true`, `all_claims_grounded=true`, `editorial_score>=0.85`, and all warning/forbidden/unsupported lists are empty.

G2 treats slide headline, body, purpose, claim IDs, asset strategy, and platform copy as immutable. It may crop, grade, composite, animate, caption, and lay out assets. It may not invent claims, rewrite the CTA, fabricate product UI, or change the offer. If scene 5 has no owned product media, its fixed control-layer illustration is used and recorded as an illustration.

Production asset records contain: `slide_number`, relative `local_path`, optional `source_url`, `provider`, `license`, and `approved`. Owned product UI and supplied illustrations require approval. Provider assets require provenance and license metadata.
