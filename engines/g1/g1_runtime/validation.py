from __future__ import annotations

import os
import re
from itertools import combinations

from .models import CampaignPackage, DraftCampaign, QualityReport
from .security import normalize_text, similarity


GENERIC_COPY = {
    "tired of": "generic hook: tired of",
    "improve efficiency": "generic outcome: improve efficiency",
    "learn more": "generic CTA: learn more",
    "helps manage": "generic product copy: helps manage",
    "successfully managing": "generic outcome: successfully managing",
}

CLAIM_LANGUAGE = re.compile(
    r"\b(?:cause[sd]?|lead(?:s|ing)? to|result(?:s|ing)? in|reduce[sd]?|increase[sd]?|"
    r"improve[sd]?|prevent[sd]?|eliminate[sd]?|costly|delay(?:s|ed|ing)?|disrupt(?:s|ed|ion)|"
    r"more accurate|more efficient|save[sd]?|faster|slower|\d+(?:\.\d+)?\s*%|\d+\s*[x×])\b",
    re.I,
)

BRAND_NAME = os.getenv("COMPANY_NAME", "Example Company").strip() or "Example Company"

ABSTRACT_IMAGE_LANGUAGE = re.compile(
    rf"\b(?:{re.escape(BRAND_NAME)}|due to|because of|successfully|efficiently|efficiency|"
    r"control layer|visibility|accuracy|outdated shipping documents)\b",
    re.I,
)


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?])\s+", text) if item.strip()]


