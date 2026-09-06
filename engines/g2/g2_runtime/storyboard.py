from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .ingest import package_sha256
from .models import G1Campaign
from .platforms import PlatformName, get_profile
from .platform_script import PlatformScript


VisualMode = Literal[
    "kinetic_stock",
    "document_stack",
    "role_route",
    "status_handoff",
    "control_layer",
    "branded_end_card",
]


class MixedScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(ge=1, le=6)
    purpose: str
    visual_mode: VisualMode
    headline: str
    narration: str
    duration_seconds: float = Field(gt=0)
    delivery: str
    asset_slide_number: int | None = None
    claim_ids: list[str] = Field(default_factory=list)


class MixedVideoPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: Literal["mixed_media_short"] = "mixed_media_short"
    campaign_id: str
    source_package_sha256: str
    platform: PlatformName = "shorts"
    resolution: Literal["1920x1080", "1080x1920"] = "1080x1920"
    fps: Literal[30] = 30
    duration_target_seconds: int = Field(ge=20, le=300)
    scenes: list[MixedScene] = Field(min_length=5, max_length=6)
    visual_language: list[str]
    editorial_brief: dict[str, str]
    subtitle_preset: str
    outro_seconds: float
    editorial_source: Literal["g1_source_fallback", "platform_script"] = "g1_source_fallback"
    publish_allowed: Literal[False] = False


MODE_BY_PURPOSE: dict[str, VisualMode] = {
    "cover": "kinetic_stock",
    "evidence": "document_stack",
    "operational_complexity": "role_route",
    "failure_point": "status_handoff",
    "control_system": "control_layer",
    "outcome_cta": "branded_end_card",
}


def compile_storyboard(
    campaign: G1Campaign,
    duration: int | None = None,
    platform: PlatformName = "shorts",
    script: PlatformScript | None = None,
) -> MixedVideoPlan:
    profile = get_profile(platform)
    duration = duration or profile.duration_seconds
    weights = [max(7, len(slide.body.split())) for slide in campaign.slides]
    total = sum(weights)
    raw = [duration * weight / total for weight in weights]
    rounded = [round(value, 2) for value in raw]
    rounded[-1] = round(rounded[-1] + duration - sum(rounded), 2)
    scenes = []
    for slide, seconds in zip(campaign.slides, rounded):
        mode = MODE_BY_PURPOSE[slide.purpose]
        narration = script.scene_narration.get(slide.purpose, slide.body) if script else slide.body
        scenes.append(MixedScene(
            number=slide.number,
            purpose=slide.purpose,
            visual_mode=mode,
            headline=slide.headline,
            narration=narration,
            duration_seconds=seconds,
            delivery={
                "cover": "conversational_hook",
                "evidence": "clear_evidence",
                "operational_complexity": "explanatory",
                "failure_point": "restrained_concern",
                "control_system": "quiet_confidence",
                "outcome_cta": "warm_close",
            }[slide.purpose],
            asset_slide_number=None if slide.purpose == "outcome_cta" else slide.number,
            claim_ids=slide.claim_ids,
        ))
    return MixedVideoPlan(
        campaign_id=campaign.campaign_id,
        source_package_sha256=package_sha256(campaign),
        platform=platform,
        resolution=profile.resolution,
        duration_target_seconds=duration,
        scenes=scenes,
        visual_language=[
            "voiceover_led",
            "full_frame_relevant_imagery",
            "subtitles_only",
            "stable_static_framing",
            "audio_first_scene_timing",
            "provider_timestamp_subtitles",
        "strict_media_with_control_illustration",
            "no_persistent_logo",
            "premade_outro",
            "no_editorial_cards",
            "no_headline_overlays",
        ],
        editorial_brief={
            "goal": profile.narrative_goal,
            "opening": profile.opening_rule,
            "pacing": profile.pacing,
        },
        subtitle_preset=profile.subtitle_preset,
        outro_seconds=profile.outro_seconds,
        editorial_source="platform_script" if script else "g1_source_fallback",
    )
