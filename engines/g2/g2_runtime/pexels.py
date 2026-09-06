from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from .models import AssetCandidate


class PexelsProvider:
    """Read-only discovery adapter. Downloads remain a separate approved step."""

    endpoint = "https://api.pexels.com/v1/search"
    video_endpoint = "https://api.pexels.com/v1/videos/search"

    def __init__(self, api_key: str | None = None, timeout: int = 20):
        self.api_key = api_key or os.getenv("PEXELS_API_KEY", "")
        self.timeout = timeout

    def search(self, query: str, limit: int = 5, media_type: str = "image") -> list[AssetCandidate]:
        if media_type not in {"image", "video"}:
            return []
        if not self.api_key:
            raise RuntimeError("PEXELS_API_KEY is not configured")
        limit = max(1, min(limit, 15))
        if media_type == "video":
            return self._search_video(query, limit)
        url = f"{self.endpoint}?{urllib.parse.urlencode({'query': query, 'per_page': limit, 'orientation': 'portrait'})}"
        request = urllib.request.Request(url, headers={"Authorization": self.api_key, "User-Agent": "company-core-g2/0.8.1"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read(2_000_000))
        return [AssetCandidate(
                candidate_id=f"pexels:{item['id']}",
                photographer=item.get("photographer"),
                query=query,
                description=item.get("alt", ""),
                source_url=item["url"],
                download_url=item["src"].get("large2x") or item["src"]["original"],
                provider="pexels",
                source_type="stock",
                license="Pexels license",
                width=item["width"],
                height=item["height"],
            )
            for item in payload.get("photos", [])[:limit]
        ]

    def _search_video(self, query: str, limit: int) -> list[AssetCandidate]:
        url = f"{self.video_endpoint}?{urllib.parse.urlencode({'query': query, 'per_page': limit, 'orientation': 'portrait', 'size': 'medium'})}"
        request = urllib.request.Request(url, headers={"Authorization": self.api_key, "User-Agent": "company-core-g2/0.8.1"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read(4_000_000))
        candidates = []
        for item in payload.get("videos", [])[:limit]:
            files = [
                value for value in item.get("video_files", [])
                if value.get("file_type") == "video/mp4" and value.get("link")
                and value.get("width") and value.get("height")
            ]
            if not files:
                continue
            # Prefer a portrait file at or above delivery resolution without
            # selecting an unnecessarily huge source when a clean 1080p file exists.
            selected = max(
                files,
                key=lambda value: (
                    value["height"] >= value["width"],
                    value["width"] >= 1080 and value["height"] >= 1920,
                    min(value["width"] * value["height"], 1080 * 1920),
                ),
            )
            candidates.append(AssetCandidate(
                candidate_id=f"pexels-video:{item['id']}:{selected.get('id', 'file')}",
                provider="pexels",
                source_type="stock",
                media_type="video",
                query=query,
                description=item.get("url", "").rsplit("/", 1)[-1].replace("-", " "),
                source_url=item.get("url"),
                download_url=selected["link"],
                preview_url=item.get("image"),
                photographer=(item.get("user") or {}).get("name"),
                license="Pexels license",
                width=selected["width"],
                height=selected["height"],
                duration_seconds=float(item.get("duration") or 0) or None,
            ))
        return candidates
