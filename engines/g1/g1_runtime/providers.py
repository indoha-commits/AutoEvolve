from __future__ import annotations

import asyncio
import json
import re
import time
from io import BytesIO
from html import unescape
from json import JSONDecoder
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from .models import SearchResult, SourceEvidence
from .security import safe_public_url
from .security import normalize_text
from .store import CampaignStore


class JsonModel(Protocol):
    def complete_json(self, stage: str, system: str, user: str, *, campaign_id: str | None = None, writing: bool = False) -> dict: ...


def _parse_json(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        value = "\n".join(block.get("text", "") for block in value if isinstance(block, dict))
    if not isinstance(value, str) or not value.strip():
        raise ValueError("assistant content is empty")
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = JSONDecoder()
        for index, char in enumerate(text):
            if char == "{":
                try:
                    result, _ = decoder.raw_decode(text[index:])
                    if isinstance(result, dict):
                        return result
                except json.JSONDecodeError:
                    pass
    raise ValueError(f"no JSON object in model content: {text[:180]!r}")


class OmniRouteModel:
    def __init__(self, base_url: str, api_key: str, reasoning_model: str, writing_model: str,
                 fallback_model: str, store: CampaignStore, timeouts: tuple[int, ...] = (60, 90)):
        self.base_url, self.api_key = base_url.rstrip("/"), api_key
        self.reasoning_model, self.writing_model, self.fallback_model = reasoning_model, writing_model, fallback_model
        self.store, self.timeouts = store, timeouts

    def complete_json(self, stage: str, system: str, user: str, *, campaign_id: str | None = None,
                      writing: bool = False) -> dict:
        if not self.api_key:
            raise RuntimeError("CODING_API_KEY is not configured")
        preferred = self.writing_model if writing else self.reasoning_model
        models = [preferred] + ([self.fallback_model] if self.fallback_model != preferred else [])
        # Research claims are optional and fail soft. Do not spend several
        # minutes retrying route combinations before the deterministic pipeline
        # can continue. Generation stages receive one bounded fallback attempt.
        if stage == "research":
            plans = [(preferred, self.timeouts[0])]
        else:
            plans = [(model, self.timeouts[-1]) for model in models]
        last_error: Exception | None = None
        attempt = 0
        for model, timeout in plans:
            attempt += 1
            started = time.monotonic()
            try:
                result, usage, actual_model = self._request(model, system, user, timeout)
                latency = int((time.monotonic() - started) * 1000)
                self.store.model_run(campaign_id, stage, model, attempt, "pass", latency, usage,
                                     actual_model=actual_model)
                return result
            except Exception as exc:
                last_error = exc
                latency = int((time.monotonic() - started) * 1000)
                self.store.model_run(campaign_id, stage, model, attempt, "fail", latency, error=f"{type(exc).__name__}: {exc}")
                if attempt < len(plans):
                    time.sleep(min(2 ** (attempt - 1), 4))
        raise RuntimeError(f"OmniRoute failed after {attempt} attempts: {last_error}")

    def _request(self, model: str, system: str, user: str, timeout: int) -> tuple[dict, dict, str | None]:
        payload = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "response_format": {"type": "json_object"}, "temperature": 0.25, "stream": False}
        request = Request(self.base_url + "/chat/completions", data=json.dumps(payload).encode(),
                          headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8", errors="replace"))
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}: {exc.read(300).decode(errors='replace')}") from exc
        choices = body.get("choices") or []
        if not choices:
            raise ValueError(f"response contains no choices: {sorted(body)}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content in (None, "", []):
            calls = message.get("tool_calls") or []
            content = calls[0].get("function", {}).get("arguments") if calls else content
        return _parse_json(content), body.get("usage", {}), body.get("model")


AUTHORITY = {
    "unctad.org": (1.0, "primary_institution"), "wto.org": (1.0, "primary_institution"),
    "wcoomd.org": (1.0, "primary_institution"), "imo.org": (1.0, "primary_institution"),
    "worldbank.org": (0.92, "authoritative_institution"), "documents.worldbank.org": (0.98, "primary_report"),
    "afdb.org": (0.92, "authoritative_institution"), "trademarkafrica.com": (0.86, "regional_authority"),
    "gov.rw": (0.98, "government"), "rra.gov.rw": (0.98, "government"),
}


def authority_for(url: str) -> tuple[float, str]:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    for domain, value in AUTHORITY.items():
        if host == domain or host.endswith("." + domain):
            score, source_class = value
            if "/blog" in url.lower():
                return min(score, 0.78), "institutional_blog"
            return score, source_class
    return (0.64, "academic_or_secondary") if any(part in host for part in ("doi.org", ".edu", ".ac.")) else (0.38, "unknown_web")


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_")]
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", urlencode(query), ""))


def diverse_results(results: list[SearchResult], limit: int = 8, per_domain: int = 2) -> list[SearchResult]:
    selected: list[SearchResult] = []
    counts: dict[str, int] = {}
    for result in sorted(results, key=lambda row: row.authority, reverse=True):
        host = (urlparse(result.url).hostname or "").lower().removeprefix("www.")
        registrable = ".".join(host.split(".")[-2:]) if host else ""
        if counts.get(registrable, 0) >= per_domain:
            continue
        counts[registrable] = counts.get(registrable, 0) + 1
        selected.append(result)
        if len(selected) >= limit:
            break
    return selected


class SearXNGSearch:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        url = self.base_url + "/search?" + urlencode({"q": query, "format": "json", "language": "en"})
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=30) as response:
            payload = json.load(response)
        results, seen, seen_titles = [], set(), set()
        for item in payload.get("results", []):
            raw_url = item.get("url", "")
            try:
                safe_public_url(raw_url)
            except (ValueError, OSError):
                continue
            normalized = canonical_url(raw_url)
            normalized_title = normalize_text(item.get("title", ""))
            if normalized in seen or (normalized_title and normalized_title in seen_titles):
                continue
            seen.add(normalized)
            if normalized_title:
                seen_titles.add(normalized_title)
            score, source_class = authority_for(raw_url)
            results.append(SearchResult(title=item.get("title", ""), url=raw_url, snippet=item.get("content", ""),
                                        engine=item.get("engine"), authority=score, source_class=source_class))
        results.sort(key=lambda row: row.authority, reverse=True)
        return results[:limit]


