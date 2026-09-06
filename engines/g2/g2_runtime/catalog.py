from __future__ import annotations

import json
import re
from pathlib import Path

from .models import AssetCandidate
from .models import AssetRecord, G1Campaign


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", value.lower()))


class LocalCatalogProvider:
    """Search previously curated media before making network requests."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def search(self, query: str, limit: int = 5, media_type: str = "image") -> list[AssetCandidate]:
        if not self.path.is_file():
            return []
        value = json.loads(self.path.read_text(encoding="utf-8"))
        items = value.get("candidates", value) if isinstance(value, dict) else value
        query_terms = _terms(query)
        scored = []
        for item in items:
            candidate = AssetCandidate.model_validate(item)
            if candidate.media_type != media_type:
                continue
            metadata = _terms(" ".join([candidate.description, *candidate.tags, candidate.query]))
            scored.append((len(query_terms & metadata), candidate))
        scored.sort(key=lambda pair: (-pair[0], pair[1].candidate_id))
        return [candidate for _, candidate in scored[:limit]]


def index_approved_media(
    records: list[AssetRecord], campaign: G1Campaign, asset_root: str | Path, catalog_path: str | Path,
) -> dict:
    """Add Founder-approved files to a reusable, searchable local catalog."""
    root = Path(asset_root).resolve()
    destination = Path(catalog_path)
    existing = []
    if destination.is_file():
        value = json.loads(destination.read_text(encoding="utf-8"))
        existing = value.get("candidates", value) if isinstance(value, dict) else value
    by_id = {item["candidate_id"]: item for item in existing}
    slides = {slide.number: slide for slide in campaign.slides}
    added = []
    for record in records:
        if not record.approved:
            continue
        path = (root / record.local_path).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"catalog asset is missing or escaped its root: {record.local_path}")
        slide = slides[record.slide_number]
        identifier = f"local:{record.sha256 or path.name}"
        item = AssetCandidate(
            candidate_id=identifier, provider="local", source_type="owned",
            media_type=record.media_type, query=" ".join(slide.image_queries),
            description=slide.visual_concept, tags=[slide.purpose, campaign.buyer, campaign.narrative],
            source_url=record.source_url, local_path=str(path), photographer=None,
            license=record.license or "Founder-approved local media", width=record.width or 1,
            height=record.height or 1, duration_seconds=record.duration_seconds,
        ).model_dump(mode="json")
        by_id[identifier] = item
        added.append(identifier)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"candidates": list(by_id.values())}, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "added": added, "catalog": str(destination), "total": len(by_id)}
