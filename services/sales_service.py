from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.utils import parseaddr
from html import escape, unescape
from typing import Any

from core.sales_store import (
    add_event, claim_draft_for_send, contact_profile_for_lead, count_company_profile_attempts, create_draft, get_company_profile,
    get_draft, get_draft_by_provider_message_id, get_lead, get_lead_by_email, list_leads, merge_lead_metadata, normalize_company_domain, mark_replied, mark_sent,
    record_contact_resolution, release_send_claim, score_lead, suppress_lead,
    update_lead, upsert_company_profile, upsert_lead,
)
from services.apollo import ApolloClient, ApolloError
from services.company_enrich import CompanyEnrichClient, CompanyEnrichError, company_usage_profile
from services.pdl import PeopleDataLabsClient, PeopleDataLabsError
from services.hunter import HunterClient, HunterError
from services.prospeo import ProspeoClient, ProspeoError
from core.branding import company_logo_url, company_name, company_public_url
from services.lusha import LushaClient, LushaError


RESEND_SEND_URL = "https://api.resend.com/emails"
RESEND_RECEIVING_URL = "https://api.resend.com/emails/receiving"
RESEND_USER_AGENT = "company-sales/0.8"
RESEND_WEBHOOK_TOLERANCE_SECONDS = 300


def _provider_name(provider: str | None) -> str:
    value = str(provider or "hunter").strip().lower()
    if value not in {"hunter", "apollo", "prospeo", "lusha"}:
        raise ValueError("unsupported provider")
    return value


def import_hunter_domain(domain: str, limit: int = 5) -> list[dict]:
    results = []
    for item in HunterClient().domain_search(domain, limit):
        verification = item.get("verification") or {}
        lead, created = upsert_lead({
            "email": item.get("value"),
            "full_name": " ".join(filter(None, [item.get("first_name"), item.get("last_name")])),
            "job_title": item.get("position"),
            "company": item.get("company"),
            "company_domain": domain,
            "source": "hunter",
            "source_detail": "domain_search",
            "verification_status": verification.get("status"),
            "metadata": {"hunter_sources": item.get("sources", [])[:5]},
        })
        results.append({"lead": lead, "created": created})
    return results


def import_apollo_domain(domain: str, limit: int = 5) -> list[dict]:
    client = ApolloClient()
    people = client.people_search(domain, limit)
    details = []
    for item in people[: min(max(limit, 1), 10)]:
        apollo_id = item.get("person_id") or item.get("id")
        if apollo_id:
            details.append({"id": apollo_id})
    matches = client.bulk_match(details) if details else []
    matches_by_id = {
        str(item.get("id") or item.get("person_id") or ""): item
        for item in matches
        if isinstance(item, dict)
    }
    results = []
    for item in people[: min(max(limit, 1), 10)]:
        apollo_id = str(item.get("person_id") or item.get("id") or "")
        enriched = matches_by_id.get(apollo_id, {})
        first_name = enriched.get("first_name") or item.get("first_name") or item.get("first_name_for_emails")
        last_name = enriched.get("last_name") or item.get("last_name") or item.get("last_name_for_emails")
        organization = enriched.get("organization") if isinstance(enriched.get("organization"), dict) else {}
        raw_org = item.get("organization") if isinstance(item.get("organization"), dict) else {}
        email = enriched.get("email") or item.get("email")
        lead, created = upsert_lead({
            "email": email,
            "full_name": " ".join(filter(None, [first_name, last_name])) or enriched.get("name") or item.get("name"),
            "job_title": enriched.get("title") or item.get("title"),
            "company": organization.get("name") or raw_org.get("name") or item.get("organization_name"),
            "company_domain": organization.get("primary_domain") or raw_org.get("primary_domain") or domain,
            "country": enriched.get("country") or item.get("country"),
            "source": "apollo",
            "source_detail": "people_search",
            "verification_status": enriched.get("email_status") or item.get("email_status"),
            "metadata": {
                "apollo_person_id": apollo_id or None,
                "apollo_linkedin_url": enriched.get("linkedin_url") or item.get("linkedin_url"),
            },
        })
        results.append({"lead": lead, "created": created})
    return results


def _normalize_prospeo_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_entries = payload.get("results") if isinstance(payload.get("results"), list) else payload.get("people") if isinstance(payload.get("people"), list) else []
    normalized: list[dict[str, Any]] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        person = item.get("person") if isinstance(item.get("person"), dict) else item
        company = item.get("company") if isinstance(item.get("company"), dict) else person.get("company") if isinstance(person.get("company"), dict) else {}
        normalized.append({"person": person, "company": company})
    return normalized


def _company_candidate_key(
    domain: Any,
    company_name: Any,
    *,
    provider: str | None = None,
    provider_company_id: Any = None,
) -> str | None:
    normalized_domain = normalize_company_domain(domain)
    if normalized_domain:
        return f"domain:{normalized_domain}"
    normalized_name = "".join(character for character in str(company_name or "").lower() if character.isalnum())
    if normalized_name:
        return f"name:{normalized_name}"
    normalized_id = str(provider_company_id or "").strip()
    if provider and normalized_id:
        return f"{provider}:{normalized_id}"
    return None


def _store_company_candidates(
    candidates: list[dict[str, Any]],
    *,
    provider: str,
    applied_filters: dict[str, Any],
    limit: int,
) -> list[dict]:
    existing_by_key: dict[str, dict] = {}
    for lead in list_leads(limit=200):
        metadata = lead.get("metadata") if isinstance(lead.get("metadata"), dict) else {}
        key = _company_candidate_key(
            lead.get("company_domain"),
            lead.get("company"),
            provider=str(lead.get("source") or ""),
            provider_company_id=metadata.get("provider_company_id"),
        )
        if key:
            existing_by_key.setdefault(key, lead)

    results: list[dict] = []
    seen: set[str] = set()
    result_limit = min(max(limit, 1), 25)
    for candidate in candidates:
        company_name = candidate.get("company_name")
        provider_company_id = candidate.get("provider_company_id")
        normalized_domain = normalize_company_domain(candidate.get("company_domain"))
        key = _company_candidate_key(
            normalized_domain,
            company_name,
            provider=provider,
            provider_company_id=provider_company_id,
        )
        if not key or key in seen:
            continue
        # Domain-less companies cannot use the later explicit contact lookup.
        if not normalized_domain:
            continue
        seen.add(key)
        existing = existing_by_key.get(key)
        if existing:
            results.append({"lead": existing, "created": False})
        else:
            lead, created = upsert_lead({
                "full_name": company_name,
                "company": company_name,
                "company_domain": normalized_domain,
                "country": candidate.get("country"),
                "source": provider,
                "source_detail": "company_market_research",
                "message": f"Company discovered through {provider.title()} market research; contact lookup not run.",
                "metadata": {
                    "company_candidate": True,
                    "company_identity": key,
                    "provider_company_id": provider_company_id,
                    "research_filters": applied_filters,
                },
            })
            existing_by_key[key] = lead
            results.append({"lead": lead, "created": created})
        if len(results) >= result_limit:
            break
    return results


def _store_prospeo_entries(entries: list[dict[str, Any]], *, applied_filters: dict[str, Any], message: str) -> list[dict]:
    results = []
    for entry in entries:
        person = entry.get("person") if isinstance(entry.get("person"), dict) else {}
        company = entry.get("company") if isinstance(entry.get("company"), dict) else {}
        location_value = person.get("location") if isinstance(person.get("location"), dict) else {}
        email_info = person.get("email") if isinstance(person.get("email"), dict) else {}
        phone_info = person.get("mobile") if isinstance(person.get("mobile"), dict) else {}
        lead, created = upsert_lead({
            "full_name": person.get("full_name") or " ".join(filter(None, [person.get("first_name"), person.get("last_name")])) or company.get("name"),
            "job_title": person.get("current_job_title") or person.get("headline"),
            "company": company.get("name"),
            "company_domain": company.get("website") or company.get("domain"),
            "country": location_value.get("country") or person.get("country"),
            "phone": phone_info.get("number"),
            "source": "prospeo",
            "source_detail": "search_person",
            "verification_status": email_info.get("status"),
            "message": message,
            "metadata": {
                "prospeo_person_id": person.get("person_id"),
                "prospeo_company_id": company.get("company_id"),
                "prospeo_linkedin_url": person.get("linkedin_url"),
                "research_filters": applied_filters,
            },
        })
        results.append({"lead": lead, "created": created})
    return results


