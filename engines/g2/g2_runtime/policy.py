from __future__ import annotations

import hmac
from pathlib import Path

from PIL import Image

from .models import AssetRecord, G1Campaign


ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_ASSET_BYTES = 15_000_000


def constant_time_secret(received: str, expected: str) -> bool:
    return bool(received and expected) and hmac.compare_digest(received.encode(), expected.encode())


def validate_asset(record: AssetRecord, asset_root: Path, campaign: G1Campaign) -> Path:
    slide = next((item for item in campaign.slides if item.number == record.slide_number), None)
    if slide is None:
        raise ValueError(f"asset references unknown slide {record.slide_number}")
    root = asset_root.resolve()
    candidate = (root / record.local_path).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("asset path escapes the approved asset directory")
    if candidate.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError(f"unsupported asset type: {candidate.suffix}")
    if not candidate.is_file():
        raise ValueError(f"asset file not found: {record.local_path}")
    if candidate.stat().st_size > MAX_ASSET_BYTES:
        raise ValueError("asset exceeds 15 MB")
    if not record.approved:
        raise ValueError(f"slide {slide.number} asset requires founder approval")
    if slide.asset_strategy == "product_ui" and record.source_type not in {"owned", "illustration"}:
        raise ValueError(
            f"slide {slide.number} control-system media must be owned product media "
            "or an approved illustration"
        )
    with Image.open(candidate) as image:
        image.verify()
    return candidate
