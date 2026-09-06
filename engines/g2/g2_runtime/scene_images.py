from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .models import AssetCandidate
from .pexels import PexelsProvider


class SceneImageRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int = Field(ge=1, le=100)
    pexels_query: str = Field(min_length=2, max_length=100)


class SceneImagePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scenes: list[SceneImageRequest] = Field(min_length=1, max_length=100)


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", value.lower()))


def _rank(candidate: AssetCandidate, query: str) -> tuple:
    overlap = len(_terms(query) & _terms(candidate.description))
    portrait = candidate.height >= candidate.width
    delivery_size = candidate.width >= 1080 and candidate.height >= 1920
    return portrait, delivery_size, overlap, candidate.width * candidate.height


def search_scene_images(
    plan_path: str | Path,
    *,
    deadline: float = 8.0,
    limit: int = 4,
    max_workers: int = 6,
) -> dict:
    plan = SceneImagePlan.model_validate_json(Path(plan_path).read_text(encoding="utf-8"))
    provider = PexelsProvider(timeout=max(2, min(int(deadline), 10)))
    started = time.monotonic()
    errors = []
    results: dict[int, list[AssetCandidate]] = {}
    executor = ThreadPoolExecutor(max_workers=max(1, min(max_workers, 12)))
    futures = {
        executor.submit(provider.search, scene.pexels_query, max(1, min(limit, 10)), "image"): scene
        for scene in plan.scenes
    }
    try:
        remaining = max(0.01, deadline - (time.monotonic() - started))
        try:
            for future in as_completed(futures, timeout=remaining):
                scene = futures[future]
                try:
                    results[scene.number] = future.result()
                except Exception as exc:
                    errors.append({
                        "scene": scene.number,
                        "query": scene.pexels_query,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
        except TimeoutError:
            errors.append({"provider": "pexels", "error": "global scene-image deadline reached"})
    finally:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    used = set()
    scenes = []
    for request in plan.scenes:
        ranked = sorted(results.get(request.number, []), key=lambda item: _rank(item, request.pexels_query), reverse=True)
        selected = next((item for item in ranked if item.candidate_id not in used), None)
        if selected:
            used.add(selected.candidate_id)
        scenes.append({
            "scene": request.number,
            "status": "candidate_selected" if selected else "unresolved",
            "query": request.pexels_query,
            "selected": selected.model_dump(mode="json") if selected else None,
            "clip_window": None,
            "candidates": [item.model_dump(mode="json") for item in ranked[:limit]],
        })
    return {
        "campaign_id": Path(plan_path).stem,
        "policy": "ai_scene_boundary_to_pexels_image",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "deadline_seconds": deadline,
        "scenes": scenes,
        "errors": errors,
        "status": "needs_founder_media_review",
        "publish_allowed": False,
    }


def write_scene_search_result(value: dict, destination: str | Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return path
