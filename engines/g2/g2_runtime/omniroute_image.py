from __future__ import annotations

import base64
import io
import json
import os
import urllib.error
import urllib.request

from PIL import Image


class OmniRouteImageProvider:
    """OpenAI-compatible image endpoint. Only base64 image output is accepted."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None, timeout: int = 120, opener=None):
        self.base_url = (base_url or os.getenv("OMNIROUTE_BASE_URL", "http://127.0.0.1:20128/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("CODING_API_KEY", "")
        self.model = model or os.getenv("OMNIROUTE_IMAGE_MODEL", "")
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    def generate(self, prompt: str, size: str = "1024x1536", quality: str = "medium") -> tuple[bytes, dict]:
        if not self.configured:
            raise RuntimeError("CODING_API_KEY and OMNIROUTE_IMAGE_MODEL are required")
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "n": 1,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/images/generations",
            data=payload,
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "User-Agent": "company-core-g2/0.8.1"},
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                result = json.loads(response.read(24_000_000))
        except urllib.error.HTTPError as exc:
            detail = exc.read(2_000).decode("utf-8", "replace")
            raise RuntimeError(f"OmniRoute image HTTP {exc.code}: {detail}") from exc
        entry = (result.get("data") or [{}])[0]
        encoded = entry.get("b64_json")
        if not encoded:
            raise RuntimeError("OmniRoute did not return base64 image data; URL outputs are rejected")
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 20_000_000:
            raise RuntimeError("generated image exceeds 20 MB")
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        return raw, {"model": self.model, "revised_prompt": entry.get("revised_prompt")}
