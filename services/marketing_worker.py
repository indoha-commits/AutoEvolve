from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

from dotenv import dotenv_values

from core.marketing_config import MarketingConfig
from core.marketing_store import (
    add_event,
    create_variant,
    get_campaign,
    get_variant,
    update_campaign,
    update_variant,
)
from core.state import complete_task, fail_task, get_project, update_task_status
from services.caption_variants import enrich_platform_copy


ROOT = Path(__file__).parent.parent

G2_CAMPAIGN_FIELDS = (
    "campaign_id", "objective", "buyer", "narrative", "format", "platforms",
    "claim_ids", "slides", "platform_copy", "brand", "generation", "quality", "status",
)
G2_SLIDE_FIELDS = (
    "number", "purpose", "headline", "body", "visual_concept", "asset_strategy",
    "image_queries", "claim_ids",
)
G2_NESTED_FIELDS = {
    "platform_copy": ("instagram_caption", "x_post"),
    "brand": ("canvas", "logo_asset", "logo_variant", "logo_position"),
    "generation": ("research", "concepts", "writer", "deterministic_actions"),
    "quality": (
        "schema_valid", "all_claims_grounded", "forbidden_terms", "unsupported_claims",
        "duplicate_score", "brand_score", "buyer_score", "editorial_score", "rewrite_count",
        "warnings",
    ),
}


def _environment(path: Path | None) -> dict[str, str]:
    environment = dict(os.environ)
    if path and path.is_file():
        for key, value in dotenv_values(path).items():
            if value is not None:
                environment[key] = value
    return environment


def _command_environment(command: list[str], env_file: Path | None) -> dict[str, str]:
    environment = _environment(env_file)
    executable_dir = str(Path(command[0]).expanduser().resolve().parent)
    inherited_path = environment.get("PATH", "")
    environment["PATH"] = executable_dir + (os.pathsep + inherited_path if inherited_path else "")
    return environment


def _run(
    command: list[str],
    *,
    cwd: Path,
    env_file: Path | None,
    timeout: int,
    log_path: Path,
) -> str:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=_command_environment(command, env_file),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        log_path.write_text(
            f"COMMAND: {command!r}\nTIMEOUT: {timeout}s\nSTDOUT:\n{exc.stdout or ''}\nSTDERR:\n{exc.stderr or ''}\n",
            encoding="utf-8",
        )
        raise RuntimeError(f"command timed out after {timeout}s: {command[0]}") from exc
    log_path.write_text(
        f"COMMAND: {command!r}\nEXIT: {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}\n",
        encoding="utf-8",
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "unknown command failure").strip()
        raise RuntimeError(f"{command[0]} exited {result.returncode}: {detail[-1500:]}")
    return result.stdout


def _last_json(text: str) -> dict:
    decoder = json.JSONDecoder()
    candidates: list[tuple[int, dict]] = []
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, consumed = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append((consumed, value))
    if not candidates:
        raise RuntimeError("command did not return a JSON object")
    return max(candidates, key=lambda item: item[0])[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _default_transcript_from_package(package: dict) -> str:
    for key in ("transcript", "video_script", "script", "narration"):
        value = package.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    scenes = package.get("scenes") or package.get("slides") or []
    parts: list[str] = []
    if isinstance(scenes, list):
        for scene in scenes:
            if isinstance(scene, dict):
                text = str(scene.get("voiceover") or scene.get("narration") or scene.get("body") or scene.get("text") or "").strip()
            else:
                text = str(scene or "").strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts).strip()


def _transcript_chunks(transcript: str, count: int) -> list[str]:
    if count <= 0:
        return []
    blocks = [part.strip() for part in transcript.replace("\r", "").split("\n\n") if part.strip()]
    if len(blocks) == count:
        return blocks
    lines = [part.strip() for part in transcript.replace("\r", "").splitlines() if part.strip()]
    if len(lines) == count:
        return lines
    if not blocks:
        return []
    if len(blocks) > count:
        merged = blocks[:count - 1]
        merged.append("\n\n".join(blocks[count - 1:]))
        return merged
    if len(blocks) == 1:
        return [blocks[0]] * count
    chunks = [""] * count
    for index, block in enumerate(blocks):
        slot = min(count - 1, round(index * count / max(len(blocks), 1)))
        chunks[slot] = f"{chunks[slot]}\n\n{block}".strip()
    return [item or blocks[min(index, len(blocks) - 1)] for index, item in enumerate(chunks)]