def research_prospeo_leads(
    *,
    job_title: str,
    service_keywords: list[str] | None = None,
    location: str | None = None,
    company_size: str | None = None,
    limit: int = 5,
) -> list[dict]:
    normalized_title = job_title.strip()
    if len(normalized_title) < 2:
        raise ValueError("job_title must be at least 2 characters")
    normalized_location = location.strip() if location else None
    normalized_size = company_size.strip() if company_size else None
    if normalized_size and normalized_size not in {"11-50", "51-100", "101-200", "201-500", "501-1000", "1001-5000"}:
        raise ValueError("company_size is not a supported Prospeo range")
    client = ProspeoClient()
    keywords = [item.strip() for item in (service_keywords or []) if str(item).strip()]
    search_plans: list[dict[str, Any]] = []

    def add_plan(*, use_keywords: bool, use_location: bool, use_company_size: bool) -> None:
        filters: dict[str, Any] = {
            "person_job_title": {
                "include": [normalized_title],
                "match_mode": "CONTAINS",
            },
        }
        if use_keywords and keywords:
            filters["company_keywords"] = {
                "include": keywords[:5],
                "include_all": False,
                "search_everywhere": True,
            }
        if use_location and normalized_location:
            filters["company_location_search"] = {"include": [normalized_location]}
        if use_company_size and normalized_size:
            filters["company_headcount_range"] = [normalized_size]
        if filters not in search_plans:
            search_plans.append(filters)

    add_plan(use_keywords=True, use_location=True, use_company_size=True)
    add_plan(use_keywords=True, use_location=False, use_company_size=True)
    add_plan(use_keywords=False, use_location=True, use_company_size=True)
    add_plan(use_keywords=True, use_location=False, use_company_size=False)
    add_plan(use_keywords=False, use_location=False, use_company_size=True)
    add_plan(use_keywords=False, use_location=False, use_company_size=False)

    entries: list[dict[str, Any]] = []
    applied_filters = search_plans[0]
    for filters in search_plans:
        payload = client.search_person(filters, page=1)
        entries = _normalize_prospeo_entries(payload)
        applied_filters = filters
        if entries:
            break
    return _store_prospeo_entries(
        entries[: min(max(limit, 1), 25)],
        applied_filters=applied_filters,
        message="Lead imported from Prospeo research search",
    )


def research_prospeo_market_leads(*, industry: str, location: str | None = None, limit: int = 5) -> list[dict]:
    normalized_industry = industry.strip()
    if len(normalized_industry) < 2:
        raise ValueError("industry must be at least 2 characters")
    normalized_location = location.strip() if location else None
    client = ProspeoClient()
    filters: dict[str, Any] = {
        "company_keywords": {
            "include": [normalized_industry],
            "include_all": False,
            "search_everywhere": True,
        },
    }
    if normalized_location:
        filters["company_location_search"] = {"include": [normalized_location]}
    payload = client.search_person(filters, page=1)
    candidates = []
    for entry in _normalize_prospeo_entries(payload):
        company = entry.get("company") if isinstance(entry.get("company"), dict) else {}
        person = entry.get("person") if isinstance(entry.get("person"), dict) else {}
        person_location = person.get("location") if isinstance(person.get("location"), dict) else {}
        candidates.append({
            "company_name": company.get("name"),
            "company_domain": company.get("website") or company.get("domain"),
            "provider_company_id": company.get("company_id"),
            "country": person_location.get("country") or person.get("country"),
        })
    return _store_company_candidates(
        candidates,
        provider="prospeo",
        applied_filters=filters,
        limit=limit,
    )


