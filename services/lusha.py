from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.error
import urllib.request
from typing import Any


class LushaError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


logger = logging.getLogger(__name__)


class LushaClient:
    def __init__(self, api_key: str | None = None, base_url: str = "https://api.lusha.com"):
        self.api_key = api_key or os.getenv("LUSHA_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        if not self.api_key:
            raise LushaError("LUSHA_API_KEY is not configured")
        self.user_agent = os.getenv("LUSHA_USER_AGENT", "company-core/1.0")

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        attempts: int = 3,
    ) -> dict:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
        body_preview = json.dumps(body, ensure_ascii=True)[:500] if body is not None else ""
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "api_key": self.api_key,
            "Accept": "application/json",
            "User-Agent": self.user_agent,
            "Accept-Language": "en-US,en;q=0.9",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=payload, headers=headers, method=method.upper())
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                raw = ""
                detail = str(exc)
                try:
                    raw = exc.read().decode("utf-8")
                    parsed = json.loads(raw)
                    detail = parsed.get("message") or parsed.get("detail") or parsed.get("error") or raw[:500]
                except Exception:
                    if not raw:
                        raw = detail
                normalized_detail = str(detail)
                if exc.code == 403 and "blocked access based on your browser's signature" in raw.lower():
                    normalized_detail = (
                        "Lusha blocked this request upstream based on the connector signature. "
                        "This is a provider-side access block, not a bad filter selection."
                    )
                logger.warning(
                    "Lusha upstream error method=%s url=%s status=%s attempt=%s body=%s response=%s",
                    request.get_method(),
                    url,
                    exc.code,
                    attempt + 1,
                    body_preview,
                    raw[:500],
                )
                if exc.code in {402, 403, 409, 422, 429, 500, 502, 503, 504} and attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise LushaError(
                    f"method={request.get_method()} url={url} status={exc.code} detail={normalized_detail} body={body_preview}",
                    exc.code,
                ) from exc
            except (TimeoutError, urllib.error.URLError) as exc:
                logger.warning(
                    "Lusha upstream transport error method=%s url=%s attempt=%s body=%s error=%s",
                    request.get_method(),
                    url,
                    attempt + 1,
                    body_preview,
                    exc,
                )
                if attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise LushaError(
                    f"method={request.get_method()} url={url} transport_error={exc} body={body_preview}"
                ) from exc
        raise LushaError(f"Lusha request failed method={request.get_method()} url={url} body={body_preview}")

    def contact_filter_values(self, filter_type: str, query: str | None = None) -> dict:
        params = {"query": query} if query else None
        return self._request("GET", f"v3/contacts/prospecting/filters/{filter_type}", query=params)

    def company_filter_values(self, filter_type: str, query: str | None = None) -> dict:
        query_enabled = {"names", "technologies", "locations"}
        params = {"query": query} if query and filter_type in query_enabled else None
        return self._request("GET", f"v3/companies/prospecting/filters/{filter_type}", query=params)

    def prospect_contacts(
        self,
        *,
        job_title: str | None = None,
        service_keywords: list[str] | None = None,
        location: str | None = None,
        company_size: str | None = None,
        limit: int = 5,
    ) -> dict:
        contact_include: dict[str, Any] = {}
        company_include: dict[str, Any] = {}
        if job_title:
            contact_include["jobTitles"] = [job_title]
        if location:
            contact_include["locations"] = [{"country": location}]
        if company_size:
            company_include["sizes"] = [company_size]
        keywords = [item.strip() for item in (service_keywords or []) if str(item).strip()]
        if keywords:
            company_include["searchText"] = keywords
        payload = {
            "pagination": {"page": 0, "size": min(max(limit, 10), 50)},
            "filters": {
                "contacts": {"include": contact_include} if contact_include else {},
                "companies": {"include": company_include} if company_include else {},
            },
            "options": {"maxContactsPerCompany": 1},
        }
        return self._request("POST", "v3/contacts/prospecting", body=payload)

    def enrich_contacts(self, request_id: str, contact_ids: list[str]) -> dict:
        payload = {
            "requestId": request_id,
            "contactIds": contact_ids[:100],
            "revealEmails": True,
            "revealPhones": True,
        }
        return self._request("POST", "v3/contacts/enrich", body=payload)