def _apply_transcript_override(package: dict, transcript: str | None) -> dict:
    resolved = (transcript or "").strip() or _default_transcript_from_package(package)
    if not resolved:
        return package
    package["transcript"] = resolved
    package["narration"] = resolved
    package["script"] = resolved
    scenes = package.get("scenes") or package.get("slides") or []
    if isinstance(scenes, list) and scenes:
        chunks = _transcript_chunks(resolved, len(scenes))
        if len(chunks) == len(scenes):
            for scene, chunk in zip(scenes, chunks):
                if isinstance(scene, dict):
                    scene["voiceover"] = chunk
    return package


def _project_g2_package(package: dict) -> dict:
    """Build the strict G2 input without discarding narration from the source artifact."""
    projected = {key: package[key] for key in G2_CAMPAIGN_FIELDS if key in package}
    for key, fields in G2_NESTED_FIELDS.items():
        value = package.get(key)
        if isinstance(value, dict):
            projected[key] = {field: value[field] for field in fields if field in value}

    slides = package.get("slides")
    if isinstance(slides, list):
        projected_slides = []
        for slide in slides:
            if not isinstance(slide, dict):
                projected_slides.append(slide)
                continue
            item = {key: slide[key] for key in G2_SLIDE_FIELDS if key in slide}
            narration = slide.get("voiceover") or slide.get("narration")
            if isinstance(narration, str) and narration.strip():
                item["body"] = narration.strip()
            projected_slides.append(item)
        projected["slides"] = projected_slides
    return projected


def _write_g2_package(
    campaign_id: str,
    package: dict,
    source_path: Path,
    root: Path,
) -> tuple[Path, dict]:
    projected = _project_g2_package(package)
    output_path = root / "g1_campaign_g2.json"
    encoded = json.dumps(projected, indent=2, ensure_ascii=False) + "\n"
    changed = not output_path.is_file() or output_path.read_text(encoding="utf-8") != encoded
    output_path.write_text(encoded, encoding="utf-8")
    if changed:
        add_event(campaign_id, "artifact.g2_package_ready", {
            "source": source_path.name,
            "source_sha256": _sha256(source_path),
            "g2_sha256": _sha256(output_path),
        })
    return output_path, projected


def _build_voice_handoff(campaign: dict, package_path: Path, package: dict, asset_root: Path, manifest_path: Path, root: Path) -> Path:
    transcript = _default_transcript_from_package(package)
    scenes = package.get("scenes") or package.get("slides") or []
    scene_items = []
    for index, scene in enumerate(scenes if isinstance(scenes, list) else []):
        if isinstance(scene, dict):
            scene_items.append({
                "number": scene.get("number") or scene.get("scene") or scene.get("slide") or index + 1,
                "headline": scene.get("headline") or scene.get("title"),
                "visual_concept": scene.get("visual_concept") or scene.get("visual") or scene.get("action"),
                "transcript": scene.get("voiceover") or scene.get("narration") or scene.get("body") or scene.get("text"),
            })
        else:
            scene_items.append({"number": index + 1, "transcript": str(scene)})
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    payload = {
        "schema": "company-core.voice-handoff.v1",
        "campaign_id": campaign["id"],
        "voice_mode": campaign.get("voice_mode") or "real_voice",
        "video_platform": campaign.get("video_platform"),
        "transcript": transcript,
        "recording_instructions": "Record one clean narration matching the transcript. Keep pacing deliberate and operational.",
        "package_path": str(package_path.relative_to(root)),
        "asset_root": str(asset_root.relative_to(root)),
        "asset_manifest": str(manifest_path.relative_to(root)),
        "asset_manifest_sha256": _sha256(manifest_path) if manifest_path.is_file() else None,
        "source_package_sha256": _sha256(package_path),
        "render_manifest": manifest,
        "scenes": scene_items,
    }
    handoff_path = root / "voice_handoff.json"
    handoff_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return handoff_path

