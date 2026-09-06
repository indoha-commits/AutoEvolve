from __future__ import annotations

import json
from pathlib import Path

from .fingerprint import file_sha256, image_quality, perceptual_hash
from .models import AssetCandidate, AssetPlan, AssetPlanItem, AssetRecord, G1Campaign
from .prompts import prompt_sha256


def build_asset_plan(campaign: G1Campaign, reference_registry: str = "assets/references/reference_registry.json") -> AssetPlan:
    items = []
    for slide in campaign.slides:
        if slide.asset_strategy == "product_ui":
            items.append(AssetPlanItem(
                slide_number=slide.number,
                asset_strategy=slide.asset_strategy,
                query=None,
                providers=["owned"],
                generation_prompt=None,
                requires_owned_asset=False,
                fallback="deterministic_control_illustration",
            ))
        elif slide.asset_strategy == "branded_end_card":
            items.append(AssetPlanItem(slide_number=slide.number, asset_strategy=slide.asset_strategy, query=None, providers=["deterministic_renderer"], generation_prompt=None, deterministic=True))
        else:
            items.append(AssetPlanItem(
                slide_number=slide.number,
                asset_strategy=slide.asset_strategy,
                query=(slide.image_queries or [slide.visual_concept])[0],
                providers=["pexels", "pixabay"],
                generation_prompt=None,
            ))
    return AssetPlan(campaign_id=campaign.campaign_id, reference_registry=reference_registry, items=items)


def score_candidate(candidate: AssetCandidate, query: str) -> AssetCandidate:
    score, reasons = 0.0, []
    ratio = candidate.height / candidate.width
    if ratio >= 1.15:
        score += 0.30
        reasons.append("portrait composition")
    if candidate.width >= 1000 and candidate.height >= 1200:
        score += 0.25
        reasons.append("production resolution")
    query_terms = {term for term in query.lower().split() if len(term) >= 4}
    description_terms = set(candidate.description.lower().replace(",", " ").split())
    overlap = len(query_terms & description_terms) / max(len(query_terms), 1)
    score += min(overlap * 0.35, 0.35)
    if overlap:
        reasons.append(f"query overlap {overlap:.2f}")
    if candidate.provider == "pexels":
        score += 0.10
        reasons.append("preferred documentary provider")
    return candidate.model_copy(update={"score": round(min(score, 1.0), 4), "score_reasons": reasons})


def rank_candidates(candidates: list[AssetCandidate], query: str) -> list[AssetCandidate]:
    return sorted((score_candidate(item, query) for item in candidates), key=lambda item: (-item.score, item.candidate_id))


def validate_reference_registry(root: str | Path, registry_path: str | Path) -> dict:
    root = Path(root).resolve()
    registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    if registry.get("reuse_as_output") is not False:
        raise ValueError("style references must be blocked from production reuse")
    checked = []
    for item in registry["references"]:
        path = (root / item["path"]).resolve()
        if root not in path.parents:
            raise ValueError("reference path escapes registry root")
        digest = file_sha256(path)
        if digest != item["sha256"]:
            raise ValueError(f"reference integrity mismatch: {item['path']}")
        checked.append({"path": item["path"], "sha256": digest, "perceptual_hash": perceptual_hash(path)})
    return {"ok": True, "references": checked, "reuse_as_output": False}


def make_asset_record(slide_number: int, path: str | Path, relative_path: str, provider: str, source_type: str, license_name: str, source_url: str | None, candidate_id: str | None, prompt: str | None = None) -> AssetRecord:
    quality = image_quality(path)
    if not quality["ok"]:
        raise ValueError(f"asset failed deterministic visual quality: {quality}")
    return AssetRecord(
        slide_number=slide_number,
        local_path=relative_path,
        source_url=source_url,
        provider=provider,
        license=license_name,
        approved=False,
        candidate_id=candidate_id,
        source_type=source_type,
        sha256=file_sha256(path),
        perceptual_hash=perceptual_hash(path),
        prompt_sha256=prompt_sha256(prompt) if prompt else None,
        width=quality["width"],
        height=quality["height"],
    )
