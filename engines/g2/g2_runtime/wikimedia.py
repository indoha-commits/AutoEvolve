from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request

from .models import AssetCandidate


def _plain(value: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value or ""))).strip()


class WikimediaProvider:
    """Read-only Wikimedia Commons discovery with per-file license metadata."""

    endpoint = "https://commons.wikimedia.org/w/api.php"

    def __init__(self, timeout: int = 5):
        self.timeout = timeout

    def search(self, query: str, limit: int = 5, media_type: str = "image") -> list[AssetCandidate]:
        if media_type not in {"image", "video"}:
            return []
        limit = max(1, min(limit, 20))
        search = f"{query} filetype:{'video' if media_type == 'video' else 'bitmap'}"
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrnamespace": 6,
            "gsrlimit": limit,
            "gsrsearch": search,
            "prop": "imageinfo",
            "iiprop": "url|mime|size|mediatype|extmetadata",
            "iiurlwidth": 720,
            "origin": "*",
        }
        request = urllib.request.Request(
            f"{self.endpoint}?{urllib.parse.urlencode(params)}",
            headers={"User-Agent": "Example Company-G2/0.8.1 (media discovery)"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read(5_000_000))
        candidates = []
        for page in (payload.get("query", {}).get("pages") or {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            metadata = info.get("extmetadata") or {}
            license_name = _plain((metadata.get("LicenseShortName") or {}).get("value"))
            if not license_name:
                continue
            mime = info.get("mime", "")
            actual_type = "video" if mime.startswith("video/") else "image"
            if actual_type != media_type:
                continue
            width = int(info.get("width") or 1)
            height = int(info.get("height") or 1)
            candidates.append(AssetCandidate(
                candidate_id=f"wikimedia:{page.get('pageid')}",
                provider="wikimedia",
                source_type="stock",
                media_type=actual_type,
                query=query,
                description=" ".join(filter(None, [
                    page.get("title", "").removeprefix("File:"),
                    _plain((metadata.get("ImageDescription") or {}).get("value")),
                ])),
                source_url=info.get("descriptionurl"),
                download_url=info.get("url"),
                preview_url=info.get("thumburl"),
                photographer=_plain((metadata.get("Artist") or {}).get("value")) or None,
                license=f"Wikimedia Commons: {license_name}",
                width=max(width, 1),
                height=max(height, 1),
            ))
        return candidates
