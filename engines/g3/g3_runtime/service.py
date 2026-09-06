from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from typing import Any, Protocol

from .buffer import BufferClient
from .errors import G3Error
from .handoff import Entry, Handoff, Media
from .ledger import Ledger


class Uploader(Protocol):
    def upload(self, campaign_id: str, media: Media) -> str: ...


def channel_id(entry: Entry) -> str:
    if entry.channel_id:
        return entry.channel_id
    key = "BUFFER_X_CHANNEL_ID" if entry.platform == "x" else "BUFFER_INSTAGRAM_CHANNEL_ID"
    value = os.environ.get(key, "").strip()
    if not value:
        raise G3Error(f"missing {key} and no integration_id in handoff")
    return value


def idempotency_key(handoff: Handoff, entry: Entry, resolved_id: str) -> str:
    stable = {
        "source": handoff.source_package_sha256,
        "campaign": handoff.campaign_id,
        "platform": entry.platform,
        "channel": resolved_id,
        "content": [
            {"content": part["content"], "media": [media.sha256 for media in part["media"]]}
            for part in entry.values
        ],
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


def _asset(url: str, media: Media) -> dict[str, Any]:
    mime = mimetypes.guess_type(media.path.name)[0] or ""
    return {"video" if mime == "video/mp4" else "image": {"url": url}}


def build_input(entry: Entry, resolved_id: str, urls: list[list[str]]) -> dict[str, Any]:
    parts = []
    for part, part_urls in zip(entry.values, urls, strict=True):
        assets = [_asset(url, media) for url, media in zip(part_urls, part["media"], strict=True)]
        parts.append({"text": part["content"], "assets": assets})

    result: dict[str, Any] = {
        "channelId": resolved_id,
        "text": parts[0]["text"],
        "assets": parts[0]["assets"],
        "schedulingType": "automatic",
        "mode": "addToQueue",
        "saveToDraft": True,
        "needsApproval": False,
        "source": "company-core-g3",
    }
    if entry.platform.startswith("instagram"):
        result["metadata"] = {"instagram": {"type": "post", "shouldShareToFeed": True}}
    elif entry.platform == "x" and len(parts) > 1:
        result["metadata"] = {"twitter": {"thread": parts}}
    return result


def submit_handoff(client: BufferClient, uploader: Uploader | None, ledger: Ledger,
                   handoff: Handoff, dry_run: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": True, "campaign_id": handoff.campaign_id, "provider": "buffer",
        "draft_only": True, "publish_allowed": False, "results": [],
    }
    for entry in handoff.entries:
        resolved_id = channel_id(entry)
        key = idempotency_key(handoff, entry, resolved_id)
        old_post = ledger.existing(key)
        if old_post:
            report["results"].append(
                {"platform": entry.platform, "status": "duplicate_skipped", "post_id": old_post}
            )
            continue
        urls: list[list[str]] = []
        for part in entry.values:
            if dry_run:
                urls.append([f"https://dry.invalid/{media.sha256}/{media.path.name}" for media in part["media"]])
            else:
                if uploader is None:
                    raise G3Error("media uploader is required")
                urls.append([uploader.upload(handoff.campaign_id, media) for media in part["media"]])
        post_input = build_input(entry, resolved_id, urls)
        if post_input.get("saveToDraft") is not True or "dueAt" in post_input:
            raise G3Error("internal draft-only invariant failed")
        if dry_run:
            report["results"].append(
                {"platform": entry.platform, "status": "dry_run", "buffer_input": post_input}
            )
            continue
        post = client.create_draft(post_input)
        post_id = str(post["id"])
        ledger.record(key, handoff.campaign_id, entry.platform, post_id)
        report["results"].append(
            {"platform": entry.platform, "status": "draft_confirmed", "post_id": post_id}
        )
    return report
