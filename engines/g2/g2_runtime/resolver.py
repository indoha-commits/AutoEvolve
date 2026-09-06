from __future__ import annotations

import io
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

from .assets import build_asset_plan, make_asset_record, rank_candidates, validate_reference_registry
from .fingerprint import hash_distance, perceptual_hash
from .models import AssetCandidate, G1Campaign
from .memory import AssetMemory
from .pexels import PexelsProvider
from .pixabay import PixabayProvider


DOWNLOAD_HOSTS = {
    "pexels": {"images.pexels.com"},
    "pixabay": {"cdn.pixabay.com", "pixabay.com", "cdn.pixabay.com"},
}


def _allowed_host(host: str, allowed: set[str]) -> bool:
    return any(host == item or host.endswith("." + item) for item in allowed)


def download_stock(candidate: AssetCandidate, timeout: int = 30, opener=None) -> bytes:
    if candidate.provider not in DOWNLOAD_HOSTS or not candidate.download_url:
        raise ValueError("candidate has no approved stock download route")
    parsed = urlparse(candidate.download_url)
    if parsed.scheme != "https" or not _allowed_host((parsed.hostname or "").lower(), DOWNLOAD_HOSTS[candidate.provider]):
        raise ValueError("stock URL is outside the provider allowlist")
    request = urllib.request.Request(candidate.download_url, headers={"User-Agent": "company-core-g2/0.8.1"})
    with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or not _allowed_host((final.hostname or "").lower(), DOWNLOAD_HOSTS[candidate.provider]):
            raise ValueError("stock redirect left the provider allowlist")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError(f"unexpected stock content type: {content_type}")
        raw = response.read(15_000_001)
    if len(raw) > 15_000_000:
        raise ValueError("stock asset exceeds 15 MB")
    with Image.open(io.BytesIO(raw)) as image:
        image.verify()
    return raw


def _save_normalized(raw: bytes, path: Path) -> None:
    with Image.open(io.BytesIO(raw)) as image:
        normalized = image.convert("RGB")
        normalized.save(path, "JPEG", quality=94, optimize=True)


class AssetResolver:
    def __init__(self, stock_providers=None, downloader=download_stock):
        self.stock_providers = stock_providers if stock_providers is not None else [PexelsProvider(), PixabayProvider()]
        self.downloader = downloader

    def resolve(self, campaign: G1Campaign, output_dir: str | Path, reference_root: str | Path, registry_path: str | Path, limit: int = 5, memory_path: str | Path | None = None) -> dict:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        references = validate_reference_registry(reference_root, registry_path)
        reference_phashes = [item["perceptual_hash"] for item in references["references"]]
        plan = build_asset_plan(campaign)
        records, report, seen_phashes = [], [], []
        memory = AssetMemory(memory_path) if memory_path else None
        for item in plan.items:
            if item.fallback == "deterministic_control_illustration":
                report.append({
                    "slide": item.slide_number,
                    "status": "deterministic_control_illustration",
                    "reason": "no owned product media supplied",
                })
                continue
            if item.requires_owned_asset:
                report.append({"slide": item.slide_number, "status": "requires_owned_product_ui"})
                continue
            if item.deterministic:
                report.append({"slide": item.slide_number, "status": "deterministic_end_card"})
                continue
            acquired = None
            errors = []
            for provider in self.stock_providers:
                try:
                    ranked = rank_candidates(provider.search(item.query or "", limit), item.query or "")
                except Exception as exc:
                    errors.append(f"{provider.__class__.__name__}: {type(exc).__name__}: {exc}")
                    continue
                for candidate in ranked:
                    if candidate.score < 0.30:
                        continue
                    try:
                        raw = self.downloader(candidate)
                        path = output / f"slide_{item.slide_number:02d}_{candidate.provider}.jpg"
                        _save_normalized(raw, path)
                        current_phash = perceptual_hash(path)
                        if any(hash_distance(current_phash, ref_hash) <= 3 for ref_hash in reference_phashes):
                            path.unlink(missing_ok=True)
                            raise ValueError("candidate is too similar to a style reference")
                        acquired = make_asset_record(item.slide_number, path, path.name, candidate.provider, "stock", candidate.license, candidate.source_url, candidate.candidate_id)
                        if any(hash_distance(acquired.perceptual_hash or "0", known) <= 5 for known in seen_phashes):
                            path.unlink(missing_ok=True)
                            raise ValueError("candidate duplicates another campaign asset")
                        if memory and memory.duplicate(acquired.sha256 or "", acquired.perceptual_hash or "0"):
                            path.unlink(missing_ok=True)
                            raise ValueError("candidate duplicates accepted media memory")
                        break
                    except Exception as exc:
                        errors.append(f"{candidate.candidate_id}: {type(exc).__name__}: {exc}")
                if acquired:
                    break
            if acquired:
                records.append(acquired)
                seen_phashes.append(acquired.perceptual_hash or "0")
                report.append({"slide": item.slide_number, "status": "candidate_ready_for_founder_review", "provider": acquired.provider, "path": acquired.local_path, "errors": errors})
            else:
                report.append({"slide": item.slide_number, "status": "unresolved", "errors": errors})
        manifest_path = output / "asset_manifest.json"
        manifest_path.write_text(json.dumps([item.model_dump(mode="json") for item in records], indent=2) + "\n", encoding="utf-8")
        result = {
            "campaign_id": campaign.campaign_id,
            "policy": "explicit_media_with_control_illustration",
            "reference_reuse": False,
            "assets": [item.model_dump(mode="json") for item in records],
            "resolution": report,
            "status": "needs_asset_review",
            "publish_allowed": False,
        }
        (output / "resolution_report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result
