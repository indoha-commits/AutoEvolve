from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ApolloError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ApolloClient:
    def __init__(self, api_key: str | None = None, base_url: str = "https://api.apollo.io/api/v1"):
        self.api_key = api_key or os.getenv("APOLLO_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        if not self.api_key:
            raise ApolloError("APOLLO_API_KEY is not configured")

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        attempts: int = 3,
    ) -> dict:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        if query:
            url = f"{url}?{query}"
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "x-api-key": self.api_key,
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=payload, headers=headers, method=method.upper())
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = str(exc)
                try:
                    raw = exc.read().decode("utf-8")
                    payload = json.loads(raw)
                    detail = payload.get("error") or payload.get("message") or payload.get("detail") or raw[:500]
                except Exception:
                    pass
                if exc.code in {403, 409, 422, 429, 500, 502, 503, 504} and attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise ApolloError(detail, exc.code) from exc
            except (TimeoutError, urllib.error.URLError) as exc:
                if attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise ApolloError(str(exc)) from exc
        raise ApolloError("Apollo request failed")

    def people_search(self, domain: str, limit: int = 10) -> list[dict]:
        payload = self._request(
            "POST",
            "mixed_people/api_search",
            params={
                "q_organization_domains_list[]": [domain],
                "page": 1,
                "per_page": min(max(limit, 1), 10),
            },
        )
        people = payload.get("people")
        if isinstance(people, list):
            return people
        contacts = payload.get("contacts")
        if isinstance(contacts, list):
            return contacts
        return []

    def people_market_search(self, *, industry: str, location: str | None = None, limit: int = 5) -> list[dict]:
        params: dict[str, Any] = {
            "q_keywords": industry,
            "page": 1,
            "per_page": min(max(limit, 1), 10),
        }
        if location:
            params["organization_locations[]"] = [location]
        payload = self._request("POST", "mixed_people/api_search", params=params)
        people = payload.get("people")
        if isinstance(people, list):
            return people
        contacts = payload.get("contacts")
        if isinstance(contacts, list):
            return contacts
        return []

    def bulk_match(self, details: list[dict[str, Any]]) -> list[dict]:
        payload = self._request(
            "POST",
            "people/bulk_match",
            params={"reveal_personal_emails": "false"},
            body={"details": details[:10]},
        )
        for key in ("matches", "people", "contacts", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return []

    def match_person(self, *, email: str | None = None, name: str | None = None, domain: str | None = None, apollo_id: str | None = None) -> dict:
        params: dict[str, Any] = {"reveal_personal_emails": "false"}
        if email:
            params["email"] = email
        if name:
            params["name"] = name
        if domain:
            params["domain"] = domain
        if apollo_id:
            params["id"] = apollo_id
        payload = self._request("POST", "people/match", params=params)
        for key in ("person", "contact", "match"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return payload if isinstance(payload, dict) else {}
