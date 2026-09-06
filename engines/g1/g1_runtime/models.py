from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CampaignRequest(StrictModel):
    project: str = "company-core"
    objective: Literal["awareness", "education", "walkthrough", "trial"] = "awareness"
    format: Literal["carousel"] = "carousel"
    platforms: list[Literal["instagram", "x"]] = ["instagram", "x"]
    topic: str | None = None
    buyer: str | None = None
    fresh_research: bool = True
    freshness_days: int = Field(default=90, ge=0, le=3650)


class SearchResult(StrictModel):
    title: str
    url: str
    snippet: str = ""
    engine: str | None = None
    authority: float = Field(ge=0, le=1)
    source_class: str


class SourceEvidence(StrictModel):
    id: str
    title: str
    url: str
    authority: float = Field(ge=0, le=1)
    source_class: str
    excerpt: str


class ResearchClaim(StrictModel):
    id: str
    text: str
    status: Literal["supported", "partial", "inferred", "unsupported"]
    source_ids: list[str]


class ResearchBrief(StrictModel):
    question: str
    claims: list[ResearchClaim]
    sources: list[SourceEvidence]
    uncertainties: list[str] = []


class ContentConcept(StrictModel):
    id: str
    buyer: str
    pain: str
    thesis: str
    hook: str
    narrative: str
    cta: Literal["walkthrough", "trial"]
    allowed_claim_ids: list[str]
    evidence_strength: float = Field(ge=0, le=1)
    buyer_relevance: float = Field(ge=0, le=1)
    product_fit: float = Field(ge=0, le=1)


class ConceptSet(StrictModel):
    concepts: list[ContentConcept] = Field(min_length=3, max_length=3)


class Slide(StrictModel):
    number: int = Field(ge=1, le=6)
    purpose: Literal["cover", "evidence", "operational_complexity", "failure_point", "control_system", "outcome_cta"]
    headline: str = Field(min_length=3, max_length=100)
    body: str = Field(max_length=280)
    visual_concept: str
    asset_strategy: Literal["stock_photo", "document_composite", "route_diagram", "product_ui", "branded_end_card"] = "stock_photo"
    image_queries: list[str]
    claim_ids: list[str] = []


class PlatformCopy(StrictModel):
    instagram_caption: str = Field(max_length=2200)
    x_post: str


class BrandSpec(StrictModel):
    canvas: Literal["1080x1350"] = "1080x1350"
    logo_asset: str = "company_core_logo"
    logo_variant: Literal["white"] = "white"
    logo_position: Literal["top_left"] = "top_left"


class GenerationTrace(StrictModel):
    research: Literal["model", "fail_soft", "disabled"] = "model"
    concepts: Literal["model", "model_repair", "deterministic_fallback"] = "model"
    writer: Literal[
        "model", "model_structural_normalization", "model_schema_repair", "model_repair",
        "provider_fallback", "invalid_output_fallback", "deterministic_fallback",
    ] = "model"
    deterministic_actions: list[str] = Field(default_factory=list)


class DraftCampaign(StrictModel):
    campaign_id: str
    objective: Literal["awareness", "education", "walkthrough", "trial"]
    buyer: str
    narrative: str
    format: Literal["carousel"] = "carousel"
    platforms: list[Literal["instagram", "x"]]
    claim_ids: list[str]
    slides: list[Slide] = Field(min_length=5, max_length=6)
    platform_copy: PlatformCopy
    brand: BrandSpec = BrandSpec()
    generation: GenerationTrace = Field(default_factory=GenerationTrace)


class QualityReport(StrictModel):
    schema_valid: bool
    all_claims_grounded: bool
    forbidden_terms: list[str]
    unsupported_claims: list[str]
    duplicate_score: float = Field(ge=0, le=1)
    brand_score: float = Field(ge=0, le=1)
    buyer_score: float = Field(ge=0, le=1)
    editorial_score: float = Field(ge=0, le=1)
    rewrite_count: int = Field(default=0, ge=0, le=1)
    warnings: list[str]


class CampaignPackage(DraftCampaign):
    quality: QualityReport
    status: Literal["ready_for_media", "needs_founder_review"]