def _coerce_suggestion_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized = []
    for item in values:
        if isinstance(item, str) and item.strip():
            normalized.append(item.strip())
            continue
        if isinstance(item, dict):
            for key in ("label", "name", "title", "value", "country"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    normalized.append(value.strip())
                    break
    return normalized[:25]


def research_prospeo_suggestions(*, kind: str, query: str) -> list[str]:
    normalized_kind = kind.strip().lower()
    normalized_query = query.strip()
    if normalized_kind not in {"job_title", "location", "industry"}:
        raise ValueError("unsupported suggestion kind")
    if len(normalized_query) < 1:
        raise ValueError("query is required")
    client = ProspeoClient()
    if normalized_kind == "job_title":
        payload = client.search_suggestions(job_title=normalized_query)
        keys = ["job_title_suggestions", "job_titles", "suggestions"]
    elif normalized_kind == "location":
        payload = client.search_suggestions(location=normalized_query)
        keys = ["location_suggestions", "locations", "suggestions"]
    else:
        payload = client.search_suggestions(industry=normalized_query)
        keys = ["industry_suggestions", "industries", "suggestions"]
    for key in keys:
        value = payload.get(key)
        normalized = _coerce_suggestion_strings(value)
        if normalized:
            return normalized
    return []


def research_lusha_suggestions(*, kind: str, query: str) -> list[str]:
    normalized_kind = kind.strip().lower()
    normalized_query = query.strip()
    if normalized_kind == "job_title":
        return []
    if normalized_kind not in {"location", "industry", "company_size"}:
        raise ValueError("unsupported suggestion kind")
    client = LushaClient()
    if normalized_kind == "location":
        payload = client.contact_filter_values("locations", normalized_query if len(normalized_query) >= 2 else None)
    elif normalized_kind == "industry":
        payload = client.company_filter_values("industriesLabels")
        values = _coerce_suggestion_strings(payload.get("values"))
        if len(normalized_query) >= 2:
            lowered = normalized_query.lower()
            return [value for value in values if lowered in value.lower()][:25]
        return values
    else:
        payload = client.company_filter_values("sizes")
    return _coerce_suggestion_strings(payload.get("values"))


def research_lusha_leads(
    *,
    job_title: str | None = None,
    service_keywords: list[str] | None = None,
    location: str | None = None,
    company_size: str | None = None,
    limit: int = 5,
) -> list[dict]:
    normalized_title = job_title.strip() if job_title else None
    if normalized_title and len(normalized_title) < 2:
        raise ValueError("job_title must be at least 2 characters")
    normalized_location = location.strip() if location else None
    normalized_size = company_size.strip() if company_size else None
    client = LushaClient()
    payload = client.prospect_contacts(
        job_title=normalized_title,
        service_keywords=service_keywords,
        location=normalized_location,
        company_size=normalized_size,
        limit=limit,
    )
    request_id = str(payload.get("requestId") or "")
    previews = payload.get("results") if isinstance(payload.get("results"), list) else payload.get("contacts") if isinstance(payload.get("contacts"), list) else []
    contact_ids = [str(item.get("id")) for item in previews if isinstance(item, dict) and item.get("id")]
    enriched_payload = client.enrich_contacts(request_id, contact_ids) if request_id and contact_ids else {}
    enriched_contacts = enriched_payload.get("contacts") if isinstance(enriched_payload.get("contacts"), list) else enriched_payload.get("results") if isinstance(enriched_payload.get("results"), list) else []
    enriched_by_id = {str(item.get("id") or item.get("contactId") or ""): item for item in enriched_contacts if isinstance(item, dict)}
    results = []
    for item in previews[: min(max(limit, 1), 25)]:
        if not isinstance(item, dict):
            continue
        matched = enriched_by_id.get(str(item.get("id") or ""), {})
        job = matched.get("jobTitle") if isinstance(matched.get("jobTitle"), dict) else item.get("jobTitle") if isinstance(item.get("jobTitle"), dict) else {}
        company = matched.get("company") if isinstance(matched.get("company"), dict) else item.get("company") if isinstance(item.get("company"), dict) else {}
        emails = matched.get("emails") if isinstance(matched.get("emails"), list) else []
        phones = matched.get("phones") if isinstance(matched.get("phones"), list) else []
        email_value = next((entry.get("value") for entry in emails if isinstance(entry, dict) and entry.get("value")), None)
        phone_value = next((entry.get("value") for entry in phones if isinstance(entry, dict) and entry.get("value")), None)
        lead, created = upsert_lead({
            "email": email_value,
            "full_name": " ".join(filter(None, [matched.get("firstName") or item.get("firstName"), matched.get("lastName") or item.get("lastName")])) or company.get("name"),
            "job_title": job.get("title") or item.get("headline"),
            "company": company.get("name"),
            "company_domain": company.get("domain"),
            "country": company.get("location", {}).get("country") if isinstance(company.get("location"), dict) else None,
            "phone": phone_value,
            "source": "lusha",
            "source_detail": "contacts_prospecting",
            "message": "Lead imported from Lusha research search",
            "metadata": {
                "lusha_contact_id": item.get("id"),
                "lusha_request_id": request_id or None,
                "lusha_linkedin_url": matched.get("linkedinUrl") or item.get("linkedinUrl"),
                "research_filters": {
                    "job_title": normalized_title,
                    "service_keywords": [str(x).strip() for x in (service_keywords or []) if str(x).strip()][:5],
                    "location": normalized_location,
                    "company_size": normalized_size,
                },
            },
        })
        results.append({"lead": lead, "created": created})
    return results


def research_lusha_market_leads(*, industry: str, location: str | None = None, limit: int = 5) -> list[dict]:
    normalized_industry = industry.strip()
    if len(normalized_industry) < 2:
        raise ValueError("industry must be at least 2 characters")
    normalized_location = location.strip() if location else None
    client = LushaClient()
    payload = client.prospect_contacts(
        job_title=None,
        service_keywords=[normalized_industry],
        location=normalized_location,
        company_size=None,
        limit=limit,
    )
    previews = payload.get("results") if isinstance(payload.get("results"), list) else payload.get("contacts") if isinstance(payload.get("contacts"), list) else []
    candidates = []
    for item in previews:
        if not isinstance(item, dict):
            continue
        company = item.get("company") if isinstance(item.get("company"), dict) else {}
        company_location = company.get("location") if isinstance(company.get("location"), dict) else {}
        candidates.append({
            "company_name": company.get("name") or item.get("companyName"),
            "company_domain": company.get("domain") or item.get("companyDomain"),
            "provider_company_id": company.get("id") or company.get("companyId") or item.get("companyId"),
            "country": company_location.get("country") or item.get("country"),
        })
    return _store_company_candidates(
        candidates,
        provider="lusha",
        applied_filters={"industry": normalized_industry, "location": normalized_location},
        limit=limit,
    )


def research_apollo_market_leads(*, industry: str, location: str | None = None, limit: int = 5) -> list[dict]:
    normalized_industry = industry.strip()
    if len(normalized_industry) < 2:
        raise ValueError("industry must be at least 2 characters")
    normalized_location = location.strip() if location else None
    client = ApolloClient()
    search_limit = min(max(limit * 2, limit, 1), 10)
    people = client.people_market_search(industry=normalized_industry, location=normalized_location, limit=search_limit)
    candidates = []
    for item in people:
        if not isinstance(item, dict):
            continue
        organization = item.get("organization") if isinstance(item.get("organization"), dict) else {}
        candidates.append({
            "company_name": organization.get("name") or item.get("organization_name"),
            "company_domain": organization.get("primary_domain") or item.get("organization_domain"),
            "provider_company_id": organization.get("id") or item.get("organization_id"),
            "country": organization.get("country") or item.get("country"),
        })
    return _store_company_candidates(
        candidates,
        provider="apollo",
        applied_filters={"industry": normalized_industry, "location": normalized_location},
        limit=limit,
    )


def research_market_leads(*, industry: str, location: str | None = None, limit_per_provider: int = 5) -> dict[str, Any]:
    normalized_industry = industry.strip()
    normalized_location = location.strip() if location else None
    if len(normalized_industry) < 2:
        raise ValueError("industry must be at least 2 characters")

    provider_calls: list[tuple[str, Any]] = [
        ("prospeo", lambda: research_prospeo_market_leads(industry=normalized_industry, location=normalized_location, limit=limit_per_provider)),
        ("apollo", lambda: research_apollo_market_leads(industry=normalized_industry, location=normalized_location, limit=limit_per_provider)),
        ("lusha", lambda: research_lusha_market_leads(industry=normalized_industry, location=normalized_location, limit=limit_per_provider)),
    ]
    results: list[dict] = []
    warnings: list[str] = []
    providers: dict[str, int] = {}

    for provider, fetch in provider_calls:
        try:
            provider_results = fetch()
            providers[provider] = len(provider_results)
            results.extend(provider_results)
        except (ProspeoError, ApolloError, LushaError, ValueError) as exc:
            providers[provider] = 0
            warnings.append(f"{provider}: {exc}")

    deduped: list[dict] = []
    seen: set[str] = set()
    for item in results:
        lead = item.get("lead") if isinstance(item, dict) else None
        if not isinstance(lead, dict):
            continue
        metadata = lead.get("metadata") if isinstance(lead.get("metadata"), dict) else {}
        key = _company_candidate_key(
            lead.get("company_domain"),
            lead.get("company"),
            provider=str(lead.get("source") or ""),
            provider_company_id=metadata.get("provider_company_id"),
        ) or f"lead:{lead.get('id')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return {
        "results": deduped,
        "providers": providers,
        "warnings": warnings,
        "mode": "company_first",
        "contact_enrichment": "on_demand",
        "requested": {"industry": normalized_industry, "location": normalized_location, "limit_per_provider": limit_per_provider},
    }


def _contact_payload_for_lead(lead: dict) -> dict[str, Any]:
    profile = contact_profile_for_lead(lead)
    return {
        "email": profile["email"],
        "phone": profile["phone"],
        "whatsapp_candidate": profile["whatsapp_candidate"],
        "email_found": profile["email_found"],
        "phone_found": profile["phone_found"],
        "email_ready": profile["email_ready"],
        "whatsapp_ready": profile["whatsapp_ready"],
        "ready_for_outreach": profile["ready_for_outreach"],
        "needs_generic_fallback": profile["needs_generic_fallback"],
        "verification_status": profile["verification_status"],
    }


def _company_candidate_for_domain(domain: str) -> dict | None:
    normalized_domain = normalize_company_domain(domain)
    if not normalized_domain:
        return None
    for lead in list_leads(limit=200):
        metadata = lead.get("metadata") if isinstance(lead.get("metadata"), dict) else {}
        if metadata.get("company_candidate") and normalize_company_domain(lead.get("company_domain")) == normalized_domain:
            return lead
    return None


def _join_domain_contacts(domain: str, results: list[dict]) -> list[dict]:
    candidate = _company_candidate_for_domain(domain)
    if not candidate:
        return results
    candidate_metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
    linked_ids = [str(value) for value in candidate_metadata.get("contact_lead_ids", []) if value]
    joined: list[dict] = []
    for result in results:
        lead = result.get("lead") if isinstance(result, dict) else None
        if not isinstance(lead, dict) or not lead.get("id"):
            joined.append(result)
            continue
        updates = {}
        for field in ("company", "country"):
            if not lead.get(field) and candidate.get(field):
                updates[field] = candidate.get(field)
        if not lead.get("company_domain"):
            updates["company_domain"] = normalize_company_domain(domain)
        if updates:
            update_lead(lead["id"], **updates)
        merge_lead_metadata(lead["id"], {
            "company_discovery": {
                "lead_id": candidate.get("id"),
                "source": candidate.get("source"),
                "source_detail": candidate.get("source_detail"),
                "research_filters": candidate_metadata.get("research_filters") or {},
                "provider_company_id": candidate_metadata.get("provider_company_id"),
            }
        })
        if lead["id"] not in linked_ids:
            linked_ids.append(lead["id"])
        updated = get_lead(lead["id"]) or lead
        joined.append({**result, "lead": updated, "company_candidate_id": candidate.get("id")})
    merge_lead_metadata(candidate["id"], {"contact_lead_ids": linked_ids})
    return joined


