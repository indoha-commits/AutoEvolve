from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from .models import AssetCandidate


class CoverrProvider:
    """Search Coverr without resolving a download URL until a clip is selected."""

    endpoint = "https://api.coverr.co/videos"
    storage_endpoint = "https://api.coverr.co/storage/videos"

    def __init__(self, api_key: str | None = None, timeout: int = 20, opener=None):
        self.api_key = api_key or os.getenv("COVERR_API_KEY", "")
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    def search(self, query: str, limit: int = 5, media_type: str = "video") -> list[AssetCandidate]:
        if media_type != "video":
            return []
        if not self.api_key:
            raise RuntimeError("COVERR_API_KEY is not configured")
        limit = max(1, min(limit, 20))
        url = f"{self.endpoint}?{urllib.parse.urlencode({'query': query, 'is_vertical': 'true'})}"
        request = urllib.request.Request(url, headers={
            "API_KEY": self.api_key,
            "Accept": "application/json",
            "User-Agent": "company-core-g2/0.8.2",
        })
        with self.opener(request, timeout=self.timeout) as response:
            payload = json.loads(response.read(4_000_000))
        items = payload if isinstance(payload, list) else payload.get("videos") or payload.get("data") or []
        candidates = []
        for item in items[:limit]:
            identifier = str(item.get("id") or item.get("objectID") or "").strip()
            base_filename = str(item.get("base_filename") or "").strip()
            if not identifier or not base_filename:
                continue
            vertical = bool(item.get("is_vertical"))
            candidates.append(AssetCandidate(
                candidate_id=f"coverr-video:{identifier}",
                provider="coverr",
                source_type="stock",
                media_type="video",
                query=query,
                description=str(item.get("description") or item.get("title") or item.get("name") or ""),
                source_url=f"https://coverr.co/videos/{urllib.parse.quote(base_filename)}",
                download_url=f"{self.storage_endpoint}/{urllib.parse.quote(base_filename)}",
                preview_url=item.get("full_image_path"),
                photographer=item.get("contributor_name"),
                license="Coverr License",
                width=1080 if vertical else 1920,
                height=1920 if vertical else 1080,
                duration_seconds=float(item.get("duration") or 0) or None,
            ))
        return candidates
