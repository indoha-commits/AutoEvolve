from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

class HunterError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class HunterClient:
    def __init__(self, api_key: str | None = None, base_url: str = "https://api.hunter.io/v2"):
        self.api_key = api_key or os.getenv("HUNTER_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        if not self.api_key:
            raise HunterError("HUNTER_API_KEY is not configured")

    def _get(self, endpoint: str, params: dict, attempts: int = 3) -> dict:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{self.base_url}/{endpoint}?{query}",
            headers={"X-API-KEY": self.api_key, "Accept": "application/json"},
        )
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                payload = {}
                try:
                    payload = json.loads(exc.read().decode("utf-8"))
                except Exception:
                    pass
                errors = payload.get("errors") or [{}]
                code = errors[0].get("id") or errors[0].get("code")
                detail = errors[0].get("details") or errors[0].get("message") or str(exc)
                if exc.code in {202, 403, 429, 500, 502, 503, 504} and attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise HunterError(detail, exc.code, code) from exc
            except (TimeoutError, urllib.error.URLError) as exc:
                if attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise HunterError(str(exc)) from exc
        raise HunterError("Hunter request failed")

    def domain_search(self, domain: str, limit: int = 10) -> list[dict]:
        payload = self._get("domain-search", {"domain": domain, "limit": min(max(limit, 1), 10), "type": "personal"})
        return payload.get("data", {}).get("emails", [])

    def email_finder(self, *, domain: str, full_name: str) -> dict:
        return self._get("email-finder", {"domain": domain, "full_name": full_name}).get("data", {})

    def verify(self, email: str) -> dict:
        return self._get("email-verifier", {"email": email}, attempts=4).get("data", {})

    def account(self) -> dict:
        return self._get("account", {}).get("data", {})
