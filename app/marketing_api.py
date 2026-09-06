from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from fastapi.responses import FileResponse

from core.marketing_store import (
    add_event,
    clear_variants,
    create_manual_post,
    get_campaign,
    get_manual_post,
    get_variant,
    list_events,
    list_campaigns,
    list_manual_posts,
    update_campaign,
    update_manual_post,
)
from core.marketing_config import MarketingConfig
from core.branding import company_forms_url
from core.state import now_iso, update_task_status
from services.caption_variants import build_short_caption_variants, enrich_platform_copy, _buyer_label
from services.marketing_worker import _last_json, _run, spawn
from services.asset_pack_builder import build_pack_title
from services.asset_pack_worker import spawn_asset_pack
from core.sales_store import connect as sales_connect


router = APIRouter(prefix="/company/marketing", tags=["marketing"])
ROOT = Path(__file__).parent.parent.resolve()


class CampaignLaunchRequest(BaseModel):
    objective: str = Field(min_length=3, max_length=80)
    brief: str = Field(min_length=10, max_length=500)
    social_platforms: list[str] = Field(min_length=1, max_length=4)
    video_platform: str = Field(min_length=2, max_length=40)
    voice_mode: str = Field(default="tts", min_length=3, max_length=20)
    voice_transcript: str | None = Field(default=None, max_length=6000)

    @field_validator("brief", "voice_transcript", mode="before")
    @classmethod
    def _clean_text_fields(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("voice_mode", mode="before")
    @classmethod
    def _normalize_voice_mode(cls, value: object) -> str:
        text = str(value or "tts").strip().lower()
        if text not in {"tts", "real_voice"}:
            raise ValueError("voice_mode must be tts or real_voice")
        return text


class ManualPostSessionRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=40)
    post_type: str = Field(default="carousel", min_length=3, max_length=20)
    destination_url: str | None = Field(default=None, max_length=500)
    link_label: str | None = Field(default=None, max_length=80)

    @field_validator("platform", "post_type", mode="before")
    @classmethod
    def _normalize_choice(cls, value: object) -> str:
        return str(value or "").strip().lower()

    @field_validator("destination_url", "link_label", mode="before")
    @classmethod
    def _clean_optional(cls, value: object) -> object:
        text = str(value or "").strip()
        return text or None


class AssetPackRequest(BaseModel):
    script: str = Field(min_length=20, max_length=12000)
    objective: str = Field(default="awareness", min_length=3, max_length=80)

    @field_validator("script", "objective", mode="before")
    @classmethod
    def _clean_value(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


PROJECTS_ROOT = (ROOT / "projects").resolve()
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
VOICE_UPLOAD_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".webm", ".mp4", ".mov"}


def _safe_json(path_value: str | None) -> Any:
    if not path_value:
        return None
    path = Path(path_value).resolve()
    try:
        path.relative_to(PROJECTS_ROOT)
    except ValueError:
        return None
    if not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _text(value: Any, limit: int = 4000) -> str:
    if isinstance(value, str):
        return value.strip()[:limit]
    if isinstance(value, list):
        parts = [_text(item, limit) for item in value]
        return "\n".join(part for part in parts if part)[:limit]
    if isinstance(value, dict):
        for key in ("transcript", "voiceover", "narration", "script", "text", "body", "copy"):
            if key in value:
                found = _text(value[key], limit)
                if found:
                    return found
    return ""


def _scene_summary(item: Any, index: int) -> dict:
    if not isinstance(item, dict):
        return {"number": index + 1, "text": _text(item, 800)}
    return {
        "number": item.get("scene") or item.get("number") or item.get("slide") or index + 1,
        "time": item.get("time") or item.get("timing") or item.get("duration"),
        "headline": _text(item.get("headline") or item.get("title"), 240),
        "text": _text(item, 1000),
        "visual": _text(item.get("visual_concept") or item.get("visual") or item.get("action"), 700),
    }


def _resolved_buyer(campaign: dict, package: dict[str, Any] | None = None, transcript: str = "", platform_copy: dict[str, Any] | None = None) -> str:
    package = package or {}
    buyer = _text(package.get("buyer") or campaign.get("buyer"), 120)
    topic = _text(package.get("title") or package.get("topic") or campaign.get("topic"), 240)
    return _buyer_label(buyer, topic, transcript, platform_copy or {})


def _artifact_summary(campaign: dict) -> dict:
    package = _safe_json(campaign.get("g1_output_path"))
    media = _safe_json(campaign.get("media_search_path"))
    manifest = _safe_json(campaign.get("asset_manifest_path"))
    artifacts: dict[str, Any] = {}
    if isinstance(package, dict):
        scenes = package.get("scenes") or package.get("slides") or []
        transcript = _text(
            campaign.get("voice_transcript")
            or package.get("transcript")
            or package.get("video_script")
            or package.get("script")
            or package.get("narration"),
        )
        if not transcript and isinstance(scenes, list):
            transcript = "\n\n".join(
                part for part in (_text(scene, 1000) for scene in scenes) if part
            )[:4000]
        platform_copy = enrich_platform_copy(package.get("platform_copy") if isinstance(package.get("platform_copy"), dict) else {})
        resolved_buyer = _resolved_buyer(campaign, package, transcript, platform_copy)
        artifacts["script"] = {
            "title": _text(package.get("title") or package.get("topic"), 240),
            "transcript": transcript,
            "voice_mode": campaign.get("voice_mode") or "tts",
            "voice_handoff_ready": bool(campaign.get("voice_handoff_path")),
            "voice_recording_uploaded": bool(campaign.get("voice_recording_path")),
            "scenes": [_scene_summary(item, index) for index, item in enumerate(scenes[:12])]
            if isinstance(scenes, list) else [],
            "platform_copy": platform_copy,
            "short_caption_variants": build_short_caption_variants(
                topic=_text(package.get("title") or package.get("topic"), 240),
                buyer=resolved_buyer,
                transcript=transcript,
                platform_copy=platform_copy,
            ),
            "status": package.get("status"),
        }
    if media is not None:
        scenes = media.get("scenes", []) if isinstance(media, dict) else []
        artifacts["media"] = {
            "status": media.get("status") if isinstance(media, dict) else None,
            "scene_count": len(scenes) if isinstance(scenes, list) else 0,
            "scenes": [_scene_summary(item, index) for index, item in enumerate(scenes[:12])]
            if isinstance(scenes, list) else [],
            "assets_acquired": manifest is not None,
        }
    if manifest is not None:
        if isinstance(manifest, list):
            asset_count = len(manifest)
        elif isinstance(manifest, dict):
            assets = manifest.get("assets") or manifest.get("scenes") or manifest.get("items") or []
            asset_count = len(assets) if isinstance(assets, list) else 0
        else:
            asset_count = 0
        artifacts.setdefault("media", {})["asset_count"] = asset_count
    if campaign.get("g3_result") is not None:
        artifacts["drafts"] = campaign["g3_result"]
    return artifacts


