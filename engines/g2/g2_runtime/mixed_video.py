from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from .captions import write_ass
from .fingerprint import file_sha256
from .models import AssetRecord, G1Campaign
from .storyboard import MixedScene, MixedVideoPlan, compile_storyboard
from .subtitles import FasterWhisperSubtitles, write_provider_srt
from .video_layers import render_layers
from .voice import probe_duration, silence, voice_engine


def load_asset_records(path: str | Path) -> list[AssetRecord]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("assets", [])
    if not isinstance(value, list):
        raise ValueError("asset manifest must be a list or contain an assets list")
    return [AssetRecord.model_validate(item) for item in value]


def _asset_path(record: AssetRecord, root: str | Path, review: bool) -> Path:
    asset_root = Path(root).resolve()
    path = (asset_root / record.local_path).resolve()
    if asset_root not in path.parents:
        raise ValueError("asset path escaped asset root")
    if not path.is_file():
        raise ValueError(f"asset not found: {record.local_path}")
    if record.sha256 and file_sha256(path) != record.sha256:
        raise ValueError(f"asset checksum mismatch: {record.local_path}")
    if not review and not record.approved:
        raise ValueError(f"slide {record.slide_number} asset is not Founder-approved")
    return path


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _render_scene(
    scene: MixedScene,
    layers: dict,
    audio: Path,
    output: Path,
    width: int,
    height: int,
    fps: int,
) -> None:
    duration = scene.duration_seconds
    # Static full-frame holds are intentional. They eliminate fractional crop
    # jitter, synthetic camera movement and H.264 motion artifacts.
    filter_complex = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height},setsar=1,fps={fps}[bg];"
        "[1:v]format=rgba[ov];"
        "[bg][ov]overlay=0:0:format=auto[v]"
    )
    if layers.get("background_kind") == "video":
        background_input = [
            "-stream_loop", "-1", "-ss", f"{float(layers.get('clip_start_seconds', 0.0)):.3f}",
            "-i", layers["background"],
        ]
        video_tune = []
    else:
        background_input = ["-loop", "1", "-framerate", str(fps), "-i", layers["background"]]
        video_tune = ["-tune", "stillimage"]
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        *background_input,
        "-loop", "1", "-framerate", str(fps), "-i", layers["overlay"],
        "-i", str(audio), "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "2:a:0", "-t", f"{duration:.3f}",
        "-r", str(fps), "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "medium",
        *video_tune, "-crf", "0", "-pix_fmt", "yuv420p", "-g", str(fps * 2),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(output),
    ])


def _concat(scene_files: list[Path], destination: Path) -> None:
    list_path = destination.parent / "concat.txt"
    list_path.write_text("".join(f"file '{path.resolve()}'\n" for path in scene_files), encoding="utf-8")
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_path), "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
        "-c", "copy", "-movflags", "+faststart",
        str(destination),
    ])


def _burn_captions(source: Path, captions: Path, destination: Path) -> None:
    escaped = str(captions.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-vf", f"ass='{escaped}'", "-c:v", "libx264", "-preset", "slow", "-tune", "stillimage",
        "-crf", "16", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-c:a", "copy", "-movflags", "+faststart", str(destination),
    ])


def _burn_srt(source: Path, subtitles: Path, destination: Path, platform: str) -> None:
    escaped = str(subtitles.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    if platform == "youtube":
        style = "FontName=Nimbus Sans,FontSize=22,PrimaryColour=&H00FFFFFF,Outline=2,Shadow=1,Alignment=2,MarginV=55"
    else:
        # Plain SRT is rendered by libass on its virtual script canvas rather
        # than directly in 1080x1920 pixels. Pixel-like values such as 18/230
        # therefore scale into huge type positioned near the top. These values
        # resolve to roughly 58-64 px type in the vertical bottom safe zone.
        margin = 46 if platform == "tiktok" else 40
        style = (
            "FontName=Nimbus Sans,FontSize=9,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,Outline=1.4,Shadow=0.6,"
            f"Alignment=2,MarginL=24,MarginR=24,MarginV={margin}"
        )
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-vf", f"subtitles='{escaped}':force_style='{style}'",
        "-c:v", "libx264", "-preset", "slow", "-tune", "stillimage", "-crf", "16",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-c:a", "copy", "-movflags", "+faststart", str(destination),
    ])


