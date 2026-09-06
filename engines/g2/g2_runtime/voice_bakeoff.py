from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .storyboard import MixedVideoPlan
from .voice import EdgeVoice, probe_duration


DEFAULT_MARKETING_VOICES = [
    "en-US-AndrewNeural",
    "en-US-BrianNeural",
    "en-US-EmmaNeural",
    "en-US-AvaNeural",
    "en-US-AriaNeural",
    "en-US-RogerNeural",
    "en-GB-RyanNeural",
    "en-GB-SoniaNeural",
    "en-KE-ChilembaNeural",
    "en-KE-AsiliaNeural",
    "en-NG-AbeoNeural",
    "en-NG-EzinneNeural",
    "en-ZA-LukeNeural",
    "en-TZ-ElimuNeural",
]


def parse_edge_voice_list(value: str, prefix: str = "en-") -> list[str]:
    voices = []
    for line in value.splitlines():
        first = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        if first.startswith(prefix) and first.endswith("Neural"):
            voices.append(first)
    return sorted(set(voices))


def installed_edge_voices(prefix: str = "en-") -> list[str]:
    binary = shutil.which("edge-tts")
    if not binary:
        raise RuntimeError("voice bakeoff requires: pip install edge-tts")
    result = subprocess.run(
        [binary, "--list-voices"], text=True, capture_output=True, check=True, timeout=20,
    )
    voices = parse_edge_voice_list(result.stdout, prefix)
    if not voices:
        raise RuntimeError(f"Edge returned no voices matching {prefix!r}")
    return voices


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def _concat_wav(parts: list[Path], destination: Path) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("voice bakeoff requires ffmpeg")
    manifest = destination.with_suffix(".concat.txt")
    lines = []
    for part in parts:
        escaped = str(part.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(manifest),
            "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(destination),
        ], check=True)
    finally:
        manifest.unlink(missing_ok=True)


def render_voice_bakeoff(
    plan: MixedVideoPlan,
    output: str | Path,
    voices: list[str],
    speed: float = 1.0,
    scene_timeout_seconds: float = 45.0,
    attempts: int = 2,
    retry_base_seconds: float = 1.0,
) -> dict:
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    results = []
    interrupted = False

    def checkpoint() -> dict:
        report = {
            "campaign_id": plan.campaign_id,
            "platform": plan.platform,
            "speed": speed,
            "scene_timeout_seconds": scene_timeout_seconds,
            "attempts_per_scene": attempts,
            "voice_count_requested": len(list(dict.fromkeys(voices))),
            "voice_count_processed": len(results),
            "passed": sum(item["status"] == "pass" for item in results),
            "failed": sum(item["status"] == "fail" for item in results),
            "video_rendered": False,
            "results": results,
            "status": (
                "interrupted_resume_available" if interrupted
                else "complete" if len(results) == len(list(dict.fromkeys(voices))) and all(item["status"] == "pass" for item in results)
                else "complete_with_failures" if len(results) == len(list(dict.fromkeys(voices)))
                else "in_progress"
            ),
        }
        (root / "voice_bakeoff_report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8",
        )
        return report

    for voice in dict.fromkeys(voices):
        voice_root = root / _slug(voice)
        voice_root.mkdir(parents=True, exist_ok=True)
        scene_results = []
        scene_audio = []
        try:
            engine = EdgeVoice(
                voice=voice,
                speed=speed,
                attempts=attempts,
                retry_base_seconds=retry_base_seconds,
                timeout_seconds=scene_timeout_seconds,
            )
            for scene in plan.scenes:
                path = voice_root / f"scene_{scene.number:02}.wav"
                result = engine.synthesize(scene.narration, path, scene.purpose)
                scene_audio.append(path)
                scene_results.append({
                    "scene": scene.number,
                    "purpose": scene.purpose,
                    "narration": scene.narration,
                    **result.metadata(),
                })
            combined = voice_root / f"{_slug(voice)}.wav"
            _concat_wav(scene_audio, combined)
            results.append({
                "voice": voice,
                "status": "pass",
                "audio": str(combined),
                "duration_seconds": round(probe_duration(combined), 3),
                "scenes": scene_results,
            })
        except KeyboardInterrupt:
            interrupted = True
            results.append({
                "voice": voice,
                "status": "interrupted",
                "resume": "rerun the same command; completed scene caches will be reused",
                "scenes": scene_results,
            })
            return checkpoint()
        except Exception as exc:
            results.append({
                "voice": voice,
                "status": "fail",
                "error": f"{type(exc).__name__}: {exc}",
                "scenes": scene_results,
            })
        checkpoint()
    return checkpoint()
