from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from .models import AssetCandidate


class LordiconProvider:
    """Search the official Lordicon API for free Lottie animations.

    The signed file URLs are intentionally treated as ephemeral. A selected
    animation must be downloaded into the campaign asset cache before render.
    """

    endpoint = "https://api.lordicon.com/v1/icons"

    def __init__(self, token: str | None = None, timeout: int = 5):
        self.token = token or os.getenv("LORDICON_API_TOKEN", "")
        self.timeout = timeout

    def search(self, query: str, limit: int = 5, media_type: str = "lottie") -> list[AssetCandidate]:
        if media_type not in {"lottie", "svg"}:
            return []
        if not self.token:
            raise RuntimeError("LORDICON_API_TOKEN is not configured")
        limit = max(1, min(limit, 25))
        params = {
            "search": query,
            "premium": "false",
            "family": "wired",
            "per_page": limit,
        }
        request = urllib.request.Request(
            f"{self.endpoint}?{urllib.parse.urlencode(params)}",
            headers={"Authorization": f"Bearer {self.token}", "User-Agent": "company-core-g2/0.8.1"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read(3_000_000))
        items = payload.get("data", payload.get("icons", [])) if isinstance(payload, dict) else payload
        candidates = []
        for item in items[:limit]:
            files = item.get("files") or {}
            download = files.get("json") if media_type == "lottie" else files.get("svg")
            if not download:
                continue
            candidates.append(AssetCandidate(
                candidate_id=f"lordicon:{item.get('family')}:{item.get('style')}:{item.get('index')}",
                provider="lordicon",
                source_type="vector",
                media_type=media_type,
                query=query,
                description=" ".join(filter(None, [item.get("title"), item.get("name")])),
                tags=[value for value in [item.get("family"), item.get("style"), item.get("name")] if value],
                source_url="https://lordicon.com/icons",
                download_url=download,
                preview_url=files.get("preview"),
                license="Lordicon free API license",
                width=512,
                height=512,
                premium=bool(item.get("premium")),
            ))
        return candidates
