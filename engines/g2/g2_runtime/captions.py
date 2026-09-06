from __future__ import annotations

from pathlib import Path

from .storyboard import MixedScene


def _time(value: float) -> str:
    centiseconds = max(0, round(value * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"


def _chunks(text: str, size: int = 5) -> list[str]:
    words = text.split()
    return [" ".join(words[index:index + size]) for index in range(0, len(words), size)] or [""]


def write_ass(scenes: list[MixedScene], path: str | Path) -> Path:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Caption,Nimbus Sans,52,&H00FFFFFF,&H00FFFFFF,&H00101825,&H00000000,-1,0,0,0,100,100,0,0,1,4,2,2,82,82,245,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events = []
    cursor = 0.0
    for scene in scenes:
        chunks = _chunks(scene.narration)
        unit = scene.duration_seconds / len(chunks)
        for index, chunk in enumerate(chunks):
            start = cursor + index * unit
            end = cursor + (index + 1) * unit
            safe = chunk.replace("{", "(").replace("}", ")").replace("\\", "/")
            events.append(f"Dialogue: 0,{_time(start)},{_time(end)},Caption,,0,0,0,,{safe}")
        cursor += scene.duration_seconds
    destination = Path(path)
    destination.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return destination
