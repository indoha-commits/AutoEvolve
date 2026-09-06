from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from services.company_enrich import (
    _clean_text,
    _clean_url,
    _domain_value,
    _normalized_strings,
    _pain_points,
    _personalization_points,
    _summary_line,
    _website_value,
    _choose_focus,
)


class PeopleDataLabsError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class PeopleDataLabsClient:
    def __init__(self, api_key: str | None = None, base_url: str = "https://api.peopledatalabs.com/v5"):
        self.api_key = api_key or os.getenv("PDL_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        if not self.api_key:
            raise PeopleDataLabsError("PDL_API_KEY is not configured")

    def _request(self, method: str, endpoint: str, *, query: dict[str, Any] | None = None, attempts: int = 3) -> dict:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
        request = urllib.request.Request(
            url,
            headers={
                "X-Api-Key": self.api_key.strip(),
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method=method.upper(),
        )
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = str(exc)
                try:
                    raw = exc.read().decode("utf-8")
                    parsed = json.loads(raw)
                    detail = parsed.get("error") or parsed.get("message") or parsed.get("detail") or raw[:500]
                except Exception:
                    pass
                if exc.code in {404, 409, 422, 429, 500, 502, 503, 504} and attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise PeopleDataLabsError(
                    f"method={request.get_method()} url={url} status={exc.code} detail={detail}",
                    exc.code,
                ) from exc
            except (TimeoutError, urllib.error.URLError) as exc:
                if attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise PeopleDataLabsError(f"method={request.get_method()} url={url} transport_error={exc}") from exc
        raise PeopleDataLabsError(f"People Data Labs request failed method={request.get_method()} url={url}")

    def enrich_company(self, website: str, *, pretty: bool = False) -> dict:
        return self._request("GET", "company/enrich", query={"website": website, "pretty": str(pretty).lower()})

    def extract_company_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
        keywords = _normalized_strings(payload.get("tags"))
        specialties = _normalized_strings(payload.get("employee_count_by_role"))
        social_profiles = payload.get("profiles") if isinstance(payload.get("profiles"), list) else []
        linkedin_url = None
        for item in social_profiles:
            value = _clean_text(item)
            if value and "linkedin.com/company/" in value:
                linkedin_url = value if value.startswith(("http://", "https://")) else f"https://{value}"
                break
        description = _clean_text(payload.get("summary") or payload.get("headline"))
        industries = _normalized_strings([payload.get("industry_v2"), payload.get("industry")])
        categories = _normalized_strings(payload.get("category") or payload.get("categories"))
        operational_focus = _choose_focus([], keywords, industries, categories)
        personalization_points = _personalization_points(
            description=description,
            specialties=[],
            keywords=keywords,
            target_markets=[],
            location={"city": location.get("locality"), "country": location.get("country")},
        )
        suggested_pain_points = _pain_points(personalization_points, operational_focus, industries, categories)
        domain = _domain_value(payload.get("website"))
        website = _website_value(payload.get("website"))
        return {
            "name": _clean_text(payload.get("display_name") or payload.get("name")),
            "domain": domain,
            "website": website,
            "description": description,
            "industry": industries[0] if industries else None,
            "industries": industries[:10],
            "categories": categories[:10],
            "keywords": keywords[:12],
            "employee_count": payload.get("employee_count"),
            "employee_range": _clean_text(payload.get("size")),
            "revenue_range": _clean_text(payload.get("inferred_revenue")),
            "linkedin_url": _clean_url(linkedin_url),
            "location": {
                "city": _clean_text(location.get("locality") or location.get("name")),
                "state": _clean_text(location.get("region")),
                "country": _clean_text(location.get("country")),
            },
            "technologies": [],
            "specialties": [],
            "target_markets": [],
            "signals": {
                "summary_line": _summary_line(payload.get("display_name") or payload.get("name"), description, [], keywords),
                "operational_focus": operational_focus,
                "personalization_points": personalization_points[:5],
                "suggested_pain_points": suggested_pain_points[:4],
            },
            "source_ids": {
                "pdl_id": _clean_text(payload.get("id")),
            },
        }
