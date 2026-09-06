from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic import BaseModel, Field

from services.asset_pack_builder import _headline, _image_queries, split_script_sections


MAX_SCENES = 12
WORDS_PER_MINUTE = 145
SECONDS_PER_SCENE = 12


class SceneBoundary(BaseModel):
    start_paragraph: int = Field(ge=1)
    end_paragraph: int = Field(ge=1)
    background_subject: str = Field(min_length=3, max_length=160)
    pexels_query: str = Field(min_length=3, max_length=100)
    change_reason: str = Field(min_length=3, max_length=240)


class SceneBoundaryPlan(BaseModel):
    scenes: list[SceneBoundary] = Field(min_length=1, max_length=MAX_SCENES)


def _clean_query(value: str, fallback: str) -> str:
    words = re.findall(r"[A-Za-z0-9-]+", str(value or ""))
    query = " ".join(words[:10]).strip().lower()
    return query if len(query.split()) >= 2 else fallback


def _script_metrics(script: str, paragraphs: list[str]) -> dict[str, Any]:
    spoken_text = "\n\n".join(paragraphs)
    word_count = len(re.findall(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?", spoken_text))
    duration_match = re.search(
        r"target\s+length\s*:\s*(\d+)\s*[–—-]\s*(\d+)\s*seconds?",
        script,
        flags=re.IGNORECASE,
    )
    if duration_match:
        low, high = (int(value) for value in duration_match.groups())
        duration_seconds = round((min(low, high) + max(low, high)) / 2)
        duration_source = "script"
    else:
        duration_seconds = max(1, round(word_count * 60 / WORDS_PER_MINUTE))
        duration_source = "estimated"
    target_scene_count = max(
        1,
        min(len(paragraphs), MAX_SCENES, round(duration_seconds / SECONDS_PER_SCENE)),
    )
    return {
        "word_count": word_count,
        "paragraph_count": len(paragraphs),
        "target_duration_seconds": duration_seconds,
        "target_scene_count": target_scene_count,
        "duration_source": duration_source,
    }


def _fallback_boundaries(paragraphs: list[str], target_count: int | None = None) -> list[dict[str, Any]]:
    count = min(len(paragraphs), MAX_SCENES, target_count or MAX_SCENES)
    starts = sorted({1 + (index * len(paragraphs)) // count for index in range(count)})
    return [
        {
            "start_paragraph": start,
            "end_paragraph": starts[index + 1] - 1 if index + 1 < len(starts) else len(paragraphs),
            "background_subject": _headline(paragraphs[start - 1]).title(),
            "pexels_query": _image_queries(paragraphs[start - 1])[0],
            "change_reason": "Fallback paragraph boundary",
        }
        for index, start in enumerate(starts)
    ]


def _normalize_boundaries(
    paragraphs: list[str],
    proposed: list[dict[str, Any]],
    target_count: int | None = None,
) -> list[dict[str, Any]]:
    by_start: dict[int, dict[str, Any]] = {}
    for item in proposed:
        start = int(item.get("start_paragraph") or 0)
        if 1 <= start <= len(paragraphs) and start not in by_start:
            by_start[start] = item
    if 1 not in by_start:
        by_start[1] = _fallback_boundaries(paragraphs, target_count)[0]
    starts = sorted(by_start)[:target_count or MAX_SCENES]
    scenes = []
    for index, start in enumerate(starts):
        end = starts[index + 1] - 1 if index + 1 < len(starts) else len(paragraphs)
        item = by_start[start]
        body = "\n\n".join(paragraphs[start - 1:end])
        fallback_query = _image_queries(body)[0]
        query = _clean_query(str(item.get("pexels_query") or ""), fallback_query)
        scenes.append({
            "number": index + 1,
            "paragraph_start": start,
            "paragraph_end": end,
            "headline": _headline(body),
            "body": body,
            "visual_concept": str(item.get("background_subject") or query).strip()[:240],
            "change_reason": str(item.get("change_reason") or "").strip()[:240],
            "pexels_query": query,
            "image_queries": [query],
        })
    return scenes


def _boundary_error(plan: SceneBoundaryPlan, paragraph_count: int, target_count: int) -> str | None:
    if len(plan.scenes) != target_count:
        return f"Return exactly {target_count} scenes; received {len(plan.scenes)}."
    expected_start = 1
    for index, scene in enumerate(plan.scenes, start=1):
        if scene.start_paragraph != expected_start:
            return f"Scene {index} must start at paragraph {expected_start}."
        if scene.end_paragraph < scene.start_paragraph or scene.end_paragraph > paragraph_count:
            return f"Scene {index} has an invalid paragraph range."
        expected_start = scene.end_paragraph + 1
    if expected_start != paragraph_count + 1:
        return f"The final scene must end at paragraph {paragraph_count}."
    return None


async def _request_ai_boundaries(paragraphs: list[str], target_count: int) -> SceneBoundaryPlan:
    from pydantic_ai import Agent, ModelRetry
    from core.models import cloud_model

    agent = Agent(
        cloud_model("fast"),
        output_type=SceneBoundaryPlan,
        instructions=(
            "You are a visual editor preparing background still images for a spoken business video. "
            "Group contiguous numbered paragraphs into visual scenes and decide exactly where the background "
            "image should change. Preserve paragraph order and cover the complete script from paragraph 1 "
            "through the final paragraph. Return between 1 and 12 scenes, aiming for 3 or more only when the "
            "script contains enough distinct settings. "
            "Each Pexels query must be 3-8 concrete English words describing one photograph that is likely to "
            "exist on Pexels. Prefer people, locations, documents, ports, trucks, warehouses, offices, and real "
            "operational activity. Avoid abstract concepts, software dashboards, logos, text, brand names, "
            "metaphors, and camera directions. Backgrounds are portrait images for vertical video. "
            "Set each scene start_paragraph and end_paragraph to contiguous paragraph ranges."
        ),
        retries=2,
    )

    @agent.output_validator
    def validate_scene_boundaries(plan: SceneBoundaryPlan) -> SceneBoundaryPlan:
        error = _boundary_error(plan, len(paragraphs), target_count)
        if error:
            raise ModelRetry(error)
        return plan

    numbered = "\n\n".join(f"{index}. {text}" for index, text in enumerate(paragraphs, start=1))
    result = await agent.run(
        f"Break this exact script into exactly {target_count} background-image scenes. Balance scene lengths "
        "while placing changes where the physical subject, setting, or operational idea changes. Do not "
        "rewrite or summarize any paragraph. Cover every paragraph exactly once with contiguous ranges.\n\n"
        + numbered
    )
    return result.output


def plan_script_scenes(script: str) -> dict[str, Any]:
    paragraphs = split_script_sections(script)
    if not paragraphs:
        raise ValueError("script must contain at least one usable paragraph")
    metrics = _script_metrics(script, paragraphs)
    target_count = metrics["target_scene_count"]
    try:
        result = asyncio.run(_request_ai_boundaries(paragraphs, target_count))
        proposed = [item.model_dump(mode="json") for item in result.scenes]
        source = "ai"
        error = None
    except Exception as exc:
        proposed = _fallback_boundaries(paragraphs, target_count)
        source = "fallback"
        error = f"{type(exc).__name__}: {exc}"
    scenes = _normalize_boundaries(paragraphs, proposed, target_count)
    return {
        "source": source,
        "planner_error": error,
        **metrics,
        "returned_scene_count": len(scenes),
        "scenes": scenes,
    }