def import_domain(domain: str, limit: int = 5, provider: str = "hunter") -> list[dict]:
    chosen = _provider_name(provider)
    if chosen == "apollo":
        results = import_apollo_domain(domain, limit)
    else:
        results = import_hunter_domain(domain, limit)
    return _join_domain_contacts(domain, results)


def pull_inbound_leads() -> dict:
    url = os.getenv("SALES_INBOX_URL", "").strip()
    token = os.getenv("SALES_INBOX_TOKEN", "").strip()
    if not url or not token:
        raise ValueError("SALES_INBOX_URL and SALES_INBOX_TOKEN are not configured")
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    submissions = payload.get("leads") if isinstance(payload, dict) else payload
    if not isinstance(submissions, list):
        raise ValueError("website lead inbox must return a list or {leads: [...]}" )
    imported = 0
    updated = 0
    auto_contact_sent = 0
    auto_contact_attempted = 0
    for submission in submissions[:200]:
        if not isinstance(submission, dict):
            continue
        submission.setdefault("source", "website")
        lead, created = upsert_lead(submission)
        imported += int(created)
        updated += int(not created)
        auto = maybe_auto_contact_lead(lead["id"], created=created)
        if auto and auto.get("enabled"):
            auto_contact_attempted += 1
            auto_contact_sent += int(bool(auto.get("sent")))
    return {
        "ok": True,
        "received": len(submissions[:200]),
        "created": imported,
        "updated": updated,
        "auto_contact_attempted": auto_contact_attempted,
        "auto_contact_sent": auto_contact_sent,
    }


def _available_company_profile_providers() -> list[str]:
    providers = []
    if os.getenv("CE_API_KEY"):
        providers.append("companyenrich")
    if os.getenv("PDL_API_KEY"):
        providers.append("pdl")
    return providers


def _ordered_company_profile_providers(requested: str) -> list[str]:
    available = _available_company_profile_providers()
    if not available:
        raise ValueError("no company enrichment provider is configured")
    chosen = str(requested or "auto").strip().lower()
    if chosen == "auto":
        offset = count_company_profile_attempts() % len(available)
        ordered = available[offset:] + available[:offset]
        return ordered
    if chosen not in {"companyenrich", "pdl"}:
        raise ValueError("unsupported company profile provider")
    if chosen not in available:
        raise ValueError(f"{chosen} is not configured")
    return [chosen] + [provider for provider in available if provider != chosen]


def _resolve_company_profile_once(provider: str, domain: str, lead: dict) -> tuple[dict, dict]:
    if provider == "companyenrich":
        client = CompanyEnrichClient()
        wait_for_enrichment = os.getenv("COMPANY_ENRICH_WAIT_FOR_ENRICHMENT", "true").strip().lower() != "false"
        payload = client.enrich_company(domain, wait_for_enrichment=wait_for_enrichment)
        summary = client.extract_company_profile(payload)
        return payload, summary
    if provider == "pdl":
        client = PeopleDataLabsClient()
        payload = client.enrich_company(domain)
        summary = client.extract_company_profile(payload)
        return payload, summary
    raise ValueError("unsupported company profile provider")


def resolve_company_profile(lead_id: str, provider: str = "auto", force: bool = False) -> dict[str, Any]:
    lead = get_lead(lead_id)
    if not lead:
        raise ValueError("lead not found")
    domain = normalize_company_domain(lead.get("company_domain"))
    if not domain:
        raise ValueError("company profile resolution requires a company domain")
    cached = get_company_profile(domain)
    if cached and not force and cached.get("status") == "resolved":
        add_event(lead_id, "company_profile.cache_hit", {"provider": cached.get("provider"), "domain": domain})
        return {
            "ok": True,
            "cached": True,
            "provider": cached.get("provider") or "cached",
            "domain": domain,
            "lead": get_lead(lead_id) or lead,
            "company_profile": cached,
        }

    attempts: list[dict[str, str]] = []
    last_error = "company enrichment failed"
    for chosen in _ordered_company_profile_providers(provider):
        try:
            payload, summary = _resolve_company_profile_once(chosen, domain, lead)
            stored = upsert_company_profile(
                domain,
                provider=chosen,
                status="resolved",
                company_name=summary.get("name") or lead.get("company"),
                summary=summary,
                raw=payload,
            )
            if summary.get("name") or summary.get("domain"):
                update_lead(
                    lead_id,
                    company=summary.get("name") or lead.get("company"),
                    company_domain=normalize_company_domain(summary.get("domain") or summary.get("website") or domain) or domain,
                    country=((summary.get("location") or {}).get("country") if isinstance(summary.get("location"), dict) else None) or lead.get("country"),
                )
            updated_lead = get_lead(lead_id) or lead
            add_event(lead_id, "company_profile.resolved", {"provider": chosen, "domain": domain, "company": summary.get("name"), "attempts": attempts})
            return {
                "ok": True,
                "cached": False,
                "provider": chosen,
                "domain": domain,
                "lead": updated_lead,
                "company_profile": stored,
                "attempts": attempts,
            }
        except (CompanyEnrichError, PeopleDataLabsError) as exc:
            last_error = str(exc)
            attempts.append({"provider": chosen, "error": str(exc)})
            add_event(lead_id, "company_profile.provider_failed", {"provider": chosen, "domain": domain, "error": str(exc)})
            continue

    stored = upsert_company_profile(
        domain,
        provider=attempts[-1]["provider"] if attempts else "unknown",
        status="failed",
        company_name=lead.get("company"),
        summary={},
        raw={"error": last_error, "attempts": attempts},
    )
    add_event(lead_id, "company_profile.failed", {"provider": provider, "domain": domain, "error": last_error, "attempts": attempts})
    return {
        "ok": False,
        "cached": False,
        "provider": provider,
        "domain": domain,
        "lead": get_lead(lead_id) or lead,
        "company_profile": stored,
        "detail": last_error,
        "attempts": attempts,
    }


def resolve_contact(lead_id: str, provider: str = "prospeo", force: bool = False) -> dict[str, Any]:
    chosen = _provider_name(provider)
    if chosen != "prospeo":
        raise ValueError("contact resolution is currently available only for prospeo")
    lead = get_lead(lead_id)
    if not lead:
        raise ValueError("lead not found")
    metadata = lead.get("metadata") or {}
    cached = metadata.get("contact_resolution") if isinstance(metadata.get("contact_resolution"), dict) else {}
    cached_provider = str(cached.get("provider") or "")
    cached_status = str(cached.get("status") or "")
    if not force and cached_provider == chosen and cached_status in {"resolved", "partial", "missing"}:
        return {
            "ok": True,
            "cached": True,
            "provider": chosen,
            "status": cached_status,
            "lead": lead,
            "contact": _contact_payload_for_lead(lead),
        }
    person_id = metadata.get("prospeo_person_id")
    if not person_id:
        raise ValueError("prospeo contact resolution requires a stored prospeo_person_id")
    client = ProspeoClient()
    try:
        payload = client.enrich_person(str(person_id))
        contact = client.extract_contact_fields(payload)
    except ProspeoError as exc:
        updated = record_contact_resolution(
            lead_id,
            provider=chosen,
            status="failed",
            details={"error": str(exc)},
        )
        add_event(lead_id, "contact_resolution.failed", {"provider": chosen, "error": str(exc)})
        return {
            "ok": False,
            "cached": False,
            "provider": chosen,
            "status": "failed",
            "lead": updated,
            "contact": _contact_payload_for_lead(updated),
            "detail": str(exc),
        }
    email_value = contact.get("email") if isinstance(contact.get("email"), str) else None
    phone_value = contact.get("phone") if isinstance(contact.get("phone"), str) else None
    status = "resolved" if email_value and phone_value else "partial" if email_value or phone_value else "missing"
    updated = record_contact_resolution(
        lead_id,
        provider=chosen,
        email=email_value,
        phone=phone_value,
        whatsapp_candidate=phone_value,
        status=status,
        details={
            "verification_status": contact.get("verification_status"),
            "linkedin_url": contact.get("linkedin_url"),
            "person_id": contact.get("person_id") or person_id,
        },
    )
    if contact.get("verification_status"):
        update_lead(lead_id, verification_status=contact.get("verification_status"))
        updated = get_lead(lead_id) or updated
    add_event(lead_id, "contact_resolution.completed", {
        "provider": chosen,
        "status": status,
        "email_found": bool(email_value),
        "phone_found": bool(phone_value),
    })
    return {
        "ok": True,
        "cached": False,
        "provider": chosen,
        "status": status,
        "lead": updated,
        "contact": _contact_payload_for_lead(updated),
    }


