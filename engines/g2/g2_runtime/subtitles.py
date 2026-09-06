from __future__ import annotations

import re
from pathlib import Path


def _srt_time(value: float) -> str:
    milliseconds = max(0, round(value * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def _parse_clock(value: str) -> float:
    normalized = value.strip().replace(",", ".")
    parts = normalized.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"invalid subtitle clock: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def read_vtt_words(path: str | Path) -> list[tuple[float, float, str]]:
    """Read Edge word-boundary VTT without adding a WebVTT dependency."""
    text = Path(path).read_text(encoding="utf-8-sig")
    pattern = re.compile(
        r"(?m)^(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s+-->\s+"
        r"(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})[^\n]*\n([^\n]+)"
    )
    words = []
    for match in pattern.finditer(text):
        cue_text = re.sub(r"<[^>]+>", "", match.group(3)).strip()
        if cue_text:
            words.append((_parse_clock(match.group(1)), _parse_clock(match.group(2)), cue_text))
    return words


def _estimated_words(text: str, duration: float) -> list[tuple[float, float, str]]:
    words = text.split()
    if not words:
        return []
    usable = max(0.2, duration)
    step = usable / len(words)
    return [(index * step, (index + 1) * step, word) for index, word in enumerate(words)]


def write_provider_srt(scene_timings: list[dict], destination: str | Path, words_per_cue: int = 5) -> Path:
    """Build one global subtitle timeline from the exact generated scene audio."""
    cues: list[tuple[float, float, str]] = []
    timeline_offset = 0.0
    for item in scene_timings:
        duration = float(item["duration_seconds"])
        timing_path = item.get("timing_path")
        if timing_path and Path(timing_path).is_file():
            words = read_vtt_words(timing_path)
            timing_shift = float(item.get("timing_offset_seconds", 0.0))
        else:
            words = _estimated_words(str(item["narration"]), duration)
            timing_shift = 0.0
        for index in range(0, len(words), words_per_cue):
            group = words[index:index + words_per_cue]
            if not group:
                continue
            start = min(timeline_offset + timing_shift + group[0][0], timeline_offset + duration)
            end = min(timeline_offset + timing_shift + group[-1][1], timeline_offset + duration)
            if end <= start:
                end = min(timeline_offset + duration, start + 0.25)
            cues.append((start, end, " ".join(word[2] for word in group)))
        timeline_offset += duration

    output = Path(destination)
    lines: list[str] = []
    for number, (start, end, text) in enumerate(cues, 1):
        lines.extend([str(number), f"{_srt_time(start)} --> {_srt_time(end)}", text, ""])
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


class FasterWhisperSubtitles:
    """Use Faster-Whisper for speech-aligned SRT instead of estimated text timing."""

    def __init__(self, model: str = "base.en", device: str = "cpu", compute_type: str = "int8"):
        self.model = model
        self.device = device
        self.compute_type = compute_type

    def transcribe(self, media: str | Path, destination: str | Path) -> Path:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Faster-Whisper is not installed; run: pip install -e '.[subtitles]'") from exc

        model = WhisperModel(self.model, device=self.device, compute_type=self.compute_type)
        segments, _ = model.transcribe(
            str(media),
            language="en",
            beam_size=3,
            word_timestamps=True,
            vad_filter=True,
        )
        cues: list[tuple[float, float, str]] = []
        words: list[tuple[float, float, str]] = []
        for segment in segments:
            for word in segment.words or []:
                words.append((word.start, word.end, word.word.strip()))
        for index in range(0, len(words), 5):
            group = words[index:index + 5]
            if group:
                cues.append((group[0][0], group[-1][1], " ".join(item[2] for item in group)))
        output = Path(destination)
        lines = []
        for number, (start, end, text) in enumerate(cues, 1):
            lines.extend([str(number), f"{_srt_time(start)} --> {_srt_time(end)}", text, ""])
        output.write_text("\n".join(lines), encoding="utf-8")
        return output