class PageExtractor:
    def __init__(self, max_chars: int = 12_000):
        self.max_chars = max_chars

    def extract(self, result: SearchResult, evidence_id: str) -> SourceEvidence:
        safe_public_url(result.url)
        if urlparse(result.url).path.lower().endswith(".pdf") or self._remote_is_pdf(result.url):
            text = self._pdf(result.url)
        else:
            try:
                text = asyncio.run(self._crawl(result.url))
            except (ImportError, RuntimeError):
                text = self._basic(result.url)
        return SourceEvidence(id=evidence_id, title=result.title, url=result.url, authority=result.authority,
                              source_class=result.source_class, excerpt=text[:self.max_chars])

    @staticmethod
    def _remote_is_pdf(url: str) -> bool:
        try:
            request = Request(url, headers={"User-Agent": "Example Company-G1/1.0"}, method="HEAD")
            with urlopen(request, timeout=12) as response:
                safe_public_url(response.geturl())
                content_type = (response.headers.get("Content-Type") or "").lower()
                disposition = (response.headers.get("Content-Disposition") or "").lower()
                return "application/pdf" in content_type or ".pdf" in disposition
        except Exception:
            return False

    async def _crawl(self, url: str) -> str:
        from crawl4ai import AsyncWebCrawler
        async with AsyncWebCrawler() as crawler:
            page = await crawler.arun(url=url)
        if not getattr(page, "success", True):
            message = getattr(page, "error_message", None) or "Crawl4AI extraction failed"
            raise RuntimeError(message)
        markdown = getattr(page, "markdown", "") or ""
        if hasattr(markdown, "raw_markdown"):
            markdown = markdown.raw_markdown
        text = str(markdown).strip()
        if len(text) < 80:
            raise RuntimeError("Crawl4AI returned no usable page text")
        return text

    def _basic(self, url: str) -> str:
        with urlopen(Request(url, headers={"User-Agent": "Example Company-G1/1.0"}), timeout=25) as response:
            safe_public_url(response.geturl())
            if "application/pdf" in (response.headers.get("Content-Type") or "").lower():
                raw = response.read(20_000_001)
                return self._pdf_text(raw)
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("page exceeds extraction limit")
        html = raw.decode("utf-8", errors="replace")
        html = re.sub(r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>", " ", html, flags=re.I | re.S)
        return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()

    def _pdf(self, url: str) -> str:
        with urlopen(Request(url, headers={"User-Agent": "Example Company-G1/1.0"}), timeout=45) as response:
            safe_public_url(response.geturl())
            raw = response.read(20_000_001)
        return self._pdf_text(raw)

    def _pdf_text(self, raw: bytes) -> str:
        if len(raw) > 20_000_000:
            raise ValueError("PDF exceeds 20 MB extraction limit")
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(raw))
        chunks = []
        for page in reader.pages[:80]:
            chunks.append(page.extract_text() or "")
            if sum(len(chunk) for chunk in chunks) >= self.max_chars:
                break
        text = "\n".join(chunks).strip()
        if len(text) < 80:
            raise ValueError("PDF contains no usable text")
        return text