def _progress(campaign: dict) -> dict:
    stage = campaign.get("current_stage") or "growth_intake"
    base = {
        "growth_intake": 5, "retry_queued": 5, "g1_queued": 8, "g1_campaign": 18, "g1_review": 28,
        "media_queued": 32,
        "media_search": 38, "media_acquisition": 50, "voice_handoff": 58, "voice_render_queued": 62, "variant_rendering": 68,
        "founder_variant_review": 90, "founder_draft_approval": 93,
        "g3_queued": 95, "g3_draft_creation": 97, "buffer_review": 100,
    }.get(stage, 3)
    variants = campaign.get("variants") or []
    if stage == "variant_rendering" and variants:
        finished = sum(item.get("status") in {"ready", "failed"} for item in variants)
        base = 58 + round(30 * finished / len(variants))
    if campaign.get("status") == "drafted":
        base = 100
    labels = {
        "growth_intake": "Campaign accepted", "g1_queued": "Script regeneration queued",
        "g1_campaign": "Writing campaign and script", "media_queued": "Approved script queued for media",
        "g1_review": "Campaign needs review", "media_search": "Finding scene media",
        "media_acquisition": "Downloading selected media", "voice_handoff": "Transcript and scene package ready for real voice",
        "voice_render_queued": "Uploaded narration queued for final render", "variant_rendering": "Rendering video variants",
        "founder_variant_review": "Choose a video variant", "founder_draft_approval": "Ready for draft approval",
        "g3_queued": "Draft creation queued", "g3_draft_creation": "Creating Buffer drafts",
        "buffer_review": "Drafts ready in Buffer", "retry_queued": "Retry queued",
    }
    return {"percent": base, "label": labels.get(stage, stage.replace("_", " "))}


