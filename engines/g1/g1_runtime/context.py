from __future__ import annotations

from typing import Any

from .store import CampaignStore


def context_pack(store: CampaignStore, exemplars: dict[str, Any], narrative: str | None = None) -> dict[str, Any]:
    campaigns = exemplars.get("campaigns", [])
    selected = next((item for item in campaigns if item.get("narrative") == narrative), None)
    if selected is None and campaigns:
        selected = campaigns[0]
    return {
        "style_principles": exemplars.get("style_principles", []),
        "positive_exemplar": selected,
        "reserved_headlines": exemplars.get("reserved_headlines", []),
        "usable_campaign_memory": store.usable_memory(12),
        "failure_lessons": store.failure_lessons(12),
    }


def generation_context(context: dict[str, Any]) -> dict[str, Any]:
    """Return abstract guidance only; exact protected copy stays validator-private."""
    exemplar = context.get("positive_exemplar") or {}
    distilled_exemplar = {
        "id": exemplar.get("id"),
        "narrative": exemplar.get("narrative"),
        "buyer": exemplar.get("buyer"),
        "objective": exemplar.get("objective"),
        "story_anatomy": exemplar.get("story_anatomy", []),
        "visual_strategy": exemplar.get("visual_strategy", []),
    }
    return {
        "style_principles": context.get("style_principles", []),
        "positive_structure": distilled_exemplar,
        "usable_campaign_memory": context.get("usable_campaign_memory", []),
        "failure_lessons": context.get("failure_lessons", []),
    }
