from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import G1Campaign


class MediaGateError(ValueError):
    pass


def canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def package_sha256(campaign: G1Campaign) -> str:
    return hashlib.sha256(canonical_bytes(campaign.model_dump(mode="json"))).hexdigest()


def load_campaign(path: str | Path) -> G1Campaign:
    source = Path(path)
    if not source.is_file():
        raise MediaGateError(f"campaign file not found: {source}")
    if source.stat().st_size > 2_000_000:
        raise MediaGateError("campaign JSON exceeds 2 MB")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        campaign = G1Campaign.model_validate(data)
    except Exception as exc:
        raise MediaGateError(f"invalid G1 campaign: {exc}") from exc
    validate_campaign(campaign)
    return campaign


def validate_campaign(campaign: G1Campaign) -> None:
    failures = []
    if campaign.status != "ready_for_media":
        failures.append("status is not ready_for_media")
    if not campaign.quality.schema_valid:
        failures.append("G1 schema validation did not pass")
    if not campaign.quality.all_claims_grounded:
        failures.append("claims are not fully grounded")
    if campaign.quality.forbidden_terms:
        failures.append("forbidden terms are present")
    if campaign.quality.unsupported_claims:
        failures.append("unsupported claims are present")
    if campaign.quality.warnings:
        failures.append("G1 warnings remain")
    if campaign.quality.editorial_score < 0.85:
        failures.append("editorial score is below 0.85")
    numbers = [slide.number for slide in campaign.slides]
    if numbers != list(range(1, len(numbers) + 1)):
        failures.append("slide numbering is not contiguous")
    if campaign.slides[-2].asset_strategy != "product_ui":
        failures.append("penultimate slide is not approved product UI")
    if campaign.slides[-1].asset_strategy != "branded_end_card":
        failures.append("final slide is not a branded end card")
    if failures:
        raise MediaGateError("; ".join(failures))