def render_video(
    campaign: G1Campaign,
    plan: MixedVideoPlan,
    records: list[AssetRecord],
    asset_root: str | Path,
    showcase: str | Path,
    outro: str | Path,
    output: str | Path,
    review: bool = False,
    voice_mode: str = "silent",
    voice: str = "af_heart",
    subtitle_engine: str = "auto",
    whisper_model: str = "base.en",
    speed: float = 1.0,
    duration_scale: float = 1.0,
) -> dict:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg and ffprobe are required")
    root = Path(output)
    layers_root = root / "layers"
    audio_root = root / "audio"
    scenes_root = root / "scenes"
    for directory in (layers_root, audio_root, scenes_root):
        directory.mkdir(parents=True, exist_ok=True)
    by_slide = {record.slide_number: record for record in records}
    width, height = (int(value) for value in plan.resolution.split("x"))
    engine = voice_engine(voice_mode, voice, speed)
    actual_scenes: list[MixedScene] = []
    provenance = []
    voice_provenance = []
    scene_timings = []
    scene_files = []
    for original in plan.scenes:
        scene = original.model_copy(deep=True)
        asset = None
        record = by_slide.get(scene.asset_slide_number or -1)
        if scene.purpose == "control_system":
            showcase_path = Path(showcase)
            if not showcase_path.is_file():
                raise ValueError(f"configured showcase asset not found: {showcase_path}")
            if record:
                provenance.append({
                    "scene": scene.number,
                    "candidate_id": record.candidate_id,
                    "provider": record.provider,
                    "source_url": record.source_url,
                    "sha256": record.sha256,
                    "approved": record.approved,
                    "source_type": record.source_type,
                    "media_type": record.media_type,
                    "used": False,
                    "reason": "configured showcase overrides manifest control-system media",
                })
            provenance.append({
                "scene": scene.number,
                "candidate_id": "configured:showcase",
                "provider": "configured_owned_media",
                "source_url": str(showcase_path),
                "sha256": file_sha256(showcase_path),
                "approved": True,
                "source_type": "owned",
                "media_type": "video" if showcase_path.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"} else "image",
                "used": True,
                "reason": "authoritative configured control-system showcase",
            })
        elif scene.asset_slide_number:
            if not record:
                raise ValueError(f"slide {scene.asset_slide_number} has no background asset")
            else:
                asset = _asset_path(record, asset_root, review)
                provenance.append({
                    "scene": scene.number,
                    "candidate_id": record.candidate_id,
                    "provider": record.provider,
                    "source_url": record.source_url,
                    "sha256": record.sha256,
                    "approved": record.approved,
                    "source_type": record.source_type,
                    "media_type": record.media_type,
                    "clip_start_seconds": record.clip_start_seconds,
                    "clip_end_seconds": record.clip_end_seconds,
                    "used": True,
                })
        audio_path = audio_root / f"scene_{scene.number:02d}.wav"
        if engine is not None:
            voice_result = engine.synthesize(scene.narration, audio_path, scene.purpose)
            scene.duration_seconds = max(0.65, round(voice_result.duration_seconds, 3))
            voice_provenance.append({"scene": scene.number, **voice_result.metadata()})
            scene_timings.append({
                "scene": scene.number,
                "narration": scene.narration,
                "duration_seconds": scene.duration_seconds,
                "timing_path": str(voice_result.timing_path) if voice_result.timing_path else None,
                "timing_offset_seconds": voice_result.timing_offset_seconds,
            })
        elif voice_mode == "silent":
            scene.duration_seconds = max(0.65, round(scene.duration_seconds * duration_scale, 3))
            silence(audio_path, scene.duration_seconds)
            scene_timings.append({
                "scene": scene.number,
                "narration": scene.narration,
                "duration_seconds": scene.duration_seconds,
                "timing_path": None,
                "timing_offset_seconds": 0.0,
            })
        else:
            raise ValueError("voice_mode must be silent, edge, system, or kokoro")
        layers = render_layers(
            scene,
            asset,
            outro,
            layers_root,
            review,
            (width, height),
            showcase_path=showcase,
        )
        if record and record.media_type == "video":
            layers["clip_start_seconds"] = record.clip_start_seconds
        scene_path = scenes_root / f"scene_{scene.number:02d}.mp4"
        _render_scene(scene, layers, audio_path, scene_path, width, height, plan.fps)
        scene_files.append(scene_path)
        actual_scenes.append(scene)
    joined = root / "joined_without_captions.mp4"
    _concat(scene_files, joined)
    final = root / f"{campaign.campaign_id}_review.mp4" if review else root / f"{campaign.campaign_id}.mp4"
    selected_subtitles = subtitle_engine
    if selected_subtitles == "auto":
        selected_subtitles = "provider-timed" if voice_mode != "silent" else "none"
    captions = None
    if selected_subtitles == "faster-whisper":
        captions = FasterWhisperSubtitles(model=whisper_model).transcribe(joined, root / "captions.srt")
        _burn_srt(joined, captions, final, plan.platform)
    elif selected_subtitles == "provider-timed":
        captions = write_provider_srt(scene_timings, root / "captions.srt")
        _burn_srt(joined, captions, final, plan.platform)
    elif selected_subtitles == "timed-proof":
        captions = write_ass(actual_scenes, root / "captions.ass")
        _burn_captions(joined, captions, final)
    elif selected_subtitles == "none":
        shutil.copy2(joined, final)
    else:
        raise ValueError("subtitle_engine must be auto, provider-timed, faster-whisper, timed-proof, or none")
    manifest = {
        "media_type": "mixed_media_short",
        "campaign_id": campaign.campaign_id,
        "source_package_sha256": plan.source_package_sha256,
        "video": final.name,
        "sha256": file_sha256(final),
        "platform": plan.platform,
        "resolution": plan.resolution,
        "fps": plan.fps,
        "duration_seconds": round(probe_duration(final), 3),
        "voice_mode": voice_mode,
        "voice": voice if voice_mode in {"edge", "kokoro", "system"} else None,
        "voice_provenance": voice_provenance,
        "subtitle_engine": selected_subtitles,
        "subtitle_sidecar": captions.name if captions else None,
        "review": review,
        "publish_allowed": False,
        "visual_language": {
            "imagery": "full_frame_context",
            "motion": "source_video_or_stable_static_hold",
            "missing_media": "hard_fail_except_configured_showcase",
            "control_system_priority": "authoritative_configured_showcase",
            "editorial_cards": False,
            "headline_overlays": False,
            "subtitles": "bottom_safe_zone",
            "branding": "configured_showcase_and_premade_outro",
        },
        "scenes": [scene.model_dump(mode="json") for scene in actual_scenes],
        "asset_provenance": provenance,
        "synchronization": {
            "scene_boundaries": "generated_audio_duration",
            "subtitle_timing": "provider_boundaries_or_audio_duration",
            "concat": "lossless_scene_stream_copy",
            "delivery_encode": "single_h264_crf16_after_subtitles",
        },
        "status": "review_rendered" if review else "ready_for_founder_review",
    }
    (root / "video_render_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def plan_and_render(campaign: G1Campaign, duration: int, platform: str = "shorts", **kwargs) -> dict:
    return render_video(campaign, compile_storyboard(campaign, duration, platform), **kwargs)