@router.get("/doctor")
def doctor():
    config = MarketingConfig.load()
    checks = config.diagnostics()
    g2 = config.g2_bin
    if checks["g2_bin"]["ok"]:
        try:
            probe = subprocess.run(
                [str(g2), "--help"], text=True, capture_output=True, timeout=5,
            )
            output = f"{probe.stdout}\n{probe.stderr}"
            required = ["search-media", "acquire-media", "render-mixed-video"]
            missing = [command for command in required if command not in output]
            checks["g2_contract"] = {
                "ok": probe.returncode == 0 and not missing,
                "required_commands": required,
                "missing_commands": missing,
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            checks["g2_contract"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        g2_python = g2.parent / "python"
        edge_cli = g2.parent / "edge-tts"
        try:
            voice_probe = subprocess.run(
                [
                    str(g2_python), "-c",
                    "import importlib.metadata as m; import edge_tts; print(m.version('edge-tts'))",
                ],
                text=True,
                capture_output=True,
                timeout=5,
            )
            checks["g2_edge_voice"] = {
                "ok": voice_probe.returncode == 0 and edge_cli.is_file() and edge_cli.stat().st_mode & 0o111 != 0,
                "package_importable": voice_probe.returncode == 0,
                "version": voice_probe.stdout.strip() if voice_probe.returncode == 0 else None,
                "cli_path": str(edge_cli),
                "cli_executable": edge_cli.is_file() and edge_cli.stat().st_mode & 0o111 != 0,
                "error": voice_probe.stderr.strip()[-500:] if voice_probe.returncode else None,
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            checks["g2_edge_voice"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": all(item["ok"] for item in checks.values()), "checks": checks, "draft_only": True}


def _slug(value: str, *, fallback: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    normalized = "-".join(part for part in cleaned.split("-") if part)
    return normalized[:80] or fallback


def _forms_base_url() -> str:
    return (
        str(os.getenv("MARKETING_FORMS_BASE_URL") or "").strip()
        or company_forms_url()
    )


def _forms_host_label() -> str:
    value = _forms_base_url()
    parts = urlsplit(value)
    return parts.netloc or parts.path or "your form"


def _normalize_destination_url(value: str | None) -> str:
    candidate = (value or "").strip() or _forms_base_url()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise HTTPException(422, "destination URL must be a valid http(s) URL")
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, parts.fragment))


def _generated_post_id(platform: str, post_type: str = "carousel") -> str:
    return f"{platform}_{post_type}_{now_iso().replace('-', '').replace(':', '').replace('T', '_')[:15]}"


def _generated_campaign_id(platform: str, post_type: str = "carousel") -> str:
    return f"mkt_{platform}_{post_type}_{now_iso()[:7].replace('-', '')}"


def _generated_source_detail(platform: str, post_type: str = "carousel") -> str:
    return f"{platform}_{post_type}"


def _generated_utm_campaign(platform: str, post_type: str = "carousel") -> str:
    return f"{platform}_{post_type}_{now_iso()[:7].replace('-', '')}"


def _manual_post_asset_root(project_slug: str, post_id: str) -> Path:
    return PROJECTS_ROOT / project_slug / "marketing" / "manual_posts" / post_id


def _asset_pack_root(project_slug: str, pack_id: str) -> Path:
    return PROJECTS_ROOT / project_slug / "marketing" / "asset_packs" / pack_id


def _asset_pack_index_path(root: Path) -> Path:
    return root / "asset_pack.json"


def _read_asset_pack(root: Path) -> dict[str, Any] | None:
    path = _asset_pack_index_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    data["root"] = str(root)
    return data


def list_asset_packs(*, project_slug: str, limit: int = 20) -> list[dict[str, Any]]:
    base = PROJECTS_ROOT / project_slug / "marketing" / "asset_packs"
    if not base.is_dir():
        return []
    roots = sorted((item for item in base.iterdir() if item.is_dir()), key=lambda item: item.name, reverse=True)[: max(1, min(limit, 100))]
    result = []
    for item in roots:
        record = _read_asset_pack(item)
        if record:
            result.append(record)
    return result


def _asset_entries(asset_root: Path) -> list[dict[str, Any]]:
    if not asset_root.is_dir():
        return []
    entries = []
    for item in sorted(asset_root.rglob("*")):
        if not item.is_file():
            continue
        if item.suffix.lower() in {".json", ".log", ".txt"}:
            continue
        entries.append({
            "name": item.name,
            "relative_path": str(item.relative_to(asset_root)),
            "size_bytes": item.stat().st_size,
            "content_type": "video/mp4" if item.suffix.lower() == ".mp4" else "image/jpeg" if item.suffix.lower() in {".jpg", ".jpeg"} else "image/png" if item.suffix.lower() == ".png" else "application/octet-stream",
        })
    return entries


def _public_asset_pack(record: dict[str, Any]) -> dict[str, Any]:
    root = Path(record.get("root") or record.get("pack_root") or "").resolve()
    asset_root = Path(record.get("asset_root") or root / "downloads").resolve()
    assets = []
    for item in _asset_entries(asset_root):
        asset = dict(item)
        asset["asset_url"] = f"/company/marketing/asset-packs/{record['id']}/files/{item['relative_path']}"
        assets.append(asset)
    return {
        "id": record.get("id"),
        "title": record.get("title") or record.get("id"),
        "status": record.get("status") or "ready",
        "objective": record.get("objective") or "awareness",
        "video_platform": record.get("video_platform") or "shorts",
        "scene_count": int(record.get("scene_count") or 0),
        "source_excerpt": str(record.get("source_excerpt") or "")[:360],
        "download_path": str(asset_root),
        "package_path": str(record.get("package_path") or ""),
        "search_path": str(record.get("search_path") or ""),
        "asset_manifest_path": str(record.get("asset_manifest_path") or ""),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "assets": assets,
        "scenes": record.get("scenes") or [],
        "error": record.get("error"),
        "planning_source": record.get("planning_source"),
        "planner_error": record.get("planner_error"),
        "input_word_count": record.get("input_word_count"),
        "input_paragraph_count": record.get("input_paragraph_count"),
        "target_duration_seconds": record.get("target_duration_seconds"),
        "duration_source": record.get("duration_source"),
        "target_scene_count": record.get("target_scene_count"),
        "returned_scene_count": record.get("returned_scene_count"),
    }


def _write_asset_pack_record(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload["pack_root"] = str(root)
    path = _asset_pack_index_path(root)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    payload["root"] = str(root)
    return payload


def _manual_post_status(post: dict) -> str:
    metadata = post.get("metadata") or {}
    return str(metadata.get("workflow_status") or "saved").strip() or "saved"


def _manual_post_buffer_status(post: dict) -> str:
    metadata = post.get("metadata") or {}
    return str(metadata.get("buffer_status") or "not_started").strip() or "not_started"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manual_post_handoff(post: dict) -> tuple[Path, Path]:
    metadata = post.get("metadata") or {}
    platform = str(post.get("platform") or "instagram").strip().lower()
    if platform not in {"instagram", "x"}:
        raise HTTPException(409, "manual Buffer drafts currently support Instagram and X only")
    asset_dir = Path(str(post.get("asset_dir") or "")).resolve()
    try:
        asset_dir.relative_to(PROJECTS_ROOT)
    except ValueError as exc:
        raise HTTPException(403, "asset path is outside the company workspace") from exc
    assets = post.get("assets") or []
    if not assets:
        raise HTTPException(422, "upload and finalize the post before pushing to Buffer")
    media = []
    asset_hashes = []
    for asset in assets:
        filename = str(asset.get("filename") or "").strip()
        if not filename:
            continue
        file_path = (asset_dir / filename).resolve()
        if not file_path.is_file():
            raise HTTPException(409, f"asset file is missing: {filename}")
        try:
            file_path.relative_to(asset_dir)
        except ValueError as exc:
            raise HTTPException(403, "asset file is outside the manual post asset root") from exc
        digest = _sha256(file_path)
        asset_hashes.append(digest)
        media.append({"path": filename, "sha256": digest})
    if not media:
        raise HTTPException(422, "no usable assets found for Buffer handoff")
    if platform == "x" and len(media) > 4:
        raise HTTPException(422, "X drafts are limited to 4 media assets")
    source_package_sha256 = hashlib.sha256((str(post.get("post_id") or "") + "|" + str(metadata.get("generated_caption") or "") + "|" + "|".join(asset_hashes)).encode("utf-8")).hexdigest()
    draft_entry = {"platform": platform}
    if platform == "x":
        draft_entry["thread"] = [{"content": str(metadata.get("generated_caption") or post.get("caption") or ""), "media": media}]
    else:
        draft_entry["content"] = str(metadata.get("generated_caption") or post.get("caption") or "")
        draft_entry["media"] = media
    handoff = {
        "schema": "company-core.g3-handoff.v1",
        "campaign_id": str(post.get("campaign_id") or metadata.get("generated_campaign_id") or post.get("post_id")),
        "source_package_sha256": source_package_sha256,
        "publish_allowed": False,
        "drafts": [draft_entry],
    }
    handoff_path = asset_dir / "g3_manual_handoff.json"
    handoff_path.write_text(json.dumps(handoff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return handoff_path, asset_dir


def _asset_tokens(assets: list[dict[str, Any]]) -> list[str]:
    stopwords = {"slide", "slides", "carousel", "page", "image", "img", "post", "cover", "final", "draft", "copy", "v1", "v2", "v3", "png", "jpg", "jpeg", "webp", "and", "the", "with", "from"}
    words: list[str] = []
    for asset in assets:
        stem = Path(str(asset.get("filename") or "")).stem
        for token in re.split(r"[^a-zA-Z0-9]+", stem.lower()):
            if len(token) < 3 or token in stopwords or token.isdigit():
                continue
            words.append(token)
    seen: list[str] = []
    for word in words:
        if word not in seen:
            seen.append(word)
    return seen


def _asset_seed(assets: list[dict[str, Any]]) -> str:
    words = _asset_tokens(assets)
    if not words:
        return ""
    return " ".join(word.capitalize() for word in words[:3])


def _caption_cta(platform: str, label: str, tracked_url: str | None = None) -> str:
    host = _forms_host_label()
    if platform == "instagram":
        return f"🔗 Link in bio · {label}"
    return f"🔗 Get the {label}: {tracked_url or host}"


def _caption_context(platform: str, tokens: list[str]) -> tuple[str, str, str]:
    joined = set(tokens)
    if {"trade", "data"}.issubset(joined):
        return (
            "⚠️ Bad trade data breaks good ops.",
            "Clean records, traceable handoffs, and AI-backed checks reduce avoidable delay.",
            _caption_cta(platform, "ops audit"),
        )
    if "customs" in joined:
        return (
            "🛃 Customs delays start before the border.",
            "Late files, weak validation, and missing ownership create costly hold-ups.",
            _caption_cta(platform, "customs workflow audit"),
        )
    if "automation" in joined and "control" in joined:
        return (
            "🤖 Automation without control creates rework.",
            "The gain comes from approvals, audit trails, and clean operating records.",
            _caption_cta(platform, "control checklist"),
        )
    if "record" in joined:
        return (
            "🧾 Build the record before the AI layer.",
            "When the workflow is structured first, follow-up, review, and reporting move faster.",
            _caption_cta(platform, "record-first audit"),
        )
    if "operator" in joined or "operators" in joined:
        return (
            "👷 AI should support the operator, not replace them.",
            "Better visibility and cleaner actions help teams move with less manual chasing.",
            _caption_cta(platform, "operator workflow audit"),
        )
    if "workflow" in joined or "handoff" in joined:
        return (
            "⚙️ Workflow gaps compound across every handoff.",
            "One missing update can slow documents, approvals, delivery timing, and follow-through.",
            _caption_cta(platform, "workflow audit"),
        )
    if "visibility" in joined or "control" in joined:
        return (
            "📊 Visibility is only useful when control follows.",
            "Teams need clear next actions, not just more dashboards and alerts.",
            _caption_cta(platform, "ops visibility audit"),
        )
    if "demurrage" in joined or "delay" in joined or "delays" in joined:
        return (
            "⏱️ Delay costs stack long before finance sees them.",
            "The fix is earlier coordination across records, owners, and customer communication.",
            _caption_cta(platform, "delay-reduction audit"),
        )
    return (
        "⚙️ Tighten the workflow before scale adds noise.",
        "Standardized records, faster approvals, and clearer ownership keep ops moving.",
        _caption_cta(platform, "ops audit"),
    )


def _generated_caption(
    platform: str,
    assets: list[dict[str, Any]],
    *,
    link_label: str = "ops audit",
    tracked_url: str | None = None,
) -> str:
    line1, line2, _ = _caption_context(platform, _asset_tokens(assets))
    return f"{line1}\n{line2}\n{_caption_cta(platform, link_label, tracked_url)}"


def _tracked_post_url(destination_url: str, *, platform: str, post_id: str, campaign_id: str, utm_campaign: str, source_detail: str, post_type: str = "carousel") -> str:
    parts = urlsplit(destination_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({
        "utm_source": platform,
        "utm_medium": post_type,
        "utm_campaign": utm_campaign,
        "campaign_id": campaign_id,
        "post_id": post_id,
        "source_detail": source_detail,
    })
    encoded = urlencode({key: value for key, value in query.items() if value != ""})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, encoded, parts.fragment))


def _lead_attribution(post: dict) -> dict[str, Any]:
    post_id = post.get("post_id")
    with sales_connect() as connection:
        rows = connection.execute(
            "SELECT stage, COUNT(*) AS count FROM sales_leads WHERE post_id = ? GROUP BY stage",
            (post_id,),
        ).fetchall()
    by_stage = {row["stage"]: int(row["count"]) for row in rows}
    return {
        "leads": sum(by_stage.values()),
        "qualified": by_stage.get("qualified", 0),
        "draft_ready": by_stage.get("draft_ready", 0),
        "approved": by_stage.get("approved", 0),
        "contacted": by_stage.get("contacted", 0),
        "replied": by_stage.get("replied", 0),
        "won": by_stage.get("won", 0),
        "suppressed": by_stage.get("suppressed", 0),
        "by_stage": by_stage,
    }


def _public_manual_post(post: dict) -> dict:
    metadata = post.get("metadata") or {}
    value = {
        key: post.get(key)
        for key in (
            "id", "project_id", "platform", "post_id", "tracked_url", "source_detail", "asset_dir", "created_at", "updated_at",
        )
    }
    value["campaign_id"] = post.get("campaign_id") or metadata.get("generated_campaign_id")
    value["title"] = post.get("title") or metadata.get("generated_title") or value["post_id"]
    value["creative"] = post.get("creative") or metadata.get("generated_creative")
    value["hook"] = post.get("hook") or metadata.get("generated_hook")
    value["offer"] = post.get("offer") or metadata.get("generated_offer")
    value["cta"] = post.get("cta") or metadata.get("generated_cta")
    value["destination_url"] = post.get("destination_url") or metadata.get("destination_url") or _forms_base_url()
    value["post_type"] = metadata.get("post_type") or "carousel"
    value["link_label"] = metadata.get("link_label") or "ops audit"
    value["caption"] = metadata.get("generated_caption") or _generated_caption(
        value["platform"],
        post.get("assets") or [],
        link_label=value["link_label"],
        tracked_url=value.get("tracked_url"),
    )
    value["utm_campaign"] = metadata.get("utm_campaign")
    value["workflow_status"] = _manual_post_status(post)
    value["buffer_status"] = _manual_post_buffer_status(post)
    value["buffer_ready"] = value["workflow_status"] in {"ready", "buffer_draft", "published"}
    value["asset_count"] = len(post.get("assets") or [])
    value["assets"] = []
    for index, asset in enumerate(post.get("assets") or []):
        item = dict(asset)
        item["asset_url"] = f"/company/marketing/manual-posts/{post['id']}/assets/{index}"
        value["assets"].append(item)
    value["metadata"] = metadata
    value["attribution"] = _lead_attribution(post)
    return value


def _public_campaign(campaign: dict) -> dict:
    resolved_buyer = _resolved_buyer(campaign)
    value = {
        key: campaign.get(key)
        for key in (
            "id", "project_id", "task_id", "objective", "buyer", "topic",
            "social_platforms", "video_platform", "voice_mode", "voice_transcript", "status", "current_stage",
            "g1_campaign_id", "selected_variant_id", "g3_status", "g3_result", "voice_handoff_path", "voice_recording_path",
            "g1_approved_at", "g1_revision_instruction",
            "error", "created_at", "updated_at",
        )
    }
    value["buyer"] = resolved_buyer
    value["variants"] = []
    for variant in campaign.get("variants", []):
        item = {
            key: variant.get(key)
            for key in (
                "id", "platform", "voice", "speed", "status", "sha256",
                "duration_seconds", "error", "created_at", "updated_at",
            )
        }
        if variant.get("status") == "ready":
            item["video_url"] = f"/company/marketing/campaigns/{campaign['id']}/variants/{variant['id']}/video"
        value["variants"].append(item)
    if campaign.get("voice_handoff_path"):
        value["voice_handoff_url"] = f"/company/marketing/campaigns/{campaign['id']}/voice-handoff"
    value["voice_recording_uploaded"] = bool(campaign.get("voice_recording_path"))
    value["progress"] = _progress(campaign)
    value["artifacts"] = _artifact_summary(campaign)
    value["events"] = list_events(campaign["id"])[-12:]
    return value


@router.post("/asset-packs")
def create_asset_pack(payload: AssetPackRequest):
    from core.state import get_active_project

    project = get_active_project()
    if not project:
        raise HTTPException(409, "set an active project before creating an asset pack")
    pack_id = f"assets_{now_iso().replace('-', '').replace(':', '').replace('T', '_')[:15]}"
    root = _asset_pack_root(project["slug"], pack_id)
    root.mkdir(parents=True, exist_ok=True)
    created_at = now_iso()
    script_path = root / "source_script.txt"
    script_path.write_text(payload.script.strip() + "\n", encoding="utf-8")
    package_path = root / "scene_plan.json"
    search_path = root / "media_search.json"
    asset_root = root / "downloads"
    record = _write_asset_pack_record(root, {
        "id": pack_id,
        "title": build_pack_title(payload.script),
        "status": "queued",
        "objective": payload.objective,
        "scene_count": 0,
        "source_excerpt": payload.script[:1200],
        "source_script_path": str(script_path),
        "package_path": str(package_path),
        "search_path": str(search_path),
        "asset_root": str(asset_root),
        "asset_manifest_path": str(asset_root / "asset_manifest.json"),
        "created_at": created_at,
        "updated_at": created_at,
        "scenes": [],
    })
    spawn_asset_pack(project["slug"], pack_id)
    return {
        "ok": True,
        "pack": _public_asset_pack(record),
        "message": "Asset pack queued. Images will appear in the project folder when ready.",
    }


@router.get("/asset-packs")
def asset_packs(limit: int = 20):
    from core.state import get_active_project

    project = get_active_project()
    project_slug = project["slug"] if project else None
    packs = list_asset_packs(project_slug=project_slug, limit=limit) if project_slug else []
    return {"packs": [_public_asset_pack(item) for item in packs]}


@router.get("/asset-packs/{pack_id}/files/{relative_path:path}")
def asset_pack_file(pack_id: str, relative_path: str):
    from core.state import get_active_project

    project = get_active_project()
    if not project:
        raise HTTPException(404, "asset pack not found")
    root = _asset_pack_root(project["slug"], pack_id).resolve()
    record = _read_asset_pack(root)
    if not record:
        raise HTTPException(404, "asset pack not found")
    asset_root = Path(record.get("asset_root") or root / "downloads").resolve()
    path = (asset_root / relative_path).resolve()
    try:
        path.relative_to(asset_root)
    except ValueError as exc:
        raise HTTPException(403, "asset path is outside the company workspace") from exc
    if not path.is_file():
        raise HTTPException(404, "asset file not found")
    media_type = "video/mp4" if path.suffix.lower() == ".mp4" else "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png" if path.suffix.lower() == ".png" else "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.post("/manual-posts/session")
def create_manual_post_session(payload: ManualPostSessionRequest):
    from core.state import get_active_project

    project = get_active_project()
    if not project:
        raise HTTPException(409, "set an active project before saving a manual post")
    clean_platform = payload.platform.strip().lower()
    if clean_platform not in {"instagram", "x", "linkedin"}:
        raise HTTPException(422, "unsupported platform")
    post_type = payload.post_type.strip().lower()
    if post_type not in {"carousel", "video"}:
        raise HTTPException(422, "post_type must be carousel or video")
    clean_post_id = _generated_post_id(clean_platform, post_type)
    generated_campaign_id = _generated_campaign_id(clean_platform, post_type)
    generated_source_detail = _generated_source_detail(clean_platform, post_type)
    generated_utm_campaign = _generated_utm_campaign(clean_platform, post_type)
    destination_url = _normalize_destination_url(payload.destination_url)
    link_label = (payload.link_label or "ops audit").strip()
    tracked_url = _tracked_post_url(
        destination_url,
        platform=clean_platform,
        post_id=clean_post_id,
        campaign_id=generated_campaign_id,
        utm_campaign=generated_utm_campaign,
        source_detail=generated_source_detail,
        post_type=post_type,
    )
    asset_root = _manual_post_asset_root(project["slug"], clean_post_id)
    asset_root.mkdir(parents=True, exist_ok=True)
    try:
        post = create_manual_post(
            project_id=project["id"],
            campaign_id=None,
            platform=clean_platform,
            post_id=clean_post_id,
            title=f"{clean_platform.title()} {post_type}",
            creative=None,
            hook=None,
            offer=None,
            cta=None,
            destination_url=destination_url,
            tracked_url=tracked_url,
            source_detail=generated_source_detail,
            asset_dir=str(asset_root),
            assets=[],
            metadata={
                "generated_campaign_id": generated_campaign_id,
                "utm_campaign": generated_utm_campaign,
                "generated_offer": "forms access",
                "generated_cta": _caption_cta(clean_platform, link_label, tracked_url),
                "destination_url": destination_url,
                "post_type": post_type,
                "link_label": link_label,
                "workflow_status": "uploading",
                "buffer_status": "not_started",
            },
        )
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "post": _public_manual_post(post), "message": f"{post_type.title()} record created. Upload media to continue."}


@router.post("/manual-posts/{post_record_id}/assets")
async def upload_manual_post_asset(post_record_id: str, asset: UploadFile = File(...)):
    post = get_manual_post(post_record_id)
    if not post:
        raise HTTPException(404, "manual post not found")
    metadata = dict(post.get("metadata") or {})
    post_type = str(metadata.get("post_type") or "carousel")
    filename = Path(asset.filename or ("video.mp4" if post_type == "video" else "asset.png")).name
    if not filename:
        raise HTTPException(422, "asset filename is required")
    suffix = Path(filename).suffix.lower()
    allowed = {".mp4"} if post_type == "video" else {".png"}
    if suffix not in allowed:
        expected = "one MP4 video" if post_type == "video" else "PNG carousel assets"
        raise HTTPException(422, f"this post requires {expected}")
    if post_type == "video" and post.get("assets"):
        raise HTTPException(422, "video posts accept exactly one MP4")
    asset_dir = Path(post.get("asset_dir") or "").resolve()
    try:
        asset_dir.relative_to(PROJECTS_ROOT)
    except ValueError as exc:
        raise HTTPException(403, "asset path is outside the company workspace") from exc
    asset_dir.mkdir(parents=True, exist_ok=True)
    target = asset_dir / filename
    with target.open("wb") as handle:
        shutil.copyfileobj(asset.file, handle)
    assets_meta = [dict(item) for item in (post.get("assets") or []) if str(item.get("filename") or "") != filename]
    assets_meta.append({
        "filename": filename,
        "content_type": asset.content_type,
        "size_bytes": target.stat().st_size,
    })
    metadata["workflow_status"] = "uploading"
    updated = update_manual_post(
        post_record_id,
        assets_json=json.dumps(assets_meta, ensure_ascii=False),
        metadata_json=json.dumps(metadata, ensure_ascii=False),
    )
    return {"ok": True, "post": _public_manual_post(updated), "message": f"Saved {filename}."}


@router.post("/manual-posts/{post_record_id}/finalize")
def finalize_manual_post(post_record_id: str):
    post = get_manual_post(post_record_id)
    if not post:
        raise HTTPException(404, "manual post not found")
    assets_meta = [dict(item) for item in (post.get("assets") or [])]
    if not assets_meta:
        raise HTTPException(422, "upload at least one media asset before finalizing")
    platform = str(post.get("platform") or "instagram").strip().lower() or "instagram"
    metadata = dict(post.get("metadata") or {})
    post_type = str(metadata.get("post_type") or "carousel")
    if post_type == "video" and len(assets_meta) != 1:
        raise HTTPException(422, "video posts require exactly one MP4")
    generated_title = _asset_seed(assets_meta) or post.get("title") or f"{platform.title()} {post_type}"
    generated_caption = _generated_caption(
        platform,
        assets_meta,
        link_label=str(metadata.get("link_label") or "ops audit"),
        tracked_url=str(post.get("tracked_url") or ""),
    )
    metadata.update({
        "generated_caption": generated_caption,
        "generated_title": generated_title,
        "generated_offer": metadata.get("generated_offer") or "forms access",
        "generated_cta": metadata.get("generated_cta") or _caption_cta(
            platform,
            str(metadata.get("link_label") or "ops audit"),
            str(post.get("tracked_url") or ""),
        ),
        "destination_url": metadata.get("destination_url") or _forms_base_url(),
        "workflow_status": "ready",
        "buffer_status": metadata.get("buffer_status") or "not_started",
    })
    updated = update_manual_post(
        post_record_id,
        title=generated_title,
        metadata_json=json.dumps(metadata, ensure_ascii=False),
    )
    return {"ok": True, "post": _public_manual_post(updated), "message": f"{post_type.title()} saved locally and marked ready for Buffer handoff."}


@router.post("/manual-posts")
async def create_manual_post_from_ui(
    platform: str = Form(...),
    post_type: str = Form(default="carousel"),
    destination_url: str | None = Form(default=None),
    link_label: str | None = Form(default=None),
    assets: list[UploadFile] = File(default=[]),
):
    session = create_manual_post_session(ManualPostSessionRequest(
        platform=platform,
        post_type=post_type,
        destination_url=destination_url,
        link_label=link_label,
    ))
    post = session["post"]
    for upload in assets:
        await upload_manual_post_asset(post["id"], upload)
    finalized = finalize_manual_post(post["id"])
    return {"ok": True, "post": finalized["post"], "message": "Manual post saved with backend-generated tracking and caption."}


@router.post("/manual-posts/{post_record_id}/buffer-draft")
def create_manual_post_buffer_draft(post_record_id: str):
    post = get_manual_post(post_record_id)
    if not post:
        raise HTTPException(404, "manual post not found")
    if _manual_post_status(post) != "ready":
        raise HTTPException(409, "finalize the manual post before pushing it to Buffer")
    if _manual_post_buffer_status(post) in {"queued", "running"}:
        raise HTTPException(409, "Buffer draft creation is already running for this post")
    config = MarketingConfig.load()
    if not config.g3_bin.is_file():
        raise HTTPException(409, f"G3 command not found: {config.g3_bin}")
    metadata = dict(post.get("metadata") or {})
    metadata["buffer_status"] = "running"
    update_manual_post(post_record_id, metadata_json=json.dumps(metadata, ensure_ascii=False))
    try:
        handoff_path, asset_root = _manual_post_handoff(post)
        output = _last_json(_run(
            [
                str(config.g3_bin), "draft", str(handoff_path), "--asset-root", str(asset_root),
                "--ledger", str(asset_root / "g3.sqlite3"),
            ],
            cwd=config.g3_root,
            env_file=config.g3_env_file,
            timeout=config.stage_timeout,
            log_path=asset_root / "logs" / "g3_manual.log",
        ))
    except Exception as exc:
        failed_metadata = dict(post.get("metadata") or {})
        failed_metadata["buffer_status"] = "failed"
        failed_metadata["buffer_error"] = f"{type(exc).__name__}: {exc}"
        update_manual_post(post_record_id, metadata_json=json.dumps(failed_metadata, ensure_ascii=False))
        raise HTTPException(502, f"manual Buffer draft failed: {exc}") from exc
    completed_metadata = dict((get_manual_post(post_record_id) or post).get("metadata") or {})
    completed_metadata["buffer_status"] = "drafted"
    completed_metadata["buffer_result"] = output
    completed_metadata.pop("buffer_error", None)
    completed_metadata["workflow_status"] = "buffer_draft"
    updated = update_manual_post(post_record_id, metadata_json=json.dumps(completed_metadata, ensure_ascii=False))
    return {"ok": True, "post": _public_manual_post(updated), "result": output, "message": "Buffer draft created for the manual post."}


@router.get("/manual-posts")
def manual_posts(limit: int = 50):
    from core.state import get_active_project

    project = get_active_project()
    project_id = project["id"] if project else None
    return {"posts": [_public_manual_post(item) for item in list_manual_posts(limit=limit, project_id=project_id)]}


@router.get("/manual-posts/{post_record_id}/assets/{asset_index}")
def manual_post_asset(post_record_id: str, asset_index: int):
    post = get_manual_post(post_record_id)
    if not post:
        raise HTTPException(404, "manual post not found")
    assets = post.get("assets") or []
    if asset_index < 0 or asset_index >= len(assets):
        raise HTTPException(404, "asset not found")
    asset = assets[asset_index]
    asset_path = (Path(post.get("asset_dir") or "") / str(asset.get("filename") or "")).resolve()
    try:
        asset_path.relative_to(PROJECTS_ROOT)
    except ValueError as exc:
        raise HTTPException(403, "asset path is outside the company workspace") from exc
    if not asset_path.is_file():
        raise HTTPException(404, "asset file not found")
    return FileResponse(asset_path, media_type=str(asset.get("content_type") or "application/octet-stream"))


@router.post("/campaigns")
def create_campaign_from_ui(payload: CampaignLaunchRequest):
    from core.state import create_task, get_active_project
    from core.marketing_store import create_campaign

    project = get_active_project()
    if not project:
        raise HTTPException(409, "set an active project before launching a campaign")

    social_platforms = list(dict.fromkeys(item.strip().lower() for item in payload.social_platforms if item.strip()))
    if not social_platforms:
        raise HTTPException(422, "choose at least one social platform")
    voice_mode = payload.voice_mode.strip().lower()
    voice_transcript = (payload.voice_transcript or "").strip() or None
    campaign_brief = payload.brief.strip()
    transcript_note = f" Use this approved narration transcript: {voice_transcript}" if voice_transcript else ""
    request = (
        f"Create a {payload.objective} marketing campaign. Infer the best buyer and topic from this direction: {campaign_brief}. "
        f"Prepare {', '.join(social_platforms)} drafts and a {payload.video_platform} video variant using {voice_mode}.{transcript_note}"
    )
    task = create_task(
        project_id=project["id"],
        agent="growth",
        task_type="campaign",
        input_text=request,
    )
    campaign = create_campaign(
        project_id=project["id"],
        task_id=task["id"],
        request=request,
        objective=payload.objective.strip(),
        buyer="ai_selected",
        topic=campaign_brief,
        social_platforms=social_platforms,
        video_platform=payload.video_platform.strip().lower(),
        voice_mode=voice_mode,
        voice_transcript=voice_transcript,
    )
    spawn(campaign["id"], "pipeline")
    return {
        "ok": True,
        "campaign": _public_campaign(campaign),
        "message": "Campaign accepted. Growth, media, and rendering are now running in the background.",
    }


@router.get("/campaigns")
def campaigns(limit: int = 20):
    from core.state import get_active_project

    project = get_active_project()
    packs = list_asset_packs(project_slug=project["slug"], limit=limit) if project else []
    return {
        "campaigns": [_public_campaign(item) for item in list_campaigns(limit=limit)],
        "manual_posts": [_public_manual_post(item) for item in list_manual_posts(limit=limit)],
        "asset_packs": [_public_asset_pack(item) for item in packs],
    }


@router.get("/campaigns/{campaign_id}")
def campaign(campaign_id: str):
    item = get_campaign(campaign_id)
    if not item:
        raise HTTPException(404, "campaign not found")
    return _public_campaign(item)


@router.get("/campaigns/{campaign_id}/voice-handoff")
def campaign_voice_handoff(campaign_id: str):
    campaign = get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "campaign not found")
    path = Path(campaign.get("voice_handoff_path") or "").resolve()
    if not path.is_file():
        raise HTTPException(404, "voice handoff is not ready")
    try:
        path.relative_to(PROJECTS_ROOT)
    except ValueError as exc:
        raise HTTPException(403, "voice handoff path is outside the company workspace") from exc
    return FileResponse(path, media_type="application/json", filename=path.name)


@router.post("/campaigns/{campaign_id}/voice-recording")
async def upload_campaign_voice_recording(campaign_id: str, recording: UploadFile = File(...)):
    campaign = get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "campaign not found")
    if str(campaign.get("voice_mode") or "tts") != "real_voice":
        raise HTTPException(409, "campaign is not using real voice mode")
    if campaign.get("status") not in {"needs_voice_recording", "failed"}:
        raise HTTPException(409, "campaign is not waiting for a voice recording")
    filename = Path(recording.filename or "narration.wav").name
    suffix = Path(filename).suffix.lower()
    if suffix not in VOICE_UPLOAD_EXTENSIONS:
        raise HTTPException(422, "unsupported voice recording format")
    campaign_root = (PROJECTS_ROOT / (Path(campaign.get("g1_output_path") or "").resolve().relative_to(PROJECTS_ROOT).parts[0]) )
    voice_dir = Path(campaign.get("g1_output_path") or "").resolve().parent / "voice"
    try:
        voice_dir.relative_to(PROJECTS_ROOT)
    except ValueError as exc:
        raise HTTPException(403, "voice recording path is outside the company workspace") from exc
    voice_dir.mkdir(parents=True, exist_ok=True)
    target = voice_dir / filename
    with target.open("wb") as handle:
        shutil.copyfileobj(recording.file, handle)
    update_campaign(
        campaign_id,
        voice_recording_path=str(target),
        status="queued",
        current_stage="voice_render_queued",
        error=None,
    )
    add_event(campaign_id, "voice.recording_uploaded", {"filename": filename, "size_bytes": target.stat().st_size})
    spawn(campaign_id, "real_voice")
    refreshed = get_campaign(campaign_id)
    return {"ok": True, "campaign": _public_campaign(refreshed), "message": "Voice recording uploaded and final render queued."}


@router.get("/campaigns/{campaign_id}/variants/{variant_id}/video")
def variant_video(campaign_id: str, variant_id: str):
    campaign = get_campaign(campaign_id)
    variant = get_variant(variant_id)
    if not campaign or not variant or variant["campaign_id"] != campaign_id:
        raise HTTPException(404, "video variant not found")
    path = Path(variant.get("video_path") or "").resolve()
    expected_root = (
        Path(__file__).parent.parent
        / "projects"
    ).resolve()
    try:
        path.relative_to(expected_root)
    except ValueError as exc:
        raise HTTPException(403, "video path is outside the company workspace") from exc
    if variant.get("status") != "ready" or not path.is_file() or path.suffix.lower() != ".mp4":
        raise HTTPException(404, "video file is not ready")
    return FileResponse(path, media_type="video/mp4")


@router.post("/campaigns/{campaign_id}/variants/{variant_id}/select")
def select_variant(campaign_id: str, variant_id: str):
    campaign = get_campaign(campaign_id)
    variant = get_variant(variant_id)
    if not campaign or not variant or variant["campaign_id"] != campaign_id:
        raise HTTPException(404, "video variant not found")
    if campaign["status"] not in {"variants_ready", "selected"}:
        raise HTTPException(409, "campaign is not ready for variant selection")
    if variant["status"] != "ready":
        raise HTTPException(409, "video variant is not ready")
    update_campaign(
        campaign_id,
        selected_variant_id=variant_id,
        status="selected",
        current_stage="founder_draft_approval",
        error=None,
    )
    add_event(campaign_id, "founder.variant_selected", {"variant_id": variant_id})
    return {"ok": True, "campaign_id": campaign_id, "selected_variant_id": variant_id}


@router.post("/campaigns/{campaign_id}/drafts")
def create_drafts(campaign_id: str):
    campaign = get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "campaign not found")
    if campaign["status"] != "selected" or not campaign.get("selected_variant_id"):
        raise HTTPException(409, "select a video variant first")
    if campaign["g3_status"] in {"queued", "running", "drafted"}:
        raise HTTPException(409, f"G3 is already {campaign['g3_status']}")
    update_campaign(
        campaign_id,
        g3_status="queued",
        status="drafting",
        current_stage="g3_queued",
        error=None,
    )
    add_event(campaign_id, "founder.g3_drafts_approved", {"draft_only": True})
    spawn(campaign_id, "g3")
    return {"ok": True, "campaign_id": campaign_id, "g3_status": "queued", "publish_allowed": False}


@router.post("/campaigns/{campaign_id}/approve-script")
def approve_script(campaign_id: str):
    campaign = get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "campaign not found")
    if campaign["status"] != "needs_campaign_review" or not campaign.get("g1_output_path"):
        raise HTTPException(409, "campaign is not waiting for script approval")
    approved_at = now_iso()
    update_task_status(int(campaign["task_id"]), "queued")
    update_campaign(
        campaign_id,
        g1_approved_at=approved_at,
        status="queued",
        current_stage="media_queued",
        error=None,
    )
    add_event(campaign_id, "founder.g1_approved", {"approved_at": approved_at})
    spawn(campaign_id, "media")
    return {"ok": True, "campaign_id": campaign_id, "status": "queued", "next_stage": "media_search"}