def _approved_media_package(campaign: dict, package: dict, root: Path) -> tuple[Path, dict]:
    source_path = Path(campaign["g1_output_path"])
    warnings = list((package.get("quality") or {}).get("warnings") or [])
    if package.get("status") == "ready_for_media" and not warnings:
        return source_path, package
    approved_at = campaign.get("g1_approved_at")
    requires_founder = package.get("status") != "ready_for_media"
    if requires_founder and not approved_at:
        raise RuntimeError("G1 package still requires founder approval")
    approved = json.loads(json.dumps(package))
    approved["status"] = "ready_for_media"
    approved.setdefault("quality", {})["warnings"] = []
    source_sha256 = _sha256(source_path)
    authorization = f"founder_approval approved_at={approved_at}" if approved_at else "g1_ready_for_media_advisory"
    action = (
        f"media_gate_resolution authorization={authorization} "
        f"source_sha256={source_sha256} resolved_warnings={json.dumps(warnings, ensure_ascii=False)}"
    )
    actions = approved.setdefault("generation", {}).setdefault("deterministic_actions", [])
    if action not in actions:
        actions.append(action)
    approved_path = root / "g1_campaign_approved.json"
    encoded = json.dumps(approved, indent=2, ensure_ascii=False) + "\n"
    changed = not approved_path.is_file() or approved_path.read_text(encoding="utf-8") != encoded
    approved_path.write_text(encoded, encoding="utf-8")
    if changed:
        add_event(campaign["id"], "artifact.g1_approval_package_ready", {
            "source_sha256": source_sha256,
            "approved_sha256": _sha256(approved_path),
            "authorization": authorization,
            "resolved_warnings": warnings,
        })
    return approved_path, approved


def _work_root(campaign: dict) -> Path:
    project = get_project(int(campaign["project_id"]))
    if not project:
        raise RuntimeError("campaign project no longer exists")
    root = ROOT / "projects" / project["slug"] / "marketing" / "campaigns" / campaign["id"]
    root.mkdir(parents=True, exist_ok=True)
    return root


def _stage(campaign_id: str, stage: str, status: str = "running") -> None:
    update_campaign(campaign_id, current_stage=stage, status=status, error=None)
    add_event(campaign_id, f"stage.{stage}", {"status": status})


