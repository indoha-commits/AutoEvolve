from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class CompanyEnrichError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class CompanyEnrichClient:
    def __init__(self, api_key: str | None = None, base_url: str = "https://api.companyenrich.com"):
        self.api_key = api_key or os.getenv("CE_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        if not self.api_key:
            raise CompanyEnrichError("CE_API_KEY is not configured")

    def _authorization(self) -> str:
        value = self.api_key.strip()
        return value if value.lower().startswith("bearer ") else f"Bearer {value}"

    def _request(self, method: str, endpoint: str, *, query: dict[str, Any] | None = None, attempts: int = 3) -> dict:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": self._authorization(),
                "Accept": "application/json",
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
                    detail = parsed.get("message") or parsed.get("detail") or parsed.get("title") or raw[:500]
                except Exception:
                    pass
                if exc.code in {404, 409, 422, 429, 500, 502, 503, 504} and attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise CompanyEnrichError(
                    f"method={request.get_method()} url={url} status={exc.code} detail={detail}",
                    exc.code,
                ) from exc
            except (TimeoutError, urllib.error.URLError) as exc:
                if attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise CompanyEnrichError(f"method={request.get_method()} url={url} transport_error={exc}") from exc
        raise CompanyEnrichError(f"CompanyEnrich request failed method={request.get_method()} url={url}")

    def me(self) -> dict:
        return self._request("GET", "me")

    def enrich_company(self, domain: str, *, wait_for_enrichment: bool = True) -> dict:
        return self._request(
            "GET",
            "companies/enrich",
            query={"domain": domain, "waitForEnrichment": str(wait_for_enrichment).lower()},
        )

    def extract_company_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        base = payload.get("company") if isinstance(payload.get("company"), dict) else payload
        location = base.get("location") if isinstance(base.get("location"), dict) else base.get("address") if isinstance(base.get("address"), dict) else {}
        industries = _normalized_strings(base.get("industries"))
        categories = _normalized_strings(base.get("categories"))
        keywords = _normalized_strings(base.get("keywords"))
        technologies = _normalized_strings(base.get("technologies"))
        specialties = _normalized_strings(base.get("specialities") or base.get("specialties") or base.get("services") or base.get("products"))
        target_markets = _normalized_strings(base.get("targetMarkets") or base.get("markets") or base.get("customerIndustries"))
        social_media = base.get("social_media") if isinstance(base.get("social_media"), dict) else base.get("socialProfiles") if isinstance(base.get("socialProfiles"), dict) else {}
        domain = _domain_value(base.get("domain") or base.get("website") or payload.get("domain"))
        website = _website_value(base.get("website") or payload.get("website") or domain)
        description = _clean_text(base.get("description") or base.get("shortDescription") or base.get("tagline"))
        summary_line = _summary_line(base.get("name") or base.get("legalName"), description, specialties, keywords)
        operational_focus = _choose_focus(specialties, keywords, industries, categories)
        personalization_points = _personalization_points(
            description=description,
            specialties=specialties,
            keywords=keywords,
            target_markets=target_markets,
            location=location,
        )
        suggested_pain_points = _pain_points(personalization_points, operational_focus, industries, categories)
        return {
            "name": _clean_text(base.get("name") or base.get("legalName")),
            "domain": domain,
            "website": website,
            "description": description,
            "industry": _clean_text(base.get("industry")) or (industries[0] if industries else None) or (categories[0] if categories else None),
            "industries": industries[:10],
            "categories": categories[:10],
            "keywords": keywords[:12],
            "employee_count": base.get("employeeCount") or base.get("employees") or base.get("employee_count") or base.get("staff_count"),
            "employee_range": _clean_text(base.get("employeeRange") or base.get("employee_range") or base.get("size")),
            "revenue_range": _clean_text(base.get("revenueRange") or base.get("revenue_range")),
            "linkedin_url": _clean_url(base.get("linkedinUrl") or base.get("linkedin_url") or social_media.get("linkedin")),
            "location": {
                "city": _clean_text(location.get("city") or base.get("city")),
                "state": _clean_text(location.get("state") or location.get("region") or base.get("state")),
                "country": _clean_text(location.get("country") or base.get("country")),
            },
            "technologies": technologies[:20],
            "specialties": specialties[:10],
            "target_markets": target_markets[:10],
            "signals": {
                "summary_line": summary_line,
                "operational_focus": operational_focus,
                "personalization_points": personalization_points[:5],
                "suggested_pain_points": suggested_pain_points[:4],
            },
        }


def company_usage_profile(company_profile: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(company_profile, dict):
        return {}
    summary = company_profile.get("summary") if isinstance(company_profile.get("summary"), dict) else company_profile
    location = summary.get("location") if isinstance(summary.get("location"), dict) else {}
    signals = summary.get("signals") if isinstance(summary.get("signals"), dict) else {}
    name = _clean_text(summary.get("name"))
    domain = _domain_value(summary.get("domain") or summary.get("website"))
    industry = _clean_text(summary.get("industry"))
    specialties = _normalized_strings(summary.get("specialties"))
    keywords = _normalized_strings(summary.get("keywords"))
    employee_range = _clean_text(summary.get("employee_range"))
    employee_count = summary.get("employee_count")
    size = employee_range or (str(employee_count) if employee_count else None)
    return {
        "name": name,
        "domain": domain,
        "website": _website_value(summary.get("website") or domain),
        "industry": industry,
        "size": size,
        "location": {
            "city": _clean_text(location.get("city")),
            "state": _clean_text(location.get("state")),
            "country": _clean_text(location.get("country")),
        },
        "description": _clean_text(summary.get("description")),
        "specialties": specialties[:6],
        "keywords": keywords[:8],
        "summary_line": _clean_text(signals.get("summary_line")),
        "operational_focus": _clean_text(signals.get("operational_focus")),
        "personalization_points": _normalized_strings(signals.get("personalization_points"))[:5],
        "suggested_pain_points": _normalized_strings(signals.get("suggested_pain_points"))[:4],
        "linkedin_url": _clean_url(summary.get("linkedin_url")),
    }


def _normalized_strings(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    deduped: list[str] = []
    seen: set[str] = set()
    for item in values:
        if isinstance(item, dict):
            candidate = item.get("name") or item.get("label") or item.get("value") or item.get("title")
        else:
            candidate = item
        text = _clean_text(candidate)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    return deduped


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\n", " ").replace("\r", " ").split()).strip()
    return text or None


def _clean_url(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    if text.startswith(("http://", "https://")):
        return text
    return None


def _domain_value(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    normalized = text.lower()
    for prefix in ("https://", "http://"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    normalized = normalized.split("/", 1)[0].split("?", 1)[0].strip()
    if normalized.startswith("www."):
        normalized = normalized[4:]
    return normalized or None


def _website_value(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    if text.startswith(("http://", "https://")):
        return text.rstrip("/")
    domain = _domain_value(text)
    return f"https://{domain}" if domain else None


def _summary_line(name: Any, description: str | None, specialties: list[str], keywords: list[str]) -> str | None:
    company = _clean_text(name)
    if description:
        if company and description.lower().startswith(company.lower()):
            return description
        return f"{company}: {description}" if company else description
    if company and specialties:
        return f"{company} focuses on {', '.join(specialties[:3])}."
    if company and keywords:
        return f"{company} appears focused on {', '.join(keywords[:3])}."
    return company


def _choose_focus(specialties: list[str], keywords: list[str], industries: list[str], categories: list[str]) -> str | None:
    for group in (specialties, keywords, industries, categories):
        if group:
            return ", ".join(group[:3])
    return None


def _personalization_points(*, description: str | None, specialties: list[str], keywords: list[str], target_markets: list[str], location: dict[str, Any]) -> list[str]:
    points: list[str] = []
    if description:
        points.append(description)
    if specialties:
        points.append(f"Core services: {', '.join(specialties[:4])}")
    if target_markets:
        points.append(f"Target markets: {', '.join(target_markets[:3])}")
    city = _clean_text(location.get("city"))
    country = _clean_text(location.get("country"))
    if city or country:
        points.append(f"Location footprint: {', '.join([part for part in (city, country) if part])}")
    if keywords:
        points.append(f"Visible themes: {', '.join(keywords[:4])}")
    return points


def _pain_points(personalization_points: list[str], operational_focus: str | None, industries: list[str], categories: list[str]) -> list[str]:
    joined = " ".join(personalization_points + ([operational_focus] if operational_focus else []) + industries + categories).lower()
    points: list[str] = []
    if any(word in joined for word in ("transport", "logistics", "freight", "warehouse", "fulfillment", "supply chain")):
        points.append("handoff delays between teams, shipments, and customer updates")
        points.append("document and milestone visibility across operations")
    if any(word in joined for word in ("automation", "technology", "integration", "software", "platform")):
        points.append("manual status chasing across disconnected systems")
    if any(word in joined for word in ("compliance", "retail", "customs", "audit")):
        points.append("audit-ready document control and exception tracking")
    if not points:
        points.append("fragmented operational updates across client-facing teams")
    deduped: list[str] = []
    seen: set[str] = set()
    for item in points:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
