from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import AssetCandidate, G1Campaign
from .storyboard import MixedVideoPlan


MediaType = Literal["image", "video", "lottie", "svg"]


class VisualRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_number: int = Field(ge=1, le=6)
    purpose: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    preferred_media_type: MediaType
    allowed_media_types: list[MediaType]
    queries: list[str] = Field(min_length=1, max_length=4)
    visual_concept: str
    required_visual_anchors: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    orientation: Literal["portrait", "landscape"]
    minimum_width: int
    minimum_height: int
    selection_rule: str


class MediaSearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: str
    resolution: str
    requirements: list[VisualRequirement]
    search_deadline_seconds: float = 15.0
    candidates_per_provider: int = 6
    policy: Literal["one_primary_medium_per_timed_unit"] = "one_primary_medium_per_timed_unit"


STOPWORDS = {
    "about", "across", "after", "around", "being", "between", "could", "different",
    "each", "every", "from", "have", "into", "must", "other", "same", "their",
    "there", "these", "this", "through", "with", "without", "your",
}

ABSTRACT_PENALTIES = {
    "delay", "delays", "error", "errors", "efficient", "efficiency", "uncertainty",
    "accuracy", "visibility", "current", "outdated", "mismatch", "control",
}

COMMON_AVOID = [
    "watermark", "embedded text", "visible foreign brand", "generic handshake",
    "posed corporate meeting", "futuristic hologram", "warehouse robot",
]

PURPOSE_QUERY_EXPANSIONS = {
    "cover": ["container truck freight documents operator", "cargo port logistics worker"],
    "evidence": ["shipping documents desk close up", "office paperwork document copies"],
    "operational_complexity": ["freight operations team office", "logistics staff reviewing documents"],
    "failure_point": ["freight operator checking shipping paperwork", "shipping document inspection close up"],
    "control_system": ["connected workflow", "document validation", "cargo tracking"],
}

# These are literal scene subjects, not soft ranking hints. A candidate must
# contain at least one anchor in its provider metadata before medium, duration,
# orientation, or resolution bonuses can make it selectable.
PURPOSE_REQUIRED_ANCHORS = {
    "cover": ["cargo", "freight", "container", "truck", "ship", "port", "logistics"],
    "evidence": ["document", "documents", "paper", "paperwork", "invoice", "form", "file", "files", "clipboard", "record", "records"],
    "operational_complexity": ["operator", "operators", "worker", "workers", "staff", "team", "teams", "people", "office", "document", "documents"],
    "failure_point": ["document", "documents", "paper", "paperwork", "record", "records", "operator", "inspection", "form", "validation"],
}


def _terms(value: str) -> set[str]:
    return {
        item for item in re.findall(r"[a-z0-9]+", value.lower())
        if len(item) >= 3 and item not in STOPWORDS
    }


def _literal_query(value: str) -> str:
    words = [
        item for item in re.findall(r"[A-Za-z0-9]+", value)
        if item.lower() not in ABSTRACT_PENALTIES
    ]
    return " ".join(words[:12]).strip()


def _queries(slide) -> list[str]:
    # Product-UI wording is useful to the renderer but poor input to a generic
    # vector library. Purpose expansions provide literal, searchable fallbacks.
    values = [*slide.image_queries, slide.visual_concept, *PURPOSE_QUERY_EXPANSIONS.get(slide.purpose, [])]
    if slide.purpose == "control_system":
        values = PURPOSE_QUERY_EXPANSIONS[slide.purpose]
    output = []
    for value in values:
        cleaned = _literal_query(value)
        if cleaned and cleaned.lower() not in {item.lower() for item in output}:
            output.append(cleaned)
    return output[:4] or ["freight logistics operations"]


