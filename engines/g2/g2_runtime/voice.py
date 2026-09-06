from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path


def probe_duration(path: str | Path) -> float:
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    return float(subprocess.check_output(command, text=True).strip())


@dataclass(frozen=True)
class DeliveryProfile:
    """Scene-level direction for informational narration.

    Energy is expressed as a small scene-to-scene loudness contrast instead of
    indiscriminate excitement. This keeps claims intelligible while giving the
    story a perceptible tension, explanation and resolution arc.
    """

    name: str
    rate_percent: int = 0
    pitch_hz: int = 0
    pause_before_seconds: float = 0.08
    pause_after_seconds: float = 0.30
    target_lufs: float = -16.0

    def adjusted(self, speed: float) -> "DeliveryProfile":
        rate_delta = round((float(speed) - 1.0) * 100)
        return DeliveryProfile(
            name=self.name,
            rate_percent=max(-20, min(20, self.rate_percent + rate_delta)),
            pitch_hz=self.pitch_hz,
            pause_before_seconds=self.pause_before_seconds,
            pause_after_seconds=self.pause_after_seconds,
            target_lufs=self.target_lufs,
        )


DELIVERY_BY_PURPOSE: dict[str, DeliveryProfile] = {
    "cover": DeliveryProfile("conversational_hook", 0, 0, 0.08, 0.20, -15.8),
    "evidence": DeliveryProfile("clear_evidence", 1, 0, 0.06, 0.24, -15.8),
    "operational_complexity": DeliveryProfile("explanatory", 2, 0, 0.06, 0.28, -15.8),
    "failure_point": DeliveryProfile("restrained_concern", -7, -1, 0.09, 0.50, -17.0),
    "control_system": DeliveryProfile("quiet_confidence", -1, 1, 0.08, 0.32, -15.4),
    "outcome_cta": DeliveryProfile("warm_close", -4, 0, 0.10, 0.52, -16.2),
}


def delivery_for(purpose: str, speed: float = 1.0) -> DeliveryProfile:
    return DELIVERY_BY_PURPOSE.get(purpose, DeliveryProfile("neutral")).adjusted(speed)


@dataclass(frozen=True)
class VoiceResult:
    audio_path: Path
    duration_seconds: float
    provider: str
    voice: str
    delivery: DeliveryProfile
    timing_path: Path | None = None
    timing_offset_seconds: float = 0.0
    cache_hit: bool = False

    def metadata(self) -> dict:
        return {
            "provider": self.provider,
            "voice": self.voice,
            "duration_seconds": round(self.duration_seconds, 3),
            "delivery": asdict(self.delivery),
            "timing_source": self.timing_path.name if self.timing_path else "deterministic_from_audio",
            "timing_offset_seconds": self.timing_offset_seconds,
            "cache_hit": self.cache_hit,
        }


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _postprocess_audio(source: Path, destination: Path, delivery: DeliveryProfile) -> Path:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required")
    delay_ms = max(0, round(delivery.pause_before_seconds * 1000))
    filters = []
    if delay_ms:
        filters.append(f"adelay={delay_ms}:all=1")
    filters.extend([
        "highpass=f=65",
        f"loudnorm=I={delivery.target_lufs:.1f}:TP=-1.5:LRA=7",
        f"apad=pad_dur={max(0.0, delivery.pause_after_seconds):.3f}",
        "afade=t=in:st=0:d=0.025",
    ])
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-af", ",".join(filters), "-ar", "48000", "-ac", "1",
        "-c:a", "pcm_s16le", str(destination),
    ])
    return destination


def silence(path: str | Path, duration: float) -> Path:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required")
    destination = Path(path)
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{duration:.3f}",
        "-c:a", "pcm_s16le", str(destination),
    ])
    return destination


