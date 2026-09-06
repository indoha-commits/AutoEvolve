from __future__ import annotations

from .ingest import package_sha256
from .models import G1Campaign, VideoManifest, VideoScene


VIDEO_STRATEGY = {
    "stock_photo": "stock_video",
    "document_composite": "document_motion",
    "route_diagram": "route_animation",
    "product_ui": "product_ui_motion",
    "branded_end_card": "branded_end_card",
}


def video_manifest(campaign: G1Campaign, duration: int = 45) -> VideoManifest:
    if not 35 <= duration <= 55:
        raise ValueError("video duration must be 35-55 seconds")
    weights = [0.12, 0.16, 0.18, 0.20, 0.20, 0.14] if len(campaign.slides) == 6 else [0.14, 0.20, 0.22, 0.26, 0.18]
    raw = [round(duration * weight, 2) for weight in weights]
    raw[-1] = round(raw[-1] + duration - sum(raw), 2)
    scenes = []
    for slide, seconds in zip(campaign.slides, raw):
        queries = list(slide.image_queries)
        if slide.asset_strategy in {"product_ui", "branded_end_card"}:
            queries = []
        scenes.append(VideoScene(
            number=slide.number,
            duration_seconds=seconds,
            narration=slide.body,
            on_screen_copy=slide.headline,
            asset_strategy=VIDEO_STRATEGY[slide.asset_strategy],
            asset_queries=queries,
            claim_ids=list(slide.claim_ids),
        ))
    return VideoManifest(
        campaign_id=campaign.campaign_id,
        source_package_sha256=package_sha256(campaign),
        duration_target_seconds=duration,
        scenes=scenes,
    )