@router.post("/campaigns/{campaign_id}/regenerate-script")
def regenerate_script(campaign_id: str, payload: dict = Body(default={})):
    campaign = get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "campaign not found")
    if campaign["status"] not in {"needs_campaign_review", "failed"}:
        raise HTTPException(409, "campaign is not available for script regeneration")
    direction = str(payload.get("direction") or "").strip()
    if len(direction) < 10 or len(direction) > 500:
        raise HTTPException(422, "new creative direction must be between 10 and 500 characters")
    cleared = clear_variants(campaign_id)
    update_task_status(int(campaign["task_id"]), "queued")
    update_campaign(
        campaign_id,
        g1_approved_at=None,
        g1_revision_instruction=direction,
        media_search_path=None,
        asset_root=None,
        asset_manifest_path=None,
        selected_variant_id=None,
        g3_handoff_path=None,
        g3_status="not_requested",
        g3_result_json=None,
        status="queued",
        current_stage="g1_queued",
        error=None,
    )
    add_event(campaign_id, "founder.g1_regeneration_requested", {
        "direction": direction,
        "cleared_variant_records": cleared,
    })
    spawn(campaign_id, "pipeline")
    return {"ok": True, "campaign_id": campaign_id, "status": "queued", "next_stage": "g1_campaign"}


