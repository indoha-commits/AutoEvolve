from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import G3Error

SCHEMA = "company-core.g3-handoff.v1"
PLATFORMS = {"instagram", "instagram-standalone", "x"}
MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/avif", "video/mp4"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Media:
    path: Path
    sha256: str


@dataclass(frozen=True)
class Entry:
    platform: str
    channel_id: str | None
    values: list[dict[str, Any]]


@dataclass(frozen=True)
class Handoff:
    campaign_id: str
    source_package_sha256: str
    entries: list[Entry]
    root: Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise G3Error(f"{field} must be a non-empty string")
    return value.strip()


def load_handoff(path: str | Path, asset_root: str | Path | None = None) -> Handoff:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise G3Error(f"handoff file not found: {source}")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise G3Error(f"invalid handoff JSON: {exc}") from exc
    if data.get("schema") != SCHEMA:
        raise G3Error(f"schema must be {SCHEMA!r}")
    if data.get("publish_allowed") is not False:
        raise G3Error("publish_allowed must be exactly false")
    if any(key in data for key in ("schedule", "scheduled_at", "publish_at", "type")):
        raise G3Error("handoff contains a publishing or scheduling field")
    root = Path(asset_root).expanduser().resolve() if asset_root else source.parent
    if not root.is_dir():
        raise G3Error(f"asset root not found: {root}")
    raw_entries = data.get("drafts")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise G3Error("drafts must be a non-empty list")
    entries: list[Entry] = []
    for index, raw in enumerate(raw_entries, 1):
        if not isinstance(raw, dict):
            raise G3Error(f"drafts[{index}] must be an object")
        platform = _text(raw.get("platform"), f"drafts[{index}].platform")
        if platform not in PLATFORMS:
            raise G3Error(f"unsupported platform {platform!r}")
        forbidden = {"type", "date", "schedule", "publish", "publish_at"}.intersection(raw)
        if forbidden:
            raise G3Error(f"drafts[{index}] contains forbidden fields: {sorted(forbidden)}")
        if "integration_id" in raw:
            raise G3Error(f"drafts[{index}].integration_id is obsolete; use channel_id")
        channel_id = raw.get("channel_id")
        if channel_id is not None:
            channel_id = _text(channel_id, f"drafts[{index}].channel_id")
        raw_values = raw.get("thread") if platform == "x" else [
            {"content": raw.get("content"), "media": raw.get("media", [])}
        ]
        if not isinstance(raw_values, list) or not raw_values:
            raise G3Error(f"drafts[{index}] must contain at least one content item")
        if platform != "x" and len(raw_values) != 1:
            raise G3Error("Instagram drafts must contain one caption with a media collection")
        values: list[dict[str, Any]] = []
        for part_no, part in enumerate(raw_values, 1):
            if not isinstance(part, dict):
                raise G3Error(f"drafts[{index}] part {part_no} must be an object")
            content = part.get("content")
            if not isinstance(content, str):
                raise G3Error(f"drafts[{index}] part {part_no} content must be a string")
            media_items = part.get("media", [])
            if not isinstance(media_items, list):
                raise G3Error(f"drafts[{index}] part {part_no} media must be a list")
            parsed_media: list[Media] = []
            for media_no, item in enumerate(media_items, 1):
                if not isinstance(item, dict):
                    raise G3Error(f"drafts[{index}] media {media_no} must be an object")
                relative = Path(_text(item.get("path"), "media.path"))
                candidate = (root / relative).resolve() if not relative.is_absolute() else relative.resolve()
                try:
                    candidate.relative_to(root)
                except ValueError as exc:
                    raise G3Error(f"media path escapes asset root: {relative}") from exc
                if not candidate.is_file():
                    raise G3Error(f"media file not found: {candidate}")
                mime = mimetypes.guess_type(candidate.name)[0]
                if mime not in MEDIA_TYPES:
                    raise G3Error(f"unsupported media type for {candidate.name}: {mime}")
                expected = _text(item.get("sha256"), "media.sha256").lower()
                if not SHA256.fullmatch(expected):
                    raise G3Error(f"media.sha256 must be 64 lowercase hexadecimal characters for {candidate.name}")
                actual = file_sha256(candidate)
                if actual != expected:
                    raise G3Error(f"SHA-256 mismatch for {candidate.name}")
                parsed_media.append(Media(candidate, actual))
            if platform.startswith("instagram") and not parsed_media:
                raise G3Error("Instagram drafts require media")
            if platform.startswith("instagram") and len(parsed_media) > 10:
                raise G3Error("Instagram carousel media is limited to 10 items")
            if platform.startswith("instagram") and len(parsed_media) > 1 and any(
                media.path.suffix.lower() == ".mp4" for media in parsed_media
            ):
                raise G3Error("G3 v1 does not mix video into an Instagram carousel")
            if platform == "x" and len(parsed_media) > 4:
                raise G3Error("X content items are limited to 4 media files")
            values.append({"content": content, "media": parsed_media})
        entries.append(Entry(platform, channel_id, values))
    source_sha = _text(data.get("source_package_sha256"), "source_package_sha256").lower()
    if not SHA256.fullmatch(source_sha):
        raise G3Error("source_package_sha256 must be 64 lowercase hexadecimal characters")
    return Handoff(
        campaign_id=_text(data.get("campaign_id"), "campaign_id"),
        source_package_sha256=source_sha,
        entries=entries,
        root=root,
    )
