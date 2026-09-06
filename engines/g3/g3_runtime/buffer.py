from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .errors import G3Error


class BufferClient:
    """Small GraphQL client exposing only reads and draft creation."""

    def __init__(self, api_key: str, endpoint: str = "https://api.buffer.com", timeout: int = 45):
        if not api_key.strip():
            raise G3Error("BUFFER_API_KEY is required")
        self.api_key = api_key.strip()
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def _request(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "company-core-g3/2.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            raise G3Error(f"Buffer HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise G3Error(f"Buffer request failed: {exc}") from exc
        if data.get("errors"):
            messages = "; ".join(str(item.get("message", item)) for item in data["errors"])
            raise G3Error(f"Buffer GraphQL error: {messages}")
        return data.get("data", {})

    def account(self) -> dict[str, Any]:
        data = self._request("""
            query G3Account {
              account { id email organizations { id name } }
            }
        """)
        account = data.get("account")
        if not isinstance(account, dict):
            raise G3Error("Buffer returned no account")
        return account

    def channels(self, organization_id: str) -> list[dict[str, Any]]:
        data = self._request("""
            query G3Channels($input: ChannelsInput!) {
              channels(input: $input) {
                id name displayName descriptor service type isDisconnected isLocked
              }
            }
        """, {"input": {"organizationId": organization_id, "filter": {"product": "publish"}}})
        channels = data.get("channels")
        if not isinstance(channels, list):
            raise G3Error("Buffer returned no channel list")
        return channels

    def create_draft(self, post_input: dict[str, Any]) -> dict[str, Any]:
        # Safety is enforced twice: at service construction and at the network boundary.
        if post_input.get("saveToDraft") is not True:
            raise G3Error("Buffer request denied: saveToDraft must be exactly true")
        if "dueAt" in post_input or post_input.get("mode") != "addToQueue":
            raise G3Error("Buffer request denied: scheduling/publish mode is forbidden")
        data = self._request("""
            mutation G3CreateDraft($input: CreatePostInput!) {
              createPost(input: $input) {
                __typename
                ... on PostActionSuccess { post { id text status } }
                ... on MutationError { message }
              }
            }
        """, {"input": post_input})
        result = data.get("createPost")
        if not isinstance(result, dict):
            raise G3Error("Buffer returned no createPost result")
        if result.get("__typename") != "PostActionSuccess":
            raise G3Error(f"Buffer rejected draft: {result.get('message', result)}")
        post = result.get("post") or {}
        if str(post.get("status", "")).lower() != "draft":
            raise G3Error(f"Buffer did not confirm draft state: {post.get('status')!r}")
        if not post.get("id"):
            raise G3Error("Buffer returned a draft without an ID")
        return post
