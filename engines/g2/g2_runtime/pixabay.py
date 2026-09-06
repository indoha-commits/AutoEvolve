from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from .models import AssetCandidate


class PixabayProvider:
    endpoint = "https://pixabay.com/api/"
    video_endpoint = "https://pixabay.com/api/videos/"

    def __init__(self, api_key: str | None = None, timeout: int = 20):
        self.api_key = api_key or os.getenv("PIXABAY_API_KEY", "")
        self.timeout = timeout

    def search(self, query: str, limit: int = 5, media_type: str = "image") -> list[AssetCandidate]:
        if media_type not in {"image", "video"}:
            return []
        if not self.api_key:
            raise RuntimeError("PIXABAY_API_KEY is not configured")
        limit = max(3, min(limit, 15))
        if media_type == "video":
            return self._search_video(query, limit)
        url = f"{self.endpoint}?{urllib.parse.urlencode({'key': self.api_key, 'q': query, 'per_page': limit, 'orientation': 'vertical', 'image_type': 'photo', 'safesearch': 'true'})}"
        request = urllib.request.Request(url, headers={"User-Agent": "company-core-g2/0.8.1"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read(2_000_000))
        return [
            AssetCandidate(
                candidate_id=f"pixabay:{item['id']}",
                provider="pixabay",
                source_type="stock",
                query=query,
                description=item.get("tags", ""),
                source_url=item["pageURL"],
                download_url=item.get("largeImageURL") or item["webformatURL"],
                photographer=item.get("user"),
                license="Pixabay Content License",
                width=item.get("imageWidth") or item["webformatWidth"],
                height=item.get("imageHeight") or item["webformatHeight"],
            )
            for item in payload.get("hits", [])[:limit]
        ]

    def _search_video(self, query: str, limit: int) -> list[AssetCandidate]:
        url = f"{self.video_endpoint}?{urllib.parse.urlencode({'key': self.api_key, 'q': query, 'per_page': limit, 'video_type': 'film', 'safesearch': 'true'})}"
        request = urllib.request.Request(url, headers={"User-Agent": "company-core-g2/0.8.1"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read(4_000_000))
        candidates = []
        for item in payload.get("hits", [])[:limit]:
            renditions = item.get("videos") or {}
            available = [value for value in renditions.values() if value.get("url") and value.get("width") and value.get("height")]
            if not available:
                continue
            selected = max(
                available,
                key=lambda value: (
                    value["height"] >= value["width"],
                    value["width"] >= 1080 and value["height"] >= 1920,
                    min(value["width"] * value["height"], 1080 * 1920),
                ),
            )
            candidates.append(AssetCandidate(
                candidate_id=f"pixabay-video:{item['id']}",
                provider="pixabay",
                source_type="stock",
                media_type="video",
                query=query,
                description=item.get("tags", ""),
                tags=[value.strip() for value in item.get("tags", "").split(",") if value.strip()],
                source_url=item.get("pageURL"),
                download_url=selected["url"],
                preview_url=(renditions.get("tiny") or {}).get("thumbnail"),
                photographer=item.get("user"),
                license="Pixabay Content License",
                width=selected["width"],
                height=selected["height"],
                duration_seconds=float(item.get("duration") or 0) or None,
            ))
        return candidates