def enrich_lead(lead_id: str, provider: str = "hunter") -> dict:
    chosen = _provider_name(provider)
    lead = get_lead(lead_id)
    if not lead:
        raise ValueError("lead not found")
    if lead["stage"] == "suppressed":
        raise ValueError("suppressed leads cannot be enriched")
    if chosen == "lusha":
        client = LushaClient()
        try:
            metadata = lead.get("metadata") or {}
            contact_id = metadata.get("lusha_contact_id")
            request_id = metadata.get("lusha_request_id")
            if not contact_id or not request_id:
                raise ValueError("lusha enrichment requires stored lusha_contact_id and lusha_request_id")
            payload = client.enrich_contacts(str(request_id), [str(contact_id)])
        except LushaError as exc:
            raise ValueError(str(exc)) from exc
        contacts = payload.get("contacts") if isinstance(payload.get("contacts"), list) else payload.get("results") if isinstance(payload.get("results"), list) else []
        matched = contacts[0] if contacts else {}
        job = matched.get("jobTitle") if isinstance(matched.get("jobTitle"), dict) else {}
        company = matched.get("company") if isinstance(matched.get("company"), dict) else {}
        emails = matched.get("emails") if isinstance(matched.get("emails"), list) else []
        phones = matched.get("phones") if isinstance(matched.get("phones"), list) else []
        email_value = next((entry.get("value") for entry in emails if isinstance(entry, dict) and entry.get("value")), None)
        phone_value = next((entry.get("value") for entry in phones if isinstance(entry, dict) and entry.get("value")), None)
        updates = {
            "email": email_value or lead.get("email"),
            "full_name": " ".join(filter(None, [matched.get("firstName"), matched.get("lastName")])) or lead.get("full_name"),
            "job_title": job.get("title") or lead.get("job_title"),
            "company": company.get("name") or lead.get("company"),
            "company_domain": company.get("domain") or lead.get("company_domain"),
            "country": company.get("location", {}).get("country") if isinstance(company.get("location"), dict) else lead.get("country"),
            "phone": phone_value or lead.get("phone"),
            "verification_status": lead.get("verification_status"),
            "hunter_score": lead.get("hunter_score") or 0,
        }
        update_lead(lead_id, **updates)
    elif chosen == "prospeo":
        client = ProspeoClient()
        try:
            person_id = (lead.get("metadata") or {}).get("prospeo_person_id")
            if not person_id:
                raise ValueError("prospeo enrichment requires a stored prospeo_person_id")
            matched = client.enrich_person(str(person_id))
        except ProspeoError as exc:
            raise ValueError(str(exc)) from exc
        company = matched.get("company") if isinstance(matched.get("company"), dict) else {}
        location_value = matched.get("location") if isinstance(matched.get("location"), dict) else {}
        email_info = matched.get("email") if isinstance(matched.get("email"), dict) else {}
        phone_info = matched.get("mobile") if isinstance(matched.get("mobile"), dict) else {}
        updates = {
            "email": email_info.get("email") or matched.get("email") or lead.get("email"),
            "full_name": matched.get("full_name") or lead.get("full_name"),
            "job_title": matched.get("current_job_title") or matched.get("headline") or lead.get("job_title"),
            "company": company.get("name") or lead.get("company"),
            "company_domain": company.get("website") or company.get("domain") or lead.get("company_domain"),
            "country": location_value.get("country") or lead.get("country"),
            "phone": phone_info.get("number") or lead.get("phone"),
            "verification_status": email_info.get("status") or lead.get("verification_status"),
            "hunter_score": lead.get("hunter_score") or 0,
        }
        update_lead(lead_id, **updates)
    elif chosen == "apollo":
        client = ApolloClient()
        try:
            if lead.get("email"):
                matched = client.match_person(email=lead["email"])
            elif lead.get("company_domain") and lead.get("full_name"):
                matched = client.match_person(name=lead["full_name"], domain=lead["company_domain"])
            else:
                raise ValueError("apollo enrichment requires an email or full_name + company_domain")
        except ApolloError as exc:
            raise ValueError(str(exc)) from exc
        organization = matched.get("organization") if isinstance(matched.get("organization"), dict) else {}
        updates = {
            "email": matched.get("email") or lead.get("email"),
            "full_name": matched.get("name") or lead.get("full_name"),
            "job_title": matched.get("title") or lead.get("job_title"),
            "company": organization.get("name") or lead.get("company"),
            "company_domain": organization.get("primary_domain") or lead.get("company_domain"),
            "country": matched.get("country") or lead.get("country"),
            "verification_status": matched.get("email_status") or lead.get("verification_status"),
            "hunter_score": lead.get("hunter_score") or 0,
        }
        update_lead(lead_id, **updates)
    else:
        client = HunterClient()
        try:
            if not lead.get("email") and lead.get("company_domain") and lead.get("full_name"):
                found = client.email_finder(domain=lead["company_domain"], full_name=lead["full_name"])
                if found.get("email"):
                    update_lead(lead_id, email=found["email"])
                    lead = get_lead(lead_id)
                    assert lead
            if lead.get("email"):
                verified = client.verify(lead["email"])
                update_lead(
                    lead_id,
                    verification_status=verified.get("status") or "unknown",
                    hunter_score=int(verified.get("score") or 0),
                )
        except HunterError as exc:
            if exc.status == 451 or exc.code == "claimed_email":
                suppress_lead(lead_id, "hunter_claimed_email")
                return get_lead(lead_id)  # type: ignore[return-value]
            raise
    lead = get_lead(lead_id)
    assert lead
    calculated = score_lead(lead)
    update_lead(lead_id, lead_score=calculated, stage="qualified" if calculated >= 60 else "enriched")
    return get_lead(lead_id)  # type: ignore[return-value]


class _FallbackDraft:
    def __init__(self, subject: str, body: str, rationale: str, call_to_action: str = "reply"):
        self.subject = subject
        self.body = body
        self.rationale = rationale
        self.call_to_action = call_to_action


def _fallback_outreach_from_context(context: dict[str, Any]):
    company = context.get("company") or "your team"
    job_title = context.get("job_title") or "operations"
    company_context = context.get("company_context") if isinstance(context.get("company_context"), dict) else {}
    contact = context.get("contact_profile") if isinstance(context.get("contact_profile"), dict) else {}
    summary_line = company_context.get("summary_line") or company_context.get("description") or ""
    pain_points = company_context.get("suggested_pain_points") if isinstance(company_context.get("suggested_pain_points"), list) else []
    specialties = company_context.get("specialties") if isinstance(company_context.get("specialties"), list) else []
    opener = f"Hi {context.get('full_name') or 'there'},"
    observation = pain_points[0] if pain_points else "fragmented operational updates across client-facing teams"
    specialty = specialties[0] if specialties else company_context.get("industry") or "logistics operations"
    line2 = f"I came across {company} while looking at teams focused on {specialty.lower()}."
    if summary_line:
        line2 = f"I came across {company} and noted {summary_line.rstrip('.')} .".replace(' .', '.')
    line3 = f"When {job_title.lower()} teams are managing that kind of operation, {observation} usually becomes expensive fast."
    product = os.getenv(
        "COMPANY_PRODUCT_DESCRIPTION",
        "We help teams connect operational information and customer communication in one controlled workflow.",
    )
    line4 = product.strip()
    cta = "If useful, I can send a short example of how that could fit your current workflow."
    body = "\n\n".join([opener, line2, line3, line4, cta])
    subject_focus = company_context.get("industry") or specialty
    subject = f"Operational visibility for {company}" if contact.get("ready_for_outreach") else f"{subject_focus} workflow for {company}"
    rationale = "Fallback draft generated from saved company context because the live drafting model is unavailable."
    return _FallbackDraft(subject=subject[:90], body=body[:1400], rationale=rationale, call_to_action="reply")