def run_pipeline(campaign_id: str) -> None:
    campaign = get_campaign(campaign_id)
    if not campaign:
        raise RuntimeError(f"campaign not found: {campaign_id}")
    config = MarketingConfig.load()
    failed_checks = [name for name, item in config.diagnostics().items() if not item["ok"] and name != "g3_bin"]
    if failed_checks:
        raise RuntimeError(f"marketing tools are not configured: {', '.join(failed_checks)}")
    root = _work_root(campaign)
    logs = root / "logs"
    update_task_status(int(campaign["task_id"]), "running")

    _stage(campaign_id, "g1_campaign")
    topic = campaign["topic"]
    if campaign.get("g1_revision_instruction"):
        topic = f"{topic}. New creative direction: {campaign['g1_revision_instruction']}"
    command = [
        str(config.g1_bin), "--root", str(config.g1_root), "create",
        "--objective", campaign["objective"], "--buyer", campaign["buyer"],
        "--topic", topic,
    ]
    for platform in campaign["social_platforms"]:
        command.extend(["--platform", platform])
    g1_result = _last_json(_run(
        command, cwd=config.g1_root, env_file=config.g1_env_file,
        timeout=config.stage_timeout, log_path=logs / "g1.log",
    ))
    package = g1_result.get("campaign") or g1_result
    package = _apply_transcript_override(package, campaign.get("voice_transcript"))
    package_path = root / "g1_campaign.json"
    package_path.write_text(json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    inferred_buyer = str(package.get("buyer") or campaign.get("buyer") or "ai_selected").strip() or "ai_selected"
    inferred_topic = str(
        package.get("title")
        or package.get("topic")
        or package.get("narrative")
        or campaign.get("topic")
        or campaign.get("request")
        or "campaign direction"
    ).strip()
    update_campaign(
        campaign_id,
        buyer=inferred_buyer,
        topic=inferred_topic[:240],
        g1_campaign_id=package.get("campaign_id"),
        g1_output_path=str(package_path),
    )
    add_event(campaign_id, "artifact.script_ready", {
        "campaign_id": package.get("campaign_id"),
        "scene_count": len(package.get("scenes") or package.get("slides") or []),
    })
    quality = package.get("quality") or {}
    if package.get("status") != "ready_for_media":
        update_campaign(
            campaign_id,
            status="needs_campaign_review",
            current_stage="g1_review",
            error="G1 package did not pass the media gate",
        )
        add_event(campaign_id, "campaign.needs_review", {"quality": quality})
        complete_task(int(campaign["task_id"]), json.dumps({"campaign": campaign_id, "status": "needs_campaign_review"}))
        return
    if quality.get("warnings"):
        add_event(campaign_id, "campaign.advisory_warnings", {"warnings": quality["warnings"]})
    campaign = dict(campaign)
    campaign["g1_output_path"] = str(package_path)
    campaign["g1_campaign_id"] = package.get("campaign_id")
    run_media_pipeline(
        campaign_id, campaign=campaign, package=package, config=config, root=root, logs=logs,
    )


def run_media_pipeline(
    campaign_id: str,
    *,
    campaign: dict | None = None,
    package: dict | None = None,
    config: MarketingConfig | None = None,
    root: Path | None = None,
    logs: Path | None = None,
) -> None:
    campaign = campaign or get_campaign(campaign_id)
    if not campaign:
        raise RuntimeError(f"campaign not found: {campaign_id}")
    if not campaign.get("g1_output_path"):
        raise RuntimeError("campaign has no saved G1 package")
    source_package_path = Path(campaign["g1_output_path"])
    if not source_package_path.is_file():
        raise RuntimeError("saved G1 package is missing")
    package = package or json.loads(source_package_path.read_text(encoding="utf-8"))
    if package.get("status") != "ready_for_media" and not campaign.get("g1_approved_at"):
        raise RuntimeError("G1 package still requires founder approval")
    config = config or MarketingConfig.load()
    failed_checks = [name for name, item in config.diagnostics().items() if not item["ok"] and name != "g3_bin"]
    if failed_checks:
        raise RuntimeError(f"marketing tools are not configured: {', '.join(failed_checks)}")
    root = root or _work_root(campaign)
    logs = logs or root / "logs"
    package_path, package = _approved_media_package(campaign, package, root)
    g2_package_path, _ = _write_g2_package(campaign_id, package, package_path, root)
    update_task_status(int(campaign["task_id"]), "running")

    _stage(campaign_id, "media_search")
    search_path = root / "media_search.json"
    _run(
        [
            str(config.g2_bin), "search-media", str(g2_package_path),
            "--output", str(search_path), "--platform", campaign["video_platform"],
            "--deadline", str(config.media_deadline), "--limit", str(config.media_limit),
        ],
        cwd=config.g2_root, env_file=config.g2_env_file, timeout=config.stage_timeout,
        log_path=logs / "g2_search.log",
    )
    update_campaign(campaign_id, media_search_path=str(search_path))
    add_event(campaign_id, "artifact.media_search_ready", {"path": search_path.name})

    _stage(campaign_id, "media_acquisition")
    asset_root = root / "assets"
    _run(
        [str(config.g2_bin), "acquire-media", str(search_path), "--output", str(asset_root)],
        cwd=config.g2_root, env_file=config.g2_env_file, timeout=config.stage_timeout,
        log_path=logs / "g2_acquire.log",
    )
    manifest_path = asset_root / "asset_manifest.json"
    update_campaign(campaign_id, asset_root=str(asset_root), asset_manifest_path=str(manifest_path))
    add_event(campaign_id, "artifact.media_assets_ready", {"manifest": manifest_path.name})

    voice_mode = str(campaign.get("voice_mode") or "tts").strip().lower()
    if voice_mode == "real_voice":
        handoff_path = _build_voice_handoff(campaign, package_path, package, asset_root, manifest_path, root)
        update_campaign(
            campaign_id,
            status="needs_voice_recording",
            current_stage="voice_handoff",
            voice_handoff_path=str(handoff_path),
            error=None,
        )
        add_event(campaign_id, "artifact.voice_handoff_ready", {"path": handoff_path.name})
        complete_task(
            int(campaign["task_id"]),
            json.dumps({"campaign": campaign_id, "status": "needs_voice_recording", "voice_handoff": handoff_path.name}),
        )
        return

    _stage(campaign_id, "variant_rendering")
    successes = 0
    refreshed = get_campaign(campaign_id) or campaign
    existing_by_voice = {
        item["voice"]: item
        for item in refreshed.get("variants", [])
    }
    variants = []
    for voice in config.voices:
        variant = existing_by_voice.get(voice)
        if variant is None:
            variant = create_variant(campaign_id, campaign["video_platform"], voice, config.speed)
        variants.append(variant)
    add_event(campaign_id, "variants.queued", {
        "count": len(variants),
        "voices": [variant["voice"] for variant in variants],
    })
    for variant in variants:
        voice = variant["voice"]
        if variant.get("status") == "ready" and variant.get("video_path"):
            successes += 1
            continue
        variant_root = root / "variants" / variant["id"]
        update_variant(variant["id"], status="rendering")
        add_event(campaign_id, "variant.rendering", {"variant_id": variant["id"], "voice": voice})
        try:
            output = _last_json(_run(
                [
                    str(config.g2_bin), "render-mixed-video", str(g2_package_path),
                    "--asset-manifest", str(manifest_path), "--asset-root", str(asset_root),
                    "--showcase", str(config.g2_showcase),
                    "--outro", str(config.g2_outro), "--output", str(variant_root),
                    "--platform", campaign["video_platform"], "--review",
                    "--voice-mode", "edge", "--voice", voice,
                    "--speed", str(config.speed), "--subtitle-engine", "provider-timed",
                ],
                cwd=config.g2_root, env_file=config.g2_env_file, timeout=config.stage_timeout,
                log_path=logs / f"variant_{variant['id']}.log",
            ))
            video_path = variant_root / output["video"]
            render_manifest = variant_root / "video_render_manifest.json"
            update_variant(
                variant["id"], status="ready", video_path=str(video_path),
                manifest_path=str(render_manifest), sha256=output.get("sha256") or _sha256(video_path),
                duration_seconds=float(output.get("duration_seconds") or 0), error=None,
            )
            add_event(campaign_id, "variant.ready", {"variant_id": variant["id"], "voice": voice})
            successes += 1
        except Exception as exc:
            update_variant(variant["id"], status="failed", error=f"{type(exc).__name__}: {exc}")
            add_event(campaign_id, "variant.failed", {"variant_id": variant["id"], "voice": voice, "error": str(exc)})

    if not successes:
        raise RuntimeError("all configured video variants failed to render")
    update_campaign(campaign_id, status="variants_ready", current_stage="founder_variant_review", error=None)
    complete_task(
        int(campaign["task_id"]),
        json.dumps({"campaign": campaign_id, "status": "variants_ready", "variants": successes}),
    )
    add_event(campaign_id, "campaign.variants_ready", {"count": successes})


def _probe_media_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "ffprobe failed").strip()
        raise RuntimeError(f"ffprobe failed: {detail[-800:]}")
    try:
        duration = float((result.stdout or "").strip())
    except ValueError as exc:
        raise RuntimeError("ffprobe did not return a duration") from exc
    if duration <= 0:
        raise RuntimeError("voice recording duration must be positive")
    return duration