class EdgeVoice:
    """Small network provider with native word-boundary VTT output."""

    _RETRYABLE = (
        "temporary failure in name resolution",
        "clientconnectordns",
        "cannot connect to host",
        "connection reset",
        "connection refused",
        "timed out",
        "timeout",
        "429",
        "503",
    )

    def __init__(
        self,
        voice: str = "en-GB-RyanNeural",
        speed: float = 1.0,
        attempts: int = 4,
        retry_base_seconds: float = 2.0,
        timeout_seconds: float = 45.0,
    ):
        self.voice = voice
        self.speed = speed
        self.attempts = max(1, attempts)
        self.retry_base_seconds = max(0.0, retry_base_seconds)
        self.timeout_seconds = max(5.0, timeout_seconds)

    def _cache_key(self, text: str, purpose: str, delivery: DeliveryProfile) -> str:
        value = {
            "provider": "edge",
            "voice": self.voice,
            "text": text,
            "purpose": purpose,
            "delivery": asdict(delivery),
            "cache_version": 3,
        }
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _synthesize_edge(self, command: list[str], partials: tuple[Path, ...]) -> None:
        last: subprocess.CompletedProcess[str] | None = None
        for attempt in range(1, self.attempts + 1):
            for partial in partials:
                partial.unlink(missing_ok=True)
            try:
                last = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                if attempt == self.attempts:
                    raise RuntimeError(
                        f"Edge TTS timed out after {attempt} attempt(s) for voice "
                        f"{self.voice}; per-scene timeout={self.timeout_seconds:.0f}s"
                    ) from exc
                time.sleep(self.retry_base_seconds * (2 ** (attempt - 1)))
                continue
            if last.returncode == 0:
                return
            diagnostic = f"{last.stdout}\n{last.stderr}".lower()
            retryable = any(marker in diagnostic for marker in self._RETRYABLE)
            if not retryable or attempt == self.attempts:
                detail = (last.stderr or last.stdout or "unknown provider error").strip()
                if len(detail) > 1200:
                    detail = detail[-1200:]
                raise RuntimeError(
                    f"Edge TTS failed after {attempt} attempt(s) "
                    f"for voice {self.voice}: {detail}"
                )
            time.sleep(self.retry_base_seconds * (2 ** (attempt - 1)))

    def synthesize(self, text: str, path: str | Path, purpose: str) -> VoiceResult:
        binary = shutil.which("edge-tts")
        if not binary:
            raise RuntimeError("Edge voice requires: pip install edge-tts")
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        delivery = delivery_for(purpose, self.speed)
        raw_audio = destination.with_suffix(".edge.mp3")
        timing_path = destination.with_suffix(".vtt")
        cache_path = destination.with_suffix(".voice-cache.json")
        cache_key = self._cache_key(text, purpose, delivery)
        if destination.is_file() and timing_path.is_file() and cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                duration = probe_duration(destination)
            except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
                cached = {}
                duration = 0.0
            if cached.get("key") == cache_key and duration > 0:
                return VoiceResult(
                    audio_path=destination,
                    duration_seconds=duration,
                    provider="edge",
                    voice=self.voice,
                    delivery=delivery,
                    timing_path=timing_path,
                    timing_offset_seconds=delivery.pause_before_seconds,
                    cache_hit=True,
                )
        command = [
            binary,
            "--voice", self.voice,
            f"--rate={delivery.rate_percent:+d}%",
            f"--pitch={delivery.pitch_hz:+d}Hz",
            "--text", text,
            "--write-media", str(raw_audio),
            "--write-subtitles", str(timing_path),
        ]
        self._synthesize_edge(command, (raw_audio, timing_path))
        _postprocess_audio(raw_audio, destination, delivery)
        raw_audio.unlink(missing_ok=True)
        cache_path.write_text(
            json.dumps({"key": cache_key, "voice": self.voice}, indent=2) + "\n",
            encoding="utf-8",
        )
        return VoiceResult(
            audio_path=destination,
            duration_seconds=probe_duration(destination),
            provider="edge",
            voice=self.voice,
            delivery=delivery,
            timing_path=timing_path,
            timing_offset_seconds=delivery.pause_before_seconds,
            cache_hit=False,
        )


class KokoroVoice:
    def __init__(self, voice: str = "af_heart", speed: float = 1.0, lang_code: str = "a"):
        self.voice = voice
        self.speed = speed
        self.lang_code = lang_code

    def synthesize(self, text: str, path: str | Path, purpose: str) -> VoiceResult:
        try:
            import numpy as np
            import soundfile as sf
            from kokoro import KPipeline
        except ImportError as exc:
            raise RuntimeError("Kokoro is not installed; install the optional voice dependencies") from exc
        delivery = delivery_for(purpose, self.speed)
        effective_speed = max(0.75, min(1.25, 1.0 + delivery.rate_percent / 100))
        pipeline = KPipeline(lang_code=self.lang_code)
        chunks = [audio for _, _, audio in pipeline(text, voice=self.voice, speed=effective_speed)]
        if not chunks:
            raise RuntimeError("Kokoro returned no audio")
        destination = Path(path)
        raw = destination.with_suffix(".raw.wav")
        sf.write(raw, np.concatenate(chunks), 24000)
        _postprocess_audio(raw, destination, delivery)
        raw.unlink(missing_ok=True)
        return VoiceResult(destination, probe_duration(destination), "kokoro", self.voice, delivery)


class SystemVoice:
    """Zero-model local emergency fallback using eSpeak NG."""

    def __init__(self, voice: str = "en-us", speed: float = 1.0, pitch: int = 46):
        self.voice = voice
        self.speed = speed
        self.pitch = pitch

    def synthesize(self, text: str, path: str | Path, purpose: str) -> VoiceResult:
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not binary:
            raise RuntimeError("system voice requires espeak-ng")
        destination = Path(path)
        raw = destination.with_suffix(".raw.wav")
        delivery = delivery_for(purpose, self.speed)
        words_per_minute = max(100, min(210, round(155 * (1 + delivery.rate_percent / 100))))
        _run([
            binary, "-v", self.voice, "-s", str(words_per_minute), "-p", str(self.pitch),
            "-w", str(raw), text,
        ])
        _postprocess_audio(raw, destination, delivery)
        raw.unlink(missing_ok=True)
        return VoiceResult(destination, probe_duration(destination), "system", self.voice, delivery)


def voice_engine(mode: str, voice: str, speed: float = 1.0):
    """Return preset voices only; reference-audio cloning is intentionally unsupported."""
    if mode == "edge":
        return EdgeVoice(voice=voice, speed=speed)
    if mode == "kokoro":
        return KokoroVoice(voice=voice, speed=speed)
    if mode == "system":
        name = voice if voice not in {"af_heart", "en-GB-RyanNeural"} else "en-us"
        return SystemVoice(voice=name, speed=speed)
    if mode == "silent":
        return None
    raise ValueError("voice_mode must be silent, edge, system, or kokoro")