def _generate_outreach_draft(context: dict[str, Any]):
    from agents.sales import draft_outreach

    try:
        return __import__("asyncio").run(draft_outreach(context))
    except RuntimeError:
        raise
    except Exception:
        return _fallback_outreach_from_context(context)


def _draft_company_context(lead: dict) -> dict[str, Any]:
    company_profile = lead.get("company_profile") if isinstance(lead.get("company_profile"), dict) else {}
    usage = company_usage_profile(company_profile)
    if not usage:
        return {}
    return {key: value for key, value in usage.items() if value not in (None, "", [], {})}


def _draft_lead_context(lead: dict) -> dict[str, Any]:
    safe = {
        key: lead.get(key)
        for key in (
            "id", "full_name", "job_title", "company", "company_domain", "country",
            "source", "source_detail", "campaign_id", "message", "lead_score",
        )
        if lead.get(key) not in (None, "")
    }
    contact = contact_profile_for_lead(lead)
    safe["contact_profile"] = {
        key: contact.get(key)
        for key in ("status", "email", "phone", "whatsapp_candidate", "email_ready", "whatsapp_ready", "ready_for_outreach", "verification_status")
        if contact.get(key) not in (None, "")
    }
    company_context = _draft_company_context(lead)
    if company_context:
        safe["company_context"] = company_context
    metadata = lead.get("metadata") if isinstance(lead.get("metadata"), dict) else {}
    company_discovery = metadata.get("company_discovery") if isinstance(metadata.get("company_discovery"), dict) else {}
    if not company_discovery and lead.get("company_domain"):
        candidate = _company_candidate_for_domain(str(lead["company_domain"]))
        if candidate and candidate.get("id") != lead.get("id"):
            candidate_metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
            company_discovery = {
                "source": candidate.get("source"),
                "source_detail": candidate.get("source_detail"),
                "research_filters": candidate_metadata.get("research_filters") or {},
                "provider_company_id": candidate_metadata.get("provider_company_id"),
            }
    if company_discovery:
        safe["company_discovery"] = {
            key: value
            for key, value in company_discovery.items()
            if key != "lead_id" and value not in (None, "", [], {})
        }
    latest = lead.get("latest_interaction") or {}
    if latest.get("direction") == "inbound":
        safe["latest_reply"] = {
            "subject": latest.get("subject"),
            "body": (latest.get("body") or "")[:3000],
        }
    return safe


async def preview_draft(lead_id: str) -> dict[str, Any]:
    lead = get_lead(lead_id)
    if not lead:
        raise ValueError("lead not found")
    if lead["stage"] == "suppressed":
        raise ValueError("suppressed leads cannot receive outreach")
    if not lead.get("email"):
        raise ValueError("lead has no email")

    context = _draft_lead_context(lead)
    try:
        from agents.sales import draft_outreach
        draft = await draft_outreach(context)
        provider = "model"
    except Exception:
        draft = _fallback_outreach_from_context(context)
        provider = "fallback"
    return {
        "ok": True,
        "provider": provider,
        "lead": get_lead(lead_id) or lead,
        "context": context,
        "preview": {
            "subject": draft.subject,
            "body": draft.body,
            "rationale": draft.rationale,
            "call_to_action": draft.call_to_action,
        },
    }


async def build_draft(lead_id: str) -> dict:
    lead = get_lead(lead_id)
    if not lead:
        raise ValueError("lead not found")
    if lead["stage"] == "suppressed":
        raise ValueError("suppressed leads cannot receive outreach")
    if not lead.get("email"):
        raise ValueError("lead has no email")

    context = _draft_lead_context(lead)
    try:
        from agents.sales import draft_outreach
        draft = await draft_outreach(context)
    except Exception:
        draft = _fallback_outreach_from_context(context)
    latest = lead.get("latest_interaction") or {}
    is_reply = latest.get("direction") == "inbound" and latest.get("kind") == "reply"
    subject = draft.subject
    if is_reply and not subject.lower().startswith("re:"):
        subject = f"Re: {latest.get('subject') or subject}"
    return create_draft(
        lead_id,
        subject,
        draft.body,
        kind="reply" if is_reply else "first_outreach",
        in_reply_to=latest.get("provider_message_id") if is_reply else None,
    )


def _resolved_resend_sender() -> str:
    from_email = os.getenv("SALES_FROM_EMAIL", "").strip()
    if not from_email:
        raise ValueError("SALES_FROM_EMAIL is not configured")
    resend_domain = os.getenv("SALES_RESEND_DOMAIN", "").strip()
    if not resend_domain:
        raise ValueError("SALES_RESEND_DOMAIN is not configured")
    local_part = from_email.split("@", 1)[0].strip()
    if not local_part:
        raise ValueError("SALES_FROM_EMAIL must include a local part")
    display_name = os.getenv("SALES_FROM_NAME", company_name()).strip() or company_name()
    return f'{display_name} <{local_part}@{resend_domain}>'