def _srt_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3600 * 1000)
    minutes, remainder = divmod(remainder, 60 * 1000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _write_proportional_srt(transcript: str, scene_durations: list[float], output_path: Path) -> None:
    chunks = _transcript_chunks(transcript, len(scene_durations)) or [transcript.strip()] * len(scene_durations)
    lines: list[str] = []
    cursor = 0.0
    for index, duration in enumerate(scene_durations):
        text = (chunks[index] if index < len(chunks) else "").strip() or (chunks[-1] if chunks else transcript.strip())
        if not text:
            continue
        end = cursor + max(0.1, float(duration or 0))
        lines.append(f"{index + 1}\n{_srt_timestamp(cursor)} --> {_srt_timestamp(end)}\n{text}\n")
        cursor = end
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _mux_audio_track(video_path: Path, audio_path: Path, output_path: Path) -> None:
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", str(output_path),
        ],
        text=True,
        capture_output=True,
        timeout=300,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "ffmpeg failed").strip()
        raise RuntimeError(f"ffmpeg mux failed: {detail[-1500:]}")


def run_real_voice_render(campaign_id: str) -> None:
    campaign = get_campaign(campaign_id)
    if not campaign:
        raise RuntimeError(f"campaign not found: {campaign_id}")
    if str(campaign.get("voice_mode") or "tts") != "real_voice":
        raise RuntimeError("campaign is not configured for real voice")
    if not campaign.get("voice_recording_path"):
        raise RuntimeError("campaign has no uploaded voice recording")
    if not campaign.get("asset_manifest_path") or not campaign.get("g1_output_path"):
        raise RuntimeError("campaign media assets are not ready")
    config = MarketingConfig.load()
    root = _work_root(campaign)
    logs = root / "logs"
    package_path = Path(campaign["g1_output_path"])
    package = json.loads(package_path.read_text(encoding="utf-8"))
    g2_package_path, _ = _write_g2_package(campaign_id, package, package_path, root)
    manifest_path = Path(campaign["asset_manifest_path"])
    asset_root = Path(campaign["asset_root"])
    audio_path = Path(campaign["voice_recording_path"])
    if not audio_path.is_file():
        raise RuntimeError("uploaded voice recording is missing")
    audio_duration = _probe_media_duration(audio_path)
    transcript = str(campaign.get("voice_transcript") or "").strip() or _default_transcript_from_package(package)
    _stage(campaign_id, "variant_rendering")
    refreshed = get_campaign(campaign_id) or campaign
    variant = None
    for item in refreshed.get("variants", []):
        if item.get("voice") == "real_voice":
            variant = item
            break
    if variant is None:
        variant = create_variant(campaign_id, campaign["video_platform"], "real_voice", 1.0)
    variant_root = root / "variants" / variant["id"]
    update_variant(variant["id"], status="rendering", error=None)
    add_event(campaign_id, "variant.rendering", {"variant_id": variant["id"], "voice": "real_voice", "duration_seconds": audio_duration})
    output = _last_json(_run(
        [
            str(config.g2_bin), "render-mixed-video", str(g2_package_path),
            "--asset-manifest", str(manifest_path), "--asset-root", str(asset_root),
            "--showcase", str(config.g2_showcase),
            "--outro", str(config.g2_outro), "--output", str(variant_root),
            "--platform", campaign["video_platform"], "--review",
            "--voice-mode", "silent", "--duration", str(audio_duration),
            "--subtitle-engine", "none",
        ],
        cwd=config.g2_root, env_file=config.g2_env_file, timeout=config.stage_timeout,
        log_path=logs / f"variant_{variant['id']}.log",
    ))
    silent_video_path = variant_root / output["video"]
    final_video_path = variant_root / "real_voice_review.mp4"
    _mux_audio_track(silent_video_path, audio_path, final_video_path)
    render_manifest_path = variant_root / "video_render_manifest.json"
    render_manifest = json.loads(render_manifest_path.read_text(encoding="utf-8")) if render_manifest_path.is_file() else {}
    scene_durations = []
    for scene in render_manifest.get("scenes", []):
        try:
            scene_durations.append(float(scene.get("duration_seconds") or 0))
        except (TypeError, ValueError):
            scene_durations.append(0.0)
    if not any(scene_durations):
        scene_count = len((render_manifest.get("scenes") or []) or [1])
        even = audio_duration / max(1, scene_count)
        scene_durations = [even] * max(1, scene_count)
    subtitle_path = variant_root / "captions.srt"
    _write_proportional_srt(transcript, scene_durations, subtitle_path)
    render_manifest.update({
        "video": final_video_path.name,
        "sha256": _sha256(final_video_path),
        "duration_seconds": audio_duration,
        "voice_mode": "uploaded_audio",
        "voice": "real_voice",
        "subtitle_engine": "transcript-proportional",
        "subtitle_sidecar": subtitle_path.name,
        "uploaded_audio": audio_path.name,
    })
    render_manifest_path.write_text(json.dumps(render_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    update_variant(
        variant["id"],
        status="ready",
        video_path=str(final_video_path),
        manifest_path=str(render_manifest_path),
        sha256=_sha256(final_video_path),
        duration_seconds=audio_duration,
        error=None,
    )
    update_campaign(campaign_id, status="variants_ready", current_stage="founder_variant_review", error=None)
    complete_task(int(campaign["task_id"]), json.dumps({"campaign": campaign_id, "status": "variants_ready", "variants": 1, "voice": "real_voice"}))
    add_event(campaign_id, "variant.ready", {"variant_id": variant["id"], "voice": "real_voice"})
    add_event(campaign_id, "campaign.variants_ready", {"count": 1, "voice_mode": "real_voice"})


def _build_handoff(campaign: dict, variant: dict, root: Path) -> Path:
    package = json.loads(Path(campaign["g1_output_path"]).read_text(encoding="utf-8"))
    render_manifest = json.loads(Path(variant["manifest_path"]).read_text(encoding="utf-8"))
    video_path = Path(variant["video_path"]).resolve()
    relative = video_path.relative_to(root.resolve())
    media = [{"path": str(relative), "sha256": variant["sha256"]}]
    copy = enrich_platform_copy(package.get("platform_copy") or {})
    drafts = []
    for platform in campaign["social_platforms"]:
        if platform == "instagram":
            drafts.append({"platform": "instagram", "content": copy.get("instagram_caption", ""), "media": media})
        elif platform == "x":
            drafts.append({"platform": "x", "thread": [{"content": copy.get("x_post", ""), "media": media}]})
    handoff = {
        "schema": "company-core.g3-handoff.v1",
        "campaign_id": package["campaign_id"],
        "source_package_sha256": render_manifest["source_package_sha256"],
        "publish_allowed": False,
        "drafts": drafts,
    }
    path = root / "g3_handoff.json"
    path.write_text(json.dumps(handoff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def run_g3(campaign_id: str) -> None:
    campaign = get_campaign(campaign_id)
    if not campaign:
        raise RuntimeError(f"campaign not found: {campaign_id}")
    if not campaign.get("selected_variant_id"):
        raise RuntimeError("select a video variant before creating drafts")
    variant = get_variant(campaign["selected_variant_id"])
    if not variant or variant["status"] != "ready":
        raise RuntimeError("selected video variant is not ready")
    config = MarketingConfig.load()
    if not config.g3_bin.is_file():
        raise RuntimeError(f"G3 command not found: {config.g3_bin}")
    root = _work_root(campaign)
    handoff = _build_handoff(campaign, variant, root)
    update_campaign(
        campaign_id, status="drafting", current_stage="g3_draft_creation",
        g3_status="running", g3_handoff_path=str(handoff), error=None,
    )
    add_event(campaign_id, "g3.started", {"variant_id": variant["id"]})
    add_event(campaign_id, "artifact.g3_handoff_ready", {"draft_only": True})
    output = _last_json(_run(
        [
            str(config.g3_bin), "draft", str(handoff), "--asset-root", str(root),
            "--ledger", str(root / "g3.sqlite3"),
        ],
        cwd=config.g3_root, env_file=config.g3_env_file, timeout=config.stage_timeout,
        log_path=root / "logs" / "g3.log",
    ))
    update_campaign(
        campaign_id, status="drafted", current_stage="buffer_review",
        g3_status="drafted", g3_result_json=json.dumps(output), error=None,
    )
    add_event(campaign_id, "g3.drafted", output)


def _fail(campaign_id: str, stage: str, exc: Exception) -> None:
    campaign = get_campaign(campaign_id)
    error = f"{type(exc).__name__}: {exc}"
    failed_at = (campaign or {}).get("current_stage") or stage
    update_campaign(
        campaign_id, status="failed", current_stage=failed_at,
        g3_status="failed" if stage == "g3" else (campaign or {}).get("g3_status", "not_requested"),
        error=error,
    )
    add_event(campaign_id, f"{failed_at}.failed", {
        "worker_stage": stage,
        "error": error,
        "traceback": traceback.format_exc(limit=8),
    })
    if campaign and stage != "g3":
        fail_task(int(campaign["task_id"]), error)


def spawn(campaign_id: str, stage: str = "pipeline") -> None:
    subprocess.Popen(
        [sys.executable, "-m", "services.marketing_worker", campaign_id, "--stage", stage],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_id")
    parser.add_argument("--stage", choices=["pipeline", "media", "g3", "real_voice"], default="pipeline")
    args = parser.parse_args()
    try:
        if args.stage == "g3":
            run_g3(args.campaign_id)
        elif args.stage == "media":
            run_media_pipeline(args.campaign_id)
        elif args.stage == "real_voice":
            run_real_voice_render(args.campaign_id)
        else:
            run_pipeline(args.campaign_id)
        return 0
    except Exception as exc:
        _fail(args.campaign_id, args.stage, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