@router.post("/campaigns/{campaign_id}/retry")
def retry_campaign(campaign_id: str):
    campaign = get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "campaign not found")
    if campaign["status"] == "needs_campaign_review":
        raise HTTPException(409, "approve the saved script or regenerate it with a new direction")
    if campaign["status"] != "failed":
        raise HTTPException(409, "only failed campaigns can be retried")
    if campaign["g3_status"] == "failed" and campaign.get("selected_variant_id"):
        update_campaign(
            campaign_id,
            status="drafting",
            current_stage="g3_queued",
            g3_status="queued",
            error=None,
        )
        add_event(campaign_id, "founder.g3_retry", {"draft_only": True})
        spawn(campaign_id, "g3")
        return {"ok": True, "campaign_id": campaign_id, "g3_status": "queued"}
    media_stages = {
        "pipeline", "media_queued", "media_search", "media_acquisition", "variant_rendering",
    }
    resume_stage = "media" if campaign.get("g1_output_path") and campaign.get("current_stage") in media_stages else "pipeline"
    update_task_status(int(campaign["task_id"]), "queued")
    update_campaign(
        campaign_id,
        status="queued",
        current_stage="media_queued" if resume_stage == "media" else "g1_queued",
        error=None,
    )
    add_event(campaign_id, "founder.retry", {"resume_stage": resume_stage})
    spawn(campaign_id, resume_stage)
    return {"ok": True, "campaign_id": campaign_id, "status": "queued", "resume_stage": resume_stage}