def _auto_contact_enabled() -> bool:
    return os.getenv("SALES_AUTO_CONTACT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _auto_contact_sources() -> set[str]:
    raw = os.getenv("SALES_AUTO_CONTACT_SOURCES", "website,popup_offer,quiz_funnel,post_form,social_form")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _simple_auto_contact_draft(lead: dict) -> _FallbackDraft:
    first_name = str(lead.get("full_name") or "there").strip().split()[0] or "there"
    company = str(lead.get("company") or "your team").strip() or "your team"
    source_detail = str(lead.get("source_detail") or "").strip().lower()
    offer_hint = " If you came through the quiz or popup, reply with QUIZ and I can send the checklist as well." if any(token in source_detail for token in ("quiz", "popup", "offer")) else ""
    body = "\n\n".join([
        f"Hi {first_name},",
        f"Thanks for reaching out to {company_name()}.",
        os.getenv(
            "COMPANY_PRODUCT_DESCRIPTION",
            f"We help teams like {company} improve operational visibility and customer communication.",
        ),
        "If useful, reply with your current workflow or your biggest ops bottleneck and I can send a short example.",
    ]) + offer_hint
    return _FallbackDraft(
        subject=f"Thanks for reaching out to {company_name()}",
        body=body[:1400],
        rationale="Generic auto-contact draft generated for a new inbound website or popup lead.",
        call_to_action="reply",
    )


def _lead_has_outbound_activity(lead: dict) -> bool:
    interactions = lead.get("interactions") if isinstance(lead.get("interactions"), list) else []
    if any(item.get("direction") == "outbound" for item in interactions if isinstance(item, dict)):
        return True
    drafts = lead.get("drafts") if isinstance(lead.get("drafts"), list) else []
    return bool(drafts)


def maybe_auto_contact_lead(lead_id: str, *, created: bool) -> dict | None:
    if not created or not _auto_contact_enabled():
        return None
    lead = get_lead(lead_id)
    if not lead:
        return {"enabled": True, "sent": False, "reason": "lead_not_found"}
    source = str(lead.get("source") or "").strip().lower()
    if source not in _auto_contact_sources():
        return {"enabled": True, "sent": False, "reason": "source_not_enabled"}
    if lead.get("stage") == "suppressed":
        return {"enabled": True, "sent": False, "reason": "suppressed"}
    if not lead.get("consent"):
        return {"enabled": True, "sent": False, "reason": "no_consent"}
    if not lead.get("email"):
        return {"enabled": True, "sent": False, "reason": "missing_email"}
    if _lead_has_outbound_activity(lead):
        return {"enabled": True, "sent": False, "reason": "already_contacted"}
    draft = _simple_auto_contact_draft(lead)
    stored = create_draft(lead_id, draft.subject, draft.body, kind="auto_intro")
    stored = get_draft(stored["id"]) or stored
    try:
        sender = _resolved_resend_sender()
        reply_to = os.getenv("SALES_REPLY_TO_EMAIL", "").strip() or None
        provider_message_id = _send_resend_draft(
            stored,
            lead,
            sender=sender,
            reply_to=reply_to,
            metadata={
                "provider": "resend",
                "source": "automation_auto_contact",
                "from_email": sender,
                "to_email": lead["email"],
                "reply_to": reply_to,
                "draft_id": stored["id"],
            },
        )
        add_event(lead_id, "outreach.auto_contacted", {"draft_id": stored["id"], "provider_message_id": provider_message_id})
        return {"enabled": True, "sent": True, "draft_id": stored["id"], "message_id": provider_message_id}
    except Exception as exc:
        add_event(lead_id, "outreach.auto_contact_failed", {"draft_id": stored["id"], "error": str(exc)})
        return {"enabled": True, "sent": False, "draft_id": stored["id"], "reason": str(exc)}


def _resend_api_key() -> str:
    api_key = os.getenv("SALES_RESEND_API_KEY", "").strip()
    if not api_key:
        raise ValueError("SALES_RESEND_API_KEY is not configured")
    return api_key


def _resend_headers(*, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {_resend_api_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": RESEND_USER_AGENT,
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _resend_get_received_email(email_id: str) -> dict:
    request = urllib.request.Request(
        f"{RESEND_RECEIVING_URL}/{urllib.parse.quote(email_id)}",
        headers=_resend_headers(),
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Resend received-email lookup failed: HTTP {exc.code} {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Resend received-email lookup failed: {exc}") from exc


def _svix_secret_bytes(secret: str) -> bytes:
    encoded = secret[6:] if secret.startswith("whsec_") else secret
    try:
        return base64.b64decode(encoded)
    except Exception as exc:
        raise ValueError("invalid SALES_RESEND_WEBHOOK_SECRET") from exc


def verify_resend_webhook(payload: str, headers: dict[str, str]) -> dict:
    secret = os.getenv("SALES_RESEND_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise ValueError("SALES_RESEND_WEBHOOK_SECRET is not configured")
    lowered = {str(key).lower(): value for key, value in headers.items()}
    webhook_id = str(lowered.get("svix-id") or lowered.get("webhook-id") or "").strip()
    webhook_timestamp = str(lowered.get("svix-timestamp") or lowered.get("webhook-timestamp") or "").strip()
    webhook_signature = str(lowered.get("svix-signature") or lowered.get("webhook-signature") or "").strip()
    if not webhook_id or not webhook_timestamp or not webhook_signature:
        raise ValueError("missing resend webhook signature headers")
    try:
        timestamp_value = int(webhook_timestamp)
    except ValueError as exc:
        raise ValueError("invalid resend webhook timestamp") from exc
    if abs(int(time.time()) - timestamp_value) > RESEND_WEBHOOK_TOLERANCE_SECONDS:
        raise ValueError("stale resend webhook timestamp")
    signed_content = f"{webhook_id}.{webhook_timestamp}.{payload}".encode("utf-8")
    expected = base64.b64encode(hmac.new(_svix_secret_bytes(secret), signed_content, hashlib.sha256).digest()).decode("utf-8")
    signatures = []
    for token in webhook_signature.split():
        version, _, signature_value = token.partition(",")
        if version == "v1" and signature_value:
            signatures.append(signature_value.strip())
    if not signatures or not any(hmac.compare_digest(expected, value) for value in signatures):
        raise ValueError("invalid resend webhook signature")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid resend webhook payload") from exc


def _normalize_resend_tags(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items() if item is not None}
    normalized: dict[str, str] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            tag_value = str(item.get("value") or "").strip()
            if name and tag_value:
                normalized[name] = tag_value
    return normalized


def _header_value(headers: dict[str, Any] | None, name: str) -> str | None:
    if not isinstance(headers, dict):
        return None
    target = name.lower()
    for key, value in headers.items():
        if str(key).lower() == target:
            cleaned = str(value or "").strip()
            return cleaned or None
    return None


def _normalize_references(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item for item in value.split() if item]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return []


def _resolve_inbound_lead(*, sender_email: str, in_reply_to: str | None = None, references: Any = None):
    lead = get_lead_by_email(sender_email)
    if lead:
        return lead
    for message_id in [in_reply_to, *_normalize_references(references)]:
        draft = get_draft_by_provider_message_id(message_id)
        if draft:
            resolved = get_lead(draft["lead_id"])
            if resolved:
                return resolved
    return None


def _recipient_first(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0]).strip() or None
    cleaned = str(value or "").strip()
    return cleaned or None


def _record_resend_email_event(event_type: str, payload: dict[str, Any], *, lead_id: str | None) -> None:
    add_event(lead_id, f"resend.{event_type}", payload)


def ingest_resend_event(payload: dict) -> dict:
    event_type = str(payload.get("type") or "").strip()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if not event_type:
        raise ValueError("resend event type is required")
    tags = _normalize_resend_tags(data.get("tags"))
    email_id = str(data.get("email_id") or data.get("id") or "").strip() or None
    draft_id = str(tags.get("draft_id") or "").strip() or None
    lead_id = str(tags.get("lead_id") or "").strip() or None
    draft = None
    if email_id and (not draft_id or not lead_id):
        draft = get_draft_by_provider_message_id(email_id)
        if draft:
            draft_id = draft_id or draft.get("id")
            lead_id = lead_id or draft.get("lead_id")
    if event_type == "email.received":
        details = {}
        warning = None
        if email_id:
            try:
                details = _resend_get_received_email(email_id)
            except Exception as exc:
                warning = str(exc)
        headers = details.get("headers") if isinstance(details.get("headers"), dict) else {}
        in_reply_to = _header_value(headers, "in-reply-to")
        references = _header_value(headers, "references")
        from_email = str(details.get("from") or data.get("from") or "").strip()
        resolved_lead = _resolve_inbound_lead(
            sender_email=parseaddr(from_email)[1].lower(),
            in_reply_to=in_reply_to,
            references=references,
        )
        inbound_payload = {
            "from_email": from_email,
            "to_email": _recipient_first(details.get("to") or data.get("to")),
            "subject": str(details.get("subject") or data.get("subject") or "Reply"),
            "text": details.get("text") or "",
            "html": details.get("html") or "",
            "message_id": str(details.get("message_id") or data.get("message_id") or "").strip() or None,
            "in_reply_to": in_reply_to,
            "references": _normalize_references(references),
            "received_at": str(details.get("created_at") or data.get("created_at") or payload.get("created_at") or "").strip() or None,
            "headers": headers,
            "source": "resend",
        }
        if resolved_lead is None and parseaddr(from_email)[1]:
            resolved_lead = get_lead_by_email(parseaddr(from_email)[1].lower())
        if not resolved_lead:
            raise ValueError("no lead matches resend inbound sender or thread")
        result = ingest_inbound_email({**inbound_payload, "lead_id": resolved_lead["id"]})
        _record_resend_email_event(
            event_type,
            {
                "provider": "resend",
                "email_id": email_id,
                "message_id": inbound_payload["message_id"],
                "from_email": parseaddr(from_email)[1].lower() or from_email,
                "to_email": inbound_payload["to_email"],
                "subject": inbound_payload["subject"],
                "fetched_full_email": bool(details),
                "warning": warning,
            },
            lead_id=result.get("lead_id"),
        )
        return {
            "ok": True,
            "event": event_type,
            "lead_id": result.get("lead_id"),
            "duplicate": result.get("duplicate", False),
            "fetched_full_email": bool(details),
            "warning": warning,
        }
    event_payload = {
        "provider": "resend",
        "email_id": email_id,
        "draft_id": draft_id,
        "from_email": str(data.get("from") or "").strip() or None,
        "to_email": _recipient_first(data.get("to")),
        "subject": str(data.get("subject") or "").strip() or None,
        "created_at": str(payload.get("created_at") or "").strip() or None,
        "tags": tags,
    }
    for key in ("error", "reason", "scheduled_at"):
        if data.get(key):
            event_payload[key] = data.get(key)
    _record_resend_email_event(event_type, event_payload, lead_id=lead_id)
    return {
        "ok": True,
        "event": event_type,
        "lead_id": lead_id,
        "draft_id": draft_id,
        "matched": bool(lead_id or draft_id),
    }


def _resend_request(payload: dict) -> dict:
    request = urllib.request.Request(
        RESEND_SEND_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=_resend_headers(idempotency_key=str(uuid.uuid4())),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Resend send failed: HTTP {exc.code} {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Resend send failed: {exc}") from exc


def _sales_public_base_url() -> str:
    return (
        os.getenv("SALES_PUBLIC_BASE_URL", company_public_url()).strip()
        or company_public_url()
    ).rstrip("/")


def _schedule_meeting_url() -> str:
    return (os.getenv("SALES_SCHEDULE_URL", "").strip() or f"{_sales_public_base_url()}/meet")


def _brand_logo_url() -> str:
    return os.getenv("SALES_BRAND_LOGO_URL", "").strip() or company_logo_url()


def _resend_text_for_draft(draft: dict) -> str:
    body = str(draft.get("body") or "").strip()
    schedule = f"Schedule a meeting: {_schedule_meeting_url()}"
    return f"{body}\n\n{schedule}" if body else schedule


def _htmlize_draft_body(body: str) -> str:
    paragraphs = [part.strip() for part in str(body or "").replace("\r\n", "\n").split("\n\n") if part.strip()]
    if not paragraphs:
        return ""
    return "".join(
        f'<p style="margin:0 0 14px;font-size:15px;line-height:1.7;color:#374151;">{escape(paragraph).replace(chr(10), "<br>")}</p>'
        for paragraph in paragraphs
    )


def _resend_html_for_draft(draft: dict, lead: dict) -> str:
    company = escape(str(lead.get("company") or "your team").strip() or "your team")
    brand = escape(company_name())
    subject = escape(str(draft.get("subject") or company_name()).strip() or company_name())
    body_html = _htmlize_draft_body(str(draft.get("body") or ""))
    schedule_url = escape(_schedule_meeting_url(), quote=True)
    logo_url = escape(_brand_logo_url(), quote=True)
    return (
        '<div style="background:#f5f7fb;padding:32px 16px;font-family:Arial,sans-serif;color:#111827;">'
        '<div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e5e7eb;border-radius:14px;padding:32px;">'
        f'<img src="{logo_url}" alt="{brand}" width="148" style="display:block;width:148px;max-width:100%;height:auto;margin:0 0 20px;">'
        f'<p style="margin:0 0 6px;font-size:14px;line-height:1.5;color:#5E6AD2;font-weight:700;">{brand}</p>'
        f'<h1 style="margin:0 0 10px;font-size:24px;line-height:1.3;color:#111827;">{subject}</h1>'
        f'<p style="margin:0 0 14px;font-size:13px;line-height:1.6;color:#6b7280;">Built for {company} operational workflows.</p>'
        f'{body_html}'
        f'<a href="{schedule_url}" style="display:inline-block;background:#5E6AD2;color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;padding:12px 18px;border-radius:10px;">Schedule a meeting</a>'
        f'<p style="margin:14px 0 0;font-size:13px;line-height:1.7;color:#6b7280;">Prefer to review first? {escape(_sales_public_base_url())}/meet</p>'
        f'<p style="margin:22px 0 0;font-size:14px;line-height:1.7;color:#6b7280;">Regards,<br>{brand}</p>'
        '</div>'
        '</div>'
    )


def _resend_payload_for_draft(draft: dict, lead: dict, *, sender: str, reply_to: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "from": sender,
        "to": [lead["email"]],
        "subject": draft["subject"],
        "html": _resend_html_for_draft(draft, lead),
        "text": _resend_text_for_draft(draft),
        "headers": {
            "List-Unsubscribe": f"<mailto:{reply_to or sender}?subject=unsubscribe>",
            "X-Lead-ID": lead["id"],
            "X-Draft-ID": draft["id"],
        },
        "tags": [
            {"name": "lead_id", "value": lead["id"]},
            {"name": "draft_id", "value": draft["id"]},
        ],
    }
    if reply_to:
        payload["reply_to"] = reply_to
    thread_headers = {}
    if draft.get("in_reply_to"):
        thread_headers["In-Reply-To"] = draft["in_reply_to"]
        thread_headers["References"] = draft["in_reply_to"]
    if thread_headers:
        payload["headers"].update(thread_headers)
    return payload


def _send_resend_draft(draft: dict, lead: dict, *, sender: str, reply_to: str | None, metadata: dict[str, Any] | None = None) -> str:
    payload = _resend_payload_for_draft(draft, lead, sender=sender, reply_to=reply_to)
    result = _resend_request(payload)
    provider_message_id = result.get("id") or f"resend-{draft['id']}"
    mark_sent(draft["id"], provider_message_id, metadata=metadata)
    return provider_message_id


def send_approved(draft_id: str) -> dict:
    draft = claim_draft_for_send(draft_id)
    lead = get_lead(draft["lead_id"])
    if not lead or lead["stage"] == "suppressed" or not lead.get("email"):
        release_send_claim(draft_id)
        raise ValueError("lead is unavailable or suppressed")
    try:
        sender = _resolved_resend_sender()
    except ValueError:
        release_send_claim(draft_id)
        raise
    reply_to = os.getenv("SALES_REPLY_TO_EMAIL", "").strip() or None
    try:
        provider_message_id = _send_resend_draft(
            draft,
            lead,
            sender=sender,
            reply_to=reply_to,
            metadata={
                "provider": "resend",
                "source": "resend_api",
                "from_email": sender,
                "to_email": lead["email"],
                "reply_to": reply_to,
                "draft_id": draft_id,
            },
        )
    except Exception:
        release_send_claim(draft_id)
        raise
    return {"ok": True, "draft_id": draft_id, "message_id": provider_message_id, "sent": True}


def _clean_inbound_text(value: str | None) -> str:
    if not value:
        return ""
    text = unescape(str(value)).replace("\r\n", "\n").strip()
    return text[:5000]


def ingest_inbound_email(payload: dict) -> dict:
    sender = parseaddr(str(payload.get("from_email") or ""))[1].lower()
    if not sender:
        raise ValueError("from_email is required")
    lead = None
    lead_id = str(payload.get("lead_id") or "").strip() or None
    if lead_id:
        lead = get_lead(lead_id)
    if not lead:
        lead = _resolve_inbound_lead(
            sender_email=sender,
            in_reply_to=str(payload.get("in_reply_to") or "").strip() or None,
            references=payload.get("references"),
        )
    if not lead:
        raise ValueError("no lead matches inbound sender or thread")
    subject = str(payload.get("subject") or "Reply").strip()[:500] or "Reply"
    text = _clean_inbound_text(payload.get("text") or payload.get("html"))
    metadata = {
        "to_email": str(payload.get("to_email") or "").strip() or None,
        "message_id": str(payload.get("message_id") or "").strip() or None,
        "in_reply_to": str(payload.get("in_reply_to") or "").strip() or None,
        "references": _normalize_references(payload.get("references")),
        "received_at": str(payload.get("received_at") or "").strip() or None,
        "headers": payload.get("headers") if isinstance(payload.get("headers"), dict) else None,
        "source": str(payload.get("source") or "cloudflare_email_routing"),
        "from_email": sender,
    }
    message_id = metadata["message_id"]
    if subject.lower() == "unsubscribe":
        suppress_lead(lead["id"], "unsubscribe")
        return {"ok": True, "lead_id": lead["id"], "action": "suppressed", "duplicate": False}
    before = get_lead(lead["id"])
    before_count = len(before.get("interactions", [])) if before else 0
    mark_replied(lead["id"], subject, text, message_id, metadata=metadata)
    after = get_lead(lead["id"])
    after_count = len(after.get("interactions", [])) if after else before_count
    duplicate = after_count == before_count
    return {"ok": True, "lead_id": lead["id"], "action": "replied", "duplicate": duplicate}
