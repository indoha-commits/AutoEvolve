from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Slide(StrictModel):
    number: int = Field(ge=1, le=6)
    purpose: Literal["cover", "evidence", "operational_complexity", "failure_point", "control_system", "outcome_cta"]
    headline: str
    body: str
    visual_concept: str
    asset_strategy: Literal["stock_photo", "document_composite", "route_diagram", "product_ui", "branded_end_card"]
    image_queries: list[str]
    claim_ids: list[str] = Field(default_factory=list)


class PlatformCopy(StrictModel):
    instagram_caption: str
    x_post: str


class BrandSpec(StrictModel):
    canvas: Literal["1080x1350"]
    logo_asset: str
    logo_variant: Literal["white"]
    logo_position: Literal["top_left"]


class QualityReport(StrictModel):
    schema_valid: bool
    all_claims_grounded: bool
    forbidden_terms: list[str]
    unsupported_claims: list[str]
    duplicate_score: float
    brand_score: float
    buyer_score: float
    editorial_score: float
    rewrite_count: int
    warnings: list[str]


class GenerationTrace(StrictModel):
    research: str
    concepts: str
    writer: str
    deterministic_actions: list[str] = Field(default_factory=list)


class G1Campaign(StrictModel):
    campaign_id: str
    objective: Literal["awareness", "education", "walkthrough", "trial"]
    buyer: str
    narrative: str
    format: Literal["carousel"]
    platforms: list[Literal["instagram", "x"]]
    claim_ids: list[str]
    slides: list[Slide] = Field(min_length=5, max_length=6)
    platform_copy: PlatformCopy
    brand: BrandSpec
    generation: GenerationTrace
    quality: QualityReport
    status: Literal["ready_for_media", "needs_founder_review"]


class AssetRecord(StrictModel):
    slide_number: int = Field(ge=1, le=100)
    local_path: str
    source_url: str | None = None
    provider: str | None = None
    license: str | None = None
    approved: bool = False
    candidate_id: str | None = None
    source_type: Literal["stock", "generated", "owned", "illustration", "deterministic"] | None = None
    sha256: str | None = None
    perceptual_hash: str | None = None
    prompt_sha256: str | None = None
    width: int | None = None
    height: int | None = None
    media_type: Literal["image", "video", "lottie", "svg"] = "image"
    duration_seconds: float | None = None
    clip_start_seconds: float = 0.0
    clip_end_seconds: float | None = None


class AssetCandidate(StrictModel):
    candidate_id: str
    provider: Literal["pexels", "pixabay", "coverr", "wikimedia", "lordicon", "local", "omniroute", "owned"]
    source_type: Literal["stock", "generated", "owned", "vector"]
    media_type: Literal["image", "video", "lottie", "svg"] = "image"
    query: str
    source_url: str | None = None
    download_url: str | None = None
    local_path: str | None = None
    photographer: str | None = None
    description: str = ""
    license: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    duration_seconds: float | None = None
    preview_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    premium: bool = False
    score: float = 0.0
    score_reasons: list[str] = Field(default_factory=list)


class AssetPlanItem(StrictModel):
    slide_number: int = Field(ge=1, le=6)
    asset_strategy: str
    query: str | None
    providers: list[str]
    generation_prompt: str | None
    requires_owned_asset: bool = False
    fallback: str | None = None
    deterministic: bool = False


class AssetPlan(StrictModel):
    campaign_id: str
    policy: Literal["explicit_media_with_control_illustration"] = "explicit_media_with_control_illustration"
    reference_registry: str
    items: list[AssetPlanItem]
    publish_allowed: Literal[False] = False


class VideoScene(StrictModel):
    number: int
    duration_seconds: float = Field(gt=0)
    narration: str
    on_screen_copy: str
    asset_strategy: Literal["stock_video", "document_motion", "route_animation", "product_ui_motion", "branded_end_card"]
    asset_queries: list[str]
    claim_ids: list[str]


class VideoManifest(StrictModel):
    media_type: Literal["short_video"] = "short_video"
    campaign_id: str
    source_package_sha256: str
    aspect: Literal["9:16"] = "9:16"
    resolution: Literal["1080x1920"] = "1080x1920"
    duration_target_seconds: int = Field(ge=35, le=55)
    scenes: list[VideoScene] = Field(min_length=5, max_length=7)
    caption_source: Literal["tts_timestamps", "whisper_for_uploaded_voice"] = "tts_timestamps"
    status: Literal["ready_to_render"] = "ready_to_render"