class CampaignValidator:
    def __init__(self, approved_claim_ids: set[str], prohibited: dict, recent: list[dict],
                 allowed_narratives: set[str] | None = None):
        self.approved_claim_ids = approved_claim_ids
        self.prohibited = prohibited
        self.recent = recent
        self.allowed_narratives = allowed_narratives or set()

    def validate(self, draft: DraftCampaign, rewrite_count: int = 0) -> CampaignPackage:
        content = draft.model_dump_json().lower()
        forbidden = [term for term in self.prohibited["case_insensitive_terms"] if term.lower() in content]
        for pattern in self.prohibited.get("patterns", []):
            if re.search(pattern, content, re.I):
                forbidden.append(pattern)

        used = set(draft.claim_ids)
        for slide in draft.slides:
            used.update(slide.claim_ids)
        unsupported = sorted(used - self.approved_claim_ids)

        recent_scores = [similarity(slide.headline, row["hook"]) for slide in draft.slides for row in self.recent]
        internal_scores = [similarity(left.headline + " " + left.body, right.headline + " " + right.body)
                           for left, right in combinations(draft.slides, 2)]
        duplicate = max(recent_scores + internal_scores, default=0.0)
        warnings: list[str] = []

        self._validate_cta(draft, used, warnings)
        self._validate_story(draft, warnings)
        self._validate_copy(draft, warnings)
        self._validate_claim_language(draft, warnings)
        self._validate_images(draft, warnings)

        if self.allowed_narratives and draft.narrative not in self.allowed_narratives:
            warnings.append(f"narrative is not registered: {draft.narrative}")
        if duplicate >= 0.58:
            warnings.append(f"copy similarity {duplicate:.2f} exceeds 0.58")

        warnings = sorted(set(warnings))
        penalty = min(1.0, 0.10 * len(warnings) + 0.15 * len(forbidden) + 0.15 * len(unsupported))
        editorial_score = round(max(0.0, 1.0 - penalty), 4)
        ready = not forbidden and not unsupported and not warnings and editorial_score >= 0.85
        quality = QualityReport(
            schema_valid=True,
            all_claims_grounded=not unsupported and not any(
                "ungrounded claim language" in item or "causal or outcome language" in item for item in warnings
            ),
            forbidden_terms=sorted(set(forbidden)),
            unsupported_claims=unsupported,
            duplicate_score=round(duplicate, 4),
            brand_score=1.0,
            buyer_score=0.9,
            editorial_score=editorial_score,
            rewrite_count=rewrite_count,
            warnings=warnings,
        )
        return CampaignPackage(**draft.model_dump(), quality=quality,
                               status="ready_for_media" if ready else "needs_founder_review")

    @staticmethod
    def _validate_cta(draft: DraftCampaign, used_claim_ids: set[str], warnings: list[str]) -> None:
        text = " ".join([draft.platform_copy.instagram_caption, draft.platform_copy.x_post] +
                        [slide.headline + " " + slide.body for slide in draft.slides]).lower()
        walkthrough = "book a walkthrough" in text
        trial = "7-day trial" in text or "7 day trial" in text
        if draft.objective == "trial":
            if not trial:
                warnings.append("trial campaign lacks the approved 7-day CTA")
            if walkthrough:
                warnings.append("trial campaign mixes walkthrough and trial CTA families")
            if "offer.walkthrough" in used_claim_ids:
                warnings.append("trial campaign declares the walkthrough offer claim")
        else:
            if not walkthrough:
                warnings.append("campaign lacks the approved walkthrough CTA")
            if trial:
                warnings.append(f"{draft.objective} campaign cannot use the trial CTA")
            if "offer.trial_7_day" in used_claim_ids:
                warnings.append(f"{draft.objective} campaign declares the trial offer claim")

    @staticmethod
    def _validate_story(draft: DraftCampaign, warnings: list[str]) -> None:
        purposes = [slide.purpose for slide in draft.slides]
        if purposes[0] != "cover":
            warnings.append("slide 1 must be the cover")
        if purposes[-1] != "outcome_cta":
            warnings.append("final slide must be outcome_cta")
        if len(purposes) < 2 or purposes[-2] != "control_system":
            warnings.append("control_system must immediately precede the CTA")
        if "failure_point" not in purposes[:-2]:
            warnings.append("story lacks a failure_point before the product")
        if not ({"evidence", "operational_complexity"} & set(purposes[1:-2])):
            warnings.append("story lacks evidence or operational complexity")
        if len(set(purposes)) != len(purposes):
            warnings.append("slide purposes must not repeat")
        product_start = len(draft.slides) - 1
        for slide in draft.slides[:product_start - 1]:
            if BRAND_NAME.lower() in (slide.headline + " " + slide.body).lower():
                warnings.append(f"product appears too early on slide {slide.number}")
        control = draft.slides[-2]
        if BRAND_NAME.lower() not in (control.headline + " " + control.body).lower():
            warnings.append("control_system slide must introduce Example Company")
        if not any(claim_id.startswith("product.") for claim_id in control.claim_ids):
            warnings.append("control_system slide lacks an approved product claim ID")

    @staticmethod
    def _validate_copy(draft: DraftCampaign, warnings: list[str]) -> None:
        all_copy = " ".join([draft.platform_copy.instagram_caption, draft.platform_copy.x_post] +
                            [slide.headline + " " + slide.body for slide in draft.slides]).lower()
        for phrase, label in GENERIC_COPY.items():
            if phrase in all_copy:
                warnings.append(label)
        for slide in draft.slides:
            words = normalize_text(slide.headline).split()
            if not 2 <= len(words) <= 9:
                warnings.append(f"slide {slide.number} headline must contain 2-9 words")
            if slide.headline != slide.headline.upper():
                warnings.append(f"slide {slide.number} headline must be uppercase")
            if "?" in slide.headline and slide.purpose != "failure_point":
                warnings.append(f"slide {slide.number} question headline is only allowed at the failure point")
            if len(normalize_text(slide.body).split()) > 35:
                warnings.append(f"slide {slide.number} body exceeds 35 words")

    @staticmethod
    def _validate_claim_language(draft: DraftCampaign, warnings: list[str]) -> None:
        for slide in draft.slides:
            for sentence in _sentences(slide.headline + ". " + slide.body):
                if CLAIM_LANGUAGE.search(sentence) and not slide.claim_ids:
                    warnings.append(f"slide {slide.number} has ungrounded claim language: {sentence[:90]}")
        for label, text in (("Instagram caption", draft.platform_copy.instagram_caption),
                            ("X post", draft.platform_copy.x_post)):
            if CLAIM_LANGUAGE.search(text):
                warnings.append(f"{label} contains causal or outcome language without sentence-level claim IDs")

    @staticmethod
    def _validate_images(draft: DraftCampaign, warnings: list[str]) -> None:
        for slide in draft.slides:
            if slide.asset_strategy in {"product_ui", "branded_end_card"}:
                if slide.image_queries:
                    warnings.append(f"slide {slide.number} {slide.asset_strategy} must use approved assets, not image search")
                continue
            for query in slide.image_queries:
                if not query.strip():
                    warnings.append(f"slide {slide.number} has an empty image query")
                elif ABSTRACT_IMAGE_LANGUAGE.search(query):
                    warnings.append(f"slide {slide.number} image query is not photographable: {query[:90]}")
                elif re.search(r"https?://|\bwww\.", query, re.I):
                    warnings.append(f"slide {slide.number} image query contains a URL")


def rank_concept(concept, recent: list[dict]) -> float:
    novelty = 1 - max((similarity(concept.hook, row["hook"]) for row in recent), default=0.0)
    generic_penalty = 0.25 if any(phrase in concept.hook.lower() for phrase in GENERIC_COPY) else 0.0
    question_penalty = 0.15 if "?" in concept.hook else 0.0
    score = 0.30 * concept.buyer_relevance + 0.25 * novelty + 0.25 * concept.evidence_strength + 0.20 * concept.product_fit
    return round(max(0.0, score - generic_penalty - question_penalty), 6)