def build_media_search_plan(campaign: G1Campaign, storyboard: MixedVideoPlan) -> MediaSearchPlan:
    slides = {slide.number: slide for slide in campaign.slides}
    elapsed = 0.0
    requirements = []
    width, height = (int(value) for value in storyboard.resolution.split("x"))
    orientation = "portrait" if height >= width else "landscape"
    for scene in storyboard.scenes:
        slide = slides[scene.number]
        start = round(elapsed, 3)
        end = round(elapsed + scene.duration_seconds, 3)
        elapsed = end
        if scene.purpose == "control_system":
            preferred, allowed = "lottie", ["lottie", "svg"]
            selection_rule = "abstract operational process uses one vector animation without stock-media stacking"
        elif scene.purpose == "outcome_cta":
            preferred, allowed = "image", ["image"]
            selection_rule = "approved premade branded outro only"
        else:
            preferred, allowed = "video", ["video", "image"]
            selection_rule = "literal documentary shot first; still image only when no relevant video survives ranking"
        requirements.append(VisualRequirement(
            scene_number=scene.number,
            purpose=scene.purpose,
            start_seconds=start,
            end_seconds=end,
            duration_seconds=scene.duration_seconds,
            preferred_media_type=preferred,
            allowed_media_types=allowed,
            queries=_queries(slide),
            visual_concept=slide.visual_concept,
            required_visual_anchors=PURPOSE_REQUIRED_ANCHORS.get(scene.purpose, []),
            avoid=COMMON_AVOID,
            orientation=orientation,
            minimum_width=width,
            minimum_height=height,
            selection_rule=selection_rule,
        ))
    return MediaSearchPlan(
        campaign_id=campaign.campaign_id,
        resolution=storyboard.resolution,
        requirements=requirements,
    )


def score_media_candidate(candidate: AssetCandidate, requirement: VisualRequirement) -> AssetCandidate:
    score = 0.0
    reasons = []
    query_terms = set().union(*(_terms(value) for value in requirement.queries))
    candidate_terms = _terms(" ".join([candidate.description, *candidate.tags]))
    required_anchors = set(requirement.required_visual_anchors)
    matched_anchors = sorted(candidate_terms & required_anchors)
    if required_anchors and not matched_anchors:
        return candidate.model_copy(update={
            "score": 0.0,
            "score_reasons": [
                "required visual anchor missing; expected one of: "
                + ", ".join(sorted(required_anchors))
            ],
        })
    if matched_anchors:
        reasons.append("required visual anchor matched: " + ", ".join(matched_anchors))
    overlap = len(query_terms & candidate_terms) / max(1, len(query_terms))
    semantic = min(0.34, overlap * 0.68)
    score += semantic
    reasons.append(f"literal metadata match {overlap:.2f}")

    if candidate.media_type == requirement.preferred_media_type:
        score += 0.20
        reasons.append("preferred medium")
    elif candidate.media_type in requirement.allowed_media_types:
        score += 0.06
        reasons.append("permitted fallback medium")
    else:
        return candidate.model_copy(update={"score": 0.0, "score_reasons": ["medium rejected"]})

    portrait = candidate.height >= candidate.width
    if candidate.media_type in {"lottie", "svg"}:
        score += 0.08
        reasons.append("vector canvas is layout-independent")
    elif (requirement.orientation == "portrait" and portrait) or (requirement.orientation == "landscape" and not portrait):
        score += 0.10
        reasons.append("native orientation")
    else:
        crop_ratio = min(candidate.width / candidate.height, candidate.height / candidate.width)
        score += max(0.0, 0.04 * crop_ratio)
        reasons.append("crop required")

    scale = min(candidate.width / requirement.minimum_width, candidate.height / requirement.minimum_height)
    if candidate.media_type in {"lottie", "svg"}:
        score += 0.10
        reasons.append("resolution-independent vector")
    elif scale >= 1.0:
        score += 0.10
        reasons.append("delivery resolution")
    elif scale >= 0.67:
        score += 0.04
        reasons.append("marginal delivery resolution")
    else:
        score -= 0.16
        reasons.append("insufficient resolution")

    if candidate.media_type == "video":
        duration = candidate.duration_seconds
        if duration is None:
            score += 0.02
            reasons.append("duration requires file probe")
        elif duration >= requirement.duration_seconds:
            score += 0.10
            reasons.append("covers timed unit")
        elif duration >= max(2.0, requirement.duration_seconds * 0.65):
            score += 0.03
            reasons.append("short but usable")
        else:
            score -= 0.14
            reasons.append("too short for timed unit")

    avoid_terms = set().union(*(_terms(value) for value in requirement.avoid))
    conflicts = candidate_terms & avoid_terms
    if conflicts:
        score -= min(0.25, 0.08 * len(conflicts))
        reasons.append("avoid-term conflict: " + ", ".join(sorted(conflicts)))
    if candidate.premium:
        score = 0.0
        reasons.append("premium asset rejected")
    return candidate.model_copy(update={"score": round(max(0.0, min(score, 1.0)), 4), "score_reasons": reasons})


class FederatedMediaSearch:
    """Deadline-bound, provider-neutral media discovery and deterministic ranking."""

    def __init__(self, providers: list, max_workers: int = 8):
        self.providers = providers
        self.max_workers = max_workers

    def search(self, plan: MediaSearchPlan) -> dict:
        started = time.monotonic()
        errors: list[dict] = []
        by_scene: dict[int, dict[str, AssetCandidate]] = {item.scene_number: {} for item in plan.requirements}
        futures = {}
        executor = ThreadPoolExecutor(max_workers=self.max_workers)
        try:
            for requirement in plan.requirements:
                if requirement.purpose == "outcome_cta":
                    continue
                for provider in self.providers:
                    for media_type in requirement.allowed_media_types:
                        for query in requirement.queries:
                            future = executor.submit(provider.search, query, plan.candidates_per_provider, media_type)
                            futures[future] = (requirement, provider.__class__.__name__, media_type, query)
            remaining = max(0.01, plan.search_deadline_seconds - (time.monotonic() - started))
            try:
                completed = as_completed(futures, timeout=remaining)
                for future in completed:
                    requirement, provider_name, media_type, query = futures[future]
                    try:
                        candidates = future.result()
                    except Exception as exc:
                        errors.append({
                            "scene": requirement.scene_number,
                            "provider": provider_name,
                            "media_type": media_type,
                            "query": query,
                            "error": f"{type(exc).__name__}: {exc}",
                        })
                        continue
                    for candidate in candidates:
                        previous = by_scene[requirement.scene_number].get(candidate.candidate_id)
                        ranked = score_media_candidate(candidate, requirement)
                        if previous is None or ranked.score > previous.score:
                            by_scene[requirement.scene_number][candidate.candidate_id] = ranked
            except TimeoutError:
                errors.append({"provider": "federated", "error": "global search deadline reached"})
        finally:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)

        selected_ids = set()
        scenes = []
        for requirement in plan.requirements:
            if requirement.purpose == "outcome_cta":
                scenes.append({
                    "scene": requirement.scene_number,
                    "status": "premade_outro",
                    "requirement": requirement.model_dump(mode="json"),
                    "selected": None,
                    "candidates": [],
                })
                continue
            ranked = sorted(
                by_scene[requirement.scene_number].values(),
                key=lambda item: (-item.score, item.candidate_id),
            )
            # Prevent one attractive generic clip from silently carrying several scenes.
            novel = [item for item in ranked if item.candidate_id not in selected_ids]
            selected = next((item for item in novel if item.score >= 0.34), None)
            if selected:
                selected_ids.add(selected.candidate_id)
            scenes.append({
                "scene": requirement.scene_number,
                "status": "candidate_selected" if selected else "unresolved",
                "requirement": requirement.model_dump(mode="json"),
                "selected": selected.model_dump(mode="json") if selected else None,
                "clip_window": {
                    "start_seconds": 0.0,
                    "end_seconds": round(min(
                        selected.duration_seconds or requirement.duration_seconds,
                        requirement.duration_seconds,
                    ), 3),
                } if selected and selected.media_type == "video" else None,
                "candidates": [item.model_dump(mode="json") for item in ranked[:8]],
            })
        return {
            "campaign_id": plan.campaign_id,
            "policy": plan.policy,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "deadline_seconds": plan.search_deadline_seconds,
            "scenes": scenes,
            "errors": errors,
            "status": "needs_media_review",
            "publish_allowed": False,
        }


def write_search_result(value: dict, destination: str | Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return path
